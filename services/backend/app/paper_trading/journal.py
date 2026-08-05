"""Deterministic trade-cycle reconstruction for verified paper sessions."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.backtesting.domain import (
    Fill,
    FillLiquidity,
    FillReason,
    OrderSide,
    OrderStatus,
    OrderType,
    SimulatedOrder,
    StrategyDescriptor,
    TimeInForce,
)
from app.backtesting.serialization import canonical_json_bytes
from app.market_data.domain import Timeframe, TradingPair, require_utc
from app.paper_trading.domain import (
    PaperSessionConfig,
    PaperSessionState,
    validate_paper_state_against_config,
)
from app.paper_trading.errors import InvalidPaperSessionError, PaperSessionVerificationError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ZERO = Decimal("0")


class PaperTradeStatus(StrEnum):
    """Lifecycle of one continuous long-only position cycle."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class PaperTradeExecution:
    """One verified fill joined to the order that produced it."""

    fill_id: str
    order_id: str
    order_sequence: int
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    client_tag: str | None
    fill_reason: FillReason
    liquidity: FillLiquidity
    quantity: Decimal
    base_price: Decimal
    execution_price: Decimal
    notional: Decimal
    fee: Decimal
    slippage_cost: Decimal
    event_time: datetime
    candle_index: int

    def __post_init__(self) -> None:
        try:
            _safe_identifier(self.fill_id, "fill_id")
            _safe_identifier(self.order_id, "order_id")
            if type(self.order_sequence) is not int or self.order_sequence < 1:
                raise ValueError("order_sequence must be positive")
            if not isinstance(self.side, OrderSide):
                raise ValueError("side must be canonical")
            if not isinstance(self.order_type, OrderType):
                raise ValueError("order_type must be canonical")
            if not isinstance(self.time_in_force, TimeInForce):
                raise ValueError("time_in_force must be canonical")
            if self.client_tag is not None:
                _safe_identifier(self.client_tag, "client_tag")
            if not isinstance(self.fill_reason, FillReason):
                raise ValueError("fill_reason must be canonical")
            if not isinstance(self.liquidity, FillLiquidity):
                raise ValueError("liquidity must be canonical")
            _positive(self.quantity, "quantity")
            _positive(self.base_price, "base_price")
            _positive(self.execution_price, "execution_price")
            _positive(self.notional, "notional")
            _nonnegative(self.fee, "fee")
            _nonnegative(self.slippage_cost, "slippage_cost")
            if self.notional != self.quantity * self.execution_price:
                raise ValueError("notional must match quantity and execution_price")
            event_time = require_utc(self.event_time, field_name="journal_event_time")
            object.__setattr__(self, "event_time", event_time)
            if type(self.candle_index) is not int or self.candle_index < 0:
                raise ValueError("candle_index must be nonnegative")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None

    @classmethod
    def from_domain(
        cls,
        order: SimulatedOrder,
        fill: Fill,
    ) -> PaperTradeExecution:
        """Join one fill to its canonical filled order and reject divergence."""
        try:
            if not isinstance(order, SimulatedOrder) or not isinstance(fill, Fill):
                raise ValueError("order and fill must be canonical")
            if order.order_id != fill.order_id or order.status is not OrderStatus.FILLED:
                raise ValueError("fill does not reference one filled order")
            if order.intent.side is not fill.side or order.intent.quantity != fill.quantity:
                raise ValueError("fill diverges from its all-or-none order intent")
            if order.terminal_at != fill.event_time:
                raise ValueError("fill time diverges from the order terminal time")
            if fill.candle_index < order.eligible_candle_index:
                raise ValueError("fill precedes order eligibility")
            return cls(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                order_sequence=order.created_sequence,
                side=fill.side,
                order_type=order.intent.order_type,
                time_in_force=order.intent.time_in_force,
                client_tag=order.intent.client_tag,
                fill_reason=fill.reason,
                liquidity=fill.liquidity,
                quantity=fill.quantity,
                base_price=fill.base_price,
                execution_price=fill.execution_price,
                notional=fill.notional,
                fee=fill.fee,
                slippage_cost=fill.slippage_cost,
                event_time=fill.event_time,
                candle_index=fill.candle_index,
            )
        except Exception as error:
            raise PaperSessionVerificationError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperTrade:
    """One flat-to-flat trade cycle, or the single position still open."""

    trade_id: str
    session_id: str
    sequence: int
    status: PaperTradeStatus
    opened_at: datetime
    last_entry_at: datetime
    first_exit_at: datetime | None
    closed_at: datetime | None
    entry_executions: tuple[PaperTradeExecution, ...]
    exit_executions: tuple[PaperTradeExecution, ...]
    opened_quantity: Decimal
    closed_quantity: Decimal
    remaining_quantity: Decimal
    entry_notional: Decimal
    exit_notional: Decimal
    entry_fees: Decimal
    exit_fees: Decimal
    entry_slippage_cost: Decimal
    exit_slippage_cost: Decimal
    entry_cost_basis: Decimal
    released_cost_basis: Decimal
    remaining_cost_basis: Decimal
    average_entry_price: Decimal
    average_exit_price: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    net_pnl: Decimal
    mark_price: Decimal | None

    def __post_init__(self) -> None:
        try:
            _sha256(self.trade_id, "trade_id")
            _sha256(self.session_id, "session_id")
            if type(self.sequence) is not int or self.sequence < 1:
                raise ValueError("sequence must be positive")
            if not isinstance(self.status, PaperTradeStatus):
                raise ValueError("status must be canonical")
            opened_at = require_utc(self.opened_at, field_name="trade_opened_at")
            last_entry_at = require_utc(self.last_entry_at, field_name="trade_last_entry_at")
            object.__setattr__(self, "opened_at", opened_at)
            object.__setattr__(self, "last_entry_at", last_entry_at)
            first_exit_at = _optional_utc(self.first_exit_at, "trade_first_exit_at")
            closed_at = _optional_utc(self.closed_at, "trade_closed_at")
            object.__setattr__(self, "first_exit_at", first_exit_at)
            object.__setattr__(self, "closed_at", closed_at)
            if last_entry_at < opened_at:
                raise ValueError("last entry must not precede opening")
            if not isinstance(self.entry_executions, tuple) or not self.entry_executions:
                raise ValueError("trade requires at least one entry execution")
            if not isinstance(self.exit_executions, tuple):
                raise ValueError("exit executions must be one tuple")
            if any(
                not isinstance(item, PaperTradeExecution) or item.side is not OrderSide.BUY
                for item in self.entry_executions
            ):
                raise ValueError("entry executions must be canonical buys")
            if any(
                not isinstance(item, PaperTradeExecution) or item.side is not OrderSide.SELL
                for item in self.exit_executions
            ):
                raise ValueError("exit executions must be canonical sells")
            _validate_execution_order(self.entry_executions)
            _validate_execution_order(self.exit_executions)
            if self.trade_id != _trade_id(
                self.session_id,
                self.sequence,
                self.entry_executions[0].fill_id,
            ):
                raise ValueError("trade_id diverges from the stable trade identity")
            if opened_at != self.entry_executions[0].event_time:
                raise ValueError("opened_at must match the first entry")
            if last_entry_at != self.entry_executions[-1].event_time:
                raise ValueError("last_entry_at must match the last entry")
            expected_first_exit = (
                None if not self.exit_executions else self.exit_executions[0].event_time
            )
            if first_exit_at != expected_first_exit:
                raise ValueError("first_exit_at must match the first exit")
            if self.exit_executions and self.exit_executions[0].event_time < opened_at:
                raise ValueError("exit must not precede opening")

            for name, value in (
                ("opened_quantity", self.opened_quantity),
                ("closed_quantity", self.closed_quantity),
                ("remaining_quantity", self.remaining_quantity),
                ("entry_notional", self.entry_notional),
                ("exit_notional", self.exit_notional),
                ("entry_fees", self.entry_fees),
                ("exit_fees", self.exit_fees),
                ("entry_slippage_cost", self.entry_slippage_cost),
                ("exit_slippage_cost", self.exit_slippage_cost),
                ("entry_cost_basis", self.entry_cost_basis),
                ("released_cost_basis", self.released_cost_basis),
                ("remaining_cost_basis", self.remaining_cost_basis),
            ):
                _nonnegative(value, name)
            _positive(self.opened_quantity, "opened_quantity")
            _positive(self.entry_notional, "entry_notional")
            _positive(self.entry_cost_basis, "entry_cost_basis")
            _positive(self.average_entry_price, "average_entry_price")
            if self.average_exit_price is not None:
                _positive(self.average_exit_price, "average_exit_price")
            _finite(self.realized_pnl, "realized_pnl")
            _finite(self.unrealized_pnl, "unrealized_pnl")
            _finite(self.net_pnl, "net_pnl")
            if self.mark_price is not None:
                _positive(self.mark_price, "mark_price")

            if self.opened_quantity != sum(
                (item.quantity for item in self.entry_executions), _ZERO
            ):
                raise ValueError("opened_quantity diverges from entries")
            if self.closed_quantity != sum((item.quantity for item in self.exit_executions), _ZERO):
                raise ValueError("closed_quantity diverges from exits")
            if self.remaining_quantity != self.opened_quantity - self.closed_quantity:
                raise ValueError("remaining_quantity is inconsistent")
            if self.entry_notional != sum((item.notional for item in self.entry_executions), _ZERO):
                raise ValueError("entry_notional diverges from entries")
            if self.exit_notional != sum((item.notional for item in self.exit_executions), _ZERO):
                raise ValueError("exit_notional diverges from exits")
            if self.entry_fees != sum((item.fee for item in self.entry_executions), _ZERO):
                raise ValueError("entry_fees diverge from entries")
            if self.exit_fees != sum((item.fee for item in self.exit_executions), _ZERO):
                raise ValueError("exit_fees diverge from exits")
            if self.entry_slippage_cost != sum(
                (item.slippage_cost for item in self.entry_executions), _ZERO
            ):
                raise ValueError("entry slippage diverges from entries")
            if self.exit_slippage_cost != sum(
                (item.slippage_cost for item in self.exit_executions), _ZERO
            ):
                raise ValueError("exit slippage diverges from exits")
            if self.entry_cost_basis != self.entry_notional + self.entry_fees:
                raise ValueError("entry cost basis must include entry fees")
            if self.remaining_cost_basis != self.entry_cost_basis - self.released_cost_basis:
                raise ValueError("remaining cost basis is inconsistent")
            if self.average_entry_price != self.entry_notional / self.opened_quantity:
                raise ValueError("average entry price is inconsistent")
            expected_average_exit = (
                None if self.closed_quantity == 0 else self.exit_notional / self.closed_quantity
            )
            if self.average_exit_price != expected_average_exit:
                raise ValueError("average exit price is inconsistent")
            if self.net_pnl != self.realized_pnl + self.unrealized_pnl:
                raise ValueError("net_pnl must combine realized and unrealized PnL")

            if self.status is PaperTradeStatus.OPEN:
                if (
                    self.remaining_quantity <= 0
                    or self.remaining_cost_basis <= 0
                    or closed_at is not None
                    or self.mark_price is None
                ):
                    raise ValueError("open trade lifecycle is inconsistent")
            else:
                expected_closed_at = (
                    None if not self.exit_executions else self.exit_executions[-1].event_time
                )
                if (
                    self.remaining_quantity != 0
                    or self.remaining_cost_basis != 0
                    or not self.exit_executions
                    or closed_at != expected_closed_at
                    or self.mark_price is not None
                    or self.unrealized_pnl != 0
                ):
                    raise ValueError("closed trade lifecycle is inconsistent")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None

    @property
    def total_fees(self) -> Decimal:
        return self.entry_fees + self.exit_fees

    @property
    def total_slippage_cost(self) -> Decimal:
        return self.entry_slippage_cost + self.exit_slippage_cost


