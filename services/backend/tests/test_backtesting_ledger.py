"""Append-only hash-chain ledger tests."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.backtesting.domain import Fill, FillLiquidity, FillReason, OrderSide
from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.ledger import BacktestLedger, LedgerEntryType, verify_ledger
from app.backtesting.portfolio import apply_fill, initialize_portfolio, mark_to_market
from tests.market_data_helpers import utc


def _fill(
    *,
    side: OrderSide,
    quantity: str,
    price: str,
    fee: str = "0",
    sequence: int = 1,
) -> Fill:
    qty = Decimal(quantity)
    execution = Decimal(price)
    return Fill(
        fill_id=f"F{sequence:012d}",
        order_id=f"O{sequence:012d}",
        reason=FillReason.MARKET_OPEN,
        liquidity=FillLiquidity.TAKER,
        side=side,
        quantity=qty,
        base_price=execution,
        execution_price=execution,
        notional=qty * execution,
        fee=Decimal(fee),
        slippage_cost=Decimal("0"),
        event_time=utc(2026, 1, 1),
        candle_index=sequence,
    )


def test_fill_entries_reconstruct_portfolio_balances_and_hash_chain() -> None:
    ledger = BacktestLedger()
    ledger.record_initial_capital(Decimal("1000"), utc(2026, 1, 1))
    state = initialize_portfolio(Decimal("1000"))
    fill = _fill(side=OrderSide.BUY, quantity="2", price="100", fee="1")
    mutation = apply_fill(state, fill)
    created = ledger.record_fill(fill, mutation)
    marked = mark_to_market(mutation.after, Decimal("110"))
    ledger.record_mark(
        marked,
        event_time=utc(2026, 1, 1),
        candle_index=1,
    )

    assert [entry.entry_type for entry in created] == [
        LedgerEntryType.BUY_FILL,
        LedgerEntryType.FEE,
    ]
    verification = verify_ledger(ledger.entries)
    assert verification.final_quote_balance == Decimal("799")
    assert verification.final_base_balance == Decimal("2")
    assert verification.entry_count == 4


def test_realized_pnl_is_a_separate_zero_delta_event() -> None:
    ledger = BacktestLedger()
    ledger.record_initial_capital(Decimal("1000"), utc(2026, 1, 1))
    bought_fill = _fill(side=OrderSide.BUY, quantity="2", price="100")
    bought = apply_fill(initialize_portfolio(Decimal("1000")), bought_fill)
    ledger.record_fill(bought_fill, bought)
    sell_fill = _fill(side=OrderSide.SELL, quantity="1", price="120", sequence=2)
    sold = apply_fill(bought.after, sell_fill)
    entries = ledger.record_fill(sell_fill, sold)

    assert entries[-1].entry_type is LedgerEntryType.REALIZED_PNL
    assert entries[-1].realized_pnl == Decimal("20")
    assert entries[-1].quote_delta == 0
    assert verify_ledger(ledger.entries).final_quote_balance == Decimal("920")


def test_tampering_removal_reordering_and_duplication_are_detected() -> None:
    ledger = BacktestLedger()
    first = ledger.record_initial_capital(Decimal("100"), utc(2026, 1, 1))
    ledger.append(
        event_time=utc(2026, 1, 1),
        candle_index=0,
        entry_type=LedgerEntryType.ORDER_RESERVED,
        quote_delta=Decimal("0"),
        base_delta=Decimal("0"),
    )
    entries = ledger.entries

    tampered = (replace(first, quote_delta=Decimal("99")), entries[1])
    with pytest.raises(BacktestResultCorruptError):
        verify_ledger(tampered)
    with pytest.raises(BacktestResultCorruptError):
        verify_ledger((entries[1],))
    with pytest.raises(BacktestResultCorruptError):
        verify_ledger((entries[1], entries[0]))
    with pytest.raises(BacktestResultCorruptError):
        verify_ledger((entries[0], entries[1], entries[1]))


def test_ledger_refuses_negative_intermediate_balance() -> None:
    ledger = BacktestLedger()
    ledger.record_initial_capital(Decimal("10"), utc(2026, 1, 1))
    with pytest.raises(BacktestResultCorruptError):
        ledger.append(
            event_time=utc(2026, 1, 1),
            candle_index=0,
            entry_type=LedgerEntryType.ORDER_RESERVED,
            quote_delta=Decimal("-11"),
            base_delta=Decimal("0"),
        )
