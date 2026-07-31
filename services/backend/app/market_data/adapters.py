"""Exchange-independent adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.market_data.domain import CandleBatch, DataRange, Instrument, Timeframe, TradingPair


@dataclass(frozen=True, slots=True)
class AdapterLimits:
    """Relevant public-source limits exposed to services."""

    max_candles_per_request: int
    request_weight_per_candle_page: int


class MarketDataAdapter(Protocol):
    """Contract implemented by market-specific public-data adapters."""

    @property
    def limits(self) -> AdapterLimits:
        """Expose source limits used for safe planning."""
        ...

    async def list_instruments(self) -> tuple[Instrument, ...]:
        """List instruments currently exposed by the source."""
        ...

    async def get_instrument(self, pair: TradingPair) -> Instrument:
        """Return metadata for one canonical instrument."""
        ...

    async def fetch_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        max_candles: int,
    ) -> CandleBatch:
        """Fetch and normalize a bounded historical interval."""
        ...

    def normalize_symbol(self, native_symbol: str) -> TradingPair:
        """Convert a known native symbol to its canonical pair."""
        ...

    def native_symbol(self, pair: TradingPair) -> str:
        """Convert a canonical pair to the source representation."""
        ...

    def native_timeframe(self, timeframe: Timeframe) -> str:
        """Convert a configured timeframe to its source code."""
        ...
