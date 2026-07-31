"""Closed-trade derivation and deterministic Phase 3A metrics."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from app.backtesting.domain import (
    BacktestMetrics,
    ClosedTrade,
    EquityPoint,
    Fill,
    OrderSide,
    OrderStatus,
    PortfolioSnapshot,
    SimulatedOrder,
)

_HUNDRED = Decimal("100")


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


def calculate_metrics(
    execution: ExecutionMetricsView,
    *,
    initial_equity: Decimal,
    trades: Sequence[ClosedTrade] | None = None,
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
    )
