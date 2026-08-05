"""Authenticated read-only HTTP contracts for the deterministic trade journal."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from app.api.schemas.common import ApiSchema, JsonValue
from app.api.schemas.paper_trading import PaperDecimal
from app.backtesting.domain import (
    FillLiquidity,
    FillReason,
    OrderSide,
    OrderType,
    TimeInForce,
)
from app.backtesting.serialization import canonical_value
from app.paper_trading.journal import (
    PaperTrade,
    PaperTradeExecution,
    PaperTradeStatus,
)
from app.paper_trading.journal_query import (
    PaperTradeJournalFilter,
    PaperTradePage,
    PaperTradeQueryTotals,
    PaperTradeRecord,
)


class PaperTradeJournalFilterResponse(ApiSchema):
    session_id: str | None
    base_asset: str | None
    quote_asset: str | None
    timeframe: str | None
    strategy_name: str | None
    strategy_version: str | None
    status: PaperTradeStatus | None
    opened_from: datetime | None
    opened_before: datetime | None
    closed_from: datetime | None
    closed_before: datetime | None

    @classmethod
    def from_domain(
        cls,
        value: PaperTradeJournalFilter,
    ) -> PaperTradeJournalFilterResponse:
        PaperTradeJournalFilter.__post_init__(value)
        return cls(
            session_id=value.session_id,
            base_asset=value.base_asset,
            quote_asset=value.quote_asset,
            timeframe=value.timeframe_code,
            strategy_name=value.strategy_name,
            strategy_version=value.strategy_version,
            status=value.status,
            opened_from=value.opened_from,
            opened_before=value.opened_before,
            closed_from=value.closed_from,
            closed_before=value.closed_before,
        )


class PaperTradeExecutionResponse(ApiSchema):
    fill_id: str
    order_id: str
    order_sequence: int
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    client_tag: str | None
    fill_reason: FillReason
    liquidity: FillLiquidity
    quantity: PaperDecimal
    base_price: PaperDecimal
    execution_price: PaperDecimal
    notional: PaperDecimal
    fee: PaperDecimal
    slippage_cost: PaperDecimal
    event_time: datetime
    candle_index: int

    @classmethod
    def from_domain(
        cls,
        value: PaperTradeExecution,
    ) -> PaperTradeExecutionResponse:
        PaperTradeExecution.__post_init__(value)
        return cls(
            fill_id=value.fill_id,
            order_id=value.order_id,
            order_sequence=value.order_sequence,
            side=value.side,
            order_type=value.order_type,
            time_in_force=value.time_in_force,
            client_tag=value.client_tag,
            fill_reason=value.fill_reason,
            liquidity=value.liquidity,
            quantity=value.quantity,
            base_price=value.base_price,
            execution_price=value.execution_price,
            notional=value.notional,
            fee=value.fee,
            slippage_cost=value.slippage_cost,
            event_time=value.event_time,
            candle_index=value.candle_index,
        )


class PaperTradeResponse(ApiSchema):
    trade_id: str
    session_id: str
    sequence: int
    status: PaperTradeStatus
    opened_at: datetime
    last_entry_at: datetime
    first_exit_at: datetime | None
    closed_at: datetime | None
    entry_executions: list[PaperTradeExecutionResponse]
    exit_executions: list[PaperTradeExecutionResponse]
    opened_quantity: PaperDecimal
    closed_quantity: PaperDecimal
    remaining_quantity: PaperDecimal
    entry_notional: PaperDecimal
    exit_notional: PaperDecimal
    entry_fees: PaperDecimal
    exit_fees: PaperDecimal
    total_fees: PaperDecimal
    entry_slippage_cost: PaperDecimal
    exit_slippage_cost: PaperDecimal
    total_slippage_cost: PaperDecimal
    entry_cost_basis: PaperDecimal
    released_cost_basis: PaperDecimal
    remaining_cost_basis: PaperDecimal
    average_entry_price: PaperDecimal
    average_exit_price: PaperDecimal | None
    realized_pnl: PaperDecimal
    unrealized_pnl: PaperDecimal
    net_pnl: PaperDecimal
    mark_price: PaperDecimal | None

    @classmethod
    def from_domain(cls, value: PaperTrade) -> PaperTradeResponse:
        PaperTrade.__post_init__(value)
        return cls(
            trade_id=value.trade_id,
            session_id=value.session_id,
            sequence=value.sequence,
            status=value.status,
            opened_at=value.opened_at,
            last_entry_at=value.last_entry_at,
            first_exit_at=value.first_exit_at,
            closed_at=value.closed_at,
            entry_executions=[
                PaperTradeExecutionResponse.from_domain(item) for item in value.entry_executions
            ],
            exit_executions=[
                PaperTradeExecutionResponse.from_domain(item) for item in value.exit_executions
            ],
            opened_quantity=value.opened_quantity,
            closed_quantity=value.closed_quantity,
            remaining_quantity=value.remaining_quantity,
            entry_notional=value.entry_notional,
            exit_notional=value.exit_notional,
            entry_fees=value.entry_fees,
            exit_fees=value.exit_fees,
            total_fees=value.total_fees,
            entry_slippage_cost=value.entry_slippage_cost,
            exit_slippage_cost=value.exit_slippage_cost,
            total_slippage_cost=value.total_slippage_cost,
            entry_cost_basis=value.entry_cost_basis,
            released_cost_basis=value.released_cost_basis,
            remaining_cost_basis=value.remaining_cost_basis,
            average_entry_price=value.average_entry_price,
            average_exit_price=value.average_exit_price,
            realized_pnl=value.realized_pnl,
            unrealized_pnl=value.unrealized_pnl,
            net_pnl=value.net_pnl,
            mark_price=value.mark_price,
        )


class PaperTradeJournalRecordResponse(ApiSchema):
    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    symbol: str
    base_asset: str
    quote_asset: str
    timeframe: str
    strategy_name: str
    strategy_version: str
    strategy_parameters: JsonValue
    last_candle_open_time: datetime
    replayed_at: datetime
    trade: PaperTradeResponse

    @classmethod
    def from_domain(
        cls,
        value: PaperTradeRecord,
    ) -> PaperTradeJournalRecordResponse:
        PaperTradeRecord.__post_init__(value)
        return cls(
            session_id=value.session_id,
            config_checksum=value.config_checksum,
            state_id=value.state_id,
            state_checksum=value.state_checksum,
            symbol=value.pair.symbol,
            base_asset=value.pair.base,
            quote_asset=value.pair.quote,
            timeframe=value.timeframe.code,
            strategy_name=value.strategy.name,
            strategy_version=value.strategy.version,
            strategy_parameters=cast(
                JsonValue,
                canonical_value(value.strategy.parameters),
            ),
            last_candle_open_time=value.last_candle_open_time,
            replayed_at=value.replayed_at,
            trade=PaperTradeResponse.from_domain(value.trade),
        )


class PaperTradeJournalTotalsResponse(ApiSchema):
    trades_count: int
    closed_trades_count: int
    open_trades_count: int
    total_realized_pnl: PaperDecimal
    total_unrealized_pnl: PaperDecimal
    total_net_pnl: PaperDecimal
    total_fees: PaperDecimal
    total_slippage_cost: PaperDecimal

    @classmethod
    def from_domain(
        cls,
        value: PaperTradeQueryTotals,
    ) -> PaperTradeJournalTotalsResponse:
        PaperTradeQueryTotals.__post_init__(value)
        return cls(
            trades_count=value.trades_count,
            closed_trades_count=value.closed_trades_count,
            open_trades_count=value.open_trades_count,
            total_realized_pnl=value.total_realized_pnl,
            total_unrealized_pnl=value.total_unrealized_pnl,
            total_net_pnl=value.total_net_pnl,
            total_fees=value.total_fees,
            total_slippage_cost=value.total_slippage_cost,
        )


class PaperTradeJournalPageResponse(ApiSchema):
    filters: PaperTradeJournalFilterResponse
    items: list[PaperTradeJournalRecordResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
    totals: PaperTradeJournalTotalsResponse

    @classmethod
    def from_domain(
        cls,
        value: PaperTradePage,
    ) -> PaperTradeJournalPageResponse:
        PaperTradePage.__post_init__(value)
        return cls(
            filters=PaperTradeJournalFilterResponse.from_domain(value.filters),
            items=[PaperTradeJournalRecordResponse.from_domain(item) for item in value.items],
            page=value.page,
            page_size=value.page_size,
            total=value.total,
            total_pages=value.total_pages,
            totals=PaperTradeJournalTotalsResponse.from_domain(value.totals),
        )
