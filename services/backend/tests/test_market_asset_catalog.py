"""Phase 5-01 live asset catalog domain and service tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.market_data.asset_catalog import (
    AssetCatalogQuery,
    AssetCatalogSnapshot,
    AssetMarketService,
)
from app.market_data.domain import Exchange, Instrument, MarketPrice, MarketType, TradingPair
from app.market_data.errors import (
    AssetCatalogLimitError,
    InactiveInstrumentError,
    MarketDataInconsistencyError,
    UnknownInstrumentError,
)
from tests.market_data_helpers import INSTRUMENT, utc

ETH = Instrument(
    exchange=Exchange.BINANCE,
    market_type=MarketType.SPOT,
    pair=TradingPair("ETH", "USDT"),
    native_symbol="ETHUSDT",
    active=True,
    price_precision=8,
    quantity_precision=8,
)
INACTIVE = Instrument(
    exchange=Exchange.BINANCE,
    market_type=MarketType.SPOT,
    pair=TradingPair("OLD", "USDT"),
    native_symbol="OLDUSDT",
    active=False,
)
BTC_BRL = Instrument(
    exchange=Exchange.BINANCE,
    market_type=MarketType.SPOT,
    pair=TradingPair("BTC", "BRL"),
    native_symbol="BTCBRL",
    active=True,
)


class FakeAssetAdapter:
    exchange = Exchange.BINANCE
    market_type = MarketType.SPOT

    def __init__(self, instruments: tuple[Instrument, ...]) -> None:
        self.instruments = instruments
        self.list_calls = 0
        self.price_calls = 0
        self.release: asyncio.Event | None = None

    async def list_instruments(self) -> tuple[Instrument, ...]:
        self.list_calls += 1
        if self.release is not None:
            await self.release.wait()
        return self.instruments

    async def fetch_price(self, instrument: Instrument) -> MarketPrice:
        self.price_calls += 1
        return MarketPrice(
            instrument=instrument,
            price=Decimal("100.25000000"),
            observed_at=utc(2026, 8, 2),
            source="fake_public_price",
        )


@pytest.mark.asyncio
async def test_catalog_is_sorted_filtered_paginated_and_cached() -> None:
    now = [utc(2026, 8, 2)]
    adapter = FakeAssetAdapter((INACTIVE, ETH, BTC_BRL, INSTRUMENT))
    service = AssetMarketService(adapter, catalog_ttl_seconds=60, clock=lambda: now[0])

    first = await service.list_assets(
        AssetCatalogQuery(quote_asset=" usdt ", search="t", page=1, page_size=1)
    )
    second = await service.list_assets(AssetCatalogQuery(page=1, page_size=10))

    assert [item.symbol for item in first.items] == ["BTC/USDT"]
    assert first.total == 2
    assert first.total_pages == 2
    assert [item.symbol for item in second.items] == ["BTC/BRL", "BTC/USDT", "ETH/USDT"]
    assert adapter.list_calls == 1

    now[0] += timedelta(seconds=61)
    await service.list_assets(AssetCatalogQuery())
    assert adapter.list_calls == 2


@pytest.mark.asyncio
async def test_concurrent_cache_miss_performs_one_source_refresh() -> None:
    adapter = FakeAssetAdapter((INSTRUMENT,))
    adapter.release = asyncio.Event()
    service = AssetMarketService(adapter, clock=lambda: utc(2026, 8, 2))

    tasks = [asyncio.create_task(service.list_assets(AssetCatalogQuery())) for _ in range(5)]
    await asyncio.sleep(0)
    adapter.release.set()
    pages = await asyncio.gather(*tasks)

    assert all(page.total == 1 for page in pages)
    assert adapter.list_calls == 1


@pytest.mark.asyncio
async def test_get_price_requires_known_active_asset_and_uncached_price() -> None:
    adapter = FakeAssetAdapter((INSTRUMENT, INACTIVE))
    service = AssetMarketService(adapter, clock=lambda: utc(2026, 8, 2))

    first = await service.get_price(TradingPair("BTC", "USDT"))
    second = await service.get_price(TradingPair("BTC", "USDT"))

    assert first.price == Decimal("100.25000000")
    assert second.price == first.price
    assert adapter.list_calls == 1
    assert adapter.price_calls == 2

    with pytest.raises(InactiveInstrumentError):
        await service.get_price(TradingPair("OLD", "USDT"))
    with pytest.raises(UnknownInstrumentError):
        await service.get_asset(TradingPair("MISSING", "USDT"))


@pytest.mark.asyncio
async def test_catalog_limit_is_enforced_before_publication() -> None:
    adapter = FakeAssetAdapter((INSTRUMENT, ETH))
    service = AssetMarketService(adapter, max_instruments=1)

    with pytest.raises(AssetCatalogLimitError):
        await service.list_assets(AssetCatalogQuery())

    assert service._snapshot is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"active_only": 1},
        {"page": True},
        {"page": 0},
        {"page_size": 101},
        {"quote_asset": object()},
        {"search": "x" * 65},
    ],
)
def test_query_rejects_hostile_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(MarketDataInconsistencyError):
        AssetCatalogQuery(**kwargs)  # type: ignore[arg-type]


def test_snapshot_rejects_unsorted_duplicate_or_mixed_instruments() -> None:
    with pytest.raises(MarketDataInconsistencyError):
        AssetCatalogSnapshot(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            instruments=(ETH, INSTRUMENT),
            fetched_at=utc(2026, 8, 2),
            expires_at=utc(2026, 8, 2) + timedelta(minutes=5),
            source="source",
        )
    with pytest.raises(MarketDataInconsistencyError):
        AssetCatalogSnapshot(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            instruments=(INSTRUMENT, INSTRUMENT),
            fetched_at=utc(2026, 8, 2),
            expires_at=utc(2026, 8, 2) + timedelta(minutes=5),
            source="source",
        )
    mixed = replace(INSTRUMENT, market_type=MarketType.FUTURES)
    with pytest.raises(MarketDataInconsistencyError):
        AssetCatalogSnapshot(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            instruments=(mixed,),
            fetched_at=utc(2026, 8, 2),
            expires_at=utc(2026, 8, 2) + timedelta(minutes=5),
            source="source",
        )


def test_snapshot_rejects_empty_and_duplicate_native_symbols() -> None:
    with pytest.raises(MarketDataInconsistencyError):
        AssetCatalogSnapshot(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            instruments=(),
            fetched_at=utc(2026, 8, 2),
            expires_at=utc(2026, 8, 2) + timedelta(minutes=5),
            source="source",
        )
    duplicate_native = replace(ETH, native_symbol=INSTRUMENT.native_symbol)
    with pytest.raises(MarketDataInconsistencyError):
        AssetCatalogSnapshot(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            instruments=(INSTRUMENT, duplicate_native),
            fetched_at=utc(2026, 8, 2),
            expires_at=utc(2026, 8, 2) + timedelta(minutes=5),
            source="source",
        )


def test_market_price_rejects_non_positive_non_decimal_and_non_utc() -> None:
    for price in (Decimal("0"), Decimal("NaN"), 1):
        with pytest.raises(MarketDataInconsistencyError):
            MarketPrice(
                instrument=INSTRUMENT,
                price=price,  # type: ignore[arg-type]
                observed_at=utc(2026, 8, 2),
                source="source",
            )
    with pytest.raises(MarketDataInconsistencyError):
        MarketPrice(
            instrument=INSTRUMENT,
            price=Decimal("1"),
            observed_at=utc(2026, 8, 2).replace(tzinfo=None),
            source="source",
        )


@pytest.mark.asyncio
async def test_service_rejects_hostile_or_noncanonical_pair_without_internal_error() -> None:
    service = AssetMarketService(
        FakeAssetAdapter((INSTRUMENT,)),
        clock=lambda: utc(2026, 8, 2),
    )
    hostile = TradingPair("BTC", "USDT")
    object.__setattr__(hostile, "base", object())
    with pytest.raises(UnknownInstrumentError):
        await service.get_asset(hostile)

    noncanonical = TradingPair("BTC", "USDT")
    object.__setattr__(noncanonical, "base", " btc ")
    with pytest.raises(UnknownInstrumentError):
        await service.get_asset(noncanonical)
