"""CLI commands for local deterministic paper-trading sessions."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TextIO

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    RiskLimits,
    SlippageModel,
)
from app.backtesting.serialization import canonical_value
from app.core.config import MarketDataSettings
from app.domain.errors import InvalidDomainInputError
from app.market_data.domain import TradingPair
from app.market_data.timeframes import TIMEFRAMES, get_timeframe
from app.paper_trading.domain import (
    PaperSessionConfig,
    PaperSessionState,
    paper_config_payload,
    paper_session_id,
)
from app.paper_trading.service import PaperTradingService
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.domain import StrategyParameterKind, StrategyParameterSpec
from app.strategies.registry import StrategyPluginRegistry


def configure_paper_trading_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="paper_command", required=True)
    create = commands.add_parser("create", help="Create one deterministic local paper session.")
    create.add_argument("--symbol", required=True)
    create.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)
    create.add_argument("--start", type=_utc_datetime, required=True)
    create.add_argument("--warmup-candles", type=_nonnegative_int, default=0)
    create.add_argument("--strategy", required=True)
    create.add_argument("--strategy-version", required=True)
    create.add_argument("--parameters-json", default="{}")
    create.add_argument("--initial-capital", type=_positive_decimal, required=True)
    create.add_argument("--maker-fee-bps", type=_nonnegative_decimal)
    create.add_argument("--taker-fee-bps", type=_nonnegative_decimal)
    create.add_argument("--slippage-bps", type=_nonnegative_decimal)
    create.add_argument("--minimum-quantity", type=_positive_decimal, default=Decimal("0.00000001"))
    create.add_argument("--quantity-step", type=_positive_decimal, default=Decimal("0.00000001"))
    create.add_argument("--price-tick", type=_positive_decimal, default=Decimal("0.00000001"))
    create.add_argument("--minimum-notional", type=_nonnegative_decimal, default=Decimal("0"))
    create.add_argument("--maximum-notional", type=_positive_decimal)
    create.add_argument("--max-order-notional", type=_positive_decimal)
    create.add_argument("--max-position-notional", type=_positive_decimal)
    create.add_argument("--max-drawdown-pct", type=_percentage)
    create.add_argument("--minimum-quote-reserve", type=_nonnegative_decimal, default=Decimal("0"))
    create.add_argument("--allow-all-in", action="store_true")
    create.add_argument("--yes", action="store_true")

    for name in ("run-once", "status", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--session-id", required=True)
        if name == "run-once":
            command.add_argument("--yes", action="store_true")


def run_paper_trading_command(
    args: argparse.Namespace,
    *,
    settings: MarketDataSettings,
    stdout: TextIO,
) -> int:
    service = PaperTradingService(
        settings.data_dir,
        lock_timeout_seconds=settings.market_job_lock_timeout,
        lock_stale_after_seconds=settings.market_job_stale_after,
    )
    if args.paper_command == "create":
        if not args.yes:
            raise InvalidDomainInputError("A criação da sessão exige confirmação --yes.")
        registry = StrategyPluginRegistry.builtins()
        plugin = registry.resolve(args.strategy, args.strategy_version)
        raw = _parameters(plugin.descriptor.parameters, args.parameters_json)
        strategy = registry.build(
            args.strategy,
            args.strategy_version,
            raw,
            available_indicators=builtin_indicator_capabilities(),
        )
        config = PaperSessionConfig(
            pair=TradingPair.parse(args.symbol),
            timeframe=get_timeframe(args.timeframe),
            start_at=args.start,
            warmup_candles=args.warmup_candles,
            strategy=strategy.descriptor,
            strategy_lifecycle_version=plugin.descriptor.lifecycle_version,
            initial_capital=args.initial_capital,
            execution=ExecutionAssumptions(
                fees=FeeModel(
                    args.maker_fee_bps
                    if args.maker_fee_bps is not None
                    else settings.backtest_default_maker_fee_bps,
                    args.taker_fee_bps
                    if args.taker_fee_bps is not None
                    else settings.backtest_default_taker_fee_bps,
                ),
                slippage=SlippageModel(
                    fixed_bps=(
                        args.slippage_bps
                        if args.slippage_bps is not None
                        else settings.backtest_default_slippage_bps
                    )
                ),
                force_close_at_end=False,
            ),
            constraints=InstrumentConstraints(
                minimum_quantity=args.minimum_quantity,
                quantity_step=args.quantity_step,
                price_tick=args.price_tick,
                minimum_notional=args.minimum_notional,
                maximum_notional=args.maximum_notional,
            ),
            risk_limits=RiskLimits(
                max_order_notional=args.max_order_notional,
                max_position_notional=args.max_position_notional,
                max_open_orders=settings.backtest_max_open_orders,
                max_total_orders=settings.backtest_max_orders,
                max_drawdown_pct=args.max_drawdown_pct,
                allow_all_in=args.allow_all_in,
                minimum_quote_reserve=args.minimum_quote_reserve,
            ),
            history_window=settings.backtest_history_window,
            max_candles=settings.paper_trading_max_replay_candles,
            max_orders=settings.backtest_max_orders,
            max_events=settings.backtest_max_events,
            engine_version=settings.backtest_engine_version,
        )
        service.create(config)
        _emit(
            {"session_id": paper_session_id(config), "config": paper_config_payload(config)},
            stdout,
        )
        return 0
    if args.paper_command == "run-once":
        if not args.yes:
            raise InvalidDomainInputError("A execução da sessão exige confirmação --yes.")
        result = service.run_once(args.session_id)
        _emit({"action": result.action.value, "state": _state_summary(result.state)}, stdout)
        return 0
    if args.paper_command == "status":
        state = service.status(args.session_id)
        _emit(None if state is None else _state_summary(state), stdout)
        return 0
    state = service.verify(args.session_id)
    _emit({"verified": True, "state": _state_summary(state)}, stdout)
    return 0


def _parameters(
    specs: tuple[StrategyParameterSpec, ...],
    raw_json: str,
) -> dict[str, object]:
    try:
        payload = json.loads(raw_json)
    except ValueError:
        raise InvalidDomainInputError(
            "--parameters-json deve ser um objeto JSON válido."
        ) from None
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise InvalidDomainInputError("--parameters-json deve ser um objeto JSON.")
    by_name = {item.name: item for item in specs}
    unknown = set(payload) - set(by_name)
    if unknown:
        raise InvalidDomainInputError("A configuração contém parâmetros desconhecidos.")
    normalized: dict[str, object] = {}
    for name, value in payload.items():
        spec = by_name[name]
        if spec.kind is StrategyParameterKind.DECIMAL:
            if not isinstance(value, str):
                raise InvalidDomainInputError("Parâmetros Decimal devem ser strings JSON.")
            try:
                normalized[name] = Decimal(value)
            except InvalidOperation:
                raise InvalidDomainInputError("Parâmetro Decimal inválido.") from None
        else:
            normalized[name] = value
    return normalized


def _state_summary(state: PaperSessionState) -> dict[str, object]:
    return {
        "session_id": state.session_id,
        "state_id": state.state_id,
        "dataset_version": state.dataset_version,
        "source_checksum": state.source_checksum,
        "start": state.evaluation_range.start.isoformat(),
        "end": state.evaluation_range.end.isoformat(),
        "candles_processed": state.candles_processed,
        "orders": len(state.orders),
        "fills": len(state.fills),
        "risk_halt": state.risk_halt,
        "portfolio": canonical_value(state.portfolio),
        "replayed_at": state.replayed_at.isoformat(),
    }


def _emit(value: object, stdout: TextIO) -> None:
    print(json.dumps(canonical_value(value), sort_keys=True), file=stdout)


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise argparse.ArgumentTypeError("use one timezone-aware UTC timestamp")
    return parsed


def _positive_decimal(value: str) -> Decimal:
    result = _decimal(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _nonnegative_decimal(value: str) -> Decimal:
    result = _decimal(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return result


def _percentage(value: str) -> Decimal:
    result = _nonnegative_decimal(value)
    if result > 100:
        raise argparse.ArgumentTypeError("must not exceed 100")
    return result


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("must be one Decimal") from None
    if not result.is_finite():
        raise argparse.ArgumentTypeError("must be finite")
    return result


def _nonnegative_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if result < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return result
