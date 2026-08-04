"""Deterministic asset-level performance aggregation tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.backtesting.asset_performance import (
    AssetPerformanceRun,
    asset_performance_report_from_mapping,
    asset_performance_run,
    build_asset_performance_report,
    build_asset_performance_report_from_summaries,
    normalize_asset_performance_run_ids,
)
from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.reports import BacktestComparisonEntry
from app.backtesting.serialization import canonical_value


def _summary(
    index: int,
    *,
    dataset_key: str = "derived:binance:spot:BTC/USDT:1h",
    initial_capital: str = "1000",
    net_profit: str = "100",
) -> dict[str, object]:
    initial = Decimal(initial_capital)
    profit = Decimal(net_profit)
    return {
        "run_id": f"{index:064x}",
        "status": "COMPLETE",
        "engine_version": "5-07-core",
        "schema_version": 2,
        "snapshot_id": f"{index + 100:064x}",
        "dataset_key": dataset_key,
        "dataset_version": f"{index + 200:064x}",
        "evaluation_range": {
            "start": f"2026-01-{index:02d}T00:00:00+00:00",
            "end": f"2026-02-{index:02d}T00:00:00+00:00",
        },
        "strategy": {"name": "no-op", "version": "1", "parameters": []},
        "initial_capital": str(initial),
        "metrics": {
            "final_equity": str(initial + profit),
            "total_return": str(profit / initial * Decimal("100")),
            "net_profit": str(profit),
            "maximum_drawdown_pct": "5",
            "cagr": None,
            "annualized_volatility": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "number_of_closed_trades": 2,
            "win_rate": None,
            "profit_factor": None,
            "turnover": "1",
        },
        "logical_result_checksum": f"{index + 300:064x}",
    }


def _entry(
    index: int,
    *,
    dataset_key: str,
    initial_capital: str,
    net_profit: str,
    drawdown: str,
    trades: int,
    strategy: str = "no-op",
) -> BacktestComparisonEntry:
    initial = Decimal(initial_capital)
    profit = Decimal(net_profit)
    return BacktestComparisonEntry(
        run_id=f"{index:064x}",
        snapshot_id=f"{index + 100:064x}",
        dataset_key=dataset_key,
        dataset_version=f"{index + 200:064x}",
        engine_version="5-07-core",
        schema_version=2,
        data_start=datetime(2026, 1, index, tzinfo=UTC),
        data_end=datetime(2026, 2, index, tzinfo=UTC),
        strategy_name=strategy,
        strategy_version="1",
        initial_capital=initial,
        final_equity=initial + profit,
        total_return=profit / initial * Decimal("100"),
        net_profit=profit,
        maximum_drawdown_pct=Decimal(drawdown),
        cagr=None,
        annualized_volatility=None,
        sharpe_ratio=None,
        sortino_ratio=None,
        number_of_closed_trades=trades,
        win_rate=None,
        profit_factor=None,
        turnover=Decimal("1"),
        logical_result_checksum=f"{index + 300:064x}",
    )


def test_run_projection_extracts_canonical_asset_and_timeframe() -> None:
    run = asset_performance_run(
        _entry(
            1,
            dataset_key="derived:binance:spot:BTC/USDT:1h",
            initial_capital="1000",
            net_profit="100",
            drawdown="5",
            trades=2,
        )
    )

    assert run.asset.exchange == "binance"
    assert run.asset.market_type == "spot"
    assert run.asset.symbol == "BTC/USDT"
    assert run.dataset_kind == "derived"
    assert run.timeframe == "1h"


def test_report_groups_assets_and_uses_capital_weighted_return() -> None:
    report = build_asset_performance_report(
        (
            _entry(
                3,
                dataset_key="derived:binance:spot:ETH/USDT:1h",
                initial_capital="500",
                net_profit="-50",
                drawdown="12",
                trades=4,
            ),
            _entry(
                2,
                dataset_key="derived:binance:spot:BTC/USDT:4h",
                initial_capital="3000",
                net_profit="0",
                drawdown="8",
                trades=1,
                strategy="hold",
            ),
            _entry(
                1,
                dataset_key="derived:binance:spot:BTC/USDT:1h",
                initial_capital="1000",
                net_profit="100",
                drawdown="5",
                trades=2,
            ),
        )
    )

    assert report.contract_version == 1
    assert report.run_count == 3
    assert report.asset_count == 2
    assert [group.asset.symbol for group in report.assets] == ["BTC/USDT", "ETH/USDT"]

    btc = report.assets[0]
    assert btc.run_count == 2
    assert btc.timeframe_count == 2
    assert btc.strategy_count == 2
    assert btc.total_initial_capital == Decimal("4000")
    assert btc.total_final_equity == Decimal("4100")
    assert btc.total_net_profit == Decimal("100")
    assert btc.capital_weighted_return == Decimal("2.5")
    assert (btc.profitable_runs, btc.losing_runs, btc.flat_runs) == (1, 0, 1)
    assert btc.number_of_closed_trades == 3
    assert btc.maximum_drawdown_pct == Decimal("8")
    assert btc.best_run_id == f"{1:064x}"
    assert btc.worst_run_id == f"{2:064x}"


def test_report_identity_is_independent_from_input_order() -> None:
    first = _entry(
        1,
        dataset_key="derived:binance:spot:BTC/USDT:1h",
        initial_capital="1000",
        net_profit="100",
        drawdown="5",
        trades=2,
    )
    second = _entry(
        2,
        dataset_key="derived:binance:spot:ETH/USDT:1h",
        initial_capital="1000",
        net_profit="-100",
        drawdown="10",
        trades=3,
    )

    left = build_asset_performance_report((first, second))
    right = build_asset_performance_report((second, first))

    assert left == right
    assert left.report_id == right.report_id


def test_report_rejects_duplicate_runs_and_noncanonical_dataset_keys() -> None:
    entry = _entry(
        1,
        dataset_key="derived:binance:spot:BTC/USDT:1h",
        initial_capital="1000",
        net_profit="100",
        drawdown="5",
        trades=2,
    )
    with pytest.raises(ValueError, match="unique"):
        build_asset_performance_report((entry, entry))

    hostile = _entry(
        2,
        dataset_key="derived:Binance:spot:btc/usdt:1h",
        initial_capital="1000",
        net_profit="100",
        drawdown="5",
        trades=2,
    )
    with pytest.raises(ValueError, match="canonical"):
        asset_performance_run(hostile)


def test_serialized_report_round_trip_and_tamper_rejection() -> None:
    report = build_asset_performance_report(
        (
            _entry(
                1,
                dataset_key="derived:binance:spot:BTC/USDT:1h",
                initial_capital="1000",
                net_profit="100",
                drawdown="5",
                trades=2,
            ),
        )
    )
    value = canonical_value(report)
    assert isinstance(value, dict)
    assert asset_performance_report_from_mapping(value) == report

    assets = value["assets"]
    assert isinstance(assets, list)
    group = assets[0]
    assert isinstance(group, dict)
    group["total_net_profit"] = "101"

    with pytest.raises(BacktestResultCorruptError):
        asset_performance_report_from_mapping(value)


def test_report_rejects_boolean_count_mutation() -> None:
    report = build_asset_performance_report(
        (
            _entry(
                1,
                dataset_key="derived:binance:spot:BTC/USDT:1h",
                initial_capital="1000",
                net_profit="100",
                drawdown="5",
                trades=2,
            ),
        )
    )
    object.__setattr__(report, "run_count", True)

    with pytest.raises(ValueError, match="integers"):
        report.__post_init__()


def test_hostile_run_mutation_is_revalidated_inside_group() -> None:
    report = build_asset_performance_report(
        (
            _entry(
                1,
                dataset_key="derived:binance:spot:BTC/USDT:1h",
                initial_capital="1000",
                net_profit="100",
                drawdown="5",
                trades=2,
            ),
        )
    )
    run = report.assets[0].runs[0]
    object.__setattr__(run, "net_profit", Decimal("101"))

    with pytest.raises(ValueError, match="inconsistent"):
        AssetPerformanceRun.__post_init__(run)


def test_report_builds_directly_from_verified_summary_contracts() -> None:
    report = build_asset_performance_report_from_summaries(
        (
            _summary(2, dataset_key="derived:binance:spot:ETH/USDT:4h", net_profit="-25"),
            _summary(1),
        )
    )

    assert report.run_count == 2
    assert report.asset_count == 2
    assert [group.asset.symbol for group in report.assets] == ["BTC/USDT", "ETH/USDT"]


def test_summary_bridge_rejects_unverified_or_malformed_payloads() -> None:
    incomplete = _summary(1)
    incomplete["status"] = "RUNNING"

    with pytest.raises(BacktestResultCorruptError):
        build_asset_performance_report_from_summaries((incomplete,))

    malformed = _summary(2)
    malformed["metrics"] = {"net_profit": "1"}

    with pytest.raises(BacktestResultCorruptError):
        build_asset_performance_report_from_summaries((malformed,))


def test_run_id_normalization_is_bounded_unique_and_ordered() -> None:
    assert normalize_asset_performance_run_ids(("b" * 64, "a" * 64)) == (
        "a" * 64,
        "b" * 64,
    )
    with pytest.raises(ValueError, match="unique"):
        normalize_asset_performance_run_ids(("a" * 64, "a" * 64))
    with pytest.raises(ValueError, match="between 1 and 100"):
        normalize_asset_performance_run_ids(())
