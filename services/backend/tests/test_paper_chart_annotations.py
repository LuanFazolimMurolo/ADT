from __future__ import annotations

import hashlib
from dataclasses import replace
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
from app.backtesting.serialization import canonical_json_bytes
from app.market_data.domain import (
    Candle,
    DataRange,
    Exchange,
    MarketType,
    TradingPair,
)
from app.market_data.storage import canonical_candle_bytes
from app.market_data.timeframes import get_timeframe
from app.paper_trading.chart_annotations import (
    ENGINE_STOP_LOSS_CLIENT_TAG,
    PaperChartAnnotationQuery,
    PaperChartAnnotationReadService,
    PaperChartFillRole,
)
from app.paper_trading.documents import encode_paper_state
from app.paper_trading.domain import (
    PaperCandleBatch,
    PaperSessionConfig,
    PaperSessionState,
    build_paper_session_state,
    paper_session_id,
    paper_state_id_from_payload,
    paper_state_semantic_payload,
)
from app.paper_trading.errors import InvalidPaperSessionError, PaperSessionVerificationError
from app.paper_trading.persisted_state import PaperPersistedStateVerifier
from app.paper_trading.portfolio_timeline import build_paper_portfolio_timeline
from app.paper_trading.portfolio_timeline_artifacts import (
    PaperPortfolioTimelineArtifactStore,
)
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
    source = hashlib.sha256()
    for item in candles:
        source.update(canonical_candle_bytes(item))
    batch = PaperCandleBatch(
        data_range=DataRange(start, start + timedelta(minutes=2)),
        dataset_version="a" * 64,
        source_checksum=source.hexdigest(),
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


def _publish_binding(
    tmp_path: Path,
    config: PaperSessionConfig,
    state: PaperSessionState,
) -> PaperPersistedStateVerifier:
    batch = PaperCandleBatch(
        data_range=state.data_range,
        dataset_version=state.dataset_version,
        source_checksum=state.source_checksum,
        candles=(
            _candle(config.start_at, Decimal("100")),
            _candle(config.start_at + timedelta(minutes=1), Decimal("101")),
        ),
    )
    store = PaperPortfolioTimelineArtifactStore(tmp_path)
    store.publish(build_paper_portfolio_timeline(config, batch, state))
    return PaperPersistedStateVerifier(store)


def _resign_state(
    state: PaperSessionState,
    *,
    dataset_version: str | None = None,
    source_checksum: str | None = None,
) -> PaperSessionState:
    semantic = paper_state_semantic_payload(state)
    if dataset_version is not None:
        semantic["dataset_version"] = dataset_version
    if source_checksum is not None:
        semantic["source_checksum"] = source_checksum
    state_id = paper_state_id_from_payload(semantic)
    checksum = hashlib.sha256(
        canonical_json_bytes(
            {
                **semantic,
                "replayed_at": state.replayed_at.isoformat(),
                "state_id": state_id,
            }
        )
    ).hexdigest()
    return replace(
        state,
        dataset_version=dataset_version or state.dataset_version,
        source_checksum=source_checksum or state.source_checksum,
        state_id=state_id,
        checksum=checksum,
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
    return (
        PaperChartAnnotationReadService(repository, _publish_binding(tmp_path, config, state)),
        config,
    )


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


def test_identical_annotation_reads_preserve_complete_projection_and_source_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, config = _service(tmp_path, monkeypatch)
    query = PaperChartAnnotationQuery(
        session_id=paper_session_id(config),
        range_start=config.start_at,
        range_end=config.start_at + timedelta(minutes=2),
        limit=3,
    )

    first = service.read_page(query)
    second = service.read_page(query)
    source_state = _state(config)

    assert second == first
    assert tuple(item.order_id for item in first.orders) == tuple(
        item.order_id for item in source_state.orders
    )
    assert tuple(item.fill_id for item in first.fills) == tuple(
        item.fill_id for item in source_state.fills
    )
    assert first.fills[0].trade_id == second.fills[0].trade_id
    assert first.fills[0].trade_sequence == second.fills[0].trade_sequence


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


def test_rejects_resigned_state_bound_to_another_dataset_version(tmp_path: Path) -> None:
    config = _config()
    original = _state(config)
    session_id = paper_session_id(config)
    repository = PaperTradingRepository(tmp_path)
    repository.create(config)
    with repository.lock(session_id) as lease:
        repository.publish_state(config, original, lease=lease)
    verifier = _publish_binding(tmp_path, config, original)

    tampered_batch = PaperCandleBatch(
        data_range=original.data_range,
        dataset_version="c" * 64,
        source_checksum=original.source_checksum,
        candles=(
            _candle(config.start_at, Decimal("100")),
            _candle(config.start_at + timedelta(minutes=1), Decimal("101")),
        ),
    )
    resigned = build_paper_session_state(
        config=config,
        batch=tampered_batch,
        candles_processed=original.candles_processed,
        orders=original.orders,
        fills=original.fills,
        portfolio=original.portfolio,
        risk_halt=original.risk_halt,
        replayed_at=original.replayed_at,
    )
    state_path = tmp_path / "market" / "paper-trading" / session_id / "state.json"
    state_path.write_bytes(encode_paper_state(resigned))

    service = PaperChartAnnotationReadService(repository, verifier)
    with pytest.raises(PaperSessionVerificationError):
        service.read_page(
            PaperChartAnnotationQuery(
                session_id=session_id,
                range_start=config.start_at,
                range_end=config.start_at + timedelta(minutes=2),
                limit=3,
            )
        )


def test_rejects_resigned_state_bound_to_another_source_checksum(tmp_path: Path) -> None:
    config = _config()
    original = _state(config)
    session_id = paper_session_id(config)
    repository = PaperTradingRepository(tmp_path)
    repository.create(config)
    with repository.lock(session_id) as lease:
        repository.publish_state(config, original, lease=lease)
    verifier = _publish_binding(tmp_path, config, original)
    resigned = _resign_state(original, source_checksum="d" * 64)
    state_path = tmp_path / "market" / "paper-trading" / session_id / "state.json"
    state_path.write_bytes(encode_paper_state(resigned))

    with pytest.raises(PaperSessionVerificationError):
        PaperChartAnnotationReadService(repository, verifier).read_page(
            PaperChartAnnotationQuery(
                session_id=session_id,
                range_start=config.start_at,
                range_end=config.start_at + timedelta(minutes=2),
                limit=3,
            )
        )


def test_annotations_reject_state_without_persisted_reference(tmp_path: Path) -> None:
    config = _config()
    state = _state(config)
    session_id = paper_session_id(config)
    repository = PaperTradingRepository(tmp_path)
    repository.create(config)
    with repository.lock(session_id) as lease:
        repository.publish_state(config, state, lease=lease)
    verifier = _publish_binding(tmp_path, config, state)
    reference = (
        tmp_path
        / "market"
        / "paper-trading"
        / session_id
        / "portfolio-timeline-refs"
        / f"{state.checksum}.json"
    )
    reference.unlink()

    with pytest.raises(PaperSessionVerificationError):
        PaperChartAnnotationReadService(repository, verifier).read_page(
            PaperChartAnnotationQuery(
                session_id=session_id,
                range_start=config.start_at,
                range_end=config.start_at + timedelta(minutes=2),
                limit=3,
            )
        )
