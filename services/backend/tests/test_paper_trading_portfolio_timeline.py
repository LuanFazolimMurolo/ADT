"""Deterministic paper-portfolio timeline reconstruction regressions."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import OrderSide
from app.paper_trading.domain import (
    PaperCandleBatch,
    build_paper_session_state,
    paper_session_id,
)
from app.paper_trading.errors import PaperSessionVerificationError
from app.paper_trading.portfolio_timeline import (
    build_paper_portfolio_timeline,
    validate_paper_portfolio_timeline,
)
from tests.test_paper_trading import FakeSource, _candle
from tests.test_paper_trading_journal import (
    _intent,
    _journal_config,
    _service,
)


def test_timeline_reconstructs_every_close_and_final_portfolio(
    tmp_path: Path,
) -> None:
    source = FakeSource(
        tuple(
            _candle(index, close)
            for index, close in enumerate(("100", "110", "120", "130", "140", "150", "160"))
        )
    )
    schedule = (
        (0, (_intent(OrderSide.BUY, "2", "entry-a"),)),
        (1, (_intent(OrderSide.BUY, "1", "entry-b"),)),
        (2, (_intent(OrderSide.SELL, "1", "partial-exit"),)),
        (3, (_intent(OrderSide.SELL, "2", "close"),)),
        (4, (_intent(OrderSide.BUY, "0.5", "second-entry"),)),
    )
    service = _service(tmp_path, source, schedule)
    config = _journal_config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    batch = source.load(config, end=state.data_range.end)

    first = build_paper_portfolio_timeline(config, batch, state)
    second = build_paper_portfolio_timeline(config, batch, state)

    assert first == second
    validate_paper_portfolio_timeline(first)
    assert len(first.timeline_id) == 64
    assert len(first.content_checksum) == 64
    assert first.session_id == state.session_id
    assert first.state_id == state.state_id
    assert first.state_checksum == state.checksum
    assert first.dataset_version == state.dataset_version
    assert first.source_checksum == state.source_checksum
    assert first.candles_processed == state.candles_processed
    assert len(first.observations) == state.candles_processed
    assert [item.candle_index for item in first.observations] == list(
        range(state.candles_processed)
    )

    for point, candle in zip(first.observations, source.candles, strict=True):
        assert point.session_id == state.session_id
        assert point.state_id == state.state_id
        assert point.source_checksum == state.source_checksum
        assert point.candle_open_time == candle.open_time
        assert point.candle_close_time == candle.close_time
        assert point.mark_price == candle.close

    last = first.observations[-1]
    assert last.portfolio == state.portfolio
    assert last.risk_halt is state.risk_halt
    assert last.total_fees == state.portfolio.total_fees
    assert last.total_slippage_cost == state.portfolio.total_slippage_cost
    assert last.realized_pnl == state.portfolio.realized_pnl
    assert last.unrealized_pnl == state.portfolio.unrealized_pnl
    assert last.cost_basis == state.portfolio.cost_basis


def test_timeline_without_fills_remains_flat(tmp_path: Path) -> None:
    source = FakeSource((_candle(0, "100"), _candle(1, "90")))
    service = _service(tmp_path, source, ())
    config = _journal_config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    batch = source.load(config, end=state.data_range.end)

    timeline = build_paper_portfolio_timeline(config, batch, state)

    assert state.fills == ()
    assert len(timeline.observations) == 2
    assert all(point.quote_cash == config.initial_capital for point in timeline.observations)
    assert all(point.base_quantity == 0 for point in timeline.observations)
    assert all(point.cost_basis == 0 for point in timeline.observations)
    assert all(point.realized_pnl == 0 for point in timeline.observations)
    assert all(point.unrealized_pnl == 0 for point in timeline.observations)
    assert all(point.equity == config.initial_capital for point in timeline.observations)
    assert all(not point.risk_halt for point in timeline.observations)


def test_timeline_reconstructs_drawdown_halt_at_closed_candle(
    tmp_path: Path,
) -> None:
    source = FakeSource(
        (
            _candle(0, "100"),
            _candle(1, "100"),
            _candle(2, "50"),
        )
    )
    schedule = ((0, (_intent(OrderSide.BUY, "1", "entry"),)),)
    base_config = _journal_config()
    config = replace(
        base_config,
        risk_limits=replace(
            base_config.risk_limits,
            max_drawdown_pct=Decimal("0.1"),
            stop_on_max_drawdown=True,
        ),
    )
    service = _service(tmp_path, source, schedule)
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    batch = source.load(config, end=state.data_range.end)

    timeline = build_paper_portfolio_timeline(config, batch, state)

    assert not timeline.observations[0].risk_halt
    assert not timeline.observations[1].risk_halt
    assert timeline.observations[2].risk_halt
    assert timeline.observations[2].drawdown_pct >= Decimal("0.1")
    assert timeline.observations[-1].risk_halt is state.risk_halt


def test_timeline_rejects_source_identity_divergence(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source, ())
    config = _journal_config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    batch = source.load(config, end=state.data_range.end)
    forged = PaperCandleBatch(
        data_range=batch.data_range,
        dataset_version=batch.dataset_version,
        source_checksum="0" * 64,
        candles=batch.candles,
    )

    with pytest.raises(PaperSessionVerificationError):
        build_paper_portfolio_timeline(config, forged, state)


def test_timeline_rejects_resigned_final_accounting_divergence(
    tmp_path: Path,
) -> None:
    source = FakeSource((_candle(0), _candle(1), _candle(2)))
    service = _service(
        tmp_path,
        source,
        ((0, (_intent(OrderSide.BUY, "1", "entry"),)),),
    )
    config = _journal_config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    batch = source.load(config, end=state.data_range.end)
    divergent_portfolio = replace(
        state.portfolio,
        total_fees=state.portfolio.total_fees + Decimal("1"),
    )
    resigned = build_paper_session_state(
        config=config,
        batch=batch,
        candles_processed=state.candles_processed,
        orders=state.orders,
        fills=state.fills,
        portfolio=divergent_portfolio,
        risk_halt=state.risk_halt,
        replayed_at=state.replayed_at,
        latest_market_regime=state.latest_market_regime,
    )

    with pytest.raises(PaperSessionVerificationError):
        build_paper_portfolio_timeline(config, batch, resigned)
