"""Canonical, exchange-independent market-data domain models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.market_data.errors import InvalidDataRangeError, MarketDataInconsistencyError

_ASSET_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def require_utc(value: datetime, *, field_name: str) -> datetime:
    """Require an aware UTC datetime without silently changing its meaning."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataInconsistencyError(f"{field_name} deve possuir timezone UTC.")
    normalized = value.astimezone(UTC)
    if value.utcoffset() != timedelta(0):
        raise MarketDataInconsistencyError(f"{field_name} deve estar em UTC.")
    return normalized


def datetime_to_epoch_milliseconds(value: datetime, *, field_name: str) -> int:
    """Convert an exact UTC millisecond instant without float arithmetic."""
    normalized = require_utc(value, field_name=field_name)
    if normalized.microsecond % 1_000:
        raise MarketDataInconsistencyError(
            f"{field_name} deve possuir precisão exata de milissegundo."
        )
    delta = normalized - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


class Exchange(StrEnum):
    """Canonical exchange identifiers."""

    BINANCE = "binance"


class MarketType(StrEnum):
    """Canonical market families; adapters choose what they currently support."""

    SPOT = "spot"
    FOREX = "forex"
    EQUITY = "equity"
    FUTURES = "futures"


@dataclass(frozen=True, slots=True)
class TradingPair:
    """Canonical pair kept separate from an exchange-native symbol."""

    base: str
    quote: str

    def __post_init__(self) -> None:
        base = self.base.strip().upper()
        quote = self.quote.strip().upper()
        if not _ASSET_PATTERN.fullmatch(base) or not _ASSET_PATTERN.fullmatch(quote):
            raise MarketDataInconsistencyError("O símbolo canônico contém um ativo inválido.")
        if base == quote:
            raise MarketDataInconsistencyError("Os ativos base e cotado devem ser diferentes.")
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "quote", quote)

    @classmethod
    def parse(cls, value: str) -> TradingPair:
        """Parse the strict ``BASE/QUOTE`` canonical representation."""
        parts = value.strip().split("/")
        if len(parts) != 2:
            raise MarketDataInconsistencyError("Use o símbolo canônico no formato BASE/QUOTE.")
        return cls(parts[0], parts[1])

    @property
    def symbol(self) -> str:
        """Return the canonical display representation."""
        return f"{self.base}/{self.quote}"

    @property
    def safe_path_component(self) -> str:
        """Return a traversal-safe partition value."""
        return f"{self.base}_{self.quote}"


