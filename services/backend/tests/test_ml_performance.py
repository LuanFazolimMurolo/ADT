from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtesting.reports import ComparisonMetric
from app.indicators.domain import IndicatorDescriptor
from app.market_data.datasets import (
    DatasetIdentity,
    DatasetKind,
    DatasetLineage,
    DatasetManifest,
    DatasetState,
    GapPolicy,
    PartitionSummary,
)
from app.market_data.domain import DataRange, Exchange, MarketType
from app.market_data.snapshots import build_snapshot_id
from app.ml.documents import decode_document, encode_document
from app.ml.domain import (
    MLCandleDependency,
    MLDatasetSpecification,
    MLFeatureSetSpecification,
    MLFeatureSpecification,
    MLFeatureVector,
    MLHistoryPolicy,
    MLImplementationReference,
    MLMissingPolicy,
    MLObservation,
    MLProvenance,
    MLResetPolicy,
    MLSeedPolicy,
    MLValueKind,
)
from app.ml.performance import (
    MLDegradationDirection,
    MLDegradationEvidence,
    MLDegradationSpecification,
    MLPerformanceBaseline,
    MLPerformanceMetricSpecification,
    MLPerformancePredictionEvidence,
    MLPerformanceSubject,
    build_performance_target,
    performance_target_descriptor,
)
from app.ml.predictions import (
    MLModelReference,
    MLPredictionProblemType,
    MLPredictionSpecification,
    MLRegressionPrediction,
)
from app.optimization.temporal_domain import TemporalCoverage, TemporalSnapshotReference
from app.strategies.definitions import (
    StoredStrategyParameter,
    strategy_parameter_checksum,
)
from app.strategies.domain import StrategyParameterKind

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _implementation() -> MLImplementationReference:
    return MLImplementationReference(
        "adt-performance-test-build",
        "1",
        "e" * 64,
    )


def _subject(
    *,
    fast_period: int = 10,
) -> MLPerformanceSubject:
    parameters = (
        StoredStrategyParameter(
            "fast_period",
            StrategyParameterKind.INTEGER,
            fast_period,
        ),
        StoredStrategyParameter(
            "slow_period",
            StrategyParameterKind.INTEGER,
            20,
        ),
    )

    return MLPerformanceSubject(
        plugin_name="ema_cross",
        plugin_version="1",
        plugin_schema_version=1,
        lifecycle_version=1,
        parameters=parameters,
        parameters_checksum=strategy_parameter_checksum(parameters),
    )


def _dataset(
    metric: ComparisonMetric = ComparisonMetric.TOTAL_RETURN,
) -> MLDatasetSpecification:
    implementation = _implementation()

    identity = DatasetIdentity(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTC/USDT",
        timeframe="1h",
        kind=DatasetKind.DERIVED,
        source="binance-public",
        construction_policy="canonical_ohlcv:1:STRICT:crypto_24_7",
        schema_version=1,
    )

    manifest = DatasetManifest(
        identity=identity,
        schema_version=1,
        source_dataset_key="raw:binance:spot:BTC/USDT:1m",
        source_dataset_version="b" * 64,
        source_checksum="c" * 64,
        target_dataset_key=identity.key,
        target_version="v1",
        target_checksum="a" * 64,
        source_timeframe="1m",
        target_timeframe="1h",
        gap_policy=GapPolicy.STRICT,
        calendar="crypto_24_7",
        first_open_time=START.isoformat(),
        last_open_time=(START + HOUR * 11).isoformat(),
        candle_count=12,
        partitions=(
            PartitionSummary(
                relative_path="derived/year=2026/month=01/candles.parquet",
                year=2026,
                month=1,
                candle_count=12,
                first_open_time=START.isoformat(),
                last_open_time=(START + HOUR * 11).isoformat(),
                checksum="d" * 64,
            ),
        ),
        source_partitions=(),
        algorithm="canonical_ohlcv",
        algorithm_version="1",
        created_at=START.isoformat(),
        updated_at=START.isoformat(),
        state=DatasetState.COMPLETE,
        lineage=DatasetLineage(
            source_dataset_key="raw:binance:spot:BTC/USDT:1m",
            source_dataset_version="b" * 64,
            source_checksum="c" * 64,
            source_timeframe="1m",
            target_timeframe="1h",
            algorithm="canonical_ohlcv",
            algorithm_version="1",
            gap_policy=GapPolicy.STRICT,
            open_candle_policy="REJECT",
            calendar="crypto_24_7",
            materialized_at=START.isoformat(),
        ),
    )

    snapshot_range = DataRange(
        START,
        START + HOUR * 12,
    )

    provenance = MLProvenance(
        TemporalSnapshotReference(
            snapshot_id=build_snapshot_id(manifest, snapshot_range),
            snapshot_checksum=manifest.target_checksum,
            dataset_key=identity.key,
            dataset_version=manifest.target_version,
            identity=identity,
            gap_policy=GapPolicy.STRICT,
            available_coverage=TemporalCoverage(
                snapshot_range,
                "1h",
                12,
            ),
        ),
        selected_range=DataRange(
            START + HOUR,
            START + HOUR * 11,
        ),
        context_range=snapshot_range,
        implementation=implementation,
    )

    feature = MLFeatureSpecification(
        name="close",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="raw close",
        algorithm=IndicatorDescriptor("close", "1"),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=1,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )

    target = build_performance_target(
        _subject(),
        metric,
        horizon_candles=2,
        implementation=implementation,
    )

    return MLDatasetSpecification(
        provenance,
        MLFeatureSetSpecification(
            "1h",
            (feature,),
            implementation,
        ),
        target,
    )


