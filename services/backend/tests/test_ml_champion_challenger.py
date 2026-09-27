"""Champion/challenger comparison tests for Phase 8-16."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

import pytest

from app.market_data.snapshots import _snapshot_id_from_values
from app.ml.champion_challenger import (
    MLChampionChallengerDecision,
    MLChampionChallengerEvidence,
    MLChampionChallengerMetric,
    MLChampionChallengerSpecification,
    aggregate_walk_forward_metric,
    compare_champion_challenger,
)
from app.ml.documents import decode_document, encode_document
from app.ml.errors import MLContractError
from app.ml.evaluation import MLRegressionMetrics
from app.ml.materialization import MLDatasetMaterializationService
from app.ml.walk_forward import (
    MLWalkForwardEvaluationEvidence,
    evaluate_walk_forward,
)
from tests.test_ml_materialization import _candles
from tests.test_ml_walk_forward import (
    _service,
)
from tests.test_ml_walk_forward import (
    _specification as _walk_forward_specification,
)


def _evidence() -> MLWalkForwardEvaluationEvidence:
    return evaluate_walk_forward(
        _walk_forward_specification(),
        _service(),
    )


def _service_for_declared_snapshot() -> MLDatasetMaterializationService:
    class Reader:
        def __init__(self, specification) -> None:
            self.candles = _candles()
            self.source = specification.provenance.source

        def open_snapshot(self, snapshot_id: str):
            assert snapshot_id == self.source.snapshot_id

            return type(
                "Snapshot",
                (),
                {
                    "snapshot_id": self.source.snapshot_id,
                    "checksum": self.source.snapshot_checksum,
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

    return MLDatasetMaterializationService(
        reader_factory=lambda specification, *_: Reader(specification)
    )


def _comparison_specification(
    metric: MLChampionChallengerMetric = (MLChampionChallengerMetric.MAE),
) -> MLChampionChallengerSpecification:
    evidence = _evidence()

    return MLChampionChallengerSpecification(
        metric=metric,
        implementation=evidence.implementation,
    )


def _replace_fold_metrics(
    evidence: MLWalkForwardEvaluationEvidence,
    values: tuple[tuple[Decimal, Decimal, Decimal], ...],
) -> MLWalkForwardEvaluationEvidence:
    assert len(values) == len(evidence.folds)

    folds = []

    for fold, (mae, mse, rmse) in zip(
        evidence.folds,
        values,
        strict=True,
    ):
        evaluation = replace(
            fold.evaluation,
            metrics=MLRegressionMetrics(
                mae=mae,
                mse=mse,
                rmse=rmse,
            ),
        )

        folds.append(
            replace(
                fold,
                evaluation=evaluation,
            )
        )

    return replace(
        evidence,
        folds=tuple(folds),
    )


def test_mae_aggregation_is_sample_weighted() -> None:
    evidence = _evidence()

    first_count = evidence.folds[0].evaluation.sample_count
    second_count = evidence.folds[1].evaluation.sample_count

    controlled = _replace_fold_metrics(
        evidence,
        (
            (
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
            ),
            (
                Decimal("3"),
                Decimal("9"),
                Decimal("3"),
            ),
        ),
    )

    expected = (
        Decimal("1") * Decimal(first_count) + Decimal("3") * Decimal(second_count)
    ) / Decimal(first_count + second_count)

    assert (
        aggregate_walk_forward_metric(
            controlled,
            MLChampionChallengerMetric.MAE,
        )
        == expected
    )


def test_mse_aggregation_is_sample_weighted() -> None:
    evidence = _evidence()

    first_count = evidence.folds[0].evaluation.sample_count
    second_count = evidence.folds[1].evaluation.sample_count

    controlled = _replace_fold_metrics(
        evidence,
        (
            (
                Decimal("1"),
                Decimal("4"),
                Decimal("2"),
            ),
            (
                Decimal("1"),
                Decimal("16"),
                Decimal("4"),
            ),
        ),
    )

    expected = (
        Decimal("4") * Decimal(first_count) + Decimal("16") * Decimal(second_count)
    ) / Decimal(first_count + second_count)

    assert (
        aggregate_walk_forward_metric(
            controlled,
            MLChampionChallengerMetric.MSE,
        )
        == expected
    )


def test_rmse_is_sqrt_of_sample_weighted_mse() -> None:
    evidence = _evidence()

    first_count = evidence.folds[0].evaluation.sample_count
    second_count = evidence.folds[1].evaluation.sample_count

    controlled = _replace_fold_metrics(
        evidence,
        (
            (
                Decimal("1"),
                Decimal("4"),
                Decimal("2"),
            ),
            (
                Decimal("1"),
                Decimal("16"),
                Decimal("4"),
            ),
        ),
    )

    weighted_mse = (
        Decimal("4") * Decimal(first_count) + Decimal("16") * Decimal(second_count)
    ) / Decimal(first_count + second_count)

    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        expected = weighted_mse.sqrt()

    assert (
        aggregate_walk_forward_metric(
            controlled,
            MLChampionChallengerMetric.RMSE,
        )
        == expected
    )


def test_rmse_does_not_average_fold_rmse_directly() -> None:
    evidence = _evidence()

    controlled = _replace_fold_metrics(
        evidence,
        (
            (
                Decimal("0"),
                Decimal("1"),
                Decimal("100"),
            ),
            (
                Decimal("0"),
                Decimal("9"),
                Decimal("100"),
            ),
        ),
    )

    result = aggregate_walk_forward_metric(
        controlled,
        MLChampionChallengerMetric.RMSE,
    )

    assert result != Decimal("100")


def test_lower_challenger_metric_is_preferred() -> None:
    base = _evidence()

    champion = _replace_fold_metrics(
        base,
        (
            (
                Decimal("3"),
                Decimal("9"),
                Decimal("3"),
            ),
            (
                Decimal("3"),
                Decimal("9"),
                Decimal("3"),
            ),
        ),
    )

    challenger = _replace_fold_metrics(
        base,
        (
            (
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
            ),
            (
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
            ),
        ),
    )

    result = compare_champion_challenger(
        _comparison_specification(),
        champion=champion,
        challenger=challenger,
    )

    assert result.decision is MLChampionChallengerDecision.CHALLENGER_PREFERRED
    assert result.challenger_metric < result.champion_metric
    assert result.delta < Decimal("0")


def test_lower_champion_metric_is_retained() -> None:
    base = _evidence()

    champion = _replace_fold_metrics(
        base,
        (
            (
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
            ),
            (
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
            ),
        ),
    )

    challenger = _replace_fold_metrics(
        base,
        (
            (
                Decimal("2"),
                Decimal("4"),
                Decimal("2"),
            ),
            (
                Decimal("2"),
                Decimal("4"),
                Decimal("2"),
            ),
        ),
    )

    result = compare_champion_challenger(
        _comparison_specification(),
        champion=champion,
        challenger=challenger,
    )

    assert result.decision is MLChampionChallengerDecision.CHAMPION_RETAINED
    assert result.champion_metric < result.challenger_metric
    assert result.delta > Decimal("0")


def test_equal_metrics_produce_explicit_tie() -> None:
    evidence = _evidence()

    result = compare_champion_challenger(
        _comparison_specification(),
        champion=evidence,
        challenger=evidence,
    )

    assert result.decision is MLChampionChallengerDecision.TIE
    assert result.champion_metric == result.challenger_metric
    assert result.delta == Decimal("0")


@pytest.mark.parametrize(
    "metric",
    (
        MLChampionChallengerMetric.MAE,
        MLChampionChallengerMetric.MSE,
        MLChampionChallengerMetric.RMSE,
    ),
)
def test_all_supported_metrics_are_deterministic(
    metric: MLChampionChallengerMetric,
) -> None:
    evidence = _evidence()

    first = aggregate_walk_forward_metric(
        evidence,
        metric,
    )
    second = aggregate_walk_forward_metric(
        evidence,
        metric,
    )

    assert first == second
    assert first.is_finite()
    assert first >= Decimal("0")


def test_different_walk_forward_mode_is_rejected() -> None:
    champion = _evidence()

    challenger = evaluate_walk_forward(
        _walk_forward_specification(mode=type(champion.specification.mode).ROLLING),
        _service(),
    )

    with pytest.raises(MLContractError):
        compare_champion_challenger(
            _comparison_specification(),
            champion=champion,
            challenger=challenger,
        )


def test_different_fold_protocol_is_rejected() -> None:
    champion = _evidence()

    challenger = evaluate_walk_forward(
        _walk_forward_specification(
            fold_count=1,
        ),
        _service(),
    )

    with pytest.raises(MLContractError):
        compare_champion_challenger(
            _comparison_specification(),
            champion=champion,
            challenger=challenger,
        )


def test_different_source_snapshot_is_rejected() -> None:
    champion = _evidence()

    original = champion.specification
    dataset = original.dataset
    source = dataset.provenance.source

    changed_checksum = "f" * 64

    changed_snapshot_id = _snapshot_id_from_values(
        source.dataset_key,
        source.dataset_version,
        changed_checksum,
        source.available_coverage.data_range,
    )

    changed_source = replace(
        source,
        snapshot_id=changed_snapshot_id,
        snapshot_checksum=changed_checksum,
    )

    changed_dataset = replace(
        dataset,
        provenance=replace(
            dataset.provenance,
            source=changed_source,
        ),
    )

    challenger_specification = replace(
        original,
        dataset=changed_dataset,
    )

    challenger = evaluate_walk_forward(
        challenger_specification,
        _service_for_declared_snapshot(),
    )

    with pytest.raises(MLContractError):
        compare_champion_challenger(
            _comparison_specification(),
            champion=champion,
            challenger=challenger,
        )


def test_comparison_is_deterministic() -> None:
    evidence = _evidence()
    specification = _comparison_specification(MLChampionChallengerMetric.RMSE)

    first = compare_champion_challenger(
        specification,
        champion=evidence,
        challenger=evidence,
    )
    second = compare_champion_challenger(
        specification,
        champion=evidence,
        challenger=evidence,
    )

    assert first == second
    assert first.identity == second.identity
    assert first.checksum == second.checksum


def test_specification_round_trips_codec() -> None:
    specification = _comparison_specification()

    rebuilt = decode_document(encode_document(specification))

    assert isinstance(
        rebuilt,
        MLChampionChallengerSpecification,
    )
    assert rebuilt == specification
    assert rebuilt.identity == specification.identity
    assert rebuilt.checksum == specification.checksum


def test_evidence_round_trips_codec() -> None:
    evidence = _evidence()

    comparison = compare_champion_challenger(
        _comparison_specification(),
        champion=evidence,
        challenger=evidence,
    )

    rebuilt = decode_document(encode_document(comparison))

    assert isinstance(
        rebuilt,
        MLChampionChallengerEvidence,
    )
    assert rebuilt == comparison
    assert rebuilt.identity == comparison.identity
    assert rebuilt.checksum == comparison.checksum


def test_evidence_contains_no_registry_mutation_surface() -> None:
    evidence = _evidence()

    comparison = compare_champion_challenger(
        _comparison_specification(),
        champion=evidence,
        challenger=evidence,
    )

    forbidden = (
        "activate",
        "deployment",
        "promotion",
        "registry_update",
        "paper",
        "live",
        "execute",
        "order",
    )

    names = tuple(name.lower() for name in dir(comparison))

    for token in forbidden:
        assert not any(token in name for name in names)


def test_module_has_no_operational_authority_dependencies() -> None:
    import app.ml.champion_challenger as module

    forbidden = (
        "repository",
        "database",
        "psycopg",
        "fastapi",
        "router",
        "endpoint",
        "paper",
        "live",
        "execution",
        "deploy",
        "activate",
        "promotion",
    )

    names = tuple(name.lower() for name in vars(module))

    for token in forbidden:
        assert not any(token in name for name in names)
