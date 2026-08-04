"""Atomic immutable publication of deterministic backtest artifacts."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Sequence
from copy import copy
from datetime import UTC, datetime
from decimal import Decimal
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
    EquityPoint,
    evaluation_range_for,
    market_regime_policy_for,
    strategy_lifecycle_version_for,
    validate_backtest_config,
)
from app.backtesting.engine import BacktestExecutionResult
from app.backtesting.errors import (
    BacktestResultConflictError,
    BacktestResultCorruptError,
    SnapshotChangedError,
)
from app.backtesting.metrics import calculate_metrics, derive_closed_trades, metrics_for_schema
from app.backtesting.serialization import (
    canonical_checksum,
    canonical_json_bytes,
    canonical_value,
    file_checksum,
    write_json_envelope,
)
from app.indicators._math import contextual, indicator_decimal_context
from app.indicators.regime import (
    MarketRegimeKind,
    MarketRegimePoint,
    MarketRegimePolicy,
    TrendDirection,
)
from app.indicators.regime_incremental import MarketRegimeAccumulator
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import Candle, DataRange
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager

if TYPE_CHECKING:
    from app.backtesting.verifier import SnapshotFactory, SnapshotVerifier

Clock = Callable[[], datetime]
_BASE_ARTIFACT_NAMES = (
    "config.json",
    "result.json",
    "orders.jsonl",
    "fills.jsonl",
    "ledger.jsonl",
    "equity.parquet",
    "trades.jsonl",
)
_REGIME_ARTIFACT_NAME = "regimes.jsonl"


def _artifact_names(config: BacktestConfig) -> tuple[str, ...]:
    if market_regime_policy_for(config) is None:
        return _BASE_ARTIFACT_NAMES
    return (*_BASE_ARTIFACT_NAMES, _REGIME_ARTIFACT_NAME)


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
    validate_backtest_config(config)
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
    market_regimes: object | None = None,
) -> str:
    payload: dict[str, object] = {
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
    if market_regimes is not None:
        payload["market_regimes"] = market_regimes
    return canonical_checksum(payload)


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
        period_start=evaluation_range_for(config).start,
    )
    metrics_value = metrics_for_schema(metrics, config.schema_version)
    market_regimes = _validated_market_regimes(config, execution)
    logical_checksum = build_logical_result_checksum(
        run_id=run_id,
        execution=execution,
        trades=trades,
        metrics=metrics_value,
        market_regimes=market_regimes,
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

    def relative_run_path(self, run_id: str) -> str:
        """Return the canonical published path for one validated run identity."""

        typed = BacktestRunId(run_id)
        return (self._root / typed.value).relative_to(self._market).as_posix()

    def publish(
        self,
        config: BacktestConfig,
        execution: BacktestExecutionResult,
    ) -> BacktestResult:
        validate_backtest_config(config)
        result = build_backtest_result(config, execution)
        run_id = result.run_id
        trades = result.trades
        logical_checksum = result.logical_result_checksum
        artifact_names = _artifact_names(config)
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
            snapshot_reader = self._verify_snapshot_unchanged(execution.snapshot)
            self._verify_market_regimes_against_snapshot(
                config,
                execution,
                snapshot_reader,
            )
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
                    for name in artifact_names
                )
                now = self._clock().astimezone(UTC)
                evaluation_range = evaluation_range_for(config)
                strategy_lifecycle_version = strategy_lifecycle_version_for(config)
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
                    data_range=evaluation_range,
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
                    context_range=config.data_range,
                    evaluation_range=evaluation_range,
                    strategy_lifecycle_version=strategy_lifecycle_version,
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

    def _verify_snapshot_unchanged(self, expected: DatasetSnapshot) -> SnapshotVerifier:
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
        return reader

    @staticmethod
    def _verify_market_regimes_against_snapshot(
        config: BacktestConfig,
        execution: BacktestExecutionResult,
        reader: SnapshotVerifier,
    ) -> None:
        policy = market_regime_policy_for(config)
        if policy is None:
            return
        try:
            expected = rebuild_market_regime_observations(
                policy,
                reader.iter_candles(config.data_range),
                context_range=config.data_range,
                evaluation_range=evaluation_range_for(config),
            )
            verified = reader.verify_unchanged()
        except Exception:
            raise SnapshotChangedError() from None
        if verified != execution.snapshot:
            raise SnapshotChangedError()
        if expected != tuple(execution.market_regimes):
            raise ValueError("market-regime output diverges from the immutable snapshot candles")

    @staticmethod
    def _write_artifacts(
        staging: Path,
        config: BacktestConfig,
        execution: BacktestExecutionResult,
        result: BacktestResult,
    ) -> None:
        write_json_envelope(staging / "config.json", "config", config)
        market_regimes = _validated_market_regimes(config, execution)
        result_payload: dict[str, object] = {
            "run_id": result.run_id.value,
            "final_portfolio": result.final_portfolio,
            "metrics": metrics_for_schema(result.metrics, config.schema_version),
            "logical_result_checksum": result.logical_result_checksum,
            "risk_halt": execution.risk_halt,
            "candles_processed": execution.candles_processed,
        }
        if market_regimes is not None:
            result_payload["market_regime_count"] = len(market_regimes)
        write_json_envelope(staging / "result.json", "result", result_payload)
        _write_jsonl(staging / "orders.jsonl", execution.orders)
        _write_jsonl(staging / "fills.jsonl", execution.fills)
        _write_jsonl(staging / "ledger.jsonl", execution.ledger)
        _write_jsonl(staging / "trades.jsonl", result.trades)
        _write_equity(staging / "equity.parquet", execution.equity_curve)
        if market_regimes is not None:
            _write_jsonl(staging / _REGIME_ARTIFACT_NAME, market_regimes)
        for name in _artifact_names(config):
            _fsync_file(staging / name)
        fsync_directory(staging)


def validate_market_regime_observations(
    policy: MarketRegimePolicy,
    points: Sequence[MarketRegimePoint],
    equity_curve: Sequence[EquityPoint],
) -> tuple[MarketRegimePoint, ...]:
    """Revalidate one observed slice against aligned close observations."""

    if type(policy) is not MarketRegimePolicy:
        raise ValueError("market-regime policy is invalid")
    policy_candidate = copy(policy)
    try:
        MarketRegimePolicy.__post_init__(policy_candidate)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("market-regime policy is invalid") from None
    if policy_candidate != policy:
        raise ValueError("market-regime policy is not canonical")

    typed_points = tuple(points)
    typed_equity = tuple(equity_curve)
    if len(typed_points) != len(typed_equity):
        raise ValueError("market-regime output is not aligned to evaluated candles")
    if any(type(point) is not MarketRegimePoint for point in typed_points):
        raise ValueError("market-regime point is invalid")
    if any(
        left.event_time >= right.event_time for left, right in zip(typed_points, typed_points[1:])
    ):
        raise ValueError("market-regime event times must be strictly chronological")

    classified_seen = False
    with indicator_decimal_context():
        for point, equity_point in zip(
            typed_points,
            typed_equity,
            strict=True,
        ):
            point_candidate = copy(point)
            try:
                MarketRegimePoint.__post_init__(point_candidate)
            except (AttributeError, TypeError, ValueError):
                raise ValueError("market-regime point is invalid") from None
            if point_candidate != point:
                raise ValueError("market-regime point is not canonical")
            if point.event_time != equity_point.event_time:
                raise ValueError("market-regime event times are not aligned to evaluated candles")
            if point.regime is MarketRegimeKind.WARMUP:
                if classified_seen:
                    raise ValueError("market-regime warmup cannot resume after classification")
                continue
            classified_seen = True

            fast_ema = _required_regime_metric(point.fast_ema)
            slow_ema = _required_regime_metric(point.slow_ema)
            atr = _required_regime_metric(point.atr)
            atr_ratio = _required_regime_metric(point.atr_ratio)
            trend_strength = _required_regime_metric(point.trend_strength)
            expected_atr_ratio = contextual(atr / equity_point.close_price)
            expected_trend_strength = (
                Decimal("0") if atr == Decimal("0") else contextual(abs(fast_ema - slow_ema) / atr)
            )
            if atr_ratio >= policy.volatile_atr_ratio:
                expected_regime = MarketRegimeKind.VOLATILE
                expected_direction = TrendDirection.NONE
            elif trend_strength >= policy.trend_strength_threshold and fast_ema != slow_ema:
                expected_regime = MarketRegimeKind.TREND
                expected_direction = (
                    TrendDirection.UP if fast_ema > slow_ema else TrendDirection.DOWN
                )
            else:
                expected_regime = MarketRegimeKind.RANGE
                expected_direction = TrendDirection.NONE
            if atr_ratio != expected_atr_ratio or trend_strength != expected_trend_strength:
                raise ValueError(
                    "market-regime explainability metrics diverge from evaluated closes"
                )
            if (
                point.regime is not expected_regime
                or point.trend_direction is not expected_direction
            ):
                raise ValueError("market-regime classification diverges from published metrics")

    return typed_points


def rebuild_market_regime_observations(
    policy: MarketRegimePolicy,
    candles: Iterable[Candle],
    *,
    context_range: DataRange,
    evaluation_range: DataRange,
) -> tuple[MarketRegimePoint, ...]:
    """Rebuild the exact observed slice from immutable closed candles."""

    if type(context_range) is not DataRange or type(evaluation_range) is not DataRange:
        raise ValueError("market-regime ranges are invalid")
    if evaluation_range.start < context_range.start or evaluation_range.end != context_range.end:
        raise ValueError("market-regime ranges are inconsistent")
    accumulator = MarketRegimeAccumulator(policy)
    points: list[MarketRegimePoint] = []
    for candle in candles:
        if candle.open_time < context_range.start:
            continue
        if candle.open_time >= context_range.end:
            break
        point = accumulator.update(candle)
        if evaluation_range.start <= candle.open_time < evaluation_range.end:
            points.append(point)
    return tuple(points)


def _required_regime_metric(value: Decimal | None) -> Decimal:
    if value is None:
        raise ValueError("classified market-regime metrics are incomplete")
    return value


def _validated_market_regimes(
    config: BacktestConfig,
    execution: BacktestExecutionResult,
) -> tuple[MarketRegimePoint, ...] | None:
    policy = market_regime_policy_for(config)
    try:
        points = tuple(execution.market_regimes)
    except (AttributeError, TypeError):
        raise ValueError("market-regime output is invalid") from None
    if policy is None:
        if points:
            raise ValueError("legacy backtests must not expose market regimes")
        return None
    try:
        validated = validate_market_regime_observations(
            policy,
            points,
            execution.equity_curve,
        )
    except (AttributeError, TypeError, ValueError):
        raise ValueError("market-regime output is invalid") from None
    if len(validated) != execution.candles_processed:
        raise ValueError("market-regime output is not aligned to evaluated candles")
    return validated


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
