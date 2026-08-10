"""Durable Phase 2B job checkpoints and single-host dataset locks."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from app.market_data.domain import DataRange
from app.market_data.errors import (
    InvalidDataRangeError,
    MarketDataInconsistencyError,
    MarketDataStorageError,
    MarketJobLockTimeoutError,
    MarketJobNotFoundError,
    UnsupportedTimeframeError,
)
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager
from app.market_data.planning import (
    BackfillPlan,
    BackfillProgress,
    MarketJobStatus,
    MarketJobType,
    backfill_plan_checksum,
    expected_candle_count,
)
from app.market_data.timeframes import get_timeframe

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class MarketJobRecord:
    job_id: str
    dataset_key: str
    job_type: MarketJobType
    status: MarketJobStatus
    timeframe: str
    start: str
    end: str
    chunk_ranges: tuple[tuple[str, str], ...]
    plan_checksum: str
    next_chunk_index: int
    chunks_completed: int
    candles_expected: int
    chunk_candles: int
    candles_fetched: int
    candles_stored: int
    duplicates: int
    request_count: int
    started_at: str
    updated_at: str
    finished_at: str | None
    error_code: str | None


class MarketJobCatalog:
    """Atomic JSON job state separate from candle/catalog transactions."""

    def __init__(
        self,
        data_dir: Path,
        *,
        clock: Clock | None = None,
        stale_after_seconds: float = 3_600,
    ) -> None:
        self._root = market_root(data_dir)
        self._path = ensure_safe_path(self._root, self._root / "jobs.json")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stale_after = stale_after_seconds

    @property
    def path(self) -> Path:
        return self._path

    def create(self, plan: BackfillPlan) -> MarketJobRecord:
        with self._catalog_guard():
            state = self._load()
            if plan.job_id in state:
                raw_existing = state[plan.job_id]
                if not isinstance(raw_existing, dict):
                    raise MarketDataStorageError("O catálogo de jobs é inválido.")
                existing = self._decode(raw_existing)
                if existing.plan_checksum != backfill_plan_checksum(plan):
                    raise MarketDataInconsistencyError("O plano persistido do job é imutável.")
                return existing
            now = self._now()
            record = MarketJobRecord(
                job_id=plan.job_id,
                dataset_key=plan.dataset_key,
                job_type=plan.job_type,
                status=MarketJobStatus.PLANNED,
                timeframe=plan.timeframe.code,
                start=plan.data_range.start.isoformat(),
                end=plan.data_range.end.isoformat(),
                chunk_ranges=tuple(
                    (chunk.data_range.start.isoformat(), chunk.data_range.end.isoformat())
                    for chunk in plan.chunks
                ),
                plan_checksum=backfill_plan_checksum(plan),
                next_chunk_index=0,
                chunks_completed=0,
                candles_expected=plan.expected_candles,
                chunk_candles=plan.chunk_candles,
                candles_fetched=0,
                candles_stored=0,
                duplicates=0,
                request_count=0,
                started_at=now,
                updated_at=now,
                finished_at=None,
                error_code=None,
            )
            self._validate_record(record)
            state[record.job_id] = _encode_record(record)
            self._write(state)
            return record

    def get(self, job_id: str) -> MarketJobRecord:
        _require_uuid(job_id)
        raw = self._load().get(job_id)
        if not isinstance(raw, dict):
            raise MarketJobNotFoundError()
        return self._decode(raw)

    def start(self, job_id: str) -> MarketJobRecord:
        record = self.get(job_id)
        if record.status not in {
            MarketJobStatus.PLANNED,
            MarketJobStatus.PAUSED,
            MarketJobStatus.FAILED,
        }:
            raise MarketDataInconsistencyError("O job não pode ser iniciado neste estado.")
        return self._replace(
            record,
            status=MarketJobStatus.RUNNING,
            updated_at=self._now(),
            finished_at=None,
            error_code=None,
        )

    def advance(
        self,
        job_id: str,
        *,
        chunk_index: int,
        fetched: int,
        stored: int,
        duplicates: int,
        requests: int,
    ) -> MarketJobRecord:
        metrics = (fetched, stored, duplicates, requests)
        if (
            any(type(value) is not int or value < 0 for value in metrics)
            or stored + duplicates > fetched
        ):
            raise MarketDataInconsistencyError("As métricas do chunk são incoerentes.")
        record = self.get(job_id)
        if (
            record.status
            not in {
                MarketJobStatus.RUNNING,
                MarketJobStatus.PAUSED,
                MarketJobStatus.CANCELLED,
            }
            or chunk_index != record.next_chunk_index
        ):
            raise MarketDataInconsistencyError("O checkpoint do job diverge do chunk confirmado.")
        next_index = chunk_index + 1
        completed = next_index == len(record.chunk_ranges)
        now = self._now()
        status: MarketJobStatus = record.status
        if completed and status is not MarketJobStatus.CANCELLED:
            status = MarketJobStatus.COMPLETED
        return self._replace(
            record,
            status=status,
            next_chunk_index=next_index,
            chunks_completed=next_index,
            candles_fetched=record.candles_fetched + fetched,
            candles_stored=record.candles_stored + stored,
            duplicates=record.duplicates + duplicates,
            request_count=record.request_count + requests,
            updated_at=now,
            finished_at=now if completed else record.finished_at,
        )

    def pause(self, job_id: str) -> MarketJobRecord:
        record = self.get(job_id)
        if record.status is not MarketJobStatus.RUNNING:
            raise MarketDataInconsistencyError("Somente job RUNNING pode ser pausado.")
        return self._replace(
            record,
            status=MarketJobStatus.PAUSED,
            updated_at=self._now(),
        )

    def cancel(self, job_id: str) -> MarketJobRecord:
        record = self.get(job_id)
        if record.status not in {
            MarketJobStatus.PLANNED,
            MarketJobStatus.RUNNING,
            MarketJobStatus.PAUSED,
            MarketJobStatus.FAILED,
        }:
            raise MarketDataInconsistencyError("O job não pode ser cancelado neste estado.")
        now = self._now()
        return self._replace(
            record,
            status=MarketJobStatus.CANCELLED,
            updated_at=now,
            finished_at=now,
        )

    def fail(self, job_id: str, error_code: str) -> MarketJobRecord:
        record = self.get(job_id)
        if record.status in {MarketJobStatus.COMPLETED, MarketJobStatus.CANCELLED}:
            return record
        now = self._now()
        return self._replace(
            record,
            status=MarketJobStatus.FAILED,
            updated_at=now,
            finished_at=now,
            error_code=_sanitize_error_code(error_code),
        )

    def recover_abandoned(self) -> int:
        snapshot = self._load()
        records: list[MarketJobRecord] = []
        for raw in snapshot.values():
            if not isinstance(raw, dict):
                raise MarketDataStorageError("O catálogo de jobs é inválido.")
            record = self._decode(raw)
            if record.status is MarketJobStatus.RUNNING:
                records.append(record)
        changed = 0
        for record in records:
            try:
                lock_manager = DatasetLockManager(
                    self._root.parent,
                    timeout_seconds=0,
                    stale_after_seconds=self._stale_after,
                    clock=self._clock,
                )
                with lock_manager.acquire(record.dataset_key), self._catalog_guard():
                    state = self._load()
                    raw_current = state.get(record.job_id)
                    if not isinstance(raw_current, dict):
                        continue
                    current = self._decode(raw_current)
                    if current != record or current.status is not MarketJobStatus.RUNNING:
                        continue
                    now = self._now()
                    state[record.job_id] = _encode_record(
                        replace(
                            current,
                            status=MarketJobStatus.FAILED,
                            updated_at=now,
                            finished_at=now,
                            error_code="interrupted_job",
                        )
                    )
                    self._write(state)
                    changed += 1
            except MarketJobLockTimeoutError:
                continue
        return changed

    def progress(self, job_id: str) -> BackfillProgress:
        record = self.get(job_id)
        next_start = (
            datetime.fromisoformat(record.chunk_ranges[record.next_chunk_index][0])
            if record.next_chunk_index < len(record.chunk_ranges)
            else datetime.fromisoformat(record.end)
        )
        return BackfillProgress(
            job_id=record.job_id,
            status=record.status,
            chunks_completed=record.chunks_completed,
            total_chunks=len(record.chunk_ranges),
            next_start=next_start,
            fetched_count=record.candles_fetched,
            stored_count=record.candles_stored,
            duplicate_count=record.duplicates,
        )

    def _replace(self, record: MarketJobRecord, **changes: object) -> MarketJobRecord:
        updated = replace(record, **cast(dict[str, Any], changes))
        self._validate_record(updated)
        with self._catalog_guard():
            state = self._load()
            current = state.get(record.job_id)
            if not isinstance(current, dict) or self._decode(current) != record:
                raise MarketDataInconsistencyError("O estado persistido do job divergiu.")
            state[record.job_id] = _encode_record(updated)
            self._write(state)
            return updated

    def _decode(self, raw: dict[str, object]) -> MarketJobRecord:
        try:
            payload = dict(raw)
            payload["job_type"] = MarketJobType(str(payload["job_type"]))
            payload["status"] = MarketJobStatus(str(payload["status"]))
            ranges = cast(list[list[object]], payload["chunk_ranges"])
            payload["chunk_ranges"] = tuple((str(item[0]), str(item[1])) for item in ranges)
            record = MarketJobRecord(**payload)  # type: ignore[arg-type]
            _require_uuid(record.job_id)
        except (KeyError, TypeError, ValueError, IndexError):
            raise MarketDataStorageError("O catálogo de jobs é inválido.") from None
        if record.plan_checksum != _record_checksum(record):
            raise MarketDataStorageError("O checkpoint possui plano divergente.")
        self._validate_record(record)
        return record

    def _load(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise MarketDataStorageError() from None
        if not isinstance(raw, dict):
            raise MarketDataStorageError()
        for external_job_id, record in raw.items():
            if (
                not isinstance(external_job_id, str)
                or not isinstance(record, dict)
                or record.get("job_id") != external_job_id
            ):
                raise MarketDataStorageError("A chave externa do job diverge do checkpoint.")
        return raw

    def _write(self, state: dict[str, object]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        temporary = ensure_safe_path(
            self._root,
            self._path.with_name(f".jobs.json.tmp-{uuid4()}"),
        )
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            fsync_directory(self._root)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise MarketDataStorageError() from None

    def _now(self) -> str:
        return self._clock().astimezone(UTC).isoformat()

    def _validate_record(self, record: MarketJobRecord) -> None:
        counters = (
            record.next_chunk_index,
            record.chunks_completed,
            record.candles_expected,
            record.chunk_candles,
            record.candles_fetched,
            record.candles_stored,
            record.duplicates,
            record.request_count,
        )
        if (
            any(type(value) is not int or value < 0 for value in counters)
            or not record.chunk_ranges
            or record.chunk_candles < 1
            or record.next_chunk_index != record.chunks_completed
            or record.next_chunk_index > len(record.chunk_ranges)
            or record.candles_stored + record.duplicates > record.candles_fetched
        ):
            raise MarketDataStorageError("Os contadores do checkpoint são inválidos.")
        try:
            timeframe = get_timeframe(record.timeframe)
        except UnsupportedTimeframeError:
            raise MarketDataStorageError("O timeframe do checkpoint é inválido.") from None
        ranges: list[DataRange] = []
        try:
            for start, end in record.chunk_ranges:
                ranges.append(DataRange(datetime.fromisoformat(start), datetime.fromisoformat(end)))
        except (ValueError, InvalidDataRangeError):
            raise MarketDataStorageError("Os chunks do checkpoint são inválidos.") from None
        if any(left.end > right.start for left, right in zip(ranges, ranges[1:], strict=False)):
            raise MarketDataStorageError("Os chunks do checkpoint se sobrepõem.")
        try:
            total_range = DataRange(
                datetime.fromisoformat(record.start),
                datetime.fromisoformat(record.end),
            )
        except (ValueError, InvalidDataRangeError):
            raise MarketDataStorageError("O intervalo do checkpoint é inválido.") from None
        if record.job_type in {MarketJobType.BACKFILL, MarketJobType.INCREMENTAL}:
            if (
                ranges[0].start != total_range.start
                or ranges[-1].end != total_range.end
                or any(
                    left.end != right.start for left, right in zip(ranges, ranges[1:], strict=False)
                )
            ):
                raise MarketDataStorageError("Os chunks não cobrem exatamente o intervalo.")
        elif any(item.start < total_range.start or item.end > total_range.end for item in ranges):
            raise MarketDataStorageError("Um chunk de reparo está fora do intervalo.")
        try:
            expected = sum(expected_candle_count(item, timeframe) for item in ranges)
        except InvalidDataRangeError:
            raise MarketDataStorageError("Os chunks do checkpoint são inválidos.") from None
        if expected != record.candles_expected:
            raise MarketDataStorageError("A quantidade esperada do checkpoint diverge.")
        terminal = record.status in {
            MarketJobStatus.COMPLETED,
            MarketJobStatus.FAILED,
            MarketJobStatus.CANCELLED,
        }
        if terminal != (record.finished_at is not None):
            raise MarketDataStorageError("O lifecycle do checkpoint é incoerente.")
        if record.status is MarketJobStatus.COMPLETED and record.next_chunk_index != len(
            record.chunk_ranges
        ):
            raise MarketDataStorageError("Job concluído possui chunks pendentes.")
        if (
            record.status is MarketJobStatus.COMPLETED
            and record.job_type in {MarketJobType.BACKFILL, MarketJobType.INCREMENTAL}
            and record.candles_fetched != record.candles_expected
        ):
            raise MarketDataStorageError("As métricas finais do job são incoerentes.")
        if (record.status is MarketJobStatus.FAILED) != (record.error_code is not None):
            raise MarketDataStorageError("O erro do checkpoint é incoerente.")

    @contextmanager
    def _catalog_guard(self) -> Iterator[None]:
        self._root.mkdir(parents=True, exist_ok=True)
        path = ensure_safe_path(self._root, self._root / ".jobs.lock")
        with path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _record_checksum(record: MarketJobRecord) -> str:
    payload = "|".join(
        (
            record.dataset_key,
            record.job_type.value,
            record.timeframe,
            record.start,
            record.end,
            str(record.candles_expected),
            str(record.chunk_candles),
            *(f"{start}:{end}" for start, end in record.chunk_ranges),
        )
    )
    return sha256(payload.encode()).hexdigest()


def _encode_record(record: MarketJobRecord) -> dict[str, object]:
    return asdict(record)


def _require_uuid(value: str) -> None:
    try:
        UUID(value)
    except ValueError:
        raise MarketJobNotFoundError() from None


def _sanitize_error_code(value: str) -> str:
    sanitized = "".join(character for character in value if character.isalnum() or character == "_")
    return (sanitized or "job_failed")[:64]
