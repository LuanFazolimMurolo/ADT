from __future__ import annotations

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
from app.ml.domain import (
    MLDatasetSpecification,
    MLFeatureSetSpecification,
    MLFeatureSpecification,
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
from app.ml.targets import compute_target
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


def _candles() -> tuple[Candle, ...]:
    return tuple(_candle(index, close=Decimal(str(100 + index))) for index in range(12))


def test_forward_close_return_horizon_2_contract() -> None:
    dataset = _dataset()
    observation = _observation(dataset)
    candles = _candles()

    anchor_index = 3
    baseline_close = candles[anchor_index].close
    future_start = observation.observation_time
    future_end = observation.observation_time + 2 * HOUR
    terminal_candle = candles[anchor_index + 2]

    assert baseline_close == Decimal("103")
    assert observation.candle_open_time == START + HOUR * anchor_index
    assert future_start == START + HOUR * (anchor_index + 1)
    assert future_end == START + HOUR * (anchor_index + 3)
    assert terminal_candle.open_time == observation.candle_open_time + 2 * HOUR
    assert terminal_candle.close == Decimal("105")

    expected_value = terminal_candle.close / baseline_close - Decimal("1")
    value, dependency = compute_target(dataset.target, candles, observation)

    assert value == expected_value
    assert dependency.data_range == DataRange(future_start, future_end)


def test_target_rejects_incomplete_future() -> None:
    dataset = _dataset()
    observation = _observation(dataset)
    with pytest.raises(ValueError):
        compute_target(dataset.target, _candles()[:4], observation)


def test_unsupported_target_rejected() -> None:
    dataset = _dataset()
    with pytest.raises(ValueError):
        compute_target(
            MLTargetSpecification(
                name="unsupported",
                algorithm=IndicatorDescriptor("unsupported_target", "1"),
                kind=MLValueKind.DECIMAL,
                unit="ratio",
                meaning="unsupported",
                horizon_candles=1,
                implementation=MLImplementationReference("adt-test-build", "1", "f" * 64),
            ),
            _candles(),
            _observation(dataset),
        )
