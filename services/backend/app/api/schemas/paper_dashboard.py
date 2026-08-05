"""Authenticated read-only HTTP contracts for the paper-trading dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.api.schemas.common import ApiSchema
from app.api.schemas.paper_trading import PaperDecimal, PaperPortfolioResponse
from app.backtesting.domain import PortfolioSnapshot
from app.indicators.regime import (
    MarketRegimeKind,
    MarketRegimePoint,
    TrendDirection,
)
from app.paper_trading.continuous import (
    PaperRunnerCycleStatus,
    PaperRunnerSessionStatus,
)
from app.paper_trading.dashboard import (
    PaperDashboardPage,
    PaperDashboardPageTotals,
    PaperDashboardRunnerResult,
    PaperDashboardSession,
    PaperPerformanceMetrics,
    PaperPositionSnapshot,
)


class PaperDashboardMetricsResponse(ApiSchema):
    initial_capital: PaperDecimal
    equity: PaperDecimal
    total_pnl: PaperDecimal
    return_pct: PaperDecimal
    realized_pnl: PaperDecimal
    unrealized_pnl: PaperDecimal
    drawdown: PaperDecimal
    drawdown_pct: PaperDecimal
    total_fees: PaperDecimal
    total_slippage_cost: PaperDecimal

    @classmethod
    def from_domain(cls, value: PaperPerformanceMetrics) -> PaperDashboardMetricsResponse:
        PaperPerformanceMetrics.__post_init__(value)
        return cls(
            initial_capital=value.initial_capital,
            equity=value.equity,
            total_pnl=value.total_pnl,
            return_pct=value.return_pct,
            realized_pnl=value.realized_pnl,
            unrealized_pnl=value.unrealized_pnl,
            drawdown=value.drawdown,
            drawdown_pct=value.drawdown_pct,
            total_fees=value.total_fees,
            total_slippage_cost=value.total_slippage_cost,
        )


class PaperDashboardPositionResponse(ApiSchema):
    is_open: bool
    base_quantity: PaperDecimal
    average_entry_price: PaperDecimal
    cost_basis: PaperDecimal
    market_value: PaperDecimal

    @classmethod
    def from_domain(cls, value: PaperPositionSnapshot) -> PaperDashboardPositionResponse:
        PaperPositionSnapshot.__post_init__(value)
        return cls(
            is_open=value.is_open,
            base_quantity=value.base_quantity,
            average_entry_price=value.average_entry_price,
            cost_basis=value.cost_basis,
            market_value=value.market_value,
        )


class PaperDashboardRegimeResponse(ApiSchema):
    event_time: datetime
    regime: MarketRegimeKind
    trend_direction: TrendDirection
    fast_ema: PaperDecimal | None
    slow_ema: PaperDecimal | None
    atr: PaperDecimal | None
    atr_ratio: PaperDecimal | None
    trend_strength: PaperDecimal | None

    @classmethod
    def from_domain(cls, value: MarketRegimePoint) -> PaperDashboardRegimeResponse:
        MarketRegimePoint.__post_init__(value)
        return cls(
            event_time=value.event_time,
            regime=value.regime,
            trend_direction=value.trend_direction,
            fast_ema=value.fast_ema,
            slow_ema=value.slow_ema,
            atr=value.atr,
            atr_ratio=value.atr_ratio,
            trend_strength=value.trend_strength,
        )


class PaperDashboardRunnerResultResponse(ApiSchema):
    status: PaperRunnerSessionStatus
    started_at: datetime
    finished_at: datetime
    state_id: str | None
    candles_processed: int | None
    last_candle_open_time: datetime | None
    error_code: str | None
    matches_current_state: bool

    @classmethod
    def from_domain(
        cls,
        value: PaperDashboardRunnerResult,
    ) -> PaperDashboardRunnerResultResponse:
        PaperDashboardRunnerResult.__post_init__(value)
        return cls(
            status=value.status,
            started_at=value.started_at,
            finished_at=value.finished_at,
            state_id=value.state_id,
            candles_processed=value.candles_processed,
            last_candle_open_time=value.last_candle_open_time,
            error_code=value.error_code,
            matches_current_state=value.matches_current_state,
        )


class PaperDashboardSessionResponse(ApiSchema):
    session_id: str
    symbol: str
    base_asset: str
    quote_asset: str
    timeframe: str
    strategy_name: str
    strategy_version: str
    initial_capital: PaperDecimal
    state_available: bool
    candles_processed: int | None
    last_candle_open_time: datetime | None
    replayed_at: datetime | None
    orders_count: int
    fills_count: int
    open_orders_count: int
    risk_halt: bool | None
    metrics: PaperDashboardMetricsResponse | None
    portfolio: PaperPortfolioResponse | None
    position: PaperDashboardPositionResponse | None
    latest_market_regime: PaperDashboardRegimeResponse | None
    runner: PaperDashboardRunnerResultResponse | None

    @classmethod
    def from_domain(cls, value: PaperDashboardSession) -> PaperDashboardSessionResponse:
        PaperDashboardSession.__post_init__(value)
        return cls(
            session_id=value.session_id,
            symbol=value.pair.symbol,
            base_asset=value.pair.base,
            quote_asset=value.pair.quote,
            timeframe=value.timeframe.code,
            strategy_name=value.strategy.name,
            strategy_version=value.strategy.version,
            initial_capital=value.initial_capital,
            state_available=value.state_available,
            candles_processed=value.candles_processed,
            last_candle_open_time=value.last_candle_open_time,
            replayed_at=value.replayed_at,
            orders_count=value.orders_count,
            fills_count=value.fills_count,
            open_orders_count=value.open_orders_count,
            risk_halt=value.risk_halt,
            metrics=(
                None
                if value.metrics is None
                else PaperDashboardMetricsResponse.from_domain(value.metrics)
            ),
            portfolio=(None if value.portfolio is None else _portfolio_response(value.portfolio)),
            position=(
                None
                if value.position is None
                else PaperDashboardPositionResponse.from_domain(value.position)
            ),
            latest_market_regime=(
                None
                if value.latest_market_regime is None
                else PaperDashboardRegimeResponse.from_domain(value.latest_market_regime)
            ),
            runner=(
                None
                if value.runner is None
                else PaperDashboardRunnerResultResponse.from_domain(value.runner)
            ),
        )


class PaperDashboardTotalsResponse(ApiSchema):
    scope: Literal["page"] = "page"
    sessions_count: int
    initialized_count: int
    pending_count: int
    runner_failed_count: int
    risk_halted_count: int
    open_positions_count: int
    open_orders_count: int
    configured_capital: PaperDecimal
    initialized_capital: PaperDecimal
    equity: PaperDecimal
    total_pnl: PaperDecimal
    return_pct: PaperDecimal
    maximum_drawdown_pct: PaperDecimal

    @classmethod
    def from_domain(cls, value: PaperDashboardPageTotals) -> PaperDashboardTotalsResponse:
        PaperDashboardPageTotals.__post_init__(value)
        return cls(
            sessions_count=value.sessions_count,
            initialized_count=value.initialized_count,
            pending_count=value.pending_count,
            runner_failed_count=value.runner_failed_count,
            risk_halted_count=value.risk_halted_count,
            open_positions_count=value.open_positions_count,
            open_orders_count=value.open_orders_count,
            configured_capital=value.configured_capital,
            initialized_capital=value.initialized_capital,
            equity=value.equity,
            total_pnl=value.total_pnl,
            return_pct=value.return_pct,
            maximum_drawdown_pct=value.maximum_drawdown_pct,
        )


class PaperDashboardRunnerCycleResponse(ApiSchema):
    cycle_index: int
    status: PaperRunnerCycleStatus
    finished_at: datetime
    next_cycle_at: datetime


class PaperDashboardResponse(ApiSchema):
    items: list[PaperDashboardSessionResponse]
    totals: PaperDashboardTotalsResponse
    page: int
    page_size: int
    total: int
    total_pages: int
    runner: PaperDashboardRunnerCycleResponse | None

    @classmethod
    def from_domain(cls, value: PaperDashboardPage) -> PaperDashboardResponse:
        PaperDashboardPage.__post_init__(value)
        runner = None
        if value.runner_cycle_index is not None:
            runner_status = value.runner_cycle_status
            runner_finished_at = value.runner_finished_at
            runner_next_cycle_at = value.runner_next_cycle_at
            if (
                runner_status is None or runner_finished_at is None or runner_next_cycle_at is None
            ):  # pragma: no cover - guarded by the domain model
                raise ValueError("Dashboard runner state is incomplete.")
            runner = PaperDashboardRunnerCycleResponse(
                cycle_index=value.runner_cycle_index,
                status=runner_status,
                finished_at=runner_finished_at,
                next_cycle_at=runner_next_cycle_at,
            )
        return cls(
            items=[PaperDashboardSessionResponse.from_domain(item) for item in value.items],
            totals=PaperDashboardTotalsResponse.from_domain(value.totals),
            page=value.page,
            page_size=value.page_size,
            total=value.total,
            total_pages=value.total_pages,
            runner=runner,
        )


def _portfolio_response(value: PortfolioSnapshot) -> PaperPortfolioResponse:
    if not isinstance(value, PortfolioSnapshot):
        raise ValueError("Dashboard portfolio is invalid.")
    PortfolioSnapshot.__post_init__(value)
    return PaperPortfolioResponse(
        quote_cash=value.quote_cash,
        base_quantity=value.base_quantity,
        average_entry_price=value.average_entry_price,
        realized_pnl=value.realized_pnl,
        unrealized_pnl=value.unrealized_pnl,
        total_fees=value.total_fees,
        total_slippage_cost=value.total_slippage_cost,
        equity=value.equity,
        peak_equity=value.peak_equity,
        drawdown=value.drawdown,
        drawdown_pct=value.drawdown_pct,
        cost_basis=value.cost_basis,
    )
