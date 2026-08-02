"""Typed immutable contracts for deterministic Spot backtesting."""

from __future__ import annotations

import re
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias

from app.backtesting.errors import InvalidOrderIntentError
from app.market_data.domain import DataRange, require_utc

_BPS_DENOMINATOR = Decimal("10000")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_BACKTEST_SCHEMA_VERSIONS = frozenset({1, 2})
SUPPORTED_STRATEGY_LIFECYCLE_VERSIONS = frozenset({1, 2})

StrategyParameterValue: TypeAlias = None | bool | int | str | Decimal
StrategyParameters: TypeAlias = tuple[tuple[str, StrategyParameterValue], ...]


class BacktestStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    DAY = "DAY"


class FillReason(StrEnum):
    MARKET_OPEN = "MARKET_OPEN"
    LIMIT_TOUCHED = "LIMIT_TOUCHED"
    LIMIT_GAP = "LIMIT_GAP"
    STOP_TRIGGERED = "STOP_TRIGGERED"
    STOP_GAP = "STOP_GAP"
    FORCE_CLOSE = "FORCE_CLOSE"


class FillLiquidity(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class SlippageKind(StrEnum):
    FIXED_BPS = "FIXED_BPS"


class IntrabarPolicy(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"


@dataclass(frozen=True, slots=True)
class BacktestRunId:
    """Canonical deterministic SHA-256 run identifier."""

    value: str

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.value) is None:
            raise ValueError("backtest run id must be one lowercase SHA-256 digest")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StrategyDescriptor:
    """Stable strategy identity and canonical immutable parameters."""

    name: str
    version: str
    parameters: StrategyParameters = ()

    def __post_init__(self) -> None:
        name = self.name.strip()
        version = self.version.strip()
        if _SAFE_TOKEN.fullmatch(name) is None or _SAFE_TOKEN.fullmatch(version) is None:
            raise ValueError("strategy name and version must be safe identifiers")
        normalized: list[tuple[str, StrategyParameterValue]] = []
        seen: set[str] = set()
        for raw_key, value in self.parameters:
            key = raw_key.strip()
            if _SAFE_TOKEN.fullmatch(key) is None or key in seen:
                raise ValueError("strategy parameter keys must be unique safe identifiers")
            if isinstance(value, float):
                raise ValueError("strategy parameters must not contain float values")
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError("strategy Decimal parameters must be finite")
            if not isinstance(value, (type(None), bool, int, str, Decimal)):
                raise ValueError("unsupported strategy parameter value")
            normalized.append((key, value))
            seen.add(key)
        normalized.sort(key=lambda item: item[0])
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "parameters", tuple(normalized))


@dataclass(frozen=True, slots=True)
class FeeModel:
    """Quote-asset fee assumptions in basis points."""

    maker_fee_bps: Decimal
    taker_fee_bps: Decimal

    def __post_init__(self) -> None:
        _require_bps(self.maker_fee_bps, "maker_fee_bps")
        _require_bps(self.taker_fee_bps, "taker_fee_bps")

    def rate(self, liquidity: FillLiquidity) -> Decimal:
        bps = self.maker_fee_bps if liquidity is FillLiquidity.MAKER else self.taker_fee_bps
        return bps / _BPS_DENOMINATOR


@dataclass(frozen=True, slots=True)
class SlippageModel:
    """Deterministic adverse-price slippage assumptions."""

    kind: SlippageKind = SlippageKind.FIXED_BPS
    fixed_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _require_bps(self.fixed_bps, "fixed_bps")


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    """All assumptions that can affect simulated fills."""

    fees: FeeModel
    slippage: SlippageModel
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.CONSERVATIVE
    force_close_at_end: bool = False


