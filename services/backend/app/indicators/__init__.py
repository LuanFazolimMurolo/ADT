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
from app.indicators.rsi import RelativeStrengthIndex

__all__ = [
    "SUPPORTED_INDICATOR_SCHEMA_VERSIONS",
    "AverageTrueRange",
    "BollingerBands",
    "CandleSeries",
    "CandleSeriesView",
    "CandleTechnicalIndicator",
    "CompositeTechnicalIndicator",
    "DecimalSeries",
    "DecimalSeriesView",
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
    "MovingAverageConvergenceDivergence",
    "RelativeStrengthIndex",
    "SeriesPoint",
    "TechnicalIndicator",
    "TrueRange",
    "UnsupportedIndicatorSchemaError",
    "calculate_as_of",
    "calculate_candles_as_of",
    "calculate_composite_as_of",
]
