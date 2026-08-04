"""Deterministic technical-indicator contracts and built-ins."""

from app.indicators.atr import AverageTrueRange, TrueRange
from app.indicators.bollinger import BollingerBands
from app.indicators.domain import (
    SUPPORTED_INDICATOR_SCHEMA_VERSIONS,
    CandleSeries,
    CandleSeriesView,
    DecimalSeries,
    DecimalSeriesView,
    IndicatorBundle,
    IndicatorDescriptor,
    IndicatorParameters,
    IndicatorParameterValue,
    IndicatorPoint,
    IndicatorSeries,
    IndicatorSeriesView,
    SeriesPoint,
)
from app.indicators.ema import ExponentialMovingAverage
from app.indicators.errors import (
    FutureDataAccessError,
    IndicatorError,
    InvalidIndicatorInputError,
    UnsupportedIndicatorSchemaError,
)
from app.indicators.macd import MovingAverageConvergenceDivergence
from app.indicators.protocols import (
    CandleTechnicalIndicator,
    CompositeTechnicalIndicator,
    TechnicalIndicator,
    calculate_as_of,
    calculate_candles_as_of,
    calculate_composite_as_of,
)
from app.indicators.regime import (
    SUPPORTED_MARKET_REGIME_POLICY_SCHEMA_VERSIONS,
    DeterministicMarketRegimeDetector,
    MarketRegimeKind,
    MarketRegimePoint,
    MarketRegimePolicy,
    MarketRegimeSeries,
    TrendDirection,
    calculate_market_regimes_as_of,
)
from app.indicators.regime_incremental import MarketRegimeAccumulator
from app.indicators.rsi import RelativeStrengthIndex

__all__ = [
    "SUPPORTED_INDICATOR_SCHEMA_VERSIONS",
    "SUPPORTED_MARKET_REGIME_POLICY_SCHEMA_VERSIONS",
    "AverageTrueRange",
    "BollingerBands",
    "CandleSeries",
    "CandleSeriesView",
    "CandleTechnicalIndicator",
    "CompositeTechnicalIndicator",
    "DecimalSeries",
    "DecimalSeriesView",
    "DeterministicMarketRegimeDetector",
    "ExponentialMovingAverage",
    "FutureDataAccessError",
    "IndicatorBundle",
    "IndicatorDescriptor",
    "IndicatorError",
    "IndicatorParameterValue",
    "IndicatorParameters",
    "IndicatorPoint",
    "IndicatorSeries",
    "IndicatorSeriesView",
    "InvalidIndicatorInputError",
    "MarketRegimeAccumulator",
    "MarketRegimeKind",
    "MarketRegimePoint",
    "MarketRegimePolicy",
    "MarketRegimeSeries",
    "MovingAverageConvergenceDivergence",
    "RelativeStrengthIndex",
    "SeriesPoint",
    "TrendDirection",
    "TechnicalIndicator",
    "TrueRange",
    "UnsupportedIndicatorSchemaError",
    "calculate_as_of",
    "calculate_candles_as_of",
    "calculate_market_regimes_as_of",
    "calculate_composite_as_of",
]
