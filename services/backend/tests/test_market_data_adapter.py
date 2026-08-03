"""Mocked Binance Spot adapter and public HTTP client tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.market_data.binance import BINANCE_MARKET_DATA_BASE_URL, BinanceSpotAdapter
from app.market_data.errors import (
    InvalidMarketResponseError,
    MarketDataInconsistencyError,
    MarketDataUnavailableError,
    MarketRateLimitError,
    UnknownInstrumentError,
    UnsupportedTimeframeError,
)
from app.market_data.http import PublicMarketHttpClient
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import (
    INSTRUMENT,
    PAIR,
    binance_kline,
    exchange_info_payload,
    hourly_range,
    utc,
)


def _client(
    handler: Any,
    *,
    retries: int = 0,
    sleep: Any = None,
    max_retry_after_seconds: float = 30.0,
) -> PublicMarketHttpClient:
    kwargs: dict[str, object] = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return PublicMarketHttpClient(
        base_url=BINANCE_MARKET_DATA_BASE_URL,
        user_agent="ADT-MarketData-Test/1.0",
        timeout_seconds=2,
        max_connections=2,
        retries=retries,
        max_retry_after_seconds=max_retry_after_seconds,
        transport=httpx.MockTransport(handler),
        jitter=lambda: 0.0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_exchange_info_normalizes_native_and_canonical_symbols() -> None:
    async with _client(
        lambda request: httpx.Response(200, json=exchange_info_payload(), request=request)
    ) as client:
        adapter = BinanceSpotAdapter(client)
        instruments = await adapter.list_instruments()

    assert instruments == (INSTRUMENT,)
    assert adapter.normalize_symbol("BTCUSDT") == PAIR
    assert adapter.native_symbol(PAIR) == "BTCUSDT"


@pytest.mark.asyncio
async def test_exchange_info_skips_unrepresentable_assets_without_losing_valid_pairs() -> None:
    payload = exchange_info_payload()
    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    symbols.insert(
        0,
        {
            "symbol": "币安人生USDT",
            "status": "TRADING",
            "baseAsset": "币安人生",
            "baseAssetPrecision": 8,
            "quoteAsset": "USDT",
            "quoteAssetPrecision": 8,
        },
    )

    async with _client(
        lambda request: httpx.Response(200, json=payload, request=request)
    ) as client:
        adapter = BinanceSpotAdapter(client)
        instruments = await adapter.list_instruments()

    assert instruments == (INSTRUMENT,)
    assert adapter.normalize_symbol("BTCUSDT") == PAIR


@pytest.mark.asyncio
async def test_ticker_price_is_normalized_as_positive_decimal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/ticker/price"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(
            200,
            json={"symbol": "BTCUSDT", "price": "67234.12000000"},
            request=request,
        )

    async with _client(handler) as client:
        observation = await BinanceSpotAdapter(
            client,
            now=lambda: utc(2026, 8, 2),
        ).fetch_price(INSTRUMENT)

    assert observation.instrument == INSTRUMENT
    assert str(observation.price) == "67234.12000000"
    assert observation.observed_at == utc(2026, 8, 2)
    assert observation.source == "binance_spot_ticker_price_rest"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "ETHUSDT", "price": "1"},
        {"symbol": "BTCUSDT", "price": 1},
        {"symbol": "BTCUSDT", "price": "0"},
        {"symbol": "BTCUSDT", "price": "NaN"},
        {"symbol": "BTCUSDT"},
        [],
    ],
)
async def test_ticker_price_rejects_invalid_payload(payload: object) -> None:
    async with _client(
        lambda request: httpx.Response(200, json=payload, request=request)
    ) as client:
        with pytest.raises(InvalidMarketResponseError):
            await BinanceSpotAdapter(client).fetch_price(INSTRUMENT)


@pytest.mark.asyncio
async def test_kline_response_is_decimal_utc_and_paginated_without_repetition() -> None:
    start = utc(2026, 1, 1)
    requested_starts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_starts.append(int(request.url.params["startTime"]))
        if len(requested_starts) == 1:
            payload = [binance_kline(start), binance_kline(start + timedelta(hours=1))]
        else:
            payload = []
        return httpx.Response(
            200,
            json=payload,
            headers={"X-MBX-USED-WEIGHT-1M": "12"},
            request=request,
        )

    async with _client(handler) as client:
        adapter = BinanceSpotAdapter(
            client,
            now=lambda: utc(2026, 2, 1),
            page_size=2,
        )
        batch = await adapter.fetch_candles(
            INSTRUMENT,
            get_timeframe("1h"),
            hourly_range(start, 3),
            max_candles=3,
        )

    assert len(batch.candles) == 2
    assert batch.source_request_count == 2
    assert batch.candles[0].open.as_tuple().exponent == -8
    assert batch.candles[0].open_time.tzinfo is not None
    assert requested_starts[1] > requested_starts[0]


@pytest.mark.asyncio
async def test_repeated_kline_page_is_rejected() -> None:
    start = utc(2026, 1, 1)
    payload = [binance_kline(start), binance_kline(start + timedelta(hours=1))]

    async with _client(
        lambda request: httpx.Response(200, json=payload, request=request)
    ) as client:
        adapter = BinanceSpotAdapter(
            client,
            now=lambda: utc(2026, 2, 1),
            page_size=2,
        )
        with pytest.raises(MarketDataInconsistencyError):
            await adapter.fetch_candles(
                INSTRUMENT,
                get_timeframe("1h"),
                hourly_range(start, 6),
                max_candles=6,
            )


@pytest.mark.asyncio
async def test_rate_limit_respects_retry_after_then_succeeds() -> None:
    calls = 0
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        return httpx.Response(200, json=exchange_info_payload(), request=request)

    async with _client(handler, retries=1, sleep=sleep) as client:
        instruments = await BinanceSpotAdapter(client).list_instruments()

    assert instruments == (INSTRUMENT,)
    assert calls == 2
    assert delays == [2.0]


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_has_stable_error() -> None:
    async with _client(
        lambda request: httpx.Response(429, request=request),
    ) as client:
        with pytest.raises(MarketRateLimitError):
            await BinanceSpotAdapter(client).list_instruments()


@pytest.mark.asyncio
async def test_http_418_never_retries_and_preserves_large_retry_after() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(418, headers={"Retry-After": "120"}, request=request)

    async with _client(handler, retries=3) as client:
        with pytest.raises(MarketRateLimitError) as captured:
            await BinanceSpotAdapter(client).list_instruments()

    assert calls == 1
    assert captured.value.retry_after_seconds == 120.0


@pytest.mark.asyncio
async def test_http_429_above_wait_limit_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "31"}, request=request)

    async with _client(handler, retries=3, max_retry_after_seconds=30) as client:
        with pytest.raises(MarketRateLimitError) as captured:
            await BinanceSpotAdapter(client).list_instruments()

    assert calls == 1
    assert captured.value.retry_after_seconds == 31.0


@pytest.mark.asyncio
async def test_timeout_is_retried_only_to_configured_limit() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("mock timeout", request=request)

    async def no_sleep(_delay: float) -> None:
        return None

    async with _client(handler, retries=1, sleep=no_sleep) as client:
        with pytest.raises(MarketDataUnavailableError):
            await BinanceSpotAdapter(client).list_instruments()
    assert calls == 2


@pytest.mark.asyncio
async def test_invalid_payload_and_unknown_instrument_are_safe() -> None:
    responses = iter(
        [
            {"unexpected": []},
            {"code": -1121, "msg": "Invalid symbol."},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=next(responses), request=request)

    async with _client(handler) as client:
        adapter = BinanceSpotAdapter(client)
        with pytest.raises(InvalidMarketResponseError):
            await adapter.list_instruments()
        with pytest.raises(UnknownInstrumentError):
            await adapter.get_instrument(PAIR)


def test_binance_rejects_unmapped_timeframe() -> None:
    from app.market_data.domain import Timeframe

    adapter = BinanceSpotAdapter.__new__(BinanceSpotAdapter)
    custom = Timeframe("2h", timedelta(hours=2))
    with pytest.raises(UnsupportedTimeframeError):
        adapter.native_timeframe(custom)
