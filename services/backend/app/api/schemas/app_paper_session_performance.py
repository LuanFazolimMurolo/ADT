"""Minimal authorized paper-session performance contracts."""

from __future__ import annotations

from datetime import datetime

from app.api.schemas.common import ApiSchema
from app.api.schemas.paper_trading import PaperDecimal
from app.paper_trading.period_metrics import (
    PaperPeriodGranularity,
    PaperPeriodMetricsBucket,
    PaperPeriodMetricsSeries,
    PaperPeriodMetricsTotals,
)
from app.paper_trading.portfolio_timeline import PaperPortfolioObservation
from app.paper_trading.portfolio_timeline_query import PaperPortfolioTimelinePage


class AppPaperPortfolioObservationResponse(ApiSchema):
    candle_index: int
    candle_open_time: datetime
    candle_close_time: datetime
    mark_price: PaperDecimal
    quote_cash: PaperDecimal
    base_quantity: PaperDecimal
    average_entry_price: PaperDecimal
    cost_basis: PaperDecimal
    realized_pnl: PaperDecimal
    unrealized_pnl: PaperDecimal
    total_fees: PaperDecimal
    total_slippage_cost: PaperDecimal
    equity: PaperDecimal
    peak_equity: PaperDecimal
    drawdown: PaperDecimal
    drawdown_pct: PaperDecimal
    risk_halt: bool

    @classmethod
    def from_domain(
        cls,
        value: PaperPortfolioObservation,
    ) -> AppPaperPortfolioObservationResponse:
        PaperPortfolioObservation.__post_init__(value)
        return cls(
            candle_index=value.candle_index,
            candle_open_time=value.candle_open_time,
            candle_close_time=value.candle_close_time,
            mark_price=value.mark_price,
            quote_cash=value.quote_cash,
            base_quantity=value.base_quantity,
            average_entry_price=value.average_entry_price,
            cost_basis=value.cost_basis,
            realized_pnl=value.realized_pnl,
            unrealized_pnl=value.unrealized_pnl,
            total_fees=value.total_fees,
            total_slippage_cost=value.total_slippage_cost,
            equity=value.equity,
            peak_equity=value.peak_equity,
            drawdown=value.drawdown,
            drawdown_pct=value.drawdown_pct,
            risk_halt=value.risk_halt,
        )


class AppPaperPortfolioTimelinePageResponse(ApiSchema):
    session_id: str
    state_checksum: str
    base_asset: str
    quote_asset: str
    timeframe: str
    dataset_version: str
    timeline_id: str
    timeline_content_checksum: str
    initial_capital: PaperDecimal
    available_start: datetime
    available_end: datetime
    range_start: datetime
    range_end: datetime
    limit: int
    count: int
    total_observations: int
    has_more_before: bool
    next_before: datetime | None
    content_checksum: str
    items: list[AppPaperPortfolioObservationResponse]

    @classmethod
    def from_domain(
        cls,
        value: PaperPortfolioTimelinePage,
    ) -> AppPaperPortfolioTimelinePageResponse:
        PaperPortfolioTimelinePage.__post_init__(value)
        return cls(
            session_id=value.session_id,
            state_checksum=value.state_checksum,
            base_asset=value.pair.base,
            quote_asset=value.pair.quote,
            timeframe=value.timeframe.code,
            dataset_version=value.dataset_version,
            timeline_id=value.timeline_id,
            timeline_content_checksum=value.timeline_content_checksum,
            initial_capital=value.initial_capital,
            available_start=value.available_range.start,
            available_end=value.available_range.end,
            range_start=value.page_range.start,
            range_end=value.page_range.end,
            limit=value.limit,
            count=len(value.observations),
            total_observations=value.total_observations,
            has_more_before=value.has_more_before,
            next_before=value.next_before,
            content_checksum=value.content_checksum,
            items=[
                AppPaperPortfolioObservationResponse.from_domain(item)
                for item in value.observations
            ],
        )


