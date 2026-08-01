"""Bounded Phase 3B visualization-contract tests."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import EquityPoint
from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.visualization import build_backtest_visualization
from tests.market_data_helpers import utc


def _summary() -> dict[str, object]:
    return {
        "run_id": "a" * 64,
        "snapshot_id": "b" * 64,
        "dataset_key": "derived:binance:spot:BTC/USDT:1h",
        "dataset_version": "c" * 64,
        "data_range": {
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-02T00:00:00+00:00",
        },
        "strategy": {"name": "no-op", "version": "1", "parameters": []},
        "logical_result_checksum": "d" * 64,
        "metrics": {
            "final_equity": "1010",
            "total_return": "1",
            "maximum_drawdown_pct": "2",
        },
    }


def _points(count: int) -> tuple[EquityPoint, ...]:
    start = utc(2026, 1, 1)
    return tuple(
        EquityPoint(
            candle_index=index,
            event_time=start + timedelta(minutes=index + 1),
            close_price=Decimal(100 + index),
            quote_cash=Decimal("1000"),
            base_quantity=Decimal("0"),
            equity=Decimal(1000 + index),
            peak_equity=Decimal(1000 + index),
            drawdown=Decimal("0"),
            drawdown_pct=Decimal("0"),
        )
        for index in range(count)
    )


def test_visualization_uniformly_bounds_points_and_preserves_endpoints() -> None:
    visualization = build_backtest_visualization(_summary(), _points(10), max_points=4)

    assert visualization.contract_version == 1
    assert visualization.source_point_count == 10
    assert visualization.point_count == 4
    assert visualization.downsampled is True
    assert [point.candle_index for point in visualization.points] == [0, 3, 6, 9]
    assert visualization.points[0].event_time < visualization.points[-1].event_time


def test_visualization_keeps_complete_small_curve() -> None:
    visualization = build_backtest_visualization(_summary(), _points(3), max_points=10)

    assert visualization.point_count == 3
    assert visualization.downsampled is False
    assert [point.candle_index for point in visualization.points] == [0, 1, 2]


def test_visualization_rejects_unbounded_or_empty_input() -> None:
    with pytest.raises(ValueError, match="between 2 and 2000"):
        build_backtest_visualization(_summary(), _points(3), max_points=1)
    with pytest.raises(ValueError, match="between 2 and 2000"):
        build_backtest_visualization(_summary(), _points(3), max_points=2001)
    with pytest.raises(BacktestResultCorruptError, match="vazia"):
        build_backtest_visualization(_summary(), (), max_points=10)


def test_visualization_rejects_malformed_verified_summary() -> None:
    summary = _summary()
    summary["metrics"] = {"final_equity": "1010"}

    with pytest.raises(BacktestResultCorruptError):
        build_backtest_visualization(summary, _points(2), max_points=2)


def test_reader_verifies_before_loading_bounded_curve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.backtesting.query as query_module
    from app.backtesting.domain import BacktestRunId
    from app.backtesting.query import BacktestRunReader
    from app.backtesting.verifier import BacktestVerification

    reader = BacktestRunReader(tmp_path)
    reader._run_root("a" * 64).mkdir(parents=True)
    verification = BacktestVerification(
        run_id=BacktestRunId("a" * 64),
        logical_result_checksum="d" * 64,
        artifact_count=7,
        order_count=0,
        fill_count=0,
        ledger_count=1,
        trade_count=0,
        candle_count=3,
    )
    calls: list[str] = []

    def verify(run_id: str) -> BacktestVerification:
        calls.append(f"verify:{run_id}")
        return verification

    def inspect_verified(value: BacktestVerification) -> dict[str, object]:
        assert value is verification
        calls.append("summary")
        return _summary()

    def read_equity(path: Path) -> tuple[EquityPoint, ...]:
        calls.append(f"equity:{path.name}")
        return _points(3)

    monkeypatch.setattr(reader._verifier, "verify", verify)
    monkeypatch.setattr(reader, "_inspect_verified", inspect_verified)
    monkeypatch.setattr(query_module, "read_equity_artifact", read_equity)

    visualization = reader.visualization("a" * 64, max_points=2)

    assert visualization.point_count == 2
    assert calls == [f"verify:{'a' * 64}", "equity:equity.parquet", "summary"]
