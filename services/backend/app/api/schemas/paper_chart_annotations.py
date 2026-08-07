"""Administrator-only verified paper chart annotation schemas."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from app.api.schemas.common import ApiSchema, JsonValue
from app.api.schemas.paper_trading import PaperDecimal
from app.backtesting.domain import (
    FillLiquidity,
    FillReason,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from app.backtesting.serialization import canonical_value
from app.paper_trading.chart_annotations import (
    PaperChartAnnotationPage,
    PaperChartFillAnnotation,
    PaperChartFillRole,
    PaperChartOrderAnnotation,
)


class PaperChartOrderAnnotationResponse(ApiSchema):
    order_id: str
    created_sequence: int
    created_at: datetime
    opened_at: datetime | None
    terminal_at: datetime | None
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    status: OrderStatus
    quantity: PaperDecimal
    limit_price: PaperDecimal | None
    stop_price: PaperDecimal | None
    client_tag: str | None
    rejection_code: str | None
    is_engine_protective_stop: bool

    @classmethod
    def from_domain(
        cls,
        value: PaperChartOrderAnnotation,
    ) -> PaperChartOrderAnnotationResponse:
        return cls(
            order_id=value.order_id,
            created_sequence=value.created_sequence,
            created_at=value.created_at,
            opened_at=value.opened_at,
            terminal_at=value.terminal_at,
            side=value.side,
            order_type=value.order_type,
            time_in_force=value.time_in_force,
            status=value.status,
            quantity=value.quantity,
            limit_price=value.limit_price,
            stop_price=value.stop_price,
            client_tag=value.client_tag,
            rejection_code=value.rejection_code,
            is_engine_protective_stop=value.is_engine_protective_stop,
        )


class PaperChartFillAnnotationResponse(ApiSchema):
    fill_id: str
    order_id: str
    trade_id: str
    trade_sequence: int
    role: PaperChartFillRole
    event_time: datetime
    candle_index: int
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
    is_engine_protective_stop: bool

    @classmethod
    def from_domain(
        cls,
        value: PaperChartFillAnnotation,
    ) -> PaperChartFillAnnotationResponse:
        return cls(
            fill_id=value.fill_id,
            order_id=value.order_id,
            trade_id=value.trade_id,
            trade_sequence=value.trade_sequence,
            role=value.role,
            event_time=value.event_time,
            candle_index=value.candle_index,
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
            is_engine_protective_stop=value.is_engine_protective_stop,
        )


class PaperChartAnnotationPageResponse(ApiSchema):
    schema_version: int
    session_id: str
    config_checksum: str
    state_available: bool
    state_id: str | None
    state_checksum: str | None
    dataset_version: str | None
    source_checksum: str | None
    symbol: str
    base_asset: str
    quote_asset: str
    timeframe: str
    strategy_name: str
    strategy_version: str
    strategy_parameters: JsonValue
    ema_fast_period: int | None
    ema_slow_period: int | None
    range_start: datetime
    range_end: datetime
    limit: int
    count: int
    orders_count: int
    fills_count: int
    orders: list[PaperChartOrderAnnotationResponse]
    fills: list[PaperChartFillAnnotationResponse]
    last_candle_open_time: datetime | None
    replayed_at: datetime | None
    content_checksum: str

    @classmethod
    def from_domain(
        cls,
        value: PaperChartAnnotationPage,
    ) -> PaperChartAnnotationPageResponse:
        PaperChartAnnotationPage.__post_init__(value)
        return cls(
            schema_version=value.schema_version,
            session_id=value.session_id,
            config_checksum=value.config_checksum,
            state_available=value.state_available,
            state_id=value.state_id,
            state_checksum=value.state_checksum,
            dataset_version=value.dataset_version,
            source_checksum=value.source_checksum,
            symbol=value.pair.symbol,
            base_asset=value.pair.base,
            quote_asset=value.pair.quote,
            timeframe=value.timeframe.code,
            strategy_name=value.strategy_name,
            strategy_version=value.strategy_version,
            strategy_parameters=cast(
                JsonValue,
                canonical_value(value.strategy_parameters),
            ),
            ema_fast_period=value.ema_fast_period,
            ema_slow_period=value.ema_slow_period,
            range_start=value.range_start,
            range_end=value.range_end,
            limit=value.limit,
            count=value.count,
            orders_count=len(value.orders),
            fills_count=len(value.fills),
            orders=[PaperChartOrderAnnotationResponse.from_domain(item) for item in value.orders],
            fills=[PaperChartFillAnnotationResponse.from_domain(item) for item in value.fills],
            last_candle_open_time=value.last_candle_open_time,
            replayed_at=value.replayed_at,
            content_checksum=value.content_checksum,
        )