@dataclass(frozen=True, slots=True)
class PaperTradeJournal:
    """State-bound deterministic projection of executions and trade cycles."""

    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    pair: TradingPair
    timeframe: Timeframe
    strategy: StrategyDescriptor
    last_candle_open_time: datetime
    replayed_at: datetime
    executions: tuple[PaperTradeExecution, ...]
    trades: tuple[PaperTrade, ...]
    closed_trades_count: int
    open_trades_count: int
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal
    total_net_pnl: Decimal
    total_fees: Decimal
    total_slippage_cost: Decimal

    def __post_init__(self) -> None:
        try:
            for value, name in (
                (self.session_id, "session_id"),
                (self.config_checksum, "config_checksum"),
                (self.state_id, "state_id"),
                (self.state_checksum, "state_checksum"),
            ):
                _sha256(value, name)
            if not isinstance(self.pair, TradingPair) or not isinstance(self.timeframe, Timeframe):
                raise ValueError("pair and timeframe must be canonical")
            if not isinstance(self.strategy, StrategyDescriptor):
                raise ValueError("strategy must be canonical")
            last_candle = require_utc(
                self.last_candle_open_time,
                field_name="journal_last_candle_open_time",
            )
            replayed_at = require_utc(self.replayed_at, field_name="journal_replayed_at")
            object.__setattr__(self, "last_candle_open_time", last_candle)
            object.__setattr__(self, "replayed_at", replayed_at)
            if not isinstance(self.executions, tuple) or any(
                not isinstance(item, PaperTradeExecution) for item in self.executions
            ):
                raise ValueError("executions must be canonical")
            if not isinstance(self.trades, tuple) or any(
                not isinstance(item, PaperTrade) for item in self.trades
            ):
                raise ValueError("trades must be canonical")
            _validate_execution_order(self.executions)
            if tuple(item.sequence for item in self.trades) != tuple(
                range(1, len(self.trades) + 1)
            ):
                raise ValueError("trade sequences must be contiguous")
            if any(item.session_id != self.session_id for item in self.trades):
                raise ValueError("trade belongs to another session")
            for left, right in zip(self.trades, self.trades[1:]):
                if (
                    left.status is not PaperTradeStatus.CLOSED
                    or left.closed_at is None
                    or left.closed_at > right.opened_at
                ):
                    raise ValueError("trade cycles overlap or are out of order")
            if self.trades and any(
                item.status is PaperTradeStatus.OPEN for item in self.trades[:-1]
            ):
                raise ValueError("only the final trade may remain open")
            trade_executions = tuple(
                execution
                for trade in self.trades
                for execution in (*trade.entry_executions, *trade.exit_executions)
            )
            if tuple(sorted(trade_executions, key=_execution_key)) != self.executions:
                raise ValueError("trade executions diverge from journal executions")
            if type(self.closed_trades_count) is not int or type(self.open_trades_count) is not int:
                raise ValueError("trade counts must be integers")
            if self.closed_trades_count != sum(
                item.status is PaperTradeStatus.CLOSED for item in self.trades
            ):
                raise ValueError("closed trade count is inconsistent")
            if self.open_trades_count != sum(
                item.status is PaperTradeStatus.OPEN for item in self.trades
            ):
                raise ValueError("open trade count is inconsistent")
            if self.open_trades_count > 1:
                raise ValueError("Spot journal may contain at most one open trade")
            _nonnegative(self.total_fees, "total_fees")
            _nonnegative(self.total_slippage_cost, "total_slippage_cost")
            _finite(self.total_realized_pnl, "total_realized_pnl")
            _finite(self.total_unrealized_pnl, "total_unrealized_pnl")
            _finite(self.total_net_pnl, "total_net_pnl")
            if self.total_realized_pnl != sum((item.realized_pnl for item in self.trades), _ZERO):
                raise ValueError("total realized PnL is inconsistent")
            if self.total_unrealized_pnl != sum(
                (item.unrealized_pnl for item in self.trades), _ZERO
            ):
                raise ValueError("total unrealized PnL is inconsistent")
            if self.total_net_pnl != self.total_realized_pnl + self.total_unrealized_pnl:
                raise ValueError("total net PnL is inconsistent")
            if self.total_fees != sum((item.total_fees for item in self.trades), _ZERO):
                raise ValueError("total fees are inconsistent")
            if self.total_slippage_cost != sum(
                (item.total_slippage_cost for item in self.trades), _ZERO
            ):
                raise ValueError("total slippage is inconsistent")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(slots=True)
