"""Atomic artifact publication and independent verification tests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.artifacts import BacktestArtifactStore, build_run_id
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
from app.backtesting.errors import BacktestResultCorruptError, SnapshotChangedError
from app.backtesting.ledger import BacktestLedger
from app.backtesting.portfolio import initialize_portfolio, mark_to_market
from app.backtesting.serialization import read_json_envelope, write_json_envelope
from app.backtesting.verifier import BacktestResultVerifier
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
    ledger.record_mark(
        state,
        event_time=snapshot.data_range.end - timedelta(milliseconds=1),
        candle_index=0,
    )
    ledger.record_mark(
        state,
        event_time=snapshot.data_range.end - timedelta(milliseconds=1),
        candle_index=0,
        final=True,
    )
    point = EquityPoint(
        candle_index=0,
        event_time=snapshot.data_range.end - timedelta(milliseconds=1),
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


def _verifier(tmp_path: Path, snapshot: DatasetSnapshot) -> BacktestResultVerifier:
    return BacktestResultVerifier(
        tmp_path,
        snapshot_factory=lambda _path: _SnapshotReader(snapshot),
    )


def test_publish_verify_and_idempotent_reuse(tmp_path: Path) -> None:
    snapshot = _snapshot()
    config = _config(snapshot)
    execution = _execution(snapshot)
    store = BacktestArtifactStore(
        tmp_path,
        clock=lambda: utc(2030, 1, 1),
        snapshot_factory=lambda _path: _SnapshotReader(snapshot),
    )

    first = store.publish(config, execution)
    run_root = store.root / first.run_id.value
    before = {path.name: path.stat().st_mtime_ns for path in run_root.iterdir()}
    verification = _verifier(tmp_path, snapshot).verify(first.run_id.value)
    second = store.publish(config, execution)
    after = {path.name: path.stat().st_mtime_ns for path in run_root.iterdir()}

    assert first == second
    assert verification.logical_result_checksum == first.logical_result_checksum
    assert verification.artifact_count == 7
    assert before == after


def test_run_id_ignores_operational_clock_and_parameter_order() -> None:
    snapshot = _snapshot()
    first = _config(snapshot)
    second = replace(
        first,
        strategy=StrategyDescriptor("no-op", "1", (("z", Decimal("2")), ("a", Decimal("1")))),
    )
    third = replace(
        first,
        strategy=StrategyDescriptor("no-op", "1", (("a", Decimal("1")), ("z", Decimal("2")))),
    )

    assert build_run_id(second, snapshot) == build_run_id(third, snapshot)


def test_verifier_rejects_tampered_artifact(tmp_path: Path) -> None:
    snapshot = _snapshot()
    config = _config(snapshot)
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=lambda _path: _SnapshotReader(snapshot),
    ).publish(config, _execution(snapshot))
    target = tmp_path / "market" / "backtests" / result.run_id.value / "ledger.jsonl"
    target.write_text(target.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    with pytest.raises(BacktestResultCorruptError):
        _verifier(tmp_path, snapshot).verify(result.run_id.value)


def test_failed_publication_leaves_no_complete_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.backtesting.artifacts as artifacts_module

    snapshot = _snapshot()
    config = _config(snapshot)
    run_id = build_run_id(config, snapshot)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated")

    monkeypatch.setattr(artifacts_module, "_write_equity", fail)
    with pytest.raises(OSError):
        BacktestArtifactStore(
            tmp_path,
            snapshot_factory=lambda _path: _SnapshotReader(snapshot),
        ).publish(config, _execution(snapshot))

    root = tmp_path / "market" / "backtests"
    assert not (root / run_id.value).exists()
    assert not tuple(root.glob(f".{run_id.value}.tmp-*"))


def test_publication_rechecks_snapshot_immediately_before_rename(tmp_path: Path) -> None:
    snapshot = _snapshot()
    calls = 0

    @dataclass
    class ChangingReader:
        def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
            assert snapshot_id == snapshot.snapshot_id
            return snapshot

        def verify_unchanged(self) -> DatasetSnapshot:
            nonlocal calls
            calls += 1
            if calls == 2:
                return replace(snapshot, checksum="d" * 64)
            return snapshot

    store = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=lambda _path: ChangingReader(),
    )
    run_id = build_run_id(_config(snapshot), snapshot)

    with pytest.raises(SnapshotChangedError):
        store.publish(_config(snapshot), _execution(snapshot))

    assert not (store.root / run_id.value).exists()


def test_verifier_rejects_manifest_that_diverges_from_config(tmp_path: Path) -> None:
    snapshot = _snapshot()
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=lambda _path: _SnapshotReader(snapshot),
    ).publish(_config(snapshot), _execution(snapshot))
    manifest_path = tmp_path / "market" / "backtests" / result.run_id.value / "manifest.json"
    manifest = read_json_envelope(manifest_path, "manifest")
    manifest["strategy"] = {"name": "forged", "version": "1", "parameters": []}
    write_json_envelope(manifest_path, "manifest", manifest)

    with pytest.raises(BacktestResultCorruptError):
        _verifier(tmp_path, snapshot).verify(result.run_id.value)
