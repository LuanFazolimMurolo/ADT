"""Deterministic explainable ADT confidence evidence tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.indicators.regime import MarketRegimeKind
from app.ml.anomalies import MLAnomalyEvidence
from app.ml.confidence import (
    MLConfidenceComponentEvidence,
    MLConfidenceComponentKind,
    MLConfidenceEvidence,
    MLConfidencePerformanceRule,
    MLConfidenceSpecification,
    MLConfidenceWeight,
    build_confidence_evidence,
    confidence_component_evidence,
)
from app.ml.documents import decode_document, encode_document
from app.ml.domain import MLImplementationReference
from app.ml.errors import MLContractError
from app.ml.performance import (
    MLDegradationDirection,
    MLDegradationEvidence,
)
from tests.test_ml_anomalies import (
    _dataset as anomaly_dataset,
)
from tests.test_ml_anomalies import (
    _reference as anomaly_reference,
)
from tests.test_ml_anomalies import (
    _specification as anomaly_specification,
)
from tests.test_ml_anomalies import (
    _vector as anomaly_vector,
)
from tests.test_ml_market_intelligence import _intelligence
from tests.test_ml_performance import (
    _baseline,
    _degradation_specification,
    _prediction_evidence,
    _subject,
)
from tests.test_ml_performance import (
    _dataset as performance_dataset,
)

INSTANT = datetime(2026, 1, 1, 4, tzinfo=UTC)


def _implementation() -> MLImplementationReference:
    return MLImplementationReference(
        "adt-confidence-test-build",
        "1",
        "a" * 64,
    )


def _weights(
    *,
    anomaly: Decimal = Decimal("0.10"),
    degradation: Decimal = Decimal("0.20"),
    market_regime: Decimal = Decimal("0.30"),
    performance: Decimal = Decimal("0.40"),
) -> tuple[MLConfidenceWeight, ...]:
    return (
        MLConfidenceWeight(
            MLConfidenceComponentKind.ANOMALY,
            anomaly,
        ),
        MLConfidenceWeight(
            MLConfidenceComponentKind.DEGRADATION,
            degradation,
        ),
        MLConfidenceWeight(
            MLConfidenceComponentKind.MARKET_REGIME,
            market_regime,
        ),
        MLConfidenceWeight(
            MLConfidenceComponentKind.PERFORMANCE,
            performance,
        ),
    )


def _specification(
    *,
    performance_rule: MLConfidencePerformanceRule = MLConfidencePerformanceRule.AT_LEAST,
    performance_threshold: Decimal = Decimal("0.10"),
    compatible_regimes: tuple[MarketRegimeKind, ...] = (MarketRegimeKind.TREND,),
    weights: tuple[MLConfidenceWeight, ...] | None = None,
) -> MLConfidenceSpecification:
    return MLConfidenceSpecification(
        subject=_subject(),
        performance_rule=performance_rule,
        performance_threshold=performance_threshold,
        compatible_regimes=compatible_regimes,
        weights=_weights() if weights is None else weights,
        implementation=_implementation(),
    )


def _component(
    kind: MLConfidenceComponentKind,
    contribution: Decimal,
    *,
    evidence_character: str,
    snapshot_id: str = "snapshot-confidence-v1",
    snapshot_checksum: str = "b" * 64,
    observation_time: datetime = INSTANT,
    information_time: datetime | None = None,
    subject_identity: str | None = None,
) -> MLConfidenceComponentEvidence:
    strategic = kind in (
        MLConfidenceComponentKind.PERFORMANCE,
        MLConfidenceComponentKind.DEGRADATION,
    )

    return MLConfidenceComponentEvidence(
        kind=kind,
        evidence_identity=evidence_character * 64,
        evidence_checksum=evidence_character.upper() * 64,
        source_snapshot_id=snapshot_id,
        source_snapshot_checksum=snapshot_checksum,
        observation_time=observation_time,
        information_time=observation_time if information_time is None else information_time,
        contribution=contribution,
        strategy_subject_identity=(
            _subject().identity if strategic and subject_identity is None else subject_identity
        ),
    )


def _components(
    *,
    anomaly: Decimal = Decimal("1"),
    degradation: Decimal = Decimal("1"),
    market_regime: Decimal = Decimal("1"),
    performance: Decimal = Decimal("1"),
) -> tuple[MLConfidenceComponentEvidence, ...]:
    return (
        _component(
            MLConfidenceComponentKind.ANOMALY,
            anomaly,
            evidence_character="1",
        ),
        _component(
            MLConfidenceComponentKind.DEGRADATION,
            degradation,
            evidence_character="2",
        ),
        _component(
            MLConfidenceComponentKind.MARKET_REGIME,
            market_regime,
            evidence_character="3",
        ),
        _component(
            MLConfidenceComponentKind.PERFORMANCE,
            performance,
            evidence_character="4",
        ),
    )


def _confidence(
    *,
    components: tuple[MLConfidenceComponentEvidence, ...] | None = None,
) -> MLConfidenceEvidence:
    specification = _specification()

    return build_confidence_evidence(
        specification,
        _components() if components is None else components,
    )


def test_all_unsupported_components_produce_zero_score() -> None:
    evidence = _confidence(
        components=_components(
            anomaly=Decimal("0"),
            degradation=Decimal("0"),
            market_regime=Decimal("0"),
            performance=Decimal("0"),
        )
    )

    assert evidence.score == Decimal("0")


def test_all_supported_components_produce_one_score() -> None:
    evidence = _confidence()

    assert evidence.score == Decimal("1")


def test_weighted_components_produce_exact_decimal_score() -> None:
    evidence = _confidence(
        components=_components(
            anomaly=Decimal("0"),
            degradation=Decimal("1"),
            market_regime=Decimal("1"),
            performance=Decimal("0"),
        )
    )

    assert evidence.score == Decimal("0.50")


def test_weights_must_sum_exactly_to_one() -> None:
    invalid = _weights(
        performance=Decimal("0.41"),
    )

    with pytest.raises(MLContractError):
        _specification(weights=invalid)


def test_weight_must_be_strictly_positive() -> None:
    with pytest.raises(MLContractError):
        MLConfidenceWeight(
            MLConfidenceComponentKind.ANOMALY,
            Decimal("0"),
        )


def test_weight_above_one_is_rejected() -> None:
    with pytest.raises(MLContractError):
        MLConfidenceWeight(
            MLConfidenceComponentKind.ANOMALY,
            Decimal("1.01"),
        )


def test_component_contribution_is_binary_v1() -> None:
    with pytest.raises(MLContractError):
        _component(
            MLConfidenceComponentKind.ANOMALY,
            Decimal("0.5"),
            evidence_character="1",
        )


def test_missing_component_is_rejected() -> None:
    specification = _specification()

    with pytest.raises(MLContractError):
        MLConfidenceEvidence(
            specification=specification,
            components=_components()[:-1],
            implementation=specification.implementation,
        )


def test_duplicate_component_kind_is_rejected() -> None:
    specification = _specification()
    components = list(_components())

    components[-1] = _component(
        MLConfidenceComponentKind.ANOMALY,
        Decimal("1"),
        evidence_character="5",
    )

    with pytest.raises(MLContractError):
        MLConfidenceEvidence(
            specification=specification,
            components=tuple(components),
            implementation=specification.implementation,
        )


def test_noncanonical_component_order_is_rejected() -> None:
    specification = _specification()
    components = _components()

    with pytest.raises(MLContractError):
        MLConfidenceEvidence(
            specification=specification,
            components=tuple(reversed(components)),
            implementation=specification.implementation,
        )


def test_wrong_strategy_subject_is_rejected() -> None:
    specification = _specification()
    components = list(_components())

    components[-1] = replace(
        components[-1],
        strategy_subject_identity="f" * 64,
    )

    with pytest.raises(MLContractError):
        MLConfidenceEvidence(
            specification=specification,
            components=tuple(components),
            implementation=specification.implementation,
        )


def test_different_snapshot_is_rejected() -> None:
    specification = _specification()
    components = list(_components())

    components[0] = replace(
        components[0],
        source_snapshot_id="different-snapshot",
    )

    with pytest.raises(MLContractError):
        MLConfidenceEvidence(
            specification=specification,
            components=tuple(components),
            implementation=specification.implementation,
        )


def test_different_snapshot_checksum_is_rejected() -> None:
    specification = _specification()
    components = list(_components())

    components[0] = replace(
        components[0],
        source_snapshot_checksum="f" * 64,
    )

    with pytest.raises(MLContractError):
        MLConfidenceEvidence(
            specification=specification,
            components=tuple(components),
            implementation=specification.implementation,
        )


def test_different_observation_time_is_rejected() -> None:
    specification = _specification()
    components = list(_components())

    later = INSTANT + timedelta(hours=1)

    components[0] = replace(
        components[0],
        observation_time=later,
        information_time=later,
    )

    with pytest.raises(MLContractError):
        MLConfidenceEvidence(
            specification=specification,
            components=tuple(components),
            implementation=specification.implementation,
        )


def test_information_time_is_latest_component_availability() -> None:
    specification = _specification()
    components = list(_components())

    latest = INSTANT + timedelta(hours=2)

    components[0] = replace(
        components[0],
        information_time=latest,
    )

    evidence = MLConfidenceEvidence(
        specification=specification,
        components=tuple(components),
        implementation=specification.implementation,
    )

    assert evidence.information_time == latest


def test_component_rejects_information_before_observation() -> None:
    with pytest.raises(MLContractError):
        _component(
            MLConfidenceComponentKind.ANOMALY,
            Decimal("1"),
            evidence_character="1",
            information_time=INSTANT - timedelta(hours=1),
        )


def test_performance_at_least_boundary_is_inclusive() -> None:
    dataset = performance_dataset()
    specification = _specification(
        performance_rule=MLConfidencePerformanceRule.AT_LEAST,
        performance_threshold=Decimal("0.10"),
    )
    prediction = _prediction_evidence(
        dataset,
        output=Decimal("0.10"),
    )

    component = confidence_component_evidence(
        specification,
        prediction,
    )

    assert component.kind is MLConfidenceComponentKind.PERFORMANCE
    assert component.contribution == Decimal("1")
    assert component.strategy_subject_identity == _subject().identity
    assert component.observation_time == prediction.prediction.observation.observation_time
    assert component.information_time == prediction.prediction.prediction_time
    assert component.evidence_identity == prediction.identity
    assert component.evidence_checksum == prediction.checksum


def test_performance_at_least_below_threshold_is_unsupported() -> None:
    dataset = performance_dataset()
    specification = _specification(
        performance_rule=MLConfidencePerformanceRule.AT_LEAST,
        performance_threshold=Decimal("0.10"),
    )

    component = confidence_component_evidence(
        specification,
        _prediction_evidence(
            dataset,
            output=Decimal("0.09"),
        ),
    )

    assert component.contribution == Decimal("0")


def test_performance_at_most_boundary_is_inclusive() -> None:
    dataset = performance_dataset()
    specification = _specification(
        performance_rule=MLConfidencePerformanceRule.AT_MOST,
        performance_threshold=Decimal("0.10"),
    )

    component = confidence_component_evidence(
        specification,
        _prediction_evidence(
            dataset,
            output=Decimal("0.10"),
        ),
    )

    assert component.contribution == Decimal("1")


def test_performance_at_most_above_threshold_is_unsupported() -> None:
    dataset = performance_dataset()
    specification = _specification(
        performance_rule=MLConfidencePerformanceRule.AT_MOST,
        performance_threshold=Decimal("0.10"),
    )

    component = confidence_component_evidence(
        specification,
        _prediction_evidence(
            dataset,
            output=Decimal("0.11"),
        ),
    )

    assert component.contribution == Decimal("0")


def test_non_degraded_evidence_supports_confidence() -> None:
    dataset = performance_dataset()

    degradation = MLDegradationEvidence(
        specification=_degradation_specification(
            dataset,
            direction=MLDegradationDirection.BELOW_BASELINE,
            threshold=Decimal("0.02"),
        ),
        baseline=_baseline(
            value=Decimal("0.10"),
        ),
        prediction=_prediction_evidence(
            dataset,
            output=Decimal("0.09"),
        ),
    )

    assert degradation.degraded is False

    component = confidence_component_evidence(
        _specification(),
        degradation,
    )

    assert component.kind is MLConfidenceComponentKind.DEGRADATION
    assert component.contribution == Decimal("1")
    assert component.strategy_subject_identity == _subject().identity


def test_degraded_evidence_does_not_support_confidence() -> None:
    dataset = performance_dataset()

    degradation = MLDegradationEvidence(
        specification=_degradation_specification(
            dataset,
            direction=MLDegradationDirection.BELOW_BASELINE,
            threshold=Decimal("0.02"),
        ),
        baseline=_baseline(
            value=Decimal("0.10"),
        ),
        prediction=_prediction_evidence(
            dataset,
            output=Decimal("0.07"),
        ),
    )

    assert degradation.degraded is True

    component = confidence_component_evidence(
        _specification(),
        degradation,
    )

    assert component.contribution == Decimal("0")


def test_non_anomalous_evidence_supports_confidence() -> None:
    dataset = anomaly_dataset()

    anomaly = MLAnomalyEvidence(
        specification=anomaly_specification(dataset),
        reference=anomaly_reference(dataset),
        feature_vector=anomaly_vector(
            dataset,
            value=Decimal("0.01"),
        ),
    )

    assert anomaly.anomalous is False

    component = confidence_component_evidence(
        _specification(),
        anomaly,
    )

    assert component.kind is MLConfidenceComponentKind.ANOMALY
    assert component.contribution == Decimal("1")
    assert component.strategy_subject_identity is None


def test_anomalous_evidence_does_not_support_confidence() -> None:
    dataset = anomaly_dataset()

    anomaly = MLAnomalyEvidence(
        specification=anomaly_specification(dataset),
        reference=anomaly_reference(dataset),
        feature_vector=anomaly_vector(
            dataset,
            value=Decimal("0.03"),
        ),
    )

    assert anomaly.anomalous is True

    component = confidence_component_evidence(
        _specification(),
        anomaly,
    )

    assert component.contribution == Decimal("0")


def test_compatible_regime_supports_confidence() -> None:
    intelligence = _intelligence()

    assert intelligence.regime is MarketRegimeKind.TREND

    component = confidence_component_evidence(
        _specification(compatible_regimes=(MarketRegimeKind.TREND,)),
        intelligence,
    )

    assert component.kind is MLConfidenceComponentKind.MARKET_REGIME
    assert component.contribution == Decimal("1")
    assert component.strategy_subject_identity is None


def test_incompatible_regime_does_not_support_confidence() -> None:
    intelligence = _intelligence()

    component = confidence_component_evidence(
        _specification(compatible_regimes=(MarketRegimeKind.RANGE,)),
        intelligence,
    )

    assert component.contribution == Decimal("0")


def test_projection_preserves_exact_snapshot_context() -> None:
    dataset = performance_dataset()
    prediction = _prediction_evidence(
        dataset,
        output=Decimal("0.10"),
    )

    component = confidence_component_evidence(
        _specification(),
        prediction,
    )

    source = prediction.prediction.observation.dataset.provenance.source

    assert component.source_snapshot_id == source.snapshot_id
    assert component.source_snapshot_checksum == source.snapshot_checksum


def test_equal_inputs_produce_equal_identity_and_checksum() -> None:
    first = _confidence()
    second = _confidence()

    assert first == second
    assert first.identity == second.identity
    assert first.checksum == second.checksum


def test_component_change_changes_confidence_identity_and_checksum() -> None:
    first = _confidence()

    second = _confidence(
        components=_components(
            anomaly=Decimal("0"),
        )
    )

    assert first.score != second.score
    assert first.identity != second.identity
    assert first.checksum != second.checksum


def test_confidence_round_trips_canonical_codec() -> None:
    evidence = _confidence()

    encoded = encode_document(evidence)
    decoded = decode_document(encoded)

    assert isinstance(decoded, MLConfidenceEvidence)
    assert decoded == evidence
    assert decoded.identity == evidence.identity
    assert decoded.checksum == evidence.checksum
    assert decoded.score == Decimal("1")


def test_unsupported_evidence_type_is_rejected() -> None:
    with pytest.raises(TypeError):
        confidence_component_evidence(
            _specification(),
            object(),  # type: ignore[arg-type]
        )


def test_confidence_module_has_no_forbidden_authority_exports() -> None:
    import app.ml.confidence as confidence

    forbidden = (
        "probability",
        "win_rate",
        "expected_pnl",
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

    exported = tuple(name.lower() for name in vars(confidence))

    for token in forbidden:
        assert not any(token in name for name in exported)
