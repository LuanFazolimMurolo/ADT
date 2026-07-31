"""Deterministic Phase 2C quality, resampling, manifest and snapshot tests."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from uuid import uuid4

import httpx
import pyarrow.parquet as pq
import pytest
from pydantic import AnyHttpUrl, SecretStr

import app.market_data.derived as derived_module
from app.cli import EXIT_OK, main
from app.core.config import Settings
from app.market_data.advanced_quality import AdvancedMarketDataQualityScanner
from app.market_data.catalog import (
    DatasetMetadata,
    JsonMarketDataCatalog,
    dataset_key,
)
from app.market_data.datasets import (
    AdvancedQualityIssue,
    DatasetIdentity,
    DatasetKind,
    DatasetState,
    GapPolicy,
    QualityIssueCategory,
    QualityScanMode,
    QualityScanPlan,
    QualityScanScope,
)
from app.market_data.derived import DerivedDatasetService, DerivedDatasetStore
from app.market_data.domain import DataRange
from app.market_data.errors import MarketDataInconsistencyError, MarketDataStorageError
from app.market_data.locks import DatasetLockManager
from app.market_data.resampling import DeterministicCandleResampler
from app.market_data.snapshots import DatasetSnapshotService, MarketDatasetReader
from app.market_data.storage import (
    ParquetCandleStore,
    ParquetUpsertPlan,
    canonical_candle_bytes,
)
from app.market_data.timeframes import get_timeframe
from app.market_data.transaction import MarketDataTransactionCoordinator
from tests.market_data_helpers import INSTRUMENT, PAIR, candle, utc


class SimulatedCrash(BaseException):
    pass


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        supabase_url=AnyHttpUrl("https://project.example.test"),
        supabase_publishable_key=SecretStr("public-test"),
        supabase_database_url=SecretStr("postgresql://test@example.test/adt"),
        environment="test",
        data_dir=tmp_path,
        market_http_retries=0,
    )


def _persist_raw(tmp_path: Path, candles) -> tuple[ParquetCandleStore, JsonMarketDataCatalog]:
    rows = tuple(candles)
    store = ParquetCandleStore(tmp_path)
    receipt = store.upsert(rows)
    receipt.commit()
    catalog = JsonMarketDataCatalog(tmp_path, clock=lambda: utc(2030, 1, 1))
    timeframe = rows[0].timeframe
    key = dataset_key(INSTRUMENT, timeframe)
    digest = hashlib.sha256()
    all_rows = store.read(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        PAIR,
        timeframe,
        DataRange(rows[0].open_time, rows[-1].open_time + timeframe.duration),
    )
    for item in all_rows:
        digest.update(canonical_candle_bytes(item))
    first, last, count = store.first_last_count(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        PAIR,
        timeframe,
    )
    version = digest.hexdigest()
    started = catalog.start_run(key)
    completed = replace(
        started,
        status="COMPLETED",
        finished_at=utc(2030, 1, 1).isoformat(),
        fetched_count=len(rows),
        stored_count=len(rows),
    )
    metadata = DatasetMetadata(
        key=key,
        exchange=INSTRUMENT.exchange.value,
        market_type=INSTRUMENT.market_type.value,
        symbol=INSTRUMENT.symbol,
        native_symbol=INSTRUMENT.native_symbol,
        timeframe=timeframe.code,
        location=(
            Path("market")
            / store.dataset_root(
                INSTRUMENT.exchange,
                INSTRUMENT.market_type,
                PAIR,
                timeframe,
            ).relative_to(store.root)
        ).as_posix(),
        first_open_time=first.isoformat() if first else None,
        last_open_time=last.isoformat() if last else None,
        candle_count=count,
        version=version,
        updated_at=utc(2030, 1, 1).isoformat(),
    )
    transaction_id = uuid4().hex
    empty = ParquetUpsertPlan(
        transaction_id,
        (),
        0,
        0,
        first,
        last,
        count,
        version,
    )
    with catalog.acquire_lease() as lease:
        plan = catalog.prepare_completion(
            completed,
            metadata,
            transaction_id=transaction_id,
            lease=lease,
        )
        MarketDataTransactionCoordinator(store, catalog).execute(
            empty,
            plan,
            intended_version=version,
            catalog_lease=lease,
        )
    return store, catalog


def _derived_service(
    tmp_path: Path,
    store: ParquetCandleStore,
    catalog: JsonMarketDataCatalog,
    *,
    failure_hook=None,
    recovery_identity_hook=None,
):
    locks = DatasetLockManager(tmp_path, timeout_seconds=1, stale_after_seconds=60)
    derived_store = DerivedDatasetStore(tmp_path)
    service = DerivedDatasetService(
        raw_store=store,
        raw_catalog=catalog,
        derived_store=derived_store,
        lock_manager=locks,
        max_source_candles=10_000,
        max_groups=10_000,
        clock=lambda: utc(2030, 1, 1),
        failure_hook=failure_hook,
        recovery_identity_hook=recovery_identity_hook,
    )
    return service, derived_store, locks


def _concurrent_resample_worker(
    data_dir: Path,
    target_timeframe: str,
    start_event,
) -> None:
    start_event.wait(timeout=5)
    store = ParquetCandleStore(data_dir)
    catalog = JsonMarketDataCatalog(data_dir)
    service, _derived_store, _locks = _derived_service(data_dir, store, catalog)
    plan = service.plan(
        INSTRUMENT,
        "1m",
        target_timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=15)),
    )
    service.materialize(plan)


def _toctou_writer(
    data_dir: Path,
    prepared_event,
    identity_read_event,
    leave_committed_journal: bool,
) -> None:
    store = ParquetCandleStore(data_dir)
    catalog = JsonMarketDataCatalog(data_dir)

    def hook(step: str) -> None:
        if step == "promoted:0":
            prepared_event.set()
            identity_read_event.wait(timeout=10)
        if step == "committed" and leave_committed_journal:
            raise MarketDataStorageError()

    service, _derived_store, _locks = _derived_service(
        data_dir,
        store,
        catalog,
        failure_hook=hook,
    )
    plan = service.plan(
        INSTRUMENT,
        "1m",
        "5m",
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5)),
    )
    service.materialize(plan)


def _toctou_recoverer(
    data_dir: Path,
    prepared_event,
    identity_read_event,
) -> None:
    prepared_event.wait(timeout=10)
    store = ParquetCandleStore(data_dir)
    catalog = JsonMarketDataCatalog(data_dir)
    service, _derived_store, _locks = _derived_service(
        data_dir,
        store,
        catalog,
        recovery_identity_hook=lambda _path: identity_read_event.set(),
    )
    service.recover()


def test_resampling_uses_exact_ohlcv_and_optional_sums() -> None:
    source = get_timeframe("1m")
    target = get_timeframe("5m")
    rows = tuple(
        candle(
            utc(2026, 1, 1) + index * source.duration,
            timeframe=source,
            open_price=str(100 + index),
            high=str(110 + index),
            low=str(90 - index),
            close=str(105 + index),
            volume="0.1",
            quote_volume="0.2",
            trade_count=index + 1,
        )
        for index in range(5)
    )
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1m",
        DatasetKind.RAW,
        "raw",
        "native",
        1,
    )
    target_identity = replace(
        identity,
        timeframe="5m",
        kind=DatasetKind.DERIVED,
        source="derived:test",
    )
    from app.market_data.datasets import ResamplingPlan

    plan = ResamplingPlan(
        identity,
        target_identity,
        DataRange(rows[0].open_time, rows[-1].open_time + source.duration),
        "a" * 64,
        "a" * 64,
        5,
        1,
        1,
        5,
        GapPolicy.STRICT,
        "CONTINUOUS_UTC_24_7",
    )
    result = DeterministicCandleResampler().resample(
        rows,
        plan,
        source_timeframe=source,
        target_timeframe=target,
    )
    aggregated = result.candles[0]
    assert aggregated.open == Decimal("100")
    assert aggregated.high == Decimal("114")
    assert aggregated.low == Decimal("86")
    assert aggregated.close == Decimal("109")
    assert aggregated.volume == Decimal("0.5")
    assert aggregated.quote_volume == Decimal("1.0")
    assert aggregated.trade_count == 15
    assert aggregated.close_time == utc(2026, 1, 1) + target.duration - timedelta(milliseconds=1)


def test_resampling_gap_policies_and_timeframe_compatibility() -> None:
    source = get_timeframe("1m")
    rows = (
        candle(utc(2026, 1, 1), timeframe=source),
        candle(utc(2026, 1, 1) + 2 * source.duration, timeframe=source),
    )
    base = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1m",
        DatasetKind.RAW,
        "raw",
        "native",
        1,
    )
    from app.market_data.datasets import ResamplingPlan

    def plan(policy: GapPolicy) -> ResamplingPlan:
        return ResamplingPlan(
            base,
            replace(base, kind=DatasetKind.DERIVED, timeframe="5m", source="derived"),
            DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5)),
            "a" * 64,
            "a" * 64,
            5,
            1,
            1,
            5,
            policy,
            "CONTINUOUS_UTC_24_7",
        )

    resampler = DeterministicCandleResampler()
    with pytest.raises(MarketDataInconsistencyError, match="incompleto"):
        resampler.resample(
            rows,
            plan(GapPolicy.STRICT),
            source_timeframe=source,
            target_timeframe=get_timeframe("5m"),
        )
    skipped = resampler.resample(
        rows,
        plan(GapPolicy.SKIP_INCOMPLETE),
        source_timeframe=source,
        target_timeframe=get_timeframe("5m"),
    )
    assert skipped.candles == ()
    assert len(skipped.skipped_ranges) == 1
    with pytest.raises(MarketDataInconsistencyError):
        resampler.validate_timeframes(get_timeframe("5m"), get_timeframe("1m"))


@pytest.mark.parametrize(
    ("source_code", "target_code"),
    (
        ("1m", "5m"),
        ("1m", "15m"),
        ("1m", "30m"),
        ("1m", "1h"),
        ("5m", "15m"),
        ("5m", "30m"),
        ("5m", "1h"),
        ("15m", "30m"),
        ("15m", "1h"),
        ("30m", "1h"),
        ("1h", "4h"),
        ("1h", "1d"),
        ("4h", "1d"),
    ),
)
def test_supported_resampling_matrix(source_code: str, target_code: str) -> None:
    source = get_timeframe(source_code)
    target = get_timeframe(target_code)
    group_size = target.duration // source.duration
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source)
        for index in range(group_size)
    )
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        source_code,
        DatasetKind.RAW,
        "raw",
        "native",
        1,
    )
    from app.market_data.datasets import ResamplingPlan

    plan = ResamplingPlan(
        identity,
        replace(
            identity,
            timeframe=target_code,
            kind=DatasetKind.DERIVED,
            source="derived:test",
        ),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + target.duration),
        "a" * 64,
        "a" * 64,
        group_size,
        1,
        1,
        group_size,
        GapPolicy.STRICT,
        "CONTINUOUS_UTC_24_7",
    )
    result = DeterministicCandleResampler().resample(
        rows,
        plan,
        source_timeframe=source,
        target_timeframe=target,
    )
    assert len(result.candles) == 1
    assert result.candles[0].open_time == utc(2026, 1, 1)
    assert result.candles[0].close_time == (
        utc(2026, 1, 1) + target.duration - timedelta(milliseconds=1)
    )


def test_entirely_missing_or_open_group_is_never_materialized() -> None:
    source = get_timeframe("1m")
    target = get_timeframe("5m")
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1m",
        DatasetKind.RAW,
        "raw",
        "native",
        1,
    )
    from app.market_data.datasets import ResamplingPlan

    plan = ResamplingPlan(
        identity,
        replace(identity, timeframe="5m", kind=DatasetKind.DERIVED, source="derived"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + target.duration),
        "a" * 64,
        "a" * 64,
        5,
        1,
        1,
        5,
        GapPolicy.STRICT,
        "CONTINUOUS_UTC_24_7",
    )
    resampler = DeterministicCandleResampler()
    with pytest.raises(MarketDataInconsistencyError, match="incompleto"):
        resampler.resample(
            (),
            plan,
            source_timeframe=source,
            target_timeframe=target,
        )
    rows = tuple(
        candle(
            utc(2026, 1, 1) + index * source.duration,
            timeframe=source,
            is_closed=index != 4,
        )
        for index in range(5)
    )
    skipped = resampler.resample(
        rows,
        replace(plan, gap_policy=GapPolicy.MARK_INCOMPLETE),
        source_timeframe=source,
        target_timeframe=target,
    )
    assert skipped.candles == ()
    assert skipped.skipped_ranges == (plan.data_range,)


def test_full_and_incremental_quality_scan_are_ordered(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        (
            candle(utc(2026, 1, 1), timeframe=timeframe),
            candle(utc(2026, 1, 1, 1), timeframe=timeframe),
        ),
    )
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1h",
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )
    scanner = AdvancedMarketDataQualityScanner(
        store=store,
        catalog=catalog,
        clock=lambda: utc(2030, 1, 1),
    )
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2))
    full = scanner.scan(QualityScanPlan(identity, QualityScanMode.FULL, data_range))
    assert full.is_valid
    assert full.coverage.observed_count == 2
    incremental = scanner.scan(
        QualityScanPlan(
            identity,
            QualityScanMode.INCREMENTAL,
            data_range,
            baseline=full.baseline,
        )
    )
    assert incremental.changed_partitions == ()
    assert tuple(
        (item.category.value, item.code, item.partition or "") for item in full.issues
    ) == tuple(
        sorted((item.category.value, item.code, item.partition or "") for item in full.issues)
    )


def test_quality_detects_catalog_checksum_and_operational_artifacts(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        (candle(utc(2026, 1, 1), timeframe=timeframe),),
    )
    state = json.loads(catalog.path.read_text(encoding="utf-8"))
    state["datasets"][dataset_key(INSTRUMENT, timeframe)]["version"] = "0" * 64
    state["receipts"]["corrupt"] = {"dataset_key": dataset_key(INSTRUMENT, timeframe)}
    catalog.path.write_text(json.dumps(state), encoding="utf-8")
    abandoned = store.root / ".transactions" / f"journal-{'a' * 32}.json"
    abandoned.parent.mkdir(parents=True, exist_ok=True)
    abandoned.write_text("{}", encoding="utf-8")
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1h",
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )
    result = AdvancedMarketDataQualityScanner(
        store=store,
        catalog=catalog,
        clock=lambda: utc(2030, 1, 1),
    ).scan(
        QualityScanPlan(
            identity,
            QualityScanMode.FULL,
            None,
        )
    )
    assert {
        "logical_checksum_divergence",
        "receipt_divergence",
        "abandoned_journal",
    } <= {item.code for item in result.issues}


def test_quality_detects_corrupt_schema_and_missing_partition(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        (candle(utc(2026, 1, 1), timeframe=timeframe),),
    )
    path = store.partition_paths(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        PAIR,
        timeframe,
    )[0]
    path.write_bytes(b"not parquet")
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1h",
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )
    result = AdvancedMarketDataQualityScanner(store=store, catalog=catalog).scan(
        QualityScanPlan(
            identity,
            QualityScanMode.FULL,
            DataRange(utc(2026, 1, 1), utc(2026, 3, 1)),
        )
    )
    codes = {item.code for item in result.issues}
    assert "parquet_schema_corrupt" in codes
    assert "missing_partition" in codes


def test_raw_to_derived_manifest_snapshot_and_lazy_reader(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(60)
    )
    store, catalog = _persist_raw(tmp_path, rows)
    service, derived_store, locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1))
    plan = service.plan(INSTRUMENT, "1m", "1h", data_range)
    raw_before = tuple(
        path.read_bytes()
        for path in store.partition_paths(INSTRUMENT.exchange, INSTRUMENT.market_type, PAIR, source)
    )
    result = service.materialize(plan)
    manifest = service.verify(plan)
    assert result.materialized_count == 1
    assert manifest.state is DatasetState.COMPLETE
    assert manifest.lineage.source_dataset_version == plan.source_dataset_version
    assert (
        tuple(
            path.read_bytes()
            for path in store.partition_paths(
                INSTRUMENT.exchange, INSTRUMENT.market_type, PAIR, source
            )
        )
        == raw_before
    )

    snapshots = DatasetSnapshotService(
        data_dir=tmp_path,
        derived_store=derived_store,
        derived_service=service,
        lock_manager=locks,
        max_partitions=10,
    )
    first = snapshots.create(plan, data_range)
    assert snapshots.create(plan, data_range) == first
    reader = MarketDatasetReader(tmp_path)
    reader.open_snapshot(first.snapshot_id)
    iterator = reader.iter_candles()
    assert iter(iterator) is iterator
    assert tuple(iterator) == result.candles
    first_candle, last_candle, count = reader.first_last_count()
    assert first_candle == last_candle == result.candles[0]
    assert count == 1


def test_derived_failure_rolls_back_previous_manifest(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(5)
    )
    store, catalog = _persist_raw(tmp_path, rows)
    healthy, derived_store, _locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5))
    plan = healthy.plan(INSTRUMENT, "1m", "5m", data_range)
    healthy.materialize(plan)
    original = derived_store.manifest_path(plan).read_bytes()

    def fail(step: str) -> None:
        if step == "promoted:0":
            raise MarketDataStorageError()

    failing, _store, _locks = _derived_service(
        tmp_path,
        store,
        catalog,
        failure_hook=fail,
    )
    # Force a transaction instead of the stable logical NOOP path.
    changed_plan = replace(plan, source_dataset_version=plan.source_dataset_version)
    manifest = derived_store.load_manifest(derived_store.manifest_path(plan))
    derived_store.write_manifest_atomic(
        derived_store.manifest_path(plan),
        replace(manifest, source_dataset_version="f" * 64),
    )
    with pytest.raises(MarketDataStorageError):
        failing.materialize(changed_plan)
    assert derived_store.manifest_path(plan).read_bytes() != original
    assert failing.recover() == 0


@pytest.mark.parametrize(
    "failure_step",
    (
        "before_prepare:0",
        "prepared:0",
        "before_promote:0",
        "promoted:0",
        "before_committed",
    ),
)
def test_failure_before_durable_commit_rolls_back_everything(
    tmp_path: Path,
    failure_step: str,
) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(5)
    )
    store, catalog = _persist_raw(tmp_path, rows)

    def fail(step: str) -> None:
        if step == failure_step:
            raise MarketDataStorageError()

    service, derived_store, _locks = _derived_service(
        tmp_path,
        store,
        catalog,
        failure_hook=fail,
    )
    plan = service.plan(
        INSTRUMENT,
        "1m",
        "5m",
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5)),
    )
    with pytest.raises(MarketDataStorageError):
        service.materialize(plan)
    assert not derived_store.manifest_path(plan).exists()
    assert not tuple(derived_store.root.rglob("*.parquet"))
    assert service.recover() == 0


def test_crash_recovery_and_post_commit_cleanup_are_idempotent(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(5)
    )
    store, catalog = _persist_raw(tmp_path, rows)

    def crash(step: str) -> None:
        if step == "promoted:0":
            raise SimulatedCrash

    crashing, derived_store, _locks = _derived_service(
        tmp_path,
        store,
        catalog,
        failure_hook=crash,
    )
    plan = crashing.plan(
        INSTRUMENT,
        "1m",
        "5m",
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5)),
    )
    with pytest.raises(SimulatedCrash):
        crashing.materialize(plan)
    assert crashing.recover() == 1
    assert crashing.recover() == 0
    assert not derived_store.manifest_path(plan).exists()

    def cleanup_failure(step: str) -> None:
        if step == "committed":
            raise MarketDataStorageError()

    committed, _derived_store, _locks = _derived_service(
        tmp_path,
        store,
        catalog,
        failure_hook=cleanup_failure,
    )
    committed.materialize(plan)
    assert (
        derived_store.load_manifest(derived_store.manifest_path(plan)).state
        is DatasetState.COMPLETE
    )
    assert committed.recover() == 1
    assert committed.recover() == 0


def test_derived_journal_cannot_target_raw_dataset(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(5)
    )
    store, catalog = _persist_raw(tmp_path, rows)

    def crash(step: str) -> None:
        if step == "promoted:0":
            raise SimulatedCrash

    service, derived_store, _locks = _derived_service(
        tmp_path,
        store,
        catalog,
        failure_hook=crash,
    )
    plan = service.plan(
        INSTRUMENT,
        "1m",
        "5m",
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5)),
    )
    with pytest.raises(SimulatedCrash):
        service.materialize(plan)
    journal = next((derived_store.root / ".transactions").glob("journal-*.json"))
    raw = json.loads(journal.read_text(encoding="utf-8"))
    raw["dataset_root"] = (
        store.dataset_root(
            INSTRUMENT.exchange,
            INSTRUMENT.market_type,
            PAIR,
            source,
        )
        .relative_to(store.root)
        .as_posix()
    )
    journal.write_text(json.dumps(raw), encoding="utf-8")
    before = {path: path.read_bytes() for path in derived_store.root.rglob("*") if path.is_file()}

    with pytest.raises(MarketDataStorageError):
        service.recover()

    assert {path: path.read_bytes() for path in before} == before


def test_recovery_ignores_journal_removed_before_identity_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetCandleStore(tmp_path)
    catalog = JsonMarketDataCatalog(tmp_path)
    service, derived_store, _locks = _derived_service(tmp_path, store, catalog)
    journal_dir = derived_store.root / ".transactions"
    journal_dir.mkdir(parents=True)
    journal = journal_dir / f"journal-{'a' * 32}.json"
    journal.write_text("{}", encoding="utf-8")
    original = derived_module._read_journal_identity

    def remove_then_read(path: Path) -> tuple[str, str, str]:
        path.unlink()
        return original(path)

    monkeypatch.setattr(derived_module, "_read_journal_identity", remove_then_read)

    assert service.recover() == 0


def test_incremental_noop_is_stable_and_source_change_is_stale(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(5)
    )
    store, catalog = _persist_raw(tmp_path, rows)
    service, derived_store, _locks = _derived_service(tmp_path, store, catalog)
    plan = service.plan(
        INSTRUMENT,
        "1m",
        "5m",
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5)),
    )
    service.materialize(plan)
    path = derived_store.manifest_path(plan)
    before = path.read_bytes()
    service.materialize_incremental(plan)
    assert path.read_bytes() == before

    state = json.loads(catalog.path.read_text(encoding="utf-8"))
    state["datasets"][dataset_key(INSTRUMENT, source)]["version"] = "f" * 64
    catalog.path.write_text(json.dumps(state), encoding="utf-8")
    assert service.verify(plan).state is DatasetState.STALE


def test_manifest_corruption_and_snapshot_partition_limit(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(5)
    )
    store, catalog = _persist_raw(tmp_path, rows)
    service, derived_store, locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5))
    plan = service.plan(INSTRUMENT, "1m", "5m", data_range)
    service.materialize(plan)
    snapshots = DatasetSnapshotService(
        data_dir=tmp_path,
        derived_store=derived_store,
        derived_service=service,
        lock_manager=locks,
        max_partitions=0,
    )
    with pytest.raises(MarketDataInconsistencyError, match="limite"):
        snapshots.create(plan, data_range)
    manifest_path = derived_store.manifest_path(plan)
    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataStorageError):
        derived_store.load_manifest(manifest_path)


def test_snapshot_rejects_corrupt_copied_manifest(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(5)
    )
    store, catalog = _persist_raw(tmp_path, rows)
    service, derived_store, locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5))
    plan = service.plan(INSTRUMENT, "1m", "5m", data_range)
    service.materialize(plan)
    snapshots = DatasetSnapshotService(
        data_dir=tmp_path,
        derived_store=derived_store,
        derived_service=service,
        lock_manager=locks,
        max_partitions=10,
    )
    snapshot = snapshots.create(plan, data_range)
    copied = store.root / "snapshots" / snapshot.snapshot_id / "dataset-manifest.json"
    envelope = json.loads(copied.read_text(encoding="utf-8"))
    envelope["checksum"] = "0" * 64
    copied.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(MarketDataStorageError):
        MarketDatasetReader(tmp_path).open_snapshot(snapshot.snapshot_id)


def test_phase2c_cli_workflow_is_local_and_safe(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    _persist_raw(
        tmp_path,
        tuple(
            candle(utc(2026, 1, 1) + index * source.duration, timeframe=source)
            for index in range(5)
        ),
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError(f"unexpected HTTP request: {request.method}")

    common = [
        "--symbol",
        "BTC/USDT",
        "--source-timeframe",
        "1m",
        "--target-timeframe",
        "5m",
        "--start",
        "2026-01-01T00:00:00Z",
        "--end",
        "2026-01-01T00:05:00Z",
    ]
    transport = httpx.MockTransport(handler)
    settings = _settings(tmp_path)

    for command in (
        ["market-data", "resample", "plan", *common],
        ["market-data", "resample", "run", *common, "--dry-run"],
        ["market-data", "resample", "run", *common, "--yes"],
        ["market-data", "resample", "verify", *common],
    ):
        output = StringIO()
        assert (
            main(
                command,
                app_settings=settings,
                transport=transport,
                stdout=output,
            )
            == EXIT_OK
        )
        assert json.loads(output.getvalue())

    quality_output = StringIO()
    assert (
        main(
            [
                "market-data",
                "quality",
                "scan",
                "--symbol",
                "BTC/USDT",
                "--timeframe",
                "1m",
                "--mode",
                "FULL",
                "--start",
                "2026-01-01T00:00:00Z",
                "--end",
                "2026-01-01T00:05:00Z",
            ],
            app_settings=settings,
            transport=transport,
            stdout=quality_output,
        )
        == EXIT_OK
    )
    raw_quality = json.loads(quality_output.getvalue())
    assert raw_quality["baseline"] is not None
    raw_baseline = settings.data_dir / "market" / raw_quality["baseline"]
    assert raw_baseline.is_file()

    before_derived_quality = set((settings.data_dir / "market" / "quality-baselines").glob("*"))
    derived_quality_output = StringIO()
    assert (
        main(
            [
                "market-data",
                "quality",
                "scan",
                "--symbol",
                "BTC/USDT",
                "--timeframe",
                "5m",
                "--dataset-kind",
                "DERIVED",
                "--source-timeframe",
                "1m",
                "--mode",
                "FULL",
                "--scope",
                "FULL_DATASET",
                "--start",
                "2026-01-01T00:00:00Z",
                "--end",
                "2026-01-01T00:05:00Z",
            ],
            app_settings=settings,
            transport=transport,
            stdout=derived_quality_output,
        )
        == EXIT_OK
    )
    assert json.loads(derived_quality_output.getvalue())["baseline"] is None
    assert set((settings.data_dir / "market" / "quality-baselines").glob("*")) == (
        before_derived_quality
    )

    snapshot_output = StringIO()
    assert (
        main(
            ["market-data", "snapshot", "create", *common],
            app_settings=settings,
            transport=transport,
            stdout=snapshot_output,
        )
        == EXIT_OK
    )
    snapshot_id = json.loads(snapshot_output.getvalue())["snapshot_id"]
    for operation in ("inspect", "verify"):
        assert (
            main(
                [
                    "market-data",
                    "snapshot",
                    operation,
                    "--snapshot-id",
                    snapshot_id,
                ],
                app_settings=settings,
                transport=transport,
                stdout=StringIO(),
            )
            == EXIT_OK
        )
    assert calls == 0


@pytest.mark.parametrize("_repetition", range(3))
def test_two_derived_datasets_complete_without_deadlock(
    tmp_path: Path,
    _repetition: int,
) -> None:
    source = get_timeframe("1m")
    _persist_raw(
        tmp_path,
        tuple(
            candle(utc(2026, 1, 1) + index * source.duration, timeframe=source)
            for index in range(15)
        ),
    )
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(
            target=_concurrent_resample_worker,
            args=(tmp_path, target, start_event),
        )
        for target in ("5m", "15m")
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    manifests = tuple((tmp_path / "market" / "derived").rglob("manifest.json"))
    assert len(manifests) == 2


@pytest.mark.parametrize("leave_committed_journal", (False, True))
def test_recovery_rereads_journal_after_waiting_for_locks(
    tmp_path: Path,
    leave_committed_journal: bool,
) -> None:
    source = get_timeframe("1m")
    _persist_raw(
        tmp_path,
        tuple(
            candle(utc(2026, 1, 1) + index * source.duration, timeframe=source)
            for index in range(5)
        ),
    )
    context = multiprocessing.get_context("spawn")
    prepared = context.Event()
    identity_read = context.Event()
    writer = context.Process(
        target=_toctou_writer,
        args=(tmp_path, prepared, identity_read, leave_committed_journal),
    )
    recoverer = context.Process(
        target=_toctou_recoverer,
        args=(tmp_path, prepared, identity_read),
    )
    writer.start()
    recoverer.start()
    writer.join(timeout=20)
    recoverer.join(timeout=20)
    assert writer.exitcode == 0
    assert recoverer.exitcode == 0
    store = ParquetCandleStore(tmp_path)
    catalog = JsonMarketDataCatalog(tmp_path)
    service, derived_store, _locks = _derived_service(tmp_path, store, catalog)
    plan = service.plan(
        INSTRUMENT,
        "1m",
        "5m",
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5)),
    )
    assert service.verify(plan).state is DatasetState.COMPLETE
    assert not tuple((derived_store.root / ".transactions").glob("journal-*.json"))


def test_materialize_recovers_pending_derived_journal_without_cli(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    rows = tuple(
        candle(utc(2026, 1, 1) + index * source.duration, timeframe=source) for index in range(5)
    )
    store, catalog = _persist_raw(tmp_path, rows)

    def crash(step: str) -> None:
        if step == "promoted:0":
            raise SimulatedCrash

    crashing, _derived_store, _locks = _derived_service(
        tmp_path, store, catalog, failure_hook=crash
    )
    plan = crashing.plan(
        INSTRUMENT,
        "1m",
        "5m",
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(minutes=5)),
    )
    with pytest.raises(SimulatedCrash):
        crashing.materialize(plan)
    healthy, _derived_store, _locks = _derived_service(tmp_path, store, catalog)
    assert healthy.materialize(plan).materialized_count == 1
    assert healthy.verify(plan).state is DatasetState.COMPLETE


def test_incremental_baseline_preserves_global_state_and_partition_issue(
    tmp_path: Path,
) -> None:
    timeframe = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        (
            candle(utc(2026, 1, 31, 23), timeframe=timeframe),
            candle(utc(2026, 2, 1, 1), timeframe=timeframe),
        ),
    )
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1h",
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )
    scanner = AdvancedMarketDataQualityScanner(store=store, catalog=catalog)
    full = scanner.scan(
        QualityScanPlan(
            identity,
            QualityScanMode.FULL,
            scope=QualityScanScope.FULL_DATASET,
        )
    )
    assert full.baseline is not None
    first_partition = full.baseline.partitions[0]
    retained_error = AdvancedQualityIssue(
        "retained_error",
        "ERROR",
        QualityIssueCategory.CONTENT,
        first_partition.summary.relative_path,
    )
    baseline = replace(
        full.baseline,
        partitions=(
            replace(
                first_partition,
                issues=(*first_partition.issues, retained_error),
            ),
            *full.baseline.partitions[1:],
        ),
    )
    unchanged = scanner.scan(
        QualityScanPlan(
            identity,
            QualityScanMode.INCREMENTAL,
            scope=QualityScanScope.FULL_DATASET,
            baseline=baseline,
        )
    )
    assert unchanged.changed_partitions == ()
    assert unchanged.coverage == full.coverage
    assert unchanged.logical_checksum == full.logical_checksum
    assert "retained_error" in {item.code for item in unchanged.issues}
    assert not unchanged.is_valid

    second_path = store.root / full.partitions[1].relative_path
    table = pq.ParquetFile(second_path).read()
    pq.write_table(table, second_path, compression=None)
    changed = scanner.scan(
        QualityScanPlan(
            identity,
            QualityScanMode.INCREMENTAL,
            scope=QualityScanScope.FULL_DATASET,
            baseline=full.baseline,
        )
    )
    assert changed.changed_partitions == (full.partitions[1].relative_path,)
    assert changed.coverage.observed_count == 2
    assert changed.coverage.internal_gap_count == 1


def test_incremental_rejects_incompatible_baseline(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        (candle(utc(2026, 1, 1), timeframe=timeframe),),
    )
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1h",
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )
    scanner = AdvancedMarketDataQualityScanner(store=store, catalog=catalog)
    full = scanner.scan(QualityScanPlan(identity, QualityScanMode.FULL))
    assert full.baseline is not None
    invalid = replace(
        full.baseline,
        scanner_version="another-version",
    )
    with pytest.raises(MarketDataInconsistencyError, match="baseline"):
        scanner.scan(
            QualityScanPlan(
                identity,
                QualityScanMode.INCREMENTAL,
                baseline=invalid,
            )
        )


def test_range_filters_rows_outside_half_open_interval(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        tuple(candle(utc(2026, 1, 1, hour), timeframe=timeframe) for hour in range(4)),
    )
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1h",
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )
    result = AdvancedMarketDataQualityScanner(store=store, catalog=catalog).scan(
        QualityScanPlan(
            identity,
            QualityScanMode.FULL,
            DataRange(utc(2026, 1, 1, 1), utc(2026, 1, 1, 3)),
            scope=QualityScanScope.RANGE,
        )
    )
    assert result.coverage.observed_count == 2
    assert result.coverage.first_open_time == utc(2026, 1, 1, 1).isoformat()
    assert result.coverage.last_open_time == utc(2026, 1, 1, 2).isoformat()
    assert not {
        "catalog_storage_divergence",
        "logical_checksum_divergence",
    } & {item.code for item in result.issues}


def test_full_dataset_ignores_range_used_only_as_cli_context(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        tuple(candle(utc(2026, 1, 1, hour), timeframe=timeframe) for hour in range(4)),
    )
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1h",
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )
    result = AdvancedMarketDataQualityScanner(store=store, catalog=catalog).scan(
        QualityScanPlan(
            identity,
            QualityScanMode.FULL,
            DataRange(utc(2026, 1, 1, 1), utc(2026, 1, 1, 3)),
            scope=QualityScanScope.FULL_DATASET,
        )
    )
    assert result.coverage.observed_count == 4
    assert result.coverage.expected_count is None
    assert result.coverage.first_open_time == utc(2026, 1, 1).isoformat()
    assert result.coverage.last_open_time == utc(2026, 1, 1, 3).isoformat()


def test_incremental_rejects_baseline_with_inconsistent_coverage(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        (candle(utc(2026, 1, 1), timeframe=timeframe),),
    )
    identity = DatasetIdentity(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        INSTRUMENT.symbol,
        "1h",
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )
    scanner = AdvancedMarketDataQualityScanner(store=store, catalog=catalog)
    full = scanner.scan(QualityScanPlan(identity, QualityScanMode.FULL))
    assert full.baseline is not None
    invalid = replace(
        full.baseline,
        coverage=replace(full.baseline.coverage, observed_count=2),
    )
    with pytest.raises(MarketDataInconsistencyError, match="baseline"):
        scanner.scan(
            QualityScanPlan(
                identity,
                QualityScanMode.INCREMENTAL,
                baseline=invalid,
            )
        )


def test_derived_quality_reports_stale_lineage(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    store, catalog = _persist_raw(
        tmp_path,
        tuple(
            candle(utc(2026, 1, 1) + index * source.duration, timeframe=source)
            for index in range(5)
        ),
    )
    service, _derived_store, _locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(
        utc(2026, 1, 1),
        utc(2026, 1, 1) + timedelta(minutes=5),
    )
    plan = service.plan(INSTRUMENT, "1m", "5m", data_range)
    service.materialize(plan)
    scanner = AdvancedMarketDataQualityScanner(
        store=store,
        catalog=catalog,
        derived_service=service,
    )
    healthy = scanner.scan(
        QualityScanPlan(
            plan.target,
            QualityScanMode.FULL,
            resampling_plan=plan,
        )
    )
    assert healthy.is_valid
    state = json.loads(catalog.path.read_text(encoding="utf-8"))
    state["datasets"][dataset_key(INSTRUMENT, source)]["version"] = "f" * 64
    catalog.path.write_text(json.dumps(state), encoding="utf-8")
    stale = scanner.scan(
        QualityScanPlan(
            plan.target,
            QualityScanMode.FULL,
            resampling_plan=plan,
        )
    )
    assert "derived_source_stale" in {item.code for item in stale.issues}


@pytest.mark.parametrize("corruption", ("schema", "partition_count", "global_bounds"))
def test_derived_verify_rejects_manifest_summary_divergence(
    tmp_path: Path,
    corruption: str,
) -> None:
    source = get_timeframe("1m")
    store, catalog = _persist_raw(
        tmp_path,
        tuple(
            candle(utc(2026, 1, 1) + index * source.duration, timeframe=source)
            for index in range(5)
        ),
    )
    service, derived_store, _locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(
        utc(2026, 1, 1),
        utc(2026, 1, 1) + timedelta(minutes=5),
    )
    plan = service.plan(INSTRUMENT, "1m", "5m", data_range)
    service.materialize(plan)
    path = derived_store.manifest_path(plan)
    manifest = derived_store.load_manifest(path)
    if corruption == "schema":
        corrupted = replace(manifest, schema_version=manifest.schema_version + 1)
    elif corruption == "partition_count":
        corrupted = replace(
            manifest,
            partitions=(
                replace(
                    manifest.partitions[0],
                    candle_count=manifest.partitions[0].candle_count + 1,
                ),
            ),
        )
    else:
        corrupted = replace(manifest, first_open_time=utc(2026, 1, 2).isoformat())
    derived_store.write_manifest_atomic(path, corrupted)

    if corruption == "schema":
        with pytest.raises(MarketDataStorageError, match="schema"):
            service.verify(plan)
    else:
        assert service.verify(plan).state is DatasetState.INVALID


def test_snapshot_rejects_outside_coverage_and_corrupt_idempotent_copy(
    tmp_path: Path,
) -> None:
    source = get_timeframe("1m")
    store, catalog = _persist_raw(
        tmp_path,
        tuple(
            candle(utc(2026, 1, 1) + index * source.duration, timeframe=source)
            for index in range(5)
        ),
    )
    service, derived_store, locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(
        utc(2026, 1, 1),
        utc(2026, 1, 1) + timedelta(minutes=5),
    )
    plan = service.plan(INSTRUMENT, "1m", "5m", data_range)
    service.materialize(plan)
    snapshots = DatasetSnapshotService(
        data_dir=tmp_path,
        derived_store=derived_store,
        derived_service=service,
        lock_manager=locks,
        max_partitions=10,
        clock=lambda: utc(2031, 1, 1),
    )
    with pytest.raises(MarketDataInconsistencyError, match="cobertura"):
        snapshots.create(
            plan,
            DataRange(
                utc(2025, 12, 31, 23) + timedelta(minutes=55),
                utc(2026, 1, 1) + timedelta(minutes=5),
            ),
        )
    snapshot = snapshots.create(plan, data_range)
    assert snapshot.created_at == utc(2031, 1, 1).isoformat()
    partition = store.root / "snapshots" / snapshot.snapshot_id / snapshot.partitions[0]
    partition.write_bytes(b"corrupt")
    with pytest.raises((MarketDataStorageError, MarketDataInconsistencyError)):
        snapshots.create(plan, data_range)


def test_snapshot_idempotency_rejects_incomplete_partition_metadata(tmp_path: Path) -> None:
    source = get_timeframe("1m")
    store, catalog = _persist_raw(
        tmp_path,
        tuple(
            candle(
                utc(2026, 1, 31, 23) + timedelta(minutes=55) + index * source.duration,
                timeframe=source,
            )
            for index in range(10)
        ),
    )
    service, derived_store, locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(
        utc(2026, 1, 31, 23) + timedelta(minutes=55),
        utc(2026, 2, 1) + timedelta(minutes=5),
    )
    plan = service.plan(INSTRUMENT, "1m", "5m", data_range)
    service.materialize(plan)
    snapshots = DatasetSnapshotService(
        data_dir=tmp_path,
        derived_store=derived_store,
        derived_service=service,
        lock_manager=locks,
        max_partitions=10,
    )
    snapshot = snapshots.create(plan, data_range)
    metadata = store.root / "snapshots" / snapshot.snapshot_id / "snapshot.json"
    envelope = json.loads(metadata.read_text(encoding="utf-8"))
    payload = envelope["snapshot"]
    payload["partitions"] = payload["partitions"][:1]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    envelope["checksum"] = hashlib.sha256(encoded).hexdigest()
    metadata.write_text(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(MarketDataInconsistencyError, match="partições"):
        snapshots.create(plan, data_range)


def test_reader_detects_metadata_and_manifest_change_after_last_yield(
    tmp_path: Path,
) -> None:
    source = get_timeframe("1m")
    store, catalog = _persist_raw(
        tmp_path,
        tuple(
            candle(utc(2026, 1, 1) + index * source.duration, timeframe=source)
            for index in range(5)
        ),
    )
    service, derived_store, locks = _derived_service(tmp_path, store, catalog)
    data_range = DataRange(
        utc(2026, 1, 1),
        utc(2026, 1, 1) + timedelta(minutes=5),
    )
    plan = service.plan(INSTRUMENT, "1m", "5m", data_range)
    service.materialize(plan)
    snapshots = DatasetSnapshotService(
        data_dir=tmp_path,
        derived_store=derived_store,
        derived_service=service,
        lock_manager=locks,
        max_partitions=10,
    )
    snapshot = snapshots.create(plan, data_range)
    snapshot_root = store.root / "snapshots" / snapshot.snapshot_id
    for changed_path in (
        snapshot_root / "snapshot.json",
        snapshot_root / "dataset-manifest.json",
    ):
        reader = MarketDatasetReader(tmp_path)
        reader.open_snapshot(snapshot.snapshot_id)
        iterator = reader.iter_candles()
        next(iterator)
        changed_path.write_bytes(changed_path.read_bytes() + b" ")
        with pytest.raises(MarketDataInconsistencyError, match="final|manifest"):
            next(iterator)
        changed_path.write_bytes(changed_path.read_bytes()[:-1])


def test_partition_estimate_counts_every_crossed_month(tmp_path: Path) -> None:
    source = get_timeframe("1h")
    store, catalog = _persist_raw(
        tmp_path,
        (candle(utc(2026, 1, 1), timeframe=source),),
    )
    service, _derived_store, _locks = _derived_service(tmp_path, store, catalog)
    plan = service.plan(
        INSTRUMENT,
        "1h",
        "1d",
        DataRange(utc(2026, 1, 1), utc(2026, 4, 1)),
    )
    assert plan.estimated_partitions == 3
