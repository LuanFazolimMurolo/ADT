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
    MLTemporalSplit,
    MLValueKind,
)
from app.ml.features import compute_feature
from app.ml.materialization import MLDatasetMaterializationService
from app.optimization.temporal_domain import TemporalCoverage, TemporalSnapshotReference

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _timeframe() -> Timeframe:
    return Timeframe("1h", HOUR)


def _candle(index: int, *, close: Decimal) -> Candle:
    t = START + timedelta(hours=index)
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
        last_open_time=(START + HOUR * 9).isoformat(),
        candle_count=10,
        partitions=(
            PartitionSummary(
                relative_path="derived/year=2026/month=01/candles.parquet",
                year=2026,
                month=1,
                candle_count=10,
                first_open_time=START.isoformat(),
                last_open_time=(START + HOUR * 9).isoformat(),
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
    snapshot_range = DataRange(START, START + HOUR * 10)
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
        name="close_ema",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA of close",
        algorithm=IndicatorDescriptor("ema", "1", (("period", 3),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=2,
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
        horizon_candles=1,
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
            available_coverage=TemporalCoverage(snapshot.data_range, "1h", 10),
        ),
        selected_range=DataRange(START + HOUR, START + HOUR * 9),
        context_range=DataRange(START, START + HOUR * 10),
        implementation=impl,
    )
    return MLDatasetSpecification(
        provenance,
        MLFeatureSetSpecification("1h", (feature,), impl),
        target,
    )


def _observation(dataset: MLDatasetSpecification, *, candle_index: int) -> MLObservation:
    open_time = START + HOUR * candle_index
    return MLObservation(dataset, open_time, open_time + HOUR)


def _candles() -> tuple[Candle, ...]:
    return tuple(_candle(index, close=Decimal(str(100 + index))) for index in range(10))


def test_raw_close_extraction() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=2)
    candles = _candles()
    raw_close = MLFeatureSpecification(
        name="raw_close",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="raw close value",
        algorithm=IndicatorDescriptor("close", "1"),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=1,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    value, dependency = compute_feature(
        raw_close,
        candles,
        observation,
        dataset.provenance.context_range,
    )
    assert value == Decimal("102")
    assert dependency.source == dataset.provenance.source
    assert dependency.data_range.start == START
    assert dependency.data_range.end == observation.observation_time


def test_ema_recomputation() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=4)
    candles = _candles()
    feature = dataset.feature_set.features[0]
    value, _ = compute_feature(feature, candles, observation, dataset.provenance.context_range)
    assert value is not None
    assert value == Decimal("103")


def test_rsi_and_atr_recomputation() -> None:
    dataset = _dataset()
    candles = _candles()
    observation = _observation(dataset, candle_index=4)
    rsi_spec = MLFeatureSpecification(
        name="rsi_close",
        kind=MLValueKind.DECIMAL,
        unit="ratio",
        meaning="RSI of close",
        algorithm=IndicatorDescriptor("rsi", "1", (("period", 3),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=5,
        warmup_candles=2,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    atr_spec = MLFeatureSpecification(
        name="atr_close",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="ATR of close",
        algorithm=IndicatorDescriptor("atr", "1", (("period", 3),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=5,
        warmup_candles=2,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    rsi_value, _ = compute_feature(rsi_spec, candles, observation, dataset.provenance.context_range)
    atr_value, _ = compute_feature(atr_spec, candles, observation, dataset.provenance.context_range)
    assert rsi_value is not None
    assert atr_value is not None


def test_bollinger_and_macd_outputs() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=5)
    candles = _candles()
    bollinger = MLFeatureSpecification(
        name="bollinger_middle",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="Bollinger middle",
        algorithm=IndicatorDescriptor(
            "bollinger_middle",
            "1",
            (("period", 3), ("standard_deviations", Decimal("2"))),
        ),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=5,
        warmup_candles=2,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    macd = MLFeatureSpecification(
        name="macd_signal",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="MACD signal",
        algorithm=IndicatorDescriptor(
            "macd_signal",
            "1",
            (("fast_period", 2), ("slow_period", 3), ("signal_period", 2)),
        ),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=5,
        warmup_candles=4,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    mid, _ = compute_feature(bollinger, candles, observation, dataset.provenance.context_range)
    signal, _ = compute_feature(macd, candles, observation, dataset.provenance.context_range)
    assert mid is not None
    assert signal is not None


def test_unsupported_recipe_is_rejected() -> None:
    dataset = _dataset()
    candles = _candles()
    feature = MLFeatureSpecification(
        name="unsupported",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="unsupported",
        algorithm=IndicatorDescriptor("unknown_recipe", "1"),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=1,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    with pytest.raises(ValueError):
        compute_feature(
            feature,
            candles,
            _observation(dataset, candle_index=4),
            dataset.provenance.context_range,
        )


def test_same_timeframe_enforced() -> None:
    dataset = _dataset()
    feature = MLFeatureSpecification(
        name="close_ema",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA of close",
        algorithm=IndicatorDescriptor("ema", "1", (("period", 3),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=2,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    bad_timeframe = Timeframe("30m", timedelta(minutes=30))
    invalid_candles = tuple(
        Candle(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            symbol="BTC/USDT",
            timeframe=bad_timeframe,
            open_time=START + timedelta(minutes=30 * index),
            close_time=START + timedelta(minutes=30 * (index + 1)),
            open=Decimal(str(100 + index)),
            high=Decimal(str(101 + index)),
            low=Decimal(str(99 + index)),
            close=Decimal(str(100 + index)),
            volume=Decimal("10"),
            quote_volume=None,
            trade_count=None,
            is_closed=True,
            source="binance-public",
        )
        for index in range(5)
    )
    with pytest.raises(ValueError):
        compute_feature(
            feature,
            invalid_candles,
            _observation(dataset, candle_index=4),
            dataset.provenance.context_range,
        )


def test_warmup_stays_unavailable() -> None:
    dataset = _dataset()
    candles = _candles()
    observation = _observation(dataset, candle_index=1)
    feature = MLFeatureSpecification(
        name="close_ema",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA of close",
        algorithm=IndicatorDescriptor("ema", "1", (("period", 3),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=2,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    value, _ = compute_feature(feature, candles, observation, dataset.provenance.context_range)
    assert value is None


def test_feature_dependency_matches_consumed_range() -> None:
    dataset = _dataset()
    observation = _observation(dataset, candle_index=5)
    candles = _candles()
    feature = dataset.feature_set.features[0]
    value, dependency = compute_feature(
        feature,
        candles,
        observation,
        dataset.provenance.context_range,
    )
    assert dependency.data_range == DataRange(START + HOUR * 3, observation.observation_time)
    assert value is not None


def test_service_produces_deterministic_materialization() -> None:
    dataset = _dataset()
    split = MLTemporalSplit(
        dataset,
        DataRange(START + HOUR, START + HOUR * 4),
        DataRange(START + HOUR * 4, START + HOUR * 7),
        DataRange(START + HOUR * 7, START + HOUR * 9),
        START + HOUR * 4,
        True,
    )

    class Reader:
        def __init__(self) -> None:
            self.candles = _candles()

        def open_snapshot(self, snapshot_id: str):
            return type(
                "Snapshot",
                (),
                {
                    "snapshot_id": snapshot_id,
                    "checksum": dataset.provenance.source.snapshot_checksum,
                },
            )()

        def iter_candles(self, data_range=None):
            if data_range is None:
                return self.candles
            return tuple(
                candle
                for candle in self.candles
                if data_range.start <= candle.open_time < data_range.end
            )

        def verify_unchanged(self):
            return None

    service = MLDatasetMaterializationService(reader_factory=lambda *_: Reader())
    materialization = service.build(dataset, split, dataset.feature_set.implementation)
    assert materialization.rows
    assert materialization.exclusions
