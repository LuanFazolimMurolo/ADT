from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ml.documents import decode_document, encode_document
from app.ml.domain import (
    MLImplementationReference,
    MLStrategyReference,
)
from app.ml.recommendations import (
    MLRecommendationEvidenceKind,
    MLRecommendationEvidenceReference,
    MLStrategyRecommendation,
)
from app.strategies.definitions import (
    StoredStrategyParameter,
    strategy_parameter_checksum,
)
from app.strategies.domain import StrategyParameterKind

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _implementation() -> MLImplementationReference:
    return MLImplementationReference(
        "adt-recommendation-test-build",
        "1",
        "e" * 64,
    )


def _candidate(
    *,
    fast_period: int = 10,
) -> MLStrategyReference:
    parameters = (
        StoredStrategyParameter(
            "fast_period",
            StrategyParameterKind.INTEGER,
            fast_period,
        ),
        StoredStrategyParameter(
            "slow_period",
            StrategyParameterKind.INTEGER,
            20,
        ),
    )

    parameters_checksum = strategy_parameter_checksum(parameters)

    return MLStrategyReference(
        plugin_name="ema_cross",
        plugin_version="1",
        plugin_schema_version=1,
        lifecycle_version=1,
        parameters=parameters,
        parameters_checksum=parameters_checksum,
        configuration_checksum=("a" * 64 if fast_period == 10 else "b" * 64),
        implementation=_implementation(),
    )


def _reference(
    kind: MLRecommendationEvidenceKind,
    *,
    suffix: str = "a",
    information_time: datetime = START + HOUR * 5,
    fast_period: int = 10,
) -> MLRecommendationEvidenceReference:
    candidate_subject_identity = None
    if kind in (
        MLRecommendationEvidenceKind.PERFORMANCE_PREDICTION,
        MLRecommendationEvidenceKind.DEGRADATION,
    ):
        from app.ml.performance import MLPerformanceSubject

        candidate = _candidate(fast_period=fast_period)
        candidate_subject_identity = MLPerformanceSubject(
            plugin_name=candidate.plugin_name,
            plugin_version=candidate.plugin_version,
            plugin_schema_version=candidate.plugin_schema_version,
            lifecycle_version=candidate.lifecycle_version,
            parameters=candidate.parameters,
            parameters_checksum=candidate.parameters_checksum,
        ).identity

    return MLRecommendationEvidenceReference(
        kind=kind,
        evidence_identity=suffix * 64,
        evidence_checksum=("f" if suffix != "f" else "e") * 64,
        information_time=information_time,
        strategy_subject_identity=candidate_subject_identity,
    )


def _recommendation(
    *,
    fast_period: int = 10,
) -> MLStrategyRecommendation:
    evidence = (
        _reference(
            MLRecommendationEvidenceKind.ANOMALY,
            suffix="a",
        ),
        _reference(
            MLRecommendationEvidenceKind.MARKET_REGIME,
            suffix="b",
        ),
        _reference(
            MLRecommendationEvidenceKind.PERFORMANCE_PREDICTION,
            suffix="c",
            fast_period=fast_period,
        ),
    )

    return MLStrategyRecommendation(
        candidate=_candidate(fast_period=fast_period),
        evidence=evidence,
        rationale_codes=(
            "market_regime_compatible",
            "no_material_anomaly",
            "positive_predicted_performance",
        ),
        recommendation_time=START + HOUR * 6,
        implementation=_implementation(),
    )


def test_strategy_recommendation_binds_exact_candidate_configuration() -> None:
    recommendation = _recommendation()

    assert recommendation.candidate.plugin_name == "ema_cross"
    assert recommendation.candidate.parameters_checksum == strategy_parameter_checksum(
        recommendation.candidate.parameters
    )


def test_strategy_parameter_change_with_stale_strategic_evidence_is_rejected() -> None:
    first = _recommendation()

    with pytest.raises(Exception):
        MLStrategyRecommendation(
            candidate=_candidate(fast_period=11),
            evidence=first.evidence,
            rationale_codes=first.rationale_codes,
            recommendation_time=first.recommendation_time,
            implementation=first.implementation,
        )


def test_strategy_parameter_change_with_matching_evidence_changes_recommendation_identity() -> None:
    first = _recommendation(fast_period=10)
    second = _recommendation(fast_period=11)

    assert first.candidate.identity != second.candidate.identity
    assert first.identity != second.identity
    assert first.checksum != second.checksum


def test_evidence_reference_requires_hash_identity_and_checksum() -> None:
    with pytest.raises(Exception):
        MLRecommendationEvidenceReference(
            kind=MLRecommendationEvidenceKind.ANOMALY,
            evidence_identity="not-a-hash",
            evidence_checksum="f" * 64,
            information_time=START,
        )

    with pytest.raises(Exception):
        MLRecommendationEvidenceReference(
            kind=MLRecommendationEvidenceKind.ANOMALY,
            evidence_identity="a" * 64,
            evidence_checksum="not-a-hash",
            information_time=START,
        )


