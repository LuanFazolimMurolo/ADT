"""Deterministic offline regression evaluation foundation for Phase 8-07."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from hashlib import sha256

from app.ml.domain import (
    MLContract,
    MLDatasetMaterialization,
    MLFeatureVector,
    MLImplementationReference,
    MLLabel,
    MLMaterializationRow,
    MLObservation,
    _nested,
    _require,
    _same,
)
from app.ml.errors import MLContractError
from app.ml.training import (
    MLFittedRegressionModel,
    MLTrainingResult,
    predict_fitted_regression,
)
from app.optimization.canonical import canonical_json_bytes, decimal_text
from app.optimization.temporal_domain import TemporalSegmentRole

_DECIMAL_EVALUATION_PRECISION = 50
_DECIMAL_EVALUATION_ROUNDING = ROUND_HALF_EVEN


class MLEvaluationError(MLContractError):
    code = "ml_evaluation_error"
    default_message = "A avaliação ML falhou."


class MLEvaluationInputError(MLEvaluationError):
    code = "ml_evaluation_input_error"
    default_message = "A entrada da avaliação ML é incompatível."


class MLNoEligibleEvaluationRowsError(MLEvaluationError):
    code = "ml_no_eligible_evaluation_rows"
    default_message = "Nenhuma linha legal foi encontrada para avaliação."


@dataclass(frozen=True, slots=True)
class MLEvaluationSpecification(MLContract):
    """Immutable specification for deterministic offline model evaluation."""

    training_result: MLTrainingResult
    materialization: MLDatasetMaterialization
    role: TemporalSegmentRole
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.training_result, MLTrainingResult)
        _nested(self.materialization, MLDatasetMaterialization)
        _require(type(self.role) is TemporalSegmentRole)
        if self.role not in (
            TemporalSegmentRole.VALIDATION,
            TemporalSegmentRole.TEST,
        ):
            raise MLEvaluationInputError("evaluation role must be VALIDATION or TEST")
        _nested(self.implementation, MLImplementationReference)

        training_specification = self.training_result.training_specification
        model = self.training_result.fitted_model

        _same(self.materialization.dataset == training_specification.dataset)
        _same(self.materialization.split == training_specification.split)
        _same(model.feature_set == self.materialization.dataset.feature_set)

        target = self.materialization.dataset.target
        if target is None:
            raise MLEvaluationInputError("regression evaluation requires a supervised dataset")
        _same(model.target == target)


@dataclass(frozen=True, slots=True)
class MLRegressionMetrics(MLContract):
    """Deterministic aggregate regression metrics over one evaluation role."""

    mae: Decimal
    mse: Decimal
    rmse: Decimal

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        for value in (self.mae, self.mse, self.rmse):
            _require(type(value) is Decimal)
            _require(value.is_finite())
            _require(value >= Decimal("0"))


@dataclass(frozen=True, slots=True)
class MLRegressionEvaluationEvidence(MLContract):
    """Immutable aggregate evidence for one deterministic evaluation run."""

    specification: MLEvaluationSpecification
    metrics: MLRegressionMetrics
    evaluation_input_checksum: str
    sample_count: int
    first_observation_id: str
    last_observation_id: str

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.specification, MLEvaluationSpecification)
        _nested(self.metrics, MLRegressionMetrics)
        _require(
            type(self.evaluation_input_checksum) is str
            and len(self.evaluation_input_checksum) == 64
        )
        _require(type(self.sample_count) is int and self.sample_count >= 1)
        _require(type(self.first_observation_id) is str and bool(self.first_observation_id))
        _require(type(self.last_observation_id) is str and bool(self.last_observation_id))


def evaluate_regression(
    specification: MLEvaluationSpecification,
) -> MLRegressionEvaluationEvidence:
    """Evaluate an already-fitted regression model without any retraining."""

    _nested(specification, MLEvaluationSpecification)

    rows = _selected_evaluation_rows(specification)
    if not rows:
        raise MLNoEligibleEvaluationRowsError()

    model = specification.training_result.fitted_model

    with localcontext() as context:
        context.prec = _DECIMAL_EVALUATION_PRECISION
        context.rounding = _DECIMAL_EVALUATION_ROUNDING

        absolute_error_sum = Decimal("0")
        squared_error_sum = Decimal("0")

        for vector, label in rows:
            prediction = predict_fitted_regression(model, vector)
            target_value = _decimal_label_value(label)
            error = prediction - target_value
            absolute_error_sum += abs(error)
            squared_error_sum += error * error

        sample_count = len(rows)
        divisor = Decimal(sample_count)
        mae = absolute_error_sum / divisor
        mse = squared_error_sum / divisor
        rmse = mse.sqrt()

    metrics = MLRegressionMetrics(
        mae=mae,
        mse=mse,
        rmse=rmse,
    )

    checksum = _evaluation_input_checksum(
        specification,
        rows,
    )

    return MLRegressionEvaluationEvidence(
        specification=specification,
        metrics=metrics,
        evaluation_input_checksum=checksum,
        sample_count=len(rows),
        first_observation_id=rows[0][0].observation.identity,
        last_observation_id=rows[-1][0].observation.identity,
    )


def _selected_evaluation_rows(
    specification: MLEvaluationSpecification,
) -> tuple[tuple[MLFeatureVector, MLLabel], ...]:
    selected: list[tuple[MLFeatureVector, MLLabel]] = []
    seen: set[str] = set()

    for materialized_row in specification.materialization.rows:
        _nested(materialized_row, MLMaterializationRow)

        if materialized_row.role is not specification.role:
            continue

        label = materialized_row.label
        if label is None:
            raise MLEvaluationInputError("supervised evaluation requires a mature label")

        vector = materialized_row.vector
        specification.materialization.split.validate_row(
            vector,
            label,
            specification.role,
        )

        _same(vector.feature_set == specification.training_result.fitted_model.feature_set)
        _same(label.target == specification.training_result.fitted_model.target)

        identity = vector.observation.identity
        if identity in seen:
            raise MLEvaluationInputError("duplicate observation identity found in evaluation rows")
        seen.add(identity)

        _decimal_feature_values(vector)
        _decimal_label_value(label)

        selected.append((vector, label))

    return tuple(
        sorted(
            selected,
            key=lambda item: _observation_sort_key(item[0].observation),
        )
    )


def _decimal_feature_values(
    vector: MLFeatureVector,
) -> tuple[Decimal, ...]:
    values: list[Decimal] = []

    for value in vector.values:
        if type(value) is not Decimal:
            raise MLEvaluationInputError("evaluation features must be Decimal values only")
        if not value.is_finite():
            raise MLEvaluationInputError("non-finite Decimal evaluation feature encountered")
        values.append(value)

    return tuple(values)


def _decimal_label_value(label: MLLabel) -> Decimal:
    value = label.value
    if type(value) is not Decimal:
        raise MLEvaluationInputError("evaluation target must be a Decimal value")
    if not value.is_finite():
        raise MLEvaluationInputError("evaluation target must be finite")
    return value


def _evaluation_input_checksum(
    specification: MLEvaluationSpecification,
    rows: tuple[tuple[MLFeatureVector, MLLabel], ...],
) -> str:
    model: MLFittedRegressionModel = specification.training_result.fitted_model

    payload = {
        "model_checksum": model.checksum,
        "training_input_checksum": model.training_input_checksum,
        "dataset": specification.materialization.dataset.identity,
        "split": specification.materialization.split.identity,
        "role": specification.role.value,
        "evaluation_implementation": specification.implementation.identity,
        "numeric_policy": {
            "arithmetic": "decimal",
            "precision": _DECIMAL_EVALUATION_PRECISION,
            "rounding": str(_DECIMAL_EVALUATION_ROUNDING),
            "metrics": (
                "MAE",
                "MSE",
                "RMSE",
            ),
            "rmse": "decimal_sqrt",
        },
        "rows": [
            {
                "observation_id": vector.observation.identity,
                "feature_values": [
                    decimal_text(value) for value in _decimal_feature_values(vector)
                ],
                "label_value": decimal_text(_decimal_label_value(label)),
            }
            for vector, label in rows
        ],
    }

    return sha256(canonical_json_bytes(payload)).hexdigest()


def _observation_sort_key(
    observation: MLObservation,
) -> tuple[object, str]:
    return (
        observation.observation_time,
        observation.identity,
    )
