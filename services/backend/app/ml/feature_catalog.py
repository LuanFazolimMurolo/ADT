"""Static deterministic catalog for v1 ML feature recipes.

This module defines the Phase 8-03 feature vocabulary without introducing
fitted state, dynamic plugin execution, or a general expression engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias

from app.ml.domain import (
    MLFeatureSpecification,
    MLHistoryPolicy,
    MLMissingPolicy,
    MLPreprocessing,
    MLResetPolicy,
    MLSeedPolicy,
    MLValueKind,
)

ParameterSchema: TypeAlias = tuple[tuple[str, type], ...]


@dataclass(frozen=True, slots=True)
class FeatureRecipeEntry:
    """Immutable metadata for one canonical recipe in the v1 catalog."""

    name: str
    version: str
    family: str
    kind: MLValueKind
    unit: str
    meaning: str
    source_fields: tuple[str, ...]
    allowed_history_policies: tuple[MLHistoryPolicy, ...]
    allowed_seed_policies: tuple[MLSeedPolicy, ...]
    allowed_reset_policies: tuple[MLResetPolicy, ...]
    allowed_missing_policies: tuple[MLMissingPolicy, ...]
    parameter_schema: ParameterSchema = ()


_RAW_RECIPE: tuple[FeatureRecipeEntry, ...] = (
    FeatureRecipeEntry(
        name="open",
        version="1",
        family="raw_market",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="raw open value",
        source_fields=("open",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.NONE,
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="high",
        version="1",
        family="raw_market",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="raw high value",
        source_fields=("high",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.NONE,
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="low",
        version="1",
        family="raw_market",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="raw low value",
        source_fields=("low",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.NONE,
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="close",
        version="1",
        family="raw_market",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="raw close value",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.NONE,
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="volume",
        version="1",
        family="raw_market",
        kind=MLValueKind.DECIMAL,
        unit="base/quote",
        meaning="raw volume value",
        source_fields=("volume",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.NONE,
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
)


_INDICATOR_RECIPE: tuple[FeatureRecipeEntry, ...] = (
    FeatureRecipeEntry(
        name="ema",
        version="1",
        family="trend",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA of close",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int),),
    ),
    FeatureRecipeEntry(
        name="rsi",
        version="1",
        family="momentum",
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="Wilder RSI of close",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int),),
    ),
    FeatureRecipeEntry(
        name="atr",
        version="1",
        family="volatility",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="average true range",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int),),
    ),
    FeatureRecipeEntry(
        name="bollinger_middle",
        version="1",
        family="volatility",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="Bollinger middle band",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int), ("standard_deviations", float)),
    ),
    FeatureRecipeEntry(
        name="bollinger_upper",
        version="1",
        family="volatility",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="Bollinger upper band",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int), ("standard_deviations", float)),
    ),
    FeatureRecipeEntry(
        name="bollinger_lower",
        version="1",
        family="volatility",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="Bollinger lower band",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int), ("standard_deviations", float)),
    ),
    FeatureRecipeEntry(
        name="macd",
        version="1",
        family="trend",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="MACD line",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("fast_period", int), ("slow_period", int), ("signal_period", int)),
    ),
    FeatureRecipeEntry(
        name="macd_signal",
        version="1",
        family="trend",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="MACD signal line",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("fast_period", int), ("slow_period", int), ("signal_period", int)),
    ),
    FeatureRecipeEntry(
        name="macd_histogram",
        version="1",
        family="trend",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="MACD histogram",
        source_fields=("close",),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(
            MLSeedPolicy.ARITHMETIC_MEAN,
            MLSeedPolicy.FIRST_VALUE,
            MLSeedPolicy.NONE,
        ),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("fast_period", int), ("slow_period", int), ("signal_period", int)),
    ),
)


_DERIVED_RECIPE: tuple[FeatureRecipeEntry, ...] = (
    FeatureRecipeEntry(
        name="simple_return",
        version="1",
        family="price_level",
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="close return over a fixed trailing horizon",
        source_fields=("close",),
        allowed_history_policies=(MLHistoryPolicy.TRAILING_WINDOW,),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION,),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="candle_body",
        version="1",
        family="price_level",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="close - open",
        source_fields=("open", "close"),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="candle_range",
        version="1",
        family="price_level",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="high - low",
        source_fields=("high", "low"),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="upper_wick",
        version="1",
        family="price_level",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="high - max(open, close)",
        source_fields=("high", "open", "close"),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="lower_wick",
        version="1",
        family="price_level",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="min(open, close) - low",
        source_fields=("low", "open", "close"),
        allowed_history_policies=(
            MLHistoryPolicy.TRAILING_WINDOW,
            MLHistoryPolicy.FROM_CONTEXT_START,
        ),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION, MLResetPolicy.CONTEXT_START),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
    ),
    FeatureRecipeEntry(
        name="close_vs_ema_ratio",
        version="1",
        family="trend",
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="(close / ema(period)) - 1",
        source_fields=("close",),
        allowed_history_policies=(MLHistoryPolicy.TRAILING_WINDOW,),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION,),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int),),
    ),
    FeatureRecipeEntry(
        name="atr_ratio",
        version="1",
        family="volatility",
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="atr(period) / close",
        source_fields=("close",),
        allowed_history_policies=(MLHistoryPolicy.TRAILING_WINDOW,),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION,),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int),),
    ),
    FeatureRecipeEntry(
        name="bollinger_width",
        version="1",
        family="volatility",
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="(upper - lower) / middle",
        source_fields=("close",),
        allowed_history_policies=(MLHistoryPolicy.TRAILING_WINDOW,),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION,),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int), ("standard_deviations", Decimal)),
    ),
    FeatureRecipeEntry(
        name="bollinger_position",
        version="1",
        family="volatility",
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="(close - lower) / (upper - lower)",
        source_fields=("close",),
        allowed_history_policies=(MLHistoryPolicy.TRAILING_WINDOW,),
        allowed_seed_policies=(MLSeedPolicy.NONE,),
        allowed_reset_policies=(MLResetPolicy.PER_OBSERVATION,),
        allowed_missing_policies=(MLMissingPolicy.REJECT, MLMissingPolicy.PRESERVE_NONE),
        parameter_schema=(("period", int), ("standard_deviations", Decimal)),
    ),
)

CATALOG_ENTRIES: tuple[FeatureRecipeEntry, ...] = (
    *_RAW_RECIPE,
    *_INDICATOR_RECIPE,
    *_DERIVED_RECIPE,
)
FEATURE_CATALOG: dict[str, FeatureRecipeEntry] = {entry.name: entry for entry in CATALOG_ENTRIES}


def lookup_recipe(name: str, *, version: str = "1") -> FeatureRecipeEntry:
    """Return a canonical recipe entry by name and version."""

    try:
        entry = FEATURE_CATALOG[name]
    except KeyError as error:
        raise ValueError(f"unsupported feature recipe: {name}") from error
    if entry.version != version:
        raise ValueError(f"unsupported feature recipe version: {version}")
    return entry


def validate_feature_spec(spec: MLFeatureSpecification) -> FeatureRecipeEntry:
    """Validate an ML feature specification against the static v1 catalog."""

    recipe = lookup_recipe(spec.algorithm.name, version=spec.algorithm.version)
    if spec.preprocessing is not MLPreprocessing.NONE:
        raise ValueError("fitted preprocessing is unsupported in v1")
    if spec.kind != recipe.kind:
        raise ValueError(f"recipe {spec.algorithm.name} kind mismatch")
    if spec.unit != recipe.unit:
        raise ValueError(f"recipe {spec.algorithm.name} unit mismatch")
    if spec.source_fields != recipe.source_fields:
        raise ValueError(f"recipe {spec.algorithm.name} source_fields mismatch")
    if spec.history_policy not in recipe.allowed_history_policies:
        raise ValueError(f"recipe {spec.algorithm.name} history policy not allowed")
    if spec.seed_policy not in recipe.allowed_seed_policies:
        raise ValueError(f"recipe {spec.algorithm.name} seed policy not allowed")
    if spec.reset_policy not in recipe.allowed_reset_policies:
        raise ValueError(f"recipe {spec.algorithm.name} reset policy not allowed")
    if spec.missing_policy not in recipe.allowed_missing_policies:
        raise ValueError(f"recipe {spec.algorithm.name} missing policy not allowed")

    actual = dict(spec.algorithm.parameters)
    expected = dict(recipe.parameter_schema)
    if set(actual) != set(expected):
        raise ValueError(f"recipe {spec.algorithm.name} parameters mismatch")
    for key, value in actual.items():
        expected_type = expected[key]
        if type(value) is not expected_type:
            raise ValueError(f"recipe {spec.algorithm.name} parameter {key} has invalid type")
        if expected_type is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"recipe {spec.algorithm.name} parameter {key} has invalid type")
            int_value: int = value
            if int_value <= 0:
                raise ValueError(f"recipe {spec.algorithm.name} parameter {key} must be positive")
        elif expected_type is Decimal:
            if not isinstance(value, Decimal):
                raise ValueError(f"recipe {spec.algorithm.name} parameter {key} has invalid type")
            decimal_value: Decimal = value
            if not decimal_value.is_finite() or decimal_value <= 0:
                raise ValueError(
                    "recipe "
                    f"{spec.algorithm.name} parameter {key} must be a positive "
                    "finite Decimal"
                )
    return recipe


def semantic_signature(spec: MLFeatureSpecification) -> tuple[object, ...]:
    """Return an identity key used to reject duplicate recipe semantics."""

    recipe = validate_feature_spec(spec)
    return (
        recipe.name,
        recipe.version,
        tuple(spec.algorithm.parameters),
        spec.source_fields,
        spec.kind,
        spec.unit,
        spec.history_policy,
        spec.lookback_candles,
        spec.warmup_candles,
        spec.seed_policy,
        spec.reset_policy,
        spec.missing_policy,
    )
