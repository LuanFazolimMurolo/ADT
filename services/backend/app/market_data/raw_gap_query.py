"""Bounded read-only RAW missing-candle inspection."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.market_data.domain import (
    Candle,
    DataRange,
    Exchange,
    MarketType,
    Timeframe,
    TradingPair,
)
from app.market_data.errors import (
    InvalidDataRangeError,
    InvalidRawGapQueryError,
    MarketDataInconsistencyError,
)
from app.market_data.operations import (
    MarketDatasetSelector,
    decode_dataset_id,
    encode_dataset_id,
)
from app.market_data.planning import expected_candle_count, find_gap_ranges
from app.market_data.raw_dataset_query import RawDatasetSnapshot

RAW_GAP_DEFAULT_PAGE_SIZE = 25
RAW_GAP_MAX_PAGE_SIZE = 100
RAW_GAP_MAX_PAGE = 100_000
RAW_GAP_MAX_EXPECTED_CANDLES = 10_000


class RawDatasetSnapshotReader(Protocol):
    """Safe catalog-backed dataset projection required by gap inspection."""

    def get(self, dataset_id: str) -> RawDatasetSnapshot: ...


class RawGapStoreReader(Protocol):
    """Minimal read-only RAW storage surface required by gap inspection."""

    def read(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
        data_range: DataRange,
    ) -> tuple[Candle, ...]: ...


class RawGapSnapshotLocker(Protocol):
    """Shared dataset snapshot lock required before catalog/storage reads."""

    def snapshot(self, dataset_key: str) -> AbstractContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class RawGapPageQuery:
    """Explicit bounded half-open RAW gap inspection query."""

    start: datetime
    end: datetime
    page: int = 1
    page_size: int = RAW_GAP_DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise InvalidRawGapQueryError()

        if (
            type(self.page) is not int
            or not 1 <= self.page <= RAW_GAP_MAX_PAGE
            or type(self.page_size) is not int
            or not 1 <= self.page_size <= RAW_GAP_MAX_PAGE_SIZE
        ):
            raise InvalidRawGapQueryError()


@dataclass(frozen=True, slots=True)
class RawGapRange:
    """One canonical half-open missing-candle range."""

    start: datetime
    end: datetime
    missing_candles: int


@dataclass(frozen=True, slots=True)
class RawGapPage:
    """One bounded deterministic page of RAW gap ranges."""

    dataset: MarketDatasetSelector
    dataset_version: str
    version_algorithm: str
    checked_start: datetime
    checked_end: datetime
    expected_candles: int
    observed_candles: int
    missing_candles: int
    total_gap_count: int
    page: int
    page_size: int
    total_pages: int
    items: tuple[RawGapRange, ...]

    @property
    def dataset_id(self) -> str:
        return encode_dataset_id(self.dataset)


@dataclass(slots=True)
class LocalRawGapReadService:
    """Inspect bounded persisted RAW gaps without jobs, repair or network access."""

    dataset_reader: RawDatasetSnapshotReader
    store: RawGapStoreReader
    lock_manager: RawGapSnapshotLocker

    def inspect(
        self,
        dataset_id: str,
        query: RawGapPageQuery,
    ) -> RawGapPage:
        RawGapPageQuery.__post_init__(query)
        identity = decode_dataset_id(dataset_id)

        # Dataset snapshot lock MUST precede catalog snapshot and Parquet reads.
        with self.lock_manager.snapshot(identity.canonical_key):
            snapshot = self.dataset_reader.get(dataset_id)

            if snapshot.dataset != identity:
                raise MarketDataInconsistencyError(
                    "A identidade RAW consultada diverge do dataset catalogado."
                )

            data_range, expected = _validated_query_range(snapshot, query)

            rows = self.store.read(
                snapshot.dataset.exchange,
                snapshot.dataset.market_type,
                snapshot.dataset.pair,
                snapshot.dataset.timeframe,
                data_range,
            )

            observed_open_times = _validated_observed_open_times(
                snapshot,
                data_range,
                rows,
            )

            gap_ranges = find_gap_ranges(
                observed_open_times,
                snapshot.dataset.timeframe,
                data_range,
            )

            ranges = tuple(
                RawGapRange(
                    start=gap.start,
                    end=gap.end,
                    missing_candles=expected_candle_count(
                        gap,
                        snapshot.dataset.timeframe,
                    ),
                )
                for gap in gap_ranges
            )

            observed = len(observed_open_times)
            missing = expected - observed

            if missing < 0 or sum(item.missing_candles for item in ranges) != missing:
                raise MarketDataInconsistencyError(
                    "A contagem de gaps RAW diverge das candles observadas."
                )

            total = len(ranges)
            total_pages = (total + query.page_size - 1) // query.page_size
            offset = (query.page - 1) * query.page_size
            page_items = ranges[offset : offset + query.page_size]

            return RawGapPage(
                dataset=snapshot.dataset,
                dataset_version=snapshot.version,
                version_algorithm=snapshot.version_algorithm,
                checked_start=data_range.start,
                checked_end=data_range.end,
                expected_candles=expected,
                observed_candles=observed,
                missing_candles=missing,
                total_gap_count=total,
                page=query.page,
                page_size=query.page_size,
                total_pages=total_pages,
                items=page_items,
            )


def _validated_query_range(
    snapshot: RawDatasetSnapshot,
    query: RawGapPageQuery,
) -> tuple[DataRange, int]:
    try:
        data_range = DataRange(query.start, query.end)
        expected = expected_candle_count(
            data_range,
            snapshot.dataset.timeframe,
        )
    except InvalidDataRangeError as error:
        raise InvalidRawGapQueryError(error.message) from None

    if expected > RAW_GAP_MAX_EXPECTED_CANDLES:
        raise InvalidRawGapQueryError("O intervalo de gaps excede o limite de 10.000 candles.")

    if snapshot.coverage_start is None or snapshot.coverage_end is None:
        raise InvalidRawGapQueryError("O dataset RAW não possui cobertura persistida consultável.")

    if data_range.start < snapshot.coverage_start or data_range.end > snapshot.coverage_end:
        raise InvalidRawGapQueryError(
            "O intervalo solicitado deve permanecer dentro da cobertura RAW persistida."
        )

    return data_range, expected


def _validated_observed_open_times(
    snapshot: RawDatasetSnapshot,
    data_range: DataRange,
    rows: tuple[Candle, ...],
) -> tuple[datetime, ...]:
    timeframe = snapshot.dataset.timeframe
    observed: list[datetime] = []
    seen: set[datetime] = set()

    for row in rows:
        if (
            row.exchange != snapshot.dataset.exchange
            or row.market_type != snapshot.dataset.market_type
            or row.symbol != snapshot.dataset.pair.symbol
            or row.timeframe != timeframe
            or not timeframe.validate_open_time(row.open_time)
            or not data_range.start <= row.open_time < data_range.end
        ):
            raise MarketDataInconsistencyError("Uma candle RAW lida diverge da consulta de gaps.")

        if row.open_time in seen:
            raise MarketDataInconsistencyError("A consulta RAW retornou open_time duplicado.")

        seen.add(row.open_time)
        observed.append(row.open_time)

    return tuple(observed)
