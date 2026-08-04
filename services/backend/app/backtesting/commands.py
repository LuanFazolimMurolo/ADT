"""Network-free CLI orchestration for deterministic local backtests."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO, cast

from app.backtesting.artifacts import (
    BacktestArtifactStore,
    build_backtest_result,
    build_run_id,
)
from app.backtesting.asset_performance import (
    build_asset_performance_report_from_summaries,
    normalize_asset_performance_run_ids,
)
from app.backtesting.asset_performance_artifacts import (
    AssetPerformanceReportStore,
    AssetPerformanceReportVerifier,
)
from app.backtesting.comparison_batch import load_comparison_batch_request
from app.backtesting.domain import (
    BacktestConfig,
    BacktestRunId,
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    PositionSizedExecutionAssumptions,
    PositionSizingKind,
    PositionSizingPolicy,
    RiskLimits,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
)
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.errors import SnapshotChangedError, SnapshotInvalidError
from app.backtesting.exports import (
    ComparisonReportExportStore,
    ComparisonReportExportVerifier,
)
from app.backtesting.query import BacktestRunReader
from app.backtesting.registry import StrategyRegistry
from app.backtesting.reports import ComparisonMetric
from app.backtesting.serialization import canonical_value
from app.backtesting.strategy import BacktestStrategy
from app.core.config import MarketDataSettings
from app.domain.errors import InvalidDomainInputError
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import DataRange
from app.market_data.snapshots import MarketDatasetReader

EXIT_OK = 0


@dataclass(frozen=True, slots=True)
class PreparedBacktest:
    """Validated logical plan shared by plan, dry-run and persisted execution."""

    run_id: BacktestRunId
    config: BacktestConfig
    strategy: BacktestStrategy
    snapshot: DatasetSnapshot


def configure_backtest_parser(parser: argparse.ArgumentParser) -> None:
    """Add the stable Phase 3A and 3B command surface to one root parser."""
    commands = parser.add_subparsers(dest="backtest_command", required=True)
    registry = StrategyRegistry()
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("--snapshot-id", required=True)
        command.add_argument("--strategy", choices=registry.names, required=True)
        command.add_argument("--quantity", type=_positive_decimal)
        command.add_argument(
            "--position-sizing",
            choices=tuple(kind.value for kind in PositionSizingKind),
            default=PositionSizingKind.EXPLICIT_QUANTITY.value,
        )
        command.add_argument("--position-sizing-value", type=_positive_decimal)
        command.add_argument("--initial-capital", type=_positive_decimal, required=True)
        command.add_argument("--start", type=_utc_datetime)
        command.add_argument("--end", type=_utc_datetime)
        command.add_argument("--maker-fee-bps", type=_nonnegative_decimal)
        command.add_argument("--taker-fee-bps", type=_nonnegative_decimal)
        command.add_argument("--slippage-bps", type=_nonnegative_decimal)
        command.add_argument(
            "--minimum-quantity",
            type=_positive_decimal,
            default=Decimal("0.00000001"),
        )
        command.add_argument(
            "--quantity-step",
            type=_positive_decimal,
            default=Decimal("0.00000001"),
        )
        command.add_argument("--price-tick", type=_positive_decimal, default=Decimal("0.00000001"))
        command.add_argument("--minimum-notional", type=_nonnegative_decimal, default=Decimal("0"))
        command.add_argument("--maximum-notional", type=_positive_decimal)
        command.add_argument("--max-order-notional", type=_positive_decimal)
        command.add_argument("--max-position-notional", type=_positive_decimal)
        command.add_argument("--max-drawdown-pct", type=_percentage)
        command.add_argument(
            "--stop-loss",
            choices=tuple(kind.value for kind in StopLossKind),
            default=StopLossKind.DISABLED.value,
        )
        command.add_argument("--stop-loss-value", type=_exclusive_percentage)
        command.add_argument(
            "--minimum-quote-reserve",
            type=_nonnegative_decimal,
            default=Decimal("0"),
        )
        command.add_argument("--allow-all-in", action="store_true")
        command.add_argument("--force-close-at-end", action="store_true")
        if name == "run":
            command.add_argument("--yes", action="store_true")
            command.add_argument("--dry-run", action="store_true")

    for name in ("inspect", "verify", "orders", "trades"):
        command = commands.add_parser(name)
        command.add_argument("--run-id", required=True)
        if name in {"orders", "trades"}:
            command.add_argument("--offset", type=_nonnegative_int, default=0)
            command.add_argument("--limit", type=_bounded_page_size, default=20)

    for name in ("compare", "compare-export"):
        compare = commands.add_parser(name)
        compare.add_argument("--run-id", action="append", required=True)
        compare.add_argument(
            "--sort-by",
            choices=tuple(metric.value for metric in ComparisonMetric),
            default=ComparisonMetric.TOTAL_RETURN.value,
        )
        compare.add_argument("--ascending", action="store_true")
        if name == "compare-export":
            compare.add_argument("--yes", action="store_true")

    for name in ("asset-performance-generate", "asset-performance-export"):
        asset_performance = commands.add_parser(name)
        asset_performance.add_argument("--run-id", action="append", required=True)
        if name == "asset-performance-export":
            asset_performance.add_argument("--yes", action="store_true")

    for name in ("asset-performance-inspect", "asset-performance-verify"):
        asset_performance = commands.add_parser(name)
        asset_performance.add_argument("--report-id", required=True)

    visualize = commands.add_parser("visualize")
    visualize.add_argument("--run-id", required=True)
    visualize.add_argument("--max-points", type=_bounded_visualization_points, default=500)

    compare_batch = commands.add_parser("compare-batch")
    compare_batch.add_argument("--request-file", type=Path, required=True)

    compare_verify = commands.add_parser("compare-verify")
    compare_verify.add_argument("--report-id", required=True)


def run_backtest_command(
    args: argparse.Namespace,
    *,
    settings: MarketDataSettings,
    stdout: TextIO,
) -> int:
    """Execute one local command without constructing an HTTP client."""
    command = args.backtest_command
    if command in {"asset-performance-inspect", "asset-performance-verify"}:
        verifier = AssetPerformanceReportVerifier(settings.data_dir)
        if command == "asset-performance-inspect":
            _emit(verifier.inspect(args.report_id), stdout)
        else:
            _emit(verifier.verify(args.report_id), stdout)
        return EXIT_OK

    if command == "compare-verify":
        verification = ComparisonReportExportVerifier(settings.data_dir).verify(args.report_id)
        _emit(verification, stdout)
        return EXIT_OK

    if command in {
        "inspect",
        "verify",
        "orders",
        "trades",
        "compare",
        "compare-export",
        "compare-batch",
        "visualize",
        "asset-performance-generate",
        "asset-performance-export",
    }:
        reader = BacktestRunReader(
            settings.data_dir,
            directory=settings.backtest_dir,
            lock_timeout_seconds=settings.market_job_lock_timeout,
            lock_stale_after_seconds=settings.market_job_stale_after,
        )
        if command == "inspect":
            _emit(reader.inspect(args.run_id), stdout)
        elif command == "verify":
            _emit(reader.verify(args.run_id), stdout)
        elif command == "orders":
            _emit(reader.orders(args.run_id, offset=args.offset, limit=args.limit), stdout)
        elif command == "trades":
            _emit(reader.trades(args.run_id, offset=args.offset, limit=args.limit), stdout)
        elif command == "visualize":
            _emit(reader.visualization(args.run_id, max_points=args.max_points), stdout)
        elif command == "compare-batch":
            try:
                request = load_comparison_batch_request(args.request_file)
                batch = reader.compare_batch(request)
            except ValueError as error:
                raise InvalidDomainInputError(str(error)) from error
            _emit(batch, stdout)
        elif command in {"asset-performance-generate", "asset-performance-export"}:
            try:
                run_ids = normalize_asset_performance_run_ids(args.run_id)
                report = build_asset_performance_report_from_summaries(
                    tuple(reader.inspect(run_id) for run_id in run_ids)
                )
            except ValueError as error:
                raise InvalidDomainInputError(str(error)) from error
            if command == "asset-performance-generate":
                _emit(report, stdout)
                return EXIT_OK
            if not args.yes:
                raise InvalidDomainInputError(
                    "A exportação por ativo exige confirmação explícita --yes."
                )
            asset_export = AssetPerformanceReportStore(
                settings.data_dir,
                lock_timeout_seconds=settings.market_job_lock_timeout,
                lock_stale_after_seconds=settings.market_job_stale_after,
            ).publish(report)
            _emit(asset_export, stdout)
        else:
            try:
                comparison = reader.compare(
                    args.run_id,
                    sort_by=ComparisonMetric(args.sort_by),
                    descending=not args.ascending,
                )
            except ValueError as error:
                raise InvalidDomainInputError(str(error)) from error
            if command == "compare":
                _emit(comparison, stdout)
                return EXIT_OK
            if not args.yes:
                raise InvalidDomainInputError(
                    "A exportação comparativa exige confirmação explícita --yes."
                )
            export = ComparisonReportExportStore(
                settings.data_dir,
                lock_timeout_seconds=settings.market_job_lock_timeout,
                lock_stale_after_seconds=settings.market_job_stale_after,
            ).publish(comparison)
            _emit(export, stdout)
        return EXIT_OK

    prepared = prepare_backtest(args, settings=settings)
    if command == "plan":
        _emit(_plan_payload(prepared), stdout)
        return EXIT_OK
    if not args.yes and not args.dry_run:
        raise InvalidDomainInputError("Backtest run exige confirmação explícita --yes.")

    execution = DeterministicBacktestEngine.from_data_dir(settings.data_dir).run(
        prepared.config,
        prepared.strategy,
    )
    if execution.snapshot != prepared.snapshot:
        raise SnapshotChangedError()
    result = build_backtest_result(prepared.config, execution)
    if args.dry_run:
        _emit(_result_payload(result, execution.candles_processed, published=False), stdout)
        return EXIT_OK
    store = BacktestArtifactStore(
        settings.data_dir,
        directory=settings.backtest_dir,
        lock_timeout_seconds=settings.market_job_lock_timeout,
        lock_stale_after_seconds=settings.market_job_stale_after,
    )
    result = store.publish(prepared.config, execution)
    _emit(_result_payload(result, execution.candles_processed, published=True), stdout)
    return EXIT_OK


def prepare_backtest(
    args: argparse.Namespace,
    *,
    settings: MarketDataSettings,
) -> PreparedBacktest:
    """Open and verify a snapshot, then construct one canonical logical config."""
    try:
        reader = MarketDatasetReader(settings.data_dir)
        snapshot = reader.open_snapshot(args.snapshot_id)
        if reader.verify_unchanged() != snapshot:
            raise SnapshotInvalidError()
    except SnapshotInvalidError:
        raise
    except Exception:
        raise SnapshotInvalidError() from None

    if (args.start is None) != (args.end is None):
        raise InvalidDomainInputError("--start e --end devem ser usados juntos.")
    data_range = snapshot.data_range if args.start is None else DataRange(args.start, args.end)
    if data_range.start < snapshot.data_range.start or data_range.end > snapshot.data_range.end:
        raise SnapshotInvalidError("O intervalo excede a cobertura do snapshot.")

    strategy = StrategyRegistry().build(args.strategy, quantity=args.quantity)
    config = BacktestConfig(
        snapshot_id=snapshot.snapshot_id,
        data_range=data_range,
        strategy=strategy.descriptor,
        initial_capital=args.initial_capital,
        execution=_execution_assumptions(args, settings),
        constraints=InstrumentConstraints(
            minimum_quantity=args.minimum_quantity,
            quantity_step=args.quantity_step,
            price_tick=args.price_tick,
            minimum_notional=args.minimum_notional,
            maximum_notional=args.maximum_notional,
        ),
        risk_limits=_risk_limits(args, settings),
        history_window=settings.backtest_history_window,
        max_candles=settings.backtest_max_candles,
        max_orders=settings.backtest_max_orders,
        max_events=settings.backtest_max_events,
        engine_version=settings.backtest_engine_version,
        schema_version=settings.backtest_schema_version,
    )
    return PreparedBacktest(build_run_id(config, snapshot), config, strategy, snapshot)


def _plan_payload(prepared: PreparedBacktest) -> dict[str, object]:
    return {
        "action": "PLAN",
        "run_id": prepared.run_id.value,
        "snapshot_id": prepared.snapshot.snapshot_id,
        "dataset_key": prepared.snapshot.dataset_key,
        "dataset_version": prepared.snapshot.dataset_version,
        "data_range": prepared.config.data_range,
        "strategy": prepared.config.strategy,
        "initial_capital": prepared.config.initial_capital,
        "execution": prepared.config.execution,
        "constraints": prepared.config.constraints,
        "risk_limits": prepared.config.risk_limits,
        "limits": {
            "history_window": prepared.config.history_window,
            "max_candles": prepared.config.max_candles,
            "max_orders": prepared.config.max_orders,
            "max_events": prepared.config.max_events,
        },
        "writes_artifacts": False,
        "uses_network": False,
    }


def _result_payload(result: object, candle_count: int, *, published: bool) -> dict[str, object]:
    value = canonical_value(result)
    if not isinstance(value, dict):
        raise TypeError("backtest result serialization is invalid")
    result_value = cast(dict[str, object], value)
    return {
        "action": "PUBLISHED" if published else "DRY_RUN",
        "published": published,
        "candles": candle_count,
        **result_value,
    }


def _emit(payload: object, stdout: TextIO) -> None:
    print(
        json.dumps(canonical_value(payload), ensure_ascii=False, sort_keys=True),
        file=stdout,
    )


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("use a decimal number") from error
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError("decimal must be finite")
    return parsed


def _execution_assumptions(
    args: argparse.Namespace,
    settings: MarketDataSettings,
) -> ExecutionAssumptions:
    fees = FeeModel(
        args.maker_fee_bps
        if args.maker_fee_bps is not None
        else settings.backtest_default_maker_fee_bps,
        args.taker_fee_bps
        if args.taker_fee_bps is not None
        else settings.backtest_default_taker_fee_bps,
    )
    slippage = SlippageModel(
        fixed_bps=(
            args.slippage_bps
            if args.slippage_bps is not None
            else settings.backtest_default_slippage_bps
        )
    )
    policy = _position_sizing_policy(args)
    if policy == PositionSizingPolicy():
        return ExecutionAssumptions(
            fees=fees,
            slippage=slippage,
            force_close_at_end=args.force_close_at_end,
        )
    return PositionSizedExecutionAssumptions(
        fees=fees,
        slippage=slippage,
        force_close_at_end=args.force_close_at_end,
        position_sizing=policy,
    )


def _position_sizing_policy(args: argparse.Namespace) -> PositionSizingPolicy:
    try:
        kind = PositionSizingKind(args.position_sizing)
        value = args.position_sizing_value
        if kind is PositionSizingKind.EXPLICIT_QUANTITY and value is not None:
            raise ValueError("explicit quantity sizing does not accept a value")
        if kind is not PositionSizingKind.EXPLICIT_QUANTITY and value is None:
            raise ValueError("selected position sizing requires --position-sizing-value")
        return PositionSizingPolicy(kind=kind, value=value)
    except ValueError as error:
        raise InvalidDomainInputError(str(error)) from error


def _risk_limits(
    args: argparse.Namespace,
    settings: MarketDataSettings,
) -> RiskLimits:
    policy = _stop_loss_policy(args)
    if policy == StopLossPolicy():
        return RiskLimits(
            max_order_notional=args.max_order_notional,
            max_position_notional=args.max_position_notional,
            max_open_orders=settings.backtest_max_open_orders,
            max_total_orders=settings.backtest_max_orders,
            max_drawdown_pct=args.max_drawdown_pct,
            allow_all_in=args.allow_all_in,
            minimum_quote_reserve=args.minimum_quote_reserve,
        )
    return StopLossRiskLimits(
        max_order_notional=args.max_order_notional,
        max_position_notional=args.max_position_notional,
        max_open_orders=settings.backtest_max_open_orders,
        max_total_orders=settings.backtest_max_orders,
        max_drawdown_pct=args.max_drawdown_pct,
        allow_all_in=args.allow_all_in,
        minimum_quote_reserve=args.minimum_quote_reserve,
        stop_loss=policy,
    )


def _stop_loss_policy(args: argparse.Namespace) -> StopLossPolicy:
    try:
        kind = StopLossKind(args.stop_loss)
        value = args.stop_loss_value
        if kind is StopLossKind.DISABLED and value is not None:
            raise ValueError("disabled stop loss does not accept a value")
        if kind is not StopLossKind.DISABLED and value is None:
            raise ValueError("selected stop loss requires --stop-loss-value")
        return StopLossPolicy(kind=kind, value=value)
    except ValueError as error:
        raise InvalidDomainInputError(str(error)) from error


def _positive_decimal(value: str) -> Decimal:
    parsed = _decimal(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("decimal must be positive")
    return parsed


def _nonnegative_decimal(value: str) -> Decimal:
    parsed = _decimal(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("decimal must be nonnegative")
    return parsed


def _percentage(value: str) -> Decimal:
    parsed = _nonnegative_decimal(value)
    if parsed > Decimal("100"):
        raise argparse.ArgumentTypeError("percentage must not exceed 100")
    return parsed


def _exclusive_percentage(value: str) -> Decimal:
    parsed = _positive_decimal(value)
    if parsed >= Decimal("100"):
        raise argparse.ArgumentTypeError("percentage must be below 100")
    return parsed


def _utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("use an ISO-8601 UTC datetime") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("datetime must be timezone-aware UTC")
    return parsed.astimezone(UTC)


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("use an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("integer must be nonnegative")
    return parsed


def _bounded_visualization_points(value: str) -> int:
    parsed = _nonnegative_int(value)
    if parsed < 2 or parsed > 2_000:
        raise argparse.ArgumentTypeError("max-points must be between 2 and 2000")
    return parsed


def _bounded_page_size(value: str) -> int:
    parsed = _nonnegative_int(value)
    if parsed < 1 or parsed > 1_000:
        raise argparse.ArgumentTypeError("limit must be between 1 and 1000")
    return parsed
