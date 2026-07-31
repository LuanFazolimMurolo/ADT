"""Strategy boundary tests proving bounded immutable history."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal

import pytest

from app.backtesting.domain import PortfolioSnapshot
from app.backtesting.strategy import (
    BuyAndHoldExample,
    NoOpStrategy,
    ScriptedStrategy,
    StrategyContext,
)
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import DataRange
from tests.market_data_helpers import candle, utc


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        quote_cash=Decimal("1000"),
        base_quantity=Decimal("0"),
        average_entry_price=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        drawdown=Decimal("0"),
    )


def _snapshot() -> DatasetSnapshot:
    return DatasetSnapshot(
        snapshot_id="a" * 64,
        dataset_key="derived:test",
        dataset_version="b" * 64,
        checksum="c" * 64,
        data_range=DataRange(utc(2026, 1, 1), utc(2026, 1, 2)),
        partitions=("partitions/year=2026/month=01/candles.parquet",),
        manifest_path="dataset-manifest.json",
        created_at=utc(2026, 1, 1).isoformat(),
    )


def _context(index: int = 0) -> StrategyContext:
    current = candle(utc(2026, 1, 1) + index * timedelta(hours=1))
    return StrategyContext(
        snapshot=_snapshot(),
        candle_index=index,
        current_candle=current,
        history=(current,),
        portfolio=_portfolio(),
        open_orders=(),
        last_fill=None,
        risk_halt=False,
    )


def test_context_rejects_future_history_and_is_frozen() -> None:
    current = candle(utc(2026, 1, 1))
    future = candle(utc(2026, 1, 1) + timedelta(hours=1))
    with pytest.raises(ValueError, match="current candle"):
        StrategyContext(
            _snapshot(),
            0,
            current,
            (current, future),
            _portfolio(),
            (),
            None,
            False,
        )

    context = _context()
    with pytest.raises(FrozenInstanceError):
        context.risk_halt = True  # type: ignore[misc]


def test_noop_and_buy_hold_example_are_deterministic() -> None:
    context = _context()
    no_op = NoOpStrategy()
    assert (
        no_op.on_start(StrategyContext(_snapshot(), -1, None, (), _portfolio(), (), None, False))
        == ()
    )
    assert no_op.on_candle(context, context.current_candle) == ()

    buy_hold = BuyAndHoldExample(Decimal("0.5"))
    buy_hold.on_start(StrategyContext(_snapshot(), -1, None, (), _portfolio(), (), None, False))
    first = buy_hold.on_candle(context, context.current_candle)
    second = buy_hold.on_candle(context, context.current_candle)
    assert len(first) == 1
    assert first[0].quantity == Decimal("0.5")
    assert second == ()


def test_scripted_strategy_returns_only_the_current_index_events() -> None:
    buy_hold = BuyAndHoldExample(Decimal("1"))
    intent = buy_hold.on_candle(_context(), _context().current_candle)[0]
    scripted = ScriptedStrategy(candle_intents=((2, (intent,)),))

    assert scripted.on_candle(_context(0), _context(0).current_candle) == ()
    assert scripted.on_candle(_context(2), _context(2).current_candle) == (intent,)
