"""Authoritative RAW partition manifest, backfill and bounded-read regressions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from app.market_data import storage as storage_module
from app.market_data.candle_query import LocalMarketCandleReadService, MarketCandlePageQuery
from app.market_data.catalog import JsonMarketDataCatalog, dataset_key
from app.market_data.domain import Candle, DataRange
from app.market_data.errors import MarketDataInconsistencyError
from app.market_data.integrity import (
    LEGACY_RAW_DATASET_VERSION_ALGORITHM,
    RAW_DATASET_VERSION_ALGORITHM,
    RAW_PARTITION_INTEGRITY_SCHEMA_VERSION,
    RawPartitionIntegrityEntry,
    RawPartitionIntegrityManifest,
)
from app.market_data.locks import DatasetLockManager
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.services import HistoricalMarketDataService
from app.market_data.storage import (
    ParquetCandleStore,
    compose_raw_dataset_version,
)
from app.market_data.timeframes import get_timeframe
from app.market_data.transaction import MarketDataTransactionCoordinator
from tests.market_data_helpers import INSTRUMENT, PAIR, candle, utc
from tests.test_market_candle_query import FixedCandleAdapter, ingest_cataloged_candles


def _history_service(
    data_dir: Path,
    *,
    candles: tuple[Candle, ...] = (),
    coordinator: MarketDataTransactionCoordinator | None = None,
    lock_manager: DatasetLockManager | None = None,
    store: ParquetCandleStore | None = None,
    catalog: JsonMarketDataCatalog | None = None,
) -> HistoricalMarketDataService:
    selected_store = store or ParquetCandleStore(data_dir)
    selected_catalog = catalog or JsonMarketDataCatalog(data_dir)
    return HistoricalMarketDataService(
        adapter=FixedCandleAdapter(candles),
        store=selected_store,
        catalog=selected_catalog,
        validator=MarketDataQualityValidator(clock=lambda: utc(2025, 1, 1)),
        max_fetch_candles=10_000,
        coordinator=coordinator,
        clock=lambda: utc(2025, 1, 1),
        lock_manager=lock_manager,
    )


def _rewrite_catalog(
    data_dir: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    catalog_path = JsonMarketDataCatalog(data_dir).path
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    mutation(payload)
    catalog_path.write_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _remove_manifest(data_dir: Path) -> None:
    key = dataset_key(INSTRUMENT, get_timeframe("1h"))

    def mutation(payload: dict[str, object]) -> None:
        datasets = payload["datasets"]
        assert isinstance(datasets, dict)
        metadata = datasets[key]
        assert isinstance(metadata, dict)
        metadata.pop("partition_integrity", None)

    _rewrite_catalog(data_dir, mutation)


def _tamper_partition(
    data_dir: Path,
    *,
    index: int,
    close: str | None = None,
    volume: str | None = None,
) -> Path:
    store = ParquetCandleStore(data_dir)
    timeframe = get_timeframe("1h")
    path = store.partition_paths(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        PAIR,
        timeframe,
    )[index]
    rows = store.read_partition(
        path,
        exchange=INSTRUMENT.exchange,
        market_type=INSTRUMENT.market_type,
        pair=PAIR,
        timeframe=timeframe,
    )
    changed = replace(
        rows[0],
        close=Decimal(close) if close is not None else rows[0].close,
        volume=Decimal(volume) if volume is not None else rows[0].volume,
    )
    pq.write_table(
        storage_module._candles_to_table((changed, *rows[1:])),
        path,
        compression="zstd",
    )
    assert store.verify_schema(path)
    return path


def test_manifest_contract_rejects_malformed_duplicate_and_unsorted_entries() -> None:
    first_path = (
        "exchange=binance/market=spot/base=BTC/quote=USDT/"
        "timeframe=1h/year=2024/month=01/candles.parquet"
    )
    second_path = first_path.replace("month=01", "month=02")
    first = RawPartitionIntegrityEntry(first_path, "1" * 64)
    second = RawPartitionIntegrityEntry(second_path, "2" * 64)

    for relative_path, checksum in (
        ("../candles.parquet", "1" * 64),
        (first_path.replace("month=01", "month=13"), "1" * 64),
        (first_path, "A" * 64),
        (first_path, "1" * 63),
    ):
        with pytest.raises(MarketDataInconsistencyError):
            RawPartitionIntegrityEntry(relative_path, checksum)

    for entries in ((first, first), (second, first)):
        with pytest.raises(MarketDataInconsistencyError):
            RawPartitionIntegrityManifest(
                schema_version=RAW_PARTITION_INTEGRITY_SCHEMA_VERSION,
                bound_dataset_version="3" * 64,
                checksum_algorithm=RAW_DATASET_VERSION_ALGORITHM,
                entries=entries,
            )


@pytest.mark.asyncio
async def test_write_path_publishes_complete_deterministic_manifest(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    first = utc(2024, 1, 31, 22)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(first),),
        start=first,
        end=first + timeframe.duration,
    )
    metadata_one = JsonMarketDataCatalog(tmp_path).get_dataset(dataset_key(INSTRUMENT, timeframe))
    assert metadata_one is not None and metadata_one.partition_integrity is not None
    first_checksum = metadata_one.partition_integrity.entries[0].checksum

    second = first + timeframe.duration
    await ingest_cataloged_candles(
        tmp_path,
        (candle(second),),
        start=second,
        end=second + timeframe.duration,
    )
    metadata_two = JsonMarketDataCatalog(tmp_path).get_dataset(dataset_key(INSTRUMENT, timeframe))
    assert metadata_two is not None and metadata_two.partition_integrity is not None
    assert metadata_two.partition_integrity.entries[0].checksum != first_checksum

    third = second + timeframe.duration
    await ingest_cataloged_candles(
        tmp_path,
        (candle(third),),
        start=third,
        end=third + timeframe.duration,
    )
    metadata_three = JsonMarketDataCatalog(tmp_path).get_dataset(dataset_key(INSTRUMENT, timeframe))
    assert metadata_three is not None and metadata_three.partition_integrity is not None
    entries = metadata_three.partition_integrity.entries
    assert len(entries) == 2
    assert entries[0].checksum == metadata_two.partition_integrity.entries[0].checksum
    assert tuple(entry.relative_path for entry in entries) == tuple(
        sorted(entry.relative_path for entry in entries)
    )
    assert (
        compose_raw_dataset_version((entry.relative_path, entry.checksum) for entry in entries)
        == metadata_three.version
    )
    assert metadata_three.partition_integrity.bound_dataset_version == metadata_three.version


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (("close", "106.00000000"), ("volume", "3.50000000")),
)
async def test_structurally_valid_financial_tamper_fails_closed(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    opening = utc(2024, 1, 1)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening),),
        start=opening,
        end=opening + timedelta(hours=1),
    )
    query = MarketCandlePageQuery(pair=PAIR, timeframe=get_timeframe("1h"), limit=1)
    service = LocalMarketCandleReadService(tmp_path)
    intact = service.read_page(query)
    _tamper_partition(
        tmp_path,
        index=0,
        close=value if field == "close" else None,
        volume=value if field == "volume" else None,
    )

    with pytest.raises(MarketDataInconsistencyError):
        service.read_page(query)

    metadata = JsonMarketDataCatalog(tmp_path).list_datasets()[0]
    assert metadata.version == intact.dataset_version
    assert len(intact.content_checksum) == 64


@pytest.mark.asyncio
async def test_bounded_page_ignores_untouched_tamper_until_navigation_reaches_it(
    tmp_path: Path,
) -> None:
    opening = utc(2024, 1, 31, 23)
    candles = (candle(opening), candle(opening + timedelta(hours=1)))
    await ingest_cataloged_candles(
        tmp_path,
        candles,
        start=opening,
        end=opening + timedelta(hours=2),
    )
    _tamper_partition(tmp_path, index=0, close="106.00000000")
    service = LocalMarketCandleReadService(tmp_path)
    timeframe = get_timeframe("1h")

    latest = service.read_page(MarketCandlePageQuery(pair=PAIR, timeframe=timeframe, limit=1))
    assert latest.candles == candles[1:]
    with pytest.raises(MarketDataInconsistencyError):
        service.read_page(
            MarketCandlePageQuery(
                pair=PAIR,
                timeframe=timeframe,
                before=opening + timedelta(hours=1),
                limit=1,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("tampered_index", (0, 1))
async def test_multi_partition_page_authenticates_every_touched_partition(
    tmp_path: Path,
    tampered_index: int,
) -> None:
    opening = utc(2024, 1, 31, 23)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening), candle(opening + timedelta(hours=1))),
        start=opening,
        end=opening + timedelta(hours=2),
    )
    _tamper_partition(tmp_path, index=tampered_index, volume="3.50000000")

    with pytest.raises(MarketDataInconsistencyError):
        LocalMarketCandleReadService(tmp_path).read_page(
            MarketCandlePageQuery(pair=PAIR, timeframe=get_timeframe("1h"), limit=2)
        )


@pytest.mark.asyncio
async def test_intact_read_is_deterministic_and_each_touched_file_is_read_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opening = utc(2024, 1, 31, 23)
    candles = (candle(opening), candle(opening + timedelta(hours=1)))
    await ingest_cataloged_candles(
        tmp_path,
        candles,
        start=opening,
        end=opening + timedelta(hours=2),
    )
    reads: list[Path] = []
    original = ParquetCandleStore._read_file

    def tracked_read(
        store: ParquetCandleStore,
        path: Path,
        **kwargs: object,
    ) -> tuple[Candle, ...]:
        reads.append(path)
        return original(store, path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ParquetCandleStore, "_read_file", tracked_read)
    query = MarketCandlePageQuery(pair=PAIR, timeframe=get_timeframe("1h"), limit=2)
    service = LocalMarketCandleReadService(tmp_path)

    first = service.read_page(query)
    assert len(reads) == 2
    reads.clear()
    second = service.read_page(query)
    assert len(reads) == 2
    assert first == second


@pytest.mark.asyncio
async def test_missing_manifest_fails_closed_without_global_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opening = utc(2024, 1, 1)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening),),
        start=opening,
        end=opening + timedelta(hours=1),
    )
    _remove_manifest(tmp_path)

    def fail_global_scan(*_args: object, **_kwargs: object) -> str:
        pytest.fail("HTTP must not recompute the complete RAW dataset version")

    monkeypatch.setattr(ParquetCandleStore, "logical_version", fail_global_scan)
    with pytest.raises(MarketDataInconsistencyError):
        LocalMarketCandleReadService(tmp_path).read_page(
            MarketCandlePageQuery(pair=PAIR, timeframe=get_timeframe("1h"), limit=1)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_kind", ("missing", "extra", "foreign"))
async def test_missing_extra_or_foreign_manifest_entry_fails_closed(
    tmp_path: Path,
    mutation_kind: str,
) -> None:
    opening = utc(2024, 1, 1)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening),),
        start=opening,
        end=opening + timedelta(hours=1),
    )
    key = dataset_key(INSTRUMENT, get_timeframe("1h"))

    def mutation(payload: dict[str, object]) -> None:
        datasets = payload["datasets"]
        assert isinstance(datasets, dict)
        metadata = datasets[key]
        assert isinstance(metadata, dict)
        manifest = metadata["partition_integrity"]
        assert isinstance(manifest, dict)
        entries = manifest["entries"]
        assert isinstance(entries, list) and len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, dict)
        if mutation_kind == "missing":
            entries.clear()
        elif mutation_kind == "extra":
            extra = dict(entry)
            extra["relative_path"] = str(entry["relative_path"]).replace(
                "month=01",
                "month=02",
            )
            extra["checksum"] = "0" * 64
            entries.append(extra)
        else:
            entry["relative_path"] = str(entry["relative_path"]).replace(
                "base=BTC",
                "base=ETH",
            )

    _rewrite_catalog(tmp_path, mutation)
    with pytest.raises(MarketDataInconsistencyError):
        LocalMarketCandleReadService(tmp_path).read_page(
            MarketCandlePageQuery(pair=PAIR, timeframe=get_timeframe("1h"), limit=1)
        )


@pytest.mark.asyncio
async def test_current_backfill_is_idempotent_and_preserves_dataset_and_parquet(
    tmp_path: Path,
) -> None:
    opening = utc(2024, 1, 31, 23)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening), candle(opening + timedelta(hours=1))),
        start=opening,
        end=opening + timedelta(hours=2),
    )
    catalog = JsonMarketDataCatalog(tmp_path)
    before = catalog.list_datasets()[0]
    paths = ParquetCandleStore(tmp_path).partition_paths(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        PAIR,
        get_timeframe("1h"),
    )
    parquet_hashes = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)
    _remove_manifest(tmp_path)

    service = _history_service(tmp_path)
    created = service.backfill_partition_integrity(INSTRUMENT, get_timeframe("1h"))
    repeated = service.backfill_partition_integrity(INSTRUMENT, get_timeframe("1h"))
    after = catalog.list_datasets()[0]

    assert created.action == "CREATED"
    assert repeated.action == "NOOP"
    assert after.version == before.version
    assert after.version_algorithm == before.version_algorithm
    assert after.candle_count == before.candle_count
    assert after.first_open_time == before.first_open_time
    assert after.last_open_time == before.last_open_time
    assert after.updated_at == before.updated_at
    assert after.partition_integrity is not None
    assert (
        compose_raw_dataset_version(
            (entry.relative_path, entry.checksum) for entry in after.partition_integrity.entries
        )
        == after.version
    )
    assert tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths) == parquet_hashes


@pytest.mark.asyncio
async def test_backfill_rejects_tamper_without_any_catalog_mutation(tmp_path: Path) -> None:
    opening = utc(2024, 1, 1)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening),),
        start=opening,
        end=opening + timedelta(hours=1),
    )
    _remove_manifest(tmp_path)
    _tamper_partition(tmp_path, index=0, close="106.00000000")
    catalog_path = JsonMarketDataCatalog(tmp_path).path
    before = catalog_path.read_bytes()

    with pytest.raises(MarketDataInconsistencyError):
        _history_service(tmp_path).backfill_partition_integrity(INSTRUMENT, get_timeframe("1h"))

    assert catalog_path.read_bytes() == before


@pytest.mark.asyncio
async def test_legacy_backfill_recomputes_exact_legacy_identity_without_changing_it(
    tmp_path: Path,
) -> None:
    opening = utc(2024, 1, 31, 23)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening), candle(opening + timedelta(hours=1))),
        start=opening,
        end=opening + timedelta(hours=2),
    )
    snapshot = ParquetCandleStore(tmp_path).partition_integrity_snapshot(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        PAIR,
        get_timeframe("1h"),
    )
    key = dataset_key(INSTRUMENT, get_timeframe("1h"))

    def mutation(payload: dict[str, object]) -> None:
        datasets = payload["datasets"]
        assert isinstance(datasets, dict)
        metadata = datasets[key]
        assert isinstance(metadata, dict)
        metadata.pop("partition_integrity", None)
        metadata["version"] = snapshot.legacy_version
        metadata["version_algorithm"] = LEGACY_RAW_DATASET_VERSION_ALGORITHM

    _rewrite_catalog(tmp_path, mutation)
    result = _history_service(tmp_path).backfill_partition_integrity(
        INSTRUMENT,
        get_timeframe("1h"),
    )
    metadata = JsonMarketDataCatalog(tmp_path).get_dataset(key)

    assert result.action == "CREATED"
    assert metadata is not None and metadata.partition_integrity is not None
    assert metadata.version == snapshot.legacy_version
    assert metadata.version_algorithm == LEGACY_RAW_DATASET_VERSION_ALGORITHM
    assert metadata.partition_integrity.bound_dataset_version == snapshot.legacy_version


class SimulatedCrash(BaseException):
    pass


@pytest.mark.asyncio
async def test_prepared_recovery_restores_version_and_manifest_together(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    opening = utc(2024, 1, 31, 23)
    await ingest_cataloged_candles(
        tmp_path,
        (candle(opening),),
        start=opening,
        end=opening + timeframe.duration,
    )
    catalog = JsonMarketDataCatalog(tmp_path)
    before = catalog.get_dataset(dataset_key(INSTRUMENT, timeframe))
    assert before is not None and before.partition_integrity is not None
    store = ParquetCandleStore(tmp_path)
    locks = DatasetLockManager(tmp_path, timeout_seconds=1, stale_after_seconds=60)

    def crash_after_catalog_promotion(step: str) -> None:
        if step == "catalog_promoted":
            raise SimulatedCrash

    coordinator = MarketDataTransactionCoordinator(
        store,
        catalog,
        failure_hook=crash_after_catalog_promotion,
        lock_manager=locks,
    )
    service = _history_service(
        tmp_path,
        candles=(candle(opening + timeframe.duration),),
        coordinator=coordinator,
        lock_manager=locks,
        store=store,
        catalog=catalog,
    )
    with pytest.raises(SimulatedCrash):
        await service.ingest(
            PAIR,
            timeframe,
            DataRange(
                opening + timeframe.duration,
                opening + timeframe.duration * 2,
            ),
        )
    promoted = catalog.get_dataset(dataset_key(INSTRUMENT, timeframe))
    assert promoted is not None and promoted.partition_integrity is not None
    assert promoted.version != before.version
    assert promoted.partition_integrity.bound_dataset_version == promoted.version

    recovery = MarketDataTransactionCoordinator(store, catalog, lock_manager=locks)
    with locks.acquire(dataset_key(INSTRUMENT, timeframe)) as lease:
        assert recovery.recover_dataset(dataset_key(INSTRUMENT, timeframe), lease) == 1
    assert catalog.get_dataset(dataset_key(INSTRUMENT, timeframe)) == before
