"""Crash recovery tests for the Parquet/catalog commit protocol."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from app.market_data.catalog import (
    DatasetMetadata,
    IngestionRunRecord,
    JsonMarketDataCatalog,
    dataset_key,
)
from app.market_data.domain import Exchange, MarketType
from app.market_data.errors import MarketDataInconsistencyError, MarketDataStorageError
from app.market_data.storage import ParquetCandleStore
from app.market_data.timeframes import get_timeframe
from app.market_data.transaction import MarketDataTransactionCoordinator
from tests.market_data_helpers import INSTRUMENT, PAIR, candle, utc


class SimulatedCrash(BaseException):
    """Model process termination without invoking in-process rollback."""


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _metadata(count: int, version: str) -> DatasetMetadata:
    return DatasetMetadata(
        key=dataset_key(INSTRUMENT, get_timeframe("1h")),
        exchange="binance",
        market_type="spot",
        symbol="BTC/USDT",
        native_symbol="BTCUSDT",
        timeframe="1h",
        location=("market/exchange=binance/market=spot/base=BTC/quote=USDT/timeframe=1h"),
        first_open_time=utc(2026, 1, 1).isoformat(),
        last_open_time=utc(2026, 1, 1, count - 1).isoformat(),
        candle_count=count,
        version=version,
        updated_at=utc(2026, 3, 1).isoformat(),
    )


def _run(started: IngestionRunRecord, count: int) -> IngestionRunRecord:
    return replace(
        started,
        status="COMPLETED",
        finished_at=utc(2026, 3, 1, 1).isoformat(),
        fetched_count=count,
        stored_count=count,
    )


def _baseline(tmp_path: Path) -> tuple[ParquetCandleStore, JsonMarketDataCatalog]:
    store = ParquetCandleStore(tmp_path)
    catalog = JsonMarketDataCatalog(tmp_path)
    transaction_id = "0" * 32
    parquet = store.plan_upsert(
        (candle(utc(2026, 1, 1)),),
        transaction_id=transaction_id,
    )
    started = catalog.start_run(dataset_key(INSTRUMENT, get_timeframe("1h")))
    version = _digest("old-version")
    catalog_plan = catalog.prepare_completion(
        _run(started, 1),
        _metadata(1, version),
        transaction_id=transaction_id,
    )
    MarketDataTransactionCoordinator(store, catalog).execute(
        parquet,
        catalog_plan,
        intended_version=version,
    )
    return store, catalog


def _plans(
    store: ParquetCandleStore,
    catalog: JsonMarketDataCatalog,
    *,
    transaction_id: str,
):
    parquet = store.plan_upsert(
        (candle(utc(2026, 1, 1, 1)),),
        transaction_id=transaction_id,
    )
    started = catalog.start_run(dataset_key(INSTRUMENT, get_timeframe("1h")))
    catalog_plan = catalog.prepare_completion(
        _run(started, 1),
        _metadata(2, _digest("new-version")),
        transaction_id=transaction_id,
    )
    return parquet, catalog_plan


@pytest.mark.parametrize(
    ("step", "committed"),
    [
        ("before_journal_prepared", False),
        ("journal_prepared", False),
        ("before_partition_prepared:0", False),
        ("partition_prepared:0", False),
        ("before_catalog_prepared", False),
        ("catalog_prepared", False),
        ("before_partition_promoted:0", False),
        ("partition_promoted:0", False),
        ("before_catalog_promoted", False),
        ("catalog_promoted", False),
        ("before_journal_committed", False),
        ("journal_committed", True),
        ("before_cleanup", True),
    ],
)
def test_recovery_before_and_after_every_protocol_stage(
    tmp_path: Path,
    step: str,
    committed: bool,
) -> None:
    store, catalog = _baseline(tmp_path)
    parquet, catalog_plan = _plans(store, catalog, transaction_id="1" * 32)

    def crash(current: str) -> None:
        if current == step:
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        MarketDataTransactionCoordinator(store, catalog, failure_hook=crash).execute(
            parquet,
            catalog_plan,
            intended_version=_digest("new-version"),
        )

    coordinator = MarketDataTransactionCoordinator(store, catalog)
    coordinator.recover()
    coordinator.recover()
    _, last, count = store.first_last_count(
        Exchange.BINANCE,
        MarketType.SPOT,
        PAIR,
        get_timeframe("1h"),
    )
    metadata = catalog.get_dataset(dataset_key(INSTRUMENT, get_timeframe("1h")))

    assert count == (2 if committed else 1)
    assert last == utc(2026, 1, 1, 1 if committed else 0)
    assert metadata is not None
    assert metadata.version == (_digest("new-version") if committed else _digest("old-version"))
    assert not tuple((tmp_path / "market" / ".transactions").glob("journal-*.json"))
    assert not tuple((tmp_path / "market").rglob("*.bak-*"))


def test_old_catalog_with_promoted_parquet_is_rolled_back(tmp_path: Path) -> None:
    store, catalog = _baseline(tmp_path)
    parquet, catalog_plan = _plans(store, catalog, transaction_id="2" * 32)

    def crash(step: str) -> None:
        if step == "partition_promoted:0":
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        MarketDataTransactionCoordinator(store, catalog, failure_hook=crash).execute(
            parquet,
            catalog_plan,
            intended_version=_digest("new-version"),
        )
    journal_path = next((store.root / ".transactions").glob("journal-*.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["transaction_id"] == "2" * 32
    assert journal["state"] == "PREPARED"
    assert journal["partitions"][0]["target"].endswith("candles.parquet")
    assert ".bak-" in journal["partitions"][0]["backup"]
    assert journal["catalog"]["target"] == "catalog.json"
    assert journal["intended_version"] == _digest("new-version")
    assert journal["intended_checksum"] == parquet.checksum
    assert (
        store.first_last_count(Exchange.BINANCE, MarketType.SPOT, PAIR, get_timeframe("1h"))[2] == 2
    )
    assert catalog.get_dataset(dataset_key(INSTRUMENT, get_timeframe("1h"))).version == (
        _digest("old-version")
    )

    MarketDataTransactionCoordinator(store, catalog).recover()

    assert (
        store.first_last_count(Exchange.BINANCE, MarketType.SPOT, PAIR, get_timeframe("1h"))[2] == 1
    )


def test_multiple_promoted_partitions_roll_back_together(tmp_path: Path) -> None:
    store, catalog = _baseline(tmp_path)
    transaction_id = "3" * 32
    parquet = store.plan_upsert(
        (candle(utc(2026, 1, 1, 1)), candle(utc(2026, 2, 1))),
        transaction_id=transaction_id,
    )
    started = catalog.start_run(dataset_key(INSTRUMENT, get_timeframe("1h")))
    catalog_plan = catalog.prepare_completion(
        _run(started, 2),
        replace(
            _metadata(3, _digest("multi-version")),
            last_open_time=utc(2026, 2, 1).isoformat(),
        ),
        transaction_id=transaction_id,
    )

    def crash(step: str) -> None:
        if step == "partition_promoted:1":
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        MarketDataTransactionCoordinator(store, catalog, failure_hook=crash).execute(
            parquet,
            catalog_plan,
            intended_version=_digest("multi-version"),
        )
    MarketDataTransactionCoordinator(store, catalog).recover()

    assert (
        store.first_last_count(Exchange.BINANCE, MarketType.SPOT, PAIR, get_timeframe("1h"))[2] == 1
    )
    assert catalog.get_dataset(dataset_key(INSTRUMENT, get_timeframe("1h"))).version == (
        _digest("old-version")
    )


def test_recovery_marks_abandoned_run_failed_with_sanitized_state(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    catalog = JsonMarketDataCatalog(tmp_path)
    started = catalog.start_run(dataset_key(INSTRUMENT, get_timeframe("1h")))

    assert MarketDataTransactionCoordinator(store, catalog).recover() == 0

    state = json.loads(catalog.path.read_text(encoding="utf-8"))
    assert state["runs"][started.run_id]["status"] == "FAILED"
    assert state["runs"][started.run_id]["error_code"] == "interrupted_ingestion"


def test_catalog_completion_validates_run_identity_state_and_final_status(
    tmp_path: Path,
) -> None:
    catalog = JsonMarketDataCatalog(tmp_path)
    key = dataset_key(INSTRUMENT, get_timeframe("1h"))
    started = catalog.start_run(key)
    valid = _run(started, 1)
    metadata = _metadata(1, _digest("version"))

    invalid_cases = (
        replace(valid, run_id="00000000-0000-0000-0000-000000000001"),
        replace(valid, dataset_key="binance:spot:ETH/USDT:1h"),
        replace(valid, status="FAILED", error_code="failed"),
        replace(valid, fetched_count=0, stored_count=1),
    )
    for index, invalid in enumerate(invalid_cases):
        with pytest.raises(MarketDataInconsistencyError):
            catalog.prepare_completion(
                invalid,
                metadata,
                transaction_id=f"{index + 4:x}" * 32,
            )

    transaction_id = "8" * 32
    parquet = ParquetCandleStore(tmp_path).plan_upsert(
        (candle(utc(2026, 1, 1)),),
        transaction_id=transaction_id,
    )
    plan = catalog.prepare_completion(valid, metadata, transaction_id=transaction_id)
    MarketDataTransactionCoordinator(ParquetCandleStore(tmp_path), catalog).execute(
        parquet,
        plan,
        intended_version=metadata.version,
    )
    with pytest.raises(MarketDataInconsistencyError):
        catalog.prepare_completion(valid, metadata, transaction_id="9" * 32)


def test_catalog_completion_reuses_persisted_started_at_with_advancing_clock(
    tmp_path: Path,
) -> None:
    instants = iter((utc(2026, 3, 1), utc(2026, 3, 1, 1)))
    catalog = JsonMarketDataCatalog(tmp_path, clock=lambda: next(instants))
    started = catalog.start_run(dataset_key(INSTRUMENT, get_timeframe("1h")))
    completed = replace(
        started,
        status="COMPLETED",
        finished_at=utc(2026, 3, 1, 2).isoformat(),
        fetched_count=1,
        stored_count=1,
    )

    plan = catalog.prepare_completion(
        completed,
        _metadata(1, _digest("clock-version")),
        transaction_id="f" * 32,
    )

    assert json.loads(plan.content)["runs"][started.run_id]["started_at"] == started.started_at
    with pytest.raises(MarketDataInconsistencyError):
        catalog.prepare_completion(
            replace(completed, started_at=utc(2026, 3, 1, 1).isoformat()),
            _metadata(1, _digest("clock-version")),
            transaction_id="e" * 32,
        )


def test_failure_before_committed_raises_and_restores_everything(tmp_path: Path) -> None:
    store, catalog = _baseline(tmp_path)
    parquet, catalog_plan = _plans(store, catalog, transaction_id="a" * 32)

    def fail(step: str) -> None:
        if step == "catalog_promoted":
            raise MarketDataStorageError()

    with pytest.raises(MarketDataStorageError):
        MarketDataTransactionCoordinator(store, catalog, failure_hook=fail).execute(
            parquet,
            catalog_plan,
            intended_version=_digest("new-version"),
        )

    assert (
        store.first_last_count(Exchange.BINANCE, MarketType.SPOT, PAIR, get_timeframe("1h"))[2] == 1
    )
    assert catalog.get_dataset(dataset_key(INSTRUMENT, get_timeframe("1h"))).version == _digest(
        "old-version"
    )
    assert not tuple((store.root / ".transactions").glob("journal-*.json"))


def test_failure_writing_committed_raises_and_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, catalog = _baseline(tmp_path)
    parquet, catalog_plan = _plans(store, catalog, transaction_id="b" * 32)
    coordinator = MarketDataTransactionCoordinator(store, catalog)
    original_write = coordinator._write_journal

    def fail_committed(path: Path, record) -> None:
        if record.state == "COMMITTED":
            raise MarketDataStorageError()
        original_write(path, record)

    monkeypatch.setattr(coordinator, "_write_journal", fail_committed)
    with pytest.raises(MarketDataStorageError):
        coordinator.execute(
            parquet,
            catalog_plan,
            intended_version=_digest("new-version"),
        )

    assert (
        store.first_last_count(Exchange.BINANCE, MarketType.SPOT, PAIR, get_timeframe("1h"))[2] == 1
    )
    assert catalog.get_dataset(dataset_key(INSTRUMENT, get_timeframe("1h"))).version == _digest(
        "old-version"
    )


def test_cleanup_failure_after_committed_is_deferred_and_recovered(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, catalog = _baseline(tmp_path)
    parquet, catalog_plan = _plans(store, catalog, transaction_id="c" * 32)

    def fail(step: str) -> None:
        if step == "before_cleanup":
            raise MarketDataStorageError("sensitive local detail")

    with caplog.at_level(logging.WARNING):
        MarketDataTransactionCoordinator(store, catalog, failure_hook=fail).execute(
            parquet,
            catalog_plan,
            intended_version=_digest("new-version"),
        )

    journal_path = next((store.root / ".transactions").glob("journal-*.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "COMMITTED"
    assert tuple(store.root.rglob("*.bak-*"))
    assert "sensitive local detail" not in caplog.text
    assert "cleanup deferred" in caplog.text

    coordinator = MarketDataTransactionCoordinator(store, catalog)
    assert coordinator.recover() == 1
    assert coordinator.recover() == 0
    assert not journal_path.exists()
    assert not tuple(store.root.rglob("*.bak-*"))
    state = json.loads(catalog.path.read_text(encoding="utf-8"))
    assert state["runs"][catalog_plan.run_id]["status"] == "COMPLETED"
    assert (
        store.first_last_count(Exchange.BINANCE, MarketType.SPOT, PAIR, get_timeframe("1h"))[2] == 2
    )


def test_cleanup_failure_after_journal_unlink_restores_committed_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, catalog = _baseline(tmp_path)
    parquet, catalog_plan = _plans(store, catalog, transaction_id="e" * 32)
    coordinator = MarketDataTransactionCoordinator(store, catalog)

    def fail_after_unlink(_record, journal_path: Path) -> None:
        journal_path.unlink()
        raise OSError("sensitive cleanup detail")

    monkeypatch.setattr(coordinator, "_finalize", fail_after_unlink)
    coordinator.execute(
        parquet,
        catalog_plan,
        intended_version=_digest("new-version"),
    )

    journal_path = next((store.root / ".transactions").glob("journal-*.json"))
    assert json.loads(journal_path.read_text(encoding="utf-8"))["state"] == "COMMITTED"
    assert MarketDataTransactionCoordinator(store, catalog).recover() == 1


@pytest.mark.parametrize(
    "invalid_case",
    [
        "state",
        "run_id",
        "version",
        "checksum",
        "duplicate_target",
        "catalog_target",
        "different_directory",
        "artifact_transaction_id",
        "transactions_partition",
    ],
)
def test_inconsistent_journal_is_rejected_without_touching_artifacts(
    tmp_path: Path,
    invalid_case: str,
) -> None:
    store, catalog = _baseline(tmp_path)
    parquet, catalog_plan = _plans(store, catalog, transaction_id="d" * 32)

    def crash(step: str) -> None:
        if step == "journal_committed":
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        MarketDataTransactionCoordinator(store, catalog, failure_hook=crash).execute(
            parquet,
            catalog_plan,
            intended_version=_digest("new-version"),
        )
    journal_path = next((store.root / ".transactions").glob("journal-*.json"))
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    if invalid_case == "state":
        payload["state"] = "UNKNOWN"
    elif invalid_case == "run_id":
        payload["run_id"] = "not-a-uuid"
    elif invalid_case == "version":
        payload["intended_version"] = "not-sha256"
    elif invalid_case == "checksum":
        payload["intended_checksum"] = "not-sha256"
    elif invalid_case == "duplicate_target":
        payload["partitions"][0]["target"] = payload["catalog"]["target"]
    elif invalid_case == "catalog_target":
        payload["catalog"]["target"] = "other.json"
    elif invalid_case == "different_directory":
        payload["partitions"][0]["temporary"] = (
            "other/.candles.parquet.tmp-" + payload["transaction_id"]
        )
    elif invalid_case == "artifact_transaction_id":
        payload["catalog"]["backup"] = ".catalog.json.bak-" + "e" * 32
    elif invalid_case == "transactions_partition":
        payload["partitions"][0].update(
            {
                "target": ".transactions/candles.parquet",
                "temporary": (".transactions/.candles.parquet.tmp-" + payload["transaction_id"]),
                "backup": (".transactions/.candles.parquet.bak-" + payload["transaction_id"]),
            }
        )
    journal_path.write_text(json.dumps(payload), encoding="utf-8")
    catalog_before = catalog.path.read_bytes()
    partition_before = parquet.partitions[0].target.read_bytes()
    backups_before = tuple(sorted(path.as_posix() for path in store.root.rglob("*.bak-*")))

    with pytest.raises(MarketDataStorageError):
        MarketDataTransactionCoordinator(store, catalog).recover()

    assert catalog.path.read_bytes() == catalog_before
    assert parquet.partitions[0].target.read_bytes() == partition_before
    assert tuple(sorted(path.as_posix() for path in store.root.rglob("*.bak-*"))) == backups_before
    assert journal_path.exists()