@dataclass(frozen=True, slots=True)
class InstrumentConstraints:
    """Exchange-like precision and notional constraints for one instrument."""

    minimum_quantity: Decimal
    quantity_step: Decimal
    price_tick: Decimal
    minimum_notional: Decimal
    maximum_notional: Decimal | None = None

    def __post_init__(self) -> None:
        _require_positive(self.minimum_quantity, "minimum_quantity")
        _require_positive(self.quantity_step, "quantity_step")
        _require_positive(self.price_tick, "price_tick")
        _require_nonnegative(self.minimum_notional, "minimum_notional")
        if self.maximum_notional is not None:
            _require_positive(self.maximum_notional, "maximum_notional")
            if self.maximum_notional < self.minimum_notional:
                raise ValueError("maximum_notional must not be below minimum_notional")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Bounded long-only risk configuration evaluated before opening orders."""

    max_order_notional: Decimal | None = None
    max_position_notional: Decimal | None = None
    max_open_orders: int = 1_000
    max_total_orders: int = 100_000
    max_drawdown_pct: Decimal | None = None
    stop_on_max_drawdown: bool = True
    allow_all_in: bool = False
    minimum_quote_reserve: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name, value in (
            ("max_order_notional", self.max_order_notional),
            ("max_position_notional", self.max_position_notional),
        ):
            if value is not None:
                _require_positive(value, name)
        if self.max_open_orders < 1 or self.max_total_orders < 1:
            raise ValueError("order limits must be positive")
        if self.max_open_orders > self.max_total_orders:
            raise ValueError("max_open_orders must not exceed max_total_orders")
        if self.max_drawdown_pct is not None:
            _require_nonnegative(self.max_drawdown_pct, "max_drawdown_pct")
            if self.max_drawdown_pct > Decimal("100"):
                raise ValueError("max_drawdown_pct must not exceed 100")
        _require_nonnegative(self.minimum_quote_reserve, "minimum_quote_reserve")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """One strategy request; the engine alone may convert it into an order."""

    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    time_in_force: TimeInForce = TimeInForce.GTC
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    client_tag: str | None = None

    def __post_init__(self) -> None:
        try:
            _require_positive(self.quantity, "quantity")
            if self.order_type is OrderType.MARKET:
                if self.limit_price is not None or self.stop_price is not None:
                    raise ValueError("market order must not carry trigger prices")
            elif self.order_type is OrderType.LIMIT:
                if self.limit_price is None or self.stop_price is not None:
                    raise ValueError("limit order requires only limit_price")
                _require_positive(self.limit_price, "limit_price")
            elif self.order_type is OrderType.STOP_MARKET:
                if self.stop_price is None or self.limit_price is not None:
                    raise ValueError("stop-market order requires only stop_price")
                _require_positive(self.stop_price, "stop_price")
            else:  # pragma: no cover - enum exhaustiveness guard
                raise ValueError("unsupported order type")
            if self.client_tag is not None:
                tag = self.client_tag.strip()
                if _SAFE_TAG.fullmatch(tag) is None:
                    raise ValueError("client_tag must be a safe identifier")
                object.__setattr__(self, "client_tag", tag)
        except ValueError as error:
            raise InvalidOrderIntentError(str(error)) from None


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    """One deterministic all-or-none order lifecycle record."""

    order_id: str
    created_sequence: int
    created_at: datetime
    created_candle_index: int
    eligible_candle_index: int
    intent: OrderIntent
    status: OrderStatus = OrderStatus.CREATED
    opened_at: datetime | None = None
    terminal_at: datetime | None = None
    rejection_code: str | None = None

    def __post_init__(self) -> None:
        if _SAFE_TOKEN.fullmatch(self.order_id) is None:
            raise ValueError("order_id must be a safe identifier")
        if self.created_sequence < 1 or self.created_candle_index < -1:
            raise ValueError("order sequence and candle index are invalid")
        if self.eligible_candle_index <= self.created_candle_index:
            raise ValueError("an order may only be eligible after its creation candle")
        created_at = require_utc(self.created_at, field_name="created_at")
        object.__setattr__(self, "created_at", created_at)
        if self.opened_at is not None:
            object.__setattr__(
                self,
                "opened_at",
                require_utc(self.opened_at, field_name="opened_at"),
            )
        if self.terminal_at is not None:
            object.__setattr__(
                self,
                "terminal_at",
                require_utc(self.terminal_at, field_name="terminal_at"),
            )
        if self.status is OrderStatus.CREATED:
            if self.opened_at is not None or self.terminal_at is not None:
                raise ValueError("created order cannot have lifecycle timestamps")
        elif self.status is OrderStatus.OPEN:
            if self.opened_at is None or self.terminal_at is not None:
                raise ValueError("open order requires only opened_at")
        elif self.status.is_terminal:
            if self.terminal_at is None:
                raise ValueError("terminal order requires terminal_at")
            if self.status is OrderStatus.REJECTED:
                if not self.rejection_code or _SAFE_TAG.fullmatch(self.rejection_code) is None:
                    raise ValueError("rejected order requires a safe rejection_code")
            elif self.rejection_code is not None:
                raise ValueError("only rejected orders may contain rejection_code")


@dataclass(frozen=True, slots=True)
class Fill:
    """One deterministic all-or-none simulated execution."""

    fill_id: str
    order_id: str
    reason: FillReason
    liquidity: FillLiquidity
    side: OrderSide
    quantity: Decimal
    base_price: Decimal
    execution_price: Decimal
    notional: Decimal
    fee: Decimal
    slippage_cost: Decimal
    event_time: datetime
    candle_index: int

    def __post_init__(self) -> None:
        if (
            _SAFE_TOKEN.fullmatch(self.fill_id) is None
            or _SAFE_TOKEN.fullmatch(self.order_id) is None
        ):
            raise ValueError("fill and order ids must be safe identifiers")
        _require_positive(self.quantity, "quantity")
        _require_positive(self.base_price, "base_price")
        _require_positive(self.execution_price, "execution_price")
        _require_positive(self.notional, "notional")
        _require_nonnegative(self.fee, "fee")
        _require_nonnegative(self.slippage_cost, "slippage_cost")
        if self.notional != self.quantity * self.execution_price:
            raise ValueError("fill notional must equal quantity times execution price")
        if self.candle_index < 0:
            raise ValueError("fill candle_index must be nonnegative")
        object.__setattr__(
            self,
            "event_time",
            require_utc(self.event_time, field_name="event_time"),
        )


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Read-only strategy-facing state; no mutator is exposed."""

    quote_cash: Decimal
    base_quantity: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    total_slippage_cost: Decimal
    equity: Decimal
    peak_equity: Decimal
    drawdown: Decimal
    cost_basis: Decimal = Decimal("0")
    drawdown_pct: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name, value in (
            ("quote_cash", self.quote_cash),
            ("base_quantity", self.base_quantity),
            ("average_entry_price", self.average_entry_price),
            ("total_fees", self.total_fees),
            ("total_slippage_cost", self.total_slippage_cost),
            ("equity", self.equity),
            ("peak_equity", self.peak_equity),
            ("drawdown", self.drawdown),
            ("cost_basis", self.cost_basis),
            ("drawdown_pct", self.drawdown_pct),
        ):
            _require_nonnegative(value, name)
        _require_finite(self.realized_pnl, "realized_pnl")
        _require_finite(self.unrealized_pnl, "unrealized_pnl")
        if self.base_quantity == 0 and (self.average_entry_price != 0 or self.cost_basis != 0):
            raise ValueError("flat portfolio must have zero average entry price and cost basis")
        if self.base_quantity > 0 and (self.average_entry_price <= 0 or self.cost_basis <= 0):
            raise ValueError("open position requires positive average entry price and cost basis")
        if self.equity > self.peak_equity:
            raise ValueError("equity must not exceed peak_equity")
        if self.drawdown != self.peak_equity - self.equity:
            raise ValueError("drawdown must equal peak_equity minus equity")
        expected_pct = (
            Decimal("0")
            if self.peak_equity == 0
            else self.drawdown / self.peak_equity * Decimal("100")
        )
        if self.drawdown_pct != expected_pct:
            raise ValueError("drawdown_pct must match peak and current equity")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One immutable close-based mark-to-market observation."""

    candle_index: int
    event_time: datetime
    close_price: Decimal
    quote_cash: Decimal
    base_quantity: Decimal
    equity: Decimal
    peak_equity: Decimal
    drawdown: Decimal
    drawdown_pct: Decimal

    def __post_init__(self) -> None:
        if self.candle_index < 0:
            raise ValueError("equity candle_index must be nonnegative")
        object.__setattr__(
            self,
            "event_time",
            require_utc(self.event_time, field_name="event_time"),
        )
        _require_positive(self.close_price, "close_price")
        for name, value in (
            ("quote_cash", self.quote_cash),
            ("base_quantity", self.base_quantity),
            ("equity", self.equity),
            ("peak_equity", self.peak_equity),
            ("drawdown", self.drawdown),
            ("drawdown_pct", self.drawdown_pct),
        ):
            _require_nonnegative(value, name)
        if self.equity > self.peak_equity:
            raise ValueError("equity point exceeds peak equity")
        if self.drawdown != self.peak_equity - self.equity:
            raise ValueError("equity point drawdown is inconsistent")
        expected_pct = (
            Decimal("0")
            if self.peak_equity == 0
            else self.drawdown / self.peak_equity * Decimal("100")
        )
        if self.drawdown_pct != expected_pct:
            raise ValueError("equity point drawdown_pct is inconsistent")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Logical run configuration independent from operational timestamps."""

    snapshot_id: str
    data_range: DataRange
    strategy: StrategyDescriptor
    initial_capital: Decimal
    execution: ExecutionAssumptions
    constraints: InstrumentConstraints
    risk_limits: RiskLimits
    history_window: int
    max_candles: int
    max_orders: int
    max_events: int
    engine_version: str
    schema_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.data_range, DataRange):
            raise ValueError("data_range must be a data range")
        _revalidate_data_range(self.data_range, "data_range")
        _revalidate_strategy_descriptor(self.strategy)
        _revalidate_execution_assumptions(self.execution)
        _revalidate_instrument_constraints(self.constraints)
        _revalidate_risk_limits(self.risk_limits)
        if not isinstance(self.snapshot_id, str) or _SAFE_TOKEN.fullmatch(self.snapshot_id) is None:
            raise ValueError("snapshot_id must be a safe identifier")
        _require_positive(self.initial_capital, "initial_capital")
        limits = (self.history_window, self.max_candles, self.max_orders, self.max_events)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in limits):
            raise ValueError("backtest limits must be integers")
        if min(limits) < 1:
            raise ValueError("backtest limits must be positive")
        if self.history_window > self.max_candles:
            raise ValueError("history_window must not exceed max_candles")
        if not isinstance(self.engine_version, str):
            raise ValueError("engine_version must be a safe identifier")
        engine_version = self.engine_version.strip()
        if _SAFE_TOKEN.fullmatch(engine_version) is None:
            raise ValueError("engine_version must be a safe identifier")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version not in SUPPORTED_BACKTEST_SCHEMA_VERSIONS
        ):
            raise ValueError("schema_version is not supported")
        object.__setattr__(self, "engine_version", engine_version)


