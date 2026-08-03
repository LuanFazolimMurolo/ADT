"""Bounded continuous RAW candle collection for Phase 5-02."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from app.domain.errors import DomainError
from app.market_data.domain import (
    DataRange,
    Instrument,
    Timeframe,
    TradingPair,
    require_utc,
    validate_instrument,
)
from app.market_data.errors import (
    InactiveInstrumentError,
    MarketDataInconsistencyError,
    MarketDataStorageError,
)
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager
from app.market_data.planning import (
    BackfillChunk,
    BackfillPlan,
    BackfillResult,
    IncrementalUpdatePlan,
    MarketJobStatus,
    MarketJobType,
)
from app.market_data.storage import ParquetCandleStore
from app.market_data.timeframes import get_timeframe

logger = logging.getLogger(__name__)

_COLLECTION_SCHEMA = 1
_COLLECTION_LOCK_KEY = "adt:continuous-market-collection:v1"
_COLLECTION_STATE_KEYS = {
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
}
_RESULT_KEYS = {
    "target",
    "status",
    "started_at",
    "finished_at",
    "latest_closed_end",
    "job_id",
    "fetched_count",
    "stored_count",
    "duplicate_count",
    "request_count",
    "error_code",
}
_MAX_TARGETS_ABSOLUTE = 1_000
_MAX_BOOTSTRAP_CANDLES = 1_000_000
_MAX_CYCLE_INDEX = 10**15
_MAX_ERROR_CODE_LENGTH = 128
_MAX_STATE_BYTES = 4 * 1024 * 1024
_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,128}$")

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
StartupHook = Callable[[], object]


class ContinuousTargetStatus(StrEnum):
    """Terminal outcome for one target inside a collection cycle."""

    UPDATED = "UPDATED"
    NOOP = "NOOP"
    FAILED = "FAILED"


class ContinuousCycleStatus(StrEnum):
    """Aggregate terminal state for one complete collection cycle."""

    COMPLETED = "COMPLETED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ContinuousCollectionTarget:
    """One normalized pair/timeframe continuously maintained as RAW candles."""

    pair: TradingPair
    timeframe: Timeframe
    bootstrap_candles: int

    def __post_init__(self) -> None:
        _validate_pair(self.pair)
        _validate_timeframe(self.timeframe)
        _require_exact_int(
            self.bootstrap_candles,
            field_name="bootstrap_candles",
            minimum=1,
            maximum=_MAX_BOOTSTRAP_CANDLES,
        )

    @property
    def key(self) -> str:
        return f"{self.pair.symbol}:{self.timeframe.code}"

    @property
    def dataset_key(self) -> str:
        return f"binance:spot:{self.pair.symbol}:{self.timeframe.code}"

    def bootstrap_start(self, latest_closed_end: datetime) -> datetime:
        end = require_utc(latest_closed_end, field_name="latest_closed_end")
        try:
            return end - self.bootstrap_candles * self.timeframe.duration
        except OverflowError:
            raise MarketDataInconsistencyError(
                "O bootstrap do target excede o intervalo temporal suportado."
            ) from None


@dataclass(frozen=True, slots=True)
class ContinuousCollectionPolicy:
    """Bounded deterministic cadence and failure behavior."""

    interval_seconds: int
    overlap_candles: int
    max_targets: int

    def __post_init__(self) -> None:
        _require_exact_int(
            self.interval_seconds,
            field_name="interval_seconds",
            minimum=1,
            maximum=3_600,
        )
        _require_exact_int(
            self.overlap_candles,
            field_name="overlap_candles",
            minimum=0,
            maximum=100,
        )
        _require_exact_int(
            self.max_targets,
            field_name="max_targets",
            minimum=1,
            maximum=_MAX_TARGETS_ABSOLUTE,
        )


@dataclass(frozen=True, slots=True)
class ContinuousTargetResult:
    """Sanitized durable result for one target update attempt."""

    target: ContinuousCollectionTarget
    status: ContinuousTargetStatus
    started_at: datetime
    finished_at: datetime
    latest_closed_end: datetime
    job_id: str | None = None
    fetched_count: int = 0
    stored_count: int = 0
    duplicate_count: int = 0
    request_count: int = 0
    error_code: str | None = None

    def __post_init__(self) -> None:
        _validate_target(self.target)
        if not isinstance(self.status, ContinuousTargetStatus):
            raise MarketDataInconsistencyError("O estado do target é inválido.")
        started_at = _validate_datetime(self.started_at, field_name="started_at")
        finished_at = _validate_datetime(self.finished_at, field_name="finished_at")
        latest_closed_end = _validate_datetime(
            self.latest_closed_end,
            field_name="latest_closed_end",
        )
        if finished_at < started_at or latest_closed_end > finished_at:
            raise MarketDataInconsistencyError("A temporalidade do resultado é inválida.")
        for field_name, value in (
            ("fetched_count", self.fetched_count),
            ("stored_count", self.stored_count),
            ("duplicate_count", self.duplicate_count),
            ("request_count", self.request_count),
        ):
            _require_exact_int(value, field_name=field_name, minimum=0, maximum=10**12)
        if self.stored_count + self.duplicate_count > self.fetched_count:
            raise MarketDataInconsistencyError("Os contadores do target são inválidos.")
        job_id = _normalize_job_id(self.job_id)
        error_code = _normalize_error_code(self.error_code)
        if self.status is ContinuousTargetStatus.UPDATED:
            if job_id is None or error_code is not None:
                raise MarketDataInconsistencyError("O resultado atualizado é inválido.")
        elif self.status is ContinuousTargetStatus.NOOP:
            if (
                job_id is not None
                or error_code is not None
                or any(
                    value != 0
                    for value in (
                        self.fetched_count,
                        self.stored_count,
                        self.duplicate_count,
                        self.request_count,
                    )
                )
            ):
                raise MarketDataInconsistencyError("O resultado NOOP é inválido.")
        else:
            if error_code is None:
                raise MarketDataInconsistencyError("O resultado falho é inválido.")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)
        object.__setattr__(self, "latest_closed_end", latest_closed_end)
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "error_code", error_code)


@dataclass(frozen=True, slots=True)
class ContinuousCollectionState:
    """Latest complete collection cycle persisted below ADT_DATA_DIR/market."""

    cycle_index: int
    status: ContinuousCycleStatus
    policy: ContinuousCollectionPolicy
    started_at: datetime
    finished_at: datetime
    next_cycle_at: datetime
    results: tuple[ContinuousTargetResult, ...]
    cycle_id: str = ""
    checksum: str = ""
    schema_version: int = _COLLECTION_SCHEMA

    def __post_init__(self) -> None:
        _require_exact_int(
            self.schema_version,
            field_name="schema_version",
            minimum=_COLLECTION_SCHEMA,
            maximum=_COLLECTION_SCHEMA,
        )
        _require_exact_int(
            self.cycle_index,
            field_name="cycle_index",
            minimum=1,
            maximum=_MAX_CYCLE_INDEX,
        )
        if not isinstance(self.status, ContinuousCycleStatus):
            raise MarketDataInconsistencyError("O estado agregado da coleta é inválido.")
        _validate_policy(self.policy)
        started_at = _validate_datetime(self.started_at, field_name="started_at")
        finished_at = _validate_datetime(self.finished_at, field_name="finished_at")
        next_cycle_at = _validate_datetime(self.next_cycle_at, field_name="next_cycle_at")
        if finished_at < started_at or next_cycle_at != started_at + timedelta(
            seconds=self.policy.interval_seconds
        ):
            raise MarketDataInconsistencyError("A temporalidade do ciclo é inválida.")
        if (
            not isinstance(self.results, tuple)
            or not self.results
            or len(self.results) > self.policy.max_targets
        ):
            raise MarketDataInconsistencyError("O ciclo deve conter resultados válidos.")
        for result in self.results:
            _validate_result(result)
            if result.started_at < started_at or result.finished_at > finished_at:
                raise MarketDataInconsistencyError("Um resultado está fora do ciclo.")
        keys = tuple(result.target.key for result in self.results)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise MarketDataInconsistencyError("Os resultados devem ser únicos e ordenados.")
        expected_status = _aggregate_status(self.results)
        if self.status is not expected_status:
            raise MarketDataInconsistencyError("O estado agregado diverge dos targets.")
        canonical = _state_payload(self, include_identity=False)
        expected_checksum = _hash_document("adt-continuous-state-checksum-v1", canonical)
        expected_cycle_id = _hash_document(
            "adt-continuous-cycle-id-v1",
            {**canonical, "checksum": expected_checksum},
        )
        if self.checksum and self.checksum != expected_checksum:
            raise MarketDataInconsistencyError("O checksum do ciclo é inválido.")
        if self.cycle_id and self.cycle_id != expected_cycle_id:
            raise MarketDataInconsistencyError("A identidade do ciclo é inválida.")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)
        object.__setattr__(self, "next_cycle_at", next_cycle_at)
        object.__setattr__(self, "checksum", expected_checksum)
        object.__setattr__(self, "cycle_id", expected_cycle_id)


class InstrumentLookup(Protocol):
    async def get_asset(self, pair: TradingPair) -> Instrument: ...


class DatasetLeaseProvider(Protocol):
    def dataset_lease(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> AbstractContextManager[object]: ...


class IncrementalPlanner(Protocol):
    def incremental(
        self,
        store: ParquetCandleStore,
        instrument: Instrument,
        timeframe: Timeframe,
        *,
        now: datetime,
        overlap_candles: int,
        start: datetime | None = None,
    ) -> IncrementalUpdatePlan: ...


class IncrementalExecutor(Protocol):
    async def run(
        self,
        plan: BackfillPlan,
        pair: TradingPair,
        *,
        dry_run: bool = False,
    ) -> BackfillResult: ...


class ContinuousCollectionService:
    """Execute one sequential, failure-isolated incremental collection cycle."""

    def __init__(
        self,
        *,
        instruments: InstrumentLookup,
        history: DatasetLeaseProvider,
        planner: IncrementalPlanner,
        executor: IncrementalExecutor,
        store: ParquetCandleStore,
        policy: ContinuousCollectionPolicy,
        clock: Clock | None = None,
    ) -> None:
        _validate_policy(policy)
        self._instruments = instruments
        self._history = history
        self._planner = planner
        self._executor = executor
        self._store = store
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def validate_targets(
        self,
        targets: object,
    ) -> tuple[ContinuousCollectionTarget, ...]:
        return _validate_targets(targets, max_targets=self._policy.max_targets)

    async def collect_cycle(
        self,
        targets: tuple[ContinuousCollectionTarget, ...],
        *,
        cycle_index: int,
    ) -> ContinuousCollectionState:
        validated_targets = self.validate_targets(targets)
        _require_exact_int(
            cycle_index,
            field_name="cycle_index",
            minimum=1,
            maximum=_MAX_CYCLE_INDEX,
        )
        cycle_started = self._now()
        results: list[ContinuousTargetResult] = []
        for target in validated_targets:
            results.append(await self._collect_target(target))
        cycle_finished = self._now()
        return ContinuousCollectionState(
            cycle_index=cycle_index,
            status=_aggregate_status(tuple(results)),
            policy=self._policy,
            started_at=cycle_started,
            finished_at=cycle_finished,
            next_cycle_at=cycle_started + timedelta(seconds=self._policy.interval_seconds),
            results=tuple(results),
        )

    async def _collect_target(
        self,
        target: ContinuousCollectionTarget,
    ) -> ContinuousTargetResult:
        started_at = self._now()
        latest_closed_end = _latest_closed_end(started_at, target.timeframe)
        planned_job_id: str | None = None
        try:
            instrument = await self._instruments.get_asset(target.pair)
            validate_instrument(instrument)
            if instrument.pair != target.pair:
                raise MarketDataInconsistencyError(
                    "O catálogo retornou instrumento incompatível com o target."
                )
            if not instrument.active:
                raise InactiveInstrumentError()
            with self._history.dataset_lease(instrument, target.timeframe):
                _first, last_open_time, _count = self._store.first_last_count(
                    instrument.exchange,
                    instrument.market_type,
                    instrument.pair,
                    target.timeframe,
                )
                if (
                    last_open_time is not None
                    and target.timeframe.next_open_time(last_open_time) >= latest_closed_end
                ):
                    return ContinuousTargetResult(
                        target=target,
                        status=ContinuousTargetStatus.NOOP,
                        started_at=started_at,
                        finished_at=self._now(),
                        latest_closed_end=latest_closed_end,
                    )
                plan = self._planner.incremental(
                    self._store,
                    instrument,
                    target.timeframe,
                    now=started_at,
                    overlap_candles=self._policy.overlap_candles,
                    start=target.bootstrap_start(latest_closed_end),
                )
            backfill = _validate_incremental_plan(
                plan,
                target=target,
                latest_closed_end=latest_closed_end,
            )
            if backfill is None:
                return ContinuousTargetResult(
                    target=target,
                    status=ContinuousTargetStatus.NOOP,
                    started_at=started_at,
                    finished_at=self._now(),
                    latest_closed_end=latest_closed_end,
                )
            planned_job_id = backfill.job_id
            result = await self._executor.run(backfill, target.pair)
            _validate_backfill_result(result, plan=backfill)
            return ContinuousTargetResult(
                target=target,
                status=ContinuousTargetStatus.UPDATED,
                started_at=started_at,
                finished_at=self._now(),
                latest_closed_end=latest_closed_end,
                job_id=result.job_id,
                fetched_count=result.fetched_count,
                stored_count=result.stored_count,
                duplicate_count=result.duplicate_count,
                request_count=result.request_count,
            )
        except Exception as error:
            error_code = _safe_error_code(error)
            logger.warning(
                "Continuous market-data target failed",
                extra={"target": target.key, "failure_code": error_code},
            )
            return ContinuousTargetResult(
                target=target,
                status=ContinuousTargetStatus.FAILED,
                started_at=started_at,
                finished_at=self._now(),
                latest_closed_end=latest_closed_end,
                job_id=planned_job_id,
                error_code=error_code,
            )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise MarketDataInconsistencyError("O relógio da coleta é inválido.")
        return require_utc(value, field_name="collection_clock")


class ContinuousCollectionStateStore:
    """Atomic latest-cycle state used by the worker and read-only API."""

    def __init__(self, data_dir: Path) -> None:
        self._root = market_root(data_dir)
        self._directory = ensure_safe_path(self._root, self._root / "continuous")
        self._path = ensure_safe_path(self._root, self._directory / "state.json")

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> ContinuousCollectionState | None:
        path = ensure_safe_path(self._root, self._path)
        if not path.exists():
            return None
        try:
            raw = path.read_bytes()
        except OSError:
            raise MarketDataStorageError("O estado da coleta não pôde ser lido.") from None
        if len(raw) > _MAX_STATE_BYTES:
            raise MarketDataStorageError("O estado da coleta excede o limite seguro.")
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, ValueError):
            raise MarketDataStorageError("O estado da coleta é inválido.") from None
        state = _decode_state(payload)
        if raw != _canonical_bytes(_state_payload(state, include_identity=True)):
            raise MarketDataStorageError("O estado da coleta não é canônico.")
        return state

    def write(self, state: ContinuousCollectionState) -> ContinuousCollectionState:
        _validate_state(state)
        existing = self.read()
        if existing is not None:
            if state == existing:
                return existing
            if state.cycle_index != existing.cycle_index + 1:
                raise MarketDataStorageError("O índice do ciclo não é monotônico.")
        payload = _state_payload(state, include_identity=True)
        encoded = _canonical_bytes(payload)
        if len(encoded) > _MAX_STATE_BYTES:
            raise MarketDataStorageError("O estado da coleta excede o limite seguro.")
        directory = ensure_safe_path(self._root, self._directory)
        path = ensure_safe_path(self._root, self._path)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = ensure_safe_path(
            self._root,
            self._directory / f".state.tmp-{os.getpid()}-{uuid4().hex}",
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            fsync_directory(directory)
        except FileExistsError:
            temporary.unlink(missing_ok=True)
            raise MarketDataStorageError("Já existe publicação temporária da coleta.") from None
        except OSError:
            temporary.unlink(missing_ok=True)
            raise MarketDataStorageError("O estado da coleta não pôde ser persistido.") from None
        persisted = self.read()
        if persisted is None or persisted != state:
            raise MarketDataStorageError("O estado persistido da coleta diverge.")
        return persisted


class ContinuousCollectionRunner:
    """Hold one process-wide lease and execute cycles at a fixed cadence."""

    def __init__(
        self,
        *,
        service: ContinuousCollectionService,
        state_store: ContinuousCollectionStateStore,
        lock_manager: DatasetLockManager,
        sleeper: Sleeper = asyncio.sleep,
        clock: Clock | None = None,
        startup_hook: StartupHook | None = None,
    ) -> None:
        self._service = service
        self._state_store = state_store
        self._lock_manager = lock_manager
        self._sleeper = sleeper
        self._clock = clock or (lambda: datetime.now(UTC))
        self._startup_hook = startup_hook

    async def run(
        self,
        targets: tuple[ContinuousCollectionTarget, ...],
        *,
        max_cycles: int | None = None,
    ) -> ContinuousCollectionState:
        if max_cycles is not None:
            _require_exact_int(
                max_cycles,
                field_name="max_cycles",
                minimum=1,
                maximum=1_000_000,
            )
        validated_targets = self._service.validate_targets(targets)
        last_state: ContinuousCollectionState | None = None
        with self._lock_manager.acquire(_COLLECTION_LOCK_KEY):
            if self._startup_hook is not None:
                self._startup_hook()
            previous = self._state_store.read()
            cycle_index = previous.cycle_index + 1 if previous is not None else 1
            completed = 0
            while max_cycles is None or completed < max_cycles:
                state = await self._service.collect_cycle(
                    validated_targets,
                    cycle_index=cycle_index,
                )
                last_state = self._state_store.write(state)
                completed += 1
                cycle_index += 1
                if max_cycles is not None and completed >= max_cycles:
                    break
                delay = max(0.0, (state.next_cycle_at - self._now()).total_seconds())
                await self._sleeper(delay)
        if last_state is None:
            raise MarketDataInconsistencyError("Nenhum ciclo de coleta foi executado.")
        return last_state

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise MarketDataInconsistencyError("O relógio do runner é inválido.")
        return require_utc(value, field_name="runner_clock")


def collection_target_from_text(
    value: str,
    *,
    bootstrap_candles: int,
) -> ContinuousCollectionTarget:
    """Parse strict ``BASE/QUOTE:TIMEFRAME`` CLI input."""
    if not isinstance(value, str) or value.count(":") != 1:
        raise MarketDataInconsistencyError("Use o target no formato BASE/QUOTE:TIMEFRAME.")
    symbol, timeframe_code = value.rsplit(":", 1)
    try:
        pair = TradingPair.parse(symbol)
        timeframe = get_timeframe(timeframe_code)
    except DomainError:
        raise
    except (TypeError, ValueError):
        raise MarketDataInconsistencyError(
            "Use o target no formato BASE/QUOTE:TIMEFRAME."
        ) from None
    return ContinuousCollectionTarget(pair, timeframe, bootstrap_candles)


def validate_continuous_collection_state(state: object) -> ContinuousCollectionState:
    return _validate_state(state)


def validate_continuous_collection_targets(
    targets: object,
    *,
    max_targets: int,
) -> tuple[ContinuousCollectionTarget, ...]:
    _require_exact_int(
        max_targets,
        field_name="max_targets",
        minimum=1,
        maximum=_MAX_TARGETS_ABSOLUTE,
    )
    return _validate_targets(targets, max_targets=max_targets)


def _validate_incremental_plan(
    plan: object,
    *,
    target: ContinuousCollectionTarget,
    latest_closed_end: datetime,
) -> BackfillPlan | None:
    if not isinstance(plan, IncrementalUpdatePlan):
        raise MarketDataInconsistencyError("O planner incremental retornou valor inválido.")
    expected_end = _validate_datetime(latest_closed_end, field_name="latest_closed_end")
    plan_end = _validate_datetime(plan.latest_closed_end, field_name="plan.latest_closed_end")
    if plan_end != expected_end or type(plan.action) is not str:
        raise MarketDataInconsistencyError("O plano incremental diverge do ciclo.")
    if plan.last_open_time is not None:
        last_open_time = _validate_datetime(
            plan.last_open_time,
            field_name="plan.last_open_time",
        )
        if (
            not target.timeframe.validate_open_time(last_open_time)
            or last_open_time >= expected_end
        ):
            raise MarketDataInconsistencyError("A cobertura do plano incremental é inválida.")
    if plan.action == "NOOP":
        if plan.backfill is not None:
            raise MarketDataInconsistencyError("O plano NOOP não pode conter backfill.")
        return None
    if plan.action != "RUN" or not isinstance(plan.backfill, BackfillPlan):
        raise MarketDataInconsistencyError("A ação do plano incremental é inválida.")
    backfill = plan.backfill
    _normalize_job_id(backfill.job_id)
    if (
        backfill.dataset_key != target.dataset_key
        or backfill.timeframe != target.timeframe
        or backfill.job_type is not MarketJobType.INCREMENTAL
        or not isinstance(backfill.data_range, DataRange)
        or backfill.data_range.end != expected_end
        or not target.timeframe.validate_open_time(backfill.data_range.start)
        or not target.timeframe.validate_open_time(backfill.data_range.end)
    ):
        raise MarketDataInconsistencyError("O backfill incremental diverge do target.")
    _require_exact_int(
        backfill.expected_candles,
        field_name="backfill.expected_candles",
        minimum=1,
        maximum=10_000_000,
    )
    _require_exact_int(
        backfill.chunk_candles,
        field_name="backfill.chunk_candles",
        minimum=1,
        maximum=10_000_000,
    )
    if not isinstance(backfill.chunks, tuple) or not backfill.chunks:
        raise MarketDataInconsistencyError("Os chunks incrementais são inválidos.")
    cursor = backfill.data_range.start
    total = 0
    for index, chunk in enumerate(backfill.chunks):
        if (
            not isinstance(chunk, BackfillChunk)
            or type(chunk.index) is not int
            or chunk.index != index
            or not isinstance(chunk.data_range, DataRange)
            or chunk.data_range.start != cursor
            or chunk.data_range.end > backfill.data_range.end
        ):
            raise MarketDataInconsistencyError("Um chunk incremental é inválido.")
        count = (chunk.data_range.end - chunk.data_range.start) // target.timeframe.duration
        if (
            type(chunk.expected_candles) is not int
            or chunk.expected_candles != count
            or count < 1
            or count > backfill.chunk_candles
            or chunk.data_range.start + count * target.timeframe.duration != chunk.data_range.end
        ):
            raise MarketDataInconsistencyError("A cardinalidade do chunk é inválida.")
        total += count
        cursor = chunk.data_range.end
    if cursor != backfill.data_range.end or total != backfill.expected_candles:
        raise MarketDataInconsistencyError("A cardinalidade do backfill é inválida.")
    return backfill


def _validate_backfill_result(result: object, *, plan: BackfillPlan) -> None:
    if not isinstance(result, BackfillResult):
        raise MarketDataInconsistencyError("O executor incremental retornou valor inválido.")
    job_id = _normalize_job_id(result.job_id)
    if (
        result.status is not MarketJobStatus.COMPLETED
        or job_id != plan.job_id
        or type(result.chunks_completed) is not int
        or type(result.total_chunks) is not int
        or result.chunks_completed != len(plan.chunks)
        or result.total_chunks != len(plan.chunks)
    ):
        raise MarketDataInconsistencyError("A atualização incremental não foi concluída.")
    for field_name, value in (
        ("result.fetched_count", result.fetched_count),
        ("result.stored_count", result.stored_count),
        ("result.duplicate_count", result.duplicate_count),
        ("result.request_count", result.request_count),
    ):
        _require_exact_int(value, field_name=field_name, minimum=0, maximum=10**12)
    if result.stored_count + result.duplicate_count > result.fetched_count:
        raise MarketDataInconsistencyError("Os contadores do executor são inválidos.")


def _validate_policy(policy: object) -> None:
    if not isinstance(policy, ContinuousCollectionPolicy):
        raise MarketDataInconsistencyError("A política de coleta é inválida.")
    ContinuousCollectionPolicy.__post_init__(policy)


def _validate_target(target: object) -> None:
    if not isinstance(target, ContinuousCollectionTarget):
        raise MarketDataInconsistencyError("O target de coleta é inválido.")
    ContinuousCollectionTarget.__post_init__(target)


def _validate_targets(
    targets: object,
    *,
    max_targets: int,
) -> tuple[ContinuousCollectionTarget, ...]:
    if not isinstance(targets, tuple) or not targets or len(targets) > max_targets:
        raise MarketDataInconsistencyError("A lista de targets é inválida.")
    for target in targets:
        _validate_target(target)
    ordered = tuple(sorted(targets, key=lambda item: item.key))
    if ordered != targets or len({target.key for target in targets}) != len(targets):
        raise MarketDataInconsistencyError("Os targets devem ser únicos e ordenados.")
    return targets


def _validate_result(result: object) -> None:
    if not isinstance(result, ContinuousTargetResult):
        raise MarketDataInconsistencyError("O resultado do target é inválido.")
    ContinuousTargetResult.__post_init__(result)


def _validate_state(state: object) -> ContinuousCollectionState:
    if not isinstance(state, ContinuousCollectionState):
        raise MarketDataInconsistencyError("O estado da coleta é inválido.")
    ContinuousCollectionState.__post_init__(state)
    return state


def _validate_pair(pair: object) -> None:
    if (
        not isinstance(pair, TradingPair)
        or not isinstance(pair.base, str)
        or not isinstance(pair.quote, str)
    ):
        raise MarketDataInconsistencyError("O par do target é inválido.")
    if TradingPair(pair.base, pair.quote) != pair:
        raise MarketDataInconsistencyError("O par do target não é canônico.")


def _validate_timeframe(timeframe: object) -> None:
    if not isinstance(timeframe, Timeframe) or not isinstance(timeframe.code, str):
        raise MarketDataInconsistencyError("O timeframe do target é inválido.")
    try:
        canonical = get_timeframe(timeframe.code)
    except DomainError:
        raise MarketDataInconsistencyError("O timeframe do target é inválido.") from None
    if canonical != timeframe:
        raise MarketDataInconsistencyError("O timeframe do target não é canônico.")


def _validate_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise MarketDataInconsistencyError(f"{field_name} é inválido.")
    return require_utc(value, field_name=field_name)


def _normalize_job_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MarketDataInconsistencyError("O job_id é inválido.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise MarketDataInconsistencyError("O job_id é inválido.") from None
    if str(parsed) != value or parsed.int == 0:
        raise MarketDataInconsistencyError("O job_id é inválido.")
    return value


def _safe_error_code(error: Exception) -> str:
    candidate = error.code if isinstance(error, DomainError) else "collection_target_failed"
    if not isinstance(candidate, str) or _ERROR_CODE_PATTERN.fullmatch(candidate) is None:
        return "collection_target_failed"
    return candidate


def _normalize_error_code(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MarketDataInconsistencyError("O código de falha é inválido.")
    if (
        value.strip() != value
        or len(value) > _MAX_ERROR_CODE_LENGTH
        or _ERROR_CODE_PATTERN.fullmatch(value) is None
    ):
        raise MarketDataInconsistencyError("O código de falha é inválido.")
    return value


def _aggregate_status(results: tuple[ContinuousTargetResult, ...]) -> ContinuousCycleStatus:
    failed = sum(result.status is ContinuousTargetStatus.FAILED for result in results)
    if failed == 0:
        return ContinuousCycleStatus.COMPLETED
    if failed == len(results):
        return ContinuousCycleStatus.FAILED
    return ContinuousCycleStatus.PARTIALLY_FAILED


def _latest_closed_end(now: datetime, timeframe: Timeframe) -> datetime:
    current = require_utc(now, field_name="now")
    epoch = datetime(1970, 1, 1, tzinfo=UTC) + timeframe.alignment
    periods = (current - epoch) // timeframe.duration
    return epoch + periods * timeframe.duration


def _require_exact_int(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MarketDataInconsistencyError(f"{field_name} é inválido.")
    return value


def _result_payload(result: ContinuousTargetResult) -> dict[str, object]:
    return {
        "target": {
            "symbol": result.target.pair.symbol,
            "timeframe": result.target.timeframe.code,
            "bootstrap_candles": result.target.bootstrap_candles,
        },
        "status": result.status.value,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "latest_closed_end": result.latest_closed_end.isoformat(),
        "job_id": result.job_id,
        "fetched_count": result.fetched_count,
        "stored_count": result.stored_count,
        "duplicate_count": result.duplicate_count,
        "request_count": result.request_count,
        "error_code": result.error_code,
    }


def _state_payload(
    state: ContinuousCollectionState,
    *,
    include_identity: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": state.schema_version,
        "cycle_index": state.cycle_index,
        "status": state.status.value,
        "policy": {
            "interval_seconds": state.policy.interval_seconds,
            "overlap_candles": state.policy.overlap_candles,
            "max_targets": state.policy.max_targets,
        },
        "started_at": state.started_at.isoformat(),
        "finished_at": state.finished_at.isoformat(),
        "next_cycle_at": state.next_cycle_at.isoformat(),
        "results": [_result_payload(result) for result in state.results],
    }
    if include_identity:
        payload["cycle_id"] = state.cycle_id
        payload["checksum"] = state.checksum
    return payload


def _canonical_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash_document(domain: str, payload: Mapping[str, object]) -> str:
    return sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(payload)).hexdigest()


def _decode_state(payload: object) -> ContinuousCollectionState:
    if not isinstance(payload, dict) or set(payload) != _COLLECTION_STATE_KEYS:
        raise MarketDataStorageError("O documento de estado da coleta é inválido.")
    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise MarketDataStorageError("Os resultados persistidos da coleta são inválidos.")
    results: list[ContinuousTargetResult] = []
    try:
        for raw in raw_results:
            if not isinstance(raw, dict) or set(raw) != _RESULT_KEYS:
                raise MarketDataStorageError("Um resultado persistido da coleta é inválido.")
            raw_target = raw["target"]
            if not isinstance(raw_target, dict) or set(raw_target) != {
                "symbol",
                "timeframe",
                "bootstrap_candles",
            }:
                raise MarketDataStorageError("Um target persistido da coleta é inválido.")
            target = ContinuousCollectionTarget(
                TradingPair.parse(_require_string(raw_target["symbol"])),
                get_timeframe(_require_string(raw_target["timeframe"])),
                _require_int(raw_target["bootstrap_candles"]),
            )
            results.append(
                ContinuousTargetResult(
                    target=target,
                    status=ContinuousTargetStatus(_require_string(raw["status"])),
                    started_at=_parse_datetime(raw["started_at"]),
                    finished_at=_parse_datetime(raw["finished_at"]),
                    latest_closed_end=_parse_datetime(raw["latest_closed_end"]),
                    job_id=_optional_string(raw["job_id"]),
                    fetched_count=_require_int(raw["fetched_count"]),
                    stored_count=_require_int(raw["stored_count"]),
                    duplicate_count=_require_int(raw["duplicate_count"]),
                    request_count=_require_int(raw["request_count"]),
                    error_code=_optional_string(raw["error_code"]),
                )
            )
        raw_policy = payload["policy"]
        if not isinstance(raw_policy, dict) or set(raw_policy) != {
            "interval_seconds",
            "overlap_candles",
            "max_targets",
        }:
            raise MarketDataStorageError("A política persistida da coleta é inválida.")
        return ContinuousCollectionState(
            schema_version=_require_int(payload["schema_version"]),
            cycle_index=_require_int(payload["cycle_index"]),
            cycle_id=_require_string(payload["cycle_id"]),
            status=ContinuousCycleStatus(_require_string(payload["status"])),
            policy=ContinuousCollectionPolicy(
                interval_seconds=_require_int(raw_policy["interval_seconds"]),
                overlap_candles=_require_int(raw_policy["overlap_candles"]),
                max_targets=_require_int(raw_policy["max_targets"]),
            ),
            started_at=_parse_datetime(payload["started_at"]),
            finished_at=_parse_datetime(payload["finished_at"]),
            next_cycle_at=_parse_datetime(payload["next_cycle_at"]),
            results=tuple(results),
            checksum=_require_string(payload["checksum"]),
        )
    except (KeyError, TypeError, ValueError, DomainError):
        raise MarketDataStorageError("O estado persistido da coleta é inválido.") from None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate JSON key")
        payload[key] = value
    return payload


def _parse_datetime(value: object) -> datetime:
    raw = _require_string(value)
    parsed = datetime.fromisoformat(raw)
    if parsed.isoformat() != raw:
        raise ValueError("noncanonical datetime")
    return require_utc(parsed, field_name="persisted_datetime")


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _require_string(value)


def _require_int(value: object) -> int:
    if type(value) is not int:
        raise TypeError("expected int")
    return value
