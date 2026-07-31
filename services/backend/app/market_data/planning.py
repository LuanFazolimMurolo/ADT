"""Deterministic Phase 2B backfill, incremental and gap planning."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from app.market_data.domain import DataRange, Instrument, Timeframe, require_utc
from app.market_data.errors import InvalidDataRangeError
from app.market_data.storage import ParquetCandleStore


class MarketJobType(StrEnum):
    BACKFILL = "BACKFILL"
    INCREMENTAL = "INCREMENTAL"
    GAP_REPAIR = "GAP_REPAIR"


class MarketJobStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class BackfillChunk:
    index: int
    data_range: DataRange
    expected_candles: int


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    job_id: str
    dataset_key: str
    timeframe: Timeframe
    data_range: DataRange
    chunks: tuple[BackfillChunk, ...]
    expected_candles: int
    chunk_candles: int
    job_type: MarketJobType = MarketJobType.BACKFILL


@dataclass(frozen=True, slots=True)
class BackfillProgress:
    job_id: str
    status: MarketJobStatus
    chunks_completed: int
    total_chunks: int
    next_start: datetime
    fetched_count: int
    stored_count: int
    duplicate_count: int


@dataclass(frozen=True, slots=True)
class BackfillResult:
    job_id: str
    status: MarketJobStatus
    chunks_completed: int
    total_chunks: int
    fetched_count: int
    stored_count: int
    duplicate_count: int
    request_count: int


@dataclass(frozen=True, slots=True)
class IncrementalUpdatePlan:
    action: str
    backfill: BackfillPlan | None
    last_open_time: datetime | None
    latest_closed_end: datetime


@dataclass(frozen=True, slots=True)
class GapRepairPlan:
    backfill: BackfillPlan | None
    gap_ranges: tuple[DataRange, ...]
    severity: str = "WARNING"


def expected_candle_count(data_range: DataRange, timeframe: Timeframe) -> int:
    """Return the exact half-open interval cardinality using integer arithmetic."""
    if not timeframe.validate_open_time(data_range.start) or not timeframe.validate_open_time(
        data_range.end
    ):
        raise InvalidDataRangeError("O intervalo deve estar alinhado ao timeframe.")
    delta = data_range.end - data_range.start
    count = delta // timeframe.duration
    if count < 1 or data_range.start + count * timeframe.duration != data_range.end:
        raise InvalidDataRangeError()
    return count


class MarketDataPlanner:
    """Pure planner bounded by source, call and job safety limits."""

    def __init__(
        self,
        *,
        adapter_request_limit: int,
        max_fetch_candles: int,
        chunk_candles: int,
        max_total_candles: int,
        max_chunks: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        values = (
            adapter_request_limit,
            max_fetch_candles,
            chunk_candles,
            max_total_candles,
            max_chunks,
        )
        if any(value < 1 for value in values):
            raise ValueError("planning limits must be positive")
        self._chunk_candles = min(
            adapter_request_limit,
            max_fetch_candles,
            chunk_candles,
        )
        self._max_total_candles = max_total_candles
        self._max_chunks = max_chunks
        self._clock = clock or (lambda: datetime.now(UTC))

    def backfill(
        self,
        dataset_key: str,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        job_type: MarketJobType = MarketJobType.BACKFILL,
        job_id: str | None = None,
        latest_closed_at: datetime | None = None,
    ) -> BackfillPlan:
        total = expected_candle_count(data_range, timeframe)
        closed_reference = latest_closed_at or self._clock()
        if data_range.end > _latest_closed_end(closed_reference, timeframe):
            raise InvalidDataRangeError("O intervalo inclui candle ainda não fechado.")
        if total > self._max_total_candles:
            raise InvalidDataRangeError(
                "O intervalo excede o limite total seguro e deve ser dividido."
            )
        chunk_count = (total + self._chunk_candles - 1) // self._chunk_candles
        if chunk_count > self._max_chunks:
            raise InvalidDataRangeError("O plano excede o limite seguro de chunks.")
        chunks: list[BackfillChunk] = []
        cursor = data_range.start
        remaining = total
        for index in range(chunk_count):
            size = min(self._chunk_candles, remaining)
            end = cursor + size * timeframe.duration
            chunks.append(BackfillChunk(index, DataRange(cursor, end), size))
            cursor = end
            remaining -= size
        if cursor != data_range.end or remaining != 0:
            raise InvalidDataRangeError("O plano não cobre exatamente o intervalo.")
        return BackfillPlan(
            job_id=job_id or str(uuid4()),
            dataset_key=dataset_key,
            timeframe=timeframe,
            data_range=data_range,
            chunks=tuple(chunks),
            expected_candles=total,
            chunk_candles=self._chunk_candles,
            job_type=job_type,
        )

    def incremental(
        self,
        store: ParquetCandleStore,
        instrument: Instrument,
        timeframe: Timeframe,
        *,
        now: datetime,
        overlap_candles: int,
        start: datetime | None = None,
    ) -> IncrementalUpdatePlan:
        current = require_utc(now, field_name="now")
        if overlap_candles < 0:
            raise InvalidDataRangeError("A sobreposição incremental não pode ser negativa.")
        latest_end = _latest_closed_end(current, timeframe)
        first, last, _count = store.first_last_count(
            instrument.exchange,
            instrument.market_type,
            instrument.pair,
            timeframe,
        )
        if last is None:
            if start is None:
                raise InvalidDataRangeError(
                    "Dataset inexistente exige início incremental explícito."
                )
            candidate_start = start
        else:
            next_expected = timeframe.next_open_time(last)
            candidate_start = next_expected - overlap_candles * timeframe.duration
            if first is not None:
                candidate_start = max(candidate_start, first)
        if candidate_start >= latest_end:
            return IncrementalUpdatePlan("NOOP", None, last, latest_end)
        key = (
            f"{instrument.exchange.value}:{instrument.market_type.value}:"
            f"{instrument.symbol}:{timeframe.code}"
        )
        plan = self.backfill(
            key,
            timeframe,
            DataRange(candidate_start, latest_end),
            job_type=MarketJobType.INCREMENTAL,
            latest_closed_at=current,
        )
        return IncrementalUpdatePlan("RUN", plan, last, latest_end)

    def gaps(
        self,
        store: ParquetCandleStore,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
    ) -> GapRepairPlan:
        requested = expected_candle_count(data_range, timeframe)
        if requested > self._max_total_candles:
            raise InvalidDataRangeError("O intervalo de verificação excede o limite total seguro.")
        rows = store.read(
            instrument.exchange,
            instrument.market_type,
            instrument.pair,
            timeframe,
            data_range,
        )
        observed = {candle.open_time for candle in rows}
        gaps: list[DataRange] = []
        cursor = data_range.start
        gap_start: datetime | None = None
        while cursor < data_range.end:
            if cursor not in observed and gap_start is None:
                gap_start = cursor
            if cursor in observed and gap_start is not None:
                gaps.append(DataRange(gap_start, cursor))
                gap_start = None
            cursor += timeframe.duration
        if gap_start is not None:
            gaps.append(DataRange(gap_start, data_range.end))
        key = (
            f"{instrument.exchange.value}:{instrument.market_type.value}:"
            f"{instrument.symbol}:{timeframe.code}"
        )
        if not gaps:
            return GapRepairPlan(None, ())
        chunks: list[BackfillChunk] = []
        total = 0
        for gap in gaps:
            partial = self.backfill(
                key,
                timeframe,
                gap,
                job_type=MarketJobType.GAP_REPAIR,
            )
            for chunk in partial.chunks:
                chunks.append(
                    BackfillChunk(
                        index=len(chunks),
                        data_range=chunk.data_range,
                        expected_candles=chunk.expected_candles,
                    )
                )
            total += partial.expected_candles
        if total > self._max_total_candles:
            raise InvalidDataRangeError(
                "Os gaps excedem o limite total seguro e devem ser divididos."
            )
        if len(chunks) > self._max_chunks:
            raise InvalidDataRangeError("O reparo excede o limite seguro de chunks.")
        combined = BackfillPlan(
            job_id=str(uuid4()),
            dataset_key=key,
            timeframe=timeframe,
            data_range=data_range,
            chunks=tuple(chunks),
            expected_candles=total,
            chunk_candles=self._chunk_candles,
            job_type=MarketJobType.GAP_REPAIR,
        )
        return GapRepairPlan(combined, tuple(gaps))


def _latest_closed_end(now: datetime, timeframe: Timeframe) -> datetime:
    now = require_utc(now, field_name="now")
    epoch = datetime(1970, 1, 1, tzinfo=UTC) + timeframe.alignment
    periods = (now - epoch) // timeframe.duration
    return epoch + periods * timeframe.duration
