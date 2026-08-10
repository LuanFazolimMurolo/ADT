"""Deterministic HTTP regression budgets for bounded Phase 6 projections."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.api.dependencies.auth import (
    get_authenticated_user,
    require_administrator,
    require_app_paper_session_reader,
)
from app.api.dependencies.resources import (
    get_market_candle_read_service,
    get_paper_chart_annotation_read_service,
    get_paper_period_metrics_service,
    get_paper_portfolio_timeline_read_service,
    get_paper_trade_journal_read_service,
    get_paper_trading_read_service,
)
from app.backtesting.domain import (
    FillLiquidity,
    FillReason,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from app.main import app
from app.market_data.candle_query import (
    MARKET_CANDLE_PAGE_SCHEMA_VERSION,
    MarketCandlePage,
    market_candle_page_checksum,
)
from app.market_data.domain import DataRange, Exchange, MarketType, TradingPair
from app.market_data.storage import RAW_DATASET_VERSION_ALGORITHM
from app.market_data.timeframes import get_timeframe
from app.paper_trading.chart_annotations import (
    PaperChartAnnotationPage,
    PaperChartFillAnnotation,
    PaperChartFillRole,
    PaperChartOrderAnnotation,
)
from app.paper_trading.journal import _trade_id
from app.paper_trading.journal_query import (
    PaperTradeJournalFilter,
    PaperTradeJournalReadService,
    PaperTradePage,
)
from app.paper_trading.journal_query import (
    _totals as journal_totals,
)
from app.paper_trading.period_metrics import (
    PaperPeriodGranularity,
    PaperPeriodMetricsBucket,
    PaperPeriodMetricsFilter,
    PaperPeriodMetricsSeries,
    PaperPeriodSourceState,
)
from app.paper_trading.period_metrics import (
    _content_checksum as period_content_checksum,
)
from app.paper_trading.period_metrics import (
    _query_checksum as period_query_checksum,
)
from app.paper_trading.period_metrics import (
    _totals as period_totals,
)
from app.paper_trading.portfolio_timeline import PaperPortfolioObservation
from app.paper_trading.portfolio_timeline_query import (
    PAPER_PORTFOLIO_TIMELINE_PAGE_SCHEMA_VERSION,
    PaperPortfolioTimelinePage,
    _page_content_checksum,
)
from tests.market_data_helpers import candle
from tests.test_paper_trading_journal_query import (
    _populated_repository,
    _state_verifier,
)

CANDLES_ADMIN_BUDGET = 1_638_400
CANDLES_APP_BUDGET = 1_638_400
ANNOTATIONS_ADMIN_BUDGET = 2_621_440
ANNOTATIONS_APP_BUDGET = 1_376_256
TIMELINE_ADMIN_BUDGET = 3_145_728
TIMELINE_APP_BUDGET = 3_145_728
JOURNAL_ADMIN_BUDGET = 262_144
JOURNAL_APP_BUDGET = 65_536
PERIOD_ADMIN_BUDGET = 8_388_608
PERIOD_APP_BUDGET = 3_145_728

MAX_CANDLES = 5_000
MAX_ANNOTATIONS = 5_000
MAX_TIMELINE_OBSERVATIONS = 5_000
MAX_JOURNAL_PAGE = 100
MAX_PERIOD_BUCKETS = 5_000
MAX_PERIOD_SOURCE_STATES = 10_000

_START = datetime(2020, 1, 1, tzinfo=UTC)
_AUTH_ID = UUID("11111111-1111-1111-1111-111111111111")
_DIGESTS = {
    name: hashlib.sha256(name.encode()).hexdigest()
    for name in (
        "session",
        "config",
        "state",
        "state_checksum",
        "dataset",
        "source",
        "timeline",
        "timeline_content",
    )
}


class _StaticService:
    def __init__(self, value: Any) -> None:
        self.value = value

    def read_page(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.value

    def list_trades(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.value

    def build_series(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.value

    def get_session(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.value


def _service_override(value: Any) -> Any:
    return lambda: _StaticService(value)


def assert_response_budget(
    *,
    name: str,
    response: httpx.Response,
    ceiling: int,
    cardinality: str,
) -> int:
    """Assert one reviewed canonical HTTP response stays within its D1 ceiling."""
    actual = len(response.content)
    overage = max(0, actual - ceiling)
    assert actual <= ceiling, (
        f"{name} response budget exceeded: actual={actual} bytes, "
        f"ceiling={ceiling} bytes, overage={overage} bytes, cardinality={cardinality}"
    )
    return actual


def _candle_page() -> MarketCandlePage:
    timeframe = get_timeframe("1m")
    candles = tuple(
        candle(
            _START + index * timeframe.duration,
            timeframe=timeframe,
            open_price="100.12345678",
            high="110.87654321",
            low="90.12345678",
            close="105.87654321",
            volume="12345.12345678",
            quote_volume="1300000.87654321",
            trade_count=123_456,
        )
        for index in range(MAX_CANDLES)
    )
    data_range = DataRange(_START, _START + MAX_CANDLES * timeframe.duration)
    pair = TradingPair("BTC", "USDT")
    checksum = market_candle_page_checksum(
        schema_version=MARKET_CANDLE_PAGE_SCHEMA_VERSION,
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=pair,
        timeframe=timeframe,
        requested_before=data_range.end,
        available_range=data_range,
        data_range=data_range,
        limit=MAX_CANDLES,
        dataset_candle_count=MAX_CANDLES,
        dataset_version=_DIGESTS["dataset"],
        dataset_version_algorithm=RAW_DATASET_VERSION_ALGORITHM,
        has_more_before=False,
        next_before=None,
        candles=candles,
    )
    return MarketCandlePage(
        schema_version=MARKET_CANDLE_PAGE_SCHEMA_VERSION,
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=pair,
        timeframe=timeframe,
        requested_before=data_range.end,
        available_range=data_range,
        data_range=data_range,
        limit=MAX_CANDLES,
        dataset_candle_count=MAX_CANDLES,
        dataset_version=_DIGESTS["dataset"],
        dataset_version_algorithm=RAW_DATASET_VERSION_ALGORITHM,
        content_checksum=checksum,
        has_more_before=False,
        next_before=None,
        candles=candles,
    )


def _annotation_page() -> PaperChartAnnotationPage:
    orders_count = MAX_ANNOTATIONS // 2
    fills_count = MAX_ANNOTATIONS - orders_count
    quantity = Decimal("0.12345678")
    base_price = Decimal("100.12345678")
    execution_price = Decimal("100.22358024")
    orders = tuple(
        PaperChartOrderAnnotation(
            order_id=f"order-{index:05d}",
            created_sequence=index + 1,
            created_at=_START + index * timedelta(seconds=2),
            opened_at=_START + index * timedelta(seconds=2),
            terminal_at=_START + index * timedelta(seconds=2) + timedelta(seconds=1),
            side=OrderSide.BUY if index % 2 == 0 else OrderSide.SELL,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            status=OrderStatus.FILLED,
            quantity=quantity,
            limit_price=None,
            stop_price=None,
            client_tag=f"signal-{index:05d}",
            rejection_code=None,
            is_engine_protective_stop=False,
        )
        for index in range(orders_count)
    )
    fills = tuple(
        PaperChartFillAnnotation(
            fill_id=f"fill-{index:05d}",
            order_id=f"order-{index:05d}",
            trade_id=hashlib.sha256(f"trade-{index}".encode()).hexdigest(),
            trade_sequence=index + 1,
            role=PaperChartFillRole.ENTRY if index % 2 == 0 else PaperChartFillRole.EXIT,
            event_time=_START + index * timedelta(seconds=2) + timedelta(seconds=1),
            candle_index=index,
            side=OrderSide.BUY if index % 2 == 0 else OrderSide.SELL,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            client_tag=f"signal-{index:05d}",
            fill_reason=FillReason.MARKET_OPEN,
            liquidity=FillLiquidity.TAKER,
            quantity=quantity,
            base_price=base_price,
            execution_price=execution_price,
            notional=quantity * execution_price,
            fee=Decimal("0.01237328"),
            slippage_cost=Decimal("0.01236091"),
            is_engine_protective_stop=False,
        )
        for index in range(fills_count)
    )
    return PaperChartAnnotationPage(
        session_id=_DIGESTS["session"],
        config_checksum=_DIGESTS["config"],
        state_available=True,
        state_id=_DIGESTS["state"],
        state_checksum=_DIGESTS["state_checksum"],
        dataset_version=_DIGESTS["dataset"],
        source_checksum=_DIGESTS["source"],
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1m"),
        strategy_name="ema-cross-example",
        strategy_version="1",
        strategy_parameters=(("fast_period", 12), ("slow_period", 26)),
        ema_fast_period=12,
        ema_slow_period=26,
        range_start=_START,
        range_end=_START + timedelta(days=1),
        limit=MAX_ANNOTATIONS,
        orders=orders,
        fills=fills,
        last_candle_open_time=_START + timedelta(hours=12),
        replayed_at=_START + timedelta(days=1),
    )


def _timeline_page() -> PaperPortfolioTimelinePage:
    timeframe = get_timeframe("1m")
    quote_cash = Decimal("8765.43210987")
    base_quantity = Decimal("1.23456789")
    average_entry_price = Decimal("10000.12345678")
    cost_basis = base_quantity * average_entry_price
    mark_price = Decimal("12345.67890123")
    market_value = base_quantity * mark_price
    equity = quote_cash + market_value
    unrealized_pnl = market_value - cost_basis
    observations = tuple(
        PaperPortfolioObservation(
            session_id=_DIGESTS["session"],
            config_checksum=_DIGESTS["config"],
            state_id=_DIGESTS["state"],
            dataset_version=_DIGESTS["dataset"],
            source_checksum=_DIGESTS["source"],
            candle_index=index,
            candle_open_time=_START + index * timeframe.duration,
            candle_close_time=(
                _START + (index + 1) * timeframe.duration - timedelta(milliseconds=1)
            ),
            mark_price=mark_price,
            quote_cash=quote_cash,
            base_quantity=base_quantity,
            average_entry_price=average_entry_price,
            cost_basis=cost_basis,
            realized_pnl=Decimal("123.45678901"),
            unrealized_pnl=unrealized_pnl,
            total_fees=Decimal("12.34567890"),
            total_slippage_cost=Decimal("6.78901234"),
            equity=equity,
            peak_equity=equity,
            drawdown=Decimal("0"),
            drawdown_pct=Decimal("0"),
            risk_halt=False,
        )
        for index in range(MAX_TIMELINE_OBSERVATIONS)
    )
    data_range = DataRange(
        _START,
        _START + MAX_TIMELINE_OBSERVATIONS * timeframe.duration,
    )
    values: dict[str, Any] = {
        "schema_version": PAPER_PORTFOLIO_TIMELINE_PAGE_SCHEMA_VERSION,
        "session_id": _DIGESTS["session"],
        "config_checksum": _DIGESTS["config"],
        "state_id": _DIGESTS["state"],
        "state_checksum": _DIGESTS["state_checksum"],
        "state_replayed_at": _START + timedelta(days=4),
        "pair": TradingPair("BTC", "USDT"),
        "timeframe": timeframe,
        "dataset_version": _DIGESTS["dataset"],
        "source_checksum": _DIGESTS["source"],
        "timeline_id": _DIGESTS["timeline"],
        "timeline_content_checksum": _DIGESTS["timeline_content"],
        "initial_capital": Decimal("10000.00000000"),
        "requested_before": None,
        "available_range": data_range,
        "page_range": data_range,
        "limit": MAX_TIMELINE_OBSERVATIONS,
        "total_observations": MAX_TIMELINE_OBSERVATIONS,
        "has_more_before": False,
        "next_before": None,
        "observations": observations,
    }
    return PaperPortfolioTimelinePage(
        **values,
        content_checksum=_page_content_checksum(**values),
    )


def _period_buckets() -> tuple[PaperPeriodMetricsBucket, ...]:
    gross_profit = Decimal("345678.12345678")
    gross_loss = Decimal("-234567.12345678")
    return tuple(
        PaperPeriodMetricsBucket(
            period_start=_START + timedelta(days=index),
            period_end=_START + timedelta(days=index + 1),
            quote_asset="USDT",
            realizations_count=3,
            winning_realizations_count=1,
            losing_realizations_count=1,
            breakeven_realizations_count=1,
            sessions_count=1,
            symbols_count=1,
            exit_notional=Decimal("123456789.12345678"),
            released_cost_basis=Decimal("120000000.12345678"),
            realized_fees=Decimal("1234.12345678"),
            realized_slippage_cost=Decimal("567.12345678"),
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            realized_pnl=gross_profit + gross_loss,
            win_rate_pct=Decimal(1) / Decimal(3) * Decimal(100),
            profit_factor=gross_profit / -gross_loss,
        )
        for index in range(MAX_PERIOD_BUCKETS)
    )


def _source_state(index: int) -> PaperPeriodSourceState:
    return PaperPeriodSourceState(
        session_id=hashlib.sha256(f"source-session-{index}".encode()).hexdigest(),
        config_checksum=hashlib.sha256(f"source-config-{index}".encode()).hexdigest(),
        state_id=hashlib.sha256(f"source-state-{index}".encode()).hexdigest(),
        state_checksum=hashlib.sha256(f"source-checksum-{index}".encode()).hexdigest(),
        base_asset="BTC",
        quote_asset="USDT",
        last_candle_open_time=(_START + timedelta(days=MAX_PERIOD_BUCKETS) - timedelta(minutes=1)),
        replayed_at=_START + timedelta(days=MAX_PERIOD_BUCKETS),
    )


def _period_series(*, combined_admin: bool) -> PaperPeriodMetricsSeries:
    filters = PaperPeriodMetricsFilter(
        quote_asset="USDT",
        period_from=_START,
        period_before=_START + timedelta(days=MAX_PERIOD_BUCKETS),
        session_id=None if combined_admin else _DIGESTS["session"],
        base_asset=None if combined_admin else "BTC",
        timeframe_code=None if combined_admin else "1m",
        strategy_name=None if combined_admin else "ema-cross-example",
        strategy_version=None if combined_admin else "1",
    )
    if combined_admin:
        source_states = tuple(
            sorted(
                (_source_state(index) for index in range(MAX_PERIOD_SOURCE_STATES)),
                key=lambda item: item.session_id,
            )
        )
    else:
        source_states = (
            PaperPeriodSourceState(
                session_id=_DIGESTS["session"],
                config_checksum=_DIGESTS["config"],
                state_id=_DIGESTS["state"],
                state_checksum=_DIGESTS["state_checksum"],
                base_asset="BTC",
                quote_asset="USDT",
                last_candle_open_time=(
                    _START + timedelta(days=MAX_PERIOD_BUCKETS) - timedelta(minutes=1)
                ),
                replayed_at=_START + timedelta(days=MAX_PERIOD_BUCKETS),
            ),
        )
    items = _period_buckets()
    totals = period_totals(items, source_states)
    query_checksum = period_query_checksum(PaperPeriodGranularity.DAILY, filters)
    content_checksum = period_content_checksum(
        PaperPeriodGranularity.DAILY,
        filters,
        source_states,
        items,
        totals,
        query_checksum,
    )
    return PaperPeriodMetricsSeries(
        granularity=PaperPeriodGranularity.DAILY,
        filters=filters,
        source_states=source_states,
        items=items,
        totals=totals,
        query_checksum=query_checksum,
        content_checksum=content_checksum,
    )


def _journal_page(tmp_path: Path) -> PaperTradePage:
    repository, session_id = _populated_repository(tmp_path)
    base_page = PaperTradeJournalReadService(
        repository,
        _state_verifier(tmp_path),
    ).list_trades(
        PaperTradeJournalFilter(session_id=session_id),
        page=1,
        page_size=2,
    )
    closed_template = next(record for record in base_page.items if record.trade.closed_at)
    records = tuple(
        replace(
            closed_template,
            trade=replace(
                closed_template.trade,
                sequence=index + 1,
                trade_id=_trade_id(
                    closed_template.trade.session_id,
                    index + 1,
                    closed_template.trade.entry_executions[0].fill_id,
                ),
            ),
        )
        for index in range(MAX_JOURNAL_PAGE)
    )
    records = tuple(
        sorted(
            records,
            key=lambda record: (
                record.trade.opened_at,
                record.session_id,
                record.trade.sequence,
                record.trade.trade_id,
            ),
            reverse=True,
        )
    )
    return PaperTradePage(
        filters=PaperTradeJournalFilter(session_id=session_id),
        items=records,
        page=1,
        page_size=MAX_JOURNAL_PAGE,
        total=MAX_JOURNAL_PAGE,
        total_pages=1,
        totals=journal_totals(records),
    )


def _authorize_http() -> None:
    app.dependency_overrides[require_administrator] = lambda: _AUTH_ID
    app.dependency_overrides[require_app_paper_session_reader] = lambda: _AUTH_ID
    app.dependency_overrides[get_authenticated_user] = lambda: _AUTH_ID


@pytest.mark.asyncio
async def test_candle_max_http_budgets_and_determinism() -> None:
    page = _candle_page()
    _authorize_http()
    app.dependency_overrides[get_market_candle_read_service] = _service_override(page)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            admin = await client.get(
                "/api/v1/admin/market-data/candles/BTC/USDT",
                params={"timeframe": "1m", "limit": MAX_CANDLES},
            )
            repeated = await client.get(
                "/api/v1/admin/market-data/candles/BTC/USDT",
                params={"timeframe": "1m", "limit": MAX_CANDLES},
            )
            user_app = await client.get(
                "/api/v1/app/market-data/candles/BTC/USDT",
                params={"timeframe": "1m", "limit": MAX_CANDLES},
            )
    finally:
        app.dependency_overrides.clear()

    assert admin.status_code == 200
    assert user_app.status_code == 200
    assert admin.json()["count"] == MAX_CANDLES
    assert len(admin.json()["items"]) == MAX_CANDLES
    assert user_app.json()["count"] == MAX_CANDLES
    assert len(user_app.json()["items"]) == MAX_CANDLES
    assert admin.content == repeated.content
    assert admin.content == user_app.content
    assert_response_budget(
        name="candles admin",
        response=admin,
        ceiling=CANDLES_ADMIN_BUDGET,
        cardinality="5000 candles",
    )
    assert_response_budget(
        name="candles app",
        response=user_app,
        ceiling=CANDLES_APP_BUDGET,
        cardinality="5000 candles",
    )


@pytest.mark.asyncio
async def test_annotation_max_http_budgets() -> None:
    page = _annotation_page()
    _authorize_http()
    app.dependency_overrides[get_paper_chart_annotation_read_service] = _service_override(page)
    path = f"/sessions/{_DIGESTS['session']}/chart-annotations"
    params = {
        "start": "2020-01-01T00:00:00Z",
        "before": "2020-01-02T00:00:00Z",
        "limit": MAX_ANNOTATIONS,
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            admin = await client.get(f"/api/v1/admin/paper-trading{path}", params=params)
            user_app = await client.get(f"/api/v1/app/paper-trading{path}", params=params)
    finally:
        app.dependency_overrides.clear()

    assert admin.status_code == 200
    assert user_app.status_code == 200
    assert admin.json()["count"] == MAX_ANNOTATIONS
    assert len(admin.json()["orders"]) == MAX_ANNOTATIONS // 2
    assert len(admin.json()["fills"]) == MAX_ANNOTATIONS // 2
    assert user_app.json()["count"] == MAX_ANNOTATIONS
    assert len(user_app.json()["orders"]) == MAX_ANNOTATIONS // 2
    assert len(user_app.json()["fills"]) == MAX_ANNOTATIONS // 2
    assert_response_budget(
        name="annotations admin",
        response=admin,
        ceiling=ANNOTATIONS_ADMIN_BUDGET,
        cardinality="2500 orders + 2500 fills",
    )
    assert_response_budget(
        name="annotations app",
        response=user_app,
        ceiling=ANNOTATIONS_APP_BUDGET,
        cardinality="2500 orders + 2500 fills",
    )


@pytest.mark.asyncio
async def test_timeline_max_http_budgets_and_determinism() -> None:
    page = _timeline_page()
    _authorize_http()
    app.dependency_overrides[get_paper_portfolio_timeline_read_service] = _service_override(page)
    path = f"/sessions/{_DIGESTS['session']}/portfolio-timeline"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            admin = await client.get(
                f"/api/v1/admin/paper-trading{path}",
                params={"limit": MAX_TIMELINE_OBSERVATIONS},
            )
            repeated = await client.get(
                f"/api/v1/admin/paper-trading{path}",
                params={"limit": MAX_TIMELINE_OBSERVATIONS},
            )
            user_app = await client.get(
                f"/api/v1/app/paper-trading{path}",
                params={"limit": MAX_TIMELINE_OBSERVATIONS},
            )
    finally:
        app.dependency_overrides.clear()

    assert admin.status_code == 200
    assert user_app.status_code == 200
    assert admin.json()["count"] == MAX_TIMELINE_OBSERVATIONS
    assert len(admin.json()["items"]) == MAX_TIMELINE_OBSERVATIONS
    assert user_app.json()["count"] == MAX_TIMELINE_OBSERVATIONS
    assert len(user_app.json()["items"]) == MAX_TIMELINE_OBSERVATIONS
    assert admin.content == repeated.content
    assert_response_budget(
        name="timeline admin",
        response=admin,
        ceiling=TIMELINE_ADMIN_BUDGET,
        cardinality="5000 observations",
    )
    assert_response_budget(
        name="timeline app",
        response=user_app,
        ceiling=TIMELINE_APP_BUDGET,
        cardinality="5000 observations",
    )


@pytest.mark.asyncio
async def test_journal_max_page_http_budgets(tmp_path: Path) -> None:
    page = _journal_page(tmp_path)
    session_id = page.filters.session_id
    assert session_id is not None
    _authorize_http()
    app.dependency_overrides[get_paper_trade_journal_read_service] = _service_override(page)
    params = {"page": 1, "page_size": MAX_JOURNAL_PAGE}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            admin = await client.get(
                "/api/v1/admin/paper-trading/journal",
                params={**params, "session_id": session_id},
            )
            user_app = await client.get(
                f"/api/v1/app/paper-trading/sessions/{session_id}/trades",
                params=params,
            )
    finally:
        app.dependency_overrides.clear()

    assert admin.status_code == 200
    assert user_app.status_code == 200
    assert len(admin.json()["items"]) == MAX_JOURNAL_PAGE
    assert len(user_app.json()["items"]) == MAX_JOURNAL_PAGE
    assert all(item["trade"]["entry_executions"] for item in admin.json()["items"])
    assert all(item["trade"]["exit_executions"] for item in admin.json()["items"])
    assert_response_budget(
        name="journal admin",
        response=admin,
        ceiling=JOURNAL_ADMIN_BUDGET,
        cardinality="100 complete closed trades",
    )
    assert_response_budget(
        name="journal app",
        response=user_app,
        ceiling=JOURNAL_APP_BUDGET,
        cardinality="100 complete closed trades",
    )


@pytest.mark.asyncio
async def test_period_max_http_budgets_combined_admin_and_determinism() -> None:
    app_series = _period_series(combined_admin=False)
    admin_series = _period_series(combined_admin=True)
    _authorize_http()
    session = SimpleNamespace(config=SimpleNamespace(pair=TradingPair("BTC", "USDT")))
    app.dependency_overrides[get_paper_trading_read_service] = _service_override(session)
    period_before = (_START + timedelta(days=MAX_PERIOD_BUCKETS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    admin_params = {
        "quote_asset": "USDT",
        "period_from": "2020-01-01T00:00:00Z",
        "period_before": period_before,
        "granularity": "DAILY",
    }
    app_params = {
        "period_from": "2020-01-01T00:00:00Z",
        "period_before": period_before,
        "granularity": "DAILY",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            app.dependency_overrides[get_paper_period_metrics_service] = _service_override(
                app_series
            )
            user_app = await client.get(
                f"/api/v1/app/paper-trading/sessions/{_DIGESTS['session']}/period-metrics",
                params=app_params,
            )
            app.dependency_overrides[get_paper_period_metrics_service] = _service_override(
                admin_series
            )
            admin = await client.get(
                "/api/v1/admin/paper-trading/period-metrics",
                params=admin_params,
            )
            repeated = await client.get(
                "/api/v1/admin/paper-trading/period-metrics",
                params=admin_params,
            )
    finally:
        app.dependency_overrides.clear()

    assert user_app.status_code == 200
    assert admin.status_code == 200
    assert len(user_app.json()["items"]) == MAX_PERIOD_BUCKETS
    assert len(admin.json()["items"]) == MAX_PERIOD_BUCKETS
    assert len(admin.json()["source_states"]) == MAX_PERIOD_SOURCE_STATES
    assert admin.content == repeated.content
    assert_response_budget(
        name="period app",
        response=user_app,
        ceiling=PERIOD_APP_BUDGET,
        cardinality="5000 active buckets",
    )
    actual_admin = assert_response_budget(
        name="period admin combined",
        response=admin,
        ceiling=PERIOD_ADMIN_BUDGET,
        cardinality="5000 active buckets + 10000 source states",
    )
    headroom = PERIOD_ADMIN_BUDGET - actual_admin
    headroom_pct = headroom / actual_admin * 100
    assert headroom_pct >= 10, (
        "period admin combined budget lacks the approved headroom: "
        f"actual={actual_admin} bytes, ceiling={PERIOD_ADMIN_BUDGET} bytes, "
        f"headroom={headroom} bytes, headroom_pct={headroom_pct:.2f}%"
    )