@dataclass(frozen=True, slots=True)
class EvaluationBacktestConfig(BacktestConfig):
    """Backtest config with an explicit scored range inside its read context."""

    evaluation_range: DataRange
    strategy_lifecycle_version: int

    def __post_init__(self) -> None:
        BacktestConfig.__post_init__(self)
        if not isinstance(self.evaluation_range, DataRange):
            raise ValueError("evaluation_range must be a data range")
        if (
            self.evaluation_range.start < self.data_range.start
            or self.evaluation_range.end != self.data_range.end
        ):
            raise ValueError("evaluation_range must end with and remain inside context")
        lifecycle_version = _validate_strategy_lifecycle_version(self.strategy_lifecycle_version)
        if lifecycle_version == 1 and self.evaluation_range != self.data_range:
            raise ValueError("strategy lifecycle version 1 does not support warmup")


def validate_backtest_config(config: BacktestConfig) -> None:
    """Revalidate one immutable config without repairing hostile mutations."""

    if not isinstance(config, BacktestConfig):
        raise ValueError("backtest config is invalid")
    candidate = copy(config)
    try:
        if isinstance(candidate, EvaluationBacktestConfig):
            EvaluationBacktestConfig.__post_init__(candidate)
        else:
            BacktestConfig.__post_init__(candidate)
    except ValueError as error:
        raise ValueError(str(error)) from None
    except Exception:
        raise ValueError("backtest config is invalid") from None
    if candidate != config:
        raise ValueError("backtest config is not canonical")