def _observation(dataset: MLDatasetSpecification) -> MLObservation:
    return MLObservation(
        dataset,
        START + HOUR * 3,
        START + HOUR * 4,
    )


def _vector(
    dataset: MLDatasetSpecification,
    observation: MLObservation,
) -> MLFeatureVector:
    dependency = MLCandleDependency(
        source=dataset.provenance.source,
        data_range=DataRange(
            START,
            START + HOUR * 3,
        ),
    )

    return MLFeatureVector(
        observation,
        dataset.feature_set,
        (Decimal("100"),),
        (dependency,),
    )


def _model_reference() -> MLModelReference:
    return MLModelReference(
        model_id="performance-model",
        model_version="v1",
        artifact_checksum="c" * 64,
        implementation=MLImplementationReference(
            "adt-performance-model",
            "1",
            "d" * 64,
        ),
    )


def _performance_specification(
    dataset: MLDatasetSpecification,
    metric: ComparisonMetric = ComparisonMetric.TOTAL_RETURN,
) -> MLPerformanceMetricSpecification:
    assert dataset.target is not None

    return MLPerformanceMetricSpecification(
        subject=_subject(),
        metric=metric,
        target=dataset.target,
        implementation=dataset.provenance.implementation,
    )


def _prediction(
    dataset: MLDatasetSpecification,
    *,
    output: Decimal,
) -> MLRegressionPrediction:
    assert dataset.target is not None

    observation = _observation(dataset)
    feature_vector = _vector(dataset, observation)
    model_reference = _model_reference()

    prediction_specification = MLPredictionSpecification(
        problem_type=MLPredictionProblemType.REGRESSION,
        target=dataset.target,
        feature_set=dataset.feature_set,
        implementation=dataset.provenance.implementation,
        model_reference=model_reference,
        feature_vector=feature_vector,
    )

    return MLRegressionPrediction(
        prediction_specification=prediction_specification,
        model_reference=model_reference,
        observation=observation,
        feature_vector=feature_vector,
        prediction_time=observation.observation_time,
        output=output,
    )


def _prediction_evidence(
    dataset: MLDatasetSpecification,
    *,
    output: Decimal,
) -> MLPerformancePredictionEvidence:
    return MLPerformancePredictionEvidence(
        specification=_performance_specification(dataset),
        prediction=_prediction(dataset, output=output),
    )


def _baseline(
    *,
    metric: ComparisonMetric = ComparisonMetric.TOTAL_RETURN,
    value: Decimal = Decimal("0.10"),
) -> MLPerformanceBaseline:
    return MLPerformanceBaseline(
        subject=_subject(),
        metric=metric,
        value=value,
        data_range=DataRange(
            START,
            START + HOUR * 3,
        ),
        information_time=START + HOUR * 3,
        source_checksum="f" * 64,
    )


def _degradation_specification(
    dataset: MLDatasetSpecification,
    *,
    direction: MLDegradationDirection,
    threshold: Decimal = Decimal("0.02"),
) -> MLDegradationSpecification:
    return MLDegradationSpecification(
        performance=_performance_specification(dataset),
        direction=direction,
        absolute_threshold=threshold,
    )


