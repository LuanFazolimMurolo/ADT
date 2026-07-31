"""Extensible market-session calendars; Phase 2C implements continuous crypto."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.market_data.domain import Timeframe
from app.market_data.errors import MarketDataInconsistencyError


class MarketCalendar(Protocol):
    name: str

    def group_start(self, instant: datetime, target: Timeframe) -> datetime: ...

    def validate_pair(self, source: Timeframe, target: Timeframe) -> int: ...


class ContinuousUtcCalendar:
    """A 24/7 calendar anchored at the Unix epoch and UTC midnight."""

    name = "CONTINUOUS_UTC_24_7"

    def validate_pair(self, source: Timeframe, target: Timeframe) -> int:
        if target.duration <= source.duration:
            raise MarketDataInconsistencyError(
                "O timeframe de destino deve ser maior que o de origem."
            )
        quotient, remainder = divmod(target.duration, source.duration)
        if remainder or quotient < 2:
            raise MarketDataInconsistencyError(
                "A duração de destino deve ser múltipla exata da origem."
            )
        if source.alignment != target.alignment:
            raise MarketDataInconsistencyError("Os alinhamentos dos timeframes são incompatíveis.")
        if target.code == "1d" and target.alignment.total_seconds() != 0:
            raise MarketDataInconsistencyError("O timeframe diário deve iniciar à meia-noite UTC.")
        return quotient

    def group_start(self, instant: datetime, target: Timeframe) -> datetime:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise MarketDataInconsistencyError("O calendário exige timestamps UTC.")
        normalized = instant.astimezone(UTC)
        offset = normalized.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise MarketDataInconsistencyError("O calendário exige timestamps UTC.")
        epoch = datetime(1970, 1, 1, tzinfo=UTC) + target.alignment
        periods = (normalized - epoch) // target.duration
        return epoch + periods * target.duration