class AppPaperPeriodMetricsBucketResponse(ApiSchema):
    period_start: datetime
    period_end: datetime
    quote_asset: str
    realizations_count: int
    winning_realizations_count: int
    losing_realizations_count: int
    breakeven_realizations_count: int
    exit_notional: PaperDecimal
    released_cost_basis: PaperDecimal
    realized_fees: PaperDecimal
    realized_slippage_cost: PaperDecimal
    gross_profit: PaperDecimal
    gross_loss: PaperDecimal
    realized_pnl: PaperDecimal
    win_rate_pct: PaperDecimal | None
    profit_factor: PaperDecimal | None

    @classmethod
    def from_domain(
        cls,
        value: PaperPeriodMetricsBucket,
    ) -> AppPaperPeriodMetricsBucketResponse:
        PaperPeriodMetricsBucket.__post_init__(value)
        return cls(
            period_start=value.period_start,
            period_end=value.period_end,
            quote_asset=value.quote_asset,
            realizations_count=value.realizations_count,
            winning_realizations_count=value.winning_realizations_count,
            losing_realizations_count=value.losing_realizations_count,
            breakeven_realizations_count=value.breakeven_realizations_count,
            exit_notional=value.exit_notional,
            released_cost_basis=value.released_cost_basis,
            realized_fees=value.realized_fees,
            realized_slippage_cost=value.realized_slippage_cost,
            gross_profit=value.gross_profit,
            gross_loss=value.gross_loss,
            realized_pnl=value.realized_pnl,
            win_rate_pct=value.win_rate_pct,
            profit_factor=value.profit_factor,
        )


class AppPaperPeriodMetricsTotalsResponse(ApiSchema):
    periods_count: int
    active_periods_count: int
    quote_asset: str
    realizations_count: int
    winning_realizations_count: int
    losing_realizations_count: int
    breakeven_realizations_count: int
    exit_notional: PaperDecimal
    released_cost_basis: PaperDecimal
    realized_fees: PaperDecimal
    realized_slippage_cost: PaperDecimal
    gross_profit: PaperDecimal
    gross_loss: PaperDecimal
    realized_pnl: PaperDecimal
    win_rate_pct: PaperDecimal | None
    profit_factor: PaperDecimal | None

    @classmethod
    def from_domain(
        cls,
        value: PaperPeriodMetricsTotals,
    ) -> AppPaperPeriodMetricsTotalsResponse:
        PaperPeriodMetricsTotals.__post_init__(value)
        return cls(
            periods_count=value.periods_count,
            active_periods_count=value.active_periods_count,
            quote_asset=value.quote_asset,
            realizations_count=value.realizations_count,
            winning_realizations_count=value.winning_realizations_count,
            losing_realizations_count=value.losing_realizations_count,
            breakeven_realizations_count=value.breakeven_realizations_count,
            exit_notional=value.exit_notional,
            released_cost_basis=value.released_cost_basis,
            realized_fees=value.realized_fees,
            realized_slippage_cost=value.realized_slippage_cost,
            gross_profit=value.gross_profit,
            gross_loss=value.gross_loss,
            realized_pnl=value.realized_pnl,
            win_rate_pct=value.win_rate_pct,
            profit_factor=value.profit_factor,
        )


class AppPaperPeriodMetricsSeriesResponse(ApiSchema):
    session_id: str
    quote_asset: str
    granularity: PaperPeriodGranularity
    period_from: datetime
    period_before: datetime
    items: list[AppPaperPeriodMetricsBucketResponse]
    totals: AppPaperPeriodMetricsTotalsResponse
    query_checksum: str
    content_checksum: str

    @classmethod
    def from_domain(
        cls,
        value: PaperPeriodMetricsSeries,
        *,
        session_id: str,
    ) -> AppPaperPeriodMetricsSeriesResponse:
        PaperPeriodMetricsSeries.__post_init__(value)
        if value.filters.session_id != session_id:
            raise ValueError("period metrics diverged from the authorized session")
        return cls(
            session_id=session_id,
            quote_asset=value.filters.quote_asset,
            granularity=value.granularity,
            period_from=value.filters.period_from,
            period_before=value.filters.period_before,
            items=[AppPaperPeriodMetricsBucketResponse.from_domain(item) for item in value.items],
            totals=AppPaperPeriodMetricsTotalsResponse.from_domain(value.totals),
            query_checksum=value.query_checksum,
            content_checksum=value.content_checksum,
        )
