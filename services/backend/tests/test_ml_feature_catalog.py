from __future__ import annotations

import pytest

from app.indicators.domain import IndicatorDescriptor
from app.ml.domain import (
    InvalidMLContractError,
    MLFeatureSetSpecification,
    MLFeatureSpecification,
    MLHistoryPolicy,
    MLImplementationReference,
    MLMissingPolicy,
    MLResetPolicy,
    MLSeedPolicy,
    MLValueKind,
)
from app.ml.feature_catalog import (
    FEATURE_CATALOG,
    lookup_recipe,
    semantic_signature,
    validate_feature_spec,
)


def _spec(
    name: str,
    *,
    params: tuple[tuple[str, object], ...] = (),
    source_fields: tuple[str, ...] = ("close",),
) -> MLFeatureSpecification:
    return MLFeatureSpecification(
        name=f"{name}_feature",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning=f"{name} feature",
        algorithm=IndicatorDescriptor(name, "1", params),
        source_fields=source_fields,
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=2,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )


def test_catalog_lookup_is_deterministic() -> None:
    assert lookup_recipe("ema") is FEATURE_CATALOG["ema"]
    assert lookup_recipe("close") is FEATURE_CATALOG["close"]
    assert lookup_recipe("simple_return") is FEATURE_CATALOG["simple_return"]


def test_unknown_recipe_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_feature_spec(_spec("not_a_recipe"))


def test_missing_required_parameter_is_rejected() -> None:
    spec = MLFeatureSpecification(
        name="ema_feature",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA feature",
        algorithm=IndicatorDescriptor("ema", "1"),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=5,
        warmup_candles=4,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    with pytest.raises(ValueError):
        validate_feature_spec(spec)


def test_extra_parameter_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_feature_spec(_spec("ema", params=(("period", 5), ("extra", 2))))


def test_duplicate_feature_names_are_rejected_by_contract() -> None:
    impl = MLImplementationReference("adt-test-build", "1", "a" * 64)
    feature_a = MLFeatureSpecification(
        name="ema_alpha",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA feature",
        algorithm=IndicatorDescriptor("ema", "1", (("period", 12),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=12,
        warmup_candles=11,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    feature_b = MLFeatureSpecification(
        name="ema_alpha",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA feature",
        algorithm=IndicatorDescriptor("ema", "1", (("period", 12),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=12,
        warmup_candles=11,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    with pytest.raises(InvalidMLContractError):
        MLFeatureSetSpecification("1h", (feature_a, feature_b), impl)


def test_semantic_signature_is_stable() -> None:
    spec = MLFeatureSpecification(
        name="ema_feature",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA feature",
        algorithm=IndicatorDescriptor("ema", "1", (("period", 3),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=2,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    assert semantic_signature(spec) == semantic_signature(spec)
