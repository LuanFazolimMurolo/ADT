"""Deterministic ML feature extraction from authenticated candle prefixes."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Protocol

from app.indicators.domain import (
    CandleSeries,
    DecimalSeries,
    IndicatorBundle,
    IndicatorSeries,
)
from app.market_data.domain import Candle, DataRange
from app.market_data.timeframes import get_timeframe
from app.ml.domain import (
    MLCandleDependency,
    MLFeatureSpecification,
    MLHistoryPolicy,
    MLObservation,
)

_ALLOWED_RAW_FIELDS = {"open", "high", "low", "close", "volume"}


class _DecimalSeriesIndicator(Protocol):
    def calculate(self, source: DecimalSeries) -> IndicatorSeries: ...


class _DecimalSeriesFactory(Protocol):
    def __call__(self, period: int) -> _DecimalSeriesIndicator: ...


class _CandleSeriesIndicator(Protocol):
    def calculate(self, source: CandleSeries) -> IndicatorSeries: ...


class _CandleSeriesFactory(Protocol):
    def __call__(self, period: int) -> _CandleSeriesIndicator: ...


class _DecimalBundleIndicator(Protocol):
    def calculate(self, source: DecimalSeries) -> IndicatorBundle: ...


class _DecimalBundleFactory(Protocol):
    def __call__(self, *, period: int, standard_deviations: Decimal) -> _DecimalBundleIndicator: ...


class _MacdFactory(Protocol):
    def __call__(
        self,
        *,
        fast_period: int,
        slow_period: int,
        signal_period: int,
    ) -> _DecimalBundleIndicator: ...


def compute_feature(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    """Return one recomputed feature value and exact dependency for the observation."""
    _validate_authenticated_stream(candles, observation, context_range)
    dependency_range = _dependency_range(feature_spec, observation, context_range)
    selected = _candles_for_range(candles, dependency_range)
    if not selected:
        raise ValueError("insufficient authenticated context for feature computation")
    value = _feature_value(feature_spec, selected)
    dependency = MLCandleDependency(
        source=observation.dataset.provenance.source,
        data_range=dependency_range,
    )
    if dependency.source != observation.dataset.provenance.source:
        raise ValueError("inconsistent feature source identity")
    return value, dependency


def _validate_authenticated_stream(
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> None:
    if not candles:
        raise ValueError("feature computation requires authenticated candles")
    expected_exchange = observation.dataset.provenance.source.identity.exchange
    expected_market = observation.dataset.provenance.source.identity.market_type
    expected_symbol = observation.dataset.provenance.source.identity.symbol
    expected_timeframe = observation.dataset.feature_set.timeframe
    first = min(candle.open_time for candle in candles)
    last = max(candle.open_time for candle in candles)
    duration = get_timeframe(expected_timeframe).duration
    for candle in candles:
        if candle.exchange != expected_exchange:
            raise ValueError("feature source exchange mismatch")
        if candle.market_type != expected_market:
            raise ValueError("feature source market-type mismatch")
        if candle.symbol != expected_symbol:
            raise ValueError("feature source symbol mismatch")
        if candle.timeframe.code != expected_timeframe:
            raise ValueError("feature source timeframe mismatch")
        if not candle.is_closed:
            raise ValueError("feature source must contain only closed candles")
    if context_range.start < first or context_range.end > (last + duration):
        raise ValueError("feature source does not cover the authenticated context")


def _dependency_range(
    feature_spec: MLFeatureSpecification,
    observation: MLObservation,
    context_range: DataRange,
) -> DataRange:
    duration = get_timeframe(observation.dataset.feature_set.timeframe).duration
    if feature_spec.history_policy is MLHistoryPolicy.FROM_CONTEXT_START:
        start = context_range.start
    else:
        start = max(
            context_range.start,
            observation.observation_time - (feature_spec.lookback_candles * duration),
        )
    end = observation.observation_time
    if start >= end:
        raise ValueError("feature dependency range is empty")
    return DataRange(start, end)


def _candles_for_range(candles: Sequence[Candle], data_range: DataRange) -> tuple[Candle, ...]:
    if not candles:
        return ()
    chrono = tuple(sorted(candles, key=lambda item: item.open_time))
    selected = tuple(
        candle
        for candle in chrono
        if data_range.start <= candle.open_time < data_range.end and candle.is_closed
    )
    if not selected:
        return ()
    duration = get_timeframe(chrono[0].timeframe.code).duration
    expected = data_range.start
    for candle in selected:
        if candle.open_time != expected:
            raise ValueError("feature dependency contains corrupted or gapped source")
        expected += duration
    if expected != data_range.end:
        raise ValueError("feature dependency range does not match authenticated coverage")
    return selected


def _feature_value(
    feature_spec: MLFeatureSpecification,
    selected: Sequence[Candle],
) -> Decimal | None:
    if not selected:
        raise ValueError("feature dependency contains no candles")
    algorithm_name = feature_spec.algorithm.name
    algorithm_version = feature_spec.algorithm.version
    if algorithm_version != "1":
        raise ValueError("unsupported algorithm version")
    parameters = dict(feature_spec.algorithm.parameters)

    if algorithm_name in _ALLOWED_RAW_FIELDS:
        if feature_spec.source_fields != (algorithm_name,):
            raise ValueError("raw feature source fields mismatch")
        return _read_raw_field(selected, algorithm_name)

    if algorithm_name == "ema":
        period = _require_positive_int(parameters.get("period"), "ema")
        ema_series: DecimalSeries = DecimalSeries.from_candles(tuple(selected), field="close")
        ema_indicator: _DecimalSeriesIndicator = _load_ema_indicator(period)
        ema_result = ema_indicator.calculate(ema_series)
        value = ema_result.points[-1].value
        return value if isinstance(value, Decimal) or value is None else None

    if algorithm_name == "rsi":
        period = _require_positive_int(parameters.get("period"), "rsi")
        rsi_series: DecimalSeries = DecimalSeries.from_candles(tuple(selected), field="close")
        rsi_indicator: _DecimalSeriesIndicator = _load_rsi_indicator(period)
        rsi_result = rsi_indicator.calculate(rsi_series)
        value = rsi_result.points[-1].value
        return value if isinstance(value, Decimal) or value is None else None

    if algorithm_name == "atr":
        period = _require_positive_int(parameters.get("period"), "atr")
        atr_series: CandleSeries = CandleSeries(tuple(selected))
        atr_indicator: _CandleSeriesIndicator = _load_atr_indicator(period)
        atr_result = atr_indicator.calculate(atr_series)
        value = atr_result.points[-1].value
        return value if isinstance(value, Decimal) or value is None else None

    if algorithm_name in {"bollinger_middle", "bollinger_upper", "bollinger_lower"}:
        period = _require_positive_int(parameters.get("period"), "bollinger")
        standard_deviation = _require_decimal(
            parameters.get("standard_deviations"),
            "bollinger",
        )
        bollinger_series: DecimalSeries = DecimalSeries.from_candles(tuple(selected), field="close")
        bollinger_indicator: _DecimalBundleIndicator = _load_bollinger_indicator(
            period,
            standard_deviation,
        )
        bollinger_bundle = bollinger_indicator.calculate(bollinger_series)
        component_name = algorithm_name.removeprefix("bollinger_")
        value = bollinger_bundle.component(component_name).points[-1].value
        return value if isinstance(value, Decimal) or value is None else None

    if algorithm_name in {"macd", "macd_signal", "macd_histogram"}:
        fast = _require_positive_int(parameters.get("fast_period"), "macd")
        slow = _require_positive_int(parameters.get("slow_period"), "macd")
        signal_period = _require_positive_int(parameters.get("signal_period"), "macd")
        macd_series: DecimalSeries = DecimalSeries.from_candles(tuple(selected), field="close")
        macd_indicator: _DecimalBundleIndicator = _load_macd_indicator(
            fast,
            slow,
            signal_period,
        )
        macd_bundle = macd_indicator.calculate(macd_series)
        component_name = {
            "macd": "macd",
            "macd_signal": "signal",
            "macd_histogram": "histogram",
        }[algorithm_name]
        value = macd_bundle.component(component_name).points[-1].value
        return value if isinstance(value, Decimal) or value is None else None

    raise ValueError(f"unsupported feature recipe: {algorithm_name}")


def _load_ema_indicator(period: int) -> _DecimalSeriesIndicator:
    module = importlib.import_module("app.indicators.ema")
    factory: Callable[[int], _DecimalSeriesIndicator] = getattr(module, "ExponentialMovingAverage")
    return factory(period)


def _load_rsi_indicator(period: int) -> _DecimalSeriesIndicator:
    module = importlib.import_module("app.indicators.rsi")
    factory: Callable[[int], _DecimalSeriesIndicator] = getattr(module, "RelativeStrengthIndex")
    return factory(period)


def _load_atr_indicator(period: int) -> _CandleSeriesIndicator:
    module = importlib.import_module("app.indicators.atr")
    factory: Callable[[int], _CandleSeriesIndicator] = getattr(module, "AverageTrueRange")
    return factory(period)


def _load_bollinger_indicator(
    period: int,
    standard_deviation: Decimal,
) -> _DecimalBundleIndicator:
    module = importlib.import_module("app.indicators.bollinger")
    factory: Callable[..., _DecimalBundleIndicator] = getattr(module, "BollingerBands")
    return factory(period=period, standard_deviations=standard_deviation)


def _load_macd_indicator(
    fast: int,
    slow: int,
    signal_period: int,
) -> _DecimalBundleIndicator:
    module = importlib.import_module("app.indicators.macd")
    factory: Callable[..., _DecimalBundleIndicator] = getattr(
        module,
        "MovingAverageConvergenceDivergence",
    )
    return factory(
        fast_period=fast,
        slow_period=slow,
        signal_period=signal_period,
    )


def _read_raw_field(selected: Sequence[Candle], field_name: str) -> Decimal:
    candle = selected[-1]
    values = {
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }
    if field_name not in values:
        raise ValueError(f"unsupported raw field: {field_name}")
    return values[field_name]


def _require_positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"invalid {name} period")
    return value


def _require_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise ValueError(f"invalid {name} parameter")
    return value