def test_future_evidence_is_rejected() -> None:
    with pytest.raises(Exception):
        MLStrategyRecommendation(
            candidate=_candidate(),
            evidence=(
                _reference(
                    MLRecommendationEvidenceKind.ANOMALY,
                    information_time=START + HOUR * 7,
                ),
            ),
            rationale_codes=("no_material_anomaly",),
            recommendation_time=START + HOUR * 6,
            implementation=_implementation(),
        )


def test_duplicate_evidence_reference_is_rejected() -> None:
    reference = _reference(
        MLRecommendationEvidenceKind.ANOMALY,
    )

    with pytest.raises(Exception):
        MLStrategyRecommendation(
            candidate=_candidate(),
            evidence=(reference, reference),
            rationale_codes=("no_material_anomaly",),
            recommendation_time=START + HOUR * 6,
            implementation=_implementation(),
        )


def test_noncanonical_evidence_order_is_rejected() -> None:
    with pytest.raises(Exception):
        MLStrategyRecommendation(
            candidate=_candidate(),
            evidence=(
                _reference(
                    MLRecommendationEvidenceKind.PERFORMANCE_PREDICTION,
                    suffix="c",
                ),
                _reference(
                    MLRecommendationEvidenceKind.ANOMALY,
                    suffix="a",
                ),
            ),
            rationale_codes=(
                "no_material_anomaly",
                "positive_predicted_performance",
            ),
            recommendation_time=START + HOUR * 6,
            implementation=_implementation(),
        )


def test_duplicate_rationale_code_is_rejected() -> None:
    with pytest.raises(Exception):
        MLStrategyRecommendation(
            candidate=_candidate(),
            evidence=(
                _reference(
                    MLRecommendationEvidenceKind.ANOMALY,
                ),
            ),
            rationale_codes=(
                "no_material_anomaly",
                "no_material_anomaly",
            ),
            recommendation_time=START + HOUR * 6,
            implementation=_implementation(),
        )


def test_noncanonical_rationale_order_is_rejected() -> None:
    with pytest.raises(Exception):
        MLStrategyRecommendation(
            candidate=_candidate(),
            evidence=(
                _reference(
                    MLRecommendationEvidenceKind.ANOMALY,
                ),
            ),
            rationale_codes=(
                "positive_predicted_performance",
                "no_material_anomaly",
            ),
            recommendation_time=START + HOUR * 6,
            implementation=_implementation(),
        )


def test_recommendation_contracts_round_trip_through_canonical_codec() -> None:
    recommendation = _recommendation()

    for value in (
        recommendation.evidence[0],
        recommendation,
    ):
        encoded = encode_document(value)
        decoded = decode_document(encoded)

        assert decoded == value
        assert decoded.identity == value.identity
        assert decoded.checksum == value.checksum


def test_equal_inputs_produce_equal_recommendation_identity_and_checksum() -> None:
    first = _recommendation()
    second = _recommendation()

    assert first == second
    assert first.identity == second.identity
    assert first.checksum == second.checksum


def test_evidence_checksum_change_changes_recommendation_checksum() -> None:
    first = _recommendation()

    changed = MLRecommendationEvidenceReference(
        kind=first.evidence[0].kind,
        evidence_identity=first.evidence[0].evidence_identity,
        evidence_checksum="d" * 64,
        information_time=first.evidence[0].information_time,
    )

    second = MLStrategyRecommendation(
        candidate=first.candidate,
        evidence=(
            changed,
            first.evidence[1],
            first.evidence[2],
        ),
        rationale_codes=first.rationale_codes,
        recommendation_time=first.recommendation_time,
        implementation=first.implementation,
    )

    assert first.checksum != second.checksum


