from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext

import pytest

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
from app.ml.anomalies import (
    MLAnomalyEvidence,
    MLAnomalyMethod,
    MLAnomalyReference,
    MLAnomalyReferenceMethod,
    MLAnomalySpecification,
)
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
from app.optimization.temporal_domain import TemporalCoverage, TemporalSnapshotReference

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _implementation() -> MLImplementationReference:
    return MLImplementationReference(
        "adt-anomaly-test-build",
        "1",
        "e" * 64,
    )


def _dataset(
    *,
    symbol: str = "BTC/USDT",
) -> MLDatasetSpecification:
    identity = DatasetIdentity(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol=symbol,
        timeframe="1h",
        kind=DatasetKind.DERIVED,
        source="binance-public",
        construction_policy="canonical_ohlcv:1:STRICT:crypto_24_7",
        schema_version=1,
    )

    manifest = DatasetManifest(
        identity=identity,
        schema_version=1,
        source_dataset_key=f"raw:binance:spot:{symbol}:1m",
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
            source_dataset_key=f"raw:binance:spot:{symbol}:1m",
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

    source = TemporalSnapshotReference(
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
    )

    implementation = _implementation()

    provenance = MLProvenance(
        source,
        selected_range=DataRange(
            START + HOUR,
            START + HOUR * 11,
        ),
        context_range=snapshot_range,
        implementation=implementation,
    )

    feature = MLFeatureSpecification(
        name="close_return",
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="close return",
        algorithm=IndicatorDescriptor("simple_return", "1"),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=1,
        seed_policy=MLSeedPolicy.NONE,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )

    return MLDatasetSpecification(
        provenance=provenance,
        feature_set=MLFeatureSetSpecification(
            "1h",
            (feature,),
            implementation,
        ),
    )


def _observation(
    dataset: MLDatasetSpecification,
) -> MLObservation:
    return MLObservation(
        dataset,
        START + HOUR * 5,
        START + HOUR * 6,
    )


def _vector(
    dataset: MLDatasetSpecification,
    *,
    value: Decimal,
) -> MLFeatureVector:
    observation = _observation(dataset)

    dependency = MLCandleDependency(
        source=dataset.provenance.source,
        data_range=DataRange(
            START + HOUR * 3,
            START + HOUR * 6,
        ),
    )

    return MLFeatureVector(
        observation=observation,
        feature_set=dataset.feature_set,
        values=(value,),
        dependencies=(dependency,),
    )


def _specification(
    dataset: MLDatasetSpecification,
    *,
    threshold: Decimal = Decimal("2"),
) -> MLAnomalySpecification:
    return MLAnomalySpecification(
        feature_set=dataset.feature_set,
        feature_name="close_return",
        method=MLAnomalyMethod.ABSOLUTE_Z_SCORE,
        reference_method=MLAnomalyReferenceMethod.POPULATION_MEAN_STANDARD_DEVIATION,
        threshold=threshold,
        implementation=dataset.provenance.implementation,
    )


def _reference(
    dataset: MLDatasetSpecification,
    *,
    mean: Decimal = Decimal("0"),
    standard_deviation: Decimal = Decimal("0.01"),
    information_time: datetime = START + HOUR * 4,
) -> MLAnomalyReference:
    return MLAnomalyReference(
        specification=_specification(dataset),
        source=dataset.provenance.source,
        data_range=DataRange(
            START,
            START + HOUR * 4,
        ),
        mean=mean,
        standard_deviation=standard_deviation,
        sample_count=4,
        information_time=information_time,
        source_checksum="f" * 64,
    )


def test_anomaly_specification_binds_exact_decimal_feature() -> None:
    dataset = _dataset()

    specification = _specification(dataset)

    assert specification.feature_set == dataset.feature_set
    assert specification.feature_name == "close_return"
    assert specification.method is MLAnomalyMethod.ABSOLUTE_Z_SCORE
    assert (
        specification.reference_method
        is MLAnomalyReferenceMethod.POPULATION_MEAN_STANDARD_DEVIATION
    )
    assert specification.threshold == Decimal("2")


def test_unknown_feature_is_rejected() -> None:
    dataset = _dataset()

    with pytest.raises(Exception):
        MLAnomalySpecification(
            feature_set=dataset.feature_set,
            feature_name="unknown",
            method=MLAnomalyMethod.ABSOLUTE_Z_SCORE,
            reference_method=MLAnomalyReferenceMethod.POPULATION_MEAN_STANDARD_DEVIATION,
            threshold=Decimal("2"),
            implementation=dataset.provenance.implementation,
        )


@pytest.mark.parametrize(
    "threshold",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_invalid_threshold_is_rejected(
    threshold: Decimal,
) -> None:
    dataset = _dataset()

    with pytest.raises(Exception):
        _specification(
            dataset,
            threshold=threshold,
        )


@pytest.mark.parametrize(
    "standard_deviation",
    [
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_invalid_standard_deviation_is_rejected(
    standard_deviation: Decimal,
) -> None:
    dataset = _dataset()

    with pytest.raises(Exception):
        _reference(
            dataset,
            standard_deviation=standard_deviation,
        )


def test_reference_requires_at_least_two_samples() -> None:
    dataset = _dataset()

    with pytest.raises(Exception):
        MLAnomalyReference(
            specification=_specification(dataset),
            source=dataset.provenance.source,
            data_range=DataRange(
                START,
                START + HOUR * 4,
            ),
            mean=Decimal("0"),
            standard_deviation=Decimal("0.01"),
            sample_count=1,
            information_time=START + HOUR * 4,
            source_checksum="f" * 64,
        )


def test_reference_information_cannot_precede_reference_range() -> None:
    dataset = _dataset()

    with pytest.raises(Exception):
        _reference(
            dataset,
            information_time=START + HOUR * 3,
        )


def test_positive_z_score_anomaly_is_deterministic() -> None:
    dataset = _dataset()

    evidence = MLAnomalyEvidence(
        specification=_specification(dataset),
        reference=_reference(dataset),
        feature_vector=_vector(
            dataset,
            value=Decimal("0.03"),
        ),
    )

    assert evidence.observed_value == Decimal("0.03")
    assert evidence.signed_z_score == Decimal("3")
    assert evidence.absolute_z_score == Decimal("3")
    assert evidence.anomalous is True


def test_negative_z_score_anomaly_is_deterministic() -> None:
    dataset = _dataset()

    evidence = MLAnomalyEvidence(
        specification=_specification(dataset),
        reference=_reference(dataset),
        feature_vector=_vector(
            dataset,
            value=Decimal("-0.03"),
        ),
    )

    assert evidence.signed_z_score == Decimal("-3")
    assert evidence.absolute_z_score == Decimal("3")
    assert evidence.anomalous is True


def test_threshold_boundary_is_not_anomalous() -> None:
    dataset = _dataset()

    evidence = MLAnomalyEvidence(
        specification=_specification(
            dataset,
            threshold=Decimal("2"),
        ),
        reference=_reference(dataset),
        feature_vector=_vector(
            dataset,
            value=Decimal("0.02"),
        ),
    )

    assert evidence.absolute_z_score == Decimal("2")
    assert evidence.anomalous is False


def test_reference_cannot_arrive_after_observation() -> None:
    dataset = _dataset()

    reference = MLAnomalyReference(
        specification=_specification(dataset),
        source=dataset.provenance.source,
        data_range=DataRange(
            START,
            START + HOUR * 6,
        ),
        mean=Decimal("0"),
        standard_deviation=Decimal("0.01"),
        sample_count=6,
        information_time=START + HOUR * 7,
        source_checksum="f" * 64,
    )

    with pytest.raises(Exception):
        MLAnomalyEvidence(
            specification=_specification(dataset),
            reference=reference,
            feature_vector=_vector(
                dataset,
                value=Decimal("0.03"),
            ),
        )


def test_reference_from_different_dataset_identity_is_rejected() -> None:
    btc_dataset = _dataset(symbol="BTC/USDT")
    eth_dataset = _dataset(symbol="ETH/USDT")

    with pytest.raises(Exception):
        MLAnomalyEvidence(
            specification=_specification(btc_dataset),
            reference=MLAnomalyReference(
                specification=_specification(btc_dataset),
                source=eth_dataset.provenance.source,
                data_range=DataRange(
                    START,
                    START + HOUR * 4,
                ),
                mean=Decimal("0"),
                standard_deviation=Decimal("0.01"),
                sample_count=4,
                information_time=START + HOUR * 4,
                source_checksum="f" * 64,
            ),
            feature_vector=_vector(
                btc_dataset,
                value=Decimal("0.03"),
            ),
        )


def test_global_decimal_context_does_not_change_anomaly_result() -> None:
    dataset = _dataset()

    original_precision = getcontext().prec
    try:
        getcontext().prec = 6

        first = MLAnomalyEvidence(
            specification=_specification(dataset),
            reference=_reference(
                dataset,
                mean=Decimal("0.001"),
                standard_deviation=Decimal("0.003"),
            ),
            feature_vector=_vector(
                dataset,
                value=Decimal("0.007"),
            ),
        )

        getcontext().prec = 28

        second = MLAnomalyEvidence(
            specification=_specification(dataset),
            reference=_reference(
                dataset,
                mean=Decimal("0.001"),
                standard_deviation=Decimal("0.003"),
            ),
            feature_vector=_vector(
                dataset,
                value=Decimal("0.007"),
            ),
        )
    finally:
        getcontext().prec = original_precision

    assert first.signed_z_score == second.signed_z_score
    assert first.absolute_z_score == second.absolute_z_score
    assert first.anomalous is second.anomalous


def test_anomaly_contracts_round_trip_through_canonical_codec() -> None:
    dataset = _dataset()

    specification = _specification(dataset)
    reference = _reference(dataset)
    evidence = MLAnomalyEvidence(
        specification=specification,
        reference=reference,
        feature_vector=_vector(
            dataset,
            value=Decimal("0.03"),
        ),
    )

    for value in (
        specification,
        reference,
        evidence,
    ):
        encoded = encode_document(value)
        decoded = decode_document(encoded)

        assert decoded == value
        assert decoded.identity == value.identity
        assert decoded.checksum == value.checksum


def test_equal_inputs_produce_equal_anomaly_identity_and_checksum() -> None:
    dataset = _dataset()

    def build() -> MLAnomalyEvidence:
        return MLAnomalyEvidence(
            specification=_specification(dataset),
            reference=_reference(dataset),
            feature_vector=_vector(
                dataset,
                value=Decimal("0.03"),
            ),
        )

    first = build()
    second = build()

    assert first == second
    assert first.identity == second.identity
    assert first.checksum == second.checksum


def test_observed_value_change_changes_anomaly_evidence_checksum() -> None:
    dataset = _dataset()

    first = MLAnomalyEvidence(
        specification=_specification(dataset),
        reference=_reference(dataset),
        feature_vector=_vector(
            dataset,
            value=Decimal("0.01"),
        ),
    )
    second = MLAnomalyEvidence(
        specification=_specification(dataset),
        reference=_reference(dataset),
        feature_vector=_vector(
            dataset,
            value=Decimal("0.03"),
        ),
    )

    assert first.checksum != second.checksum
    assert first.absolute_z_score != second.absolute_z_score
    assert first.anomalous is False
    assert second.anomalous is True


def test_anomaly_module_exposes_no_confidence_recommendation_or_execution_authority() -> None:
    import app.ml.anomalies as anomalies

    forbidden = (
        "confidence",
        "recommend",
        "rank",
        "winner",
        "champion",
        "challenger",
        "approve",
        "deploy",
        "execute",
        "order",
        "paper",
        "live",
    )

    exported = tuple(name.lower() for name in vars(anomalies))

    for token in forbidden:
        assert not any(token in name for name in exported)


def test_anomaly_specification_requires_canonical_population_reference_method() -> None:
    dataset = _dataset()

    with pytest.raises(Exception):
        MLAnomalySpecification(
            feature_set=dataset.feature_set,
            feature_name="close_return",
            method=MLAnomalyMethod.ABSOLUTE_Z_SCORE,
            reference_method="SAMPLE_STANDARD_DEVIATION",  # type: ignore[arg-type]
            threshold=Decimal("2"),
            implementation=dataset.provenance.implementation,
        )
