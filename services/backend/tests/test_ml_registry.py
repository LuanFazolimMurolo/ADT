"""Deterministic ML registry, versioning and lineage tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from app.ml.documents import decode_document, encode_document
from app.ml.errors import MLIncompatibleReferenceError
from app.ml.evaluation import (
    MLEvaluationSpecification,
    evaluate_regression,
)
from app.ml.registry import (
    MLEvaluationEvidenceReference,
    MLModelRegistryRecord,
    MLRegistryInputError,
    build_model_registry_record,
)
from app.ml.training import train_regression
from app.optimization.temporal_domain import TemporalSegmentRole
from tests.test_ml_dataset_contracts import implementation
from tests.test_ml_evaluation import _replace_role_label
from tests.test_ml_training import (
    _materialization,
    _training_specification,
)


def _trained():
    _, split, materialization = _materialization()
    result = train_regression(
        _training_specification(
            materialization,
            split,
        )
    )
    return split, materialization, result


def _evaluation(
    materialization,
    training_result,
    role: TemporalSegmentRole,
):
    return evaluate_regression(
        MLEvaluationSpecification(
            training_result=training_result,
            materialization=materialization,
            role=role,
            implementation=implementation(),
        )
    )


def _record(
    training_result,
    *,
    evidence=(),
) -> MLModelRegistryRecord:
    return build_model_registry_record(
        training_result,
        evaluation_evidence=evidence,
        registry_implementation=implementation(),
    )


def test_registry_record_is_immutable() -> None:
    _, _, training_result = _trained()
    record = _record(training_result)

    with pytest.raises(FrozenInstanceError):
        record.evaluation_evidence = ()  # type: ignore[misc]


def test_same_training_result_builds_identical_registry_record() -> None:
    _, _, training_result = _trained()

    first = _record(training_result)
    second = _record(training_result)

    assert first == second
    assert first.checksum == second.checksum
    assert first.identity == second.identity
    assert first.model_reference == second.model_reference


def test_registry_record_binds_exact_training_lineage() -> None:
    _, _, training_result = _trained()
    record = _record(training_result)

    assert record.lineage.training_result_checksum == training_result.checksum
    assert (
        record.lineage.training_specification_checksum
        == training_result.training_specification.checksum
    )
    assert record.lineage.training_input_checksum == training_result.training_input_checksum
    assert record.lineage.fitted_model_checksum == training_result.fitted_model.checksum
    assert record.model_reference.artifact_checksum == training_result.fitted_model.checksum


def test_registry_record_rejects_forged_model_id() -> None:
    _, _, training_result = _trained()
    record = _record(training_result)

    forged_reference = replace(
        record.model_reference,
        model_id="f" * 64,
    )

    with pytest.raises(MLIncompatibleReferenceError):
        replace(
            record,
            model_reference=forged_reference,
        )


def test_registry_record_rejects_forged_model_version() -> None:
    _, _, training_result = _trained()
    record = _record(training_result)

    forged_reference = replace(
        record.model_reference,
        model_version="e" * 64,
    )

    with pytest.raises(MLIncompatibleReferenceError):
        replace(
            record,
            model_reference=forged_reference,
        )


def test_registry_can_exist_without_evaluation_evidence() -> None:
    _, _, training_result = _trained()
    record = _record(training_result)

    assert record.evaluation_evidence == ()


def test_evaluation_evidence_is_canonically_ordered() -> None:
    _, materialization, training_result = _trained()

    validation = _evaluation(
        materialization,
        training_result,
        TemporalSegmentRole.VALIDATION,
    )
    test = _evaluation(
        materialization,
        training_result,
        TemporalSegmentRole.TEST,
    )

    record = _record(
        training_result,
        evidence=(test, validation),
    )

    assert tuple(reference.role for reference in record.evaluation_evidence) == (
        TemporalSegmentRole.VALIDATION,
        TemporalSegmentRole.TEST,
    )


def test_duplicate_evaluation_role_is_rejected() -> None:
    _, materialization, training_result = _trained()

    validation = _evaluation(
        materialization,
        training_result,
        TemporalSegmentRole.VALIDATION,
    )

    with pytest.raises(MLRegistryInputError):
        _record(
            training_result,
            evidence=(validation, validation),
        )


def test_foreign_evaluation_evidence_is_rejected() -> None:
    _, materialization, training_result = _trained()

    changed = _replace_role_label(
        materialization,
        TemporalSegmentRole.TRAIN,
        "500",
    )

    second_training_result = train_regression(
        _training_specification(
            changed,
            changed.split,
        )
    )

    foreign_validation = _evaluation(
        changed,
        second_training_result,
        TemporalSegmentRole.VALIDATION,
    )

    with pytest.raises(MLIncompatibleReferenceError):
        _record(
            training_result,
            evidence=(foreign_validation,),
        )


def test_evaluation_changes_do_not_change_model_reference() -> None:
    _, materialization, training_result = _trained()

    without_evidence = _record(training_result)

    validation = _evaluation(
        materialization,
        training_result,
        TemporalSegmentRole.VALIDATION,
    )

    with_evidence = _record(
        training_result,
        evidence=(validation,),
    )

    assert without_evidence.model_reference == with_evidence.model_reference
    assert without_evidence.model_id == with_evidence.model_id
    assert without_evidence.version == with_evidence.version
    assert without_evidence.checksum != with_evidence.checksum


def test_same_model_family_gets_new_version_for_new_fit() -> None:
    _, materialization, first_training_result = _trained()

    rows = []
    changed_train_label = False

    for row in materialization.rows:
        if row.role is TemporalSegmentRole.TRAIN and not changed_train_label:
            assert row.label is not None
            rows.append(
                replace(
                    row,
                    label=replace(
                        row.label,
                        value=row.label.value + Decimal("1"),
                    ),
                )
            )
            changed_train_label = True
        else:
            rows.append(row)

    changed = replace(
        materialization,
        rows=tuple(rows),
    )

    second_training_result = train_regression(
        _training_specification(
            changed,
            changed.split,
        )
    )

    first = _record(first_training_result)
    second = _record(second_training_result)

    assert first.model_id == second.model_id
    assert first.version != second.version
    assert first.model_reference.artifact_checksum != second.model_reference.artifact_checksum


def test_registry_record_round_trips_canonical_document() -> None:
    _, materialization, training_result = _trained()

    validation = _evaluation(
        materialization,
        training_result,
        TemporalSegmentRole.VALIDATION,
    )
    test = _evaluation(
        materialization,
        training_result,
        TemporalSegmentRole.TEST,
    )

    record = _record(
        training_result,
        evidence=(validation, test),
    )

    encoded = encode_document(record)
    rebuilt = decode_document(encoded)

    assert isinstance(rebuilt, MLModelRegistryRecord)
    assert rebuilt == record
    assert rebuilt.checksum == record.checksum


def test_evaluation_reference_rejects_train_role() -> None:
    with pytest.raises(MLRegistryInputError):
        MLEvaluationEvidenceReference(
            role=TemporalSegmentRole.TRAIN,
            evidence_checksum="a" * 64,
            evaluation_input_checksum="b" * 64,
            sample_count=1,
        )
