"""Bounded continuous execution for deterministic paper-trading sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.domain.errors import DomainError
from app.market_data.domain import require_utc
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager
from app.paper_trading.domain import PaperRunAction, PaperRunResult
from app.paper_trading.errors import (
    PaperRunnerCorruptError,
    PaperRunnerStateNotFoundError,
    PaperTradingError,
)

logger = logging.getLogger(__name__)

_RUNNER_SCHEMA = 1
_RUNNER_LOCK_KEY = "adt:continuous-paper-trading:v1"
_MAX_SESSIONS_ABSOLUTE = 1_000
_MAX_CYCLE_INDEX = 10**15
_MAX_STATE_BYTES = 4 * 1024 * 1024
_MAX_ERROR_CODE_LENGTH = 128
_SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,128}$")

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


class PaperSessionExecutor(Protocol):
    def run_once(self, session_id: str) -> PaperRunResult: ...


class PaperRunnerSessionStatus(StrEnum):
    UPDATED = "UPDATED"
    NOOP = "NOOP"
    FAILED = "FAILED"


class PaperRunnerCycleStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PaperRunnerPolicy:
    interval_seconds: int
    max_sessions: int

    def __post_init__(self) -> None:
        try:
            _input_exact_int(self.interval_seconds, "interval_seconds", 1, 3_600)
            _input_exact_int(self.max_sessions, "max_sessions", 1, _MAX_SESSIONS_ABSOLUTE)
        except PaperTradingError:
            raise
        except Exception:
            raise PaperTradingError("A política do runner é inválida.") from None


@dataclass(frozen=True, slots=True)
class PaperRunnerSessionResult:
    session_id: str
    status: PaperRunnerSessionStatus
    started_at: datetime
    finished_at: datetime
    state_id: str | None = None
    candles_processed: int | None = None
    last_candle_open_time: datetime | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        session_id = _stored_session_id(self.session_id)
        if not isinstance(self.status, PaperRunnerSessionStatus):
            raise PaperRunnerCorruptError("O estado da sessão no runner é inválido.")
        started_at = _utc(self.started_at, "started_at")
        finished_at = _utc(self.finished_at, "finished_at")
        if finished_at < started_at:
            raise PaperRunnerCorruptError("A temporalidade do resultado é inválida.")
        state_id = _optional_digest(self.state_id)
        error_code = _optional_error_code(self.error_code)
        last_candle = (
            None
            if self.last_candle_open_time is None
            else _utc(self.last_candle_open_time, "last_candle_open_time")
        )
        if self.status is PaperRunnerSessionStatus.FAILED:
            if (
                error_code is None
                or state_id is not None
                or self.candles_processed is not None
                or last_candle is not None
            ):
                raise PaperRunnerCorruptError("O resultado falho do runner é inválido.")
        else:
            if state_id is None or error_code is not None or last_candle is None:
                raise PaperRunnerCorruptError("O resultado concluído do runner é inválido.")
            _exact_int(self.candles_processed, "candles_processed", 1, 10**12)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)
        object.__setattr__(self, "state_id", state_id)
        object.__setattr__(self, "last_candle_open_time", last_candle)
        object.__setattr__(self, "error_code", error_code)


@dataclass(frozen=True, slots=True)
class PaperRunnerState:
    cycle_index: int
    status: PaperRunnerCycleStatus
    policy: PaperRunnerPolicy
    started_at: datetime
    finished_at: datetime
    next_cycle_at: datetime
    results: tuple[PaperRunnerSessionResult, ...]
    cycle_id: str = ""
    checksum: str = ""
    schema_version: int = _RUNNER_SCHEMA

    def __post_init__(self) -> None:
        _exact_int(self.schema_version, "schema_version", _RUNNER_SCHEMA, _RUNNER_SCHEMA)
        _exact_int(self.cycle_index, "cycle_index", 1, _MAX_CYCLE_INDEX)
        if not isinstance(self.status, PaperRunnerCycleStatus):
            raise PaperRunnerCorruptError("O estado agregado do runner é inválido.")
        _validate_policy(self.policy)
        started_at = _utc(self.started_at, "started_at")
        finished_at = _utc(self.finished_at, "finished_at")
        next_cycle_at = _utc(self.next_cycle_at, "next_cycle_at")
        if finished_at < started_at or next_cycle_at != started_at + timedelta(
            seconds=self.policy.interval_seconds
        ):
            raise PaperRunnerCorruptError("A temporalidade do ciclo é inválida.")
        if (
            not isinstance(self.results, tuple)
            or not self.results
            or len(self.results) > self.policy.max_sessions
        ):
            raise PaperRunnerCorruptError("O ciclo deve conter sessões válidas.")
        for result in self.results:
            _validate_result(result)
            if result.started_at < started_at or result.finished_at > finished_at:
                raise PaperRunnerCorruptError("Um resultado está fora do ciclo.")
        ids = tuple(result.session_id for result in self.results)
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise PaperRunnerCorruptError("As sessões devem ser únicas e ordenadas.")
        if self.status is not _aggregate(self.results):
            raise PaperRunnerCorruptError("O estado agregado diverge das sessões.")
        semantic = _state_payload(self, include_identity=False)
        checksum = _hash("adt-paper-runner-state-checksum-v1", semantic)
        cycle_id = _hash("adt-paper-runner-cycle-id-v1", semantic)
        if self.checksum not in {"", checksum} or self.cycle_id not in {"", cycle_id}:
            raise PaperRunnerCorruptError("A identidade do ciclo é inválida.")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)
        object.__setattr__(self, "next_cycle_at", next_cycle_at)
        object.__setattr__(self, "checksum", checksum)
        object.__setattr__(self, "cycle_id", cycle_id)


class PaperTradingContinuousService:
    """Execute one sequential and failure-isolated paper session cycle."""

    def __init__(
        self,
        service: PaperSessionExecutor,
        *,
        policy: PaperRunnerPolicy,
        clock: Clock | None = None,
    ) -> None:
        _validate_policy(policy)
        if not callable(getattr(service, "run_once", None)):
            raise PaperTradingError("O serviço de paper trading é inválido.")
        self._service = service
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def validate_session_ids(self, value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple) or not value or len(value) > self._policy.max_sessions:
            raise PaperTradingError("A lista de sessões do runner é inválida.")
        try:
            result = tuple(_stored_session_id(item) for item in value)
        except PaperRunnerCorruptError:
            raise PaperTradingError("O session_id do runner é inválido.") from None
        if result != tuple(sorted(result)) or len(set(result)) != len(result):
            raise PaperTradingError("As sessões do runner devem ser únicas e ordenadas.")
        return result

    def run_cycle(self, session_ids: tuple[str, ...], *, cycle_index: int) -> PaperRunnerState:
        selected = self.validate_session_ids(session_ids)
        _input_exact_int(cycle_index, "cycle_index", 1, _MAX_CYCLE_INDEX)
        cycle_started = self._now()
        results = tuple(self._run_session(session_id) for session_id in selected)
        cycle_finished = self._now()
        return PaperRunnerState(
            cycle_index=cycle_index,
            status=_aggregate(results),
            policy=self._policy,
            started_at=cycle_started,
            finished_at=cycle_finished,
            next_cycle_at=cycle_started + timedelta(seconds=self._policy.interval_seconds),
            results=results,
        )

    def _run_session(self, session_id: str) -> PaperRunnerSessionResult:
        started_at = self._now()
        try:
            result = self._service.run_once(session_id)
            result = _validate_run_result(
                result,
                expected_session_id=session_id,
            )
            expected = (
                PaperRunnerSessionStatus.UPDATED
                if result.action is PaperRunAction.UPDATED
                else PaperRunnerSessionStatus.NOOP
            )
            return PaperRunnerSessionResult(
                session_id=session_id,
                status=expected,
                started_at=started_at,
                finished_at=self._now(),
                state_id=result.state.state_id,
                candles_processed=result.state.candles_processed,
                last_candle_open_time=result.state.last_candle_open_time,
            )
        except Exception as error:
            code = _safe_error_code(error)
            logger.warning(
                "Continuous paper-trading session failed",
                extra={"session_id": session_id, "failure_code": code},
            )
            return PaperRunnerSessionResult(
                session_id=session_id,
                status=PaperRunnerSessionStatus.FAILED,
                started_at=started_at,
                finished_at=self._now(),
                error_code=code,
            )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise PaperTradingError("O relógio do runner é inválido.")
        return require_utc(value, field_name="paper_runner_clock")


class PaperRunnerStateStore:
    """Atomic latest-cycle state for the runner and read-only API."""

    def __init__(self, data_dir: Path) -> None:
        root = market_root(data_dir)
        self._root = root
        self._directory = ensure_safe_path(root, root / "paper-trading" / "runner")
        self._path = ensure_safe_path(root, self._directory / "state.json")

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> PaperRunnerState | None:
        if not self._path.exists():
            return None
        raw = _read_bounded(self._path, _MAX_STATE_BYTES)
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, ValueError):
            raise PaperRunnerCorruptError("O estado do runner é inválido.") from None
        try:
            state = _decode_state(payload)
        except PaperRunnerCorruptError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError):
            raise PaperRunnerCorruptError("O estado do runner é inválido.") from None
        if raw != _canonical_bytes(_state_payload(state, include_identity=True)):
            raise PaperRunnerCorruptError("O estado do runner não é canônico.")
        return state

    def require(self) -> PaperRunnerState:
        state = self.read()
        if state is None:
            raise PaperRunnerStateNotFoundError()
        return state

    def write(self, state: PaperRunnerState) -> PaperRunnerState:
        _validate_state(state)
        existing = self.read()
        if existing is not None:
            if existing == state:
                return existing
            if state.cycle_index != existing.cycle_index + 1:
                raise PaperRunnerCorruptError("O índice do runner não é monotônico.")
        encoded = _canonical_bytes(_state_payload(state, include_identity=True))
        if len(encoded) > _MAX_STATE_BYTES:
            raise PaperRunnerCorruptError("O estado do runner excede o limite seguro.")
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary = ensure_safe_path(
            self._root,
            self._directory / f".state.tmp-{os.getpid()}-{uuid4().hex}",
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            fsync_directory(self._directory)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise PaperRunnerCorruptError("O estado do runner não pôde ser persistido.") from None
        persisted = self.read()
        if persisted != state:
            raise PaperRunnerCorruptError("O estado persistido do runner diverge.")
        return persisted


class PaperTradingContinuousRunner:
    """Hold one global lease and execute fixed-cadence paper cycles."""

    def __init__(
        self,
        *,
        service: PaperTradingContinuousService,
        state_store: PaperRunnerStateStore,
        lock_manager: DatasetLockManager,
        sleeper: Sleeper = asyncio.sleep,
        clock: Clock | None = None,
    ) -> None:
        self._service = service
        self._state_store = state_store
        self._lock_manager = lock_manager
        self._sleeper = sleeper
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self,
        session_ids: tuple[str, ...],
        *,
        max_cycles: int | None = None,
    ) -> PaperRunnerState:
        if max_cycles is not None:
            _input_exact_int(max_cycles, "max_cycles", 1, 1_000_000)
        selected = self._service.validate_session_ids(session_ids)
        last: PaperRunnerState | None = None
        with self._lock_manager.acquire(_RUNNER_LOCK_KEY):
            previous = self._state_store.read()
            cycle_index = 1 if previous is None else previous.cycle_index + 1
            completed = 0
            while max_cycles is None or completed < max_cycles:
                state = self._service.run_cycle(selected, cycle_index=cycle_index)
                last = self._state_store.write(state)
                completed += 1
                cycle_index += 1
                if max_cycles is not None and completed >= max_cycles:
                    break
                delay = max(0.0, (state.next_cycle_at - self._now()).total_seconds())
                await self._sleeper(delay)
        if last is None:
            raise PaperTradingError("Nenhum ciclo de paper trading foi executado.")
        return last

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise PaperTradingError("O relógio do runner é inválido.")
        return require_utc(value, field_name="paper_runner_clock")


def validate_paper_runner_session_result(
    value: object,
) -> PaperRunnerSessionResult:
    return _validate_result(value)


def validate_paper_runner_state(value: object) -> PaperRunnerState:
    return _validate_state(value)


def paper_runner_state_payload(state: PaperRunnerState) -> dict[str, object]:
    _validate_state(state)
    return _state_payload(state, include_identity=True)


def _read_bounded(path: Path, maximum: int) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
    except OSError:
        raise PaperRunnerCorruptError("O estado do runner não pôde ser lido.") from None
    if len(raw) > maximum:
        raise PaperRunnerCorruptError("O estado do runner excede o limite seguro.")
    return raw


def _validate_state(value: object) -> PaperRunnerState:
    if not isinstance(value, PaperRunnerState):
        raise PaperRunnerCorruptError("O estado do runner é inválido.")
    candidate = PaperRunnerState(
        cycle_index=value.cycle_index,
        status=value.status,
        policy=value.policy,
        started_at=value.started_at,
        finished_at=value.finished_at,
        next_cycle_at=value.next_cycle_at,
        results=value.results,
        cycle_id=value.cycle_id,
        checksum=value.checksum,
        schema_version=value.schema_version,
    )
    if candidate != value:
        raise PaperRunnerCorruptError("O estado do runner foi adulterado.")
    return value


def _validate_policy(value: object) -> PaperRunnerPolicy:
    if not isinstance(value, PaperRunnerPolicy):
        raise PaperRunnerCorruptError("A política do runner é inválida.")
    try:
        candidate = PaperRunnerPolicy(value.interval_seconds, value.max_sessions)
    except PaperTradingError:
        raise PaperRunnerCorruptError("A política do runner foi adulterada.") from None
    if candidate != value:
        raise PaperRunnerCorruptError("A política do runner foi adulterada.")
    return value


def _validate_run_result(
    value: object,
    *,
    expected_session_id: str,
) -> PaperRunResult:
    if not isinstance(value, PaperRunResult):
        raise PaperTradingError("A sessão retornou resultado inválido.")
    try:
        candidate = PaperRunResult(action=value.action, state=value.state)
    except Exception:
        raise PaperTradingError("A sessão retornou resultado inválido.") from None
    if candidate != value:
        raise PaperTradingError("A sessão retornou resultado adulterado.")
    if value.state.session_id != expected_session_id:
        raise PaperTradingError("A sessão retornou estado de outra identidade.")
    return value


def _validate_result(value: object) -> PaperRunnerSessionResult:
    if not isinstance(value, PaperRunnerSessionResult):
        raise PaperRunnerCorruptError("O resultado do runner é inválido.")
    candidate = PaperRunnerSessionResult(
        session_id=value.session_id,
        status=value.status,
        started_at=value.started_at,
        finished_at=value.finished_at,
        state_id=value.state_id,
        candles_processed=value.candles_processed,
        last_candle_open_time=value.last_candle_open_time,
        error_code=value.error_code,
    )
    if candidate != value:
        raise PaperRunnerCorruptError("O resultado do runner foi adulterado.")
    return value


def _state_payload(state: PaperRunnerState, *, include_identity: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": state.schema_version,
        "cycle_index": state.cycle_index,
        "status": state.status.value,
        "policy": {
            "interval_seconds": state.policy.interval_seconds,
            "max_sessions": state.policy.max_sessions,
        },
        "started_at": state.started_at.isoformat(),
        "finished_at": state.finished_at.isoformat(),
        "next_cycle_at": state.next_cycle_at.isoformat(),
        "results": [
            {
                "session_id": result.session_id,
                "status": result.status.value,
                "started_at": result.started_at.isoformat(),
                "finished_at": result.finished_at.isoformat(),
                "state_id": result.state_id,
                "candles_processed": result.candles_processed,
                "last_candle_open_time": (
                    None
                    if result.last_candle_open_time is None
                    else result.last_candle_open_time.isoformat()
                ),
                "error_code": result.error_code,
            }
            for result in state.results
        ],
    }
    if include_identity:
        payload["cycle_id"] = state.cycle_id
        payload["checksum"] = state.checksum
    return payload


def _decode_state(value: object) -> PaperRunnerState:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "cycle_index",
        "cycle_id",
        "status",
        "policy",
        "started_at",
        "finished_at",
        "next_cycle_at",
        "results",
        "checksum",
    }:
        raise PaperRunnerCorruptError("O documento do runner é inválido.")
    policy = value["policy"]
    results = value["results"]
    if not isinstance(policy, dict) or set(policy) != {"interval_seconds", "max_sessions"}:
        raise PaperRunnerCorruptError("A política persistida é inválida.")
    if not isinstance(results, list):
        raise PaperRunnerCorruptError("Os resultados persistidos são inválidos.")
    decoded: list[PaperRunnerSessionResult] = []
    for item in results:
        if not isinstance(item, dict) or set(item) != {
            "session_id",
            "status",
            "started_at",
            "finished_at",
            "state_id",
            "candles_processed",
            "last_candle_open_time",
            "error_code",
        }:
            raise PaperRunnerCorruptError("Um resultado persistido é inválido.")
        decoded.append(
            PaperRunnerSessionResult(
                session_id=_str(item["session_id"]),
                status=PaperRunnerSessionStatus(_str(item["status"])),
                started_at=_datetime(item["started_at"]),
                finished_at=_datetime(item["finished_at"]),
                state_id=_optional_str(item["state_id"]),
                candles_processed=_optional_int(item["candles_processed"]),
                last_candle_open_time=(
                    None
                    if item["last_candle_open_time"] is None
                    else _datetime(item["last_candle_open_time"])
                ),
                error_code=_optional_str(item["error_code"]),
            )
        )
    try:
        policy_value = PaperRunnerPolicy(
            interval_seconds=_int(policy["interval_seconds"]),
            max_sessions=_int(policy["max_sessions"]),
        )
    except PaperTradingError:
        raise PaperRunnerCorruptError("A política persistida é inválida.") from None
    return PaperRunnerState(
        schema_version=_int(value["schema_version"]),
        cycle_index=_int(value["cycle_index"]),
        cycle_id=_str(value["cycle_id"]),
        status=PaperRunnerCycleStatus(_str(value["status"])),
        policy=policy_value,
        started_at=_datetime(value["started_at"]),
        finished_at=_datetime(value["finished_at"]),
        next_cycle_at=_datetime(value["next_cycle_at"]),
        results=tuple(decoded),
        checksum=_str(value["checksum"]),
    )


def _aggregate(results: tuple[PaperRunnerSessionResult, ...]) -> PaperRunnerCycleStatus:
    failed = sum(item.status is PaperRunnerSessionStatus.FAILED for item in results)
    if failed == 0:
        return PaperRunnerCycleStatus.COMPLETED
    if failed == len(results):
        return PaperRunnerCycleStatus.FAILED
    return PaperRunnerCycleStatus.PARTIALLY_FAILED


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, DomainError):
        code = error.code
    else:
        code = "paper_runner_unexpected_error"
    if not isinstance(code, str) or _ERROR_CODE_PATTERN.fullmatch(code) is None:
        return "paper_runner_error"
    return code[:_MAX_ERROR_CODE_LENGTH]


def _stored_session_id(value: object) -> str:
    if not isinstance(value, str) or _SESSION_ID_PATTERN.fullmatch(value) is None:
        raise PaperRunnerCorruptError("O session_id do runner é inválido.")
    return value


def _optional_digest(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SESSION_ID_PATTERN.fullmatch(value) is None:
        raise PaperRunnerCorruptError("A identidade do estado é inválida.")
    return value


def _optional_error_code(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _ERROR_CODE_PATTERN.fullmatch(value) is None:
        raise PaperRunnerCorruptError("O código de erro do runner é inválido.")
    return value


def _input_exact_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise PaperTradingError(f"{field} é inválido.")
    integer = value
    if integer < minimum or integer > maximum:
        raise PaperTradingError(f"{field} é inválido.")
    return integer


def _exact_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise PaperRunnerCorruptError(f"{field} é inválido.")
    integer = value
    if integer < minimum or integer > maximum:
        raise PaperRunnerCorruptError(f"{field} é inválido.")
    return integer


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise PaperRunnerCorruptError(f"{field} é inválido.")
    try:
        return require_utc(value, field_name=field)
    except Exception:
        raise PaperRunnerCorruptError(f"{field} é inválido.") from None


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash(domain: str, payload: object) -> str:
    return sha256(domain.encode() + b"\x00" + _canonical_bytes(payload)).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise PaperRunnerCorruptError("Campo textual inválido.")
    return value


def _optional_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _int(value: object) -> int:
    if type(value) is not int:
        raise PaperRunnerCorruptError("Campo inteiro inválido.")
    return value


def _optional_int(value: object) -> int | None:
    return None if value is None else _int(value)


def _datetime(value: object) -> datetime:
    text = _str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise PaperRunnerCorruptError("Timestamp inválido.") from None
    return _utc(parsed, "timestamp")
