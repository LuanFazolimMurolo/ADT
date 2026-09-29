"""Deterministic walk-forward ML evaluation tests for Phase 8-15."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.market_data.domain import DataRange
from app.ml.documents import decode_document, encode_document
from app.ml.errors import MLContractError
from app.ml.materialization import MLDatasetMaterializationService
from app.ml.training import MLTrainingAlgorithm
from app.ml.walk_forward import (
    MLWalkForwardEvaluationEvidence,
    MLWalkForwardMode,
    MLWalkForwardSpecification,
    evaluate_walk_forward,
    walk_forward_fold_plan,
)
from app.optimization.temporal_domain import TemporalSegmentRole
from tests.test_ml_materialization import (
    HOUR,
    START,
    _candles,
    _dataset,
)


def _specification(
    *,
    mode: MLWalkForwardMode = MLWalkForwardMode.EXPANDING,
    train_candles: int = 4,
    validation_candles: int = 2,
    test_candles: int = 1,
    step_candles: int = 1,
    fold_count: int = 2,
    allow_retrospective_context: bool = True,
) -> MLWalkForwardSpecification:
    dataset = _dataset()

    return MLWalkForwardSpecification(
        dataset=dataset,
        mode=mode,
        train_candles=train_candles,
        validation_candles=validation_candles,
        test_candles=test_candles,
        step_candles=step_candles,
        fold_count=fold_count,
        allow_retrospective_context=allow_retrospective_context,
        algorithm=(MLTrainingAlgorithm.LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1),
        implementation=dataset.provenance.implementation,
    )


def _service() -> MLDatasetMaterializationService:
    dataset = _dataset()
    source = dataset.provenance.source

    class Reader:
        def __init__(self) -> None:
            self.candles = _candles()

        def open_snapshot(self, snapshot_id: str):
            assert snapshot_id == source.snapshot_id

            return type(
                "Snapshot",
                (),
                {
                    "snapshot_id": source.snapshot_id,
                    "checksum": source.snapshot_checksum,
                },
            )()

        def verify_unchanged(self) -> None:
            return None

        def iter_candles(self, data_range=None):
            if data_range is None:
                return self.candles

            return tuple(
                candle
                for candle in self.candles
                if data_range.start <= candle.open_time < data_range.end
            )

    return MLDatasetMaterializationService(reader_factory=lambda *_: Reader())


def test_expanding_fold_plan_keeps_original_train_start() -> None:
    specification = _specification(mode=MLWalkForwardMode.EXPANDING)

    first_dataset, first = walk_forward_fold_plan(
        specification,
        0,
    )
    second_dataset, second = walk_forward_fold_plan(
        specification,
        1,
    )

    assert first.train == DataRange(
        START + HOUR,
        START + HOUR * 5,
    )
    assert second.train == DataRange(
        START + HOUR,
        START + HOUR * 6,
    )

    assert first.validation == DataRange(
        START + HOUR * 5,
        START + HOUR * 7,
    )
    assert second.validation == DataRange(
        START + HOUR * 6,
        START + HOUR * 8,
    )

    assert first.test == DataRange(
        START + HOUR * 7,
        START + HOUR * 8,
    )
    assert second.test == DataRange(
        START + HOUR * 8,
        START + HOUR * 9,
    )

    assert first_dataset.provenance.selected_range == DataRange(
        START + HOUR,
        START + HOUR * 8,
    )
    assert second_dataset.provenance.selected_range == DataRange(
        START + HOUR,
        START + HOUR * 9,
    )


def test_rolling_fold_plan_moves_train_start() -> None:
    specification = _specification(mode=MLWalkForwardMode.ROLLING)

    first_dataset, first = walk_forward_fold_plan(
        specification,
        0,
    )
    second_dataset, second = walk_forward_fold_plan(
        specification,
        1,
    )

    assert first.train == DataRange(
        START + HOUR,
        START + HOUR * 5,
    )
    assert second.train == DataRange(
        START + HOUR * 2,
        START + HOUR * 6,
    )

    assert first_dataset.provenance.selected_range == DataRange(
        START + HOUR,
        START + HOUR * 8,
    )
    assert second_dataset.provenance.selected_range == DataRange(
        START + HOUR * 2,
        START + HOUR * 9,
    )


def test_expanding_training_window_grows_by_step() -> None:
    specification = _specification(mode=MLWalkForwardMode.EXPANDING)

    _, first = walk_forward_fold_plan(
        specification,
        0,
    )
    _, second = walk_forward_fold_plan(
        specification,
        1,
    )

    assert first.train.end - first.train.start == HOUR * 4
    assert second.train.end - second.train.start == HOUR * 5


def test_rolling_training_window_keeps_constant_size() -> None:
    specification = _specification(mode=MLWalkForwardMode.ROLLING)

    _, first = walk_forward_fold_plan(
        specification,
        0,
    )
    _, second = walk_forward_fold_plan(
        specification,
        1,
    )

    assert first.train.end - first.train.start == HOUR * 4
    assert second.train.end - second.train.start == HOUR * 4


def test_fold_training_cutoff_is_exact_train_end() -> None:
    specification = _specification()

    for index in range(specification.fold_count):
        _, split = walk_forward_fold_plan(
            specification,
            index,
        )

        assert split.training_cutoff == split.train.end


def test_fold_preserves_explicit_test_segment() -> None:
    specification = _specification()

    for index in range(specification.fold_count):
        _, split = walk_forward_fold_plan(
            specification,
            index,
        )

        assert split.test.end - split.test.start == HOUR


def test_fold_dataset_reuses_exact_snapshot_lineage() -> None:
    specification = _specification()

    fold_dataset, _ = walk_forward_fold_plan(
        specification,
        1,
    )

    assert fold_dataset.provenance.source == specification.dataset.provenance.source
    assert fold_dataset.provenance.context_range == specification.dataset.provenance.context_range
    assert fold_dataset.feature_set == specification.dataset.feature_set
    assert fold_dataset.target == specification.dataset.target


def test_fold_outside_declared_count_is_rejected() -> None:
    specification = _specification()

    with pytest.raises(MLContractError):
        walk_forward_fold_plan(
            specification,
            specification.fold_count,
        )


def test_negative_fold_index_is_rejected() -> None:
    with pytest.raises(MLContractError):
        walk_forward_fold_plan(
            _specification(),
            -1,
        )


def test_boolean_fold_index_is_rejected() -> None:
    with pytest.raises(MLContractError):
        walk_forward_fold_plan(
            _specification(),
            True,
        )


def test_fold_plan_cannot_exceed_selected_dataset_range() -> None:
    with pytest.raises(MLContractError):
        _specification(
            fold_count=3,
        )


def test_invalid_zero_window_is_rejected() -> None:
    with pytest.raises(MLContractError):
        _specification(
            train_candles=0,
        )


def test_unsupervised_dataset_is_rejected() -> None:
    original = _dataset()
    unsupervised = replace(
        original,
        target=None,
    )

    with pytest.raises(MLContractError):
        MLWalkForwardSpecification(
            dataset=unsupervised,
            mode=MLWalkForwardMode.EXPANDING,
            train_candles=4,
            validation_candles=2,
            test_candles=1,
            step_candles=1,
            fold_count=1,
            allow_retrospective_context=True,
            algorithm=(MLTrainingAlgorithm.LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1),
            implementation=unsupervised.provenance.implementation,
        )


def test_implementation_must_match_dataset_provenance() -> None:
    original = _dataset()

    with pytest.raises(MLContractError):
        MLWalkForwardSpecification(
            dataset=original,
            mode=MLWalkForwardMode.EXPANDING,
            train_candles=4,
            validation_candles=2,
            test_candles=1,
            step_candles=1,
            fold_count=1,
            allow_retrospective_context=True,
            algorithm=(MLTrainingAlgorithm.LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1),
            implementation=replace(
                original.provenance.implementation,
                version="different",
            ),
        )


def test_walk_forward_evaluates_exact_declared_fold_count() -> None:
    specification = _specification()

    evidence = evaluate_walk_forward(
        specification,
        _service(),
    )

    assert isinstance(
        evidence,
        MLWalkForwardEvaluationEvidence,
    )
    assert len(evidence.folds) == 2
    assert tuple(fold.index for fold in evidence.folds) == (0, 1)


def test_walk_forward_training_uses_only_train_rows() -> None:
    evidence = evaluate_walk_forward(
        _specification(),
        _service(),
    )

    for fold in evidence.folds:
        split = fold.materialization.split

        materialized_train_ids = {
            row.vector.observation.identity
            for row in fold.materialization.rows
            if row.role is TemporalSegmentRole.TRAIN
        }

        assert set(fold.training_result.train_observation_ids) == materialized_train_ids

        assert all(
            split.role_for(row.vector.observation) is TemporalSegmentRole.TRAIN
            for row in fold.materialization.rows
            if row.vector.observation.identity in fold.training_result.train_observation_ids
        )


def test_walk_forward_scores_validation_only() -> None:
    evidence = evaluate_walk_forward(
        _specification(),
        _service(),
    )

    for fold in evidence.folds:
        assert fold.evaluation.specification.role is TemporalSegmentRole.VALIDATION

        validation_ids = tuple(
            row.vector.observation.identity
            for row in fold.materialization.rows
            if row.role is TemporalSegmentRole.VALIDATION
        )

        assert fold.evaluation.first_observation_id == validation_ids[0]
        assert fold.evaluation.last_observation_id == validation_ids[-1]
        assert fold.evaluation.sample_count == len(validation_ids)


def test_test_rows_never_enter_training_or_evaluation() -> None:
    evidence = evaluate_walk_forward(
        _specification(),
        _service(),
    )

    for fold in evidence.folds:
        test_ids = {
            row.vector.observation.identity
            for row in fold.materialization.rows
            if row.role is TemporalSegmentRole.TEST
        }

        assert test_ids.isdisjoint(fold.training_result.train_observation_ids)

        assert fold.evaluation.first_observation_id not in test_ids
        assert fold.evaluation.last_observation_id not in test_ids


def test_every_fold_uses_its_exact_materialization_and_split() -> None:
    specification = _specification()

    evidence = evaluate_walk_forward(
        specification,
        _service(),
    )

    for index, fold in enumerate(evidence.folds):
        expected_dataset, expected_split = walk_forward_fold_plan(
            specification,
            index,
        )

        assert fold.materialization.dataset == expected_dataset
        assert fold.materialization.split == expected_split
        assert fold.training_result.training_specification.materialization == fold.materialization
        assert fold.evaluation.specification.materialization == fold.materialization


def test_walk_forward_is_deterministic() -> None:
    specification = _specification()

    first = evaluate_walk_forward(
        specification,
        _service(),
    )
    second = evaluate_walk_forward(
        specification,
        _service(),
    )

    assert first == second
    assert first.identity == second.identity
    assert first.checksum == second.checksum

    assert tuple(fold.training_result.training_input_checksum for fold in first.folds) == tuple(
        fold.training_result.training_input_checksum for fold in second.folds
    )

    assert tuple(fold.evaluation.evaluation_input_checksum for fold in first.folds) == tuple(
        fold.evaluation.evaluation_input_checksum for fold in second.folds
    )


def test_expanding_and_rolling_have_different_identity() -> None:
    expanding = _specification(mode=MLWalkForwardMode.EXPANDING)
    rolling = _specification(mode=MLWalkForwardMode.ROLLING)

    assert expanding.identity != rolling.identity
    assert expanding.checksum != rolling.checksum


def test_walk_forward_specification_round_trips_codec() -> None:
    specification = _specification()

    rebuilt = decode_document(encode_document(specification))

    assert isinstance(
        rebuilt,
        MLWalkForwardSpecification,
    )
    assert rebuilt == specification
    assert rebuilt.identity == specification.identity
    assert rebuilt.checksum == specification.checksum


def test_walk_forward_evidence_round_trips_codec() -> None:
    evidence = evaluate_walk_forward(
        _specification(),
        _service(),
    )

    rebuilt = decode_document(encode_document(evidence))

    assert isinstance(
        rebuilt,
        MLWalkForwardEvaluationEvidence,
    )
    assert rebuilt == evidence
    assert rebuilt.identity == evidence.identity
    assert rebuilt.checksum == evidence.checksum


def test_walk_forward_has_no_cross_fold_aggregate_metrics() -> None:
    evidence = evaluate_walk_forward(
        _specification(),
        _service(),
    )

    assert not hasattr(evidence, "metrics")
    assert not hasattr(evidence, "mae")
    assert not hasattr(evidence, "mse")
    assert not hasattr(evidence, "rmse")
    assert not hasattr(evidence, "score")


def test_walk_forward_module_has_no_execution_or_selection_authority() -> None:
    import app.ml.walk_forward as walk_forward

    forbidden = (
        "champion",
        "challenger",
        "winner",
        "rank",
        "ranking",
        "recommend",
        "approve",
        "activate",
        "deploy",
        "execute",
        "order",
        "paper",
        "live",
        "repository",
        "database",
        "fastapi",
        "router",
        "endpoint",
    )

    exported = tuple(name.lower() for name in vars(walk_forward))

    for token in forbidden:
        assert not any(token in name for name in exported)