def test_performance_target_is_canonical_for_total_return() -> None:
    implementation = _implementation()

    subject = _subject()
    target = build_performance_target(
        subject,
        ComparisonMetric.TOTAL_RETURN,
        horizon_candles=2,
        implementation=implementation,
    )

    assert target.name == "strategy_total_return_v1"
    assert target.algorithm == performance_target_descriptor(
        subject,
        ComparisonMetric.TOTAL_RETURN,
    )
    assert target.kind is MLValueKind.DECIMAL
    assert target.unit == "ratio"
    assert target.meaning == "future strategy total_return"
    assert target.horizon_candles == 2
    assert target.implementation == implementation


@pytest.mark.parametrize(
    ("metric", "unit"),
    [
        (ComparisonMetric.TOTAL_RETURN, "ratio"),
        (ComparisonMetric.CAGR, "ratio"),
        (ComparisonMetric.SHARPE_RATIO, "ratio"),
        (ComparisonMetric.SORTINO_RATIO, "ratio"),
        (ComparisonMetric.MAXIMUM_DRAWDOWN_PCT, "ratio"),
        (ComparisonMetric.NET_PROFIT, "quote"),
        (ComparisonMetric.PROFIT_FACTOR, "ratio"),
    ],
)
def test_supported_comparison_metrics_have_explicit_units(
    metric: ComparisonMetric,
    unit: str,
) -> None:
    subject = _subject()
    target = build_performance_target(
        subject,
        metric,
        horizon_candles=1,
        implementation=_implementation(),
    )

    assert target.unit == unit
    assert target.algorithm == performance_target_descriptor(subject, metric)


def test_forward_close_return_cannot_be_relabelled_as_strategy_performance() -> None:
    dataset = _dataset()

    incompatible_target = replace(
        dataset.target,
        name="forward_close_return_v1",
        algorithm=IndicatorDescriptor("forward_close_return_v1", "1"),
        meaning="future close return",
    )

    with pytest.raises(Exception):
        MLPerformanceMetricSpecification(
            subject=_subject(),
            metric=ComparisonMetric.TOTAL_RETURN,
            target=incompatible_target,
            implementation=dataset.provenance.implementation,
        )


def test_performance_prediction_binds_exact_target_and_metric() -> None:
    dataset = _dataset()
    evidence = _prediction_evidence(
        dataset,
        output=Decimal("0.125"),
    )

    assert evidence.specification.metric is ComparisonMetric.TOTAL_RETURN
    assert evidence.prediction.output == Decimal("0.125")
    assert evidence.prediction.prediction_specification.target == evidence.specification.target


def test_prediction_for_different_performance_target_is_rejected() -> None:
    total_return_dataset = _dataset(ComparisonMetric.TOTAL_RETURN)
    cagr_dataset = _dataset(ComparisonMetric.CAGR)

    with pytest.raises(Exception):
        MLPerformancePredictionEvidence(
            specification=_performance_specification(total_return_dataset),
            prediction=_prediction(
                cagr_dataset,
                output=Decimal("0.05"),
            ),
        )


def test_baseline_is_immutable_and_requires_historical_information() -> None:
    baseline = _baseline()

    assert baseline.metric is ComparisonMetric.TOTAL_RETURN
    assert baseline.value == Decimal("0.10")
    assert baseline.information_time == START + HOUR * 3

    with pytest.raises(FrozenInstanceError):
        baseline.value = Decimal("0.20")  # type: ignore[misc]

    with pytest.raises(Exception):
        MLPerformanceBaseline(
            subject=_subject(),
            metric=ComparisonMetric.TOTAL_RETURN,
            value=Decimal("0.10"),
            data_range=DataRange(
                START,
                START + HOUR * 4,
            ),
            information_time=START + HOUR * 3,
            source_checksum="f" * 64,
        )


def test_baseline_information_cannot_arrive_after_prediction() -> None:
    dataset = _dataset()
    prediction = _prediction_evidence(
        dataset,
        output=Decimal("0.05"),
    )

    future_baseline = MLPerformanceBaseline(
        subject=_subject(),
        metric=ComparisonMetric.TOTAL_RETURN,
        value=Decimal("0.10"),
        data_range=DataRange(
            START,
            START + HOUR * 4,
        ),
        information_time=START + HOUR * 5,
        source_checksum="f" * 64,
    )

    specification = _degradation_specification(
        dataset,
        direction=MLDegradationDirection.BELOW_BASELINE,
    )

    with pytest.raises(Exception):
        MLDegradationEvidence(
            specification=specification,
            baseline=future_baseline,
            prediction=prediction,
        )


