"""Deterministic champion/challenger comparison evidence for Phase 8-16."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

from app.ml.domain import (
    MLContract,
    MLImplementationReference,
    _nested,
    _require,
    _same,
)
from app.ml.errors import MLContractError
from app.ml.walk_forward import MLWalkForwardEvaluationEvidence
from app.optimization.temporal_domain import TemporalSegmentRole

_DECIMAL_COMPARISON_PRECISION = 50
_DECIMAL_COMPARISON_ROUNDING = ROUND_HALF_EVEN


class MLChampionChallengerError(MLContractError):
    code = "ml_champion_challenger_error"
    default_message = "A comparação champion/challenger ML falhou."


class MLChampionChallengerMetric(StrEnum):
    MAE = "MAE"
    MSE = "MSE"
    RMSE = "RMSE"


class MLChampionChallengerDecision(StrEnum):
    CHAMPION_RETAINED = "CHAMPION_RETAINED"
    CHALLENGER_PREFERRED = "CHALLENGER_PREFERRED"
    TIE = "TIE"


@dataclass(frozen=True, slots=True)
class MLChampionChallengerSpecification(MLContract):
    """Explicit offline comparison rule for two walk-forward evaluations."""

    metric: MLChampionChallengerMetric
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _require(type(self.metric) is MLChampionChallengerMetric)
        _nested(
            self.implementation,
            MLImplementationReference,
        )


@dataclass(frozen=True, slots=True)
class MLChampionChallengerEvidence(MLContract):
    """Immutable deterministic offline comparison evidence."""

    specification: MLChampionChallengerSpecification
    champion: MLWalkForwardEvaluationEvidence
    challenger: MLWalkForwardEvaluationEvidence
    champion_metric: Decimal = field(init=False)
    challenger_metric: Decimal = field(init=False)
    delta: Decimal = field(init=False)
    decision: MLChampionChallengerDecision = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(
            self.specification,
            MLChampionChallengerSpecification,
        )
        _nested(
            self.champion,
            MLWalkForwardEvaluationEvidence,
        )
        _nested(
            self.challenger,
            MLWalkForwardEvaluationEvidence,
        )

        _validate_comparable_walk_forward_evidence(
            self.champion,
            self.challenger,
        )

        champion_metric = aggregate_walk_forward_metric(
            self.champion,
            self.specification.metric,
        )
        challenger_metric = aggregate_walk_forward_metric(
            self.challenger,
            self.specification.metric,
        )

        delta = challenger_metric - champion_metric

        if challenger_metric < champion_metric:
            decision = MLChampionChallengerDecision.CHALLENGER_PREFERRED
        elif challenger_metric > champion_metric:
            decision = MLChampionChallengerDecision.CHAMPION_RETAINED
        else:
            decision = MLChampionChallengerDecision.TIE

        object.__setattr__(
            self,
            "champion_metric",
            champion_metric,
        )
        object.__setattr__(
            self,
            "challenger_metric",
            challenger_metric,
        )
        object.__setattr__(
            self,
            "delta",
            delta,
        )
        object.__setattr__(
            self,
            "decision",
            decision,
        )


def compare_champion_challenger(
    specification: MLChampionChallengerSpecification,
    *,
    champion: MLWalkForwardEvaluationEvidence,
    challenger: MLWalkForwardEvaluationEvidence,
) -> MLChampionChallengerEvidence:
    """Compare two compatible walk-forward evaluations deterministically."""

    _nested(
        specification,
        MLChampionChallengerSpecification,
    )
    _nested(
        champion,
        MLWalkForwardEvaluationEvidence,
    )
    _nested(
        challenger,
        MLWalkForwardEvaluationEvidence,
    )

    return MLChampionChallengerEvidence(
        specification=specification,
        champion=champion,
        challenger=challenger,
    )


def aggregate_walk_forward_metric(
    evidence: MLWalkForwardEvaluationEvidence,
    metric: MLChampionChallengerMetric,
) -> Decimal:
    """Return one sample-weighted regression error across validation folds."""

    _nested(
        evidence,
        MLWalkForwardEvaluationEvidence,
    )
    _require(type(metric) is MLChampionChallengerMetric)

    total_samples = sum(fold.evaluation.sample_count for fold in evidence.folds)

    _require(
        total_samples >= 1,
        "walk-forward comparison requires evaluation samples",
    )

    with localcontext() as context:
        context.prec = _DECIMAL_COMPARISON_PRECISION
        context.rounding = _DECIMAL_COMPARISON_ROUNDING

        divisor = Decimal(total_samples)

        if metric is MLChampionChallengerMetric.MAE:
            weighted_total = sum(
                (
                    fold.evaluation.metrics.mae * Decimal(fold.evaluation.sample_count)
                    for fold in evidence.folds
                ),
                Decimal("0"),
            )
            result = weighted_total / divisor

        elif metric is MLChampionChallengerMetric.MSE:
            weighted_total = sum(
                (
                    fold.evaluation.metrics.mse * Decimal(fold.evaluation.sample_count)
                    for fold in evidence.folds
                ),
                Decimal("0"),
            )
            result = weighted_total / divisor

        else:
            weighted_mse = (
                sum(
                    (
                        fold.evaluation.metrics.mse * Decimal(fold.evaluation.sample_count)
                        for fold in evidence.folds
                    ),
                    Decimal("0"),
                )
                / divisor
            )

            result = weighted_mse.sqrt()

    _require(
        result.is_finite() and result >= Decimal("0"),
        "walk-forward aggregate metric must be finite and non-negative",
    )

    return result


def _validate_comparable_walk_forward_evidence(
    champion: MLWalkForwardEvaluationEvidence,
    challenger: MLWalkForwardEvaluationEvidence,
) -> None:
    champion_specification = champion.specification
    challenger_specification = challenger.specification

    _same(champion_specification.mode == challenger_specification.mode)
    _same(champion_specification.train_candles == challenger_specification.train_candles)
    _same(champion_specification.validation_candles == challenger_specification.validation_candles)
    _same(champion_specification.test_candles == challenger_specification.test_candles)
    _same(champion_specification.step_candles == challenger_specification.step_candles)
    _same(champion_specification.fold_count == challenger_specification.fold_count)
    _same(
        champion_specification.allow_retrospective_context
        == challenger_specification.allow_retrospective_context
    )

    champion_dataset = champion_specification.dataset
    challenger_dataset = challenger_specification.dataset

    _same(champion_dataset.provenance.source == challenger_dataset.provenance.source)
    _same(
        champion_dataset.provenance.selected_range == challenger_dataset.provenance.selected_range
    )
    _same(champion_dataset.provenance.context_range == challenger_dataset.provenance.context_range)
    _same(champion_dataset.feature_set.timeframe == challenger_dataset.feature_set.timeframe)
    _same(champion_dataset.target == challenger_dataset.target)

    for champion_fold, challenger_fold in zip(
        champion.folds,
        challenger.folds,
        strict=True,
    ):
        champion_split = champion_fold.materialization.split
        challenger_split = challenger_fold.materialization.split

        _same(champion_split.train == challenger_split.train)
        _same(champion_split.validation == challenger_split.validation)
        _same(champion_split.test == challenger_split.test)
        _same(champion_split.training_cutoff == challenger_split.training_cutoff)
        _same(
            champion_split.allow_retrospective_context
            == challenger_split.allow_retrospective_context
        )

        champion_validation_keys = _validation_observation_keys(
            champion_fold,
        )
        challenger_validation_keys = _validation_observation_keys(
            challenger_fold,
        )

        _same(champion_validation_keys == challenger_validation_keys)


def _validation_observation_keys(
    fold: object,
) -> tuple[tuple[object, object], ...]:
    from app.ml.walk_forward import MLWalkForwardFold

    _require(type(fold) is MLWalkForwardFold)
    assert isinstance(fold, MLWalkForwardFold)

    return tuple(
        (
            row.vector.observation.candle_open_time,
            row.vector.observation.observation_time,
        )
        for row in fold.materialization.rows
        if row.role is TemporalSegmentRole.VALIDATION
    )
