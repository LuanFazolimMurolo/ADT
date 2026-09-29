"""Deterministic ML market-regime classification evidence tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from app.indicators.regime import (
    MarketRegimeKind,
    MarketRegimePoint,
    MarketRegimePolicy,
    TrendDirection,
)
from app.ml.documents import decode_document, encode_document
from app.ml.errors import MLTemporalLeakageError
from app.ml.regimes import (
    MLMarketRegimeClassification,
    MLMarketRegimeInputError,
    classify_market_regime_evidence,
    market_regime_specification,
)
from tests.test_ml_dataset_contracts import implementation
from tests.test_ml_features import _dataset, _observation


def _specification():
    return market_regime_specification(
        MarketRegimePolicy(),
        implementation=implementation(),
    )


def _classified_point(observation):
    return MarketRegimePoint(
        event_time=observation.observation_time,
        regime=MarketRegimeKind.TREND,
        trend_direction=TrendDirection.UP,
        fast_ema=Decimal("102"),
        slow_ema=Decimal("100"),
        atr=Decimal("1"),
        atr_ratio=Decimal("0.01"),
        trend_strength=Decimal("2"),
    )


def test_market_regime_specification_is_immutable() -> None:
    specification = _specification()

    with pytest.raises(FrozenInstanceError):
        specification.policy = MarketRegimePolicy().descriptor  # type: ignore[misc]


def test_market_regime_specification_preserves_canonical_policy() -> None:
    policy = MarketRegimePolicy(
        fast_ema_period=10,
        slow_ema_period=30,
        atr_period=20,
        volatile_atr_ratio=Decimal("0.04"),
        trend_strength_threshold=Decimal("1.5"),
    )

    specification = market_regime_specification(
        policy,
        implementation=implementation(),
    )

    assert specification.policy == policy.descriptor
    assert specification.warmup_points == policy.warmup_points


def test_market_regime_classification_projects_canonical_point() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=4)
    point = _classified_point(observation)

    result = classify_market_regime_evidence(
        _specification(),
        observation,
        point,
    )

    assert result.observation == observation
    assert result.regime is MarketRegimeKind.TREND
    assert result.trend_direction is TrendDirection.UP
    assert result.fast_ema == Decimal("102")
    assert result.slow_ema == Decimal("100")
    assert result.atr == Decimal("1")
    assert result.atr_ratio == Decimal("0.01")
    assert result.trend_strength == Decimal("2")


def test_warmup_projection_preserves_unavailable_metrics() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=1)

    point = MarketRegimePoint(
        event_time=observation.observation_time,
        regime=MarketRegimeKind.WARMUP,
        trend_direction=TrendDirection.NONE,
        fast_ema=None,
        slow_ema=None,
        atr=None,
        atr_ratio=None,
        trend_strength=None,
    )

    result = classify_market_regime_evidence(
        _specification(),
        observation,
        point,
    )

    assert result.regime is MarketRegimeKind.WARMUP
    assert result.trend_direction is TrendDirection.NONE
    assert result.fast_ema is None
    assert result.slow_ema is None
    assert result.atr is None
    assert result.atr_ratio is None
    assert result.trend_strength is None


def test_point_must_align_exactly_to_observation_time() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=4)
    point = _classified_point(observation)

    foreign_observation = _observation(dataset, candle_index=5)

    with pytest.raises(MLTemporalLeakageError):
        classify_market_regime_evidence(
            _specification(),
            foreign_observation,
            point,
        )


def test_classification_rejects_forged_invalid_trend_direction() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=4)
    result = classify_market_regime_evidence(
        _specification(),
        observation,
        _classified_point(observation),
    )

    with pytest.raises(MLMarketRegimeInputError):
        replace(
            result,
            trend_direction=TrendDirection.NONE,
        )


def test_classification_rejects_forged_warmup_metrics() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=1)

    with pytest.raises(MLMarketRegimeInputError):
        MLMarketRegimeClassification(
            specification=_specification(),
            observation=observation,
            regime=MarketRegimeKind.WARMUP,
            trend_direction=TrendDirection.NONE,
            fast_ema=Decimal("1"),
            slow_ema=None,
            atr=None,
            atr_ratio=None,
            trend_strength=None,
        )


def test_noncanonical_policy_descriptor_is_rejected() -> None:
    policy = MarketRegimePolicy()

    bad_descriptor = replace(
        policy.descriptor,
        name="not_market_regime",
    )

    with pytest.raises(MLMarketRegimeInputError):
        replace(
            _specification(),
            policy=bad_descriptor,
        )


def test_classification_is_deterministic() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=4)
    point = _classified_point(observation)
    specification = _specification()

    first = classify_market_regime_evidence(
        specification,
        observation,
        point,
    )
    second = classify_market_regime_evidence(
        specification,
        observation,
        point,
    )

    assert first == second
    assert first.identity == second.identity
    assert first.checksum == second.checksum


def test_market_regime_classification_round_trips_document() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=4)

    result = classify_market_regime_evidence(
        _specification(),
        observation,
        _classified_point(observation),
    )

    encoded = encode_document(result)
    rebuilt = decode_document(encoded)

    assert isinstance(rebuilt, MLMarketRegimeClassification)
    assert rebuilt == result
    assert rebuilt.checksum == result.checksum


def test_policy_change_changes_classification_identity() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=4)
    point = _classified_point(observation)

    first = classify_market_regime_evidence(
        _specification(),
        observation,
        point,
    )

    second_specification = market_regime_specification(
        MarketRegimePolicy(
            volatile_atr_ratio=Decimal("0.04"),
        ),
        implementation=implementation(),
    )

    second = classify_market_regime_evidence(
        second_specification,
        observation,
        point,
    )

    assert first.identity != second.identity
    assert first.checksum != second.checksum
