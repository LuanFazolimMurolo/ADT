"""Bounded read-only queries over deterministic paper-trading journals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.backtesting.domain import StrategyDescriptor
from app.market_data.domain import Timeframe, TradingPair, require_utc
from app.paper_trading.domain import paper_session_id
from app.paper_trading.errors import InvalidPaperSessionError
from app.paper_trading.journal import (
    PaperTrade,
    PaperTradeStatus,
    build_paper_trade_journal,
)
from app.paper_trading.repository import PaperTradingRepository

_MAX_PAGE = 100_000
_MAX_PAGE_SIZE = 100
_MAX_QUERY_TRADES = 100_000
_MAX_EXPORT_TRADES = 10_000
_SESSION_ID = re.compile(r"^[0-9a-f]{64}$")
_ASSET = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PaperTradeJournalFilter:
    """Canonical filters applied before deterministic journal pagination."""

    session_id: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    timeframe_code: str | None = None
    strategy_name: str | None = None
    strategy_version: str | None = None
    status: PaperTradeStatus | None = None
    opened_from: datetime | None = None
    opened_before: datetime | None = None
    closed_from: datetime | None = None
    closed_before: datetime | None = None

    def __post_init__(self) -> None:
        try:
            if self.session_id is not None and _SESSION_ID.fullmatch(self.session_id) is None:
                raise ValueError("session_id must be one lowercase SHA-256 digest")
            for field_name in ("base_asset", "quote_asset"):
                raw = getattr(self, field_name)
                if raw is None:
                    continue
                normalized = raw.strip().upper()
                if _ASSET.fullmatch(normalized) is None:
                    raise ValueError(f"{field_name} must be one canonical asset code")
                object.__setattr__(self, field_name, normalized)
            for field_name in ("timeframe_code", "strategy_name", "strategy_version"):
                raw = getattr(self, field_name)
                if raw is None:
                    continue
                normalized = raw.strip()
                if _SAFE_TOKEN.fullmatch(normalized) is None:
                    raise ValueError(f"{field_name} must be one safe token")
                object.__setattr__(self, field_name, normalized)
            if self.status is not None and not isinstance(self.status, PaperTradeStatus):
                raise ValueError("status must be canonical")
            for field_name in (
                "opened_from",
                "opened_before",
                "closed_from",
                "closed_before",
            ):
                value = getattr(self, field_name)
                if value is not None:
                    object.__setattr__(
                        self,
                        field_name,
                        require_utc(value, field_name=f"journal_filter_{field_name}"),
                    )
            _range(self.opened_from, self.opened_before, "opened")
            _range(self.closed_from, self.closed_before, "closed")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperTradeRecord:
    """One trade enriched with immutable session and state identity."""

    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    pair: TradingPair
    timeframe: Timeframe
    strategy: StrategyDescriptor
    last_candle_open_time: datetime
    replayed_at: datetime
    trade: PaperTrade

    def __post_init__(self) -> None:
        try:
            for value, field_name in (
                (self.session_id, "session_id"),
                (self.config_checksum, "config_checksum"),
                (self.state_id, "state_id"),
                (self.state_checksum, "state_checksum"),
            ):
                if not isinstance(value, str) or _SESSION_ID.fullmatch(value) is None:
                    raise ValueError(f"{field_name} must be one lowercase SHA-256 digest")
            if not isinstance(self.pair, TradingPair) or not isinstance(
                self.timeframe,
                Timeframe,
            ):
                raise ValueError("pair and timeframe must be canonical")
            if not isinstance(self.strategy, StrategyDescriptor):
                raise ValueError("strategy must be canonical")
            object.__setattr__(
                self,
                "last_candle_open_time",
                require_utc(
                    self.last_candle_open_time,
                    field_name="record_last_candle_open_time",
                ),
            )
            object.__setattr__(
                self,
                "replayed_at",
                require_utc(self.replayed_at, field_name="record_replayed_at"),
            )
            if not isinstance(self.trade, PaperTrade) or self.trade.session_id != self.session_id:
                raise ValueError("trade belongs to another session")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperTradeQueryTotals:
    """Exact aggregates for all records matching one filter, not only one page."""

    trades_count: int
    closed_trades_count: int
    open_trades_count: int
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal
    total_net_pnl: Decimal
    total_fees: Decimal
    total_slippage_cost: Decimal

    def __post_init__(self) -> None:
        try:
            for value, field_name in (
                (self.trades_count, "trades_count"),
                (self.closed_trades_count, "closed_trades_count"),
                (self.open_trades_count, "open_trades_count"),
            ):
                if type(value) is not int or value < 0:
                    raise ValueError(f"{field_name} must be nonnegative")
            if self.trades_count != self.closed_trades_count + self.open_trades_count:
                raise ValueError("trade counts are inconsistent")
            _finite(self.total_realized_pnl, "total_realized_pnl")
            _finite(self.total_unrealized_pnl, "total_unrealized_pnl")
            _finite(self.total_net_pnl, "total_net_pnl")
            if _finite(self.total_fees, "total_fees") < 0:
                raise ValueError("total_fees must be nonnegative")
            if _finite(self.total_slippage_cost, "total_slippage_cost") < 0:
                raise ValueError("total_slippage_cost must be nonnegative")
            if self.total_net_pnl != self.total_realized_pnl + self.total_unrealized_pnl:
                raise ValueError("total net PnL is inconsistent")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperTradePage:
    """One stable newest-first page with exact filtered aggregates."""

    filters: PaperTradeJournalFilter
    items: tuple[PaperTradeRecord, ...]
    page: int
    page_size: int
    total: int
    total_pages: int
    totals: PaperTradeQueryTotals

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.filters, PaperTradeJournalFilter):
                raise ValueError("filters must be canonical")
            PaperTradeJournalFilter.__post_init__(self.filters)
            _, page_size = _page(self.page, self.page_size)
            if type(self.total) is not int or self.total < 0:
                raise ValueError("total must be nonnegative")
            expected_pages = 0 if self.total == 0 else (self.total + page_size - 1) // page_size
            if type(self.total_pages) is not int or self.total_pages != expected_pages:
                raise ValueError("total_pages is inconsistent")
            if not isinstance(self.items, tuple) or len(self.items) > page_size:
                raise ValueError("items must be one bounded tuple")
            if any(not isinstance(item, PaperTradeRecord) for item in self.items):
                raise ValueError("items contain an invalid record")
            for item in self.items:
                PaperTradeRecord.__post_init__(item)
            if tuple(sorted(self.items, key=_record_key, reverse=True)) != self.items:
                raise ValueError("items are not in canonical newest-first order")
            trade_ids = tuple(item.trade.trade_id for item in self.items)
            if len(set(trade_ids)) != len(trade_ids):
                raise ValueError("page contains duplicate trades")
            if not isinstance(self.totals, PaperTradeQueryTotals):
                raise ValueError("totals must be canonical")
            PaperTradeQueryTotals.__post_init__(self.totals)
            if self.total != self.totals.trades_count:
                raise ValueError("page total diverges from filtered totals")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


class PaperTradeJournalReadService:
    """Build verified journals, filter them, and return bounded projections."""

    def __init__(self, repository: PaperTradingRepository) -> None:
        if not isinstance(repository, PaperTradingRepository):
            raise InvalidPaperSessionError("O repositório do journal é inválido.")
        self._repository = repository

    def list_trades(
        self,
        filters: PaperTradeJournalFilter,
        *,
        page: int,
        page_size: int,
    ) -> PaperTradePage:
        _page(page, page_size)
        records = self._matching_records(filters, maximum=_MAX_QUERY_TRADES)
        total = len(records)
        start = (page - 1) * page_size
        return PaperTradePage(
            filters=filters,
            items=records[start : start + page_size],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=0 if total == 0 else (total + page_size - 1) // page_size,
            totals=_totals(records),
        )

    def records_for_export(
        self,
        filters: PaperTradeJournalFilter,
    ) -> tuple[PaperTradeRecord, ...]:
        """Return all matching records under the explicit export row ceiling."""
        return self._matching_records(filters, maximum=_MAX_EXPORT_TRADES)

    def _matching_records(
        self,
        filters: PaperTradeJournalFilter,
        *,
        maximum: int,
    ) -> tuple[PaperTradeRecord, ...]:
        if not isinstance(filters, PaperTradeJournalFilter):
            raise InvalidPaperSessionError("Os filtros do journal são inválidos.")
        PaperTradeJournalFilter.__post_init__(filters)
        if filters.session_id is None:
            session_ids = self._repository.list_session_ids()
        else:
            session_ids = (filters.session_id,)
        records: list[PaperTradeRecord] = []
        for session_id in session_ids:
            config = self._repository.load_config(session_id)
            if paper_session_id(config) != session_id:
                raise InvalidPaperSessionError("A identidade da sessão divergiu.")
            if not _matches_config(filters, config.pair, config.timeframe, config.strategy):
                continue
            state = self._repository.load_state(session_id)
            if state is None:
                continue
            journal = build_paper_trade_journal(config, state)
            for trade in journal.trades:
                if not _matches_trade(filters, trade):
                    continue
                records.append(
                    PaperTradeRecord(
                        session_id=journal.session_id,
                        config_checksum=journal.config_checksum,
                        state_id=journal.state_id,
                        state_checksum=journal.state_checksum,
                        pair=journal.pair,
                        timeframe=journal.timeframe,
                        strategy=journal.strategy,
                        last_candle_open_time=journal.last_candle_open_time,
                        replayed_at=journal.replayed_at,
                        trade=trade,
                    )
                )
                if len(records) > maximum:
                    label = "consulta" if maximum == _MAX_QUERY_TRADES else "exportação"
                    raise InvalidPaperSessionError(
                        f"A {label} do journal excede o limite de {maximum} operações."
                    )
        return tuple(sorted(records, key=_record_key, reverse=True))


def _matches_config(
    filters: PaperTradeJournalFilter,
    pair: TradingPair,
    timeframe: Timeframe,
    strategy: StrategyDescriptor,
) -> bool:
    return not (
        (filters.base_asset is not None and pair.base != filters.base_asset)
        or (filters.quote_asset is not None and pair.quote != filters.quote_asset)
        or (filters.timeframe_code is not None and timeframe.code != filters.timeframe_code)
        or (filters.strategy_name is not None and strategy.name != filters.strategy_name)
        or (filters.strategy_version is not None and strategy.version != filters.strategy_version)
    )


def _matches_trade(filters: PaperTradeJournalFilter, trade: PaperTrade) -> bool:
    if filters.status is not None and trade.status is not filters.status:
        return False
    if filters.opened_from is not None and trade.opened_at < filters.opened_from:
        return False
    if filters.opened_before is not None and trade.opened_at >= filters.opened_before:
        return False
    if filters.closed_from is not None:
        if trade.closed_at is None or trade.closed_at < filters.closed_from:
            return False
    if filters.closed_before is not None:
        if trade.closed_at is None or trade.closed_at >= filters.closed_before:
            return False
    return True


def _totals(records: tuple[PaperTradeRecord, ...]) -> PaperTradeQueryTotals:
    return PaperTradeQueryTotals(
        trades_count=len(records),
        closed_trades_count=sum(
            record.trade.status is PaperTradeStatus.CLOSED for record in records
        ),
        open_trades_count=sum(record.trade.status is PaperTradeStatus.OPEN for record in records),
        total_realized_pnl=sum((record.trade.realized_pnl for record in records), _ZERO),
        total_unrealized_pnl=sum(
            (record.trade.unrealized_pnl for record in records),
            _ZERO,
        ),
        total_net_pnl=sum((record.trade.net_pnl for record in records), _ZERO),
        total_fees=sum((record.trade.total_fees for record in records), _ZERO),
        total_slippage_cost=sum(
            (record.trade.total_slippage_cost for record in records),
            _ZERO,
        ),
    )


def _record_key(record: PaperTradeRecord) -> tuple[datetime, str, int, str]:
    return (
        record.trade.opened_at,
        record.session_id,
        record.trade.sequence,
        record.trade.trade_id,
    )


def _page(page: object, page_size: object) -> tuple[int, int]:
    if type(page) is not int or page < 1 or page > _MAX_PAGE:
        raise InvalidPaperSessionError("A página solicitada é inválida.")
    if type(page_size) is not int or page_size < 1 or page_size > _MAX_PAGE_SIZE:
        raise InvalidPaperSessionError("O tamanho da página é inválido.")
    return page, page_size


def _range(start: datetime | None, end: datetime | None, name: str) -> None:
    if start is not None and end is not None and start >= end:
        raise ValueError(f"{name} range must be increasing and half-open")


def _finite(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be one finite Decimal")
    return value
