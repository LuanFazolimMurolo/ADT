"""End-to-end local ingestion tests with a mocked adapter."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.market_data.adapters import AdapterLimits
from app.market_data.catalog import JsonMarketDataCatalog
from app.market_data.domain import CandleBatch, DataRange, Instrument, Timeframe, TradingPair
from app.market_data.errors import InvalidDataRangeError, MarketDataStorageError
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.services import HistoricalMarketDataService
from app.market_data.storage import ParquetCandleStore
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import INSTRUMENT, PAIR, candle, utc


class FakeAdapter:
    def __init__(self, candles: tuple) -> None:
        self.candles = candles
        self.get_calls = 0
        self.fetch_calls = 0

    @property
    def limits(self) -> AdapterLimits:
        return AdapterLimits(1000, 2)

    @property
    def exchange(self):
        return INSTRUMENT.exchange

    @property
    def market_type(self):
        return INSTRUMENT.market_type

    async def list_instruments(self) -> tuple[Instrument, ...]:
        return (INSTRUMENT,)

    async def get_instrument(self, pair: TradingPair) -> Instrument:
        self.get_calls += 1
        assert pair == PAIR
        return INSTRUMENT

    async def fetch_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        max_candles: int,
    ) -> CandleBatch:
        self.fetch_calls += 1
        return CandleBatch(
            instrument,
            timeframe,
            data_range,
            self.candles[:max_candles],
            source_request_count=1,
        )

    def normalize_symbol(self, native_symbol: str) -> TradingPair:
        return PAIR

    def native_symbol(self, pair: TradingPair) -> str:
        return "BTCUSDT"

    def native_timeframe(self, timeframe: Timeframe) -> str:
        return timeframe.code


class FailingCompletionCatalog(JsonMarketDataCatalog):
    def promote(self, plan, *, lease) -> None:
        raise MarketDataStorageError()


def _service(tmp_path: Path, catalog=None) -> HistoricalMarketDataService:
    start = utc(2026, 1, 1)
    adapter = FakeAdapter((candle(start), candle(start.replace(hour=1))))
    return HistoricalMarketDataService(
        adapter=adapter,
        store=ParquetCandleStore(tmp_path),
        catalog=catalog or JsonMarketDataCatalog(tmp_path),
        validator=MarketDataQualityValidator(),
        max_fetch_candles=100,
    )


@pytest.mark.asyncio
async def test_mock_adapter_to_quality_parquet_and_catalog_is_idempotent(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2))

    first = await service.ingest(PAIR, get_timeframe("1h"), data_range)
    second = await service.ingest(PAIR, get_timeframe("1h"), data_range)

    assert (first.stored_count, first.duplicate_count) == (2, 0)
    assert (second.stored_count, second.duplicate_count) == (0, 2)
    datasets = JsonMarketDataCatalog(tmp_path).list_datasets()
    assert len(datasets) == 1
    assert datasets[0].candle_count == 2


@pytest.mark.asyncio
async def test_dry_run_performs_no_local_write(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = await service.ingest(
        PAIR,
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
        dry_run=True,
    )

    assert result.dry_run
    assert result.stored_count == 0
    assert not (tmp_path / "market").exists()


@pytest.mark.asyncio
async def test_catalog_failure_rolls_back_parquet_and_never_completes_false_state(
    tmp_path: Path,
) -> None:
    catalog = FailingCompletionCatalog(tmp_path)
    service = _service(tmp_path, catalog)

    with pytest.raises(MarketDataStorageError):
        await service.ingest(
            PAIR,
            get_timeframe("1h"),
            DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
        )

    assert not tuple((tmp_path / "market").rglob("*.parquet"))
    assert catalog.list_datasets() == ()


@pytest.mark.asyncio
async def test_open_candle_is_not_persisted_and_closed_revision_can_arrive_later(
    tmp_path: Path,
) -> None:
    opening = utc(2026, 1, 1)
    data_range = DataRange(opening, utc(2026, 1, 1, 1))
    open_adapter = FakeAdapter((candle(opening, is_closed=False),))
    open_service = HistoricalMarketDataService(
        adapter=open_adapter,
        store=ParquetCandleStore(tmp_path),
        catalog=JsonMarketDataCatalog(tmp_path),
        validator=MarketDataQualityValidator(clock=lambda: utc(2026, 2, 1)),
        max_fetch_candles=100,
    )

    open_result = await open_service.ingest(PAIR, get_timeframe("1h"), data_range)

    assert open_result.fetched_count == 1
    assert open_result.stored_count == 0
    assert any(issue.code == "open_candle" for issue in open_result.quality.issues)
    assert not tuple((tmp_path / "market").rglob("*.parquet"))

    closed_adapter = FakeAdapter((candle(opening, is_closed=True),))
    closed_service = HistoricalMarketDataService(
        adapter=closed_adapter,
        store=ParquetCandleStore(tmp_path),
        catalog=JsonMarketDataCatalog(tmp_path),
        validator=MarketDataQualityValidator(clock=lambda: utc(2026, 2, 1)),
        max_fetch_candles=100,
    )
    closed_result = await closed_service.ingest(PAIR, get_timeframe("1h"), data_range)

    assert closed_result.stored_count == 1
    assert closed_result.duplicate_count == 0
    assert (
        ParquetCandleStore(tmp_path).first_last_count(
            INSTRUMENT.exchange,
            INSTRUMENT.market_type,
            PAIR,
            get_timeframe("1h"),
        )[2]
        == 1
    )


@pytest.mark.asyncio
async def test_dry_run_reports_open_candle_without_persisting(tmp_path: Path) -> None:
    opening = utc(2026, 1, 1)
    adapter = FakeAdapter((candle(opening, is_closed=False),))
    service = HistoricalMarketDataService(
        adapter=adapter,
        store=ParquetCandleStore(tmp_path),
        catalog=JsonMarketDataCatalog(tmp_path),
        validator=MarketDataQualityValidator(clock=lambda: utc(2026, 2, 1)),
        max_fetch_candles=100,
    )

    result = await service.ingest(
        PAIR,
        get_timeframe("1h"),
        DataRange(opening, utc(2026, 1, 1, 1)),
        dry_run=True,
    )

    assert result.fetched_count == 1
    assert any(issue.code == "open_candle" for issue in result.quality.issues)
    assert not (tmp_path / "market").exists()


@pytest.mark.asyncio
async def test_oversized_interval_is_rejected_before_adapter_access(tmp_path: Path) -> None:
    adapter = FakeAdapter(())
    service = HistoricalMarketDataService(
        adapter=adapter,
        store=ParquetCandleStore(tmp_path),
        catalog=JsonMarketDataCatalog(tmp_path),
        validator=MarketDataQualityValidator(),
        max_fetch_candles=2,
    )

    with pytest.raises(InvalidDataRangeError, match="deve ser dividido"):
        await service.ingest(
            PAIR,
            get_timeframe("1h"),
            DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 3)),
        )

    assert adapter.get_calls == 0
    assert adapter.fetch_calls == 0
    assert not (tmp_path / "market").exists()


@pytest.mark.asyncio
async def test_service_reuses_catalog_started_at_when_clock_advances(tmp_path: Path) -> None:
    instants = iter(
        (
            utc(2026, 3, 1),
            utc(2026, 3, 1, 1),
            utc(2026, 3, 1, 2),
        )
    )

    def clock() -> datetime:
        return next(instants)

    catalog = JsonMarketDataCatalog(tmp_path, clock=clock)
    service = HistoricalMarketDataService(
        adapter=FakeAdapter((candle(utc(2026, 1, 1)),)),
        store=ParquetCandleStore(tmp_path),
        catalog=catalog,
        validator=MarketDataQualityValidator(clock=lambda: utc(2026, 2, 1)),
        max_fetch_candles=100,
        clock=clock,
    )

    result = await service.ingest(
        PAIR,
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
    )

    state = json.loads(catalog.path.read_text(encoding="utf-8"))
    assert state["runs"][result.run_id]["started_at"] == utc(2026, 3, 1).isoformat()