def evaluation_range_for(config: BacktestConfig) -> DataRange:
    """Return the scored range while preserving legacy Phase 3A configs."""

    validate_backtest_config(config)
    if isinstance(config, EvaluationBacktestConfig):
        return config.evaluation_range
    return config.data_range


def strategy_lifecycle_version_for(config: BacktestConfig) -> int:
    """Return the explicit Phase 4 lifecycle without changing legacy config identity."""

    validate_backtest_config(config)
    if isinstance(config, EvaluationBacktestConfig):
        return config.strategy_lifecycle_version
    return 1


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """One average-cost realization derived only from immutable fills."""

    entry_time: datetime
    exit_time: datetime
    quantity: Decimal
    average_entry: Decimal
    average_exit: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    return_pct: Decimal | None
    bars_held: int
    entry_fill_ids: tuple[str, ...]
    exit_fill_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "entry_time", require_utc(self.entry_time, field_name="entry_time")
        )
        object.__setattr__(self, "exit_time", require_utc(self.exit_time, field_name="exit_time"))
        if self.exit_time < self.entry_time:
            raise ValueError("trade exit_time must not precede entry_time")
        _require_positive(self.quantity, "quantity")
        _require_positive(self.average_entry, "average_entry")
        _require_positive(self.average_exit, "average_exit")
        _require_finite(self.gross_pnl, "gross_pnl")
        _require_nonnegative(self.fees, "fees")
        _require_finite(self.net_pnl, "net_pnl")
        if self.net_pnl != self.gross_pnl - self.fees:
            raise ValueError("trade net_pnl must equal gross_pnl minus fees")
        if self.return_pct is not None:
            _require_finite(self.return_pct, "return_pct")
        if self.bars_held < 0:
            raise ValueError("trade bars_held must be nonnegative")
        if not self.entry_fill_ids or not self.exit_fill_ids:
            raise ValueError("closed trade requires entry and exit fill ids")
        if any(
            _SAFE_TOKEN.fullmatch(value) is None
            for value in (*self.entry_fill_ids, *self.exit_fill_ids)
        ):
            raise ValueError("trade fill ids must be safe identifiers")


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """Deterministic Phase 3B metrics derived from execution events."""

    initial_equity: Decimal
    final_equity: Decimal
    total_return: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    net_profit: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    total_slippage_cost: Decimal
    maximum_drawdown: Decimal
    maximum_drawdown_pct: Decimal
    number_of_orders: int
    filled_orders: int
    rejected_orders: int
    cancelled_orders: int
    expired_orders: int
    number_of_fills: int
    number_of_closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal | None
    average_trade: Decimal | None
    average_bars_held: Decimal | None
    exposure_pct: Decimal
    turnover: Decimal
    buy_and_hold_return: Decimal | None
    strategy_vs_buy_and_hold: Decimal | None
    return_periods: int
    elapsed_seconds: Decimal
    periods_per_year: Decimal | None
    cagr: Decimal | None
    annualized_volatility: Decimal | None
    annualized_downside_deviation: Decimal | None
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None

    def __post_init__(self) -> None:
        decimal_values = (
            self.initial_equity,
            self.final_equity,
            self.total_return,
            self.gross_profit,
            self.gross_loss,
            self.net_profit,
            self.realized_pnl,
            self.unrealized_pnl,
            self.total_fees,
            self.total_slippage_cost,
            self.maximum_drawdown,
            self.maximum_drawdown_pct,
            self.exposure_pct,
            self.turnover,
        )
        for value in decimal_values:
            _require_finite(value, "metric")
        for value in (
            self.gross_profit,
            self.gross_loss,
            self.total_fees,
            self.total_slippage_cost,
            self.maximum_drawdown,
            self.maximum_drawdown_pct,
            self.exposure_pct,
            self.turnover,
        ):
            if value < 0:
                raise ValueError("nonnegative metric is negative")
        for optional_metric in (
            self.win_rate,
            self.profit_factor,
            self.expectancy,
            self.average_trade,
            self.average_bars_held,
            self.buy_and_hold_return,
            self.strategy_vs_buy_and_hold,
            self.periods_per_year,
            self.cagr,
            self.annualized_volatility,
            self.annualized_downside_deviation,
            self.sharpe_ratio,
            self.sortino_ratio,
        ):
            if optional_metric is not None:
                _require_finite(optional_metric, "optional metric")
        _require_nonnegative(self.elapsed_seconds, "elapsed_seconds")
        for nonnegative_optional in (
            self.periods_per_year,
            self.annualized_volatility,
            self.annualized_downside_deviation,
        ):
            if nonnegative_optional is not None and nonnegative_optional < 0:
                raise ValueError("annualized dispersion metrics must be nonnegative")
        if self.cagr is not None and self.cagr < Decimal("-100"):
            raise ValueError("cagr must not be below -100 percent")
        counts = (
            self.number_of_orders,
            self.filled_orders,
            self.rejected_orders,
            self.cancelled_orders,
            self.expired_orders,
            self.number_of_fills,
            self.number_of_closed_trades,
            self.winning_trades,
            self.losing_trades,
            self.return_periods,
        )
        if any(value < 0 for value in counts):
            raise ValueError("metric counts must be nonnegative")
        if self.return_periods == 0:
            if self.elapsed_seconds != 0 or self.periods_per_year is not None:
                raise ValueError("zero return periods require zero elapsed time")
        elif self.elapsed_seconds <= 0 or self.periods_per_year is None:
            raise ValueError("return periods require positive elapsed time")


