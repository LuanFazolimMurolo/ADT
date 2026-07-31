"""Verified bounded reads of immutable backtest result artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from app.backtesting.domain import BacktestRunId
from app.backtesting.errors import BacktestResultCorruptError, BacktestRunMissingError
from app.backtesting.serialization import read_json_envelope
from app.backtesting.verifier import BacktestResultVerifier, BacktestVerification, SnapshotFactory
from app.market_data.filesystem import ensure_safe_path, market_root


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
        self._verifier = BacktestResultVerifier(
            data_dir,
            directory=directory,
            lock_timeout_seconds=lock_timeout_seconds,
            lock_stale_after_seconds=lock_stale_after_seconds,
            snapshot_factory=snapshot_factory,
        )

    def verify(self, run_id: str) -> BacktestVerification:
        self._require_run(run_id)
        return self._verifier.verify(run_id)

    def inspect(self, run_id: str) -> dict[str, object]:
        verification = self.verify(run_id)
        root = self._run_root(run_id)
        manifest = self._envelope(root / "manifest.json", "manifest")
        result = self._envelope(root / "result.json", "result")
        return {
            "run_id": verification.run_id.value,
            "status": manifest.get("status"),
            "snapshot_id": manifest.get("snapshot_id"),
            "dataset_key": manifest.get("dataset_key"),
            "dataset_version": manifest.get("dataset_version"),
            "data_range": manifest.get("data_range"),
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

    def orders(self, run_id: str, *, offset: int, limit: int) -> dict[str, object]:
        self._validate_page(offset, limit)
        verification = self.verify(run_id)
        return self._page(
            self._run_root(run_id) / "orders.jsonl",
            offset=offset,
            limit=limit,
            total=verification.order_count,
        )

    def trades(self, run_id: str, *, offset: int, limit: int) -> dict[str, object]:
        self._validate_page(offset, limit)
        verification = self.verify(run_id)
        return self._page(
            self._run_root(run_id) / "trades.jsonl",
            offset=offset,
            limit=limit,
            total=verification.trade_count,
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
