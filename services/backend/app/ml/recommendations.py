"""Deterministic strategy-recommendation evidence for Phase 8-12."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.ml.anomalies import MLAnomalyEvidence
from app.ml.domain import (
    MLContract,
    MLImplementationReference,
    MLStrategyReference,
    _hash,
    _nested,
    _require,
    _same,
    _temporal,
    _text,
    _tuple,
    _utc,
)
from app.ml.performance import (
    MLDegradationEvidence,
    MLPerformancePredictionEvidence,
    MLPerformanceSubject,
)
from app.ml.regimes import MLMarketRegimeClassification


class MLRecommendationEvidenceKind(StrEnum):
    """Closed vocabulary of evidence admissible in Phase 8-12."""

    PERFORMANCE_PREDICTION = "PERFORMANCE_PREDICTION"
    DEGRADATION = "DEGRADATION"
    MARKET_REGIME = "MARKET_REGIME"
    ANOMALY = "ANOMALY"


@dataclass(frozen=True, slots=True)
class MLRecommendationEvidenceReference(MLContract):
    """Immutable reference to one already-produced ML evidence document."""

    kind: MLRecommendationEvidenceKind
    evidence_identity: str
    evidence_checksum: str
    information_time: datetime
    strategy_subject_identity: str | None = None

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _require(type(self.kind) is MLRecommendationEvidenceKind)
        _hash(self.evidence_identity)
        _hash(self.evidence_checksum)
        _utc(self.information_time)

        strategic = self.kind in (
            MLRecommendationEvidenceKind.PERFORMANCE_PREDICTION,
            MLRecommendationEvidenceKind.DEGRADATION,
        )
        if strategic:
            _require(self.strategy_subject_identity is not None)
            _hash(self.strategy_subject_identity)
        else:
            _require(self.strategy_subject_identity is None)


@dataclass(frozen=True, slots=True)
class MLStrategyRecommendation(MLContract):
    """Explainable recommendation evidence with no selection or execution authority."""

    candidate: MLStrategyReference
    evidence: tuple[MLRecommendationEvidenceReference, ...]
    rationale_codes: tuple[str, ...]
    recommendation_time: datetime
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.candidate, MLStrategyReference)
        _tuple(self.evidence, minimum=1)
        _tuple(self.rationale_codes, minimum=1)
        _utc(self.recommendation_time)
        _nested(self.implementation, MLImplementationReference)

        candidate_subject = _performance_subject_for_candidate(self.candidate)
        evidence_keys: list[tuple[str, str]] = []

        for reference in self.evidence:
            _nested(reference, MLRecommendationEvidenceReference)
            _temporal(reference.information_time <= self.recommendation_time)

            if reference.strategy_subject_identity is not None:
                _same(reference.strategy_subject_identity == candidate_subject.identity)

            evidence_keys.append(
                (
                    reference.kind.value,
                    reference.evidence_identity,
                )
            )

        _require(evidence_keys == sorted(evidence_keys))
        _require(len(evidence_keys) == len(set(evidence_keys)))

        for rationale in self.rationale_codes:
            _text(rationale, token=True)

        _require(tuple(sorted(self.rationale_codes)) == self.rationale_codes)
        _require(len(set(self.rationale_codes)) == len(self.rationale_codes))


def recommendation_evidence_reference(
    evidence: (
        MLPerformancePredictionEvidence
        | MLDegradationEvidence
        | MLMarketRegimeClassification
        | MLAnomalyEvidence
    ),
) -> MLRecommendationEvidenceReference:
    """Project one supported ML evidence contract into a bounded reference."""

    strategy_subject_identity: str | None = None

    if type(evidence) is MLPerformancePredictionEvidence:
        assert isinstance(evidence, MLPerformancePredictionEvidence)
        kind = MLRecommendationEvidenceKind.PERFORMANCE_PREDICTION
        information_time = evidence.prediction.prediction_time
        strategy_subject_identity = evidence.specification.subject.identity

    elif type(evidence) is MLDegradationEvidence:
        assert isinstance(evidence, MLDegradationEvidence)
        kind = MLRecommendationEvidenceKind.DEGRADATION
        information_time = evidence.prediction.prediction.prediction_time
        strategy_subject_identity = evidence.specification.performance.subject.identity

    elif type(evidence) is MLMarketRegimeClassification:
        assert isinstance(evidence, MLMarketRegimeClassification)
        kind = MLRecommendationEvidenceKind.MARKET_REGIME
        information_time = evidence.observation.observation_time

    elif type(evidence) is MLAnomalyEvidence:
        assert isinstance(evidence, MLAnomalyEvidence)
        kind = MLRecommendationEvidenceKind.ANOMALY
        information_time = evidence.feature_vector.observation.observation_time

    else:
        raise TypeError("unsupported recommendation evidence")

    return MLRecommendationEvidenceReference(
        kind=kind,
        evidence_identity=evidence.identity,
        evidence_checksum=evidence.checksum,
        information_time=information_time,
        strategy_subject_identity=strategy_subject_identity,
    )


def _performance_subject_for_candidate(
    candidate: MLStrategyReference,
) -> MLPerformanceSubject:
    return MLPerformanceSubject(
        plugin_name=candidate.plugin_name,
        plugin_version=candidate.plugin_version,
        plugin_schema_version=candidate.plugin_schema_version,
        lifecycle_version=candidate.lifecycle_version,
        parameters=candidate.parameters,
        parameters_checksum=candidate.parameters_checksum,
    )
