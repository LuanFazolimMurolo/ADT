"""Deterministic bounded local RAW candle query tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.market_data.adapters import AdapterLimits
from app.market_data.candle_query import (
    MARKET_CANDLE_MAX_LIMIT,
    LocalMarketCandleReadService,
    MarketCandlePageQuery,
)
from app.market_data.catalog import JsonMarketDataCatalog
from app.market_data.domain import (
    Candle,
    CandleBatch,
    DataRange,
    Exchange,
    Instrument,
    MarketType,
    Timeframe,
    TradingPair,
)
from app.market_data.errors import (
    InvalidDataRangeError,
    MarketCandleDatasetNotFoundError,
    MarketDataInconsistencyError,
)
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.services import HistoricalMarketDataService
from app.market_data.storage import ParquetCandleStore
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import INSTRUMENT, PAIR, candle, utc


class FixedCandleAdapter:
    """Remote-free adapter publishing one deterministic batch."""

    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = tuple(candles)

    @property
    def limits(self) -> AdapterLimits:
        return AdapterLimits(10_000, 1)

    @property
    def exchange(self) -> Exchange:
        return INSTRUMENT.exchange

    @property
    def market_type(self) -> MarketType:
        return INSTRUMENT.market_type

    async def list_instruments(self) -> tuple[Instrument, ...]:
        return (INSTRUMENT,)

    async def get_instrument(self, pair: TradingPair) -> Instrument:
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
        assert instrument == INSTRUMENT
        return CandleBatch(
            instrument=instrument,
            timeframe=timeframe,
            data_range=data_range,
            candles=self._candles[:max_candles],
            source_request_count=1,
        )

    def normalize_symbol(self, native_symbol: str) -> TradingPair:
        assert native_symbol == "BTCUSDT"
        return PAIR

    def native_symbol(self, pair: TradingPair) -> str:
        assert pair == PAIR
        return "BTCUSDT"

    def native_timeframe(self, timeframe: Timeframe) -> str:
        return timeframe.code


async def ingest_cataloged_candles(
    data_dir: Path,
    candles: Sequence[Candle],
    *,
    start: datetime,
    end: datetime,
) -> None:
    timeframe = get_timeframe("1h")
    service = HistoricalMarketDataService(
        adapter=FixedCandleAdapter(candles),
        store=ParquetCandleStore(data_dir),
        catalog=JsonMarketDataCatalog(data_dir),
        validator=MarketDataQualityValidator(clock=lambda: utc(2025, 1, 1)),
        max_fetch_candles=10_000,
    )
    result = await service.ingest(PAIR, timeframe, DataRange(start, end))
    assert result.stored_count == len(candles)


@pytest.mark.asyncio
async def test_latest_and_previous_pages_are_stable_and_contiguous(tmp_path: Path) -> None:
    opening = utc(2024, 1, 1)
    candles = tuple(candle(opening + timedelta(hours=index)) for index in range(4))
    await ingest_cataloged_candles(
        tmp_path,
        candles,
        start=opening,
        end=opening + timedelta(hours=4),
    )
    service = LocalMarketCandleReadService(tmp_path)
    timeframe = get_timeframe("1h")

    latest = service.read_page(MarketCandlePageQuery(pair=PAIR, timeframe=timeframe, limit=2))
    repeated = service.read_page(MarketCandlePageQuery(pair=PAIR, timeframe=timeframe, limit=2))
    previous = service.read_page(
        MarketCandlePageQuery(
            pair=PAIR,
            timeframe=timeframe,
            before=latest.next_before,
            limit=2,
        )
    )

    assert latest.candles == candles[2:]
    assert latest.data_range == DataRange(
        opening + timedelta(hours=2),
        opening + timedelta(hours=4),
    )
    assert latest.has_more_before
    assert latest.next_before == opening + timedelta(hours=2)
    assert latest.content_checksum == repeated.content_checksum
    assert previous.candles == candles[:2]
    assert not previous.has_more_before
    assert previous.next_before is None
    assert previous.dataset_version == latest.dataset_version
    assert previous.dataset_candle_count == 4


@pytest.mark.asyncio
async def test_page_uses_catalog_version_without_network_access(tmp_path: Path) -> None:
    opening = utc(2024, 1, 1)
    candles = (candle(opening),)
    await ingest_cataloged_candles(
        tmp_path,
        candles,
        start=opening,
        end=opening + timedelta(hours=1),
    )
    metadata = JsonMarketDataCatalog(tmp_path).list_datasets()[0]

    page = LocalMarketCandleReadService(tmp_path).read_page(
        MarketCandlePageQuery(pair=PAIR, timeframe=get_timeframe("1h"), limit=1)
    )

    assert page.dataset_version == metadata.version
    assert page.dataset_version_algorithm == metadata.version_algorithm
    assert page.candles == candles
    assert len(page.content_checksum) == 64


@pytest.mark.parametrize(
    ("before", "limit"),
    [
        (datetime(2024, 1, 1), 1),
        (datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=-3))), 1),
        (datetime(2024, 1, 1, 0, 30, tzinfo=UTC), 1),
        (None, 0),
        (None, MARKET_CANDLE_MAX_LIMIT + 1),
    ],
)
def test_query_rejects_noncanonical_cursor_and_limits(
    before: datetime | None,
    limit: int,
) -> None:
    with pytest.raises(InvalidDataRangeError):
        MarketCandlePageQuery(
            pair=PAIR,
            timeframe=get_timeframe("1h"),
            before=before,
            limit=limit,
        )


def test_missing_cataloged_dataset_uses_stable_not_found(tmp_path: Path) -> None:
    service = LocalMarketCandleReadService(tmp_path)

    with pytest.raises(MarketCandleDatasetNotFoundError):
        service.read_page(MarketCandlePageQuery(pair=PAIR, timeframe=get_timeframe("1h"), limit=1))


@pytest.mark.asyncio
async def test_query_rejects_cursor_outside_available_coverage(tmp_path: Path) -> None:
    opening = utc(2024, 1, 1)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening),),
        start=opening,
        end=opening + timedelta(hours=1),
    )
    service = LocalMarketCandleReadService(tmp_path)

    with pytest.raises(InvalidDataRangeError):
        service.read_page(
            MarketCandlePageQuery(
                pair=PAIR,
                timeframe=get_timeframe("1h"),
                before=opening + timedelta(hours=2),
                limit=1,
            )
        )


@pytest.mark.asyncio
async def test_query_rejects_cataloged_gap_in_requested_page(tmp_path: Path) -> None:
    opening = utc(2024, 1, 1)
    store = ParquetCandleStore(tmp_path)
    receipt = store.upsert(
        (
            candle(opening),
            candle(opening + timedelta(hours=2)),
        )
    )
    receipt.commit()
    version = store.logical_version(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        PAIR,
        get_timeframe("1h"),
    )

    catalog_path = JsonMarketDataCatalog(tmp_path).path
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        (
            '{"datasets":{"binance:spot:BTC/USDT:1h":{'
            '"key":"binance:spot:BTC/USDT:1h",'
            '"exchange":"binance","market_type":"spot","symbol":"BTC/USDT",'
            '"native_symbol":"BTCUSDT","timeframe":"1h","location":"market",'
            '"first_open_time":"2024-01-01T00:00:00+00:00",'
            '"last_open_time":"2024-01-01T02:00:00+00:00",'
            '"candle_count":2,'
            f'"version":"{version}",'
            '"updated_at":"2024-01-01T03:00:00+00:00",'
            '"version_algorithm":"raw-partition-canonical-sha256-v1"}},'
            '"runs":{},"receipts":{}}\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(MarketDataInconsistencyError):
        LocalMarketCandleReadService(tmp_path).read_page(
            MarketCandlePageQuery(pair=PAIR, timeframe=get_timeframe("1h"), limit=3)
        )
