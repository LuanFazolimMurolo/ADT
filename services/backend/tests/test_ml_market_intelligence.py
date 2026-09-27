"""Deterministic asset/timeframe/regime intelligence tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.ml.documents import decode_document, encode_document
from app.ml.domain import MLImplementationReference
from app.ml.market_intelligence import (
    MLMarketContextIntelligence,
    market_context_intelligence,
)
from app.ml.regimes import classify_market_regime_evidence
from tests.test_ml_features import _dataset, _observation
from tests.test_ml_regimes import (
    _classified_point,
    _specification,
)


def _classification(
    *,
    candle_index: int = 4,
):
    dataset = _dataset()
    observation = _observation(
        dataset,
        candle_index=candle_index,
    )

    return classify_market_regime_evidence(
        _specification(),
        observation,
        _classified_point(observation),
    )


def _intelligence() -> MLMarketContextIntelligence:
    return market_context_intelligence(_classification())


def test_market_context_projects_exact_dataset_identity() -> None:
    intelligence = _intelligence()

    source = intelligence.classification.observation.dataset.provenance.source

    assert intelligence.source == source
    assert intelligence.dataset_identity == source.identity
    assert intelligence.symbol == source.identity.symbol
    assert intelligence.timeframe == source.identity.timeframe


def test_market_context_projects_exact_regime() -> None:
    intelligence = _intelligence()

    assert intelligence.regime is intelligence.classification.regime
    assert intelligence.trend_direction is intelligence.classification.trend_direction


def test_market_context_information_time_is_exact_observation_time() -> None:
    intelligence = _intelligence()

    assert intelligence.information_time == intelligence.classification.observation.observation_time


def test_market_context_binds_classification_implementation() -> None:
    classification = _classification()

    intelligence = MLMarketContextIntelligence(
        classification=classification,
        implementation=classification.specification.implementation,
    )

    assert intelligence.implementation == classification.specification.implementation


def test_different_implementation_is_rejected() -> None:
    classification = _classification()

    foreign = MLImplementationReference(
        name="foreign-market-intelligence",
        version="1",
        content_checksum="f" * 64,
    )

    with pytest.raises(Exception):
        MLMarketContextIntelligence(
            classification=classification,
            implementation=foreign,
        )


def test_observation_change_changes_market_context_identity() -> None:
    first = market_context_intelligence(
        _classification(candle_index=4),
    )
    second = market_context_intelligence(
        _classification(candle_index=5),
    )

    assert first.information_time != second.information_time
    assert first.identity != second.identity
    assert first.checksum != second.checksum


def test_classification_change_changes_market_context_identity() -> None:
    classification = _classification()

    changed = replace(
        classification,
        fast_ema=classification.fast_ema + 1,
    )

    first = market_context_intelligence(classification)
    second = market_context_intelligence(changed)

    assert first.identity != second.identity
    assert first.checksum != second.checksum


def test_equal_inputs_produce_equal_market_context_identity_and_checksum() -> None:
    first = _intelligence()
    second = _intelligence()

    assert first == second
    assert first.identity == second.identity
    assert first.checksum == second.checksum


def test_market_context_round_trips_canonical_codec() -> None:
    intelligence = _intelligence()

    encoded = encode_document(intelligence)
    decoded = decode_document(encoded)

    assert isinstance(decoded, MLMarketContextIntelligence)
    assert decoded == intelligence
    assert decoded.identity == intelligence.identity
    assert decoded.checksum == intelligence.checksum


def test_market_context_properties_are_derived_not_independent_contract_fields() -> None:
    intelligence = _intelligence()

    assert "symbol" not in intelligence.__dataclass_fields__
    assert "timeframe" not in intelligence.__dataclass_fields__
    assert "regime" not in intelligence.__dataclass_fields__
    assert "trend_direction" not in intelligence.__dataclass_fields__
    assert "information_time" not in intelligence.__dataclass_fields__


def test_market_intelligence_module_has_no_ranking_recommendation_or_execution_authority() -> None:
    import app.ml.market_intelligence as market_intelligence

    forbidden = (
        "confidence",
        "rank",
        "ranking",
        "winner",
        "best",
        "recommend",
        "selected",
        "select_candidate",
        "approve",
        "activate",
        "deploy",
        "execute",
        "order",
        "paper",
        "live",
        "repository",
        "database",
    )

    exported = tuple(name.lower() for name in vars(market_intelligence))

    for token in forbidden:
        assert not any(token in name for name in exported)
