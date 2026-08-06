"""Authenticated HTTP contracts for deterministic paper period metrics."""

from __future__ import annotations

from datetime import datetime

from app.api.schemas.common import ApiSchema
from app.api.schemas.paper_trading import PaperDecimal
from app.paper_trading.period_metrics import (
    PaperPeriodGranularity,
    PaperPeriodMetricsBucket,
    PaperPeriodMetricsFilter,
    PaperPeriodMetricsSeries,
    PaperPeriodMetricsTotals,
    PaperPeriodSourceState,
)


class PaperPeriodMetricsFilterResponse(ApiSchema):
    quote_asset: str
    period_from: datetime
    period_before: datetime
    session_id: str | None
    base_asset: str | None
    timeframe: str | None
    strategy_name: str | None
    strategy_version: str | None

    @classmethod
    def from_domain(
        cls,
        value: PaperPeriodMetricsFilter,
    ) -> PaperPeriodMetricsFilterResponse:
        PaperPeriodMetricsFilter.__post_init__(value)
        return cls(
            quote_asset=value.quote_asset,
            period_from=value.period_from,
            period_before=value.period_before,
            session_id=value.session_id,
            base_asset=value.base_asset,
            timeframe=value.timeframe_code,
            strategy_name=value.strategy_name,
            strategy_version=value.strategy_version,
        )


class PaperPeriodSourceStateResponse(ApiSchema):
    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    base_asset: str
    quote_asset: str
    last_candle_open_time: datetime
    replayed_at: datetime

    @classmethod
    def from_domain(
        cls,
        value: PaperPeriodSourceState,
    ) -> PaperPeriodSourceStateResponse:
        PaperPeriodSourceState.__post_init__(value)
        return cls(
            session_id=value.session_id,
            config_checksum=value.config_checksum,
            state_id=value.state_id,
            state_checksum=value.state_checksum,
            base_asset=value.base_asset,
            quote_asset=value.quote_asset,
            last_candle_open_time=value.last_candle_open_time,
            replayed_at=value.replayed_at,
        )


class PaperPeriodMetricsBucketResponse(ApiSchema):
    period_start: datetime
    period_end: datetime
    quote_asset: str
    realizations_count: int
    winning_realizations_count: int
    losing_realizations_count: int
    breakeven_realizations_count: int
    sessions_count: int
    symbols_count: int
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
    ) -> PaperPeriodMetricsBucketResponse:
        PaperPeriodMetricsBucket.__post_init__(value)
        return cls(
            period_start=value.period_start,
            period_end=value.period_end,
            quote_asset=value.quote_asset,
            realizations_count=value.realizations_count,
            winning_realizations_count=value.winning_realizations_count,
            losing_realizations_count=value.losing_realizations_count,
            breakeven_realizations_count=value.breakeven_realizations_count,
            sessions_count=value.sessions_count,
            symbols_count=value.symbols_count,
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


class PaperPeriodMetricsTotalsResponse(ApiSchema):
    periods_count: int
    active_periods_count: int
    quote_asset: str
    realizations_count: int
    winning_realizations_count: int
    losing_realizations_count: int
    breakeven_realizations_count: int
    sessions_count: int
    symbols_count: int
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
    ) -> PaperPeriodMetricsTotalsResponse:
        PaperPeriodMetricsTotals.__post_init__(value)
        return cls(
            periods_count=value.periods_count,
            active_periods_count=value.active_periods_count,
            quote_asset=value.quote_asset,
            realizations_count=value.realizations_count,
            winning_realizations_count=value.winning_realizations_count,
            losing_realizations_count=value.losing_realizations_count,
            breakeven_realizations_count=value.breakeven_realizations_count,
            sessions_count=value.sessions_count,
            symbols_count=value.symbols_count,
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


class PaperPeriodMetricsSeriesResponse(ApiSchema):
    schema_version: int
    granularity: PaperPeriodGranularity
    filters: PaperPeriodMetricsFilterResponse
    source_states: list[PaperPeriodSourceStateResponse]
    items: list[PaperPeriodMetricsBucketResponse]
    totals: PaperPeriodMetricsTotalsResponse
    query_checksum: str
    content_checksum: str

    @classmethod
    def from_domain(
        cls,
        value: PaperPeriodMetricsSeries,
    ) -> PaperPeriodMetricsSeriesResponse:
        PaperPeriodMetricsSeries.__post_init__(value)
        return cls(
            schema_version=value.schema_version,
            granularity=value.granularity,
            filters=PaperPeriodMetricsFilterResponse.from_domain(value.filters),
            source_states=[
                PaperPeriodSourceStateResponse.from_domain(item) for item in value.source_states
            ],
            items=[PaperPeriodMetricsBucketResponse.from_domain(item) for item in value.items],
            totals=PaperPeriodMetricsTotalsResponse.from_domain(value.totals),
            query_checksum=value.query_checksum,
            content_checksum=value.content_checksum,
        )
