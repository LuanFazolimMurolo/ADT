"""Fit-scope temporal safety contracts for TRAIN-only model fitting."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace

import pytest

from app.ml.documents import decode_document, encode_document, from_document, to_document
from app.ml.domain import MLContract, validate_contract
from app.ml.errors import MLContractError, MLCorruptDocumentError, MLIncompatibleReferenceError
from app.ml.temporal_safety import MLFitScope
from app.optimization.temporal_domain import TemporalSegmentRole
from tests.test_ml_dataset_contracts import (
    dataset,
    implementation,
    instant,
    label,
    observation,
    split,
    vector,
)


@dataclass(frozen=True, slots=True)
class UnsupportedContract(MLContract):
    value: str

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)


def test_fit_scope_construction_and_derived_semantics() -> None:
    current = split()
    scope = MLFitScope(current)
    assert scope.dataset == current.dataset
    assert scope.role is TemporalSegmentRole.TRAIN
    assert scope.coverage == current.train
    assert scope.training_cutoff == current.training_cutoff
    assert scope.allow_retrospective_context is current.allow_retrospective_context
    assert "allow_retrospective_context" not in MLFitScope.__dataclass_fields__
    assert "training_cutoff" not in MLFitScope.__dataclass_fields__
    assert "dataset" not in MLFitScope.__dataclass_fields__
    assert scope.identity == scope.identity
    assert scope.checksum == scope.checksum


def test_fit_scope_is_immutable() -> None:
    scope = MLFitScope(split())
    with pytest.raises(FrozenInstanceError):
        scope.split = split()


def test_fit_scope_identity_changes_with_split_safety_fields() -> None:
    original = MLFitScope(split())
    changed = MLFitScope(replace(split(), training_cutoff=instant(4)))
    assert original.identity != changed.identity
    assert original.checksum != changed.checksum


def test_fit_scope_rejects_invalid_train_cutoff() -> None:
    with pytest.raises(MLContractError):
        MLFitScope(replace(split(), training_cutoff=instant(0)))
    with pytest.raises(MLContractError):
        MLFitScope(replace(split(), training_cutoff=instant(11)))


def test_fit_scope_accepts_legal_supervised_train_row() -> None:
    current = MLFitScope(split())
    obs = observation(hour=2)
    row = vector(obs)
    target = label(obs)
    current.validate_row(row, target)


def test_fit_scope_rejects_validation_role_rows() -> None:
    scope = MLFitScope(split())
    obs = observation(hour=6)
    with pytest.raises(MLContractError):
        scope.validate_row(vector(obs), label(obs))


def test_fit_scope_rejects_test_role_rows() -> None:
    scope = MLFitScope(split())
    obs = observation(hour=9)
    with pytest.raises(MLContractError):
        scope.validate_row(vector(obs), label(obs))


def test_fit_scope_rejects_dataset_mismatch() -> None:
    scope = MLFitScope(split())
    other = replace(
        dataset(),
        feature_set=replace(
            dataset().feature_set,
            implementation=replace(implementation(), version="2"),
        ),
    )
    mismatch = observation(other)
    with pytest.raises(MLIncompatibleReferenceError):
        scope.validate_row(vector(mismatch), label(mismatch))


def test_fit_scope_requires_label_for_supervised_training() -> None:
    scope = MLFitScope(split())
    obs = observation(hour=2)
    with pytest.raises(MLContractError):
        scope.validate_row(vector(obs), None)


def test_fit_scope_rejects_mismatched_label_observation() -> None:
    scope = MLFitScope(split())
    obs = observation(hour=2)
    wrong = observation(hour=3)
    with pytest.raises(MLIncompatibleReferenceError):
        scope.validate_row(vector(obs), label(wrong))


def test_fit_scope_rejects_target_crossing_train_boundary() -> None:
    scope = MLFitScope(split())
    obs = observation(hour=2)
    target = label(obs)
    with pytest.raises(MLContractError):
        scope.validate_row(
            vector(obs),
            replace(
                target,
                future=replace(
                    target.future,
                    data_range=replace(target.future.data_range, end=instant(6)),
                ),
            ),
        )


def test_fit_scope_rejects_target_after_training_cutoff() -> None:
    scope = MLFitScope(split())
    obs = observation(hour=2)
    target = label(obs)
    with pytest.raises(MLContractError):
        scope.validate_row(
            vector(obs),
            replace(
                target,
                future=replace(
                    target.future,
                    data_range=replace(target.future.data_range, end=instant(7)),
                ),
            ),
        )


def test_fit_scope_respects_exact_cutoff_equality() -> None:
    current = split()
    scope = MLFitScope(current)
    obs = observation(hour=2)
    target = label(obs)
    scope.validate_row(vector(obs), target)
    with pytest.raises(MLContractError):
        scope.validate_row(
            vector(obs),
            replace(
                target,
                future=replace(
                    target.future,
                    data_range=replace(target.future.data_range, end=instant(5)),
                ),
            ),
        )


def test_fit_scope_uses_split_retrospective_policy() -> None:
    obs = observation(hour=2)
    legal = MLFitScope(split())
    legal.validate_row(vector(obs), label(obs))
    disallowed = MLFitScope(replace(split(), allow_retrospective_context=False))
    assert disallowed.allow_retrospective_context is False
    assert legal.allow_retrospective_context is True


def test_fit_scope_unsupervised_train_row_keeps_split_temporal_policy() -> None:
    spec = dataset(supervised=False)
    scope = MLFitScope(split(spec))
    obs = observation(spec, hour=2)
    row = vector(obs)
    scope.validate_row(row, None)

    with pytest.raises(MLContractError):
        scope.validate_row(row, label())

    retro = replace(
        row,
        dependencies=(
            replace(
                row.dependencies[0],
                data_range=replace(row.dependencies[0].data_range, start=instant(0)),
            ),
        ),
    )

    with pytest.raises(MLContractError):
        MLFitScope(replace(split(spec), allow_retrospective_context=False)).validate_row(
            retro, None
        )

    MLFitScope(replace(split(spec), allow_retrospective_context=True)).validate_row(retro, None)


def test_fit_scope_rejects_future_feature_dependency() -> None:
    scope = MLFitScope(split())
    obs = observation(hour=2)
    feature = vector(obs)
    dependency = replace(
        feature.dependencies[0],
        data_range=replace(feature.dependencies[0].data_range, end=instant(7)),
    )
    with pytest.raises(MLContractError):
        replace(feature, dependencies=(dependency,))
    assert scope.validate_row(feature, label(obs)) is None


def test_overlapping_train_labels_are_individually_legal_inside_train() -> None:
    scope = MLFitScope(split())
    first = observation(hour=2)
    second = observation(hour=3)
    scope.validate_row(vector(first), label(first))
    scope.validate_row(vector(second), label(second))


def test_fit_scope_round_trips_as_canonical_document() -> None:
    scope = MLFitScope(split())
    encoded = encode_document(scope)
    rebuilt = decode_document(encoded)
    assert rebuilt == scope
    assert from_document(to_document(scope)) == scope


def test_fit_scope_document_rejects_unknown_missing_and_unsupported_contracts() -> None:
    scope = MLFitScope(split())
    document = to_document(scope)
    document["payload"]["fields"]["verified"] = True
    with pytest.raises(MLCorruptDocumentError):
        decode_document(document)
    del document["payload"]["fields"]["split"]
    with pytest.raises(MLCorruptDocumentError):
        decode_document(document)
    with pytest.raises(MLContractError):
        validate_contract(UnsupportedContract("bad"))


def test_fit_scope_has_no_manual_purge_or_embargo_concepts() -> None:
    scope = MLFitScope(split())
    assert not hasattr(scope, "purge_width")
    assert not hasattr(scope, "purge_candles")
    assert not hasattr(scope, "PURGED")
    assert not hasattr(scope, "embargo")
