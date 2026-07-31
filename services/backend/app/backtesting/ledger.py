"""Append-only hash-chained local ledger for deterministic backtests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.backtesting.domain import Fill, OrderSide
from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.portfolio import PortfolioMutation, PortfolioState
from app.market_data.domain import require_utc

_ZERO_HASH = "0" * 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class LedgerEntryType(StrEnum):
    INITIAL_CAPITAL = "INITIAL_CAPITAL"
    ORDER_RESERVED = "ORDER_RESERVED"
    ORDER_RELEASED = "ORDER_RELEASED"
    BUY_FILL = "BUY_FILL"
    SELL_FILL = "SELL_FILL"
    FEE = "FEE"
    REALIZED_PNL = "REALIZED_PNL"
    MARK_TO_MARKET = "MARK_TO_MARKET"
    FINAL_STATE = "FINAL_STATE"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One immutable financial event linked to all preceding events."""

    sequence: int
    event_time: datetime
    candle_index: int
    entry_type: LedgerEntryType
    quote_delta: Decimal
    base_delta: Decimal
    fee: Decimal
    realized_pnl: Decimal
    quote_balance: Decimal
    base_balance: Decimal
    previous_hash: str
    entry_hash: str
    order_id: str | None = None
    fill_id: str | None = None
    notional: Decimal = Decimal("0")
    reference_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.candle_index < -1:
            raise ValueError("ledger sequence or candle index is invalid")
        object.__setattr__(
            self,
            "event_time",
            require_utc(self.event_time, field_name="event_time"),
        )
        for value in (
            self.quote_delta,
            self.base_delta,
            self.fee,
            self.realized_pnl,
            self.quote_balance,
            self.base_balance,
            self.notional,
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError("ledger financial values must be finite Decimal")
        if self.fee < 0 or self.quote_balance < 0 or self.base_balance < 0:
            raise ValueError("ledger fees and balances must be nonnegative")
        if self.notional < 0:
            raise ValueError("ledger notional must be nonnegative")
        if self.reference_price is not None:
            if not self.reference_price.is_finite() or self.reference_price <= 0:
                raise ValueError("ledger reference price must be positive")
        for reference_id in (self.order_id, self.fill_id):
            if reference_id is not None and _SAFE_ID.fullmatch(reference_id) is None:
                raise ValueError("ledger reference id is invalid")
        if _SHA256.fullmatch(self.previous_hash) is None:
            raise ValueError("ledger previous hash is invalid")
        if _SHA256.fullmatch(self.entry_hash) is None:
            raise ValueError("ledger entry hash is invalid")


@dataclass(frozen=True, slots=True)
class LedgerVerification:
    entry_count: int
    final_quote_balance: Decimal
    final_base_balance: Decimal
    final_hash: str


class BacktestLedger:
    """Bounded in-memory ledger builder used by the candle engine."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def append(
        self,
        *,
        event_time: datetime,
        candle_index: int,
        entry_type: LedgerEntryType,
        quote_delta: Decimal,
        base_delta: Decimal,
        fee: Decimal = Decimal("0"),
        realized_pnl: Decimal = Decimal("0"),
        order_id: str | None = None,
        fill_id: str | None = None,
        notional: Decimal = Decimal("0"),
        reference_price: Decimal | None = None,
    ) -> LedgerEntry:
        previous = self._entries[-1] if self._entries else None
        quote_before = previous.quote_balance if previous else Decimal("0")
        base_before = previous.base_balance if previous else Decimal("0")
        quote_balance = quote_before + quote_delta
        base_balance = base_before + base_delta
        if quote_balance < 0 or base_balance < 0:
            raise BacktestResultCorruptError("O ledger produziria saldo negativo.")
        sequence = len(self._entries) + 1
        previous_hash = previous.entry_hash if previous else _ZERO_HASH
        raw = _RawLedgerEntry(
            sequence=sequence,
            event_time=require_utc(event_time, field_name="event_time"),
            candle_index=candle_index,
            entry_type=entry_type,
            quote_delta=quote_delta,
            base_delta=base_delta,
            fee=fee,
            realized_pnl=realized_pnl,
            quote_balance=quote_balance,
            base_balance=base_balance,
            order_id=order_id,
            fill_id=fill_id,
            notional=notional,
            reference_price=reference_price,
        )
        entry_hash = calculate_entry_hash(previous_hash, raw)
        entry = LedgerEntry(
            **asdict(raw),
            previous_hash=previous_hash,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        return entry

    def record_initial_capital(self, amount: Decimal, event_time: datetime) -> LedgerEntry:
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            raise BacktestResultCorruptError("O capital inicial do ledger é inválido.")
        if self._entries:
            raise BacktestResultCorruptError("O capital inicial deve ser o primeiro evento.")
        return self.append(
            event_time=event_time,
            candle_index=-1,
            entry_type=LedgerEntryType.INITIAL_CAPITAL,
            quote_delta=amount,
            base_delta=Decimal("0"),
        )

    def record_fill(
        self,
        fill: Fill,
        mutation: PortfolioMutation,
    ) -> tuple[LedgerEntry, ...]:
        before_count = len(self._entries)
        gross_quote = -fill.notional if fill.side is OrderSide.BUY else fill.notional
        base_delta = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        self.append(
            event_time=fill.event_time,
            candle_index=fill.candle_index,
            entry_type=(
                LedgerEntryType.BUY_FILL
                if fill.side is OrderSide.BUY
                else LedgerEntryType.SELL_FILL
            ),
            quote_delta=gross_quote,
            base_delta=base_delta,
            order_id=fill.order_id,
            fill_id=fill.fill_id,
            notional=fill.notional,
            reference_price=fill.execution_price,
        )
        if fill.fee:
            self.append(
                event_time=fill.event_time,
                candle_index=fill.candle_index,
                entry_type=LedgerEntryType.FEE,
                quote_delta=-fill.fee,
                base_delta=Decimal("0"),
                fee=fill.fee,
                order_id=fill.order_id,
                fill_id=fill.fill_id,
                reference_price=fill.execution_price,
            )
        if mutation.realized_pnl_delta:
            self.append(
                event_time=fill.event_time,
                candle_index=fill.candle_index,
                entry_type=LedgerEntryType.REALIZED_PNL,
                quote_delta=Decimal("0"),
                base_delta=Decimal("0"),
                realized_pnl=mutation.realized_pnl_delta,
                order_id=fill.order_id,
                fill_id=fill.fill_id,
                reference_price=fill.execution_price,
            )
        last = self._entries[-1]
        if (
            last.quote_balance != mutation.after.quote_cash
            or last.base_balance != mutation.after.base_quantity
        ):
            raise BacktestResultCorruptError("O ledger diverge do portfolio após o fill.")
        return tuple(self._entries[before_count:])

    def record_mark(
        self,
        state: PortfolioState,
        *,
        event_time: datetime,
        candle_index: int,
        final: bool = False,
    ) -> LedgerEntry:
        previous = self._entries[-1] if self._entries else None
        if previous is None:
            raise BacktestResultCorruptError("O ledger não possui capital inicial.")
        if (
            previous.quote_balance != state.quote_cash
            or previous.base_balance != state.base_quantity
        ):
            raise BacktestResultCorruptError("O mark-to-market diverge do saldo do ledger.")
        return self.append(
            event_time=event_time,
            candle_index=candle_index,
            entry_type=(LedgerEntryType.FINAL_STATE if final else LedgerEntryType.MARK_TO_MARKET),
            quote_delta=Decimal("0"),
            base_delta=Decimal("0"),
            realized_pnl=Decimal("0"),
            reference_price=state.last_mark_price,
        )


@dataclass(frozen=True, slots=True)
class _RawLedgerEntry:
    sequence: int
    event_time: datetime
    candle_index: int
    entry_type: LedgerEntryType
    quote_delta: Decimal
    base_delta: Decimal
    fee: Decimal
    realized_pnl: Decimal
    quote_balance: Decimal
    base_balance: Decimal
    order_id: str | None
    fill_id: str | None
    notional: Decimal
    reference_price: Decimal | None


def calculate_entry_hash(previous_hash: str, entry: LedgerEntry | _RawLedgerEntry) -> str:
    """Calculate ``SHA256(previous_hash + canonical entry without hashes)``."""
    if _SHA256.fullmatch(previous_hash) is None:
        raise ValueError("previous_hash must be one lowercase SHA-256 digest")
    payload = {
        "sequence": entry.sequence,
        "event_time": entry.event_time.isoformat(),
        "candle_index": entry.candle_index,
        "entry_type": entry.entry_type.value,
        "quote_delta": _decimal(entry.quote_delta),
        "base_delta": _decimal(entry.base_delta),
        "fee": _decimal(entry.fee),
        "realized_pnl": _decimal(entry.realized_pnl),
        "quote_balance": _decimal(entry.quote_balance),
        "base_balance": _decimal(entry.base_balance),
        "order_id": entry.order_id,
        "fill_id": entry.fill_id,
        "notional": _decimal(entry.notional),
        "reference_price": (
            _decimal(entry.reference_price) if entry.reference_price is not None else None
        ),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    digest = hashlib.sha256()
    digest.update(previous_hash.encode("ascii"))
    digest.update(encoded)
    return digest.hexdigest()


def verify_ledger(entries: tuple[LedgerEntry, ...]) -> LedgerVerification:
    """Detect alteration, removal, reordering, duplication and balance divergence."""
    previous_hash = _ZERO_HASH
    quote_balance = Decimal("0")
    base_balance = Decimal("0")
    seen_hashes: set[str] = set()
    for expected_sequence, entry in enumerate(entries, start=1):
        if entry.sequence != expected_sequence or entry.previous_hash != previous_hash:
            raise BacktestResultCorruptError("A sequência do ledger é inválida.")
        if entry.entry_hash in seen_hashes:
            raise BacktestResultCorruptError("O ledger contém entrada duplicada.")
        if calculate_entry_hash(previous_hash, entry) != entry.entry_hash:
            raise BacktestResultCorruptError("A cadeia de hash do ledger é inválida.")
        quote_balance += entry.quote_delta
        base_balance += entry.base_delta
        if quote_balance != entry.quote_balance or base_balance != entry.base_balance:
            raise BacktestResultCorruptError("Os saldos do ledger são inválidos.")
        if quote_balance < 0 or base_balance < 0:
            raise BacktestResultCorruptError("O ledger contém saldo negativo.")
        seen_hashes.add(entry.entry_hash)
        previous_hash = entry.entry_hash
    return LedgerVerification(
        entry_count=len(entries),
        final_quote_balance=quote_balance,
        final_base_balance=base_balance,
        final_hash=previous_hash,
    )


def _decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")
