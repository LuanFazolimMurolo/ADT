"""Deterministic offline ML evaluation foundation tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext

import pytest

from app.ml.documents import decode_document, encode_document
from app.ml.domain import MLDatasetMaterialization
from app.ml.evaluation import (
    MLEvaluationInputError,
    MLEvaluationSpecification,
    MLNoEligibleEvaluationRowsError,
    MLRegressionEvaluationEvidence,
    evaluate_regression,
)
from app.ml.training import train_regression
from app.optimization.temporal_domain import TemporalSegmentRole
from tests.test_ml_dataset_contracts import implementation
from tests.test_ml_training import (
    _materialization,
    _training_specification,
)


def _evaluation_specification(
    materialization: MLDatasetMaterialization,
    training_result,
    role: TemporalSegmentRole = TemporalSegmentRole.VALIDATION,
) -> MLEvaluationSpecification:
    return MLEvaluationSpecification(
        training_result=training_result,
        materialization=materialization,
        role=role,
        implementation=implementation(),
    )


def _trained():
    _, part, materialization = _materialization()
    training_result = train_regression(
        _training_specification(
            materialization,
            part,
        )
    )
    return part, materialization, training_result


def _replace_role_label(
    materialization: MLDatasetMaterialization,
    role: TemporalSegmentRole,
    value: str,
) -> MLDatasetMaterialization:
    rows = []

    for row in materialization.rows:
        if row.role is role:
            assert row.label is not None
            rows.append(
                replace(
                    row,
                    label=replace(
                        row.label,
                        value=Decimal(value),
                    ),
                )
            )
        else:
            rows.append(row)

    return replace(
        materialization,
        rows=tuple(rows),
    )


def test_evaluation_specification_is_immutable() -> None:
    _, materialization, training_result = _trained()

    specification = _evaluation_specification(
        materialization,
        training_result,
    )

    with pytest.raises(FrozenInstanceError):
        specification.role = TemporalSegmentRole.TEST  # type: ignore[misc]


def test_evaluation_rejects_train_role() -> None:
    _, materialization, training_result = _trained()

    with pytest.raises(MLEvaluationInputError):
        _evaluation_specification(
            materialization,
            training_result,
            TemporalSegmentRole.TRAIN,
        )


def test_validation_evaluation_exact_zero_error() -> None:
    _, materialization, training_result = _trained()

    evidence = evaluate_regression(
        _evaluation_specification(
            materialization,
            training_result,
            TemporalSegmentRole.VALIDATION,
        )
    )

    assert evidence.sample_count == 1
    assert evidence.metrics.mae == Decimal("0")
    assert evidence.metrics.mse == Decimal("0")
    assert evidence.metrics.rmse == Decimal("0")


def test_validation_evaluation_exact_nonzero_error() -> None:
    _, materialization, training_result = _trained()

    changed = _replace_role_label(
        materialization,
        TemporalSegmentRole.VALIDATION,
        "200",
    )

    evidence = evaluate_regression(
        _evaluation_specification(
            changed,
            training_result,
            TemporalSegmentRole.VALIDATION,
        )
    )

    assert evidence.sample_count == 1
    assert evidence.metrics.mae == Decimal("1")
    assert evidence.metrics.mse == Decimal("1")
    assert evidence.metrics.rmse == Decimal("1")


def test_test_role_is_evaluated_independently() -> None:
    _, materialization, training_result = _trained()

    validation = evaluate_regression(
        _evaluation_specification(
            materialization,
            training_result,
            TemporalSegmentRole.VALIDATION,
        )
    )

    test = evaluate_regression(
        _evaluation_specification(
            materialization,
            training_result,
            TemporalSegmentRole.TEST,
        )
    )

    assert validation.sample_count == 1
    assert test.sample_count == 1
    assert validation.first_observation_id != test.first_observation_id
    assert validation.evaluation_input_checksum != test.evaluation_input_checksum


def test_evaluation_does_not_mutate_or_retrain_model() -> None:
    _, materialization, training_result = _trained()

    checksum_before = training_result.fitted_model.checksum
    coefficients_before = training_result.fitted_model.coefficients

    evaluate_regression(
        _evaluation_specification(
            materialization,
            training_result,
        )
    )

    assert training_result.fitted_model.checksum == checksum_before
    assert training_result.fitted_model.coefficients == coefficients_before


def test_evaluation_checksum_changes_with_role_input() -> None:
    _, materialization, training_result = _trained()

    original = evaluate_regression(
        _evaluation_specification(
            materialization,
            training_result,
        )
    )

    changed_materialization = _replace_role_label(
        materialization,
        TemporalSegmentRole.VALIDATION,
        "200",
    )

    changed = evaluate_regression(
        _evaluation_specification(
            changed_materialization,
            training_result,
        )
    )

    assert original.evaluation_input_checksum != changed.evaluation_input_checksum


def test_evaluation_is_independent_of_external_decimal_context() -> None:
    _, materialization, training_result = _trained()

    specification = _evaluation_specification(
        materialization,
        training_result,
    )

    with localcontext() as context:
        context.prec = 6
        low_precision = evaluate_regression(specification)

    with localcontext() as context:
        context.prec = 80
        high_precision = evaluate_regression(specification)

    assert low_precision.metrics == high_precision.metrics
    assert low_precision.evaluation_input_checksum == high_precision.evaluation_input_checksum


def test_evaluation_evidence_round_trips_canonical_document() -> None:
    _, materialization, training_result = _trained()

    evidence = evaluate_regression(
        _evaluation_specification(
            materialization,
            training_result,
        )
    )

    encoded = encode_document(evidence)
    rebuilt = decode_document(encoded)

    assert isinstance(
        rebuilt,
        MLRegressionEvaluationEvidence,
    )
    assert rebuilt == evidence


def test_validation_evaluation_ignores_test_only_changes() -> None:
    _, materialization, training_result = _trained()

    original = evaluate_regression(
        _evaluation_specification(
            materialization,
            training_result,
            TemporalSegmentRole.VALIDATION,
        )
    )

    test_changed = _replace_role_label(
        materialization,
        TemporalSegmentRole.TEST,
        "999",
    )

    changed = evaluate_regression(
        _evaluation_specification(
            test_changed,
            training_result,
            TemporalSegmentRole.VALIDATION,
        )
    )

    assert original.sample_count == changed.sample_count
    assert original.metrics == changed.metrics
    assert original.evaluation_input_checksum == changed.evaluation_input_checksum


def test_no_eligible_role_rows_are_rejected() -> None:
    _, materialization, training_result = _trained()

    train_only = replace(
        materialization,
        rows=tuple(row for row in materialization.rows if row.role is TemporalSegmentRole.TRAIN),
    )

    with pytest.raises(MLNoEligibleEvaluationRowsError):
        evaluate_regression(
            _evaluation_specification(
                train_only,
                training_result,
                TemporalSegmentRole.TEST,
            )
        )