def test_below_baseline_degradation_is_deterministic() -> None:
    dataset = _dataset()

    evidence = MLDegradationEvidence(
        specification=_degradation_specification(
            dataset,
            direction=MLDegradationDirection.BELOW_BASELINE,
            threshold=Decimal("0.02"),
        ),
        baseline=_baseline(value=Decimal("0.10")),
        prediction=_prediction_evidence(
            dataset,
            output=Decimal("0.07"),
        ),
    )

    assert evidence.delta == Decimal("-0.03")
    assert evidence.degraded is True


def test_below_baseline_threshold_boundary_is_not_degraded() -> None:
    dataset = _dataset()

    evidence = MLDegradationEvidence(
        specification=_degradation_specification(
            dataset,
            direction=MLDegradationDirection.BELOW_BASELINE,
            threshold=Decimal("0.02"),
        ),
        baseline=_baseline(value=Decimal("0.10")),
        prediction=_prediction_evidence(
            dataset,
            output=Decimal("0.08"),
        ),
    )

    assert evidence.delta == Decimal("-0.02")
    assert evidence.degraded is False


def test_above_baseline_degradation_is_deterministic() -> None:
    dataset = _dataset()

    evidence = MLDegradationEvidence(
        specification=_degradation_specification(
            dataset,
            direction=MLDegradationDirection.ABOVE_BASELINE,
            threshold=Decimal("0.02"),
        ),
        baseline=_baseline(value=Decimal("0.10")),
        prediction=_prediction_evidence(
            dataset,
            output=Decimal("0.13"),
        ),
    )

    assert evidence.delta == Decimal("0.03")
    assert evidence.degraded is True


def test_metric_mismatch_between_baseline_and_rule_is_rejected() -> None:
    dataset = _dataset()

    with pytest.raises(Exception):
        MLDegradationEvidence(
            specification=_degradation_specification(
                dataset,
                direction=MLDegradationDirection.BELOW_BASELINE,
            ),
            baseline=_baseline(
                metric=ComparisonMetric.CAGR,
                value=Decimal("0.10"),
            ),
            prediction=_prediction_evidence(
                dataset,
                output=Decimal("0.05"),
            ),
        )