def test_recommendation_module_exposes_no_selection_confidence_or_execution_authority() -> None:
    import app.ml.recommendations as recommendations

    forbidden = (
        "confidence",
        "winner",
        "best",
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

    exported = tuple(name.lower() for name in vars(recommendations))

    for token in forbidden:
        assert not any(token in name for name in exported)


def test_market_regime_evidence_projection_preserves_identity_checksum_and_time() -> None:
    from app.ml.recommendations import recommendation_evidence_reference
    from app.ml.regimes import classify_market_regime_evidence
    from tests.test_ml_features import _dataset, _observation
    from tests.test_ml_regimes import (
        _classified_point,
        _specification,
    )

    dataset = _dataset()
    observation = _observation(dataset, candle_index=4)

    evidence = classify_market_regime_evidence(
        _specification(),
        observation,
        _classified_point(observation),
    )

    reference = recommendation_evidence_reference(evidence)

    assert reference.kind is MLRecommendationEvidenceKind.MARKET_REGIME
    assert reference.evidence_identity == evidence.identity
    assert reference.evidence_checksum == evidence.checksum
    assert reference.information_time == observation.observation_time
    assert reference.strategy_subject_identity is None


def test_anomaly_evidence_projection_preserves_identity_checksum_and_time() -> None:
    from decimal import Decimal

    from app.ml.anomalies import MLAnomalyEvidence
    from app.ml.recommendations import recommendation_evidence_reference
    from tests.test_ml_anomalies import (
        _dataset,
        _reference,
        _specification,
        _vector,
    )

    dataset = _dataset()

    evidence = MLAnomalyEvidence(
        specification=_specification(dataset),
        reference=_reference(dataset),
        feature_vector=_vector(
            dataset,
            value=Decimal("0.03"),
        ),
    )

    reference = recommendation_evidence_reference(evidence)

    assert reference.kind is MLRecommendationEvidenceKind.ANOMALY
    assert reference.evidence_identity == evidence.identity
    assert reference.evidence_checksum == evidence.checksum
    assert reference.information_time == evidence.feature_vector.observation.observation_time
    assert reference.strategy_subject_identity is None


def test_unsupported_recommendation_evidence_is_rejected() -> None:
    from app.ml.recommendations import recommendation_evidence_reference

    with pytest.raises(TypeError):
        recommendation_evidence_reference(object())  # type: ignore[arg-type]


def test_strategic_evidence_from_different_candidate_is_rejected() -> None:
    from app.ml.performance import MLPerformanceSubject

    foreign_candidate = _candidate(fast_period=11)

    foreign_subject = MLPerformanceSubject(
        plugin_name=foreign_candidate.plugin_name,
        plugin_version=foreign_candidate.plugin_version,
        plugin_schema_version=foreign_candidate.plugin_schema_version,
        lifecycle_version=foreign_candidate.lifecycle_version,
        parameters=foreign_candidate.parameters,
        parameters_checksum=foreign_candidate.parameters_checksum,
    )

    reference = MLRecommendationEvidenceReference(
        kind=MLRecommendationEvidenceKind.PERFORMANCE_PREDICTION,
        evidence_identity="a" * 64,
        evidence_checksum="f" * 64,
        information_time=START + HOUR * 5,
        strategy_subject_identity=foreign_subject.identity,
    )

    with pytest.raises(Exception):
        MLStrategyRecommendation(
            candidate=_candidate(fast_period=10),
            evidence=(reference,),
            rationale_codes=("positive_predicted_performance",),
            recommendation_time=START + HOUR * 6,
            implementation=_implementation(),
        )


def test_contextual_evidence_must_not_claim_strategy_subject() -> None:
    from app.ml.performance import MLPerformanceSubject

    candidate = _candidate()

    subject = MLPerformanceSubject(
        plugin_name=candidate.plugin_name,
        plugin_version=candidate.plugin_version,
        plugin_schema_version=candidate.plugin_schema_version,
        lifecycle_version=candidate.lifecycle_version,
        parameters=candidate.parameters,
        parameters_checksum=candidate.parameters_checksum,
    )

    with pytest.raises(Exception):
        MLRecommendationEvidenceReference(
            kind=MLRecommendationEvidenceKind.ANOMALY,
            evidence_identity="a" * 64,
            evidence_checksum="f" * 64,
            information_time=START,
            strategy_subject_identity=subject.identity,
        )


def test_performance_prediction_projection_preserves_strategy_subject_and_time() -> None:
    from decimal import Decimal

    from app.ml.recommendations import recommendation_evidence_reference
    from tests.test_ml_performance import (
        _dataset,
        _prediction_evidence,
    )

    dataset = _dataset()

    evidence = _prediction_evidence(
        dataset,
        output=Decimal("0.07"),
    )

    reference = recommendation_evidence_reference(evidence)

    assert reference.kind is MLRecommendationEvidenceKind.PERFORMANCE_PREDICTION
    assert reference.evidence_identity == evidence.identity
    assert reference.evidence_checksum == evidence.checksum
    assert reference.information_time == evidence.prediction.prediction_time
    assert reference.strategy_subject_identity == evidence.specification.subject.identity


def test_degradation_projection_preserves_strategy_subject_and_time() -> None:
    from decimal import Decimal

    from app.ml.performance import (
        MLDegradationDirection,
        MLDegradationEvidence,
    )
    from app.ml.recommendations import recommendation_evidence_reference
    from tests.test_ml_performance import (
        _baseline,
        _dataset,
        _degradation_specification,
        _prediction_evidence,
    )

    dataset = _dataset()

    evidence = MLDegradationEvidence(
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

    reference = recommendation_evidence_reference(evidence)

    assert reference.kind is MLRecommendationEvidenceKind.DEGRADATION
    assert reference.evidence_identity == evidence.identity
    assert reference.evidence_checksum == evidence.checksum
    assert reference.information_time == evidence.prediction.prediction.prediction_time
    assert (
        reference.strategy_subject_identity == evidence.specification.performance.subject.identity
    )
