"""Verified bounded backtest artifact-query tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.artifacts import BacktestArtifactStore
from app.backtesting.domain import (
    BacktestConfig,
    EquityPoint,
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    RiskLimits,
    SlippageModel,
    StrategyDescriptor,
)
from app.backtesting.engine import BacktestExecutionResult
from app.backtesting.errors import BacktestRunMissingError
from app.backtesting.ledger import BacktestLedger
from app.backtesting.portfolio import initialize_portfolio, mark_to_market
from app.backtesting.query import BacktestRunReader
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import DataRange
from tests.market_data_helpers import utc


@dataclass
class _SnapshotReader:
    snapshot: DatasetSnapshot

    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        assert snapshot_id == self.snapshot.snapshot_id
        return self.snapshot

    def verify_unchanged(self) -> DatasetSnapshot:
        return self.snapshot


def _snapshot() -> DatasetSnapshot:
    start = utc(2026, 1, 1)
    return DatasetSnapshot(
        snapshot_id="a" * 64,
        dataset_key="derived:binance:spot:BTC/USDT:1h",
        dataset_version="b" * 64,
        checksum="c" * 64,
        data_range=DataRange(start, start + timedelta(hours=1)),
        partitions=("partitions/year=2026/month=01/candles.parquet",),
        manifest_path="dataset-manifest.json",
        created_at=start.isoformat(),
    )


def _config(snapshot: DatasetSnapshot) -> BacktestConfig:
    return BacktestConfig(
        snapshot_id=snapshot.snapshot_id,
        data_range=snapshot.data_range,
        strategy=StrategyDescriptor("no-op", "1"),
        initial_capital=Decimal("1000"),
        execution=ExecutionAssumptions(
            FeeModel(Decimal("0"), Decimal("0")),
            SlippageModel(fixed_bps=Decimal("0")),
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        risk_limits=RiskLimits(),
        history_window=10,
        max_candles=100,
        max_orders=100,
        max_events=1000,
        engine_version="phase3a-test",
        schema_version=1,
    )


def _execution(snapshot: DatasetSnapshot) -> BacktestExecutionResult:
    state = mark_to_market(initialize_portfolio(Decimal("1000")), Decimal("100"))
    ledger = BacktestLedger()
    ledger.record_initial_capital(Decimal("1000"), snapshot.data_range.start)
    event_time = snapshot.data_range.end - timedelta(milliseconds=1)
    ledger.record_mark(state, event_time=event_time, candle_index=0)
    ledger.record_mark(state, event_time=event_time, candle_index=0, final=True)
    point = EquityPoint(
        candle_index=0,
        event_time=event_time,
        close_price=Decimal("100"),
        quote_cash=state.quote_cash,
        base_quantity=state.base_quantity,
        equity=state.equity,
        peak_equity=state.peak_equity,
        drawdown=state.drawdown,
        drawdown_pct=state.drawdown_pct,
    )
    return BacktestExecutionResult(
        snapshot=snapshot,
        candles_processed=1,
        orders=(),
        fills=(),
        ledger=ledger.entries,
        equity_curve=(point,),
        final_portfolio=state.snapshot(),
        risk_halt=False,
    )


def test_reader_verifies_and_returns_bounded_summaries(tmp_path: Path) -> None:
    snapshot = _snapshot()

    def factory(_path: Path) -> _SnapshotReader:
        return _SnapshotReader(snapshot)

    result = BacktestArtifactStore(tmp_path, snapshot_factory=factory).publish(
        _config(snapshot),
        _execution(snapshot),
    )
    reader = BacktestRunReader(tmp_path, snapshot_factory=factory)

    summary = reader.inspect(result.run_id.value)
    orders = reader.orders(result.run_id.value, offset=0, limit=20)
    trades = reader.trades(result.run_id.value, offset=0, limit=20)

    assert summary["run_id"] == result.run_id.value
    assert summary["status"] == "COMPLETE"
    assert summary["candle_count"] == 1
    assert orders == {"offset": 0, "limit": 20, "total": 0, "items": [], "truncated": False}
    assert trades["items"] == []


def test_reader_rejects_missing_run_and_invalid_page(tmp_path: Path) -> None:
    reader = BacktestRunReader(tmp_path)

    with pytest.raises(BacktestRunMissingError):
        reader.verify("a" * 64)
    with pytest.raises(ValueError):
        reader.orders("a" * 64, offset=0, limit=0)
