"""Deterministic Phase 3B comparison-report tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.query import BacktestRunReader
from app.backtesting.reports import (
    ComparisonMetric,
    build_comparison_report,
    comparison_entry_from_summary,
    comparison_report_from_mapping,
    normalize_comparison_run_ids,
)
from app.backtesting.serialization import canonical_value


def _summary(
    token: str,
    *,
    total_return: str,
    sharpe_ratio: str | None,
    schema_version: int = 2,
) -> dict[str, object]:
    metrics: dict[str, object] = {
        "final_equity": str(Decimal("1000") * (Decimal("1") + Decimal(total_return) / 100)),
        "total_return": total_return,
        "net_profit": str(Decimal("10") * Decimal(total_return)),
        "maximum_drawdown_pct": "5",
        "number_of_closed_trades": 3,
        "win_rate": "66.6666666667",
        "profit_factor": "2",
        "turnover": "1.5",
    }
    if schema_version >= 2:
        metrics.update(
            {
                "cagr": total_return,
                "annualized_volatility": "12",
                "sharpe_ratio": sharpe_ratio,
                "sortino_ratio": sharpe_ratio,
            }
        )
    return {
        "run_id": token * 64,
        "status": "COMPLETE",
        "engine_version": "3b-1",
        "schema_version": schema_version,
        "snapshot_id": "d" * 64,
        "dataset_key": "derived:binance:spot:BTC/USDT:1h",
        "dataset_version": "e" * 64,
        "data_range": {
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-02-01T00:00:00+00:00",
        },
        "strategy": {"name": "no-op", "version": "1", "parameters": []},
        "initial_capital": "1000",
        "metrics": metrics,
        "logical_result_checksum": "f" * 64,
    }


def test_report_sorts_defined_metrics_and_places_nulls_last() -> None:
    report = build_comparison_report(
        (
            _summary("a", total_return="10", sharpe_ratio=None),
            _summary("c", total_return="5", sharpe_ratio="2"),
            _summary("b", total_return="5", sharpe_ratio="2"),
        ),
        sort_by=ComparisonMetric.SHARPE_RATIO,
    )

    assert report.contract_version == 1
    assert report.same_snapshot is True
    assert report.same_data_range is True
    assert report.same_initial_capital is True
    assert [entry.run_id for entry in report.entries] == ["b" * 64, "c" * 64, "a" * 64]
    assert report.entries[-1].sharpe_ratio is None


def test_report_supports_ascending_order_with_stable_run_id_ties() -> None:
    report = build_comparison_report(
        (
            _summary("c", total_return="5", sharpe_ratio="1"),
            _summary("a", total_return="10", sharpe_ratio="1"),
            _summary("b", total_return="5", sharpe_ratio="1"),
        ),
        sort_by=ComparisonMetric.TOTAL_RETURN,
        descending=False,
    )

    assert [entry.run_id for entry in report.entries] == ["b" * 64, "c" * 64, "a" * 64]


def test_schema_one_summary_projects_advanced_metrics_as_null() -> None:
    report = build_comparison_report(
        (
            _summary("a", total_return="1", sharpe_ratio=None, schema_version=1),
            _summary("b", total_return="2", sharpe_ratio="3"),
        ),
        sort_by=ComparisonMetric.CAGR,
    )

    assert report.entries[0].run_id == "b" * 64
    assert report.entries[1].cagr is None
    assert report.entries[1].annualized_volatility is None


def test_comparison_requires_unique_bounded_run_ids() -> None:
    with pytest.raises(ValueError, match="between 2 and 100"):
        normalize_comparison_run_ids(("a" * 64,))
    with pytest.raises(ValueError, match="unique"):
        normalize_comparison_run_ids(("a" * 64, "a" * 64))
    with pytest.raises(ValueError, match="between 2 and 100"):
        normalize_comparison_run_ids(tuple(f"{index:064x}" for index in range(101)))


def test_malformed_verified_summary_is_rejected_as_corrupt() -> None:
    malformed = _summary("a", total_return="1", sharpe_ratio="1")
    malformed["metrics"] = {"total_return": "1"}

    with pytest.raises(BacktestResultCorruptError):
        build_comparison_report((malformed, _summary("b", total_return="2", sharpe_ratio="2")))


def test_reader_compares_only_after_inspecting_every_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = BacktestRunReader(tmp_path)
    summaries = {
        "a" * 64: _summary("a", total_return="1", sharpe_ratio="1"),
        "b" * 64: _summary("b", total_return="2", sharpe_ratio="2"),
    }
    inspected: list[str] = []

    def inspect(run_id: str) -> dict[str, object]:
        inspected.append(run_id)
        return summaries[run_id]

    monkeypatch.setattr(reader, "inspect", inspect)
    report = reader.compare(
        ("a" * 64, "b" * 64),
        sort_by=ComparisonMetric.TOTAL_RETURN,
    )

    assert inspected == ["a" * 64, "b" * 64]
    assert report.entries[0].run_id == "b" * 64


def test_report_marks_non_equivalent_comparison_scope() -> None:
    first = _summary("a", total_return="1", sharpe_ratio="1")
    second = _summary("b", total_return="2", sharpe_ratio="2")
    second["snapshot_id"] = "c" * 64
    second["initial_capital"] = "2000"
    second["data_range"] = {
        "start": "2026-01-02T00:00:00+00:00",
        "end": "2026-02-02T00:00:00+00:00",
    }

    report = build_comparison_report((first, second))

    assert report.same_snapshot is False
    assert report.same_data_range is False
    assert report.same_initial_capital is False


def test_serialized_report_rejects_unknown_fields() -> None:
    report = build_comparison_report(
        (
            _summary("a", total_return="1", sharpe_ratio="1"),
            _summary("b", total_return="2", sharpe_ratio="2"),
        )
    )
    value = canonical_value(report)
    assert isinstance(value, dict)
    value["unexpected"] = True

    with pytest.raises(BacktestResultCorruptError):
        comparison_report_from_mapping(value)


def test_verified_summary_projection_is_public_and_canonical() -> None:
    summary = _summary("a", total_return="10", sharpe_ratio="2")

    entry = comparison_entry_from_summary(summary)

    assert entry.run_id == "a" * 64
    assert entry.dataset_key == "derived:binance:spot:BTC/USDT:1h"
    assert entry.total_return == Decimal("10")


def test_verified_summary_projection_rejects_noncomplete_status() -> None:
    summary = _summary("a", total_return="10", sharpe_ratio="2")
    summary["status"] = "RUNNING"

    with pytest.raises(BacktestResultCorruptError):
        comparison_entry_from_summary(summary)
