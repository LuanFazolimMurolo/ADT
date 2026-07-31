"""Pure Decimal Spot portfolio accounting for deterministic backtests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.backtesting.domain import Fill, OrderSide, PortfolioSnapshot
from app.backtesting.errors import InsufficientCashError, InsufficientPositionError


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Complete immutable long-only portfolio state owned by the engine."""

    initial_equity: Decimal
    quote_cash: Decimal
    base_quantity: Decimal
    cost_basis: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    total_slippage_cost: Decimal
    equity: Decimal
    peak_equity: Decimal
    drawdown: Decimal
    drawdown_pct: Decimal
    last_mark_price: Decimal | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("initial_equity", self.initial_equity),
            ("quote_cash", self.quote_cash),
            ("base_quantity", self.base_quantity),
            ("cost_basis", self.cost_basis),
            ("average_entry_price", self.average_entry_price),
            ("total_fees", self.total_fees),
            ("total_slippage_cost", self.total_slippage_cost),
            ("equity", self.equity),
            ("peak_equity", self.peak_equity),
            ("drawdown", self.drawdown),
            ("drawdown_pct", self.drawdown_pct),
        ):
            _require_nonnegative(value, name)
        _require_finite(self.realized_pnl, "realized_pnl")
        _require_finite(self.unrealized_pnl, "unrealized_pnl")
        if self.last_mark_price is not None:
            _require_positive(self.last_mark_price, "last_mark_price")
        if self.base_quantity == 0:
            if self.cost_basis != 0 or self.average_entry_price != 0:
                raise ValueError("flat portfolio must have zero cost basis and average price")
        elif self.cost_basis <= 0 or self.average_entry_price <= 0:
            raise ValueError("open position requires positive cost basis and average price")
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

    def snapshot(self) -> PortfolioSnapshot:
        """Return the bounded immutable strategy-facing portfolio view."""
        return PortfolioSnapshot(
            quote_cash=self.quote_cash,
            base_quantity=self.base_quantity,
            average_entry_price=self.average_entry_price,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            total_fees=self.total_fees,
            total_slippage_cost=self.total_slippage_cost,
            equity=self.equity,
            peak_equity=self.peak_equity,
            drawdown=self.drawdown,
            cost_basis=self.cost_basis,
            drawdown_pct=self.drawdown_pct,
        )


@dataclass(frozen=True, slots=True)
class PortfolioMutation:
    """Auditable financial effects of applying exactly one fill."""

    before: PortfolioState
    after: PortfolioState
    quote_delta: Decimal
    base_delta: Decimal
    cost_basis_delta: Decimal
    realized_pnl_delta: Decimal


def initialize_portfolio(initial_capital: Decimal) -> PortfolioState:
    """Create a flat portfolio with all capital held in the quote asset."""
    _require_positive(initial_capital, "initial_capital")
    zero = Decimal("0")
    return PortfolioState(
        initial_equity=initial_capital,
        quote_cash=initial_capital,
        base_quantity=zero,
        cost_basis=zero,
        average_entry_price=zero,
        realized_pnl=zero,
        unrealized_pnl=zero,
        total_fees=zero,
        total_slippage_cost=zero,
        equity=initial_capital,
        peak_equity=initial_capital,
        drawdown=zero,
        drawdown_pct=zero,
    )


def apply_fill(state: PortfolioState, fill: Fill) -> PortfolioMutation:
    """Apply one all-or-none fill using weighted-average Spot cost accounting."""
    if fill.side is OrderSide.BUY:
        mutation = _apply_buy(state, fill)
    else:
        mutation = _apply_sell(state, fill)
    marked = mark_to_market(mutation.after, fill.execution_price)
    return replace(mutation, after=marked)


def mark_to_market(state: PortfolioState, price: Decimal) -> PortfolioState:
    """Mark the existing position without producing cash or realized PnL."""
    _require_positive(price, "mark_price")
    market_value = state.base_quantity * price
    unrealized = market_value - state.cost_basis
    equity = state.quote_cash + market_value
    peak = max(state.peak_equity, equity)
    drawdown = peak - equity
    drawdown_pct = Decimal("0") if peak == 0 else drawdown / peak * Decimal("100")
    return replace(
        state,
        unrealized_pnl=unrealized,
        equity=equity,
        peak_equity=peak,
        drawdown=drawdown,
        drawdown_pct=drawdown_pct,
        last_mark_price=price,
    )


def _apply_buy(state: PortfolioState, fill: Fill) -> PortfolioMutation:
    economic_cost = fill.notional + fill.fee
    if economic_cost > state.quote_cash:
        raise InsufficientCashError()
    quote_cash = state.quote_cash - economic_cost
    base_quantity = state.base_quantity + fill.quantity
    cost_basis = state.cost_basis + economic_cost
    average_entry = cost_basis / base_quantity
    after = replace(
        state,
        quote_cash=quote_cash,
        base_quantity=base_quantity,
        cost_basis=cost_basis,
        average_entry_price=average_entry,
        total_fees=state.total_fees + fill.fee,
        total_slippage_cost=state.total_slippage_cost + fill.slippage_cost,
    )
    return PortfolioMutation(
        before=state,
        after=after,
        quote_delta=-economic_cost,
        base_delta=fill.quantity,
        cost_basis_delta=economic_cost,
        realized_pnl_delta=Decimal("0"),
    )


def _apply_sell(state: PortfolioState, fill: Fill) -> PortfolioMutation:
    if fill.quantity > state.base_quantity:
        raise InsufficientPositionError()
    if fill.quantity == state.base_quantity:
        released_cost = state.cost_basis
    else:
        released_cost = state.cost_basis * fill.quantity / state.base_quantity
    net_proceeds = fill.notional - fill.fee
    realized_delta = net_proceeds - released_cost
    quote_cash = state.quote_cash + net_proceeds
    base_quantity = state.base_quantity - fill.quantity
    cost_basis = state.cost_basis - released_cost
    if base_quantity == 0:
        cost_basis = Decimal("0")
        average_entry = Decimal("0")
    else:
        average_entry = cost_basis / base_quantity
    after = replace(
        state,
        quote_cash=quote_cash,
        base_quantity=base_quantity,
        cost_basis=cost_basis,
        average_entry_price=average_entry,
        realized_pnl=state.realized_pnl + realized_delta,
        total_fees=state.total_fees + fill.fee,
        total_slippage_cost=state.total_slippage_cost + fill.slippage_cost,
    )
    return PortfolioMutation(
        before=state,
        after=after,
        quote_delta=net_proceeds,
        base_delta=-fill.quantity,
        cost_basis_delta=-released_cost,
        realized_pnl_delta=realized_delta,
    )


def _require_finite(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be one finite Decimal")


def _require_positive(value: Decimal, field_name: str) -> None:
    _require_finite(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_nonnegative(value: Decimal, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0:
        raise ValueError(f"{field_name} must be nonnegative")
