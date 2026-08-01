"""Explicit bounded Phase 3B batch-comparison tests."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.comparison_batch import (
    ComparisonBatchGroup,
    ComparisonBatchRequest,
    build_comparison_batch,
    comparison_batch_request_from_mapping,
    load_comparison_batch_request,
)
from app.backtesting.query import BacktestRunReader
from app.backtesting.reports import ComparisonMetric, build_comparison_report


def _summary(token: str, total_return: str) -> dict[str, object]:
    return {
        "run_id": token * 64,
        "status": "COMPLETE",
        "engine_version": "3b-1",
        "schema_version": 2,
        "snapshot_id": "d" * 64,
        "dataset_key": "derived:binance:spot:BTC/USDT:1h",
        "dataset_version": "e" * 64,
        "data_range": {
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-02-01T00:00:00+00:00",
        },
        "strategy": {"name": "no-op", "version": "1", "parameters": []},
        "initial_capital": "1000",
        "metrics": {
            "final_equity": str(Decimal("1000") * (Decimal("1") + Decimal(total_return) / 100)),
            "total_return": total_return,
            "net_profit": str(Decimal("10") * Decimal(total_return)),
            "maximum_drawdown_pct": "5",
            "cagr": total_return,
            "annualized_volatility": "12",
            "sharpe_ratio": total_return,
            "sortino_ratio": total_return,
            "number_of_closed_trades": 3,
            "win_rate": "66",
            "profit_factor": "2",
            "turnover": "1.5",
        },
        "logical_result_checksum": token * 64,
    }


def _request() -> ComparisonBatchRequest:
    return ComparisonBatchRequest(
        contract_version=1,
        groups=(
            ComparisonBatchGroup(
                "returns",
                ("a" * 64, "b" * 64),
                ComparisonMetric.TOTAL_RETURN,
                True,
            ),
            ComparisonBatchGroup(
                "risk-adjusted",
                ("b" * 64, "c" * 64),
                ComparisonMetric.SHARPE_RATIO,
                False,
            ),
        ),
    )


def test_request_decodes_strict_explicit_groups_and_defaults() -> None:
    request = comparison_batch_request_from_mapping(
        {
            "contract_version": 1,
            "groups": [
                {
                    "name": "baseline",
                    "run_ids": ["a" * 64, "b" * 64],
                }
            ],
        }
    )

    assert request.groups[0].sort_by is ComparisonMetric.TOTAL_RETURN
    assert request.groups[0].descending is True
    assert request.unique_run_ids == ("a" * 64, "b" * 64)


def test_request_rejects_unknown_fields_duplicate_names_and_excess_groups() -> None:
    with pytest.raises(ValueError, match="fields"):
        comparison_batch_request_from_mapping(
            {"contract_version": 1, "groups": [], "optimize": True}
        )
    with pytest.raises(ValueError, match="names"):
        ComparisonBatchRequest(
            1,
            (
                ComparisonBatchGroup("same", ("a" * 64, "b" * 64)),
                ComparisonBatchGroup("same", ("b" * 64, "c" * 64)),
            ),
        )
    with pytest.raises(ValueError, match="between 1 and 20"):
        ComparisonBatchRequest(
            1,
            tuple(
                ComparisonBatchGroup(
                    f"group-{index}",
                    (f"{index + 1:064x}", f"{index + 101:064x}"),
                )
                for index in range(21)
            ),
        )


def test_request_file_is_bounded_and_must_be_valid_json(tmp_path: Path) -> None:
    request_path = tmp_path / "batch.json"
    request_path.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "groups": [
                    {
                        "name": "baseline",
                        "run_ids": ["a" * 64, "b" * 64],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_comparison_batch_request(request_path).groups[0].name == "baseline"

    request_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="file is invalid"):
        load_comparison_batch_request(request_path)


def test_batch_result_has_deterministic_content_bound_id() -> None:
    request = _request()
    reports = (
        build_comparison_report(
            (_summary("a", "1"), _summary("b", "2")),
            sort_by=ComparisonMetric.TOTAL_RETURN,
        ),
        build_comparison_report(
            (_summary("b", "2"), _summary("c", "3")),
            sort_by=ComparisonMetric.SHARPE_RATIO,
            descending=False,
        ),
    )

    first = build_comparison_batch(request, reports)
    second = build_comparison_batch(request, reports)

    assert first == second
    assert len(first.batch_id) == 64
    assert first.group_count == 2
    assert first.unique_run_count == 3


def test_reader_verifies_each_unique_run_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = BacktestRunReader(tmp_path)
    summaries = {
        "a" * 64: _summary("a", "1"),
        "b" * 64: _summary("b", "2"),
        "c" * 64: _summary("c", "3"),
    }
    inspected: list[str] = []

    def inspect(run_id: str) -> dict[str, object]:
        inspected.append(run_id)
        return summaries[run_id]

    monkeypatch.setattr(reader, "inspect", inspect)
    result = reader.compare_batch(_request())

    assert inspected == ["a" * 64, "b" * 64, "c" * 64]
    assert result.unique_run_count == 3
    assert result.groups[0].report.entries[0].run_id == "b" * 64