class _TradeAccumulator:
    sequence: int
    session_id: str
    entries: list[PaperTradeExecution] = field(default_factory=list)
    exits: list[PaperTradeExecution] = field(default_factory=list)
    remaining_quantity: Decimal = _ZERO
    cost_basis: Decimal = _ZERO
    released_cost_basis: Decimal = _ZERO
    realized_pnl: Decimal = _ZERO

    def buy(self, execution: PaperTradeExecution) -> None:
        if execution.side is not OrderSide.BUY:
            raise PaperSessionVerificationError("A entrada da operação não é uma compra.")
        self.entries.append(execution)
        self.remaining_quantity += execution.quantity
        self.cost_basis += execution.notional + execution.fee

    def sell(self, execution: PaperTradeExecution) -> None:
        if execution.side is not OrderSide.SELL:
            raise PaperSessionVerificationError("A saída da operação não é uma venda.")
        if execution.quantity > self.remaining_quantity or self.remaining_quantity <= 0:
            raise PaperSessionVerificationError("A venda excede a posição Spot reconstruída.")
        released = (
            self.cost_basis
            if execution.quantity == self.remaining_quantity
            else self.cost_basis * execution.quantity / self.remaining_quantity
        )
        self.exits.append(execution)
        self.remaining_quantity -= execution.quantity
        self.cost_basis -= released
        self.released_cost_basis += released
        self.realized_pnl += execution.notional - execution.fee - released
        if self.remaining_quantity == 0:
            self.cost_basis = _ZERO

    def finish(
        self,
        *,
        mark_price: Decimal | None,
        unrealized_pnl: Decimal,
    ) -> PaperTrade:
        if not self.entries:
            raise PaperSessionVerificationError("A operação não possui entrada.")
        opened_quantity = sum((item.quantity for item in self.entries), _ZERO)
        closed_quantity = sum((item.quantity for item in self.exits), _ZERO)
        entry_notional = sum((item.notional for item in self.entries), _ZERO)
        exit_notional = sum((item.notional for item in self.exits), _ZERO)
        entry_fees = sum((item.fee for item in self.entries), _ZERO)
        exit_fees = sum((item.fee for item in self.exits), _ZERO)
        entry_slippage = sum((item.slippage_cost for item in self.entries), _ZERO)
        exit_slippage = sum((item.slippage_cost for item in self.exits), _ZERO)
        status = PaperTradeStatus.OPEN if self.remaining_quantity > 0 else PaperTradeStatus.CLOSED
        return PaperTrade(
            trade_id=_trade_id(self.session_id, self.sequence, self.entries[0].fill_id),
            session_id=self.session_id,
            sequence=self.sequence,
            status=status,
            opened_at=self.entries[0].event_time,
            last_entry_at=self.entries[-1].event_time,
            first_exit_at=None if not self.exits else self.exits[0].event_time,
            closed_at=None if status is PaperTradeStatus.OPEN else self.exits[-1].event_time,
            entry_executions=tuple(self.entries),
            exit_executions=tuple(self.exits),
            opened_quantity=opened_quantity,
            closed_quantity=closed_quantity,
            remaining_quantity=self.remaining_quantity,
            entry_notional=entry_notional,
            exit_notional=exit_notional,
            entry_fees=entry_fees,
            exit_fees=exit_fees,
            entry_slippage_cost=entry_slippage,
            exit_slippage_cost=exit_slippage,
            entry_cost_basis=entry_notional + entry_fees,
            released_cost_basis=self.released_cost_basis,
            remaining_cost_basis=self.cost_basis,
            average_entry_price=entry_notional / opened_quantity,
            average_exit_price=None if closed_quantity == 0 else exit_notional / closed_quantity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized_pnl,
            net_pnl=self.realized_pnl + unrealized_pnl,
            mark_price=mark_price,
        )


