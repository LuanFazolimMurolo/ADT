"""Indicator capabilities shipped with the deterministic strategy runtime."""

from decimal import Decimal

from app.indicators.atr import AverageTrueRange, TrueRange
from app.indicators.bollinger import BollingerBands
from app.indicators.ema import ExponentialMovingAverage
from app.indicators.macd import MovingAverageConvergenceDivergence
from app.indicators.rsi import RelativeStrengthIndex
from app.strategies.domain import IndicatorCapability


def builtin_indicator_capabilities() -> tuple[IndicatorCapability, ...]:
    """Return parameter-independent identities for all approved built-ins."""

    descriptors = (
        TrueRange().descriptor,
        AverageTrueRange(1).descriptor,
        BollingerBands(1, Decimal("1")).descriptor,
        ExponentialMovingAverage(1).descriptor,
        MovingAverageConvergenceDivergence(1, 2, 1).descriptor,
        RelativeStrengthIndex(1).descriptor,
    )
    capabilities = {IndicatorCapability.from_descriptor(item) for item in descriptors}
    return tuple(sorted(capabilities, key=lambda item: item.canonical_key))
