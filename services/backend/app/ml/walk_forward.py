"""Deterministic walk-forward ML evaluation for Phase 8-15."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from app.market_data.domain import DataRange
from app.market_data.timeframes import get_timeframe
from app.ml.domain import (
    MLContract,
    MLDatasetMaterialization,
    MLDatasetSpecification,
    MLImplementationReference,
    MLTemporalSplit,
    _count,
    _nested,
    _require,
    _same,
)
from app.ml.evaluation import (
    MLEvaluationSpecification,
    MLRegressionEvaluationEvidence,
    evaluate_regression,
)
from app.ml.materialization import MLDatasetMaterializationService
from app.ml.temporal_safety import MLFitScope
from app.ml.training import (
    MLTrainingAlgorithm,
    MLTrainingResult,
    MLTrainingSpecification,
    train_regression,
)
from app.optimization.temporal_domain import TemporalSegmentRole


class MLWalkForwardMode(StrEnum):
    """Canonical v1 walk-forward window progression."""

    EXPANDING = "EXPANDING"
    ROLLING = "ROLLING"


@dataclass(frozen=True, slots=True)
class MLWalkForwardSpecification(MLContract):
    """Deterministic recipe for a sequence of walk-forward folds."""

    dataset: MLDatasetSpecification
    mode: MLWalkForwardMode
    train_candles: int
    validation_candles: int
    test_candles: int
    step_candles: int
    fold_count: int
    allow_retrospective_context: bool
    algorithm: MLTrainingAlgorithm
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.dataset, MLDatasetSpecification)

        _require(
            self.dataset.target is not None,
            "walk-forward evaluation requires a supervised dataset",
        )

        _require(type(self.mode) is MLWalkForwardMode)

        _count(self.train_candles, minimum=1)
        _count(self.validation_candles, minimum=1)
        _count(self.test_candles, minimum=1)
        _count(self.step_candles, minimum=1)
        _count(self.fold_count, minimum=1)

        _require(type(self.allow_retrospective_context) is bool)

        _require(type(self.algorithm) is MLTrainingAlgorithm)
        _require(
            self.algorithm is MLTrainingAlgorithm.LINEAR_REGRESSION_OLS_NORMAL_EQUATIONS_DECIMAL_V1
        )

        _nested(
            self.implementation,
            MLImplementationReference,
        )

        _same(self.dataset.provenance.implementation == self.implementation)

        for index in range(self.fold_count):
            dataset, split = walk_forward_fold_plan(
                self,
                index,
            )
            _nested(dataset, MLDatasetSpecification)
            _nested(split, MLTemporalSplit)


@dataclass(frozen=True, slots=True)
class MLWalkForwardFold(MLContract):
    """Training and validation evidence for one exact walk-forward fold."""

    index: int
    materialization: MLDatasetMaterialization
    training_result: MLTrainingResult
    evaluation: MLRegressionEvaluationEvidence

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _count(self.index)

        _nested(
            self.materialization,
            MLDatasetMaterialization,
        )
        _nested(
            self.training_result,
            MLTrainingResult,
        )
        _nested(
            self.evaluation,
            MLRegressionEvaluationEvidence,
        )

        _same(self.training_result.training_specification.materialization == self.materialization)

        _same(self.evaluation.specification.training_result == self.training_result)

        _same(self.evaluation.specification.materialization == self.materialization)

        _require(self.evaluation.specification.role is TemporalSegmentRole.VALIDATION)


@dataclass(frozen=True, slots=True)
class MLWalkForwardEvaluationEvidence(MLContract):
    """Ordered per-fold evidence for one deterministic walk-forward run."""

    specification: MLWalkForwardSpecification
    folds: tuple[MLWalkForwardFold, ...]
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(
            self.specification,
            MLWalkForwardSpecification,
        )

        _nested(
            self.implementation,
            MLImplementationReference,
        )

        _same(self.implementation == self.specification.implementation)

        _require(type(self.folds) is tuple and len(self.folds) == self.specification.fold_count)

        for expected_index, fold in enumerate(self.folds):
            _nested(
                fold,
                MLWalkForwardFold,
            )

            _same(fold.index == expected_index)

            expected_dataset, expected_split = walk_forward_fold_plan(
                self.specification,
                expected_index,
            )

            _same(fold.materialization.dataset == expected_dataset)

            _same(fold.materialization.split == expected_split)

            _same(
                fold.training_result.training_specification.algorithm
                == self.specification.algorithm
            )

            _same(
                fold.training_result.training_specification.implementation
                == self.specification.implementation
            )

            _same(fold.evaluation.specification.implementation == self.specification.implementation)


def walk_forward_fold_plan(
    specification: MLWalkForwardSpecification,
    index: int,
) -> tuple[MLDatasetSpecification, MLTemporalSplit]:
    """Build the canonical dataset and temporal split for one fold."""

    _require(type(specification) is MLWalkForwardSpecification)

    _require(
        type(index) is int and not isinstance(index, bool) and 0 <= index < specification.fold_count
    )

    base_dataset = specification.dataset
    base_selected = base_dataset.provenance.selected_range

    cadence = get_timeframe(base_dataset.feature_set.timeframe).duration

    step = cadence * specification.step_candles

    if specification.mode is MLWalkForwardMode.EXPANDING:
        train_start = base_selected.start
        train_end = train_start + cadence * specification.train_candles + step * index
    else:
        train_start = base_selected.start + step * index
        train_end = train_start + cadence * specification.train_candles

    validation_start = train_end
    validation_end = validation_start + cadence * specification.validation_candles

    test_start = validation_end
    test_end = test_start + cadence * specification.test_candles

    _require(
        base_selected.start
        <= train_start
        < train_end
        < validation_end
        < test_end
        <= base_selected.end,
        "walk-forward fold exceeds selected dataset range",
    )

    selected_range = DataRange(
        train_start,
        test_end,
    )

    fold_provenance = replace(
        base_dataset.provenance,
        selected_range=selected_range,
    )

    fold_dataset = replace(
        base_dataset,
        provenance=fold_provenance,
    )

    split = MLTemporalSplit(
        dataset=fold_dataset,
        train=DataRange(
            train_start,
            train_end,
        ),
        validation=DataRange(
            validation_start,
            validation_end,
        ),
        test=DataRange(
            test_start,
            test_end,
        ),
        training_cutoff=train_end,
        allow_retrospective_context=(specification.allow_retrospective_context),
    )

    return fold_dataset, split


def evaluate_walk_forward(
    specification: MLWalkForwardSpecification,
    materialization_service: MLDatasetMaterializationService,
) -> MLWalkForwardEvaluationEvidence:
    """Materialize, train, and validate every canonical walk-forward fold."""

    _nested(
        specification,
        MLWalkForwardSpecification,
    )

    if type(materialization_service) is not MLDatasetMaterializationService:
        raise TypeError("materialization_service must be MLDatasetMaterializationService")

    folds: list[MLWalkForwardFold] = []

    for index in range(specification.fold_count):
        dataset, split = walk_forward_fold_plan(
            specification,
            index,
        )

        materialization = materialization_service.build(
            dataset,
            split,
            specification.implementation,
        )

        training_specification = MLTrainingSpecification(
            materialization=materialization,
            fit_scope=MLFitScope(split),
            algorithm=specification.algorithm,
            implementation=specification.implementation,
        )

        training_result = train_regression(training_specification)

        evaluation_specification = MLEvaluationSpecification(
            training_result=training_result,
            materialization=materialization,
            role=TemporalSegmentRole.VALIDATION,
            implementation=specification.implementation,
        )

        evaluation = evaluate_regression(evaluation_specification)

        folds.append(
            MLWalkForwardFold(
                index=index,
                materialization=materialization,
                training_result=training_result,
                evaluation=evaluation,
            )
        )

    return MLWalkForwardEvaluationEvidence(
        specification=specification,
        folds=tuple(folds),
        implementation=specification.implementation,
    )
