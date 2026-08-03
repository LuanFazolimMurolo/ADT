"""Deterministic candle-by-candle engine with strict no-future strategy views."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import ParamSpec, Protocol, TypeVar, cast

from app.backtesting.domain import (
    BacktestConfig,
    EquityPoint,
    Fill,
    FillLiquidity,
    FillReason,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    SimulatedOrder,
    TimeInForce,
    evaluation_range_for,
    strategy_lifecycle_version_for,
)
from app.backtesting.errors import (
    MaximumCandlesExceededError,
    MaximumEventsExceededError,
    SnapshotChangedError,
    SnapshotInvalidError,
    StrategyFailureError,
    UnsupportedBacktestMarketError,
)
from app.backtesting.execution import (
    DeterministicExecutionModel,
    create_order,
    order_priority_key,
    transition_order,
)
from app.backtesting.ledger import BacktestLedger, LedgerEntry
from app.backtesting.portfolio import (
    PortfolioState,
    apply_fill,
    initialize_portfolio,
    mark_to_market,
)
from app.backtesting.risk import DeterministicRiskManager
from app.backtesting.strategy import BacktestStrategy, StrategyContext
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import Candle, DataRange, MarketType

_P = ParamSpec("_P")
_R = TypeVar("_R")


class BacktestSnapshotReader(Protocol):
    """Minimal immutable snapshot boundary consumed by the engine."""

    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot: ...

    def iter_candles(self, data_range: DataRange | None = None) -> Iterator[Candle]: ...

    def verify_unchanged(self) -> DatasetSnapshot: ...


class LocalMarketSnapshotReader:
    """Production adapter around the Phase 2C ``MarketDatasetReader``."""

    def __init__(self, data_dir: Path) -> None:
        from app.market_data.snapshots import MarketDatasetReader

        self._reader = MarketDatasetReader(data_dir)

    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        return self._reader.open_snapshot(snapshot_id)

    def iter_candles(self, data_range: DataRange | None = None) -> Iterator[Candle]:
        return self._reader.iter_candles(data_range)

    def verify_unchanged(self) -> DatasetSnapshot:
        return self._reader.verify_unchanged()


@dataclass(frozen=True, slots=True)
class BacktestExecutionResult:
    """Complete in-memory result before Phase 3A artifact publication."""

    snapshot: DatasetSnapshot
    candles_processed: int
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[Fill, ...]
    ledger: tuple[LedgerEntry, ...]
    equity_curve: tuple[EquityPoint, ...]
    final_portfolio: PortfolioSnapshot
    risk_halt: bool


class DeterministicBacktestEngine:
    """Run one Spot long-only strategy over an immutable snapshot lazily."""

    def __init__(self, reader: BacktestSnapshotReader) -> None:
        self._reader = reader

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> DeterministicBacktestEngine:
        return cls(LocalMarketSnapshotReader(data_dir))

    def run(
        self,
        config: BacktestConfig,
        strategy: BacktestStrategy,
        *,
        cancel_open_orders_at_end: bool = True,
    ) -> BacktestExecutionResult:
        if type(cancel_open_orders_at_end) is not bool:
            raise StrategyFailureError("A política terminal de ordens é inválida.")
        if strategy.descriptor != config.strategy:
            raise StrategyFailureError("A identidade da estratégia diverge da configuração.")
        try:
            evaluation_range = evaluation_range_for(config)
            lifecycle_version = strategy_lifecycle_version_for(config)
        except ValueError as error:
            raise StrategyFailureError("A configuração de lifecycle é inválida.") from error
        has_warmup = evaluation_range.start > config.data_range.start
        warmup_callback: Callable[[StrategyContext, Candle], None] | None = None
        if lifecycle_version == 1:
            if has_warmup:
                raise StrategyFailureError("Lifecycle 1 não permite candles de warmup.")
        else:
            try:
                candidate = getattr(strategy, "on_warmup_candle")
            except (AttributeError, TypeError) as exc:
                raise StrategyFailureError(
                    "A estratégia não suporta o lifecycle de warmup exigido."
                ) from exc
            if not callable(candidate):
                raise StrategyFailureError("A estratégia não fornece callback de warmup chamável.")
            warmup_callback = cast(Callable[[StrategyContext, Candle], None], candidate)

        snapshot = self._open_snapshot(config.snapshot_id)
        if (
            config.data_range.start < snapshot.data_range.start
            or config.data_range.end > snapshot.data_range.end
        ):
            raise SnapshotInvalidError("O intervalo do backtest excede a cobertura do snapshot.")

        execution = DeterministicExecutionModel(
            fees=config.execution.fees,
            slippage=config.execution.slippage,
        )
        risk = DeterministicRiskManager(
            constraints=config.constraints,
            limits=config.risk_limits,
            fees=config.execution.fees,
            slippage=config.execution.slippage,
        )
        portfolio = initialize_portfolio(config.initial_capital)
        ledger = BacktestLedger()
        ledger.record_initial_capital(config.initial_capital, evaluation_range.start)
        orders: list[SimulatedOrder] = []
        fills: list[Fill] = []
        equity: list[EquityPoint] = []
        history: deque[Candle] = deque(maxlen=config.history_window)
        risk_halt = False
        last_fill: Fill | None = None
        last_candle: Candle | None = None
        previous_open_time: datetime | None = None
        context_candles_seen = 0
        candles_processed = 0
        start_context = self._context(
            snapshot=snapshot,
            candle_index=-1,
            current_candle=None,
            history=(),
            portfolio=portfolio,
            orders=orders,
            last_fill=None,
            risk_halt=False,
        )
        pending_start_intents = self._strategy_call(strategy.on_start, start_context)

        for candle in self._reader.iter_candles(config.data_range):
            if candle.open_time < config.data_range.start:
                continue
            if candle.open_time >= config.data_range.end:
                break
            if context_candles_seen >= config.max_candles:
                raise MaximumCandlesExceededError()
            self._validate_candle(candle, previous_open_time, last_candle)
            context_candles_seen += 1
            previous_open_time = candle.open_time
            last_candle = candle
            if candle.open_time < evaluation_range.start:
                history.append(candle)
                warmup_context = self._context(
                    snapshot=snapshot,
                    candle_index=context_candles_seen - 1,
                    current_candle=candle,
                    history=tuple(history),
                    portfolio=portfolio,
                    orders=orders,
                    last_fill=None,
                    risk_halt=False,
                )
                warmup_result = self._strategy_call(
                    cast(Callable[[StrategyContext, Candle], None], warmup_callback),
                    warmup_context,
                    candle,
                )
                if warmup_result is not None:
                    raise StrategyFailureError("on_warmup_candle não pode produzir intents")
                continue

            candle_index = candles_processed
            previous_history = tuple(history)

            if candle_index == 0 and pending_start_intents:
                self._submit_intents(
                    pending_start_intents,
                    created_candle_index=-1,
                    event_time=candle.open_time,
                    reference_price=candle.open,
                    portfolio=portfolio,
                    orders=orders,
                    config=config,
                    risk=risk,
                    risk_halt=risk_halt,
                )
                self._check_event_limit(config, orders, fills, ledger, equity)

            for order_position in self._eligible_order_positions(orders, candle_index):
                order = orders[order_position]
                if self._day_expired(order, candle):
                    orders[order_position] = transition_order(
                        order,
                        OrderStatus.EXPIRED,
                        event_time=candle.open_time,
                    )
                    continue
                quote = execution.quote(order, candle, candle_index=candle_index)
                if quote is None:
                    if (
                        order.intent.time_in_force is TimeInForce.IOC
                        and candle_index >= order.eligible_candle_index
                    ):
                        orders[order_position] = transition_order(
                            order,
                            OrderStatus.EXPIRED,
                            event_time=candle.close_time,
                        )
                    continue
                fill = self._make_fill(
                    order,
                    quote_base=quote.base_price,
                    execution_price=quote.execution_price,
                    reason=quote.reason,
                    liquidity=quote.liquidity,
                    slippage_per_unit=quote.slippage_cost_per_unit,
                    event_time=candle.open_time,
                    candle_index=candle_index,
                    fill_sequence=len(fills) + 1,
                    execution=execution,
                )
                fill_rejection = risk.validate_fill(fill, portfolio.snapshot())
                if fill_rejection is not None:
                    orders[order_position] = transition_order(
                        order,
                        OrderStatus.CANCELLED,
                        event_time=candle.open_time,
                    )
                    continue
                mutation = apply_fill(portfolio, fill)
                portfolio = mutation.after
                ledger.record_fill(fill, mutation)
                fills.append(fill)
                last_fill = fill
                orders[order_position] = transition_order(
                    order,
                    OrderStatus.FILLED,
                    event_time=candle.open_time,
                )
                fill_context = self._context_from_previous_history(
                    snapshot=snapshot,
                    previous_history=previous_history,
                    portfolio=portfolio,
                    orders=orders,
                    last_fill=fill,
                    risk_halt=risk_halt,
                )
                fill_intents = self._strategy_call(strategy.on_fill, fill_context, fill)
                self._submit_intents(
                    fill_intents,
                    created_candle_index=candle_index,
                    event_time=candle.open_time,
                    reference_price=fill.execution_price,
                    portfolio=portfolio,
                    orders=orders,
                    config=config,
                    risk=risk,
                    risk_halt=risk_halt,
                )
                self._check_event_limit(config, orders, fills, ledger, equity)

            portfolio = mark_to_market(portfolio, candle.close)
            ledger.record_mark(
                portfolio,
                event_time=candle.close_time,
                candle_index=candle_index,
            )
            equity.append(self._equity_point(candle_index, candle, portfolio))
            if not risk_halt and risk.drawdown_halt_required(portfolio.snapshot()):
                risk_halt = True
                self._cancel_open_orders(orders, candle.close_time)

            history.append(candle)
            candle_context = self._context(
                snapshot=snapshot,
                candle_index=candle_index,
                current_candle=candle,
                history=history,
                portfolio=portfolio,
                orders=orders,
                last_fill=last_fill,
                risk_halt=risk_halt,
            )
            candle_intents = self._strategy_call(strategy.on_candle, candle_context, candle)
            self._submit_intents(
                candle_intents,
                created_candle_index=candle_index,
                event_time=candle.close_time,
                reference_price=candle.close,
                portfolio=portfolio,
                orders=orders,
                config=config,
                risk=risk,
                risk_halt=risk_halt,
            )
            self._check_event_limit(config, orders, fills, ledger, equity)
            candles_processed += 1

        if candles_processed == 0 or last_candle is None:
            raise SnapshotInvalidError("O intervalo do backtest não contém candles.")

        if cancel_open_orders_at_end:
            self._cancel_open_orders(orders, last_candle.close_time)
        if config.execution.force_close_at_end and portfolio.base_quantity > 0:
            if len(orders) >= min(config.max_orders, config.risk_limits.max_total_orders):
                raise MaximumEventsExceededError(
                    "O fechamento forçado excederia o limite seguro de ordens."
                )
            portfolio, force_order, force_fill = self._force_close(
                portfolio=portfolio,
                candle=last_candle,
                candle_index=candles_processed - 1,
                order_sequence=len(orders) + 1,
                fill_sequence=len(fills) + 1,
                execution=execution,
                ledger=ledger,
            )
            orders.append(force_order)
            fills.append(force_fill)
            last_fill = force_fill
            equity[-1] = self._equity_point(candles_processed - 1, last_candle, portfolio)

        ledger.record_mark(
            portfolio,
            event_time=last_candle.close_time,
            candle_index=candles_processed - 1,
            final=True,
        )
        end_context = self._context(
            snapshot=snapshot,
            candle_index=candles_processed - 1,
            current_candle=last_candle,
            history=history,
            portfolio=portfolio,
            orders=orders,
            last_fill=last_fill,
            risk_halt=risk_halt,
        )
        self._strategy_call(strategy.on_end, end_context)
        self._check_event_limit(config, orders, fills, ledger, equity)
        verified = self._verify_snapshot()
        if verified != snapshot:
            raise SnapshotChangedError()
        return BacktestExecutionResult(
            snapshot=snapshot,
            candles_processed=candles_processed,
            orders=tuple(orders),
            fills=tuple(fills),
            ledger=ledger.entries,
            equity_curve=tuple(equity),
            final_portfolio=portfolio.snapshot(),
            risk_halt=risk_halt,
        )

    @staticmethod
    def _validate_candle(
        candle: Candle,
        previous_open_time: datetime | None,
        previous_candle: Candle | None,
    ) -> None:
        if candle.market_type is not MarketType.SPOT:
            raise UnsupportedBacktestMarketError()
        if not candle.is_closed:
            raise SnapshotInvalidError("O snapshot contém candle aberto.")
        if previous_open_time is not None and candle.open_time <= previous_open_time:
            raise SnapshotInvalidError("Os candles não estão em ordem estrita.")
        if previous_candle is not None:
            current_identity = (
                candle.exchange,
                candle.market_type,
                candle.symbol,
                candle.timeframe.code,
            )
            previous_identity = (
                previous_candle.exchange,
                previous_candle.market_type,
                previous_candle.symbol,
                previous_candle.timeframe.code,
            )
            if current_identity != previous_identity:
                raise SnapshotInvalidError("O snapshot mistura instrumentos ou timeframes.")

    @staticmethod
    def _eligible_order_positions(
        orders: list[SimulatedOrder],
        candle_index: int,
    ) -> tuple[int, ...]:
        candidates = (
            (position, order)
            for position, order in enumerate(orders)
            if order.status is OrderStatus.OPEN and order.eligible_candle_index <= candle_index
        )
        return tuple(
            position
            for position, _order in sorted(candidates, key=lambda item: order_priority_key(item[1]))
        )

    @staticmethod
    def _day_expired(order: SimulatedOrder, candle: Candle) -> bool:
        return bool(
            order.intent.time_in_force is TimeInForce.DAY
            and candle.open_time.date() > order.created_at.date()
        )

    def _submit_intents(
        self,
        intents: tuple[OrderIntent, ...],
        *,
        created_candle_index: int,
        event_time: datetime,
        reference_price: Decimal,
        portfolio: PortfolioState,
        orders: list[SimulatedOrder],
        config: BacktestConfig,
        risk: DeterministicRiskManager,
        risk_halt: bool,
    ) -> None:
        if not isinstance(intents, tuple) or any(
            not isinstance(intent, OrderIntent) for intent in intents
        ):
            raise StrategyFailureError("A estratégia retornou intenções inválidas.")
        for intent in intents:
            if len(orders) >= config.max_orders:
                raise MaximumEventsExceededError("O backtest excedeu o limite seguro de ordens.")
            sequence = len(orders) + 1
            decision = risk.evaluate_order(
                intent,
                portfolio.snapshot(),
                reference_price=reference_price,
                open_order_count=sum(order.status is OrderStatus.OPEN for order in orders),
                total_order_count=len(orders),
                risk_halt=risk_halt,
            )
            effective_intent = decision.normalized_intent or intent
            order = create_order(
                effective_intent,
                sequence=sequence,
                created_at=event_time,
                created_candle_index=created_candle_index,
            )
            if decision.accepted:
                order = transition_order(order, OrderStatus.OPEN, event_time=event_time)
            else:
                assert decision.rejection_code is not None
                order = transition_order(
                    order,
                    OrderStatus.REJECTED,
                    event_time=event_time,
                    rejection_code=decision.rejection_code.value,
                )
            orders.append(order)

    @staticmethod
    def _make_fill(
        order: SimulatedOrder,
        *,
        quote_base: Decimal,
        execution_price: Decimal,
        reason: FillReason,
        liquidity: FillLiquidity,
        slippage_per_unit: Decimal,
        event_time: datetime,
        candle_index: int,
        fill_sequence: int,
        execution: DeterministicExecutionModel,
    ) -> Fill:
        quantity = order.intent.quantity
        notional = quantity * execution_price
        return Fill(
            fill_id=f"F{fill_sequence:012d}",
            order_id=order.order_id,
            reason=reason,
            liquidity=liquidity,
            side=order.intent.side,
            quantity=quantity,
            base_price=quote_base,
            execution_price=execution_price,
            notional=notional,
            fee=execution.fee(notional, liquidity),
            slippage_cost=slippage_per_unit * quantity,
            event_time=event_time,
            candle_index=candle_index,
        )

    def _force_close(
        self,
        *,
        portfolio: PortfolioState,
        candle: Candle,
        candle_index: int,
        order_sequence: int,
        fill_sequence: int,
        execution: DeterministicExecutionModel,
        ledger: BacktestLedger,
    ) -> tuple[PortfolioState, SimulatedOrder, Fill]:
        created_index = candle_index - 1
        order = create_order(
            OrderIntent(
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=portfolio.base_quantity,
                time_in_force=TimeInForce.IOC,
                client_tag="force-close",
            ),
            sequence=order_sequence,
            created_at=candle.open_time,
            created_candle_index=created_index,
        )
        order = transition_order(order, OrderStatus.OPEN, event_time=candle.open_time)
        quote = execution.force_close_quote(candle.close, OrderSide.SELL)
        fill = self._make_fill(
            order,
            quote_base=quote.base_price,
            execution_price=quote.execution_price,
            reason=quote.reason,
            liquidity=quote.liquidity,
            slippage_per_unit=quote.slippage_cost_per_unit,
            event_time=candle.close_time,
            candle_index=candle_index,
            fill_sequence=fill_sequence,
            execution=execution,
        )
        mutation = apply_fill(portfolio, fill)
        ledger.record_fill(fill, mutation)
        closed = transition_order(order, OrderStatus.FILLED, event_time=candle.close_time)
        return mark_to_market(mutation.after, candle.close), closed, fill

    @staticmethod
    def _cancel_open_orders(orders: list[SimulatedOrder], event_time: datetime) -> None:
        for position, order in enumerate(orders):
            if order.status is OrderStatus.OPEN:
                orders[position] = transition_order(
                    order,
                    OrderStatus.CANCELLED,
                    event_time=event_time,
                )

    @staticmethod
    def _equity_point(
        candle_index: int,
        candle: Candle,
        portfolio: PortfolioState,
    ) -> EquityPoint:
        return EquityPoint(
            candle_index=candle_index,
            event_time=candle.close_time,
            close_price=candle.close,
            quote_cash=portfolio.quote_cash,
            base_quantity=portfolio.base_quantity,
            equity=portfolio.equity,
            peak_equity=portfolio.peak_equity,
            drawdown=portfolio.drawdown,
            drawdown_pct=portfolio.drawdown_pct,
        )

    @staticmethod
    def _context(
        *,
        snapshot: DatasetSnapshot,
        candle_index: int,
        current_candle: Candle | None,
        history: Iterable[Candle],
        portfolio: PortfolioState,
        orders: list[SimulatedOrder],
        last_fill: Fill | None,
        risk_halt: bool,
    ) -> StrategyContext:
        return StrategyContext(
            snapshot=snapshot,
            candle_index=candle_index,
            current_candle=current_candle,
            history=tuple(history),
            portfolio=portfolio.snapshot(),
            open_orders=tuple(
                sorted(
                    (order for order in orders if order.status is OrderStatus.OPEN),
                    key=order_priority_key,
                )
            ),
            last_fill=last_fill,
            risk_halt=risk_halt,
        )

    def _context_from_previous_history(
        self,
        *,
        snapshot: DatasetSnapshot,
        previous_history: tuple[Candle, ...],
        portfolio: PortfolioState,
        orders: list[SimulatedOrder],
        last_fill: Fill,
        risk_halt: bool,
    ) -> StrategyContext:
        if previous_history:
            return self._context(
                snapshot=snapshot,
                candle_index=max(0, last_fill.candle_index - 1),
                current_candle=previous_history[-1],
                history=previous_history,
                portfolio=portfolio,
                orders=orders,
                last_fill=last_fill,
                risk_halt=risk_halt,
            )
        return self._context(
            snapshot=snapshot,
            candle_index=-1,
            current_candle=None,
            history=(),
            portfolio=portfolio,
            orders=orders,
            last_fill=last_fill,
            risk_halt=risk_halt,
        )

    @staticmethod
    def _check_event_limit(
        config: BacktestConfig,
        orders: list[SimulatedOrder],
        fills: list[Fill],
        ledger: BacktestLedger,
        equity: list[EquityPoint],
    ) -> None:
        event_count = len(orders) + len(fills) + len(ledger.entries) + len(equity)
        if event_count > config.max_events:
            raise MaximumEventsExceededError()

    @staticmethod
    def _strategy_call(
        function: Callable[_P, _R],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _R:
        try:
            return function(*args, **kwargs)
        except StrategyFailureError:
            raise
        except Exception:
            raise StrategyFailureError() from None

    def _open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        try:
            snapshot = self._reader.open_snapshot(snapshot_id)
        except Exception:
            raise SnapshotInvalidError() from None
        if snapshot.snapshot_id != snapshot_id:
            raise SnapshotInvalidError("O snapshot aberto possui identidade divergente.")
        return snapshot

    def _verify_snapshot(self) -> DatasetSnapshot:
        try:
            return self._reader.verify_unchanged()
        except Exception:
            raise SnapshotChangedError() from None