@dataclass(frozen=True, slots=True)
class ArtifactChecksum:
    relative_path: str
    checksum: str
    size_bytes: int

    def __post_init__(self) -> None:
        if (
            not self.relative_path
            or self.relative_path.startswith("/")
            or ".." in self.relative_path.split("/")
        ):
            raise ValueError("artifact path must be safe and relative")
        if _SHA256.fullmatch(self.checksum) is None or self.size_bytes < 0:
            raise ValueError("artifact checksum metadata is invalid")


@dataclass(frozen=True, slots=True)
class BacktestManifest:
    run_id: BacktestRunId
    engine_version: str
    schema_version: int
    status: BacktestStatus
    snapshot_id: str
    dataset_key: str
    dataset_version: str
    dataset_checksum: str
    snapshot_data_range: DataRange
    data_range: DataRange
    strategy: StrategyDescriptor
    strategy_parameters_checksum: str
    initial_capital: Decimal
    execution: ExecutionAssumptions
    risk_limits: RiskLimits
    candle_count: int
    order_count: int
    fill_count: int
    trade_count: int
    artifacts: tuple[ArtifactChecksum, ...]
    logical_result_checksum: str
    created_at: datetime
    completed_at: datetime
    context_range: DataRange | None = None
    evaluation_range: DataRange | None = None
    strategy_lifecycle_version: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_data_range, DataRange):
            raise ValueError("manifest snapshot_data_range must be a data range")
        if not isinstance(self.data_range, DataRange):
            raise ValueError("manifest data_range must be a data range")
        if self.context_range is not None and not isinstance(self.context_range, DataRange):
            raise ValueError("manifest context_range must be a data range")
        if self.evaluation_range is not None and not isinstance(self.evaluation_range, DataRange):
            raise ValueError("manifest evaluation_range must be a data range")
        has_explicit_ranges = self.context_range is not None or self.evaluation_range is not None
        if has_explicit_ranges and self.strategy_lifecycle_version is None:
            raise ValueError("manifest with explicit ranges must declare strategy lifecycle")
        lifecycle_version = (
            1
            if self.strategy_lifecycle_version is None
            else _validate_strategy_lifecycle_version(self.strategy_lifecycle_version)
        )
        if self.status is not BacktestStatus.COMPLETE:
            raise ValueError("published backtest manifest must be COMPLETE")
        if (
            _SAFE_TOKEN.fullmatch(self.engine_version) is None
            or self.schema_version not in SUPPORTED_BACKTEST_SCHEMA_VERSIONS
        ):
            raise ValueError("manifest engine or schema version is invalid")
        if _SAFE_TOKEN.fullmatch(self.snapshot_id) is None:
            raise ValueError("manifest snapshot_id is invalid")
        for digest in (
            self.dataset_checksum,
            self.strategy_parameters_checksum,
            self.logical_result_checksum,
        ):
            if _SHA256.fullmatch(digest) is None:
                raise ValueError("manifest checksum is invalid")
        if min(self.candle_count, self.order_count, self.fill_count, self.trade_count) < 0:
            raise ValueError("manifest counts must be nonnegative")
        _require_positive(self.initial_capital, "initial_capital")
        object.__setattr__(
            self, "created_at", require_utc(self.created_at, field_name="created_at")
        )
        object.__setattr__(
            self, "completed_at", require_utc(self.completed_at, field_name="completed_at")
        )
        if self.completed_at < self.created_at:
            raise ValueError("completed_at must not precede created_at")
        context_range = self.context_range or self.data_range
        evaluation_range = self.evaluation_range or self.data_range
        if (
            self.data_range != evaluation_range
            or evaluation_range.start < context_range.start
            or evaluation_range.end != context_range.end
        ):
            raise ValueError("manifest context and evaluation ranges are inconsistent")
        if lifecycle_version == 1 and context_range != evaluation_range:
            raise ValueError("strategy lifecycle version 1 does not support warmup")
        object.__setattr__(self, "context_range", context_range)
        object.__setattr__(self, "evaluation_range", evaluation_range)
        object.__setattr__(self, "strategy_lifecycle_version", lifecycle_version)
        paths = [item.relative_path for item in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest artifact paths must be unique")


@dataclass(frozen=True, slots=True)
class BacktestResult:
    run_id: BacktestRunId
    final_portfolio: PortfolioSnapshot
    metrics: BacktestMetrics
    trades: tuple[ClosedTrade, ...]
    logical_result_checksum: str

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.logical_result_checksum) is None:
            raise ValueError("logical result checksum is invalid")


