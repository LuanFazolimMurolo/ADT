"""Deterministic feature-anomaly evidence for Phase 8-11."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from app.market_data.domain import DataRange
from app.ml.domain import (
    MLContract,
    MLFeatureSetSpecification,
    MLFeatureSpecification,
    MLFeatureVector,
    MLImplementationReference,
    MLValueKind,
    _count,
    _hash,
    _inside,
    _nested,
    _range,
    _require,
    _same,
    _scalar,
    _source,
    _temporal,
    _text,
    _utc,
)
from app.optimization.temporal_domain import TemporalSnapshotReference

_DECIMAL_CONTEXT = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
)


class MLAnomalyMethod(StrEnum):
    """Deterministic anomaly methods supported by the v1 contract."""

    ABSOLUTE_Z_SCORE = "ABSOLUTE_Z_SCORE"


class MLAnomalyReferenceMethod(StrEnum):
    """Canonical historical estimator used by one anomaly reference."""

    POPULATION_MEAN_STANDARD_DEVIATION = "POPULATION_MEAN_STANDARD_DEVIATION"


@dataclass(frozen=True, slots=True)
class MLAnomalySpecification(MLContract):
    """One feature and threshold interpreted by one anomaly method."""

    feature_set: MLFeatureSetSpecification
    feature_name: str
    method: MLAnomalyMethod
    reference_method: MLAnomalyReferenceMethod
    threshold: Decimal
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.feature_set, MLFeatureSetSpecification)
        _text(self.feature_name, token=True)
        _require(type(self.method) is MLAnomalyMethod)
        _require(
            self.reference_method is MLAnomalyReferenceMethod.POPULATION_MEAN_STANDARD_DEVIATION
        )
        _scalar(self.threshold, MLValueKind.DECIMAL)
        _require(self.threshold > Decimal("0"))
        _nested(self.implementation, MLImplementationReference)
        _same(self.feature_set.implementation == self.implementation)

        feature = _feature_for(self)
        _require(feature.kind is MLValueKind.DECIMAL)


@dataclass(frozen=True, slots=True)
class MLAnomalyReference(MLContract):
    """Historical deterministic reference distribution for one feature."""

    specification: MLAnomalySpecification
    source: TemporalSnapshotReference
    data_range: DataRange
    mean: Decimal
    standard_deviation: Decimal
    sample_count: int
    information_time: datetime
    source_checksum: str

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.specification, MLAnomalySpecification)
        _source(self.source)
        _same(self.source.timeframe == self.specification.feature_set.timeframe)

        _range(self.data_range, self.source.timeframe)
        _require(_inside(self.data_range, self.source.available_coverage.data_range))

        _scalar(self.mean, MLValueKind.DECIMAL)
        _scalar(self.standard_deviation, MLValueKind.DECIMAL)
        _require(self.standard_deviation > Decimal("0"))

        _count(self.sample_count, minimum=2)

        _utc(self.information_time)
        _temporal(self.data_range.end <= self.information_time)

        _hash(self.source_checksum)


@dataclass(frozen=True, slots=True)
class MLAnomalyEvidence(MLContract):
    """Explainable anomaly classification for one feature-vector observation."""

    specification: MLAnomalySpecification
    reference: MLAnomalyReference
    feature_vector: MLFeatureVector
    observed_value: Decimal = field(init=False)
    signed_z_score: Decimal = field(init=False)
    absolute_z_score: Decimal = field(init=False)
    anomalous: bool = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.specification, MLAnomalySpecification)
        _nested(self.reference, MLAnomalyReference)
        _nested(self.feature_vector, MLFeatureVector)

        _same(self.reference.specification == self.specification)
        _same(self.feature_vector.feature_set == self.specification.feature_set)

        observation = self.feature_vector.observation
        observation_source = observation.dataset.provenance.source

        _same(self.reference.source.identity == observation_source.identity)
        _temporal(self.reference.information_time <= observation.observation_time)
        _temporal(self.reference.data_range.end <= observation.observation_time)

        feature_index = _feature_index(self.specification)
        value = self.feature_vector.values[feature_index]

        _require(type(value) is Decimal)
        assert isinstance(value, Decimal)
        _scalar(value, MLValueKind.DECIMAL)

        with localcontext(_DECIMAL_CONTEXT):
            signed_z_score = (value - self.reference.mean) / self.reference.standard_deviation
            absolute_z_score = abs(signed_z_score)

        _scalar(signed_z_score, MLValueKind.DECIMAL)
        _scalar(absolute_z_score, MLValueKind.DECIMAL)

        anomalous = absolute_z_score > self.specification.threshold

        object.__setattr__(self, "observed_value", value)
        object.__setattr__(self, "signed_z_score", signed_z_score)
        object.__setattr__(self, "absolute_z_score", absolute_z_score)
        object.__setattr__(self, "anomalous", anomalous)


def _feature_index(specification: MLAnomalySpecification) -> int:
    matches = tuple(
        index
        for index, feature in enumerate(specification.feature_set.features)
        if feature.name == specification.feature_name
    )
    _require(len(matches) == 1)
    return matches[0]


def _feature_for(
    specification: MLAnomalySpecification,
) -> MLFeatureSpecification:
    return specification.feature_set.features[_feature_index(specification)]
