"""Read-model regressions for the paper-trading performance dashboard."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.indicators.regime import MarketRegimePolicy
from app.paper_trading.continuous import (
    PaperRunnerCycleStatus,
    PaperRunnerPolicy,
    PaperRunnerSessionResult,
    PaperRunnerSessionStatus,
    PaperRunnerState,
)
from app.paper_trading.dashboard import PaperDashboardReadService
from app.paper_trading.domain import paper_session_id
from app.paper_trading.errors import InvalidPaperSessionError
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading import FakeSource, _candle, _config, _service


def _regime_policy() -> MarketRegimePolicy:
    return MarketRegimePolicy(
        fast_ema_period=2,
        slow_ema_period=3,
        atr_period=2,
        volatile_atr_ratio=Decimal("0.5"),
        trend_strength_threshold=Decimal("0.1"),
    )


def _runner_state(
    session_id: str,
    *,
    state_id: str,
    candles_processed: int,
    last_candle_open_time: datetime,
) -> PaperRunnerState:
    started_at = datetime(2026, 8, 2, tzinfo=UTC)
    result = PaperRunnerSessionResult(
        session_id=session_id,
        status=PaperRunnerSessionStatus.UPDATED,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        state_id=state_id,
        candles_processed=candles_processed,
        last_candle_open_time=last_candle_open_time,
    )
    return PaperRunnerState(
        cycle_index=7,
        status=PaperRunnerCycleStatus.COMPLETED,
        policy=PaperRunnerPolicy(interval_seconds=30, max_sessions=10),
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        next_cycle_at=started_at + timedelta(seconds=30),
        results=(result,),
    )


def test_dashboard_projects_verified_metrics_position_regime_and_runner(
    tmp_path: Path,
) -> None:
    candles = tuple(
        _candle(index, close) for index, close in enumerate(("100", "105", "110", "120"))
    )
    source = FakeSource(candles)
    service = _service(tmp_path, source)
    config = replace(
        _config(),
        market_regime_policy=_regime_policy(),
        schema_version=2,
    )
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    runner = _runner_state(
        session_id,
        state_id=state.state_id,
        candles_processed=state.candles_processed,
        last_candle_open_time=state.last_candle_open_time,
    )

    page = PaperDashboardReadService(PaperTradingRepository(tmp_path)).build_page(
        page=1,
        page_size=20,
        runner_state=runner,
    )

    assert page.total == 1
    assert page.runner_cycle_index == 7
    assert page.runner_cycle_status is PaperRunnerCycleStatus.COMPLETED
    item = page.items[0]
    assert item.session_id == session_id
    assert item.metrics is not None
    assert item.metrics.total_pnl == state.portfolio.equity - config.initial_capital
    assert item.metrics.return_pct == (
        item.metrics.total_pnl / config.initial_capital * Decimal("100")
    )
    assert item.position is not None
    assert item.position.is_open is (state.portfolio.base_quantity > 0)
    assert item.position.market_value == state.portfolio.equity - state.portfolio.quote_cash
    assert item.latest_market_regime == state.latest_market_regime
    assert item.runner is not None
    assert item.runner.status is PaperRunnerSessionStatus.UPDATED
    assert item.runner.matches_current_state is True
    assert page.totals.configured_capital == config.initial_capital
    assert page.totals.initialized_capital == config.initial_capital
    assert page.totals.equity == state.portfolio.equity
    assert page.totals.total_pnl == item.metrics.total_pnl


def test_dashboard_keeps_pending_capital_separate_from_initialized_performance(
    tmp_path: Path,
) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    initialized = _config()
    pending = replace(_config(), initial_capital=Decimal("2500"))
    service.create(initialized)
    state = service.run_once(paper_session_id(initialized)).state
    service.create(pending)

    page = PaperDashboardReadService(PaperTradingRepository(tmp_path)).build_page(
        page=1,
        page_size=20,
    )

    assert page.totals.sessions_count == 2
    assert page.totals.initialized_count == 1
    assert page.totals.pending_count == 1
    assert page.totals.configured_capital == Decimal("3500")
    assert page.totals.initialized_capital == Decimal("1000")
    assert page.totals.equity == state.portfolio.equity
    pending_item = next(item for item in page.items if not item.state_available)
    assert pending_item.initial_capital == Decimal("2500")
    assert pending_item.metrics is None
    assert pending_item.position is None


def test_dashboard_joins_failed_runner_without_inventing_session_state(
    tmp_path: Path,
) -> None:
    repository = PaperTradingRepository(tmp_path)
    config = repository.create(_config())
    session_id = paper_session_id(config)
    started_at = datetime(2026, 8, 2, tzinfo=UTC)
    runner = PaperRunnerState(
        cycle_index=1,
        status=PaperRunnerCycleStatus.FAILED,
        policy=PaperRunnerPolicy(interval_seconds=60, max_sessions=1),
        started_at=started_at,
        finished_at=started_at,
        next_cycle_at=started_at + timedelta(seconds=60),
        results=(
            PaperRunnerSessionResult(
                session_id=session_id,
                status=PaperRunnerSessionStatus.FAILED,
                started_at=started_at,
                finished_at=started_at,
                error_code="paper_session_data_unavailable",
            ),
        ),
    )

    page = PaperDashboardReadService(repository).build_page(
        page=1,
        page_size=10,
        runner_state=runner,
    )

    item = page.items[0]
    assert item.state_available is False
    assert item.runner is not None
    assert item.runner.status is PaperRunnerSessionStatus.FAILED
    assert item.runner.error_code == "paper_session_data_unavailable"
    assert item.runner.matches_current_state is False
    assert page.totals.runner_failed_count == 1


def test_dashboard_rejects_successful_runner_without_persisted_state(
    tmp_path: Path,
) -> None:
    repository = PaperTradingRepository(tmp_path)
    config = repository.create(_config())
    session_id = paper_session_id(config)
    runner = _runner_state(
        session_id,
        state_id="a" * 64,
        candles_processed=1,
        last_candle_open_time=config.start_at,
    )

    with pytest.raises(InvalidPaperSessionError):
        PaperDashboardReadService(repository).build_page(
            page=1,
            page_size=10,
            runner_state=runner,
        )


def test_dashboard_page_is_bounded_and_deterministically_sorted(tmp_path: Path) -> None:
    repository = PaperTradingRepository(tmp_path)
    for capital in ("1000", "2000", "3000"):
        repository.create(replace(_config(), initial_capital=Decimal(capital)))

    first = PaperDashboardReadService(repository).build_page(page=1, page_size=2)
    second = PaperDashboardReadService(repository).build_page(page=2, page_size=2)

    assert first.total == 3
    assert first.total_pages == 2
    assert len(first.items) == 2
    assert len(second.items) == 1
    assert tuple(item.session_id for item in first.items) == tuple(
        sorted(item.session_id for item in first.items)
    )
    assert set(item.session_id for item in first.items).isdisjoint(
        item.session_id for item in second.items
    )


@pytest.mark.parametrize(
    ("page", "page_size"),
    ((0, 10), (1, 0), (1, 101), (100_001, 10)),
)
def test_dashboard_rejects_unbounded_pagination(
    tmp_path: Path,
    page: int,
    page_size: int,
) -> None:
    service = PaperDashboardReadService(PaperTradingRepository(tmp_path))

    with pytest.raises(InvalidPaperSessionError):
        service.build_page(page=page, page_size=page_size)
