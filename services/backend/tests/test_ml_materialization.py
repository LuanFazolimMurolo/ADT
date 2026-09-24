from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
    MLProvenance,
    MLResetPolicy,
    MLSeedPolicy,
    MLTargetSpecification,
    MLTemporalSplit,
    MLValueKind,
)
from app.ml.materialization import MLDatasetMaterializationService
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


def _candles() -> tuple[Candle, ...]:
    return tuple(_candle(index, close=Decimal(str(100 + index))) for index in range(10))


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
    impl = MLImplementationReference("adt-test-build", "1", "e" * 64)
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


def _split(spec: MLDatasetSpecification) -> MLTemporalSplit:
    return MLTemporalSplit(
        spec,
        DataRange(START + HOUR, START + HOUR * 4),
        DataRange(START + HOUR * 4, START + HOUR * 7),
        DataRange(START + HOUR * 7, START + HOUR * 9),
        START + HOUR * 4,
        True,
    )


def test_materialization_is_deterministic() -> None:
    spec = _dataset()
    split = _split(spec)

    class Reader:
        def __init__(self) -> None:
            self.candles = _candles()

        def open_snapshot(self, snapshot_id: str):
            return type(
                "Snapshot",
                (),
                {"snapshot_id": snapshot_id, "checksum": spec.provenance.source.snapshot_checksum},
            )()

        def verify_unchanged(self):
            return None

        def iter_candles(self, data_range=None):
            if data_range is None:
                return self.candles
            return tuple(
                c for c in self.candles if data_range.start <= c.open_time < data_range.end
            )

    service = MLDatasetMaterializationService(reader_factory=lambda *_: Reader())
    result = service.build(spec, split, spec.feature_set.implementation)
    assert result.rows
    assert result.exclusions
    assert result.observation_count == len(result.rows)


def test_materialization_assigns_train_validation_test_roles() -> None:
    spec = _dataset()
    split = _split(spec)

    class Reader:
        def __init__(self) -> None:
            self.candles = _candles()

        def open_snapshot(self, snapshot_id: str):
            return type(
                "Snapshot",
                (),
                {"snapshot_id": snapshot_id, "checksum": spec.provenance.source.snapshot_checksum},
            )()

        def verify_unchanged(self):
            return None

        def iter_candles(self, data_range=None):
            if data_range is None:
                return self.candles
            return tuple(
                c for c in self.candles if data_range.start <= c.open_time < data_range.end
            )

    service = MLDatasetMaterializationService(reader_factory=lambda *_: Reader())
    result = service.build(spec, split, spec.feature_set.implementation)
    assert {row.role.value for row in result.rows} <= {"TRAIN", "VALIDATION", "TEST"}
