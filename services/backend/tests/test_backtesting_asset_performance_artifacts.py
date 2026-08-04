"""Immutable asset-performance report export tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import app.backtesting.asset_performance_artifacts as artifacts_module
from app.backtesting.asset_performance import (
    AssetPerformanceReport,
    build_asset_performance_report_from_summaries,
)
from app.backtesting.asset_performance_artifacts import (
    AssetPerformanceReportStore,
    AssetPerformanceReportVerifier,
)
from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.serialization import canonical_json_bytes, sha256_bytes


def _summary(index: int, *, symbol: str = "BTC/USDT") -> dict[str, object]:
    initial = Decimal("1000")
    profit = Decimal(index * 10)
    return {
        "run_id": f"{index:064x}",
        "status": "COMPLETE",
        "engine_version": "5-07",
        "schema_version": 2,
        "snapshot_id": f"{index + 100:064x}",
        "dataset_key": f"derived:binance:spot:{symbol}:1h",
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
            "number_of_closed_trades": index,
            "win_rate": None,
            "profit_factor": None,
            "turnover": "1",
        },
        "logical_result_checksum": f"{index + 300:064x}",
    }


def _report() -> AssetPerformanceReport:
    return build_asset_performance_report_from_summaries(
        (_summary(2, symbol="ETH/USDT"), _summary(1))
    )


def test_publish_is_atomic_verifiable_and_idempotent(tmp_path: Path) -> None:
    report = _report()

    def clock() -> datetime:
        return datetime(2026, 8, 4, tzinfo=UTC)

    store = AssetPerformanceReportStore(tmp_path, clock=clock)

    first = store.publish(report)
    target = tmp_path / "market" / first.relative_path

    assert first.report_id == report.report_id
    assert first.reused is False
    assert {path.name for path in target.iterdir()} == {"manifest.json", "report.json"}

    verifier = AssetPerformanceReportVerifier(tmp_path)
    assert verifier.inspect(report.report_id) == report
    verification = verifier.verify(report.report_id)
    assert verification.verified is True
    assert verification.run_count == 2
    assert verification.asset_count == 2
    assert verification.source_run_count == 2

    manifest_before = (target / "manifest.json").read_bytes()
    second = store.publish(report)
    assert second.reused is True
    assert (target / "manifest.json").read_bytes() == manifest_before


def test_verifier_rejects_payload_tampering_and_extra_files(tmp_path: Path) -> None:
    report = _report()
    result = AssetPerformanceReportStore(tmp_path).publish(report)
    target = tmp_path / "market" / result.relative_path
    report_path = target / "report.json"
    envelope = json.loads(report_path.read_text(encoding="utf-8"))
    envelope["report"]["run_count"] = 99
    report_path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(BacktestResultCorruptError):
        AssetPerformanceReportVerifier(tmp_path).verify(report.report_id)

    AssetPerformanceReportStore(tmp_path / "fresh").publish(report)
    fresh_target = tmp_path / "fresh" / "market" / result.relative_path
    (fresh_target / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(BacktestResultCorruptError):
        AssetPerformanceReportVerifier(tmp_path / "fresh").verify(report.report_id)


def test_verifier_rejects_noncanonical_envelope_bytes(tmp_path: Path) -> None:
    report = _report()
    result = AssetPerformanceReportStore(tmp_path).publish(report)
    report_path = tmp_path / "market" / result.relative_path / "report.json"
    envelope = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.write_text(
        json.dumps(envelope, indent=2, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(BacktestResultCorruptError):
        AssetPerformanceReportVerifier(tmp_path).verify(report.report_id)


def test_verifier_rejects_rechecksummed_manifest_source_mutation(tmp_path: Path) -> None:
    report = _report()
    result = AssetPerformanceReportStore(tmp_path).publish(report)
    manifest_path = tmp_path / "market" / result.relative_path / "manifest.json"
    envelope = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = envelope["manifest"]
    payload["source_runs"][0]["logical_result_checksum"] = "f" * 64
    envelope["checksum"] = sha256_bytes(canonical_json_bytes(payload))
    manifest_path.write_bytes(canonical_json_bytes(envelope))

    with pytest.raises(BacktestResultCorruptError, match="inconsistente"):
        AssetPerformanceReportVerifier(tmp_path).verify(report.report_id)


def test_verifier_reads_export_files_with_explicit_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report()
    result = AssetPerformanceReportStore(tmp_path).publish(report)

    def forbidden_read_text(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Path.read_text() must not be used by the verifier")

    def forbidden_read_bytes(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("Path.read_bytes() must not be used by the verifier")

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)
    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    verification = AssetPerformanceReportVerifier(tmp_path).verify(result.report_id)
    assert verification.verified is True


def test_verifier_rejects_oversized_report_before_json_decoding(tmp_path: Path) -> None:
    report = _report()
    result = AssetPerformanceReportStore(tmp_path).publish(report)
    report_path = tmp_path / "market" / result.relative_path / "report.json"
    report_path.write_bytes(
        b" " * (artifacts_module._MAX_REPORT_ENVELOPE_BYTES + 1)  # noqa: SLF001
    )

    with pytest.raises(BacktestResultCorruptError):
        AssetPerformanceReportVerifier(tmp_path).verify(report.report_id)
