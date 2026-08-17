"""Shared read-only dataset snapshot locking for Phase 7-04."""

from __future__ import annotations

import multiprocessing
import time
from hashlib import sha256
from pathlib import Path

import pytest

from app.market_data.errors import (
    MarketDataSnapshotBusyError,
    MarketJobLockTimeoutError,
)
from app.market_data.filesystem import market_root
from app.market_data.locks import DatasetLockManager

_DATASET_KEY = "binance:spot:BTC/USDT:1h"


def _manager(
    data_dir: Path,
    *,
    timeout_seconds: float,
) -> DatasetLockManager:
    return DatasetLockManager(
        data_dir,
        timeout_seconds=timeout_seconds,
        stale_after_seconds=60,
    )


def _hold_snapshot(
    data_dir: Path,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    manager = _manager(data_dir, timeout_seconds=1)
    with manager.snapshot(_DATASET_KEY):
        ready.set()
        release.wait(timeout=5)


def _hold_writer(
    data_dir: Path,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    manager = _manager(data_dir, timeout_seconds=1)
    with manager.acquire(_DATASET_KEY):
        ready.set()
        release.wait(timeout=5)


def _lock_path(data_dir: Path) -> Path:
    digest = sha256(_DATASET_KEY.encode()).hexdigest()
    return market_root(data_dir) / ".locks" / f"{digest}.lock"


def _stop_process(
    process: multiprocessing.Process,
    release: multiprocessing.synchronize.Event,
) -> None:
    release.set()
    process.join(timeout=5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    assert process.exitcode == 0


def test_two_snapshot_readers_can_share_dataset_lock(tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_snapshot,
        args=(tmp_path, ready, release),
    )
    process.start()

    try:
        assert ready.wait(timeout=5)

        manager = _manager(tmp_path, timeout_seconds=0.2)
        with manager.snapshot(_DATASET_KEY):
            assert process.is_alive()
    finally:
        _stop_process(process, release)


def test_snapshot_reader_blocks_exclusive_writer(tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_snapshot,
        args=(tmp_path, ready, release),
    )
    process.start()

    try:
        assert ready.wait(timeout=5)

        manager = _manager(tmp_path, timeout_seconds=0.05)
        with pytest.raises(MarketJobLockTimeoutError):
            manager.acquire(_DATASET_KEY)
    finally:
        _stop_process(process, release)


def test_exclusive_writer_blocks_snapshot_reader_with_bounded_503_error(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_writer,
        args=(tmp_path, ready, release),
    )
    process.start()

    try:
        assert ready.wait(timeout=5)

        manager = _manager(tmp_path, timeout_seconds=0.05)
        started = time.monotonic()

        with pytest.raises(MarketDataSnapshotBusyError) as captured:
            with manager.snapshot(_DATASET_KEY):
                raise AssertionError("snapshot não poderia ter sido adquirido")

        elapsed = time.monotonic() - started

        assert captured.value.status_code == 503
        assert captured.value.code == "market_data_snapshot_busy"
        assert elapsed < 1
    finally:
        _stop_process(process, release)


def test_snapshot_does_not_rewrite_existing_writer_metadata(tmp_path: Path) -> None:
    writer = _manager(tmp_path, timeout_seconds=0)
    with writer.acquire(_DATASET_KEY):
        pass

    lock_file = _lock_path(tmp_path)
    before = lock_file.read_bytes()
    before_mtime_ns = lock_file.stat().st_mtime_ns

    reader = _manager(tmp_path, timeout_seconds=0.1)
    with reader.snapshot(_DATASET_KEY):
        assert lock_file.read_bytes() == before

    assert lock_file.read_bytes() == before
    assert lock_file.stat().st_mtime_ns == before_mtime_ns


def test_writer_can_acquire_after_snapshot_release(tmp_path: Path) -> None:
    manager = _manager(tmp_path, timeout_seconds=0)

    with manager.snapshot(_DATASET_KEY):
        pass

    with manager.acquire(_DATASET_KEY) as lease:
        assert lease.active
        assert lease.dataset_key == _DATASET_KEY
