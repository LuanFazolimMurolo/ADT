"""Deterministic UTC calendar-period metrics for verified paper trading."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import OrderSide
from app.paper_trading.domain import paper_session_id
from app.paper_trading.errors import InvalidPaperSessionError
from app.paper_trading.period_metrics import (
    PaperPeriodGranularity,
    PaperPeriodMetricsFilter,
    PaperPeriodMetricsService,
    calendar_period_end,
    calendar_period_start,
)
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading import FakeSource, _candle
from tests.test_paper_trading_journal import (
    _intent,
    _journal_config,
    _service,
)


def _populated_period_repository(
    tmp_path: Path,
) -> tuple[PaperTradingRepository, str, Decimal]:
    start = datetime(2026, 8, 1, 23, 55, tzinfo=UTC)
    candles = tuple(
        _candle(1_435 + index, close)
        for index, close in enumerate(("100", "101", "110", "120", "130", "140", "150"))
    )
    schedule = (
        (0, (_intent(OrderSide.BUY, "2", "entry"),)),
        (2, (_intent(OrderSide.SELL, "1", "exit-day-one"),)),
        (4, (_intent(OrderSide.SELL, "1", "exit-day-two"),)),
    )
    service = _service(tmp_path, FakeSource(candles), schedule)
    config = replace(_journal_config(), start_at=start)
    service.create(config)
    state = service.run_once(paper_session_id(config)).state
    return (
        PaperTradingRepository(tmp_path),
        paper_session_id(config),
        state.portfolio.realized_pnl,
    )


def test_calendar_period_bounds_are_utc_half_open_and_iso_weekly() -> None:
    value = datetime(2026, 8, 6, 13, 45, 30, tzinfo=UTC)

    daily_start = calendar_period_start(value, PaperPeriodGranularity.DAILY)
    assert daily_start == datetime(2026, 8, 6, tzinfo=UTC)
    assert calendar_period_end(daily_start, PaperPeriodGranularity.DAILY) == datetime(
        2026,
        8,
        7,
        tzinfo=UTC,
    )

    weekly_start = calendar_period_start(value, PaperPeriodGranularity.WEEKLY)
    assert weekly_start == datetime(2026, 8, 3, tzinfo=UTC)
    assert calendar_period_end(
        weekly_start,
        PaperPeriodGranularity.WEEKLY,
    ) == datetime(2026, 8, 10, tzinfo=UTC)

    monthly_start = calendar_period_start(value, PaperPeriodGranularity.MONTHLY)
    assert monthly_start == datetime(2026, 8, 1, tzinfo=UTC)
    assert calendar_period_end(
        monthly_start,
        PaperPeriodGranularity.MONTHLY,
    ) == datetime(2026, 9, 1, tzinfo=UTC)


def test_period_metrics_allocate_partial_realizations_to_exit_calendar_days(
    tmp_path: Path,
) -> None:
    repository, session_id, realized_pnl = _populated_period_repository(tmp_path)
    service = PaperPeriodMetricsService(repository)
    filters = PaperPeriodMetricsFilter(
        quote_asset="usdt",
        period_from=datetime(2026, 8, 1, tzinfo=UTC),
        period_before=datetime(2026, 8, 4, tzinfo=UTC),
        session_id=session_id,
        base_asset="btc",
        timeframe_code="1m",
        strategy_name="paper-journal-test",
        strategy_version="1",
    )

    series = service.build_series(
        filters,
        granularity=PaperPeriodGranularity.DAILY,
    )

    assert (
        service.build_series(
            filters,
            granularity=PaperPeriodGranularity.DAILY,
        )
        == series
    )
    assert filters.quote_asset == "USDT"
    assert filters.base_asset == "BTC"
    assert len(series.source_states) == 1
    assert series.source_states[0].session_id == session_id
    assert series.source_states[0].quote_asset == "USDT"
    assert len(series.query_checksum) == 64
    assert len(series.content_checksum) == 64
    assert len(series.items) == 3
    assert [item.realizations_count for item in series.items] == [1, 1, 0]
    assert series.items[0].period_start == datetime(2026, 8, 1, tzinfo=UTC)
    assert series.items[1].period_start == datetime(2026, 8, 2, tzinfo=UTC)
    assert series.items[2].period_start == datetime(2026, 8, 3, tzinfo=UTC)
    assert series.items[2].realized_pnl == 0
    assert series.items[2].win_rate_pct is None
    assert series.items[2].profit_factor is None

    assert series.totals.periods_count == 3
    assert series.totals.active_periods_count == 2
    assert series.totals.realizations_count == 2
    assert series.totals.sessions_count == 1
    assert series.totals.symbols_count == 1
    assert series.totals.realized_pnl == realized_pnl
    assert series.totals.exit_notional > 0
    assert series.totals.released_cost_basis > 0
    assert series.totals.realized_fees > 0
    assert series.totals.realized_slippage_cost > 0
    assert series.totals.realized_pnl == (series.totals.gross_profit + series.totals.gross_loss)


def test_period_metrics_empty_series_is_bounded_and_source_free(
    tmp_path: Path,
) -> None:
    service = PaperPeriodMetricsService(PaperTradingRepository(tmp_path))
    filters = PaperPeriodMetricsFilter(
        quote_asset="USDT",
        period_from=datetime(2026, 8, 1, tzinfo=UTC),
        period_before=datetime(2026, 9, 1, tzinfo=UTC),
    )

    series = service.build_series(
        filters,
        granularity=PaperPeriodGranularity.MONTHLY,
    )

    assert len(series.items) == 1
    assert series.source_states == ()
    assert series.totals.periods_count == 1
    assert series.totals.active_periods_count == 0
    assert series.totals.realizations_count == 0
    assert series.totals.sessions_count == 0
    assert series.totals.symbols_count == 0
    assert series.totals.realized_pnl == 0
    assert series.totals.win_rate_pct is None
    assert series.totals.profit_factor is None


def test_period_metrics_reject_noncanonical_or_unaligned_queries(
    tmp_path: Path,
) -> None:
    with pytest.raises(InvalidPaperSessionError):
        PaperPeriodMetricsFilter(
            quote_asset="",
            period_from=datetime(2026, 8, 1, tzinfo=UTC),
            period_before=datetime(2026, 8, 2, tzinfo=UTC),
        )
    with pytest.raises(InvalidPaperSessionError):
        PaperPeriodMetricsFilter(
            quote_asset="USDT",
            period_from=datetime(2026, 8, 1),
            period_before=datetime(2026, 8, 2, tzinfo=UTC),
        )
    with pytest.raises(InvalidPaperSessionError):
        PaperPeriodMetricsFilter(
            quote_asset="USDT",
            period_from=datetime(2026, 8, 2, tzinfo=UTC),
            period_before=datetime(2026, 8, 1, tzinfo=UTC),
        )

    service = PaperPeriodMetricsService(PaperTradingRepository(tmp_path))
    unaligned = PaperPeriodMetricsFilter(
        quote_asset="USDT",
        period_from=datetime(2026, 8, 1, 0, 1, tzinfo=UTC),
        period_before=datetime(2026, 8, 2, tzinfo=UTC),
    )
    with pytest.raises(InvalidPaperSessionError, match="period_from"):
        service.build_series(
            unaligned,
            granularity=PaperPeriodGranularity.DAILY,
        )

    aligned = replace(
        unaligned,
        period_from=datetime(2026, 8, 1, tzinfo=UTC),
        period_before=datetime(2026, 8, 2, 0, 1, tzinfo=UTC),
    )
    with pytest.raises(InvalidPaperSessionError, match="period_before"):
        service.build_series(
            aligned,
            granularity=PaperPeriodGranularity.DAILY,
        )


def test_query_checksum_binds_granularity_and_quote_asset(tmp_path: Path) -> None:
    service = PaperPeriodMetricsService(PaperTradingRepository(tmp_path))
    daily_filters = PaperPeriodMetricsFilter(
        quote_asset="USDT",
        period_from=datetime(2026, 8, 3, tzinfo=UTC),
        period_before=datetime(2026, 8, 10, tzinfo=UTC),
    )
    daily = service.build_series(
        daily_filters,
        granularity=PaperPeriodGranularity.DAILY,
    )
    weekly = service.build_series(
        daily_filters,
        granularity=PaperPeriodGranularity.WEEKLY,
    )
    other_quote = service.build_series(
        replace(daily_filters, quote_asset="BRL"),
        granularity=PaperPeriodGranularity.DAILY,
    )

    assert daily.query_checksum != weekly.query_checksum
    assert daily.content_checksum != weekly.content_checksum
    assert daily.query_checksum != other_quote.query_checksum
    assert daily.content_checksum != other_quote.content_checksum
