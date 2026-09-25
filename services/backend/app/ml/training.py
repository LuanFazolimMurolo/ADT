"""Deterministic Decimal OLS training foundation for Phase 8-06 Gate 1."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

from app.ml.domain import (
    MLContract,
    MLDatasetMaterialization,
    MLDatasetSpecification,
    MLFeatureSetSpecification,
    MLFeatureVector,
    MLImplementationReference,
    MLLabel,
    MLMaterializationRow,
    MLObservation,
    MLTargetSpecification,
    _nested,
    _require,
    _same,
)
from app.ml.errors import MLContractError
from app.ml.temporal_safety import MLFitScope
from app.optimization.temporal_domain import TemporalSegmentRole

_DECIMAL_TRAINING_PRECISION = 50
_DECIMAL_TRAINING_ROUNDING = ROUND_HALF_EVEN


class MLTrainingError(MLContractError):
    code = "ml_training_error"
    default_message = "O treinamento ML falhou."


class MLTrainingInputError(MLTrainingError):
    code = "ml_training_input_error"
    default_message = "A entrada do treinamento ML é incompatível."


class MLUnsupportedTrainingAlgorithmError(MLTrainingError):
    code = "ml_unsupported_training_algorithm"
    default_message = "O algoritmo de treinamento ML não é suportado."


class MLNoEligibleTrainingRowsError(MLTrainingError):
    code = "ml_no_eligible_training_rows"
    default_message = "Nenhuma linha de treinamento legal foi encontrada."


class MLDegenerateTrainingDataError(MLTrainingError):
    code = "ml_degenerate_training_data"
    default_message = "Os dados de treinamento são degenerados ou singulares."


class MLTrainingArtifactError(MLTrainingError):
    code = "ml_training_artifact_error"
    default_message = "O artefato de treinamento não atende ao contrato."


class MLTrainingAlgorithm(StrEnum):
    LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1 = (
        "LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1"
    )


@dataclass(frozen=True, slots=True)
class MLTrainingSpecification(MLContract):
    """Deterministic training specification for the Gate 1 decimal OLS trainer."""

    materialization: MLDatasetMaterialization
    fit_scope: MLFitScope
    algorithm: MLTrainingAlgorithm
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.materialization, MLDatasetMaterialization)
        _nested(self.fit_scope, MLFitScope)
        _require(self.materialization.split == self.fit_scope.split)
        _same(self.materialization.dataset == self.fit_scope.dataset)
        _require(type(self.algorithm) is MLTrainingAlgorithm)
        _require(
            self.algorithm is MLTrainingAlgorithm.LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1
        )
        _nested(self.implementation, MLImplementationReference)
        dataset = self.materialization.dataset
        if dataset.target is None:
            raise MLTrainingInputError("supervised training requires a target")
        if self.fit_scope.role is not TemporalSegmentRole.TRAIN:
            raise MLTrainingInputError("fit scope must authorize TRAIN evidence")

    @property
    def dataset(self) -> MLDatasetSpecification:
        return self.materialization.dataset

    @property
    def split(self) -> object:
        return self.materialization.split

    @property
    def feature_set(self) -> object:
        return self.materialization.dataset.feature_set

    @property
    def target(self) -> object:
        return self.materialization.dataset.target

    @property
    def training_cutoff(self) -> object:
        return self.materialization.split.training_cutoff

    @property
    def checksum(self) -> str:
        from app.ml.documents import logical_payload

        return self._canonical_checksum(logical_payload(self))

    @property
    def identity(self) -> str:
        from app.ml.documents import logical_payload

        return self._canonical_identity(
            f"{self.namespace}-{type(self).__name__}-v1", logical_payload(self)
        )

    def _canonical_checksum(self, payload: object) -> str:
        from app.optimization.canonical import document_checksum

        return document_checksum(payload)

    def _canonical_identity(self, namespace: str, payload: object) -> str:
        from app.optimization.canonical import deterministic_id

        return deterministic_id(namespace, payload)


@dataclass(frozen=True, slots=True)
class MLFittedRegressionModel(MLContract):
    """Transparent deterministic OLS model artifact for the Gate 1 training contract."""

    algorithm: MLTrainingAlgorithm
    implementation: MLImplementationReference
    feature_set: MLFeatureSetSpecification
    target: MLTargetSpecification
    training_input_checksum: str
    intercept: Decimal
    coefficients: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _require(type(self.algorithm) is MLTrainingAlgorithm)
        _require(
            self.algorithm is MLTrainingAlgorithm.LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1
        )
        _nested(self.implementation, MLImplementationReference)
        _nested(self.feature_set, MLFeatureSetSpecification)
        _nested(self.target, MLTargetSpecification)
        _require(
            type(self.training_input_checksum) is str and len(self.training_input_checksum) == 64
        )
        _require(type(self.intercept) is Decimal and self.intercept.is_finite())
        _tuple_decimal_values(self.coefficients)
        _require(len(self.coefficients) == len(self.feature_set.features))


@dataclass(frozen=True, slots=True)
class MLTrainingResult(MLContract):
    """Immutable evidence for a deterministic fit over EXACT TRAIN rows."""

    training_specification: MLTrainingSpecification
    fitted_model: MLFittedRegressionModel
    train_observation_ids: tuple[str, ...]
    training_input_checksum: str
    sample_count: int
    feature_count: int

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.training_specification, MLTrainingSpecification)
        _nested(self.fitted_model, MLFittedRegressionModel)
        _same(self.fitted_model.algorithm == self.training_specification.algorithm)
        _same(self.fitted_model.implementation == self.training_specification.implementation)
        _same(self.fitted_model.feature_set == self.training_specification.feature_set)
        _same(self.fitted_model.target == self.training_specification.target)
        _same(self.fitted_model.training_input_checksum == self.training_input_checksum)
        _tuple(self.train_observation_ids, minimum=1)
        _require(len(self.train_observation_ids) == self.sample_count)
        _require(
            self.feature_count == len(self.training_specification.dataset.feature_set.features)
        )
        _require(
            type(self.training_input_checksum) is str and len(self.training_input_checksum) == 64
        )
        _require(type(self.sample_count) is int and self.sample_count >= 1)
        _require(type(self.feature_count) is int and self.feature_count >= 1)


def train_regression(specification: MLTrainingSpecification) -> MLTrainingResult:
    _nested(specification, MLTrainingSpecification)
    selected_rows = _selected_train_rows(specification)
    if not selected_rows:
        raise MLNoEligibleTrainingRowsError()
    feature_set = specification.dataset.feature_set
    target = specification.dataset.target
    if target is None:
        raise MLTrainingInputError("supervised training requires a target")
    rows = tuple(
        sorted(
            selected_rows,
            key=lambda item: (
                _observation_sort_key(item[0].observation),
                item[0].observation.identity,
            ),
        )
    )
    row_count = len(rows)
    feature_count = len(feature_set.features)
    required_parameters = feature_count + 1
    if row_count < required_parameters:
        raise MLTrainingInputError("insufficient samples for OLS normal-equations fit")
    x_values: list[list[Decimal]] = []
    y_values: list[Decimal] = []
    for vector, label in rows:
        feature_values = _decimal_feature_values(vector, feature_set)
        label_value = _decimal_label_value(label)
        _same(label.observation == vector.observation)
        _same(label.target == target)
        x_values.append([Decimal("1"), *feature_values])
        y_values.append(label_value)
    _require(len(x_values) == row_count)
    _require(len(y_values) == row_count)
    _guard_constant_columns(x_values, feature_count)
    with localcontext() as context:
        context.prec = _DECIMAL_TRAINING_PRECISION
        context.rounding = _DECIMAL_TRAINING_ROUNDING
        normal_matrix = _zeros_matrix(required_parameters, required_parameters)
        normal_vector = _zeros_vector(required_parameters)
        for row_index, row in enumerate(x_values):
            y_value = y_values[row_index]
            for i in range(required_parameters):
                normal_vector[i] += row[i] * y_value
                for j in range(required_parameters):
                    normal_matrix[i][j] += row[i] * row[j]
        theta = _solve_normal_equations(normal_matrix, normal_vector)

    intercept = theta[0]
    coefficients = tuple(theta[1:])
    checksum = _training_input_checksum(specification, rows)
    assert target is not None
    model = MLFittedRegressionModel(
        algorithm=specification.algorithm,
        implementation=specification.implementation,
        feature_set=feature_set,
        target=target,
        training_input_checksum=checksum,
        intercept=intercept,
        coefficients=coefficients,
    )
    result = MLTrainingResult(
        training_specification=specification,
        fitted_model=model,
        train_observation_ids=tuple(row[0].observation.identity for row in rows),
        training_input_checksum=checksum,
        sample_count=row_count,
        feature_count=feature_count,
    )
    return result


def predict_fitted_regression(model: MLFittedRegressionModel, vector: MLFeatureVector) -> Decimal:
    _nested(model, MLFittedRegressionModel)
    _nested(vector, MLFeatureVector)
    _same(vector.feature_set == model.feature_set)
    feature_count = len(model.coefficients)
    _require(len(vector.values) == feature_count)
    feature_values = _decimal_feature_values(vector, model.feature_set)
    with localcontext() as context:
        context.prec = _DECIMAL_TRAINING_PRECISION
        context.rounding = _DECIMAL_TRAINING_ROUNDING
        total = model.intercept
        for value, coefficient in zip(
            feature_values,
            model.coefficients,
            strict=True,
        ):
            total += value * coefficient
        return total


def _selected_train_rows(
    specification: MLTrainingSpecification,
) -> list[tuple[MLFeatureVector, MLLabel]]:
    rows: list[tuple[MLFeatureVector, MLLabel]] = []
    seen: set[str] = set()
    for materialized_row in specification.materialization.rows:
        _nested(materialized_row, MLMaterializationRow)
        if materialized_row.role is not TemporalSegmentRole.TRAIN:
            continue
        if materialized_row.label is None:
            raise MLTrainingInputError("supervised training requires a label for TRAIN rows")
        vector = materialized_row.vector
        label = materialized_row.label
        specification.fit_scope.validate_row(vector, label)
        identity = vector.observation.identity
        if identity in seen:
            raise MLTrainingInputError("duplicate observation identity found in training rows")
        seen.add(identity)
        rows.append((vector, label))
    return rows


def _decimal_feature_values(
    vector: MLFeatureVector,
    feature_set: MLFeatureSetSpecification,
) -> tuple[Decimal, ...]:
    if vector.feature_set != feature_set:
        raise MLTrainingInputError("feature set mismatch in training input")
    if len(vector.values) != len(feature_set.features):
        raise MLTrainingInputError("feature count mismatch in training input")

    values: list[Decimal] = []
    for value in vector.values:
        if type(value) is not Decimal:
            raise MLTrainingInputError("training features must be Decimal values only")
        if not value.is_finite():
            raise MLTrainingInputError("non-finite Decimal feature encountered")
        values.append(value)
    return tuple(values)


def _decimal_label_value(label: MLLabel) -> Decimal:
    value = label.value
    if type(value) is not Decimal:
        raise MLTrainingInputError("training target must be a Decimal value")
    if not value.is_finite():
        raise MLTrainingInputError("training target must be finite")
    return value


def _guard_constant_columns(x_values: list[list[Decimal]], feature_count: int) -> None:
    for column_index in range(feature_count):
        values = [row[column_index + 1] for row in x_values]
        if all(value == values[0] for value in values):
            raise MLDegenerateTrainingDataError(
                f"feature column {column_index} is constant across all TRAIN rows"
            )


def _zeros_matrix(rows: int, cols: int) -> list[list[Decimal]]:
    return [[Decimal("0")] * cols for _ in range(rows)]


def _zeros_vector(size: int) -> list[Decimal]:
    return [Decimal("0")] * size


def _solve_normal_equations(matrix: list[list[Decimal]], vector: list[Decimal]) -> list[Decimal]:
    with localcontext() as context:
        context.prec = _DECIMAL_TRAINING_PRECISION
        context.rounding = _DECIMAL_TRAINING_ROUNDING
        size = len(matrix)
        augmented = [matrix[row][:] + [vector[row]] for row in range(size)]
        for column in range(size):
            pivot_candidates = [row for row in range(column, size) if augmented[row][column] != 0]
            if not pivot_candidates:
                raise MLDegenerateTrainingDataError("singular design matrix encountered")
            pivot_row = max(
                pivot_candidates,
                key=lambda row: (abs(augmented[row][column]), -row),
            )
            if pivot_row != column:
                augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]
            pivot = augmented[column][column]
            if pivot == 0:
                raise MLDegenerateTrainingDataError(
                    "zero pivot encountered in Gaussian elimination"
                )
            for cell in range(column, size + 1):
                augmented[column][cell] /= pivot
            for row in range(size):
                if row == column:
                    continue
                factor = augmented[row][column]
                if factor == 0:
                    continue
                for cell in range(column, size + 1):
                    augmented[row][cell] -= factor * augmented[column][cell]
        return [augmented[row][size] for row in range(size)]


def _training_input_checksum(
    specification: MLTrainingSpecification,
    rows: tuple[tuple[MLFeatureVector, MLLabel], ...],
) -> str:
    from hashlib import sha256

    from app.optimization.canonical import canonical_json_bytes, decimal_text

    target = specification.dataset.target
    if target is None:
        raise MLTrainingInputError("supervised training requires a target")

    payload = {
        "algorithm": specification.algorithm.value,
        "implementation": specification.implementation.identity,
        "feature_set": specification.dataset.feature_set.identity,
        "target": target.identity,
        "numeric_policy": {
            "arithmetic": "decimal",
            "precision": _DECIMAL_TRAINING_PRECISION,
            "rounding": str(_DECIMAL_TRAINING_ROUNDING),
            "intercept": True,
            "solver": "normal_equations_gaussian_elimination_partial_pivoting_v1",
        },
        "rows": [
            {
                "observation_id": vector.observation.identity,
                "feature_values": [
                    decimal_text(value)
                    for value in _decimal_feature_values(
                        vector,
                        specification.dataset.feature_set,
                    )
                ],
                "label_value": decimal_text(_decimal_label_value(label)),
            }
            for vector, label in rows
        ],
    }
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _observation_sort_key(observation: MLObservation) -> tuple[object, str]:
    return observation.observation_time, observation.identity


def _tuple(
    value: object,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> tuple[object, ...]:
    if type(value) is not tuple:
        _require(False)
        raise AssertionError("unreachable")

    if maximum is not None:
        _require(minimum <= len(value) <= maximum)
    else:
        _require(len(value) >= minimum)

    return value


def _tuple_decimal_values(value: object) -> None:
    items = _tuple(value, minimum=0)
    if not all(type(item) is Decimal and item.is_finite() for item in items):
        raise MLTrainingInputError("coefficients must be finite Decimal values")
