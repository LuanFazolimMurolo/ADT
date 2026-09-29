"""Deterministic explainable ADT confidence evidence for Phase 8-14."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from app.indicators.regime import MarketRegimeKind
from app.ml.anomalies import MLAnomalyEvidence
from app.ml.domain import (
    MLContract,
    MLImplementationReference,
    MLValueKind,
    _hash,
    _nested,
    _require,
    _same,
    _scalar,
    _temporal,
    _tuple,
    _utc,
)
from app.ml.market_intelligence import MLMarketContextIntelligence
from app.ml.performance import (
    MLDegradationEvidence,
    MLPerformancePredictionEvidence,
    MLPerformanceSubject,
)

_DECIMAL_CONTEXT = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class MLConfidenceComponentKind(StrEnum):
    """Closed v1 vocabulary of confidence components."""

    PERFORMANCE = "PERFORMANCE"
    DEGRADATION = "DEGRADATION"
    ANOMALY = "ANOMALY"
    MARKET_REGIME = "MARKET_REGIME"


class MLConfidencePerformanceRule(StrEnum):
    """Explicit direction for interpreting one performance threshold."""

    AT_LEAST = "AT_LEAST"
    AT_MOST = "AT_MOST"


@dataclass(frozen=True, slots=True)
class MLConfidenceWeight(MLContract):
    """One explicit deterministic component weight."""

    kind: MLConfidenceComponentKind
    weight: Decimal

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _require(type(self.kind) is MLConfidenceComponentKind)
        _scalar(self.weight, MLValueKind.DECIMAL)
        _require(self.weight.is_finite())
        _require(_ZERO < self.weight <= _ONE)


@dataclass(frozen=True, slots=True)
class MLConfidenceSpecification(MLContract):
    """Explicit bounded policy for deterministic confidence synthesis."""

    subject: MLPerformanceSubject
    performance_rule: MLConfidencePerformanceRule
    performance_threshold: Decimal
    compatible_regimes: tuple[MarketRegimeKind, ...]
    weights: tuple[MLConfidenceWeight, ...]
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.subject, MLPerformanceSubject)

        _require(type(self.performance_rule) is MLConfidencePerformanceRule)

        _scalar(
            self.performance_threshold,
            MLValueKind.DECIMAL,
        )
        _require(self.performance_threshold.is_finite())

        _tuple(
            self.compatible_regimes,
            minimum=1,
        )
        _require(all(type(regime) is MarketRegimeKind for regime in self.compatible_regimes))
        _require(
            tuple(regime.value for regime in self.compatible_regimes)
            == tuple(sorted(regime.value for regime in self.compatible_regimes))
        )
        _require(len(set(self.compatible_regimes)) == len(self.compatible_regimes))

        _tuple(
            self.weights,
            minimum=4,
            maximum=4,
        )

        expected_kinds = tuple(
            sorted(
                MLConfidenceComponentKind,
                key=lambda item: item.value,
            )
        )

        kinds: list[MLConfidenceComponentKind] = []

        with localcontext(_DECIMAL_CONTEXT):
            total = _ZERO

            for weight in self.weights:
                _nested(
                    weight,
                    MLConfidenceWeight,
                )
                kinds.append(weight.kind)
                total += weight.weight

        _require(tuple(kinds) == expected_kinds)
        _same(total == _ONE)

        _nested(
            self.implementation,
            MLImplementationReference,
        )


@dataclass(frozen=True, slots=True)
class MLConfidenceComponentEvidence(MLContract):
    """One bounded contribution derived from existing evidence."""

    kind: MLConfidenceComponentKind
    evidence_identity: str
    evidence_checksum: str

    source_snapshot_id: str
    source_snapshot_checksum: str
    observation_time: datetime

    information_time: datetime
    contribution: Decimal

    strategy_subject_identity: str | None = None

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _require(type(self.kind) is MLConfidenceComponentKind)

        _hash(self.evidence_identity)
        _hash(self.evidence_checksum)

        _require(
            type(self.source_snapshot_id) is str
            and bool(self.source_snapshot_id)
            and self.source_snapshot_id == self.source_snapshot_id.strip()
        )
        _hash(self.source_snapshot_checksum)

        _utc(self.observation_time)
        _utc(self.information_time)

        _temporal(self.observation_time <= self.information_time)

        _scalar(
            self.contribution,
            MLValueKind.DECIMAL,
        )
        _require(self.contribution in (_ZERO, _ONE))

        strategic = self.kind in (
            MLConfidenceComponentKind.PERFORMANCE,
            MLConfidenceComponentKind.DEGRADATION,
        )

        if strategic:
            _require(self.strategy_subject_identity is not None)
            assert self.strategy_subject_identity is not None
            _hash(self.strategy_subject_identity)
        else:
            _require(self.strategy_subject_identity is None)


@dataclass(frozen=True, slots=True)
class MLConfidenceEvidence(MLContract):
    """Deterministic bounded and explainable confidence score."""

    specification: MLConfidenceSpecification
    components: tuple[
        MLConfidenceComponentEvidence,
        ...,
    ]
    implementation: MLImplementationReference

    score: Decimal = field(init=False)
    information_time: datetime = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(
            self.specification,
            MLConfidenceSpecification,
        )

        _tuple(
            self.components,
            minimum=4,
            maximum=4,
        )

        _nested(
            self.implementation,
            MLImplementationReference,
        )

        _same(self.implementation == self.specification.implementation)

        expected_subject = self.specification.subject.identity

        expected_kinds = tuple(
            sorted(
                MLConfidenceComponentKind,
                key=lambda item: item.value,
            )
        )

        kinds: list[MLConfidenceComponentKind] = []

        context_keys: list[tuple[str, str, datetime]] = []

        component_by_kind: dict[
            MLConfidenceComponentKind,
            MLConfidenceComponentEvidence,
        ] = {}

        for component in self.components:
            _nested(
                component,
                MLConfidenceComponentEvidence,
            )

            kinds.append(component.kind)

            component_by_kind[component.kind] = component

            context_keys.append(
                (
                    component.source_snapshot_id,
                    component.source_snapshot_checksum,
                    component.observation_time,
                )
            )

            if component.strategy_subject_identity is not None:
                _same(component.strategy_subject_identity == expected_subject)

        _require(tuple(kinds) == expected_kinds)

        _require(len(component_by_kind) == 4)

        _same(len(set(context_keys)) == 1)

        weight_by_kind = {weight.kind: weight.weight for weight in self.specification.weights}

        with localcontext(_DECIMAL_CONTEXT):
            score = sum(
                (
                    weight_by_kind[component.kind] * component.contribution
                    for component in self.components
                ),
                start=_ZERO,
            )

        _scalar(
            score,
            MLValueKind.DECIMAL,
        )

        _require(_ZERO <= score <= _ONE)

        information_time = max(component.information_time for component in self.components)

        _utc(information_time)

        for component in self.components:
            _temporal(component.information_time <= information_time)

        object.__setattr__(
            self,
            "score",
            score,
        )

        object.__setattr__(
            self,
            "information_time",
            information_time,
        )


def confidence_component_evidence(
    specification: MLConfidenceSpecification,
    evidence: (
        MLPerformancePredictionEvidence
        | MLDegradationEvidence
        | MLAnomalyEvidence
        | MLMarketContextIntelligence
    ),
) -> MLConfidenceComponentEvidence:
    """Project supported evidence into one bounded confidence contribution."""

    _nested(
        specification,
        MLConfidenceSpecification,
    )

    strategy_subject_identity: str | None = None

    if type(evidence) is MLPerformancePredictionEvidence:
        assert isinstance(
            evidence,
            MLPerformancePredictionEvidence,
        )

        kind = MLConfidenceComponentKind.PERFORMANCE

        observation = evidence.prediction.observation

        source = observation.dataset.provenance.source

        information_time = evidence.prediction.prediction_time

        strategy_subject_identity = evidence.specification.subject.identity

        output = evidence.prediction.output

        _scalar(
            output,
            MLValueKind.DECIMAL,
        )

        if specification.performance_rule is MLConfidencePerformanceRule.AT_LEAST:
            supported = output >= specification.performance_threshold
        else:
            supported = output <= specification.performance_threshold

    elif type(evidence) is MLDegradationEvidence:
        assert isinstance(
            evidence,
            MLDegradationEvidence,
        )

        kind = MLConfidenceComponentKind.DEGRADATION

        observation = evidence.prediction.prediction.observation

        source = observation.dataset.provenance.source

        information_time = evidence.prediction.prediction.prediction_time

        strategy_subject_identity = evidence.specification.performance.subject.identity

        supported = not evidence.degraded

    elif type(evidence) is MLAnomalyEvidence:
        assert isinstance(
            evidence,
            MLAnomalyEvidence,
        )

        kind = MLConfidenceComponentKind.ANOMALY

        observation = evidence.feature_vector.observation

        source = observation.dataset.provenance.source

        information_time = observation.observation_time

        supported = not evidence.anomalous

    elif type(evidence) is MLMarketContextIntelligence:
        assert isinstance(
            evidence,
            MLMarketContextIntelligence,
        )

        kind = MLConfidenceComponentKind.MARKET_REGIME

        observation = evidence.classification.observation

        source = observation.dataset.provenance.source

        information_time = evidence.information_time

        supported = evidence.regime in specification.compatible_regimes

    else:
        raise TypeError("unsupported confidence evidence")

    return MLConfidenceComponentEvidence(
        kind=kind,
        evidence_identity=evidence.identity,
        evidence_checksum=evidence.checksum,
        source_snapshot_id=source.snapshot_id,
        source_snapshot_checksum=(source.snapshot_checksum),
        observation_time=(observation.observation_time),
        information_time=information_time,
        contribution=(_ONE if supported else _ZERO),
        strategy_subject_identity=(strategy_subject_identity),
    )


def build_confidence_evidence(
    specification: MLConfidenceSpecification,
    components: tuple[
        MLConfidenceComponentEvidence,
        ...,
    ],
) -> MLConfidenceEvidence:
    """Build confidence from already-projected evidence."""

    return MLConfidenceEvidence(
        specification=specification,
        components=components,
        implementation=(specification.implementation),
    )
