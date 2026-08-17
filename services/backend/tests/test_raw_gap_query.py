"""Bounded remote-free RAW gap inspection for Phase 7-04."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.market_data.domain import (
    Candle,
    DataRange,
    Exchange,
    MarketType,
    Timeframe,
    TradingPair,
)
from app.market_data.errors import (
    InvalidRawGapQueryError,
    MarketDataInconsistencyError,
)
from app.market_data.integrity import RAW_DATASET_VERSION_ALGORITHM
from app.market_data.operations import MarketDatasetSelector
from app.market_data.raw_dataset_query import (
    RawDatasetIntegritySummary,
    RawDatasetSnapshot,
)
from app.market_data.raw_gap_query import (
    LocalRawGapReadService,
    RawGapPageQuery,
)
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import candle, utc


def _selector() -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1h"),
    )


def _snapshot(
    *,
    coverage_start: datetime | None = None,
    coverage_end: datetime | None = None,
) -> RawDatasetSnapshot:
    selector = _selector()
    start = coverage_start or utc(2026, 8, 1)
    end = coverage_end or utc(2026, 8, 1, 6)

    return RawDatasetSnapshot(
        dataset=selector,
        first_open_time=start,
        last_open_time=end - selector.timeframe.duration,
        coverage_start=start,
        coverage_end=end,
        candle_count=6,
        version="a" * 64,
        version_algorithm=RAW_DATASET_VERSION_ALGORITHM,
        updated_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        integrity=RawDatasetIntegritySummary(
            present=False,
            schema_version=None,
            checksum_algorithm=None,
            partition_count=0,
        ),
    )


class FakeSnapshotReader:
    def __init__(
        self,
        snapshot: RawDatasetSnapshot,
        events: list[str],
    ) -> None:
        self.snapshot = snapshot
        self.events = events

    def get(self, dataset_id: str) -> RawDatasetSnapshot:
        self.events.append("catalog_read")
        assert dataset_id == self.snapshot.dataset_id
        return self.snapshot


class FakeGapStore:
    def __init__(
        self,
        rows: tuple[Candle, ...],
        events: list[str],
    ) -> None:
        self.rows = rows
        self.events = events
        self.calls = 0

    def read(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
        data_range: DataRange,
    ) -> tuple[Candle, ...]:
        self.events.append("store_read")
        self.calls += 1

        assert exchange == Exchange.BINANCE
        assert market_type == MarketType.SPOT
        assert pair == TradingPair("BTC", "USDT")
        assert timeframe == get_timeframe("1h")

        return tuple(row for row in self.rows if data_range.start <= row.open_time < data_range.end)


class FakeSnapshotLocker:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    @contextmanager
    def snapshot(self, dataset_key: str) -> Iterator[None]:
        assert dataset_key == _selector().canonical_key
        self.events.append("dataset_lock_enter")
        try:
            yield
        finally:
            self.events.append("dataset_lock_exit")


def _service(
    rows: tuple[Candle, ...],
    *,
    snapshot: RawDatasetSnapshot | None = None,
) -> tuple[LocalRawGapReadService, FakeGapStore, list[str], RawDatasetSnapshot]:
    events: list[str] = []
    selected_snapshot = snapshot or _snapshot()
    store = FakeGapStore(rows, events)

    service = LocalRawGapReadService(
        dataset_reader=FakeSnapshotReader(selected_snapshot, events),
        store=store,
        lock_manager=FakeSnapshotLocker(events),
    )

    return service, store, events, selected_snapshot


def _rows(*hours: int) -> tuple[Candle, ...]:
    timeframe = get_timeframe("1h")
    return tuple(
        candle(
            utc(2026, 8, 1, hour),
            timeframe=timeframe,
        )
        for hour in hours
    )


def test_gap_query_projects_counts_ranges_and_lock_order() -> None:
    service, store, events, snapshot = _service(_rows(0, 1, 4, 5))

    result = service.inspect(
        snapshot.dataset_id,
        RawGapPageQuery(
            start=utc(2026, 8, 1),
            end=utc(2026, 8, 1, 6),
        ),
    )

    assert result.dataset == snapshot.dataset
    assert result.dataset_id == snapshot.dataset_id
    assert result.dataset_version == snapshot.version
    assert result.version_algorithm == snapshot.version_algorithm
    assert result.expected_candles == 6
    assert result.observed_candles == 4
    assert result.missing_candles == 2
    assert result.total_gap_count == 1
    assert result.total_pages == 1
    assert len(result.items) == 1
    assert result.items[0].start == utc(2026, 8, 1, 2)
    assert result.items[0].end == utc(2026, 8, 1, 4)
    assert result.items[0].missing_candles == 2
    assert store.calls == 1

    assert events == [
        "dataset_lock_enter",
        "catalog_read",
        "store_read",
        "dataset_lock_exit",
    ]


def test_gap_query_returns_zero_gap_summary_without_fabrication() -> None:
    service, _store, _events, snapshot = _service(_rows(0, 1, 2, 3, 4, 5))

    result = service.inspect(
        snapshot.dataset_id,
        RawGapPageQuery(
            start=utc(2026, 8, 1),
            end=utc(2026, 8, 1, 6),
        ),
    )

    assert result.expected_candles == 6
    assert result.observed_candles == 6
    assert result.missing_candles == 0
    assert result.total_gap_count == 0
    assert result.total_pages == 0
    assert result.items == ()


def test_gap_ranges_are_canonically_paginated() -> None:
    service, _store, _events, snapshot = _service(_rows(0, 2, 4))

    first = service.inspect(
        snapshot.dataset_id,
        RawGapPageQuery(
            start=utc(2026, 8, 1),
            end=utc(2026, 8, 1, 6),
            page=1,
            page_size=2,
        ),
    )
    second = service.inspect(
        snapshot.dataset_id,
        RawGapPageQuery(
            start=utc(2026, 8, 1),
            end=utc(2026, 8, 1, 6),
            page=2,
            page_size=2,
        ),
    )

    assert first.total_gap_count == 3
    assert first.total_pages == 2
    assert [(item.start.hour, item.end.hour) for item in first.items] == [
        (1, 2),
        (3, 4),
    ]

    assert len(second.items) == 1
    assert second.items[0].start == utc(2026, 8, 1, 5)
    assert second.items[0].end == utc(2026, 8, 1, 6)


@pytest.mark.parametrize(
    ("start", "end"),
    (
        (
            datetime(2026, 8, 1, 0, 1, tzinfo=UTC),
            datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 31, 23, 0, tzinfo=UTC),
            datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 8, 1, 5, 0, tzinfo=UTC),
            datetime(2026, 8, 1, 7, 0, tzinfo=UTC),
        ),
    ),
)
def test_invalid_or_outside_coverage_range_is_rejected_before_store_read(
    start: datetime,
    end: datetime,
) -> None:
    service, store, _events, snapshot = _service(_rows())

    with pytest.raises(InvalidRawGapQueryError):
        service.inspect(
            snapshot.dataset_id,
            RawGapPageQuery(start=start, end=end),
        )

    assert store.calls == 0


def test_more_than_10000_expected_candles_is_rejected_before_store_read() -> None:
    service, store, _events, snapshot = _service(_rows())
    start = utc(2026, 8, 1)

    with pytest.raises(InvalidRawGapQueryError, match="10.000"):
        service.inspect(
            snapshot.dataset_id,
            RawGapPageQuery(
                start=start,
                end=start + timedelta(hours=10_001),
            ),
        )

    assert store.calls == 0


@pytest.mark.parametrize(
    ("page", "page_size"),
    (
        (0, 25),
        (100_001, 25),
        (1, 0),
        (1, 101),
    ),
)
def test_gap_pagination_is_bounded(
    page: int,
    page_size: int,
) -> None:
    with pytest.raises(InvalidRawGapQueryError):
        RawGapPageQuery(
            start=utc(2026, 8, 1),
            end=utc(2026, 8, 1, 1),
            page=page,
            page_size=page_size,
        )


def test_duplicate_observed_open_time_is_rejected() -> None:
    duplicate = _rows(0)[0]
    service, _store, _events, snapshot = _service((duplicate, duplicate))

    with pytest.raises(
        MarketDataInconsistencyError,
        match="open_time duplicado",
    ):
        service.inspect(
            snapshot.dataset_id,
            RawGapPageQuery(
                start=utc(2026, 8, 1),
                end=utc(2026, 8, 1, 1),
            ),
        )
