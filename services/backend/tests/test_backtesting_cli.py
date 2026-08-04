"""Network-free Phase 3A CLI tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

import httpx
import pytest

import app.backtesting.commands as commands_module
import app.cli as cli_module
from app.backtesting.commands import prepare_backtest, run_backtest_command
from app.backtesting.domain import EquityPoint, PositionSizedExecutionAssumptions
from app.backtesting.engine import BacktestExecutionResult
from app.backtesting.ledger import BacktestLedger
from app.backtesting.portfolio import initialize_portfolio, mark_to_market
from app.cli import EXIT_DOMAIN_FAILURE, EXIT_OK, build_parser, main
from app.core.config import MarketDataSettings
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


def _settings(tmp_path: Path) -> MarketDataSettings:
    return MarketDataSettings(
        data_dir=tmp_path,
        market_http_retries=0,
        backtest_default_maker_fee_bps=Decimal("1"),
        backtest_default_taker_fee_bps=Decimal("2"),
        backtest_default_slippage_bps=Decimal("3"),
    )


def _snapshot() -> DatasetSnapshot:
    start = utc(2026, 1, 1)
    return DatasetSnapshot(
        snapshot_id="a" * 64,
        dataset_key="derived:binance:spot:BTC/USDT:1h",
        dataset_version="b" * 64,
        checksum="c" * 64,
        data_range=DataRange(start, start + timedelta(hours=2)),
        partitions=("partitions/year=2026/month=01/candles.parquet",),
        manifest_path="dataset-manifest.json",
        created_at=start.isoformat(),
    )


def _arguments(*extra: str) -> list[str]:
    return [
        "backtest",
        "plan",
        "--snapshot-id",
        "a" * 64,
        "--strategy",
        "no-op",
        "--initial-capital",
        "1000",
        *extra,
    ]


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


def test_main_routes_backtest_before_constructing_http_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError(request.url)

    def fake_run(
        args: object,
        *,
        settings: MarketDataSettings,
        stdout: StringIO,
    ) -> int:
        del args, settings
        stdout.write('{"local": true}\n')
        return EXIT_OK

    monkeypatch.setattr(cli_module, "run_backtest_command", fake_run)
    output = StringIO()
    code = main(
        ["backtest", "inspect", "--run-id", "a" * 64],
        app_settings=_settings(tmp_path),
        transport=httpx.MockTransport(handler),
        stdout=output,
    )

    assert code == EXIT_OK
    assert json.loads(output.getvalue()) == {"local": True}
    assert calls == 0


def test_plan_is_canonical_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(
        commands_module,
        "MarketDatasetReader",
        lambda _path: _SnapshotReader(snapshot),
    )
    args = build_parser().parse_args(_arguments())
    output = StringIO()

    code = run_backtest_command(args, settings=_settings(tmp_path), stdout=output)
    payload = json.loads(output.getvalue())

    assert code == EXIT_OK
    assert payload["action"] == "PLAN"
    assert payload["uses_network"] is False
    assert payload["writes_artifacts"] is False
    assert payload["execution"]["fees"] == {"maker_fee_bps": "1", "taker_fee_bps": "2"}
    assert len(payload["run_id"]) == 64
    assert not (tmp_path / "market" / "backtests").exists()


def test_prepare_uses_snapshot_range_and_explicit_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(
        commands_module,
        "MarketDatasetReader",
        lambda _path: _SnapshotReader(snapshot),
    )
    args = build_parser().parse_args(_arguments())

    prepared = prepare_backtest(args, settings=_settings(tmp_path))

    assert prepared.config.data_range == snapshot.data_range
    assert prepared.strategy.descriptor.name == "no-op"
    assert prepared.config.execution.slippage.fixed_bps == Decimal("3")


def test_run_requires_confirmation_before_engine_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(
        commands_module,
        "MarketDatasetReader",
        lambda _path: _SnapshotReader(snapshot),
    )
    errors = StringIO()
    code = main(
        [*_arguments()[0:1], "run", *_arguments()[2:]],
        app_settings=_settings(tmp_path),
        stdout=StringIO(),
        stderr=errors,
    )

    assert code == EXIT_DOMAIN_FAILURE
    assert "--yes" in errors.getvalue()


def test_dry_run_executes_but_does_not_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    execution = _execution(snapshot)
    monkeypatch.setattr(
        commands_module,
        "MarketDatasetReader",
        lambda _path: _SnapshotReader(snapshot),
    )

    class _Engine:
        @classmethod
        def from_data_dir(cls, _path: Path) -> _Engine:
            return cls()

        def run(self, config: object, strategy: object) -> BacktestExecutionResult:
            del config, strategy
            return execution

    monkeypatch.setattr(commands_module, "DeterministicBacktestEngine", _Engine)
    arguments = [*_arguments()[0:1], "run", *_arguments()[2:], "--dry-run"]
    output = StringIO()

    code = main(arguments, app_settings=_settings(tmp_path), stdout=output)
    payload = json.loads(output.getvalue())

    assert code == EXIT_OK
    assert payload["action"] == "DRY_RUN"
    assert payload["published"] is False
    assert not (tmp_path / "market" / "backtests").exists()


def test_parser_rejects_arbitrary_strategy_module() -> None:
    arguments = _arguments()
    strategy_index = arguments.index("no-op")
    arguments[strategy_index] = "some.module:Strategy"

    with pytest.raises(SystemExit) as captured:
        build_parser().parse_args(arguments)
    assert captured.value.code == 2


def test_compare_parser_accepts_explicit_metric_and_direction() -> None:
    args = build_parser().parse_args(
        [
            "backtest",
            "compare",
            "--run-id",
            "a" * 64,
            "--run-id",
            "b" * 64,
            "--sort-by",
            "sharpe_ratio",
            "--ascending",
        ]
    )

    assert args.backtest_command == "compare"
    assert args.run_id == ["a" * 64, "b" * 64]
    assert args.sort_by == "sharpe_ratio"
    assert args.ascending is True


def test_compare_command_emits_bounded_local_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Reader:
        def __init__(self, data_dir: Path, **kwargs: object) -> None:
            captured["data_dir"] = data_dir
            captured["kwargs"] = kwargs

        def compare(
            self,
            run_ids: list[str],
            *,
            sort_by: object,
            descending: bool,
        ) -> dict[str, object]:
            captured["run_ids"] = run_ids
            captured["sort_by"] = getattr(sort_by, "value")
            captured["descending"] = descending
            return {
                "contract_version": 1,
                "run_count": 2,
                "entries": [],
            }

    monkeypatch.setattr(commands_module, "BacktestRunReader", _Reader)
    args = build_parser().parse_args(
        [
            "backtest",
            "compare",
            "--run-id",
            "a" * 64,
            "--run-id",
            "b" * 64,
            "--sort-by",
            "sharpe_ratio",
        ]
    )
    output = StringIO()

    code = run_backtest_command(args, settings=_settings(tmp_path), stdout=output)

    assert code == EXIT_OK
    assert json.loads(output.getvalue())["contract_version"] == 1
    assert captured["run_ids"] == ["a" * 64, "b" * 64]
    assert captured["sort_by"] == "sharpe_ratio"
    assert captured["descending"] is True


def test_compare_export_requires_explicit_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Reader:
        def __init__(self, data_dir: Path, **kwargs: object) -> None:
            del data_dir, kwargs

        def compare(self, *args: object, **kwargs: object) -> dict[str, object]:
            del args, kwargs
            return {"contract_version": 1}

    monkeypatch.setattr(commands_module, "BacktestRunReader", _Reader)
    errors = StringIO()

    code = main(
        [
            "backtest",
            "compare-export",
            "--run-id",
            "a" * 64,
            "--run-id",
            "b" * 64,
        ],
        app_settings=_settings(tmp_path),
        stdout=StringIO(),
        stderr=errors,
    )

    assert code == EXIT_DOMAIN_FAILURE
    assert "--yes" in errors.getvalue()


def test_compare_export_publishes_verified_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    comparison = {"contract_version": 1, "run_count": 2}

    class _Reader:
        def __init__(self, data_dir: Path, **kwargs: object) -> None:
            del data_dir, kwargs

        def compare(self, *args: object, **kwargs: object) -> dict[str, object]:
            del args, kwargs
            return comparison

    class _Store:
        def __init__(self, data_dir: Path, **kwargs: object) -> None:
            captured["data_dir"] = data_dir
            captured["kwargs"] = kwargs

        def publish(self, report: object) -> dict[str, object]:
            captured["report"] = report
            return {
                "report_id": "c" * 64,
                "relative_path": f"backtest-reports/{'c' * 64}",
                "reused": False,
                "run_count": 2,
                "sort_by": "total_return",
                "descending": True,
            }

    monkeypatch.setattr(commands_module, "BacktestRunReader", _Reader)
    monkeypatch.setattr(commands_module, "ComparisonReportExportStore", _Store)
    output = StringIO()

    code = run_backtest_command(
        build_parser().parse_args(
            [
                "backtest",
                "compare-export",
                "--run-id",
                "a" * 64,
                "--run-id",
                "b" * 64,
                "--yes",
            ]
        ),
        settings=_settings(tmp_path),
        stdout=output,
    )

    assert code == EXIT_OK
    assert json.loads(output.getvalue())["report_id"] == "c" * 64
    assert captured["report"] is comparison


def test_compare_verify_emits_local_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Verifier:
        def __init__(self, data_dir: Path) -> None:
            captured["data_dir"] = data_dir

        def verify(self, report_id: str) -> dict[str, object]:
            captured["report_id"] = report_id
            return {"report_id": report_id, "run_count": 2}

    monkeypatch.setattr(commands_module, "ComparisonReportExportVerifier", _Verifier)
    output = StringIO()

    code = run_backtest_command(
        build_parser().parse_args(["backtest", "compare-verify", "--report-id", "d" * 64]),
        settings=_settings(tmp_path),
        stdout=output,
    )

    assert code == EXIT_OK
    assert json.loads(output.getvalue())["run_count"] == 2
    assert captured["report_id"] == "d" * 64


def test_plan_accepts_fixed_notional_position_sizing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(
        commands_module,
        "MarketDatasetReader",
        lambda _path: _SnapshotReader(snapshot),
    )
    args = build_parser().parse_args(
        _arguments()
        + [
            "--position-sizing",
            "fixed_notional",
            "--position-sizing-value",
            "250",
        ]
    )

    prepared = prepare_backtest(args, settings=_settings(tmp_path))

    assert isinstance(
        prepared.config.execution,
        PositionSizedExecutionAssumptions,
    )
    assert prepared.config.execution.position_sizing.kind.value == "fixed_notional"
    assert prepared.config.execution.position_sizing.value == Decimal("250")