def build_paper_trade_journal(
    config: PaperSessionConfig,
    state: PaperSessionState,
) -> PaperTradeJournal:
    """Reconstruct verified long-only trade cycles from one persisted paper state."""
    validate_paper_state_against_config(state, config)
    orders = {order.order_id: order for order in state.orders}
    try:
        executions = tuple(
            sorted(
                (
                    PaperTradeExecution.from_domain(orders[fill.order_id], fill)
                    for fill in state.fills
                ),
                key=_execution_key,
            )
        )
    except KeyError:
        raise PaperSessionVerificationError("Um fill não possui ordem correspondente.") from None

    trades: list[PaperTrade] = []
    current: _TradeAccumulator | None = None
    sequence = 0
    quote_cash = config.initial_capital
    base_quantity = _ZERO
    cost_basis = _ZERO
    realized_pnl = _ZERO
    total_fees = _ZERO
    total_slippage = _ZERO

    for execution in executions:
        total_fees += execution.fee
        total_slippage += execution.slippage_cost
        if execution.side is OrderSide.BUY:
            if current is None:
                sequence += 1
                current = _TradeAccumulator(sequence=sequence, session_id=state.session_id)
            economic_cost = execution.notional + execution.fee
            quote_cash -= economic_cost
            base_quantity += execution.quantity
            cost_basis += economic_cost
            current.buy(execution)
            continue

        if current is None or base_quantity <= 0 or execution.quantity > base_quantity:
            raise PaperSessionVerificationError("A venda não possui posição Spot correspondente.")
        released = (
            cost_basis
            if execution.quantity == base_quantity
            else cost_basis * execution.quantity / base_quantity
        )
        quote_cash += execution.notional - execution.fee
        base_quantity -= execution.quantity
        cost_basis -= released
        realized_pnl += execution.notional - execution.fee - released
        current.sell(execution)
        if base_quantity == 0:
            cost_basis = _ZERO
            trades.append(current.finish(mark_price=None, unrealized_pnl=_ZERO))
            current = None

    _verify_accounting(
        state,
        quote_cash=quote_cash,
        base_quantity=base_quantity,
        cost_basis=cost_basis,
        realized_pnl=realized_pnl,
        total_fees=total_fees,
        total_slippage=total_slippage,
    )

    if current is not None:
        mark_price = _state_mark_price(state)
        trades.append(
            current.finish(
                mark_price=mark_price,
                unrealized_pnl=state.portfolio.unrealized_pnl,
            )
        )

    trade_tuple = tuple(trades)
    return PaperTradeJournal(
        session_id=state.session_id,
        config_checksum=state.config_checksum,
        state_id=state.state_id,
        state_checksum=state.checksum,
        pair=config.pair,
        timeframe=config.timeframe,
        strategy=config.strategy,
        last_candle_open_time=state.last_candle_open_time,
        replayed_at=state.replayed_at,
        executions=executions,
        trades=trade_tuple,
        closed_trades_count=sum(trade.status is PaperTradeStatus.CLOSED for trade in trade_tuple),
        open_trades_count=sum(trade.status is PaperTradeStatus.OPEN for trade in trade_tuple),
        total_realized_pnl=state.portfolio.realized_pnl,
        total_unrealized_pnl=state.portfolio.unrealized_pnl,
        total_net_pnl=state.portfolio.realized_pnl + state.portfolio.unrealized_pnl,
        total_fees=state.portfolio.total_fees,
        total_slippage_cost=state.portfolio.total_slippage_cost,
    )


