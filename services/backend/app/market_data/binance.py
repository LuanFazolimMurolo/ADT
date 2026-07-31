"""Binance Spot public REST adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from app.market_data.adapters import AdapterLimits
from app.market_data.domain import (
    Candle,
    CandleBatch,
    DataRange,
    Exchange,
    Instrument,
    MarketType,
    Timeframe,
    TradingPair,
    datetime_to_epoch_milliseconds,
)
from app.market_data.errors import (
    InvalidMarketResponseError,
    MarketDataInconsistencyError,
    UnknownInstrumentError,
)
from app.market_data.http import JsonHttpResult, PublicMarketHttpClient

BINANCE_MARKET_DATA_BASE_URL = "https://data-api.binance.vision"
_SOURCE = "binance_spot_rest"


class BinanceSpotAdapter:
    """Normalize Binance Spot public metadata and klines."""

    def __init__(
        self,
        http_client: PublicMarketHttpClient,
        *,
        allow_open_candles: bool = False,
        now: Callable[[], datetime] | None = None,
        page_size: int = 1000,
    ) -> None:
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        self._http = http_client
        self._allow_open_candles = allow_open_candles
        self._now = now or (lambda: datetime.now(UTC))
        self._page_size = page_size
        self._instruments_by_native: dict[str, Instrument] = {}

    @property
    def limits(self) -> AdapterLimits:
        return AdapterLimits(max_candles_per_request=1000, request_weight_per_candle_page=2)

    @property
    def exchange(self) -> Exchange:
        return Exchange.BINANCE

    @property
    def market_type(self) -> MarketType:
        return MarketType.SPOT

    async def list_instruments(self) -> tuple[Instrument, ...]:
        result = await self._http.get_json(
            "/api/v3/exchangeInfo",
            params={"permissions": "SPOT", "showPermissionSets": "false"},
            operation="binance_exchange_info",
        )
        instruments = self._parse_exchange_info(result)
        self._instruments_by_native.update({item.native_symbol: item for item in instruments})
        return tuple(sorted(instruments, key=lambda item: item.symbol))

    async def get_instrument(self, pair: TradingPair) -> Instrument:
        native = self.native_symbol(pair)
        cached = self._instruments_by_native.get(native)
        if cached is not None:
            return cached
        result = await self._http.get_json(
            "/api/v3/exchangeInfo",
            params={"symbol": native, "showPermissionSets": "false"},
            operation="binance_instrument",
        )
        self._raise_api_error(result)
        instruments = self._parse_exchange_info(result)
        if len(instruments) != 1:
            raise UnknownInstrumentError()
        instrument = instruments[0]
        self._instruments_by_native[native] = instrument
        return instrument

    async def fetch_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        max_candles: int,
    ) -> CandleBatch:
        if (
            instrument.exchange is not Exchange.BINANCE
            or instrument.market_type is not MarketType.SPOT
        ):
            raise UnknownInstrumentError()
        native_timeframe = self.native_timeframe(timeframe)
        page_limit = min(self.limits.max_candles_per_request, self._page_size, max_candles)
        if page_limit < 1:
            raise ValueError("max_candles must be positive")

        cursor_ms = _to_milliseconds(data_range.start)
        end_ms = _to_milliseconds(data_range.end)
        candles: list[Candle] = []
        request_count = 0
        previous_page: tuple[int, int] | None = None

        while cursor_ms < end_ms and len(candles) < max_candles:
            limit = min(page_limit, max_candles - len(candles))
            result = await self._http.get_json(
                "/api/v3/klines",
                params={
                    "symbol": instrument.native_symbol,
                    "interval": native_timeframe,
                    "startTime": cursor_ms,
                    "endTime": end_ms - 1,
                    "limit": limit,
                    "timeZone": "0",
                },
                operation="binance_klines",
            )
            request_count += 1
            self._raise_api_error(result)
            rows = result.data
            if not isinstance(rows, list):
                raise InvalidMarketResponseError()
            if not rows:
                break

            page_open_times = tuple(self._row_open_time(row) for row in rows)
            page_marker = (page_open_times[0], page_open_times[-1])
            if page_marker == previous_page:
                raise MarketDataInconsistencyError("A fonte repetiu uma página de candles.")
            if previous_page is not None and page_open_times[0] <= previous_page[1]:
                raise MarketDataInconsistencyError("A fonte retornou páginas sobrepostas.")
            if any(
                later <= earlier for earlier, later in zip(page_open_times, page_open_times[1:])
            ):
                raise MarketDataInconsistencyError("A fonte retornou candles fora de ordem.")
            previous_page = page_marker

            for row in rows:
                candle = self._parse_candle(row, instrument, timeframe)
                if candle.open_time < data_range.start or candle.open_time >= data_range.end:
                    continue
                if candle.is_closed or self._allow_open_candles:
                    candles.append(candle)
                if len(candles) >= max_candles:
                    break

            next_cursor = page_open_times[-1] + _timedelta_milliseconds(timeframe.duration)
            if next_cursor <= cursor_ms:
                raise MarketDataInconsistencyError("A paginação não avançou.")
            cursor_ms = next_cursor
            if len(rows) < limit:
                break

        return CandleBatch(
            instrument=instrument,
            timeframe=timeframe,
            data_range=data_range,
            candles=tuple(candles),
            source_request_count=request_count,
        )

    def normalize_symbol(self, native_symbol: str) -> TradingPair:
        try:
            return self._instruments_by_native[native_symbol].pair
        except KeyError:
            raise UnknownInstrumentError() from None

    def native_symbol(self, pair: TradingPair) -> str:
        return f"{pair.base}{pair.quote}"

    def native_timeframe(self, timeframe: Timeframe) -> str:
        return timeframe.native_code(Exchange.BINANCE)

    def _parse_exchange_info(self, result: JsonHttpResult) -> list[Instrument]:
        self._raise_api_error(result)
        payload = result.data
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise InvalidMarketResponseError()
        instruments: list[Instrument] = []
        for raw_symbol in payload["symbols"]:
            if not isinstance(raw_symbol, dict):
                raise InvalidMarketResponseError()
            try:
                native = raw_symbol["symbol"]
                base = raw_symbol["baseAsset"]
                quote = raw_symbol["quoteAsset"]
                status = raw_symbol["status"]
                price_precision = raw_symbol.get("quoteAssetPrecision")
                quantity_precision = raw_symbol.get("baseAssetPrecision")
            except KeyError:
                raise InvalidMarketResponseError() from None
            if not all(isinstance(value, str) for value in (native, base, quote, status)):
                raise InvalidMarketResponseError()
            if price_precision is not None and not isinstance(price_precision, int):
                raise InvalidMarketResponseError()
            if quantity_precision is not None and not isinstance(quantity_precision, int):
                raise InvalidMarketResponseError()
            instruments.append(
                Instrument(
                    exchange=Exchange.BINANCE,
                    market_type=MarketType.SPOT,
                    pair=TradingPair(cast(str, base), cast(str, quote)),
                    native_symbol=cast(str, native),
                    active=status == "TRADING",
                    price_precision=price_precision,
                    quantity_precision=quantity_precision,
                )
            )
        return instruments

    def _parse_candle(
        self,
        row: object,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> Candle:
        if not isinstance(row, list) or len(row) < 9:
            raise InvalidMarketResponseError()
        if not isinstance(row[0], int) or not isinstance(row[6], int):
            raise InvalidMarketResponseError()
        if not isinstance(row[8], int):
            raise InvalidMarketResponseError()
        try:
            open_price = Decimal(_require_string(row[1]))
            high = Decimal(_require_string(row[2]))
            low = Decimal(_require_string(row[3]))
            close = Decimal(_require_string(row[4]))
            volume = Decimal(_require_string(row[5]))
            quote_volume = Decimal(_require_string(row[7]))
        except (InvalidOperation, ValueError):
            raise InvalidMarketResponseError() from None
        open_time = _from_milliseconds(row[0])
        close_time = _from_milliseconds(row[6])
        return Candle(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            symbol=instrument.symbol,
            timeframe=timeframe,
            open_time=open_time,
            close_time=close_time,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            quote_volume=quote_volume,
            trade_count=row[8],
            is_closed=close_time < self._now(),
            source=_SOURCE,
        )

    @staticmethod
    def _row_open_time(row: object) -> int:
        if not isinstance(row, list) or not row or not isinstance(row[0], int):
            raise InvalidMarketResponseError()
        return row[0]

    @staticmethod
    def _raise_api_error(result: JsonHttpResult) -> None:
        if isinstance(result.data, dict) and "code" in result.data:
            code = result.data.get("code")
            if code == -1121:
                raise UnknownInstrumentError()
            raise InvalidMarketResponseError()


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _from_milliseconds(value: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)


def _to_milliseconds(value: datetime) -> int:
    return datetime_to_epoch_milliseconds(value, field_name="timestamp")


def _timedelta_milliseconds(value: timedelta) -> int:
    if value.microseconds % 1_000:
        raise MarketDataInconsistencyError("O timeframe deve possuir precisão de milissegundo.")
    return value.days * 86_400_000 + value.seconds * 1_000 + value.microseconds // 1_000
