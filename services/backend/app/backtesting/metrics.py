"""Closed-trade derivation and deterministic Phase 3B metrics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Protocol, cast

from app.backtesting.domain import (
    SUPPORTED_BACKTEST_SCHEMA_VERSIONS,
    BacktestMetrics,
    ClosedTrade,
    EquityPoint,
    Fill,
    OrderSide,
    OrderStatus,
    PortfolioSnapshot,
    SimulatedOrder,
)
from app.backtesting.serialization import canonical_value
from app.market_data.domain import require_utc

_HUNDRED = Decimal("100")
_MICROSECONDS = Decimal("1000000")
_SECONDS_PER_YEAR = Decimal("31536000")
_CALCULATION_PRECISION = 50
_ADVANCED_METRIC_FIELDS = frozenset(
    {
        "return_periods",
        "elapsed_seconds",
        "periods_per_year",
        "cagr",
        "annualized_volatility",
        "annualized_downside_deviation",
        "sharpe_ratio",
        "sortino_ratio",
    }
)


class ExecutionMetricsView(Protocol):
    @property
    def orders(self) -> tuple[SimulatedOrder, ...]: ...

    @property
    def fills(self) -> tuple[Fill, ...]: ...

    @property
    def equity_curve(self) -> tuple[EquityPoint, ...]: ...

    @property
    def final_portfolio(self) -> PortfolioSnapshot: ...


def derive_closed_trades(fills: Sequence[Fill]) -> tuple[ClosedTrade, ...]:
    """Derive average-cost realizations without maintaining parallel engine state."""
    open_quantity = Decimal("0")
    open_notional = Decimal("0")
    open_fees = Decimal("0")
    entry_fill_ids: list[str] = []
    entry_time = None
    entry_candle_index = 0
    trades: list[ClosedTrade] = []

    for fill in fills:
        if fill.side is OrderSide.BUY:
            if open_quantity == 0:
                entry_time = fill.event_time
                entry_candle_index = fill.candle_index
                entry_fill_ids = []
            open_quantity += fill.quantity
            open_notional += fill.notional
            open_fees += fill.fee
            entry_fill_ids.append(fill.fill_id)
            continue

        if fill.quantity > open_quantity or open_quantity <= 0 or entry_time is None:
            raise ValueError("sell fills cannot exceed the open average-cost position")
        fraction = fill.quantity / open_quantity
        allocated_notional = open_notional * fraction
        allocated_entry_fees = open_fees * fraction
        average_entry = allocated_notional / fill.quantity
        gross_pnl = fill.notional - allocated_notional
        fees = allocated_entry_fees + fill.fee
        net_pnl = gross_pnl - fees
        economic_basis = allocated_notional + allocated_entry_fees
        return_pct = None if economic_basis == 0 else net_pnl / economic_basis * _HUNDRED
        trades.append(
            ClosedTrade(
                entry_time=entry_time,
                exit_time=fill.event_time,
                quantity=fill.quantity,
                average_entry=average_entry,
                average_exit=fill.execution_price,
                gross_pnl=gross_pnl,
                fees=fees,
                net_pnl=net_pnl,
                return_pct=return_pct,
                bars_held=max(0, fill.candle_index - entry_candle_index),
                entry_fill_ids=tuple(entry_fill_ids),
                exit_fill_ids=(fill.fill_id,),
            )
        )
        open_quantity -= fill.quantity
        open_notional -= allocated_notional
        open_fees -= allocated_entry_fees
        if open_quantity == 0:
            open_notional = Decimal("0")
            open_fees = Decimal("0")
            entry_fill_ids = []
            entry_time = None

    return tuple(trades)


@dataclass(frozen=True, slots=True)
class _TemporalMetrics:
    return_periods: int
    elapsed_seconds: Decimal
    periods_per_year: Decimal | None
    cagr: Decimal | None
    annualized_volatility: Decimal | None
    annualized_downside_deviation: Decimal | None
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None


def metrics_for_schema(metrics: BacktestMetrics, schema_version: int) -> dict[str, object]:
    """Project metrics to the immutable artifact contract for one schema version."""
    if schema_version not in SUPPORTED_BACKTEST_SCHEMA_VERSIONS:
        raise ValueError("schema_version is not supported")
    value = canonical_value(metrics)
    if not isinstance(value, dict):  # pragma: no cover - canonical dataclass invariant
        raise TypeError("canonical metrics must be a mapping")
    typed_value = cast(dict[str, object], value)
    if schema_version == 1:
        return {
            key: item for key, item in typed_value.items() if key not in _ADVANCED_METRIC_FIELDS
        }
    return typed_value


def _duration_seconds(start: datetime, end: datetime) -> Decimal:
    delta = end - start
    microseconds = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return Decimal(microseconds) / _MICROSECONDS


def _calculate_temporal_metrics(
    curve: Sequence[EquityPoint],
    *,
    initial_equity: Decimal,
    period_start: datetime | None,
) -> _TemporalMetrics:
    if period_start is None:
        start_time = curve[0].event_time
        start_equity = curve[0].equity
        observations = curve[1:]
    else:
        start_time = require_utc(period_start, field_name="period_start")
        if start_time >= curve[0].event_time:
            raise ValueError("period_start must precede the first equity observation")
        start_equity = initial_equity
        observations = curve

    if not observations:
        return _TemporalMetrics(
            return_periods=0,
            elapsed_seconds=Decimal("0"),
            periods_per_year=None,
            cagr=None,
            annualized_volatility=None,
            annualized_downside_deviation=None,
            sharpe_ratio=None,
            sortino_ratio=None,
        )

    previous_time = start_time
    previous_equity = start_equity
    returns: list[Decimal] = []
    return_series_available = previous_equity > 0
    for point in observations:
        if point.event_time <= previous_time:
            raise ValueError("equity observation times must be strictly increasing")
        if return_series_available:
            if previous_equity <= 0:
                return_series_available = False
                returns = []
            else:
                returns.append(point.equity / previous_equity - Decimal("1"))
        previous_time = point.event_time
        previous_equity = point.equity

    elapsed_seconds = _duration_seconds(start_time, observations[-1].event_time)
    if elapsed_seconds <= 0:  # pragma: no cover - guarded by timestamp ordering
        raise ValueError("metric period must have positive duration")

    return_periods = len(observations)
    with localcontext() as context:
        context.prec = _CALCULATION_PRECISION
        periods_per_year = Decimal(return_periods) * _SECONDS_PER_YEAR / elapsed_seconds
        annualization_factor = periods_per_year.sqrt()

        cagr: Decimal | None
        if start_equity <= 0:
            cagr = None
        elif observations[-1].equity == 0:
            cagr = -_HUNDRED
        else:
            equity_ratio = observations[-1].equity / start_equity
            annual_exponent = _SECONDS_PER_YEAR / elapsed_seconds
            cagr = ((equity_ratio.ln() * annual_exponent).exp() - Decimal("1")) * _HUNDRED

        if not return_series_available or len(returns) != return_periods:
            annualized_volatility = None
            annualized_downside_deviation = None
            sharpe_ratio = None
            sortino_ratio = None
        else:
            divisor = Decimal(return_periods)
            mean_return = sum(returns, Decimal("0")) / divisor
            variance = (
                sum(
                    ((period_return - mean_return) ** 2 for period_return in returns),
                    Decimal("0"),
                )
                / divisor
            )
            downside_variance = (
                sum(
                    (min(period_return, Decimal("0")) ** 2 for period_return in returns),
                    Decimal("0"),
                )
                / divisor
            )
            volatility = variance.sqrt()
            downside_deviation = downside_variance.sqrt()
            annualized_volatility = volatility * annualization_factor * _HUNDRED
            annualized_downside_deviation = downside_deviation * annualization_factor * _HUNDRED
            sharpe_ratio = (
                None if volatility == 0 else mean_return / volatility * annualization_factor
            )
            sortino_ratio = (
                None
                if downside_deviation == 0
                else mean_return / downside_deviation * annualization_factor
            )

    return _TemporalMetrics(
        return_periods=return_periods,
        elapsed_seconds=elapsed_seconds,
        periods_per_year=periods_per_year,
        cagr=cagr,
        annualized_volatility=annualized_volatility,
        annualized_downside_deviation=annualized_downside_deviation,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
    )


def calculate_metrics(
    execution: ExecutionMetricsView,
    *,
    initial_equity: Decimal,
    trades: Sequence[ClosedTrade] | None = None,
    period_start: datetime | None = None,
) -> BacktestMetrics:
    if initial_equity <= 0 or not initial_equity.is_finite():
        raise ValueError("initial_equity must be positive and finite")
    curve = execution.equity_curve
    if not curve:
        raise ValueError("metrics require at least one equity point")
    closed = tuple(trades) if trades is not None else derive_closed_trades(execution.fills)
    final = execution.final_portfolio
    final_equity = final.equity
    total_return = (final_equity - initial_equity) / initial_equity * _HUNDRED
    positive = [trade.net_pnl for trade in closed if trade.net_pnl > 0]
    negative = [-trade.net_pnl for trade in closed if trade.net_pnl < 0]
    gross_profit = sum(positive, Decimal("0"))
    gross_loss = sum(negative, Decimal("0"))
    winners = len(positive)
    losers = len(negative)
    trade_count = len(closed)
    total_trade_pnl = sum((trade.net_pnl for trade in closed), Decimal("0"))
    win_rate = None if trade_count == 0 else Decimal(winners) / Decimal(trade_count) * _HUNDRED
    profit_factor = None if gross_loss == 0 else gross_profit / gross_loss
    expectancy = None if trade_count == 0 else total_trade_pnl / Decimal(trade_count)
    average_trade = expectancy
    average_bars = (
        None
        if trade_count == 0
        else Decimal(sum(trade.bars_held for trade in closed)) / Decimal(trade_count)
    )
    exposed = sum(1 for point in curve if point.base_quantity > 0)
    exposure_pct = Decimal(exposed) / Decimal(len(curve)) * _HUNDRED
    turnover = (
        sum((fill.notional for fill in execution.fills), Decimal("0")) / initial_equity * _HUNDRED
    )
    first_price = curve[0].close_price
    last_price = curve[-1].close_price
    buy_and_hold = None if first_price == 0 else (last_price - first_price) / first_price * _HUNDRED
    strategy_vs = None if buy_and_hold is None else total_return - buy_and_hold
    temporal = _calculate_temporal_metrics(
        curve,
        initial_equity=initial_equity,
        period_start=period_start,
    )
    statuses = [order.status for order in execution.orders]
    return BacktestMetrics(
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return=total_return,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_profit=final_equity - initial_equity,
        realized_pnl=final.realized_pnl,
        unrealized_pnl=final.unrealized_pnl,
        total_fees=final.total_fees,
        total_slippage_cost=final.total_slippage_cost,
        maximum_drawdown=max((point.drawdown for point in curve), default=Decimal("0")),
        maximum_drawdown_pct=max((point.drawdown_pct for point in curve), default=Decimal("0")),
        number_of_orders=len(execution.orders),
        filled_orders=statuses.count(OrderStatus.FILLED),
        rejected_orders=statuses.count(OrderStatus.REJECTED),
        cancelled_orders=statuses.count(OrderStatus.CANCELLED),
        expired_orders=statuses.count(OrderStatus.EXPIRED),
        number_of_fills=len(execution.fills),
        number_of_closed_trades=trade_count,
        winning_trades=winners,
        losing_trades=losers,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        average_trade=average_trade,
        average_bars_held=average_bars,
        exposure_pct=exposure_pct,
        turnover=turnover,
        buy_and_hold_return=buy_and_hold,
        strategy_vs_buy_and_hold=strategy_vs,
        return_periods=temporal.return_periods,
        elapsed_seconds=temporal.elapsed_seconds,
        periods_per_year=temporal.periods_per_year,
        cagr=temporal.cagr,
        annualized_volatility=temporal.annualized_volatility,
        annualized_downside_deviation=temporal.annualized_downside_deviation,
        sharpe_ratio=temporal.sharpe_ratio,
        sortino_ratio=temporal.sortino_ratio,
    )