def _verify_accounting(
    state: PaperSessionState,
    *,
    quote_cash: Decimal,
    base_quantity: Decimal,
    cost_basis: Decimal,
    realized_pnl: Decimal,
    total_fees: Decimal,
    total_slippage: Decimal,
) -> None:
    portfolio = state.portfolio
    expected_average = _ZERO if base_quantity == 0 else cost_basis / base_quantity
    if (
        quote_cash != portfolio.quote_cash
        or base_quantity != portfolio.base_quantity
        or cost_basis != portfolio.cost_basis
        or expected_average != portfolio.average_entry_price
        or realized_pnl != portfolio.realized_pnl
        or total_fees != portfolio.total_fees
        or total_slippage != portfolio.total_slippage_cost
    ):
        raise PaperSessionVerificationError(
            "A contabilidade reconstruída diverge do portfolio persistido."
        )
    if base_quantity == 0:
        if portfolio.unrealized_pnl != 0 or portfolio.equity != portfolio.quote_cash:
            raise PaperSessionVerificationError(
                "O portfolio flat possui marcação de mercado inconsistente."
            )
        return
    mark_price = _state_mark_price(state)
    if base_quantity * mark_price - cost_basis != portfolio.unrealized_pnl:
        raise PaperSessionVerificationError("O PnL não realizado diverge da marcação persistida.")


