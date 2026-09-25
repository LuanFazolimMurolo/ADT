"""Deterministic Decimal OLS training foundation tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext

import pytest

from app.indicators.domain import IndicatorDescriptor
from app.market_data.domain import DataRange
from app.ml.documents import decode_document, encode_document
from app.ml.domain import (
    MLDatasetMaterialization,
    MLFeatureSetSpecification,
    MLMaterializationRow,
    MLTemporalSplit,
)
from app.ml.errors import MLContractError
from app.ml.temporal_safety import MLFitScope
from app.ml.training import (
    MLDegenerateTrainingDataError,
    MLFittedRegressionModel,
    MLTrainingAlgorithm,
    MLTrainingInputError,
    MLTrainingSpecification,
    predict_fitted_regression,
    train_regression,
)
from app.optimization.temporal_domain import TemporalSegmentRole
from tests.test_ml_dataset_contracts import (
    dataset,
    implementation,
    instant,
    label,
    observation,
    vector,
)

ALGORITHM = MLTrainingAlgorithm.LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1


def _training_split(spec) -> MLTemporalSplit:
    return MLTemporalSplit(
        spec,
        DataRange(instant(1), instant(7)),
        DataRange(instant(7), instant(9)),
        DataRange(instant(9), instant(11)),
        instant(7),
        True,
    )


def _row(spec, part, *, hour: int, x: str, y: str) -> MLMaterializationRow:
    obs = observation(spec, hour=hour)
    feature_vector = replace(vector(obs), values=(Decimal(x),))
    target_label = replace(label(obs), value=Decimal(y))
    return MLMaterializationRow(
        feature_vector,
        target_label,
        part.role_for(obs),
    )


def _materialization(
    *,
    validation_x: str = "100",
    validation_y: str = "201",
    test_x: str = "200",
    test_y: str = "401",
    source_order: tuple[str, ...] = (
        "train1",
        "train2",
        "train3",
        "validation",
        "test",
    ),
):
    spec = dataset()
    part = _training_split(spec)

    rows_by_name = {
        "train1": _row(spec, part, hour=2, x="-1", y="-1"),
        "train2": _row(spec, part, hour=3, x="0", y="1"),
        "train3": _row(spec, part, hour=4, x="1", y="3"),
        "validation": _row(
            spec,
            part,
            hour=7,
            x=validation_x,
            y=validation_y,
        ),
        "test": _row(
            spec,
            part,
            hour=9,
            x=test_x,
            y=test_y,
        ),
    }

    materialization = MLDatasetMaterialization(
        spec,
        part,
        tuple(rows_by_name[name] for name in source_order),
        (),
        implementation(),
        built_at=None,
    )
    return spec, part, materialization


def _training_specification(materialization, part) -> MLTrainingSpecification:
    return MLTrainingSpecification(
        materialization=materialization,
        fit_scope=MLFitScope(part),
        algorithm=ALGORITHM,
        implementation=implementation(),
    )


def test_training_spec_requires_supervised_dataset() -> None:
    spec = dataset(supervised=False)
    part = _training_split(spec)
    obs = observation(spec, hour=2)

    materialization = MLDatasetMaterialization(
        spec,
        part,
        (
            MLMaterializationRow(
                vector(obs),
                None,
                part.role_for(obs),
            ),
        ),
        (),
        implementation(),
        built_at=None,
    )

    with pytest.raises(MLTrainingInputError):
        _training_specification(materialization, part)


def test_training_specification_is_immutable() -> None:
    _, part, materialization = _materialization()
    specification = _training_specification(materialization, part)

    with pytest.raises(FrozenInstanceError):
        specification.algorithm = ALGORITHM


def test_training_result_exact_one_feature_fit() -> None:
    spec, part, materialization = _materialization()
    result = train_regression(_training_specification(materialization, part))

    assert result.fitted_model.intercept == Decimal("1")
    assert result.fitted_model.coefficients == (Decimal("2"),)
    assert result.sample_count == 3
    assert result.feature_count == 1
    assert len(result.fitted_model.coefficients) == len(spec.feature_set.features)

    first_train_vector = next(
        row.vector for row in materialization.rows if row.role is TemporalSegmentRole.TRAIN
    )
    assert predict_fitted_regression(result.fitted_model, first_train_vector) == Decimal("-1")


def test_training_uses_only_train_rows() -> None:
    _, part, materialization = _materialization()
    result = train_regression(_training_specification(materialization, part))

    expected_train_ids = tuple(
        row.observation_id for row in materialization.rows if row.role is TemporalSegmentRole.TRAIN
    )
    validation_test_ids = {
        row.observation_id
        for row in materialization.rows
        if row.role is not TemporalSegmentRole.TRAIN
    }

    assert result.train_observation_ids == expected_train_ids
    assert result.sample_count == 3
    assert not validation_test_ids.intersection(result.train_observation_ids)


def test_validation_and_test_values_do_not_change_fitted_model() -> None:
    _, first_part, first_materialization = _materialization(
        validation_x="100",
        validation_y="201",
        test_x="200",
        test_y="401",
    )
    _, second_part, second_materialization = _materialization(
        validation_x="-999",
        validation_y="12345",
        test_x="777",
        test_y="-333",
    )

    first = train_regression(_training_specification(first_materialization, first_part))
    second = train_regression(_training_specification(second_materialization, second_part))

    assert first.training_input_checksum == second.training_input_checksum
    assert first.fitted_model.intercept == second.fitted_model.intercept
    assert first.fitted_model.coefficients == second.fitted_model.coefficients
    assert first.fitted_model.checksum == second.fitted_model.checksum


def test_source_row_order_does_not_change_fit() -> None:
    _, first_part, first_materialization = _materialization()
    _, second_part, second_materialization = _materialization(
        source_order=("test", "train3", "validation", "train1", "train2")
    )

    first = train_regression(_training_specification(first_materialization, first_part))
    second = train_regression(_training_specification(second_materialization, second_part))

    assert first.train_observation_ids == second.train_observation_ids
    assert first.training_input_checksum == second.training_input_checksum
    assert first.fitted_model.checksum == second.fitted_model.checksum


def test_non_decimal_feature_values_cannot_enter_canonical_training_contract() -> None:
    spec = dataset()
    obs = observation(spec, hour=2)

    with pytest.raises(MLContractError):
        replace(vector(obs), values=(1,))


def test_training_rejects_constant_train_feature() -> None:
    spec = dataset()
    part = _training_split(spec)

    rows = tuple(
        _row(spec, part, hour=hour, x="1", y=y) for hour, y in ((2, "3"), (3, "5"), (4, "7"))
    )

    materialization = MLDatasetMaterialization(
        spec,
        part,
        rows,
        (),
        implementation(),
        built_at=None,
    )

    with pytest.raises(MLDegenerateTrainingDataError):
        train_regression(_training_specification(materialization, part))


def test_fit_scope_authorizes_only_train_rows() -> None:
    spec = dataset()
    part = _training_split(spec)
    scope = MLFitScope(part)
    obs = observation(spec, hour=7)

    assert part.role_for(obs) is TemporalSegmentRole.VALIDATION

    with pytest.raises(MLContractError):
        scope.validate_row(vector(obs), label(obs))


def test_fitted_model_round_trips_through_canonical_document() -> None:
    _, part, materialization = _materialization()
    result = train_regression(_training_specification(materialization, part))

    encoded = encode_document(result.fitted_model)
    rebuilt = decode_document(encoded)

    assert isinstance(rebuilt, MLFittedRegressionModel)
    assert rebuilt == result.fitted_model
    assert rebuilt.checksum == result.fitted_model.checksum


def test_training_result_round_trips_through_canonical_document() -> None:
    _, part, materialization = _materialization()
    result = train_regression(_training_specification(materialization, part))

    encoded = encode_document(result)
    rebuilt = decode_document(encoded)

    assert rebuilt == result
    assert rebuilt.training_input_checksum == result.training_input_checksum


def _two_feature_dataset():
    spec = dataset()
    first = spec.feature_set.features[0]
    second = replace(
        first,
        name="close_ema_secondary",
        algorithm=IndicatorDescriptor("ema", "1", (("period", 4),)),
        lookback_candles=4,
    )
    feature_set = MLFeatureSetSpecification(
        spec.feature_set.timeframe,
        (first, second),
        spec.feature_set.implementation,
    )
    return replace(spec, feature_set=feature_set)


def _two_feature_row(
    spec,
    part,
    *,
    hour: int,
    x1: str,
    x2: str,
    y: str,
) -> MLMaterializationRow:
    obs = observation(spec, hour=hour)
    original = vector(obs)
    feature_vector = replace(
        original,
        values=(Decimal(x1), Decimal(x2)),
    )
    target_label = replace(label(obs), value=Decimal(y))
    return MLMaterializationRow(
        feature_vector,
        target_label,
        part.role_for(obs),
    )


def test_training_exact_two_feature_full_rank_fit() -> None:
    spec = _two_feature_dataset()
    part = _training_split(spec)

    # y = 1 + 2*x1 - 3*x2
    rows = (
        _two_feature_row(spec, part, hour=2, x1="1", x2="0", y="3"),
        _two_feature_row(spec, part, hour=3, x1="0", x2="1", y="-2"),
        _two_feature_row(spec, part, hour=4, x1="-1", x2="0", y="-1"),
        _two_feature_row(spec, part, hour=5, x1="0", x2="-1", y="4"),
    )

    materialization = MLDatasetMaterialization(
        spec,
        part,
        rows,
        (),
        implementation(),
        built_at=None,
    )

    result = train_regression(_training_specification(materialization, part))

    assert result.sample_count == 4
    assert result.feature_count == 2
    assert result.fitted_model.intercept == Decimal("1")
    assert result.fitted_model.coefficients == (
        Decimal("2"),
        Decimal("-3"),
    )


def test_training_rejects_collinear_feature_matrix() -> None:
    spec = _two_feature_dataset()
    part = _training_split(spec)

    # x2 = 2*x1 creates a singular design matrix, while neither
    # feature column is constant.
    rows = (
        _two_feature_row(spec, part, hour=2, x1="-1", x2="-2", y="-1"),
        _two_feature_row(spec, part, hour=3, x1="0", x2="0", y="1"),
        _two_feature_row(spec, part, hour=4, x1="1", x2="2", y="3"),
        _two_feature_row(spec, part, hour=5, x1="2", x2="4", y="5"),
    )

    materialization = MLDatasetMaterialization(
        spec,
        part,
        rows,
        (),
        implementation(),
        built_at=None,
    )

    with pytest.raises(MLDegenerateTrainingDataError):
        train_regression(_training_specification(materialization, part))


def test_training_rejects_insufficient_samples() -> None:
    spec = dataset()
    part = _training_split(spec)

    # One feature + intercept requires at least two parameters.
    # A single TRAIN sample cannot fit the Gate-1 OLS contract.
    rows = (_row(spec, part, hour=2, x="1", y="3"),)

    materialization = MLDatasetMaterialization(
        spec,
        part,
        rows,
        (),
        implementation(),
        built_at=None,
    )

    with pytest.raises(MLTrainingInputError):
        train_regression(_training_specification(materialization, part))


def test_training_is_independent_of_external_decimal_context() -> None:
    _, part, materialization = _materialization()
    specification = _training_specification(materialization, part)

    with localcontext() as context:
        context.prec = 6
        low_precision = train_regression(specification)

    with localcontext() as context:
        context.prec = 80
        high_precision = train_regression(specification)

    assert low_precision.training_input_checksum == high_precision.training_input_checksum
    assert low_precision.fitted_model == high_precision.fitted_model
    assert low_precision.fitted_model.checksum == high_precision.fitted_model.checksum
