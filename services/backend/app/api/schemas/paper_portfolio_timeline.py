"""Authenticated HTTP contracts for persisted paper portfolio timelines."""

from __future__ import annotations

from datetime import datetime

from app.api.schemas.common import ApiSchema
from app.api.schemas.paper_trading import PaperDecimal
from app.paper_trading.portfolio_timeline import PaperPortfolioObservation
from app.paper_trading.portfolio_timeline_query import PaperPortfolioTimelinePage


class PaperPortfolioObservationResponse(ApiSchema):
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
    ) -> PaperPortfolioObservationResponse:
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


class PaperPortfolioTimelinePageResponse(ApiSchema):
    schema_version: int
    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    state_replayed_at: datetime
    symbol: str
    base_asset: str
    quote_asset: str
    timeframe: str
    dataset_version: str
    source_checksum: str
    timeline_id: str
    timeline_content_checksum: str
    initial_capital: PaperDecimal
    requested_before: datetime | None
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
    items: list[PaperPortfolioObservationResponse]

    @classmethod
    def from_domain(
        cls,
        value: PaperPortfolioTimelinePage,
    ) -> PaperPortfolioTimelinePageResponse:
        PaperPortfolioTimelinePage.__post_init__(value)
        return cls(
            schema_version=value.schema_version,
            session_id=value.session_id,
            config_checksum=value.config_checksum,
            state_id=value.state_id,
            state_checksum=value.state_checksum,
            state_replayed_at=value.state_replayed_at,
            symbol=value.pair.symbol,
            base_asset=value.pair.base,
            quote_asset=value.pair.quote,
            timeframe=value.timeframe.code,
            dataset_version=value.dataset_version,
            source_checksum=value.source_checksum,
            timeline_id=value.timeline_id,
            timeline_content_checksum=value.timeline_content_checksum,
            initial_capital=value.initial_capital,
            requested_before=value.requested_before,
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
                PaperPortfolioObservationResponse.from_domain(item) for item in value.observations
            ],
        )
