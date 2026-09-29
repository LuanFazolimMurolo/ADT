"""Canonical deterministic feature execution for Phase 8-03 v1 recipes.

This layer intentionally avoids fitted state, feature-to-feature DAGs, and any
runtime plugin model. Direct-source features are executed via the canonical
existing indicator code and the ML materializer remains the single source of
truth for dataset construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import TypeAlias

from app.indicators.domain import IndicatorDescriptor
from app.market_data.domain import Candle, DataRange
from app.market_data.timeframes import get_timeframe
from app.ml.domain import (
    MLCandleDependency,
    MLFeatureSetSpecification,
    MLFeatureSpecification,
    MLHistoryPolicy,
    MLMissingPolicy,
    MLObservation,
    MLResetPolicy,
    MLSeedPolicy,
    MLValueKind,
)
from app.ml.feature_catalog import semantic_signature, validate_feature_spec
from app.ml.features import compute_feature

_DIRECT_RECIPES = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "ema",
    "rsi",
    "atr",
    "bollinger_middle",
    "bollinger_upper",
    "bollinger_lower",
    "macd",
    "macd_signal",
    "macd_histogram",
}

_DERIVED_RECIPES = {
    "simple_return",
    "candle_body",
    "candle_range",
    "upper_wick",
    "lower_wick",
    "close_vs_ema_ratio",
    "atr_ratio",
    "bollinger_width",
    "bollinger_position",
}

ParameterValue: TypeAlias = bool | int | str | Decimal | None
RecipeParameters: TypeAlias = tuple[tuple[str, ParameterValue], ...]


def validate_feature_set(
    feature_set: MLFeatureSetSpecification,
) -> tuple[FeatureRecipeSignature, ...]:
    """Validate a feature set and return the semantic signatures for duplicate checks."""

    seen_names: set[str] = set()
    seen_signatures: set[tuple[object, ...]] = set()
    signatures: list[tuple[object, ...]] = []
    for feature in feature_set.features:
        validate_feature_spec(feature)
        if feature.name in seen_names:
            raise ValueError(f"duplicate feature name: {feature.name}")
        signature = semantic_signature(feature)
        if signature in seen_signatures:
            raise ValueError(f"duplicate semantic feature: {feature.name}")
        seen_names.add(feature.name)
        seen_signatures.add(signature)
        signatures.append(signature)
    return tuple(signatures)


FeatureRecipeSignature = tuple[object, ...]


def compute_feature_value(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    """Compute a v1 feature value while preserving the direct-source invariant."""

    validate_feature_spec(feature_spec)
    if feature_spec.algorithm.name in _DIRECT_RECIPES:
        return compute_feature(feature_spec, candles, observation, context_range)
    if feature_spec.algorithm.name in _DERIVED_RECIPES:
        return _compute_derived_feature(feature_spec, candles, observation, context_range)
    raise ValueError(f"unsupported feature recipe: {feature_spec.algorithm.name}")


def _compute_derived_feature(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    name = feature_spec.algorithm.name
    if name == "simple_return":
        return _simple_return(feature_spec, candles, observation, context_range)
    if name == "candle_body":
        return _candle_body(feature_spec, candles, observation, context_range)
    if name == "candle_range":
        return _candle_range(feature_spec, candles, observation, context_range)
    if name == "upper_wick":
        return _upper_wick(feature_spec, candles, observation, context_range)
    if name == "lower_wick":
        return _lower_wick(feature_spec, candles, observation, context_range)
    if name == "close_vs_ema_ratio":
        return _close_vs_ema_ratio(feature_spec, candles, observation, context_range)
    if name == "atr_ratio":
        return _atr_ratio(feature_spec, candles, observation, context_range)
    if name == "bollinger_width":
        return _bollinger_width(feature_spec, candles, observation, context_range)
    if name == "bollinger_position":
        return _bollinger_position(feature_spec, candles, observation, context_range)
    raise ValueError(f"unsupported derived feature: {name}")


def _simple_return(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    lookback = feature_spec.lookback_candles
    duration = get_timeframe(observation.dataset.feature_set.timeframe).duration
    anchor = observation.candle_open_time
    baseline_time = anchor - (duration * lookback)
    current_close = _close_for_time(candles, anchor)
    baseline_close = _close_for_time(candles, baseline_time)
    if baseline_close == 0:
        raise ValueError("simple_return denominator is zero")
    value = (current_close / baseline_close) - Decimal("1")
    dependency = MLCandleDependency(
        source=observation.dataset.provenance.source,
        data_range=DataRange(baseline_time, observation.observation_time),
    )
    return value, dependency


def _candle_body(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    current = _candle_for_time(candles, observation.candle_open_time)
    value = current.close - current.open
    dependency = MLCandleDependency(
        source=observation.dataset.provenance.source,
        data_range=DataRange(current.open_time, observation.observation_time),
    )
    return value, dependency


def _candle_range(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    current = _candle_for_time(candles, observation.candle_open_time)
    value = current.high - current.low
    dependency = MLCandleDependency(
        source=observation.dataset.provenance.source,
        data_range=DataRange(current.open_time, observation.observation_time),
    )
    return value, dependency


def _upper_wick(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    current = _candle_for_time(candles, observation.candle_open_time)
    upper = max(current.open, current.close)
    value = current.high - upper
    dependency = MLCandleDependency(
        source=observation.dataset.provenance.source,
        data_range=DataRange(current.open_time, observation.observation_time),
    )
    return value, dependency


def _lower_wick(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    current = _candle_for_time(candles, observation.candle_open_time)
    lower = min(current.open, current.close)
    value = lower - current.low
    dependency = MLCandleDependency(
        source=observation.dataset.provenance.source,
        data_range=DataRange(current.open_time, observation.observation_time),
    )
    return value, dependency


def _close_vs_ema_ratio(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    period = _require_int_param(feature_spec, "period")
    ema_feature = _indicator_feature_spec("ema", period=period)
    ema_value, dependency = compute_feature(ema_feature, candles, observation, context_range)
    if ema_value is None:
        return None, dependency
    current_close = _close_for_time(candles, observation.candle_open_time)
    if ema_value == 0:
        raise ValueError("close_vs_ema_ratio denominator is zero")
    return ((current_close / ema_value) - Decimal("1")), dependency


def _atr_ratio(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    period = _require_int_param(feature_spec, "period")
    atr_feature = _indicator_feature_spec("atr", period=period)
    atr_value, dependency = compute_feature(atr_feature, candles, observation, context_range)
    if atr_value is None:
        return None, dependency
    current_close = _close_for_time(candles, observation.candle_open_time)
    if current_close == 0:
        raise ValueError("atr_ratio denominator is zero")
    return atr_value / current_close, dependency


def _bollinger_width(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    period = _require_int_param(feature_spec, "period")
    std = _require_decimal_param(feature_spec, "standard_deviations")
    upper_feature = _indicator_feature_spec(
        "bollinger_upper", period=period, standard_deviations=std
    )
    lower_feature = _indicator_feature_spec(
        "bollinger_lower", period=period, standard_deviations=std
    )
    middle_feature = _indicator_feature_spec(
        "bollinger_middle", period=period, standard_deviations=std
    )
    upper_value, upper_dep = compute_feature(upper_feature, candles, observation, context_range)
    lower_value, lower_dep = compute_feature(lower_feature, candles, observation, context_range)
    middle_value, _ = compute_feature(middle_feature, candles, observation, context_range)
    if upper_value is None or lower_value is None or middle_value is None:
        return None, upper_dep if upper_dep.data_range == lower_dep.data_range else upper_dep
    if middle_value == 0:
        raise ValueError("bollinger_width denominator is zero")
    return ((upper_value - lower_value) / middle_value), upper_dep


def _bollinger_position(
    feature_spec: MLFeatureSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
    context_range: DataRange,
) -> tuple[Decimal | None, MLCandleDependency]:
    period = _require_int_param(feature_spec, "period")
    std = _require_decimal_param(feature_spec, "standard_deviations")
    upper_feature = _indicator_feature_spec(
        "bollinger_upper", period=period, standard_deviations=std
    )
    lower_feature = _indicator_feature_spec(
        "bollinger_lower", period=period, standard_deviations=std
    )
    upper_value, upper_dep = compute_feature(upper_feature, candles, observation, context_range)
    lower_value, lower_dep = compute_feature(lower_feature, candles, observation, context_range)
    if upper_value is None or lower_value is None:
        return None, upper_dep if upper_dep.data_range == lower_dep.data_range else upper_dep
    denominator = upper_value - lower_value
    if denominator == 0:
        raise ValueError("bollinger_position denominator is zero")
    current_close = _close_for_time(candles, observation.candle_open_time)
    return ((current_close - lower_value) / denominator), upper_dep


def _indicator_feature_spec(
    name: str,
    *,
    period: int | None = None,
    standard_deviations: Decimal | None = None,
) -> MLFeatureSpecification:
    params: RecipeParameters = ()
    if period is not None:
        params += (("period", period),)
    if standard_deviations is not None:
        params += (("standard_deviations", standard_deviations),)
    return MLFeatureSpecification(
        name=f"{name}_temp",
        kind=__kind_for_recipe(name),
        unit=__unit_for_recipe(name),
        meaning=f"temporary {name}",
        algorithm=__algorithm_for_recipe(name, params),
        source_fields=("close",),
        history_policy=__history_policy_for_recipe(name),
        lookback_candles=__lookback_for_recipe(name, period),
        warmup_candles=max(0, __lookback_for_recipe(name, period) - 1),
        seed_policy=__seed_policy_for_recipe(name),
        reset_policy=__reset_policy_for_recipe(name),
        missing_policy=__missing_policy_for_recipe(name),
    )


def __kind_for_recipe(name: str) -> MLValueKind:
    if name in {
        "ema",
        "rsi",
        "atr",
        "bollinger_middle",
        "bollinger_upper",
        "bollinger_lower",
        "macd",
        "macd_signal",
        "macd_histogram",
    }:
        return MLValueKind.DECIMAL
    raise ValueError(f"unsupported recipe for temporary feature spec: {name}")


def __unit_for_recipe(name: str) -> str:
    if name in {"ema", "atr", "bollinger_middle", "bollinger_upper", "bollinger_lower"}:
        return "quote/base"
    if name in {"rsi", "macd", "macd_signal", "macd_histogram"}:
        return "ratio"
    raise ValueError(f"unsupported unit for recipe: {name}")


def __algorithm_for_recipe(name: str, params: RecipeParameters) -> IndicatorDescriptor:
    return IndicatorDescriptor(name, "1", params)


def __history_policy_for_recipe(name: str) -> MLHistoryPolicy:
    return MLHistoryPolicy.TRAILING_WINDOW


def __lookback_for_recipe(name: str, period: int | None) -> int:
    if period is None:
        return 1
    return period


def __seed_policy_for_recipe(name: str) -> MLSeedPolicy:
    return MLSeedPolicy.ARITHMETIC_MEAN


def __reset_policy_for_recipe(name: str) -> MLResetPolicy:
    return MLResetPolicy.PER_OBSERVATION


def __missing_policy_for_recipe(name: str) -> MLMissingPolicy:
    return MLMissingPolicy.REJECT


def _close_for_time(candles: Sequence[Candle], open_time: datetime) -> Decimal:
    for candle in candles:
        if candle.open_time == open_time:
            return candle.close
    raise ValueError("source candle missing from authenticated dependency")


def _candle_for_time(candles: Sequence[Candle], open_time: datetime) -> Candle:
    for candle in candles:
        if candle.open_time == open_time:
            return candle
    raise ValueError("source candle missing from authenticated dependency")


def _require_int_param(feature: MLFeatureSpecification, key: str) -> int:
    params = dict(feature.algorithm.parameters)
    value = params.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"invalid {key} parameter")
    return value


def _require_decimal_param(feature: MLFeatureSpecification, key: str) -> Decimal:
    params = dict(feature.algorithm.parameters)
    value = params.get(key)
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"invalid {key} parameter")
    return value
