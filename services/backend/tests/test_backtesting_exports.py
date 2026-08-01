"""Deterministic Phase 3B comparison-export tests."""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from app.backtesting.errors import BacktestComparisonExportCorruptError
from app.backtesting.exports import (
    ComparisonReportExportStore,
    ComparisonReportExportVerifier,
    build_comparison_report_id,
    render_comparison_csv,
)
from app.backtesting.reports import (
    BacktestComparisonReport,
    ComparisonMetric,
    build_comparison_report,
)
from app.backtesting.serialization import (
    canonical_json_bytes,
    file_checksum,
    sha256_bytes,
)


def _summary(token: str, *, total_return: str, sharpe_ratio: str | None) -> dict[str, object]:
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
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sharpe_ratio,
            "number_of_closed_trades": 3,
            "win_rate": "66.6666666667",
            "profit_factor": "2",
            "turnover": "1.5",
        },
        "logical_result_checksum": ("f" if token == "a" else "9") * 64,
    }


def _report() -> BacktestComparisonReport:
    return build_comparison_report(
        (
            _summary("a", total_return="1", sharpe_ratio=None),
            _summary("b", total_return="2", sharpe_ratio="3"),
        ),
        sort_by=ComparisonMetric.SHARPE_RATIO,
    )


def test_report_id_is_content_addressed_and_stable() -> None:
    first = _report()
    second = build_comparison_report(
        (
            _summary("b", total_return="2", sharpe_ratio="3"),
            _summary("a", total_return="1", sharpe_ratio=None),
        ),
        sort_by=ComparisonMetric.SHARPE_RATIO,
    )

    assert build_comparison_report_id(first) == build_comparison_report_id(second)
    assert len(build_comparison_report_id(first)) == 64


def test_csv_is_stable_and_uses_blank_for_null_metrics() -> None:
    rendered = render_comparison_csv(_report()).decode("utf-8")
    lines = rendered.splitlines()

    assert lines[0].startswith("rank,run_id,snapshot_id")
    assert lines[1].startswith(f"1,{'b' * 64},")
    assert lines[2].startswith(f"2,{'a' * 64},")
    rows = list(csv.DictReader(StringIO(rendered)))
    assert rows[0]["sharpe_ratio"] == "3"
    assert rows[0]["sortino_ratio"] == "3"
    assert rows[1]["sharpe_ratio"] == ""
    assert rows[1]["sortino_ratio"] == ""


def test_publish_and_verify_exact_export(tmp_path: Path) -> None:
    report = _report()
    published = ComparisonReportExportStore(tmp_path).publish(report)
    root = tmp_path / "market" / published.relative_path

    assert published.reused is False
    assert set(path.name for path in root.iterdir()) == {
        "manifest.json",
        "report.json",
        "report.csv",
    }
    verification = ComparisonReportExportVerifier(tmp_path).verify(published.report_id)
    assert verification.report_id == published.report_id
    assert verification.run_count == 2


def test_publish_is_idempotent_for_same_report(tmp_path: Path) -> None:
    store = ComparisonReportExportStore(tmp_path)

    first = store.publish(_report())
    second = store.publish(_report())

    assert first.report_id == second.report_id
    assert first.reused is False
    assert second.reused is True


def test_verifier_rejects_modified_csv(tmp_path: Path) -> None:
    published = ComparisonReportExportStore(tmp_path).publish(_report())
    path = tmp_path / "market" / published.relative_path / "report.csv"
    path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    with pytest.raises(BacktestComparisonExportCorruptError):
        ComparisonReportExportVerifier(tmp_path).verify(published.report_id)


def test_verifier_rejects_noncanonical_report_payload_even_with_updated_checksums(
    tmp_path: Path,
) -> None:
    published = ComparisonReportExportStore(tmp_path).publish(_report())
    root = tmp_path / "market" / published.relative_path
    report_path = root / "report.json"
    manifest_path = root / "manifest.json"

    report_envelope = json.loads(report_path.read_text(encoding="utf-8"))
    report_payload = report_envelope["comparison_report"]
    report_payload["unexpected"] = True
    report_envelope["checksum"] = sha256_bytes(canonical_json_bytes(report_payload))
    report_path.write_bytes(canonical_json_bytes(report_envelope))

    manifest_envelope = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload = manifest_envelope["comparison_export_manifest"]
    for artifact in manifest_payload["artifacts"]:
        if artifact["relative_path"] == "report.json":
            artifact["checksum"] = file_checksum(report_path)
            artifact["size_bytes"] = report_path.stat().st_size
    manifest_envelope["checksum"] = sha256_bytes(canonical_json_bytes(manifest_payload))
    manifest_path.write_bytes(canonical_json_bytes(manifest_envelope))

    with pytest.raises(BacktestComparisonExportCorruptError):
        ComparisonReportExportVerifier(tmp_path).verify(published.report_id)
