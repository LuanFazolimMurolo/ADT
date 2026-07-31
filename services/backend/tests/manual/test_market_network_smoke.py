"""Explicitly opt-in minimal Binance public market-data smoke test."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.market_data.binance import BINANCE_MARKET_DATA_BASE_URL, BinanceSpotAdapter
from app.market_data.domain import DataRange, TradingPair
from app.market_data.http import PublicMarketHttpClient
from app.market_data.timeframes import get_timeframe

pytestmark = pytest.mark.skipif(
    os.getenv("ADT_ALLOW_NETWORK_TESTS") != "true",
    reason="Set ADT_ALLOW_NETWORK_TESTS=true for this explicit network smoke test.",
)


@pytest.mark.asyncio
async def test_minimal_public_binance_smoke() -> None:
    """Fetch at most two public closed candles and require no credentials."""
    assert not any(
        name in os.environ
        for name in (
            "BINANCE_API_KEY",
            "BINANCE_API_SECRET",
            "ADT_BINANCE_API_KEY",
            "ADT_BINANCE_API_SECRET",
        )
    )
    end = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    start = end - timedelta(minutes=2)
    async with PublicMarketHttpClient(
        base_url=BINANCE_MARKET_DATA_BASE_URL,
        user_agent="ADT-MarketData-ManualSmoke/0.1",
        timeout_seconds=10,
        max_connections=1,
        retries=1,
    ) as client:
        adapter = BinanceSpotAdapter(client)
        instrument = await adapter.get_instrument(TradingPair("BTC", "USDT"))
        batch = await adapter.fetch_candles(
            instrument,
            get_timeframe("1m"),
            DataRange(start, end),
            max_candles=2,
        )
    candles = batch.candles
    assert 1 <= len(candles) <= 2
    assert instrument.symbol == "BTC/USDT"
    assert batch.timeframe.code == "1m"
    assert all(candle.is_closed for candle in candles)
    assert all(candle.symbol == "BTC/USDT" and candle.timeframe.code == "1m" for candle in candles)
    assert all(start <= candle.open_time < end for candle in candles)
    assert all(earlier.open_time < later.open_time for earlier, later in zip(candles, candles[1:]))
