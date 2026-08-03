"""Read-only HTTP contracts for continuous deterministic paper trading."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, cast

from pydantic import AfterValidator, PlainSerializer

from app.api.schemas.common import ApiSchema, JsonValue
from app.backtesting.domain import Fill, OrderIntent, SimulatedOrder
from app.paper_trading.continuous import (
    PaperRunnerSessionResult,
    PaperRunnerState,
    validate_paper_runner_session_result,
    validate_paper_runner_state,
)
from app.paper_trading.domain import paper_config_payload, paper_session_id, paper_state_summary
from app.paper_trading.query import (
    PaperFillPage,
    PaperOrderPage,
    PaperSessionPage,
    PaperSessionSummaryView,
    PaperSessionView,
)


def _finite_decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Paper-trading value must be finite.")
    return value


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


PaperDecimal = Annotated[
    Decimal,
    AfterValidator(_finite_decimal),
    PlainSerializer(_decimal_text, return_type=str, when_used="json"),
]


class PaperRunnerSessionResultResponse(ApiSchema):
    session_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    state_id: str | None
    candles_processed: int | None
    last_candle_open_time: datetime | None
    error_code: str | None

    @classmethod
    def from_domain(cls, value: PaperRunnerSessionResult) -> PaperRunnerSessionResultResponse:
        validate_paper_runner_session_result(value)
        return cls(
            session_id=value.session_id,
            status=value.status.value,
            started_at=value.started_at,
            finished_at=value.finished_at,
            state_id=value.state_id,
            candles_processed=value.candles_processed,
            last_candle_open_time=value.last_candle_open_time,
            error_code=value.error_code,
        )


class PaperRunnerStatusResponse(ApiSchema):
    schema_version: int
    cycle_index: int
    cycle_id: str
    status: str
    interval_seconds: int
    max_sessions: int
    started_at: datetime
    finished_at: datetime
    next_cycle_at: datetime
    checksum: str
    results: list[PaperRunnerSessionResultResponse]

    @classmethod
    def from_domain(cls, value: PaperRunnerState) -> PaperRunnerStatusResponse:
        validate_paper_runner_state(value)
        return cls(
            schema_version=value.schema_version,
            cycle_index=value.cycle_index,
            cycle_id=value.cycle_id,
            status=value.status.value,
            interval_seconds=value.policy.interval_seconds,
            max_sessions=value.policy.max_sessions,
            started_at=value.started_at,
            finished_at=value.finished_at,
            next_cycle_at=value.next_cycle_at,
            checksum=value.checksum,
            results=[PaperRunnerSessionResultResponse.from_domain(item) for item in value.results],
        )


class PaperPortfolioResponse(ApiSchema):
    quote_cash: PaperDecimal
    base_quantity: PaperDecimal
    average_entry_price: PaperDecimal
    realized_pnl: PaperDecimal
    unrealized_pnl: PaperDecimal
    total_fees: PaperDecimal
    total_slippage_cost: PaperDecimal
    equity: PaperDecimal
    peak_equity: PaperDecimal
    drawdown: PaperDecimal
    drawdown_pct: PaperDecimal
    cost_basis: PaperDecimal


class PaperSessionSummaryResponse(ApiSchema):
    session_id: str
    symbol: str
    timeframe: str
    start_at: datetime
    warmup_candles: int
    strategy_name: str
    strategy_version: str
    strategy_lifecycle_version: int
    initial_capital: PaperDecimal
    state_available: bool
    state_id: str | None
    evaluation_end: datetime | None
    last_candle_open_time: datetime | None
    candles_processed: int | None
    orders_count: int
    fills_count: int
    risk_halt: bool | None
    replayed_at: datetime | None
    portfolio: PaperPortfolioResponse | None

    @classmethod
    def from_domain(
        cls,
        view: PaperSessionView | PaperSessionSummaryView,
    ) -> PaperSessionSummaryResponse:
        if isinstance(view, PaperSessionView):
            PaperSessionView.__post_init__(view)
            config = view.config
            summary = None if view.state is None else paper_state_summary(view.state)
        else:
            PaperSessionSummaryView.__post_init__(view)
            config = view.config
            summary = view.summary
        portfolio = None
        if summary is not None:
            portfolio = PaperPortfolioResponse(
                quote_cash=summary.portfolio.quote_cash,
                base_quantity=summary.portfolio.base_quantity,
                average_entry_price=summary.portfolio.average_entry_price,
                realized_pnl=summary.portfolio.realized_pnl,
                unrealized_pnl=summary.portfolio.unrealized_pnl,
                total_fees=summary.portfolio.total_fees,
                total_slippage_cost=summary.portfolio.total_slippage_cost,
                equity=summary.portfolio.equity,
                peak_equity=summary.portfolio.peak_equity,
                drawdown=summary.portfolio.drawdown,
                drawdown_pct=summary.portfolio.drawdown_pct,
                cost_basis=summary.portfolio.cost_basis,
            )
        return cls(
            session_id=paper_session_id(config),
            symbol=config.pair.symbol,
            timeframe=config.timeframe.code,
            start_at=config.start_at,
            warmup_candles=config.warmup_candles,
            strategy_name=config.strategy.name,
            strategy_version=config.strategy.version,
            strategy_lifecycle_version=config.strategy_lifecycle_version,
            initial_capital=config.initial_capital,
            state_available=summary is not None,
            state_id=None if summary is None else summary.state_id,
            evaluation_end=None if summary is None else summary.evaluation_end,
            last_candle_open_time=(None if summary is None else summary.last_candle_open_time),
            candles_processed=None if summary is None else summary.candles_processed,
            orders_count=0 if summary is None else summary.orders_count,
            fills_count=0 if summary is None else summary.fills_count,
            risk_halt=None if summary is None else summary.risk_halt,
            replayed_at=None if summary is None else summary.replayed_at,
            portfolio=portfolio,
        )


class PaperSessionListResponse(ApiSchema):
    items: list[PaperSessionSummaryResponse]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_domain(cls, value: PaperSessionPage) -> PaperSessionListResponse:
        PaperSessionPage.__post_init__(value)
        return cls(
            items=[PaperSessionSummaryResponse.from_domain(item) for item in value.items],
            page=value.page,
            page_size=value.page_size,
            total=value.total,
            total_pages=value.total_pages,
        )


class PaperSessionDetailResponse(ApiSchema):
    summary: PaperSessionSummaryResponse
    config: JsonValue

    @classmethod
    def from_domain(cls, value: PaperSessionView) -> PaperSessionDetailResponse:
        return cls(
            summary=PaperSessionSummaryResponse.from_domain(value),
            config=cast(JsonValue, paper_config_payload(value.config)),
        )


class PaperOrderIntentResponse(ApiSchema):
    side: str
    order_type: str
    quantity: PaperDecimal
    time_in_force: str
    limit_price: PaperDecimal | None
    stop_price: PaperDecimal | None
    client_tag: str | None

    @classmethod
    def from_domain(cls, value: OrderIntent) -> PaperOrderIntentResponse:
        OrderIntent.__post_init__(value)
        return cls(
            side=value.side.value,
            order_type=value.order_type.value,
            quantity=value.quantity,
            time_in_force=value.time_in_force.value,
            limit_price=value.limit_price,
            stop_price=value.stop_price,
            client_tag=value.client_tag,
        )


class PaperOrderResponse(ApiSchema):
    order_id: str
    created_sequence: int
    created_at: datetime
    created_candle_index: int
    eligible_candle_index: int
    intent: PaperOrderIntentResponse
    status: str
    opened_at: datetime | None
    terminal_at: datetime | None
    rejection_code: str | None

    @classmethod
    def from_domain(cls, value: SimulatedOrder) -> PaperOrderResponse:
        SimulatedOrder.__post_init__(value)
        return cls(
            order_id=value.order_id,
            created_sequence=value.created_sequence,
            created_at=value.created_at,
            created_candle_index=value.created_candle_index,
            eligible_candle_index=value.eligible_candle_index,
            intent=PaperOrderIntentResponse.from_domain(value.intent),
            status=value.status.value,
            opened_at=value.opened_at,
            terminal_at=value.terminal_at,
            rejection_code=value.rejection_code,
        )


class PaperOrderListResponse(ApiSchema):
    session_id: str
    items: list[PaperOrderResponse]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_domain(cls, value: PaperOrderPage) -> PaperOrderListResponse:
        PaperOrderPage.__post_init__(value)
        return cls(
            session_id=value.session_id,
            items=[PaperOrderResponse.from_domain(item) for item in value.items],
            page=value.page,
            page_size=value.page_size,
            total=value.total,
            total_pages=value.total_pages,
        )


class PaperFillResponse(ApiSchema):
    fill_id: str
    order_id: str
    reason: str
    liquidity: str
    side: str
    quantity: PaperDecimal
    base_price: PaperDecimal
    execution_price: PaperDecimal
    notional: PaperDecimal
    fee: PaperDecimal
    slippage_cost: PaperDecimal
    event_time: datetime
    candle_index: int

    @classmethod
    def from_domain(cls, value: Fill) -> PaperFillResponse:
        Fill.__post_init__(value)
        return cls(
            fill_id=value.fill_id,
            order_id=value.order_id,
            reason=value.reason.value,
            liquidity=value.liquidity.value,
            side=value.side.value,
            quantity=value.quantity,
            base_price=value.base_price,
            execution_price=value.execution_price,
            notional=value.notional,
            fee=value.fee,
            slippage_cost=value.slippage_cost,
            event_time=value.event_time,
            candle_index=value.candle_index,
        )


class PaperFillListResponse(ApiSchema):
    session_id: str
    items: list[PaperFillResponse]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_domain(cls, value: PaperFillPage) -> PaperFillListResponse:
        PaperFillPage.__post_init__(value)
        return cls(
            session_id=value.session_id,
            items=[PaperFillResponse.from_domain(item) for item in value.items],
            page=value.page,
            page_size=value.page_size,
            total=value.total,
            total_pages=value.total_pages,
        )
