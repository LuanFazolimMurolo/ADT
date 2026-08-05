"""Deterministic trade-cycle reconstruction regressions for paper trading."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    OrderIntent,
    OrderSide,
    OrderType,
    SlippageModel,
    StrategyDescriptor,
    StrategyParameterValue,
)
from app.backtesting.strategy import ScriptedStrategy
from app.paper_trading.domain import (
    PaperSessionConfig,
    build_paper_session_state,
    paper_session_id,
)
from app.paper_trading.errors import PaperSessionVerificationError
from app.paper_trading.journal import (
    PaperTradeStatus,
    build_paper_trade_journal,
)
from app.paper_trading.service import PaperTradingService
from app.strategies.domain import StrategyPluginDescriptor
from app.strategies.registry import StrategyPluginRegistry
from tests.test_paper_trading import FakeSource, _candle, _config

_DESCRIPTOR = StrategyDescriptor("paper-journal-test", "1")


@dataclass(frozen=True, slots=True)
class _JournalPlugin:
    candle_intents: tuple[tuple[int, tuple[OrderIntent, ...]], ...]
    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        name="paper-journal-test",
        version="1",
        description="Test-only scripted journal strategy.",
        parameters=(),
    )

    def build(
        self,
        parameters: tuple[tuple[str, StrategyParameterValue], ...],
    ) -> ScriptedStrategy:
        if parameters:
            raise ValueError("journal test strategy does not accept parameters")
        return ScriptedStrategy(
            candle_intents=self.candle_intents,
            descriptor=_DESCRIPTOR,
        )


def _intent(
    side: OrderSide,
    quantity: str,
    tag: str,
) -> OrderIntent:
    return OrderIntent(
        side=side,
        order_type=OrderType.MARKET,
        quantity=Decimal(quantity),
        client_tag=tag,
    )


def _service(
    tmp_path: Path,
    source: FakeSource,
    candle_intents: tuple[tuple[int, tuple[OrderIntent, ...]], ...],
) -> PaperTradingService:
    return PaperTradingService(
        tmp_path,
        source=source,
        registry=StrategyPluginRegistry((_JournalPlugin(candle_intents),)),
        clock=lambda: datetime(2026, 8, 5, tzinfo=UTC),
    )


def _journal_config() -> PaperSessionConfig:
    return replace(
        _config(),
        strategy=_DESCRIPTOR,
        initial_capital=Decimal("10000"),
        execution=ExecutionAssumptions(
            fees=FeeModel(Decimal("0.5"), Decimal("1")),
            slippage=SlippageModel(fixed_bps=Decimal("0.5")),
            force_close_at_end=False,
        ),
    )


def test_journal_reconstructs_partial_exits_closed_trade_and_open_trade(
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
    service.create(config)
    state = service.run_once(paper_session_id(config)).state

    journal = build_paper_trade_journal(config, state)

    assert build_paper_trade_journal(config, state) == journal
    assert journal.state_id == state.state_id
    assert journal.state_checksum == state.checksum
    assert len(journal.executions) == 5
    assert journal.closed_trades_count == 1
    assert journal.open_trades_count == 1
    assert journal.total_realized_pnl == state.portfolio.realized_pnl
    assert journal.total_unrealized_pnl == state.portfolio.unrealized_pnl
    assert journal.total_fees == state.portfolio.total_fees
    assert journal.total_slippage_cost == state.portfolio.total_slippage_cost

    closed, opened = journal.trades
    assert closed.status is PaperTradeStatus.CLOSED
    assert closed.opened_quantity == Decimal("3")
    assert closed.closed_quantity == Decimal("3")
    assert closed.remaining_quantity == 0
    assert closed.remaining_cost_basis == 0
    assert closed.closed_at == closed.exit_executions[-1].event_time
    assert [item.client_tag for item in closed.entry_executions] == ["entry-a", "entry-b"]
    assert [item.client_tag for item in closed.exit_executions] == ["partial-exit", "close"]
    assert closed.realized_pnl > 0

    assert opened.status is PaperTradeStatus.OPEN
    assert opened.remaining_quantity == Decimal("0.5")
    assert opened.closed_quantity == 0
    assert opened.closed_at is None
    assert (
        opened.mark_price
        == (state.portfolio.equity - state.portfolio.quote_cash) / state.portfolio.base_quantity
    )
    assert opened.unrealized_pnl == state.portfolio.unrealized_pnl
    assert opened.entry_executions[0].client_tag == "second-entry"
    assert closed.trade_id != opened.trade_id


def test_journal_handles_verified_session_without_executions(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source, ())
    config = _journal_config()
    service.create(config)
    state = service.run_once(paper_session_id(config)).state

    journal = build_paper_trade_journal(config, state)

    assert journal.executions == ()
    assert journal.trades == ()
    assert journal.closed_trades_count == 0
    assert journal.open_trades_count == 0
    assert journal.total_realized_pnl == 0
    assert journal.total_unrealized_pnl == 0
    assert journal.total_net_pnl == 0
    assert journal.total_fees == 0
    assert journal.total_slippage_cost == 0


def test_journal_rejects_fill_that_diverges_from_its_order(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1), _candle(2)))
    service = _service(
        tmp_path,
        source,
        ((0, (_intent(OrderSide.BUY, "1", "entry"),)),),
    )
    config = _journal_config()
    service.create(config)
    state = service.run_once(paper_session_id(config)).state
    original_order = state.orders[0]
    divergent_order = replace(
        original_order,
        intent=replace(original_order.intent, side=OrderSide.SELL),
    )
    batch = source.load(config, end=state.data_range.end)
    resigned = build_paper_session_state(
        config=config,
        batch=batch,
        candles_processed=state.candles_processed,
        orders=(divergent_order,),
        fills=state.fills,
        portfolio=state.portfolio,
        risk_halt=state.risk_halt,
        replayed_at=state.replayed_at,
        latest_market_regime=state.latest_market_regime,
    )

    with pytest.raises(PaperSessionVerificationError):
        build_paper_trade_journal(config, resigned)


def test_journal_rejects_resigned_portfolio_accounting_divergence(
    tmp_path: Path,
) -> None:
    source = FakeSource((_candle(0), _candle(1), _candle(2)))
    service = _service(
        tmp_path,
        source,
        ((0, (_intent(OrderSide.BUY, "1", "entry"),)),),
    )
    config = _journal_config()
    service.create(config)
    state = service.run_once(paper_session_id(config)).state
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
        build_paper_trade_journal(config, resigned)