def _require_finite(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be one finite Decimal")


def _revalidate_data_range(value: object, field_name: str) -> None:
    if not isinstance(value, DataRange):
        raise ValueError(f"{field_name} must be a data range")
    candidate = copy(value)
    try:
        DataRange.__post_init__(candidate)
    except Exception:
        raise ValueError(f"{field_name} must be a valid data range") from None
    if candidate != value:
        raise ValueError(f"{field_name} must be canonical")


def _revalidate_strategy_descriptor(value: object) -> None:
    if not isinstance(value, StrategyDescriptor):
        raise ValueError("strategy must be a strategy descriptor")
    candidate = copy(value)
    try:
        StrategyDescriptor.__post_init__(candidate)
    except Exception:
        raise ValueError("strategy descriptor is invalid") from None
    if candidate != value:
        raise ValueError("strategy descriptor is not canonical")


def _revalidate_execution_assumptions(value: object) -> None:
    if not isinstance(value, ExecutionAssumptions):
        raise ValueError("execution must contain execution assumptions")
    if not isinstance(value.fees, FeeModel):
        raise ValueError("execution fee model is invalid")
    fees = copy(value.fees)
    try:
        FeeModel.__post_init__(fees)
    except Exception:
        raise ValueError("execution fee model is invalid") from None
    if fees != value.fees:
        raise ValueError("execution fee model is not canonical")
    if not isinstance(value.slippage, SlippageModel):
        raise ValueError("execution slippage model is invalid")
    slippage = copy(value.slippage)
    try:
        SlippageModel.__post_init__(slippage)
    except Exception:
        raise ValueError("execution slippage model is invalid") from None
    if (
        slippage != value.slippage
        or value.slippage.kind is not SlippageKind.FIXED_BPS
        or value.intrabar_policy is not IntrabarPolicy.CONSERVATIVE
        or not isinstance(value.force_close_at_end, bool)
    ):
        raise ValueError("execution assumptions are invalid")


def _revalidate_instrument_constraints(value: object) -> None:
    if not isinstance(value, InstrumentConstraints):
        raise ValueError("instrument constraints are invalid")
    candidate = copy(value)
    try:
        InstrumentConstraints.__post_init__(candidate)
    except Exception:
        raise ValueError("instrument constraints are invalid") from None
    if candidate != value:
        raise ValueError("instrument constraints are not canonical")


def _revalidate_risk_limits(value: object) -> None:
    if not isinstance(value, RiskLimits):
        raise ValueError("risk limits are invalid")
    if (
        isinstance(value.max_open_orders, bool)
        or not isinstance(value.max_open_orders, int)
        or isinstance(value.max_total_orders, bool)
        or not isinstance(value.max_total_orders, int)
        or not isinstance(value.stop_on_max_drawdown, bool)
        or not isinstance(value.allow_all_in, bool)
    ):
        raise ValueError("risk limits are invalid")
    candidate = copy(value)
    try:
        RiskLimits.__post_init__(candidate)
    except Exception:
        raise ValueError("risk limits are invalid") from None
    if candidate != value:
        raise ValueError("risk limits are not canonical")


def _validate_strategy_lifecycle_version(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value not in SUPPORTED_STRATEGY_LIFECYCLE_VERSIONS
    ):
        raise ValueError("strategy_lifecycle_version is not supported")
    return value


def _require_positive(value: Decimal, field_name: str) -> None:
    _require_finite(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_nonnegative(value: Decimal, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0:
        raise ValueError(f"{field_name} must be nonnegative")


def _require_bps(value: Decimal, field_name: str) -> None:
    _require_nonnegative(value, field_name)
    if value > Decimal("1000"):
        raise ValueError(f"{field_name} must not exceed 1000 bps")
