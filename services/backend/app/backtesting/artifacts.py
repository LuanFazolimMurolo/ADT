"""Atomic immutable publication of deterministic backtest artifacts."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from app.backtesting.domain import (
    ArtifactChecksum,
    BacktestConfig,
    BacktestManifest,
    BacktestResult,
    BacktestRunId,
    BacktestStatus,
)
from app.backtesting.engine import BacktestExecutionResult
from app.backtesting.errors import (
    BacktestResultConflictError,
    BacktestResultCorruptError,
    SnapshotChangedError,
)
from app.backtesting.metrics import calculate_metrics, derive_closed_trades
from app.backtesting.serialization import (
    canonical_checksum,
    canonical_json_bytes,
    canonical_value,
    file_checksum,
    write_json_envelope,
)
from app.market_data.datasets import DatasetSnapshot
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager

if TYPE_CHECKING:
    from app.backtesting.verifier import SnapshotFactory, SnapshotVerifier

Clock = Callable[[], datetime]
_ARTIFACT_NAMES = (
    "config.json",
    "result.json",
    "orders.jsonl",
    "fills.jsonl",
    "ledger.jsonl",
    "equity.parquet",
    "trades.jsonl",
)


def build_run_id_from_values(config_value: object, snapshot_value: object) -> BacktestRunId:
    return BacktestRunId(
        canonical_checksum(
            {
                "config": canonical_value(config_value),
                "snapshot": canonical_value(snapshot_value),
            }
        )
    )


def build_run_id(config: BacktestConfig, snapshot: DatasetSnapshot) -> BacktestRunId:
    return build_run_id_from_values(
        config,
        {
            "snapshot_id": snapshot.snapshot_id,
            "dataset_key": snapshot.dataset_key,
            "dataset_version": snapshot.dataset_version,
            "checksum": snapshot.checksum,
            "data_range": canonical_value(snapshot.data_range),
        },
    )


def build_logical_result_checksum(
    *,
    run_id: BacktestRunId,
    execution: BacktestExecutionResult,
    trades: object,
    metrics: object,
) -> str:
    return canonical_checksum(
        {
            "run_id": run_id.value,
            "orders": execution.orders,
            "fills": execution.fills,
            "ledger": execution.ledger,
            "equity": execution.equity_curve,
            "trades": trades,
            "final_portfolio": execution.final_portfolio,
            "metrics": metrics,
            "risk_halt": execution.risk_halt,
            "candles_processed": execution.candles_processed,
        }
    )


def build_backtest_result(
    config: BacktestConfig,
    execution: BacktestExecutionResult,
) -> BacktestResult:
    """Derive one logical result without writing operational artifacts."""
    run_id = build_run_id(config, execution.snapshot)
    trades = derive_closed_trades(execution.fills)
    metrics = calculate_metrics(
        execution,
        initial_equity=config.initial_capital,
        trades=trades,
    )
    logical_checksum = build_logical_result_checksum(
        run_id=run_id,
        execution=execution,
        trades=trades,
        metrics=metrics,
    )
    return BacktestResult(
        run_id=run_id,
        final_portfolio=execution.final_portfolio,
        metrics=metrics,
        trades=trades,
        logical_result_checksum=logical_checksum,
    )


class BacktestArtifactStore:
    """Publish COMPLETE results under one run-id lock and never overwrite them."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("backtests"),
        lock_timeout_seconds: float = 30,
        lock_stale_after_seconds: float = 300,
        clock: Clock | None = None,
        snapshot_factory: SnapshotFactory | None = None,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("backtest directory must be safe and relative")
        self._data_dir = data_dir
        self._market = market_root(data_dir)
        self._root = ensure_safe_path(self._market, self._market / directory)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._snapshot_factory = snapshot_factory
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
            clock=self._clock,
        )

    @property
    def root(self) -> Path:
        return self._root

    def publish(
        self,
        config: BacktestConfig,
        execution: BacktestExecutionResult,
    ) -> BacktestResult:
        result = build_backtest_result(config, execution)
        run_id = result.run_id
        trades = result.trades
        logical_checksum = result.logical_result_checksum
        self._root.mkdir(parents=True, exist_ok=True)
        fsync_directory(self._market)
        with self._locks.acquire(f"backtest:{run_id.value}"):
            target = ensure_safe_path(self._market, self._root / run_id.value)
            if target.exists():
                from app.backtesting.verifier import BacktestResultVerifier

                verification = BacktestResultVerifier(
                    self._data_dir,
                    directory=self._root.relative_to(self._market),
                    lock_timeout_seconds=0,
                    acquire_lock=False,
                    snapshot_factory=self._snapshot_factory,
                ).verify(run_id.value)
                if verification.logical_result_checksum != logical_checksum:
                    raise BacktestResultConflictError()
                return result
            self._verify_snapshot_unchanged(execution.snapshot)
            staging = ensure_safe_path(
                self._market,
                self._root / f".{run_id.value}.tmp-{os.getpid()}-{uuid4().hex}",
            )
            staging.mkdir(parents=False, exist_ok=False)
            try:
                self._write_artifacts(staging, config, execution, result)
                artifacts = tuple(
                    ArtifactChecksum(
                        name,
                        file_checksum(staging / name),
                        (staging / name).stat().st_size,
                    )
                    for name in _ARTIFACT_NAMES
                )
                now = self._clock().astimezone(UTC)
                manifest = BacktestManifest(
                    run_id=run_id,
                    engine_version=config.engine_version,
                    schema_version=config.schema_version,
                    status=BacktestStatus.COMPLETE,
                    snapshot_id=execution.snapshot.snapshot_id,
                    dataset_key=execution.snapshot.dataset_key,
                    dataset_version=execution.snapshot.dataset_version,
                    dataset_checksum=execution.snapshot.checksum,
                    snapshot_data_range=execution.snapshot.data_range,
                    data_range=config.data_range,
                    strategy=config.strategy,
                    strategy_parameters_checksum=canonical_checksum(config.strategy.parameters),
                    initial_capital=config.initial_capital,
                    execution=config.execution,
                    risk_limits=config.risk_limits,
                    candle_count=execution.candles_processed,
                    order_count=len(execution.orders),
                    fill_count=len(execution.fills),
                    trade_count=len(trades),
                    artifacts=artifacts,
                    logical_result_checksum=logical_checksum,
                    created_at=now,
                    completed_at=self._clock().astimezone(UTC),
                )
                write_json_envelope(staging / "manifest.json", "manifest", manifest)
                _fsync_file(staging / "manifest.json")
                fsync_directory(staging)
                self._verify_snapshot_unchanged(execution.snapshot)
                os.replace(staging, target)
                fsync_directory(self._root)
            except Exception:
                _remove_tree(staging)
                raise
        return result

    def _verify_snapshot_unchanged(self, expected: DatasetSnapshot) -> None:
        reader: SnapshotVerifier
        if self._snapshot_factory is None:
            from app.market_data.snapshots import MarketDatasetReader

            reader = MarketDatasetReader(self._data_dir)
        else:
            reader = self._snapshot_factory(self._data_dir)
        try:
            opened = reader.open_snapshot(expected.snapshot_id)
            verified = reader.verify_unchanged()
        except Exception:
            raise SnapshotChangedError() from None
        if opened != expected or verified != expected:
            raise SnapshotChangedError()

    @staticmethod
    def _write_artifacts(
        staging: Path,
        config: BacktestConfig,
        execution: BacktestExecutionResult,
        result: BacktestResult,
    ) -> None:
        write_json_envelope(staging / "config.json", "config", config)
        write_json_envelope(
            staging / "result.json",
            "result",
            {
                "run_id": result.run_id.value,
                "final_portfolio": result.final_portfolio,
                "metrics": result.metrics,
                "logical_result_checksum": result.logical_result_checksum,
                "risk_halt": execution.risk_halt,
                "candles_processed": execution.candles_processed,
            },
        )
        _write_jsonl(staging / "orders.jsonl", execution.orders)
        _write_jsonl(staging / "fills.jsonl", execution.fills)
        _write_jsonl(staging / "ledger.jsonl", execution.ledger)
        _write_jsonl(staging / "trades.jsonl", result.trades)
        _write_equity(staging / "equity.parquet", execution.equity_curve)
        for name in _ARTIFACT_NAMES:
            _fsync_file(staging / name)
        fsync_directory(staging)


def _write_jsonl(path: Path, values: Iterable[object]) -> None:
    with path.open("wb") as stream:
        for value in values:
            stream.write(canonical_json_bytes(value))
            stream.write(b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_equity(path: Path, values: Iterable[object]) -> None:
    rows = [canonical_value(value) for value in values]
    schema = pa.schema(
        [
            pa.field("candle_index", pa.int64(), nullable=False),
            pa.field("event_time", pa.string(), nullable=False),
            pa.field("close_price", pa.string(), nullable=False),
            pa.field("quote_cash", pa.string(), nullable=False),
            pa.field("base_quantity", pa.string(), nullable=False),
            pa.field("equity", pa.string(), nullable=False),
            pa.field("peak_equity", pa.string(), nullable=False),
            pa.field("drawdown", pa.string(), nullable=False),
            pa.field("drawdown_pct", pa.string(), nullable=False),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="snappy", version="2.6")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise BacktestResultCorruptError(
            "Não foi possível limpar o staging do backtest."
        ) from error