@dataclass(frozen=True, slots=True)
class Timeframe:
    """Configured candle cadence and source mappings."""

    code: str
    duration: timedelta
    alignment: timedelta = timedelta(0)
    native_codes: Mapping[Exchange, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[1-9][0-9]*[smhdwM]", self.code):
            raise ValueError("invalid canonical timeframe code")
        if self.duration <= timedelta(0):
            raise ValueError("timeframe duration must be positive")
        if self.alignment < timedelta(0) or self.alignment >= self.duration:
            raise ValueError("timeframe alignment must fit inside its duration")

    def validate_open_time(self, open_time: datetime) -> bool:
        """Return whether an opening instant is UTC and aligned to this cadence."""
        try:
            normalized = require_utc(open_time, field_name="open_time")
        except MarketDataInconsistencyError:
            return False
        epoch = datetime(1970, 1, 1, tzinfo=UTC) + self.alignment
        return (normalized - epoch) % self.duration == timedelta(0)

    def next_open_time(self, open_time: datetime) -> datetime:
        """Calculate the next expected candle opening."""
        normalized = require_utc(open_time, field_name="open_time")
        if not self.validate_open_time(normalized):
            raise MarketDataInconsistencyError("open_time não está alinhado ao timeframe.")
        return normalized + self.duration

    def native_code(self, exchange: Exchange) -> str:
        """Resolve the exchange-specific code without conditionals in adapters."""
        try:
            return self.native_codes[exchange]
        except KeyError:
            from app.market_data.errors import UnsupportedTimeframeError

            raise UnsupportedTimeframeError() from None


@dataclass(frozen=True, slots=True)
class Instrument:
    """Canonical instrument metadata supplied by an adapter."""

    exchange: Exchange
    market_type: MarketType
    pair: TradingPair
    native_symbol: str
    active: bool
    price_precision: int | None = None
    quantity_precision: int | None = None

    def __post_init__(self) -> None:
        native_symbol = self.native_symbol.strip()
        if not native_symbol or len(native_symbol) > 64:
            raise MarketDataInconsistencyError("O símbolo nativo é inválido.")
        if self.price_precision is not None and not 0 <= self.price_precision <= 30:
            raise MarketDataInconsistencyError("A precisão de preço é inválida.")
        if self.quantity_precision is not None and not 0 <= self.quantity_precision <= 30:
            raise MarketDataInconsistencyError("A precisão de quantidade é inválida.")
        object.__setattr__(self, "native_symbol", native_symbol)

    @property
    def symbol(self) -> str:
        """Return the canonical pair symbol."""
        return self.pair.symbol


@dataclass(frozen=True, slots=True)
class DataRange:
    """Half-open UTC interval ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        try:
            start = require_utc(self.start, field_name="start")
            end = require_utc(self.end, field_name="end")
        except MarketDataInconsistencyError as error:
            raise InvalidDataRangeError(error.message) from None
        if start >= end:
            raise InvalidDataRangeError()
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True, slots=True)
class Candle:
    """One canonical OHLCV candle using Decimal and UTC exclusively."""

    exchange: Exchange
    market_type: MarketType
    symbol: str
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None
    trade_count: int | None
    is_closed: bool
    source: str

    def __post_init__(self) -> None:
        pair = TradingPair.parse(self.symbol)
        open_time = require_utc(self.open_time, field_name="open_time")
        close_time = require_utc(self.close_time, field_name="close_time")
        datetime_to_epoch_milliseconds(open_time, field_name="open_time")
        datetime_to_epoch_milliseconds(close_time, field_name="close_time")
        if not self.timeframe.validate_open_time(open_time):
            raise MarketDataInconsistencyError("open_time não está alinhado ao timeframe.")
        if close_time <= open_time:
            raise MarketDataInconsistencyError("close_time deve ser posterior a open_time.")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise MarketDataInconsistencyError("Preço e volume devem ser Decimal finitos.")
        if self.quote_volume is not None and (
            not isinstance(self.quote_volume, Decimal) or not self.quote_volume.is_finite()
        ):
            raise MarketDataInconsistencyError("quote_volume deve ser Decimal finito.")
        if self.high < max(self.open, self.close, self.low):
            raise MarketDataInconsistencyError("high é menor que outro valor OHLC.")
        if self.low > min(self.open, self.close, self.high):
            raise MarketDataInconsistencyError("low é maior que outro valor OHLC.")
        if self.volume < 0 or (self.quote_volume is not None and self.quote_volume < 0):
            raise MarketDataInconsistencyError("Volumes não podem ser negativos.")
        if self.trade_count is not None and self.trade_count < 0:
            raise MarketDataInconsistencyError("trade_count não pode ser negativo.")
        if not self.source.strip():
            raise MarketDataInconsistencyError("A fonte do candle é obrigatória.")
        object.__setattr__(self, "symbol", pair.symbol)
        object.__setattr__(self, "open_time", open_time)
        object.__setattr__(self, "close_time", close_time)

    @property
    def key(self) -> tuple[Exchange, str, str, datetime]:
        """Return the canonical uniqueness key."""
        return self.exchange, self.symbol, self.timeframe.code, self.open_time


@dataclass(frozen=True, slots=True)
class CandleBatch:
    """A typed batch fetched from one source request sequence."""

    instrument: Instrument
    timeframe: Timeframe
    data_range: DataRange
    candles: tuple[Candle, ...]
    source_request_count: int = 0

    def __post_init__(self) -> None:
        for candle in self.candles:
            identity = (candle.exchange, candle.market_type, candle.symbol, candle.timeframe.code)
            expected = (
                self.instrument.exchange,
                self.instrument.market_type,
                self.instrument.symbol,
                self.timeframe.code,
            )
            if identity != expected:
                raise MarketDataInconsistencyError("O lote mistura instrumentos ou timeframes.")


class QualitySeverity(StrEnum):
    """Severity of a deterministic quality finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    """One sanitized data-quality finding."""

    code: str
    severity: QualitySeverity
    message: str
    open_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """Aggregated validation outcome."""

    issues: tuple[DataQualityIssue, ...]
    checked_count: int
    expected_count: int | None = None

    @property
    def is_valid(self) -> bool:
        """Return whether no blocking finding exists."""
        return not any(issue.severity is QualitySeverity.ERROR for issue in self.issues)


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Safe operational summary returned by ingestion and the CLI."""

    run_id: str
    fetched_count: int
    stored_count: int
    duplicate_count: int
    request_count: int
    first_open_time: datetime | None
    last_open_time: datetime | None
    quality: DataQualityReport
    dry_run: bool
