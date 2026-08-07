from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    Fill,
    FillLiquidity,
    FillReason,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    SimulatedOrder,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
    StrategyDescriptor,
    TimeInForce,
)
from app.market_data.domain import (
    Candle,
    DataRange,
    Exchange,
    MarketType,
    TradingPair,
)
from app.market_data.timeframes import get_timeframe
from app.paper_trading.chart_annotations import (
    ENGINE_STOP_LOSS_CLIENT_TAG,
    PaperChartAnnotationQuery,
    PaperChartAnnotationReadService,
    PaperChartFillRole,
)
from app.paper_trading.domain import (
    PaperCandleBatch,
    PaperSessionConfig,
    build_paper_session_state,
    paper_session_id,
)
from app.paper_trading.errors import InvalidPaperSessionError
from app.paper_trading.repository import PaperTradingRepository


def _config() -> PaperSessionConfig:
    return PaperSessionConfig(
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1m"),
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        warmup_candles=0,
        strategy=StrategyDescriptor(
            "ema-cross-example",
            "1",
            (
                ("fast_period", 3),
                ("quantity", Decimal("1")),
                ("slow_period", 5),
            ),
        ),
        strategy_lifecycle_version=1,
        initial_capital=Decimal("1000"),
        execution=ExecutionAssumptions(
            fees=FeeModel(Decimal("0"), Decimal("0")),
            slippage=SlippageModel(),
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("0"),
        ),
        risk_limits=StopLossRiskLimits(
            stop_loss=StopLossPolicy(
                StopLossKind.FIXED_PERCENT,
                Decimal("5"),
            )
        ),
        history_window=10,
        max_candles=100,
        max_orders=100,
        max_events=1_000,
        engine_version="1",
        schema_version=1,
    )


def _candle(open_time: datetime, close: Decimal) -> Candle:
    timeframe = get_timeframe("1m")
    return Candle(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTC/USDT",
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
        quote_volume=None,
        trade_count=None,
        is_closed=True,
        source="test",
    )


def _state(config: PaperSessionConfig):
    start = config.start_at
    fill_time = start + timedelta(minutes=1)
    entry_order = SimulatedOrder(
        order_id="O000000000001",
        created_sequence=1,
        created_at=fill_time,
        created_candle_index=0,
        eligible_candle_index=1,
        intent=OrderIntent(
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            time_in_force=TimeInForce.GTC,
            client_tag="ema-cross-entry",
        ),
        status=OrderStatus.FILLED,
        opened_at=fill_time,
        terminal_at=fill_time,
    )
    protective_order = SimulatedOrder(
        order_id="O000000000002",
        created_sequence=2,
        created_at=fill_time,
        created_candle_index=0,
        eligible_candle_index=1,
        intent=OrderIntent(
            side=OrderSide.SELL,
            order_type=OrderType.STOP_MARKET,
            quantity=Decimal("1"),
            time_in_force=TimeInForce.GTC,
            stop_price=Decimal("95"),
            client_tag=ENGINE_STOP_LOSS_CLIENT_TAG,
        ),
        status=OrderStatus.OPEN,
        opened_at=fill_time,
    )
    fill = Fill(
        fill_id="F000000000001",
        order_id=entry_order.order_id,
        reason=FillReason.MARKET_OPEN,
        liquidity=FillLiquidity.TAKER,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        base_price=Decimal("100"),
        execution_price=Decimal("100"),
        notional=Decimal("100"),
        fee=Decimal("0"),
        slippage_cost=Decimal("0"),
        event_time=fill_time,
        candle_index=1,
    )
    candles = (
        _candle(start, Decimal("100")),
        _candle(fill_time, Decimal("101")),
    )
    batch = PaperCandleBatch(
        data_range=DataRange(start, start + timedelta(minutes=2)),
        dataset_version="a" * 64,
        source_checksum="b" * 64,
        candles=candles,
    )
    return build_paper_session_state(
        config=config,
        batch=batch,
        candles_processed=2,
        orders=(entry_order, protective_order),
        fills=(fill,),
        portfolio=PortfolioSnapshot(
            quote_cash=Decimal("900"),
            base_quantity=Decimal("1"),
            average_entry_price=Decimal("100"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("1"),
            total_fees=Decimal("0"),
            total_slippage_cost=Decimal("0"),
            equity=Decimal("1001"),
            peak_equity=Decimal("1001"),
            drawdown=Decimal("0"),
            cost_basis=Decimal("100"),
            drawdown_pct=Decimal("0"),
        ),
        risk_halt=False,
        replayed_at=start + timedelta(minutes=2),
    )


def _service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PaperChartAnnotationReadService, PaperSessionConfig]:
    config = _config()
    state = _state(config)
    repository = PaperTradingRepository(tmp_path)
    monkeypatch.setattr(repository, "load_config", lambda _session_id: config)
    monkeypatch.setattr(repository, "load_state", lambda _session_id: state)
    return PaperChartAnnotationReadService(repository), config


def test_projects_verified_entries_and_engine_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, config = _service(tmp_path, monkeypatch)
    start = config.start_at
    page = service.read_page(
        PaperChartAnnotationQuery(
            session_id=paper_session_id(config),
            range_start=start,
            range_end=start + timedelta(minutes=2),
            limit=3,
        )
    )

    assert page.count == 3
    assert page.ema_fast_period == 3
    assert page.ema_slow_period == 5
    assert page.fills[0].role is PaperChartFillRole.ENTRY
    assert page.fills[0].trade_sequence == 1
    assert page.orders[1].is_engine_protective_stop is True
    assert page.orders[1].stop_price == Decimal("95")
    assert page.content_checksum != "0" * 64


def test_rejects_interval_that_exceeds_explicit_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, config = _service(tmp_path, monkeypatch)
    with pytest.raises(InvalidPaperSessionError, match="limite de 2"):
        service.read_page(
            PaperChartAnnotationQuery(
                session_id=paper_session_id(config),
                range_start=config.start_at,
                range_end=config.start_at + timedelta(minutes=2),
                limit=2,
            )
        )


def test_half_open_range_excludes_events_at_before(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, config = _service(tmp_path, monkeypatch)
    page = service.read_page(
        PaperChartAnnotationQuery(
            session_id=paper_session_id(config),
            range_start=config.start_at,
            range_end=config.start_at + timedelta(minutes=1),
            limit=10,
        )
    )

    assert page.count == 0
