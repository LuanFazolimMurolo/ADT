"""Atomic artifact publication and independent verification tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import app.backtesting.serialization as serialization_module
from app.backtesting.artifacts import (
    BacktestArtifactStore,
    build_logical_result_checksum,
    build_run_id,
    build_run_id_from_values,
)
from app.backtesting.domain import (
    BacktestConfig,
    BacktestManifest,
    BacktestRunId,
    BacktestStatus,
    EquityPoint,
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    RiskLimits,
    SlippageModel,
    StrategyDescriptor,
)
from app.backtesting.engine import BacktestExecutionResult, DeterministicBacktestEngine
from app.backtesting.errors import BacktestResultCorruptError, SnapshotChangedError
from app.backtesting.ledger import BacktestLedger
from app.backtesting.metrics import derive_closed_trades, metrics_for_schema
from app.backtesting.portfolio import initialize_portfolio, mark_to_market
from app.backtesting.query import BacktestRunReader
from app.backtesting.serialization import (
    canonical_value,
    file_checksum,
    read_json_envelope,
    write_json_envelope,
)
from app.backtesting.strategy import NoOpStrategy
from app.backtesting.verifier import BacktestResultVerifier, _verify_evaluation_boundaries
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import DataRange
from tests.market_data_helpers import utc
from tests.test_backtesting_engine import (
    FakeSnapshotReader,
    RecordingStrategy,
    _candles,
    _evaluation_config,
    _market_buy,
)
from tests.test_backtesting_engine import (
    _config as engine_config,
)
from tests.test_backtesting_engine import (
    _snapshot as engine_snapshot,
)


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


def _manifest_contract(
    snapshot: DatasetSnapshot,
    config: BacktestConfig,
    *,
    context_range: DataRange | None = None,
    evaluation_range: DataRange | None = None,
    lifecycle_version: int | None = None,
) -> BacktestManifest:
    scored_range = evaluation_range or config.data_range
    return BacktestManifest(
        run_id=BacktestRunId("f" * 64),
        engine_version=config.engine_version,
        schema_version=config.schema_version,
        status=BacktestStatus.COMPLETE,
        snapshot_id=snapshot.snapshot_id,
        dataset_key=snapshot.dataset_key,
        dataset_version=snapshot.dataset_version,
        dataset_checksum=snapshot.checksum,
        snapshot_data_range=snapshot.data_range,
        data_range=scored_range,
        strategy=config.strategy,
        strategy_parameters_checksum="e" * 64,
        initial_capital=config.initial_capital,
        execution=config.execution,
        risk_limits=config.risk_limits,
        candle_count=0,
        order_count=0,
        fill_count=0,
        trade_count=0,
        artifacts=(),
        logical_result_checksum="d" * 64,
        created_at=utc(2026, 1, 1),
        completed_at=utc(2026, 1, 1),
        context_range=context_range,
        evaluation_range=evaluation_range,
        strategy_lifecycle_version=lifecycle_version,
    )


@pytest.mark.parametrize(
    "field",
    ["snapshot_data_range", "data_range", "context_range", "evaluation_range"],
)
def test_backtest_manifest_rejects_hostile_range_types_before_bound_access(
    field: str,
) -> None:
    snapshot = _snapshot()
    config = _config(snapshot)
    ranges: dict[str, object] = {
        "snapshot_data_range": snapshot.data_range,
        "data_range": snapshot.data_range,
        "context_range": snapshot.data_range,
        "evaluation_range": snapshot.data_range,
    }
    ranges[field] = object()

    with pytest.raises(ValueError, match="range"):
        BacktestManifest(
            run_id=BacktestRunId("f" * 64),
            engine_version=config.engine_version,
            schema_version=config.schema_version,
            status=BacktestStatus.COMPLETE,
            snapshot_id=snapshot.snapshot_id,
            dataset_key=snapshot.dataset_key,
            dataset_version=snapshot.dataset_version,
            dataset_checksum=snapshot.checksum,
            snapshot_data_range=ranges["snapshot_data_range"],  # type: ignore[arg-type]
            data_range=ranges["data_range"],  # type: ignore[arg-type]
            strategy=config.strategy,
            strategy_parameters_checksum="e" * 64,
            initial_capital=config.initial_capital,
            execution=config.execution,
            risk_limits=config.risk_limits,
            candle_count=0,
            order_count=0,
            fill_count=0,
            trade_count=0,
            artifacts=(),
            logical_result_checksum="d" * 64,
            created_at=utc(2026, 1, 1),
            completed_at=utc(2026, 1, 1),
            context_range=ranges["context_range"],  # type: ignore[arg-type]
            evaluation_range=ranges["evaluation_range"],  # type: ignore[arg-type]
        )


def test_backtest_manifest_rejects_lifecycle_one_with_warmup() -> None:
    snapshot = _snapshot()
    config = _config(snapshot)
    evaluation_range = DataRange(
        snapshot.data_range.start + timedelta(minutes=30),
        snapshot.data_range.end,
    )

    with pytest.raises(ValueError, match="lifecycle version 1"):
        _manifest_contract(
            snapshot,
            config,
            context_range=snapshot.data_range,
            evaluation_range=evaluation_range,
            lifecycle_version=1,
        )


def test_backtest_manifest_accepts_explicit_lifecycle_one_without_warmup() -> None:
    snapshot = _snapshot()
    config = _config(snapshot)

    manifest = _manifest_contract(
        snapshot,
        config,
        context_range=snapshot.data_range,
        evaluation_range=snapshot.data_range,
        lifecycle_version=1,
    )

    assert manifest.context_range == manifest.evaluation_range == snapshot.data_range
    assert manifest.strategy_lifecycle_version == 1


def test_backtest_manifest_accepts_lifecycle_two_with_warmup() -> None:
    snapshot = _snapshot()
    config = _config(snapshot)
    evaluation_range = DataRange(
        snapshot.data_range.start + timedelta(minutes=30),
        snapshot.data_range.end,
    )

    manifest = _manifest_contract(
        snapshot,
        config,
        context_range=snapshot.data_range,
        evaluation_range=evaluation_range,
        lifecycle_version=2,
    )

    assert manifest.context_range == snapshot.data_range
    assert manifest.evaluation_range == evaluation_range
    assert manifest.strategy_lifecycle_version == 2


def test_legacy_manifest_normalizes_to_reserializable_lifecycle_one(tmp_path: Path) -> None:
    snapshot = _snapshot()
    config = _config(snapshot)

    manifest = _manifest_contract(snapshot, config)
    reconstructed = replace(manifest)
    path = tmp_path / "manifest.json"
    write_json_envelope(path, "manifest", manifest)
    document = read_json_envelope(path, "manifest")

    assert reconstructed == manifest
    assert manifest.context_range == manifest.evaluation_range == snapshot.data_range
    assert manifest.strategy_lifecycle_version == 1
    assert document == canonical_value(manifest)
    assert document["strategy_lifecycle_version"] == 1


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


def _financial_artifact_run(
    tmp_path: Path,
) -> tuple[DatasetSnapshot, Path, BacktestResultVerifier]:
    rows = _candles("100", "105", "110")
    snapshot = engine_snapshot(len(rows))
    strategy = RecordingStrategy(start_intents=(_market_buy(),))
    config = engine_config(snapshot, strategy.descriptor, force_close=True)
    snapshot_factory = lambda _path: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).publish(config, execution)
    root = tmp_path / "market" / "backtests" / result.run_id.value
    verifier = BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory)
    return snapshot, root, verifier


def _refresh_artifact_checksum(root: Path, artifact_name: str) -> None:
    artifact_path = root / artifact_name
    manifest_path = root / "manifest.json"
    manifest = read_json_envelope(manifest_path, "manifest")
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        if artifact["relative_path"] == artifact_name:
            artifact["checksum"] = file_checksum(artifact_path)
            artifact["size_bytes"] = artifact_path.stat().st_size
    write_json_envelope(manifest_path, "manifest", manifest)


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


def test_legacy_phase3a_run_id_golden_remains_unchanged() -> None:
    snapshot = _snapshot()

    assert build_run_id(_config(snapshot), snapshot).value == (
        "6d00f5f9a98801a8e4bc237db7c8ab71a8663a3a9b57e76881323c96ca9c83f6"
    )


def test_same_strategy_identity_with_different_lifecycle_has_different_run_id() -> None:
    snapshot = engine_snapshot(2)
    descriptor = StrategyDescriptor("same-custom-plugin", "1")
    lifecycle_two = _evaluation_config(snapshot, descriptor, evaluation_start=0)
    lifecycle_one = replace(lifecycle_two, strategy_lifecycle_version=1)

    assert lifecycle_one.strategy == lifecycle_two.strategy
    assert lifecycle_one.evaluation_range == lifecycle_two.evaluation_range
    assert build_run_id(lifecycle_one, snapshot) != build_run_id(lifecycle_two, snapshot)


def test_run_id_rejects_lifecycle_one_warmup_mutation() -> None:
    snapshot = engine_snapshot(3)
    strategy = NoOpStrategy()
    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=1,
        lifecycle_version=2,
    )
    object.__setattr__(config, "strategy_lifecycle_version", 1)

    with pytest.raises(ValueError, match="lifecycle version 1"):
        build_run_id(config, snapshot)


def test_publish_rejects_lifecycle_one_warmup_before_any_write(tmp_path: Path) -> None:
    snapshot = engine_snapshot(3)
    rows = _candles("100", "101", "102")
    strategy = NoOpStrategy()
    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=1,
        lifecycle_version=2,
    )
    snapshot_factory = lambda _path: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    store = BacktestArtifactStore(tmp_path, snapshot_factory=snapshot_factory)
    before = tuple(tmp_path.rglob("*"))
    object.__setattr__(config, "strategy_lifecycle_version", 1)

    with pytest.raises(ValueError, match="lifecycle version 1"):
        store.publish(config, execution)

    assert tuple(tmp_path.rglob("*")) == before == ()


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


def test_evaluation_manifest_and_read_api_expose_context_and_evaluation_ranges(
    tmp_path: Path,
) -> None:
    snapshot = engine_snapshot(5)
    rows = _candles("100", "99", "98", "101", "102")
    strategy = NoOpStrategy()
    config = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=2)
    snapshot_factory = lambda _path: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).publish(config, execution)
    root = tmp_path / "market" / "backtests" / result.run_id.value
    manifest = read_json_envelope(root / "manifest.json", "manifest")

    assert manifest["context_range"] == {
        "start": config.data_range.start.isoformat(),
        "end": config.data_range.end.isoformat(),
    }
    assert manifest["evaluation_range"] == {
        "start": config.evaluation_range.start.isoformat(),
        "end": config.evaluation_range.end.isoformat(),
    }
    assert manifest["data_range"] == manifest["evaluation_range"]
    assert manifest["strategy_lifecycle_version"] == 2
    summary = BacktestRunReader(tmp_path, snapshot_factory=snapshot_factory).inspect(
        result.run_id.value
    )
    assert summary["context_range"] == manifest["context_range"]
    assert summary["evaluation_range"] == manifest["evaluation_range"]
    assert summary["data_range"] == manifest["evaluation_range"]
    assert _verifier(tmp_path, snapshot).verify(result.run_id.value).strategy_lifecycle_version == 2


def test_verifier_accepts_evaluation_lifecycle_one_without_warmup(tmp_path: Path) -> None:
    snapshot = engine_snapshot(3)
    rows = _candles("100", "101", "102")
    strategy = NoOpStrategy()
    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=0,
        lifecycle_version=1,
    )
    snapshot_factory = lambda _path: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).publish(config, execution)

    verification = BacktestResultVerifier(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).verify(result.run_id.value)

    assert verification.strategy_lifecycle_version == 1


def test_legacy_manifest_without_explicit_ranges_remains_verifiable(tmp_path: Path) -> None:
    snapshot = _snapshot()
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=lambda _path: _SnapshotReader(snapshot),
    ).publish(_config(snapshot), _execution(snapshot))
    path = tmp_path / "market" / "backtests" / result.run_id.value / "manifest.json"
    manifest = read_json_envelope(path, "manifest")
    manifest.pop("context_range")
    manifest.pop("evaluation_range")
    manifest.pop("strategy_lifecycle_version")
    write_json_envelope(path, "manifest", manifest)

    verification = _verifier(tmp_path, snapshot).verify(result.run_id.value)

    assert verification.run_id == result.run_id
    assert verification.strategy_lifecycle_version == 1


@pytest.mark.parametrize("target", ["manifest", "config"])
def test_verifier_rejects_tampered_evaluation_lifecycle(
    tmp_path: Path,
    target: str,
) -> None:
    snapshot = engine_snapshot(3)
    rows = _candles("100", "101", "102")
    strategy = NoOpStrategy()
    config = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=1)
    snapshot_factory = lambda _path: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).publish(config, execution)
    root = tmp_path / "market" / "backtests" / result.run_id.value
    path = root / f"{target}.json"
    document = read_json_envelope(path, target)
    document["strategy_lifecycle_version"] = 1
    write_json_envelope(path, target, document)
    if target == "config":
        _refresh_artifact_checksum(root, "config.json")

    with pytest.raises(BacktestResultCorruptError, match="lifecycle"):
        BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory).verify(
            result.run_id.value
        )


@pytest.mark.parametrize("target", ["manifest", "config"])
def test_verifier_rejects_missing_evaluation_lifecycle(
    tmp_path: Path,
    target: str,
) -> None:
    snapshot = engine_snapshot(3)
    rows = _candles("100", "101", "102")
    strategy = NoOpStrategy()
    config = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=1)
    snapshot_factory = lambda _path: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).publish(config, execution)
    root = tmp_path / "market" / "backtests" / result.run_id.value
    path = root / f"{target}.json"
    document = read_json_envelope(path, target)
    document.pop("strategy_lifecycle_version")
    write_json_envelope(path, target, document)
    if target == "config":
        _refresh_artifact_checksum(root, "config.json")

    with pytest.raises(BacktestResultCorruptError, match="lifecycle"):
        BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory).verify(
            result.run_id.value
        )


@pytest.mark.parametrize("declared_lifecycle", [1, 2])
@pytest.mark.parametrize(
    "raw_lifecycle",
    [True, False, 1.0, 2.0, "1", "2", 3, None],
)
def test_verifier_rejects_non_exact_manifest_lifecycle_after_envelope_rehash(
    tmp_path: Path,
    declared_lifecycle: int,
    raw_lifecycle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = engine_snapshot(3)
    rows = _candles("100", "101", "102")
    strategy = NoOpStrategy()
    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=0,
        lifecycle_version=declared_lifecycle,
    )
    snapshot_factory = lambda _path: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).publish(config, execution)
    manifest_path = tmp_path / "market" / "backtests" / result.run_id.value / "manifest.json"
    manifest = read_json_envelope(manifest_path, "manifest")
    manifest["strategy_lifecycle_version"] = raw_lifecycle
    if isinstance(raw_lifecycle, float):
        canonical_value = serialization_module.canonical_value

        def canonical_value_accepting_float(value: object) -> object:
            if isinstance(value, float):
                return value
            return canonical_value(value)

        monkeypatch.setattr(
            serialization_module,
            "canonical_value",
            canonical_value_accepting_float,
        )
    write_json_envelope(manifest_path, "manifest", manifest)

    rehashed_manifest = read_json_envelope(manifest_path, "manifest")
    rehashed_lifecycle = rehashed_manifest["strategy_lifecycle_version"]
    assert type(rehashed_lifecycle) is type(raw_lifecycle)
    assert rehashed_lifecycle == raw_lifecycle
    with pytest.raises(BacktestResultCorruptError, match="lifecycle"):
        BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory).verify(
            result.run_id.value
        )


def test_verifier_rejects_fully_reassigned_lifecycle_one_warmup_artifact(
    tmp_path: Path,
) -> None:
    snapshot = engine_snapshot(3)
    rows = _candles("100", "101", "102")
    strategy = NoOpStrategy()
    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=1,
        lifecycle_version=2,
    )
    snapshot_factory = lambda _path: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).publish(config, execution)
    old_root = tmp_path / "market" / "backtests" / result.run_id.value
    config_path = old_root / "config.json"
    result_path = old_root / "result.json"
    manifest_path = old_root / "manifest.json"
    config_document = read_json_envelope(config_path, "config")
    result_document = read_json_envelope(result_path, "result")
    manifest = read_json_envelope(manifest_path, "manifest")
    config_document["strategy_lifecycle_version"] = 1
    snapshot_value = {
        "snapshot_id": manifest["snapshot_id"],
        "dataset_key": manifest["dataset_key"],
        "dataset_version": manifest["dataset_version"],
        "checksum": manifest["dataset_checksum"],
        "data_range": manifest["snapshot_data_range"],
    }
    reassigned_run_id = build_run_id_from_values(config_document, snapshot_value)
    logical_checksum = build_logical_result_checksum(
        run_id=reassigned_run_id,
        execution=execution,
        trades=result.trades,
        metrics=metrics_for_schema(result.metrics, config.schema_version),
    )
    result_document["run_id"] = reassigned_run_id.value
    result_document["logical_result_checksum"] = logical_checksum
    manifest_run_id = manifest["run_id"]
    assert isinstance(manifest_run_id, dict)
    manifest_run_id["value"] = reassigned_run_id.value
    manifest["strategy_lifecycle_version"] = 1
    manifest["logical_result_checksum"] = logical_checksum
    write_json_envelope(config_path, "config", config_document)
    write_json_envelope(result_path, "result", result_document)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        relative_path = artifact["relative_path"]
        assert isinstance(relative_path, str)
        if relative_path in {"config.json", "result.json"}:
            artifact_path = old_root / relative_path
            artifact["checksum"] = file_checksum(artifact_path)
            artifact["size_bytes"] = artifact_path.stat().st_size
    write_json_envelope(manifest_path, "manifest", manifest)
    new_root = old_root.with_name(reassigned_run_id.value)
    old_root.rename(new_root)

    assert read_json_envelope(new_root / "config.json", "config") == config_document
    assert read_json_envelope(new_root / "result.json", "result") == result_document
    assert read_json_envelope(new_root / "manifest.json", "manifest") == manifest
    with pytest.raises(BacktestResultCorruptError, match="Lifecycle 1"):
        BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory).verify(
            reassigned_run_id.value
        )


def test_evaluation_boundary_changes_run_identity() -> None:
    snapshot = engine_snapshot(5)
    strategy = NoOpStrategy()
    first = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=1)
    second = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=2)

    assert build_run_id(first, snapshot) != build_run_id(second, snapshot)


def test_temporal_contract_rejects_every_event_family_outside_evaluation() -> None:
    rows = _candles("100", "105", "110")
    snapshot = engine_snapshot(len(rows))
    strategy = RecordingStrategy(start_intents=(_market_buy(),))
    config = engine_config(snapshot, strategy.descriptor, force_close=True)
    execution = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        config, strategy
    )
    trades = derive_closed_trades(execution.fills)
    start = config.data_range.start
    end = config.data_range.end
    arguments = {
        "orders": execution.orders,
        "fills": execution.fills,
        "ledger": execution.ledger,
        "trades": trades,
        "equity": execution.equity_curve,
        "candle_count": execution.candles_processed,
        "evaluation_start": start,
        "evaluation_end": end,
    }
    _verify_evaluation_boundaries(**arguments)

    corruptions = (
        {"orders": (replace(execution.orders[0], created_at=end + timedelta(hours=1)),)},
        {"fills": (replace(execution.fills[0], event_time=end + timedelta(hours=1)),)},
        {"ledger": (replace(execution.ledger[0], event_time=start + timedelta(seconds=1)),)},
        {"equity": (replace(execution.equity_curve[0], event_time=end + timedelta(hours=1)),)},
        {"trades": (replace(trades[0], exit_time=end + timedelta(hours=1)),)},
    )
    for corruption in corruptions:
        with pytest.raises(BacktestResultCorruptError):
            _verify_evaluation_boundaries(**(arguments | corruption))


def test_semantic_equity_tamper_is_rejected_after_checksums_are_recalculated(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=lambda _path: _SnapshotReader(snapshot),
    ).publish(_config(snapshot), _execution(snapshot))
    root = tmp_path / "market" / "backtests" / result.run_id.value
    equity_path = root / "equity.parquet"
    table = pq.read_table(equity_path)
    rows = table.to_pylist()
    rows[0]["event_time"] = (snapshot.data_range.end + timedelta(hours=1)).isoformat()
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), equity_path)
    _refresh_artifact_checksum(root, "equity.parquet")

    with pytest.raises(BacktestResultCorruptError):
        _verifier(tmp_path, snapshot).verify(result.run_id.value)


@pytest.mark.parametrize(
    ("artifact_name", "row_index", "field"),
    [
        ("orders.jsonl", 0, "created_at"),
        ("fills.jsonl", 0, "event_time"),
        ("ledger.jsonl", 1, "event_time"),
        ("trades.jsonl", 0, "exit_time"),
    ],
)
def test_semantic_jsonl_temporal_tamper_survives_recalculated_file_checksums(
    tmp_path: Path,
    artifact_name: str,
    row_index: int,
    field: str,
) -> None:
    snapshot, root, verifier = _financial_artifact_run(tmp_path)
    path = root / artifact_name
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    rows[row_index][field] = (snapshot.data_range.end + timedelta(hours=1)).isoformat()
    path.write_text(
        "".join(
            f"{json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=True)}\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    _refresh_artifact_checksum(root, artifact_name)

    with pytest.raises(BacktestResultCorruptError):
        verifier.verify(root.name)
