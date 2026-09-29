from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.indicators.domain import IndicatorDescriptor
from app.market_data.datasets import (
    DatasetIdentity,
    DatasetKind,
    DatasetManifest,
    DatasetSnapshot,
    DatasetState,
    GapPolicy,
    PartitionSummary,
)
from app.market_data.domain import Candle, DataRange, Exchange, MarketType, Timeframe
from app.market_data.snapshots import build_snapshot_id
from app.ml.documents import encode_document, to_document
from app.ml.domain import (
    MLContract,
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
    MLTargetSpecification,
    MLValueKind,
)
from app.ml.predictions import (
    MLModelReference,
    MLPredictionProblemType,
    MLPredictionSpecification,
    MLRegressionPrediction,
)
from app.optimization.temporal_domain import TemporalCoverage, TemporalSnapshotReference

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _timeframe() -> Timeframe:
    return Timeframe("1h", HOUR)


def _candle(index: int, *, close: Decimal) -> Candle:
    t = START + HOUR * index
    return Candle(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTC/USDT",
        timeframe=_timeframe(),
        open_time=t,
        close_time=t + HOUR,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("10"),
        quote_volume=None,
        trade_count=None,
        is_closed=True,
        source="binance-public",
    )


def _dataset() -> MLDatasetSpecification:
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
        lineage=__import__("app.market_data.datasets", fromlist=["DatasetLineage"]).DatasetLineage(
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
    snapshot_range = DataRange(START, START + HOUR * 12)
    snapshot = DatasetSnapshot(
        snapshot_id=build_snapshot_id(manifest, snapshot_range),
        dataset_key=identity.key,
        dataset_version="v1",
        checksum="a" * 64,
        data_range=snapshot_range,
        partitions=("partitions/year=2026/month=01/candles.parquet",),
        manifest_path="dataset-manifest.json",
        created_at=START.isoformat(),
    )
    impl = MLImplementationReference("adt-test-build", "1", "e" * 64)
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
    target = MLTargetSpecification(
        name="forward_close_return_v1",
        algorithm=IndicatorDescriptor("forward_close_return_v1", "1"),
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="future close return",
        horizon_candles=2,
        implementation=impl,
    )
    provenance = MLProvenance(
        TemporalSnapshotReference(
            snapshot_id=snapshot.snapshot_id,
            snapshot_checksum=snapshot.checksum,
            dataset_key=snapshot.dataset_key,
            dataset_version=snapshot.dataset_version,
            identity=identity,
            gap_policy=GapPolicy.STRICT,
            available_coverage=TemporalCoverage(snapshot.data_range, "1h", 12),
        ),
        selected_range=DataRange(START + HOUR, START + HOUR * 11),
        context_range=DataRange(START, START + HOUR * 12),
        implementation=impl,
    )
    return MLDatasetSpecification(
        provenance,
        MLFeatureSetSpecification("1h", (feature,), impl),
        target,
    )


def _observation(dataset: MLDatasetSpecification) -> MLObservation:
    return MLObservation(dataset, START + HOUR * 3, START + HOUR * 4)


def _vector(dataset: MLDatasetSpecification, observation: MLObservation) -> MLFeatureVector:
    value = Decimal("100.0")
    dependency = __import__("app.ml.domain", fromlist=["MLCandleDependency"]).MLCandleDependency(
        source=dataset.provenance.source,
        data_range=DataRange(START, START + HOUR * 3),
    )
    return MLFeatureVector(observation, dataset.feature_set, (value,), (dependency,))


def _model_reference() -> MLModelReference:
    return MLModelReference(
        model_id="model-demo",
        model_version="v1",
        artifact_checksum="a" * 64,
        implementation=MLImplementationReference("adt-model-build", "1", "b" * 64),
    )


def test_model_reference_contract() -> None:
    reference = _model_reference()
    assert reference.model_id == "model-demo"
    assert reference.model_version == "v1"
    assert reference.artifact_checksum == "a" * 64
    assert reference.implementation.name == "adt-model-build"


def test_prediction_specification_contract() -> None:
    dataset = _dataset()
    observation = _observation(dataset)
    model_reference = _model_reference()
    feature_vector = _vector(dataset, observation)
    spec = MLPredictionSpecification(
        problem_type=MLPredictionProblemType.REGRESSION,
        target=dataset.target,
        feature_set=dataset.feature_set,
        implementation=dataset.provenance.implementation,
        model_reference=model_reference,
        feature_vector=feature_vector,
    )
    assert spec.problem_type is MLPredictionProblemType.REGRESSION
    assert spec.target == dataset.target
    assert spec.feature_set == dataset.feature_set
    assert spec.output_kind is MLValueKind.DECIMAL
    assert spec.output_unit == dataset.target.unit
    assert spec.horizon_candles == dataset.target.horizon_candles
    assert spec.model_reference == model_reference
    assert spec.feature_vector == feature_vector


def test_prediction_time_and_maturity() -> None:
    dataset = _dataset()
    observation = _observation(dataset)
    feature_vector = _vector(dataset, observation)
    specification = MLPredictionSpecification(
        problem_type=MLPredictionProblemType.REGRESSION,
        target=dataset.target,
        feature_set=dataset.feature_set,
        implementation=dataset.provenance.implementation,
        model_reference=_model_reference(),
        feature_vector=feature_vector,
    )
    prediction = MLRegressionPrediction(
        prediction_specification=specification,
        model_reference=_model_reference(),
        observation=observation,
        feature_vector=feature_vector,
        prediction_time=observation.observation_time,
        output=Decimal("0.0125"),
    )
    assert prediction.prediction_time == observation.observation_time
    assert prediction.feature_vector.observation == observation
    assert prediction.output == Decimal("0.0125")
    assert prediction.prediction_time < __import__(
        "app.ml.domain", fromlist=["target_maturity"]
    ).target_maturity(observation)


def test_prediction_identity_excludes_output_value() -> None:
    dataset = _dataset()
    observation = _observation(dataset)
    feature_vector = _vector(dataset, observation)
    specification = MLPredictionSpecification(
        problem_type=MLPredictionProblemType.REGRESSION,
        target=dataset.target,
        feature_set=dataset.feature_set,
        implementation=dataset.provenance.implementation,
        model_reference=_model_reference(),
        feature_vector=feature_vector,
    )
    first = MLRegressionPrediction(
        prediction_specification=specification,
        model_reference=_model_reference(),
        observation=observation,
        feature_vector=feature_vector,
        prediction_time=observation.observation_time,
        output=Decimal("0.01"),
    )
    second = MLRegressionPrediction(
        prediction_specification=specification,
        model_reference=_model_reference(),
        observation=observation,
        feature_vector=feature_vector,
        prediction_time=observation.observation_time,
        output=Decimal("0.02"),
    )
    assert first.identity == second.identity
    first_document = to_document(first)
    second_document = to_document(second)
    assert first_document["identity"] == second_document["identity"]
    assert first_document["document_checksum"] != second_document["document_checksum"]


def test_document_round_trip_and_nonfinite_output_rejected() -> None:
    dataset = _dataset()
    observation = _observation(dataset)
    feature_vector = _vector(dataset, observation)
    specification = MLPredictionSpecification(
        problem_type=MLPredictionProblemType.REGRESSION,
        target=dataset.target,
        feature_set=dataset.feature_set,
        implementation=dataset.provenance.implementation,
        model_reference=_model_reference(),
        feature_vector=feature_vector,
    )
    prediction = MLRegressionPrediction(
        prediction_specification=specification,
        model_reference=_model_reference(),
        observation=observation,
        feature_vector=feature_vector,
        prediction_time=observation.observation_time,
        output=Decimal("0.10"),
    )
    payload = encode_document(prediction)
    assert payload
    with pytest.raises(Exception):
        MLRegressionPrediction(
            prediction_specification=specification,
            model_reference=_model_reference(),
            observation=observation,
            feature_vector=feature_vector,
            prediction_time=observation.observation_time,
            output=Decimal("NaN"),
        )


def test_clean_import_order_for_prediction_contracts() -> None:
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    commands = (
        "import app.ml.predictions; print('predictions-first-import: PASS')",
        "import app.ml.domain; import app.ml.predictions; print('domain-first-import: PASS')",
        "import app.ml.documents; import app.ml.predictions; print('documents-first-import: PASS')",
    )
    for command in commands:
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "PASS" in result.stdout


def test_validate_contract_rejects_arbitrary_mlcontract_subclass() -> None:
    class ArbitraryContract(MLContract):
        value: int = 1

    with pytest.raises(Exception):
        from app.ml.domain import validate_contract

        validate_contract(ArbitraryContract())


def test_prediction_contracts_round_trip_via_canonical_document_codec() -> None:
    dataset = _dataset()
    observation = _observation(dataset)
    feature_vector = _vector(dataset, observation)
    specification = MLPredictionSpecification(
        problem_type=MLPredictionProblemType.REGRESSION,
        target=dataset.target,
        feature_set=dataset.feature_set,
        implementation=dataset.provenance.implementation,
        model_reference=_model_reference(),
        feature_vector=feature_vector,
    )

    model_reference = _model_reference()
    round_trip_values = (
        model_reference,
        specification,
        MLRegressionPrediction(
            prediction_specification=specification,
            model_reference=model_reference,
            observation=observation,
            feature_vector=feature_vector,
            prediction_time=observation.observation_time,
            output=Decimal("0.125"),
        ),
    )

    for value in round_trip_values:
        encoded = encode_document(value)
        decoder = __import__("app.ml.documents", fromlist=["decode_document"])
        decoded = decoder.decode_document(encoded)
        assert decoded == value
        assert decoded.identity == value.identity
        assert decoded.checksum == value.checksum