def _state_mark_price(state: PaperSessionState) -> Decimal:
    portfolio = state.portfolio
    if portfolio.base_quantity <= 0:
        raise PaperSessionVerificationError("A sessão não possui posição aberta.")
    market_value = portfolio.equity - portfolio.quote_cash
    if market_value <= 0:
        raise PaperSessionVerificationError("O valor de mercado da posição é inválido.")
    mark_price = market_value / portfolio.base_quantity
    _positive(mark_price, "mark_price")
    return mark_price


def _trade_id(session_id: str, sequence: int, first_fill_id: str) -> str:
    payload = {
        "session_id": session_id,
        "sequence": sequence,
        "first_fill_id": first_fill_id,
    }
    return hashlib.sha256(b"adt-paper-trade-v1\x00" + canonical_json_bytes(payload)).hexdigest()


def _execution_key(execution: PaperTradeExecution) -> tuple[datetime, int, int, str]:
    return (
        execution.event_time,
        execution.candle_index,
        execution.order_sequence,
        execution.fill_id,
    )


def _validate_execution_order(executions: tuple[PaperTradeExecution, ...]) -> None:
    if tuple(sorted(executions, key=_execution_key)) != executions:
        raise ValueError("executions must be in canonical chronological order")
    if len({item.fill_id for item in executions}) != len(executions):
        raise ValueError("execution fill ids must be unique")
    if len({item.order_id for item in executions}) != len(executions):
        raise ValueError("execution order ids must be unique")


def _optional_utc(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    return require_utc(value, field_name=field_name)


def _safe_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be one safe identifier")
    return value


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be one lowercase SHA-256 digest")
    return value


def _finite(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be one finite Decimal")
    return value


def _positive(value: object, field_name: str) -> Decimal:
    decimal_value = _finite(value, field_name)
    if decimal_value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return decimal_value


def _nonnegative(value: object, field_name: str) -> Decimal:
    decimal_value = _finite(value, field_name)
    if decimal_value < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return decimal_value
