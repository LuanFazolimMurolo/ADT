"""Atomic Parquet storage and schema tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from shutil import copyfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.market_data.domain import DataRange, Exchange, MarketType, TradingPair
from app.market_data.errors import MarketDataInconsistencyError, MarketDataStorageError
from app.market_data.filesystem import ensure_safe_path
from app.market_data.storage import PARQUET_SCHEMA, ParquetCandleStore
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import PAIR, candle, utc


def _only_partition(store: ParquetCandleStore) -> Path:
    return next(store.root.rglob("candles.parquet"))


def _assert_partition_rejected(store: ParquetCandleStore) -> None:
    with pytest.raises(MarketDataInconsistencyError):
        store.first_last_count(
            Exchange.BINANCE,
            MarketType.SPOT,
            PAIR,
            get_timeframe("1h"),
        )


def test_parquet_write_read_schema_decimal_utc_and_ordering(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    timeframe = get_timeframe("1h")
    later = candle(utc(2026, 1, 1, 1), open_price="100.12345678")
    earlier = candle(utc(2026, 1, 1), open_price="99.12345678")

    receipt = store.upsert((later, earlier))
    receipt.commit()
    rows = store.read(
        Exchange.BINANCE,
        MarketType.SPOT,
        PAIR,
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
    )
    path = next(store.root.rglob("candles.parquet"))

    assert [item.open_time for item in rows] == [earlier.open_time, later.open_time]
    assert str(rows[0].open) == "99.123456780000000000"
    assert rows[0].open_time.utcoffset() == timedelta(0)
    assert pq.ParquetFile(path).schema_arrow.equals(PARQUET_SCHEMA)
    assert store.verify_schema(path)


def test_upsert_is_idempotent_and_deduplicates_canonical_key(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    item = candle(utc(2026, 1, 1))

    first = store.upsert((item,))
    first.commit()
    second = store.upsert((item,))
    second.commit()
    boundaries = store.first_last_count(
        Exchange.BINANCE,
        MarketType.SPOT,
        PAIR,
        get_timeframe("1h"),
    )

    assert first.stored_count == 1
    assert second.stored_count == 0
    assert second.duplicate_count == 1
    assert boundaries == (item.open_time, item.open_time, 1)


def test_identical_duplicate_inside_received_batch_is_counted(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    item = candle(utc(2026, 1, 1))

    plan = store.plan_upsert((item, item), transaction_id="e" * 32)

    assert plan.stored_count == 1
    assert plan.duplicate_count == 1


def test_storage_rejects_open_candle_defensively(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)

    with pytest.raises(MarketDataInconsistencyError):
        store.plan_upsert(
            (candle(utc(2026, 1, 1), is_closed=False),),
            transaction_id="f" * 32,
        )

    assert not tuple(store.root.rglob("*.parquet"))


def test_monthly_partitions_and_interval_reading(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    january = candle(utc(2026, 1, 31, 23))
    february = candle(utc(2026, 2, 1))
    receipt = store.upsert((february, january))
    receipt.commit()

    paths = sorted(
        path.relative_to(store.root).as_posix() for path in store.root.rglob("*.parquet")
    )
    rows = store.read(
        Exchange.BINANCE,
        MarketType.SPOT,
        PAIR,
        get_timeframe("1h"),
        DataRange(utc(2026, 2, 1), utc(2026, 2, 1, 1)),
    )

    assert any("year=2026/month=01" in path for path in paths)
    assert any("year=2026/month=02" in path for path in paths)
    assert rows == (february,)


def test_interrupted_rewrite_keeps_previous_valid_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetCandleStore(tmp_path)
    original = candle(utc(2026, 1, 1))
    receipt = store.upsert((original,))
    receipt.commit()

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated local interruption")

    monkeypatch.setattr("app.market_data.storage.pq.write_table", fail_write)
    with pytest.raises(MarketDataStorageError):
        store.upsert((candle(utc(2026, 1, 1, 1)),))

    assert store.first_last_count(
        Exchange.BINANCE,
        MarketType.SPOT,
        PAIR,
        get_timeframe("1h"),
    ) == (original.open_time, original.open_time, 1)
    assert not tuple(store.root.rglob("*.tmp-*"))


def test_compensating_rollback_restores_previous_partition(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    original = candle(utc(2026, 1, 1))
    initial = store.upsert((original,))
    initial.commit()

    new_item = candle(utc(2026, 1, 1, 1))
    pending = store.upsert((new_item,))
    pending.rollback()

    assert store.first_last_count(
        Exchange.BINANCE,
        MarketType.SPOT,
        PAIR,
        get_timeframe("1h"),
    ) == (original.open_time, original.open_time, 1)


def test_fsync_failure_after_target_promotion_restores_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetCandleStore(tmp_path)
    original = candle(utc(2026, 1, 1))
    initial = store.upsert((original,))
    initial.commit()
    plan = store.plan_upsert((candle(utc(2026, 1, 1, 1)),), transaction_id="a" * 32)
    store.prepare_files(plan)
    calls = 0

    def fail_second_fsync(_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated fsync failure after promotion")

    monkeypatch.setattr("app.market_data.storage.fsync_directory", fail_second_fsync)
    with pytest.raises(OSError):
        store.promote_partition(plan.partitions[0])

    assert store.first_last_count(
        Exchange.BINANCE,
        MarketType.SPOT,
        PAIR,
        get_timeframe("1h"),
    ) == (original.open_time, original.open_time, 1)


def test_storage_path_cannot_be_built_from_traversal_symbol(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    with pytest.raises(Exception):
        store.dataset_root(
            Exchange.BINANCE,
            MarketType.SPOT,
            TradingPair.parse("../../etc/passwd"),
            get_timeframe("1h"),
        )


def test_symbol_components_do_not_collide(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    first = store.dataset_root(
        Exchange.BINANCE,
        MarketType.SPOT,
        TradingPair("A_B", "C"),
        get_timeframe("1h"),
    )
    second = store.dataset_root(
        Exchange.BINANCE,
        MarketType.SPOT,
        TradingPair("A", "B_C"),
        get_timeframe("1h"),
    )

    assert first != second
    assert "base=A_B/quote=C" in first.as_posix()
    assert "base=A/quote=B_C" in second.as_posix()


def test_existing_symlink_cannot_escape_market_root(tmp_path: Path) -> None:
    market = tmp_path / "market"
    market.mkdir()
    (market / "exchange=binance").symlink_to(tmp_path / "outside", target_is_directory=True)

    with pytest.raises(MarketDataStorageError):
        ParquetCandleStore(tmp_path).dataset_root(
            Exchange.BINANCE,
            MarketType.SPOT,
            PAIR,
            get_timeframe("1h"),
        )


@pytest.mark.parametrize("candidate", [Path("/etc/passwd"), Path("../outside")])
def test_absolute_or_traversal_candidate_is_rejected(tmp_path: Path, candidate: Path) -> None:
    root = (tmp_path / "market").resolve()

    with pytest.raises(MarketDataStorageError):
        ensure_safe_path(root, candidate)


def test_conflicting_existing_and_batch_duplicates_are_rejected(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    original = candle(utc(2026, 1, 1))
    receipt = store.upsert((original,))
    receipt.commit()
    conflicting = replace(original, volume=Decimal("3"))

    with pytest.raises(MarketDataInconsistencyError):
        store.plan_upsert((conflicting,), transaction_id="b" * 32)
    with pytest.raises(MarketDataInconsistencyError):
        store.plan_upsert((original, conflicting), transaction_id="c" * 32)


@pytest.mark.parametrize("value", [Decimal("1e20"), Decimal("1.0000000000000000001")])
def test_decimal_not_exactly_representable_is_rejected_before_write(
    tmp_path: Path,
    value: Decimal,
) -> None:
    store = ParquetCandleStore(tmp_path)
    invalid = replace(candle(utc(2026, 1, 1)), volume=value)

    with pytest.raises(MarketDataInconsistencyError):
        store.plan_upsert((invalid,), transaction_id="d" * 32)

    assert not tuple(store.root.rglob("*.parquet"))


def test_parquet_row_identity_must_match_requested_path(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    other_pair = TradingPair("ETH", "USDT")
    wrong_identity = replace(candle(utc(2026, 1, 1)), symbol=other_pair.symbol)
    receipt = store.upsert((wrong_identity,))
    receipt.commit()
    source = next(
        store.dataset_root(
            Exchange.BINANCE,
            MarketType.SPOT,
            other_pair,
            get_timeframe("1h"),
        ).rglob("candles.parquet")
    )
    target = (
        store.dataset_root(
            Exchange.BINANCE,
            MarketType.SPOT,
            PAIR,
            get_timeframe("1h"),
        )
        / "year=2026"
        / "month=01"
        / "candles.parquet"
    )
    target.parent.mkdir(parents=True)
    copyfile(source, target)

    _assert_partition_rejected(store)


def test_existing_parquet_rejects_identical_duplicate_key(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    receipt = store.upsert((candle(utc(2026, 1, 1)),))
    receipt.commit()
    target = _only_partition(store)
    table = pq.ParquetFile(target).read()
    pq.write_table(pa.concat_tables((table, table)), target)

    _assert_partition_rejected(store)


def test_existing_parquet_rejects_conflicting_duplicate_key(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path / "dataset")
    receipt = store.upsert((candle(utc(2026, 1, 1)),))
    receipt.commit()
    target = _only_partition(store)
    other = ParquetCandleStore(tmp_path / "conflict")
    conflicting = replace(candle(utc(2026, 1, 1)), volume=Decimal("3"))
    other_receipt = other.upsert((conflicting,))
    other_receipt.commit()
    table = pq.ParquetFile(target).read()
    conflict_table = pq.ParquetFile(_only_partition(other)).read()
    pq.write_table(pa.concat_tables((table, conflict_table)), target)

    _assert_partition_rejected(store)


def test_existing_parquet_rejects_rows_out_of_order(tmp_path: Path) -> None:
    store = ParquetCandleStore(tmp_path)
    receipt = store.upsert(
        (candle(utc(2026, 1, 1)), candle(utc(2026, 1, 1, 1))),
    )
    receipt.commit()
    target = _only_partition(store)
    table = pq.ParquetFile(target).read()
    pq.write_table(table.take(pa.array([1, 0])), target)

    _assert_partition_rejected(store)


@pytest.mark.parametrize("foreign_time", [utc(2026, 2, 1), utc(2025, 1, 1)])
def test_existing_parquet_rejects_candle_outside_partition_month_or_year(
    tmp_path: Path,
    foreign_time,
) -> None:
    source_store = ParquetCandleStore(tmp_path / "source")
    receipt = source_store.upsert((candle(foreign_time),))
    receipt.commit()
    store = ParquetCandleStore(tmp_path / "dataset")
    target = (
        store.dataset_root(
            Exchange.BINANCE,
            MarketType.SPOT,
            PAIR,
            get_timeframe("1h"),
        )
        / "year=2026"
        / "month=01"
        / "candles.parquet"
    )
    target.parent.mkdir(parents=True)
    copyfile(_only_partition(source_store), target)

    _assert_partition_rejected(store)
