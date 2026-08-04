"""Verified bounded reads of immutable backtest result artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from app.backtesting.comparison_batch import (
    BacktestComparisonBatch,
    ComparisonBatchRequest,
    build_comparison_batch,
)
from app.backtesting.domain import BacktestRunId
from app.backtesting.errors import BacktestResultCorruptError, BacktestRunMissingError
from app.backtesting.reports import (
    BacktestComparisonReport,
    ComparisonMetric,
    build_comparison_report,
    normalize_comparison_run_ids,
)
from app.backtesting.serialization import read_json_envelope
from app.backtesting.verifier import (
    BacktestResultVerifier,
    BacktestVerification,
    SnapshotFactory,
    read_equity_artifact,
)
from app.backtesting.visualization import BacktestVisualization, build_backtest_visualization
from app.market_data.filesystem import ensure_safe_path, market_root
from app.market_data.locks import DatasetLockManager


class BacktestRunReader:
    """Verify a run before exposing a bounded operational summary or event page."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("backtests"),
        lock_timeout_seconds: float = 30,
        lock_stale_after_seconds: float = 300,
        snapshot_factory: SnapshotFactory | None = None,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("backtest directory must be safe and relative")
        self._market = market_root(data_dir)
        self._root = ensure_safe_path(self._market, self._market / directory)
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
        )
        self._verifier = BacktestResultVerifier(
            data_dir,
            directory=directory,
            lock_timeout_seconds=lock_timeout_seconds,
            lock_stale_after_seconds=lock_stale_after_seconds,
            acquire_lock=False,
            snapshot_factory=snapshot_factory,
        )

    def verify(self, run_id: str) -> BacktestVerification:
        with self._verified(run_id) as verification:
            return verification

    def inspect(self, run_id: str) -> dict[str, object]:
        with self._verified(run_id) as verification:
            return self._inspect_verified(verification)

    def visualization(self, run_id: str, *, max_points: int) -> BacktestVisualization:
        """Verify and project one run while holding its immutable-result lock."""
        with self._verified(run_id) as verification:
            root = self._run_root(run_id)
            equity = read_equity_artifact(root / "equity.parquet")
            if len(equity) != verification.candle_count:
                raise BacktestResultCorruptError("A curva de equity possui contagem divergente.")
            return build_backtest_visualization(
                self._inspect_verified(verification),
                equity,
                max_points=max_points,
            )

    def compare_batch(self, request: ComparisonBatchRequest) -> BacktestComparisonBatch:
        """Verify each unique run once and build all explicit comparison groups."""
        summaries = {run_id: self.inspect(run_id) for run_id in request.unique_run_ids}
        reports = tuple(
            build_comparison_report(
                tuple(summaries[run_id] for run_id in group.run_ids),
                sort_by=group.sort_by,
                descending=group.descending,
            )
            for group in request.groups
        )
        return build_comparison_batch(request, reports)

    def _inspect_verified(self, verification: BacktestVerification) -> dict[str, object]:
        root = self._run_root(verification.run_id.value)
        manifest = self._envelope(root / "manifest.json", "manifest")
        config = self._envelope(root / "config.json", "config")
        result = self._envelope(root / "result.json", "result")
        evaluation_range = manifest.get("evaluation_range", manifest.get("data_range"))
        context_range = manifest.get("context_range", manifest.get("data_range"))
        payload: dict[str, object] = {
            "run_id": verification.run_id.value,
            "status": manifest.get("status"),
            "engine_version": manifest.get("engine_version"),
            "schema_version": manifest.get("schema_version"),
            "snapshot_id": manifest.get("snapshot_id"),
            "dataset_key": manifest.get("dataset_key"),
            "dataset_version": manifest.get("dataset_version"),
            "data_range": evaluation_range,
            "context_range": context_range,
            "evaluation_range": evaluation_range,
            "strategy": manifest.get("strategy"),
            "initial_capital": manifest.get("initial_capital"),
            "execution": manifest.get("execution"),
            "risk_limits": manifest.get("risk_limits"),
            "candle_count": verification.candle_count,
            "order_count": verification.order_count,
            "fill_count": verification.fill_count,
            "trade_count": verification.trade_count,
            "logical_result_checksum": verification.logical_result_checksum,
            "final_portfolio": result.get("final_portfolio"),
            "metrics": result.get("metrics"),
            "created_at": manifest.get("created_at"),
            "completed_at": manifest.get("completed_at"),
        }
        if "market_regime_policy" in config:
            payload["market_regime_policy"] = config["market_regime_policy"]
            payload["market_regime_count"] = verification.market_regime_count
        return payload

    def compare(
        self,
        run_ids: Sequence[str],
        *,
        sort_by: ComparisonMetric = ComparisonMetric.TOTAL_RETURN,
        descending: bool = True,
    ) -> BacktestComparisonReport:
        """Verify every run, then return one bounded deterministic comparison."""
        normalized = normalize_comparison_run_ids(run_ids)
        summaries = tuple(self.inspect(run_id) for run_id in normalized)
        return build_comparison_report(
            summaries,
            sort_by=sort_by,
            descending=descending,
        )

    def orders(self, run_id: str, *, offset: int, limit: int) -> dict[str, object]:
        self._validate_page(offset, limit)
        with self._verified(run_id) as verification:
            return self._page(
                self._run_root(run_id) / "orders.jsonl",
                offset=offset,
                limit=limit,
                total=verification.order_count,
            )

    def trades(self, run_id: str, *, offset: int, limit: int) -> dict[str, object]:
        self._validate_page(offset, limit)
        with self._verified(run_id) as verification:
            return self._page(
                self._run_root(run_id) / "trades.jsonl",
                offset=offset,
                limit=limit,
                total=verification.trade_count,
            )

    def regimes(self, run_id: str, *, offset: int, limit: int) -> dict[str, object]:
        """Verify and page persisted closed-candle regime observations."""

        self._validate_page(offset, limit)
        with self._verified(run_id) as verification:
            if verification.market_regime_count == 0:
                return {
                    "offset": offset,
                    "limit": limit,
                    "total": 0,
                    "items": [],
                    "truncated": False,
                }
            return self._page(
                self._run_root(run_id) / "regimes.jsonl",
                offset=offset,
                limit=limit,
                total=verification.market_regime_count,
            )

    def _page(
        self,
        path: Path,
        *,
        offset: int,
        limit: int,
        total: int,
    ) -> dict[str, object]:
        items: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    if index < offset:
                        continue
                    if len(items) >= limit:
                        break
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError
                    items.append(cast(dict[str, Any], value))
        except (OSError, ValueError, json.JSONDecodeError):
            raise BacktestResultCorruptError() from None
        return {
            "offset": offset,
            "limit": limit,
            "total": total,
            "items": items,
            "truncated": offset + len(items) < total,
        }

    @contextmanager
    def _verified(self, run_id: str) -> Iterator[BacktestVerification]:
        typed_run_id = BacktestRunId(run_id)
        with self._locks.acquire(f"backtest:{typed_run_id.value}"):
            if not self._run_root(typed_run_id.value).is_dir():
                raise BacktestRunMissingError()
            yield self._verifier.verify(typed_run_id.value)

    @staticmethod
    def _validate_page(offset: int, limit: int) -> None:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise ValueError("event pagination is invalid")

    def _require_run(self, run_id: str) -> None:
        BacktestRunId(run_id)
        if not self._run_root(run_id).is_dir():
            raise BacktestRunMissingError()

    def _run_root(self, run_id: str) -> Path:
        return ensure_safe_path(self._market, self._root / run_id)

    @staticmethod
    def _envelope(path: Path, key: str) -> dict[str, Any]:
        try:
            return read_json_envelope(path, key)
        except ValueError:
            raise BacktestResultCorruptError() from None
