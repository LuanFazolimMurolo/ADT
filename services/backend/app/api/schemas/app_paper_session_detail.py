"""Minimal authorized paper-session detail, annotation, and trade contracts."""

from __future__ import annotations

from datetime import datetime

from app.api.schemas.common import ApiSchema
from app.api.schemas.paper_trading import PaperDecimal
from app.backtesting.domain import OrderSide, OrderStatus
from app.paper_trading.chart_annotations import (
    PaperChartAnnotationPage,
    PaperChartFillAnnotation,
    PaperChartFillRole,
    PaperChartOrderAnnotation,
)
from app.paper_trading.journal import PaperTrade, PaperTradeStatus
from app.paper_trading.journal_query import PaperTradePage, PaperTradeQueryTotals
from app.paper_trading.query import PaperSessionView


class AppPaperSessionDetailResponse(ApiSchema):
    session_id: str
    base_asset: str
    quote_asset: str
    timeframe: str
    strategy_name: str
    strategy_version: str
    state_available: bool
    last_candle_open_time: datetime | None

    @classmethod
    def from_domain(cls, value: PaperSessionView) -> AppPaperSessionDetailResponse:
        PaperSessionView.__post_init__(value)
        return cls(
            session_id=value.session_id,
            base_asset=value.config.pair.base,
            quote_asset=value.config.pair.quote,
            timeframe=value.config.timeframe.code,
            strategy_name=value.config.strategy.name,
            strategy_version=value.config.strategy.version,
            state_available=value.state is not None,
            last_candle_open_time=(
                None if value.state is None else value.state.last_candle_open_time
            ),
        )


class AppPaperChartOrderAnnotationResponse(ApiSchema):
    order_id: str
    created_at: datetime
    status: OrderStatus
    side: OrderSide
    quantity: PaperDecimal
    stop_price: PaperDecimal | None
    is_engine_protective_stop: bool

    @classmethod
    def from_domain(
        cls,
        value: PaperChartOrderAnnotation,
    ) -> AppPaperChartOrderAnnotationResponse:
        return cls(
            order_id=value.order_id,
            created_at=value.created_at,
            status=value.status,
            side=value.side,
            quantity=value.quantity,
            stop_price=value.stop_price,
            is_engine_protective_stop=value.is_engine_protective_stop,
        )


class AppPaperChartFillAnnotationResponse(ApiSchema):
    fill_id: str
    trade_id: str
    trade_sequence: int
    role: PaperChartFillRole
    event_time: datetime
    side: OrderSide
    quantity: PaperDecimal
    execution_price: PaperDecimal
    fee: PaperDecimal
    slippage_cost: PaperDecimal
    is_engine_protective_stop: bool

    @classmethod
    def from_domain(
        cls,
        value: PaperChartFillAnnotation,
    ) -> AppPaperChartFillAnnotationResponse:
        return cls(
            fill_id=value.fill_id,
            trade_id=value.trade_id,
            trade_sequence=value.trade_sequence,
            role=value.role,
            event_time=value.event_time,
            side=value.side,
            quantity=value.quantity,
            execution_price=value.execution_price,
            fee=value.fee,
            slippage_cost=value.slippage_cost,
            is_engine_protective_stop=value.is_engine_protective_stop,
        )


class AppPaperChartAnnotationPageResponse(ApiSchema):
    session_id: str
    base_asset: str
    quote_asset: str
    timeframe: str
    state_available: bool
    dataset_version: str | None
    range_start: datetime
    range_end: datetime
    count: int
    orders_count: int
    fills_count: int
    orders: list[AppPaperChartOrderAnnotationResponse]
    fills: list[AppPaperChartFillAnnotationResponse]
    last_candle_open_time: datetime | None
    content_checksum: str

    @classmethod
    def from_domain(
        cls,
        value: PaperChartAnnotationPage,
    ) -> AppPaperChartAnnotationPageResponse:
        PaperChartAnnotationPage.__post_init__(value)
        return cls(
            session_id=value.session_id,
            base_asset=value.pair.base,
            quote_asset=value.pair.quote,
            timeframe=value.timeframe.code,
            state_available=value.state_available,
            dataset_version=value.dataset_version,
            range_start=value.range_start,
            range_end=value.range_end,
            count=value.count,
            orders_count=len(value.orders),
            fills_count=len(value.fills),
            orders=[
                AppPaperChartOrderAnnotationResponse.from_domain(item) for item in value.orders
            ],
            fills=[AppPaperChartFillAnnotationResponse.from_domain(item) for item in value.fills],
            last_candle_open_time=value.last_candle_open_time,
            content_checksum=value.content_checksum,
        )


class AppPaperTradeResponse(ApiSchema):
    trade_id: str
    sequence: int
    status: PaperTradeStatus
    opened_at: datetime
    closed_at: datetime | None
    opened_quantity: PaperDecimal
    closed_quantity: PaperDecimal
    remaining_quantity: PaperDecimal
    average_entry_price: PaperDecimal
    average_exit_price: PaperDecimal | None
    realized_pnl: PaperDecimal
    unrealized_pnl: PaperDecimal
    net_pnl: PaperDecimal
    total_fees: PaperDecimal
    total_slippage_cost: PaperDecimal
    mark_price: PaperDecimal | None

    @classmethod
    def from_domain(cls, value: PaperTrade) -> AppPaperTradeResponse:
        PaperTrade.__post_init__(value)
        return cls(
            trade_id=value.trade_id,
            sequence=value.sequence,
            status=value.status,
            opened_at=value.opened_at,
            closed_at=value.closed_at,
            opened_quantity=value.opened_quantity,
            closed_quantity=value.closed_quantity,
            remaining_quantity=value.remaining_quantity,
            average_entry_price=value.average_entry_price,
            average_exit_price=value.average_exit_price,
            realized_pnl=value.realized_pnl,
            unrealized_pnl=value.unrealized_pnl,
            net_pnl=value.net_pnl,
            total_fees=value.total_fees,
            total_slippage_cost=value.total_slippage_cost,
            mark_price=value.mark_price,
        )


class AppPaperTradeTotalsResponse(ApiSchema):
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
    ) -> AppPaperTradeTotalsResponse:
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


class AppPaperTradePageResponse(ApiSchema):
    items: list[AppPaperTradeResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
    totals: AppPaperTradeTotalsResponse

    @classmethod
    def from_domain(cls, value: PaperTradePage) -> AppPaperTradePageResponse:
        PaperTradePage.__post_init__(value)
        return cls(
            items=[AppPaperTradeResponse.from_domain(item.trade) for item in value.items],
            page=value.page,
            page_size=value.page_size,
            total=value.total,
            total_pages=value.total_pages,
            totals=AppPaperTradeTotalsResponse.from_domain(value.totals),
        )