@pytest.mark.parametrize(
    "threshold",
    [
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_invalid_degradation_threshold_is_rejected(
    threshold: Decimal,
) -> None:
    dataset = _dataset()

    with pytest.raises(Exception):
        MLDegradationSpecification(
            performance=_performance_specification(dataset),
            direction=MLDegradationDirection.BELOW_BASELINE,
            absolute_threshold=threshold,
        )


def test_performance_contracts_round_trip_through_canonical_codec() -> None:
    dataset = _dataset()

    performance = _performance_specification(dataset)
    prediction = _prediction_evidence(
        dataset,
        output=Decimal("0.07"),
    )
    baseline = _baseline(value=Decimal("0.10"))
    degradation_specification = MLDegradationSpecification(
        performance=performance,
        direction=MLDegradationDirection.BELOW_BASELINE,
        absolute_threshold=Decimal("0.02"),
    )
    degradation = MLDegradationEvidence(
        specification=degradation_specification,
        baseline=baseline,
        prediction=prediction,
    )

    values = (
        performance,
        prediction,
        baseline,
        degradation_specification,
        degradation,
    )

    for value in values:
        encoded = encode_document(value)
        decoded = decode_document(encoded)

        assert decoded == value
        assert decoded.identity == value.identity
        assert decoded.checksum == value.checksum


def test_equal_inputs_produce_equal_degradation_identity_and_checksum() -> None:
    dataset = _dataset()

    def build() -> MLDegradationEvidence:
        return MLDegradationEvidence(
            specification=_degradation_specification(
                dataset,
                direction=MLDegradationDirection.BELOW_BASELINE,
                threshold=Decimal("0.02"),
            ),
            baseline=_baseline(value=Decimal("0.10")),
            prediction=_prediction_evidence(
                dataset,
                output=Decimal("0.07"),
            ),
        )

    first = build()
    second = build()

    assert first == second
    assert first.identity == second.identity
    assert first.checksum == second.checksum
    assert first.delta == second.delta == Decimal("-0.03")
    assert first.degraded is second.degraded is True


def test_prediction_value_changes_degradation_evidence_checksum() -> None:
    dataset = _dataset()
    specification = _degradation_specification(
        dataset,
        direction=MLDegradationDirection.BELOW_BASELINE,
        threshold=Decimal("0.02"),
    )
    baseline = _baseline(value=Decimal("0.10"))

    first = MLDegradationEvidence(
        specification=specification,
        baseline=baseline,
        prediction=_prediction_evidence(
            dataset,
            output=Decimal("0.07"),
        ),
    )
    second = MLDegradationEvidence(
        specification=specification,
        baseline=baseline,
        prediction=_prediction_evidence(
            dataset,
            output=Decimal("0.09"),
        ),
    )

    assert first.prediction.prediction.identity == second.prediction.prediction.identity
    assert first.checksum != second.checksum
    assert first.delta != second.delta


def test_performance_module_exposes_no_selection_or_execution_authority() -> None:
    import app.ml.performance as performance

    forbidden = (
        "rank",
        "winner",
        "champion",
        "challenger",
        "recommend",
        "approve",
        "deploy",
        "order",
        "execute",
        "paper",
        "live",
    )

    exported = tuple(name.lower() for name in vars(performance))

    for token in forbidden:
        assert not any(token in name for name in exported)


def test_strategy_parameters_are_part_of_performance_target_identity() -> None:
    first_subject = _subject(fast_period=10)
    second_subject = _subject(fast_period=11)

    first = build_performance_target(
        first_subject,
        ComparisonMetric.TOTAL_RETURN,
        horizon_candles=2,
        implementation=_implementation(),
    )
    second = build_performance_target(
        second_subject,
        ComparisonMetric.TOTAL_RETURN,
        horizon_candles=2,
        implementation=_implementation(),
    )

    assert first_subject.parameters_checksum != second_subject.parameters_checksum
    assert first.algorithm != second.algorithm
    assert first.identity != second.identity
    assert first.checksum != second.checksum


def test_performance_subject_rejects_wrong_parameter_checksum() -> None:
    parameters = (
        StoredStrategyParameter(
            "fast_period",
            StrategyParameterKind.INTEGER,
            10,
        ),
    )

    with pytest.raises(Exception):
        MLPerformanceSubject(
            plugin_name="ema_cross",
            plugin_version="1",
            plugin_schema_version=1,
            lifecycle_version=1,
            parameters=parameters,
            parameters_checksum="a" * 64,
        )


def test_performance_subject_rejects_noncanonical_parameter_order() -> None:
    parameters = (
        StoredStrategyParameter(
            "slow_period",
            StrategyParameterKind.INTEGER,
            20,
        ),
        StoredStrategyParameter(
            "fast_period",
            StrategyParameterKind.INTEGER,
            10,
        ),
    )

    with pytest.raises(Exception):
        MLPerformanceSubject(
            plugin_name="ema_cross",
            plugin_version="1",
            plugin_schema_version=1,
            lifecycle_version=1,
            parameters=parameters,
            parameters_checksum=strategy_parameter_checksum(parameters),
        )


def test_baseline_from_different_strategy_configuration_is_rejected() -> None:
    dataset = _dataset()

    specification = _degradation_specification(
        dataset,
        direction=MLDegradationDirection.BELOW_BASELINE,
    )

    different_subject = _subject(fast_period=11)

    baseline = MLPerformanceBaseline(
        subject=different_subject,
        metric=ComparisonMetric.TOTAL_RETURN,
        value=Decimal("0.10"),
        data_range=DataRange(
            START,
            START + HOUR * 3,
        ),
        information_time=START + HOUR * 3,
        source_checksum="f" * 64,
    )

    with pytest.raises(Exception):
        MLDegradationEvidence(
            specification=specification,
            baseline=baseline,
            prediction=_prediction_evidence(
                dataset,
                output=Decimal("0.07"),
            ),
        )


def test_performance_subject_round_trip_through_canonical_codec() -> None:
    subject = _subject()

    encoded = encode_document(subject)
    decoded = decode_document(encoded)

    assert decoded == subject
    assert decoded.identity == subject.identity
    assert decoded.checksum == subject.checksum
