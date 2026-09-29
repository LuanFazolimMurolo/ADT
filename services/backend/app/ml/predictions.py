"""Minimal deterministic prediction contracts for Phase 8-04 regression gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.ml.domain import (
    MLContract,
    MLFeatureSetSpecification,
    MLFeatureVector,
    MLImplementationReference,
    MLObservation,
    MLTargetSpecification,
    MLValueKind,
    _hash,
    _nested,
    _require,
    _scalar,
    _text,
    _utc,
    target_maturity,
)
from app.optimization.canonical import deterministic_id, document_checksum


class MLPredictionProblemType(StrEnum):
    REGRESSION = "REGRESSION"


@dataclass(frozen=True, slots=True)
class MLModelReference(MLContract):
    """Small immutable model reference without registry or training logic."""

    model_id: str
    model_version: str
    artifact_checksum: str
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _text(self.model_id, token=True)
        _text(self.model_version, token=True)
        _hash(self.artifact_checksum)
        _nested(self.implementation, MLImplementationReference)


@dataclass(frozen=True, slots=True)
class MLPredictionSpecification(MLContract):
    """Immutable task definition for a regression prediction."""

    problem_type: MLPredictionProblemType
    target: MLTargetSpecification
    feature_set: MLFeatureSetSpecification
    implementation: MLImplementationReference
    model_reference: MLModelReference
    output_kind: MLValueKind = MLValueKind.DECIMAL
    output_unit: str = ""
    horizon_candles: int = field(init=False)
    feature_vector: MLFeatureVector | None = None

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _require(type(self.problem_type) is MLPredictionProblemType)
        _require(self.problem_type is MLPredictionProblemType.REGRESSION)
        _nested(self.target, MLTargetSpecification)
        _nested(self.feature_set, MLFeatureSetSpecification)
        _nested(self.implementation, MLImplementationReference)
        _nested(self.model_reference, MLModelReference)
        _require(type(self.output_kind) is MLValueKind)
        _require(self.output_kind is MLValueKind.DECIMAL)
        if not self.output_unit:
            object.__setattr__(self, "output_unit", self.target.unit)
        _text(self.output_unit)
        _require(self.output_unit == self.target.unit)
        _require(self.target.horizon_candles > 0)
        if self.feature_vector is not None:
            _nested(self.feature_vector, MLFeatureVector)
            _same(self.feature_vector.feature_set, self.feature_set)
        object.__setattr__(self, "horizon_candles", self.target.horizon_candles)


@dataclass(frozen=True, slots=True)
class MLRegressionPrediction(MLContract):
    """A deterministic regression prediction made at observation time."""

    prediction_specification: MLPredictionSpecification
    model_reference: MLModelReference
    observation: MLObservation
    feature_vector: MLFeatureVector
    prediction_time: datetime
    output: Decimal

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.prediction_specification, MLPredictionSpecification)
        _nested(self.model_reference, MLModelReference)
        _nested(self.observation, MLObservation)
        _nested(self.feature_vector, MLFeatureVector)
        _same(self.model_reference, self.prediction_specification.model_reference)
        _same(self.feature_vector.observation, self.observation)
        _same(self.feature_vector.feature_set, self.prediction_specification.feature_set)
        _same(self.prediction_specification.target, self.observation.dataset.target)
        _require(self.prediction_specification.problem_type is MLPredictionProblemType.REGRESSION)
        _utc(self.prediction_time)
        _require(self.prediction_time == self.observation.observation_time)
        _require(self.feature_vector.feature_information_time <= self.prediction_time)
        _require(self.prediction_time < target_maturity(self.observation))
        _scalar(self.output, self.prediction_specification.output_kind)
        _require(self.output.is_finite())
        _require(
            self.prediction_specification.output_unit == self.prediction_specification.target.unit
        )
        _require(
            self.prediction_specification.horizon_candles
            == self.prediction_specification.target.horizon_candles
        )

    @property
    def identity(self) -> str:
        from app.ml.documents import logical_payload

        payload = {
            "schema_version": self.schema_version,
            "namespace": self.namespace,
            "prediction_specification": logical_payload(self.prediction_specification),
            "model_reference": logical_payload(self.model_reference),
            "observation": logical_payload(self.observation),
            "feature_vector": logical_payload(self.feature_vector),
            "prediction_time": self.prediction_time.isoformat(),
        }
        return deterministic_id(f"{self.namespace}-{type(self).__name__}-v1", payload)

    @property
    def checksum(self) -> str:
        from app.ml.documents import logical_payload

        return document_checksum(logical_payload(self))


def _same(left: object, right: object) -> None:
    if left != right:
        raise ValueError("prediction contract reference mismatch")


def _prediction_logical_payload(value: MLRegressionPrediction) -> dict[str, object]:
    encoded: dict[str, object] = {
        "schema_version": value.schema_version,
        "namespace": value.namespace,
        "prediction_specification": value.prediction_specification,
        "model_reference": value.model_reference,
        "observation": value.observation,
        "feature_vector": value.feature_vector,
        "prediction_time": value.prediction_time,
    }
    return encoded
