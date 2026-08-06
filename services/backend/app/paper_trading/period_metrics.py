"""Deterministic UTC calendar-period metrics over verified paper realizations."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.backtesting.domain import OrderSide, StrategyDescriptor
from app.backtesting.serialization import canonical_json_bytes
from app.market_data.domain import Timeframe, TradingPair, require_utc
from app.paper_trading.domain import paper_session_id
from app.paper_trading.errors import InvalidPaperSessionError, PaperSessionVerificationError
from app.paper_trading.journal import PaperTradeJournal, build_paper_trade_journal
from app.paper_trading.repository import PaperTradingRepository

_SCHEMA_VERSION = 1
_MAX_PERIOD_BUCKETS = 5_000
_MAX_REALIZATIONS = 100_000
_MAX_SOURCE_STATES = 10_000
_SESSION_ID = re.compile(r"^[0-9a-f]{64}$")
_ASSET = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class PaperPeriodGranularity(StrEnum):
    """Supported UTC calendar buckets."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


@dataclass(frozen=True, slots=True)
class PaperPeriodMetricsFilter:
    """One bounded, single-quote-asset period-metrics query."""

    quote_asset: str
    period_from: datetime
    period_before: datetime
    session_id: str | None = None
    base_asset: str | None = None
    timeframe_code: str | None = None
    strategy_name: str | None = None
    strategy_version: str | None = None

    def __post_init__(self) -> None:
        try:
            quote_asset = _asset(self.quote_asset, "quote_asset")
            object.__setattr__(self, "quote_asset", quote_asset)
            if self.session_id is not None and _SESSION_ID.fullmatch(self.session_id) is None:
                raise ValueError("session_id must be one lowercase SHA-256 digest")
            if self.base_asset is not None:
                object.__setattr__(
                    self,
                    "base_asset",
                    _asset(self.base_asset, "base_asset"),
                )
            for field_name in ("timeframe_code", "strategy_name", "strategy_version"):
                raw = getattr(self, field_name)
                if raw is None:
                    continue
                normalized = raw.strip()
                if _SAFE_TOKEN.fullmatch(normalized) is None:
                    raise ValueError(f"{field_name} must be one safe token")
                object.__setattr__(self, field_name, normalized)
            period_from = require_utc(
                self.period_from,
                field_name="period_metrics_from",
            )
            period_before = require_utc(
                self.period_before,
                field_name="period_metrics_before",
            )
            if period_from >= period_before:
                raise ValueError("period range must be increasing and half-open")
            object.__setattr__(self, "period_from", period_from)
            object.__setattr__(self, "period_before", period_before)
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperPeriodSourceState:
    """Immutable paper-state identity consumed by one series."""

    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    base_asset: str
    quote_asset: str
    last_candle_open_time: datetime
    replayed_at: datetime

    def __post_init__(self) -> None:
        try:
            for value, field_name in (
                (self.session_id, "session_id"),
                (self.config_checksum, "config_checksum"),
                (self.state_id, "state_id"),
                (self.state_checksum, "state_checksum"),
            ):
                _sha256(value, field_name)
            object.__setattr__(self, "base_asset", _asset(self.base_asset, "base_asset"))
            object.__setattr__(
                self,
                "quote_asset",
                _asset(self.quote_asset, "quote_asset"),
            )
            object.__setattr__(
                self,
                "last_candle_open_time",
                require_utc(
                    self.last_candle_open_time,
                    field_name="period_source_last_candle_open_time",
                ),
            )
            object.__setattr__(
                self,
                "replayed_at",
                require_utc(
                    self.replayed_at,
                    field_name="period_source_replayed_at",
                ),
            )
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperRealization:
    """One SELL fill with average-cost accounting allocated at its event time."""

    session_id: str
    state_id: str
    state_checksum: str
    base_asset: str
    quote_asset: str
    fill_id: str
    event_time: datetime
    exit_notional: Decimal
    released_cost_basis: Decimal
    realized_fees: Decimal
    realized_slippage_cost: Decimal
    realized_pnl: Decimal

    def __post_init__(self) -> None:
        try:
            _sha256(self.session_id, "session_id")
            _sha256(self.state_id, "state_id")
            _sha256(self.state_checksum, "state_checksum")
            object.__setattr__(self, "base_asset", _asset(self.base_asset, "base_asset"))
            object.__setattr__(
                self,
                "quote_asset",
                _asset(self.quote_asset, "quote_asset"),
            )
            if not isinstance(self.fill_id, str) or _SAFE_TOKEN.fullmatch(self.fill_id) is None:
                raise ValueError("fill_id must be one safe token")
            object.__setattr__(
                self,
                "event_time",
                require_utc(self.event_time, field_name="realization_event_time"),
            )
            _positive(self.exit_notional, "exit_notional")
            _positive(self.released_cost_basis, "released_cost_basis")
            _nonnegative(self.realized_fees, "realized_fees")
            _nonnegative(
                self.realized_slippage_cost,
                "realized_slippage_cost",
            )
            _finite(self.realized_pnl, "realized_pnl")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperPeriodMetricsBucket:
    """Exact realized activity for one UTC calendar bucket."""

    period_start: datetime
    period_end: datetime
    quote_asset: str
    realizations_count: int
    winning_realizations_count: int
    losing_realizations_count: int
    breakeven_realizations_count: int
    sessions_count: int
    symbols_count: int
    exit_notional: Decimal
    released_cost_basis: Decimal
    realized_fees: Decimal
    realized_slippage_cost: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    realized_pnl: Decimal
    win_rate_pct: Decimal | None
    profit_factor: Decimal | None

    def __post_init__(self) -> None:
        _validate_period_aggregate(
            self,
            period_start=self.period_start,
            period_end=self.period_end,
        )


@dataclass(frozen=True, slots=True)
class PaperPeriodMetricsTotals:
    """Exact aggregate across every bucket in one series."""

    periods_count: int
    active_periods_count: int
    quote_asset: str
    realizations_count: int
    winning_realizations_count: int
    losing_realizations_count: int
    breakeven_realizations_count: int
    sessions_count: int
    symbols_count: int
    exit_notional: Decimal
    released_cost_basis: Decimal
    realized_fees: Decimal
    realized_slippage_cost: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    realized_pnl: Decimal
    win_rate_pct: Decimal | None
    profit_factor: Decimal | None

    def __post_init__(self) -> None:
        try:
            _count(self.periods_count, "periods_count")
            _count(self.active_periods_count, "active_periods_count")
            if self.active_periods_count > self.periods_count:
                raise ValueError("active_periods_count exceeds periods_count")
            _validate_aggregate_values(self)
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperPeriodMetricsSeries:
    """Content-addressed calendar series bound to exact paper states."""

    granularity: PaperPeriodGranularity
    filters: PaperPeriodMetricsFilter
    source_states: tuple[PaperPeriodSourceState, ...]
    items: tuple[PaperPeriodMetricsBucket, ...]
    totals: PaperPeriodMetricsTotals
    query_checksum: str
    content_checksum: str
    schema_version: int = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.granularity, PaperPeriodGranularity):
                raise ValueError("granularity must be canonical")
            if not isinstance(self.filters, PaperPeriodMetricsFilter):
                raise ValueError("filters must be canonical")
            PaperPeriodMetricsFilter.__post_init__(self.filters)
            if self.schema_version != _SCHEMA_VERSION:
                raise ValueError("schema_version is unsupported")
            if not isinstance(self.source_states, tuple) or any(
                not isinstance(item, PaperPeriodSourceState) for item in self.source_states
            ):
                raise ValueError("source_states must be canonical")
            for source_state in self.source_states:
                PaperPeriodSourceState.__post_init__(source_state)
                if source_state.quote_asset != self.filters.quote_asset:
                    raise ValueError("source quote asset diverges from the query")
            if tuple(sorted(self.source_states, key=lambda item: item.session_id)) != (
                self.source_states
            ):
                raise ValueError("source_states must be sorted by session_id")
            if len({item.session_id for item in self.source_states}) != len(self.source_states):
                raise ValueError("source_states contain duplicate sessions")
            if not isinstance(self.items, tuple) or not self.items:
                raise ValueError("items must contain the requested bounded calendar range")
            if len(self.items) > _MAX_PERIOD_BUCKETS:
                raise ValueError("items exceed the period-bucket limit")
            for bucket in self.items:
                if not isinstance(bucket, PaperPeriodMetricsBucket):
                    raise ValueError("items must be canonical")
                PaperPeriodMetricsBucket.__post_init__(bucket)
                if bucket.quote_asset != self.filters.quote_asset:
                    raise ValueError("bucket quote asset diverges from the query")
            if self.items[0].period_start != self.filters.period_from:
                raise ValueError("series does not start at period_from")
            if self.items[-1].period_end != self.filters.period_before:
                raise ValueError("series does not end at period_before")
            for left, right in zip(self.items, self.items[1:]):
                if left.period_end != right.period_start:
                    raise ValueError("calendar buckets are not contiguous")
            if not isinstance(self.totals, PaperPeriodMetricsTotals):
                raise ValueError("totals must be canonical")
            PaperPeriodMetricsTotals.__post_init__(self.totals)
            if self.totals != _totals(self.items, self.source_states):
                raise ValueError("totals diverge from calendar buckets")
            _sha256(self.query_checksum, "query_checksum")
            _sha256(self.content_checksum, "content_checksum")
            if self.query_checksum != _query_checksum(self.granularity, self.filters):
                raise ValueError("query checksum is inconsistent")
            expected_content = _content_checksum(
                self.granularity,
                self.filters,
                self.source_states,
                self.items,
                self.totals,
                self.query_checksum,
            )
            if self.content_checksum != expected_content:
                raise ValueError("content checksum is inconsistent")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


class PaperPeriodMetricsService:
    """Build bounded UTC calendar series from verified SELL realizations."""

    def __init__(self, repository: PaperTradingRepository) -> None:
        if not isinstance(repository, PaperTradingRepository):
            raise InvalidPaperSessionError("O repositório de métricas por período é inválido.")
        self._repository = repository

    def build_series(
        self,
        filters: PaperPeriodMetricsFilter,
        *,
        granularity: PaperPeriodGranularity,
    ) -> PaperPeriodMetricsSeries:
        if not isinstance(filters, PaperPeriodMetricsFilter):
            raise InvalidPaperSessionError("Os filtros de métricas por período são inválidos.")
        PaperPeriodMetricsFilter.__post_init__(filters)
        if not isinstance(granularity, PaperPeriodGranularity):
            raise InvalidPaperSessionError("A granularidade de período é inválida.")
        _validate_aligned_range(filters, granularity)
        starts = _period_starts(
            filters.period_from,
            filters.period_before,
            granularity,
        )
        buckets: dict[datetime, list[PaperRealization]] = {start: [] for start in starts}
        source_states: list[PaperPeriodSourceState] = []
        realizations_count = 0

        session_ids = (
            self._repository.list_session_ids()
            if filters.session_id is None
            else (filters.session_id,)
        )
        for session_id in session_ids:
            config = self._repository.load_config(session_id)
            if paper_session_id(config) != session_id:
                raise InvalidPaperSessionError("A identidade da sessão divergiu.")
            if not _matches_config(
                filters,
                config.pair,
                config.timeframe,
                config.strategy,
            ):
                continue
            state = self._repository.load_state(session_id)
            if state is None:
                continue
            journal = build_paper_trade_journal(config, state)
            source_states.append(
                PaperPeriodSourceState(
                    session_id=journal.session_id,
                    config_checksum=journal.config_checksum,
                    state_id=journal.state_id,
                    state_checksum=journal.state_checksum,
                    base_asset=journal.pair.base,
                    quote_asset=journal.pair.quote,
                    last_candle_open_time=journal.last_candle_open_time,
                    replayed_at=journal.replayed_at,
                )
            )
            if len(source_states) > _MAX_SOURCE_STATES:
                raise InvalidPaperSessionError(
                    f"A consulta excede o limite de {_MAX_SOURCE_STATES} estados-fonte."
                )
            for realization in _journal_realizations(journal):
                if realization.event_time < filters.period_from:
                    continue
                if realization.event_time >= filters.period_before:
                    continue
                realizations_count += 1
                if realizations_count > _MAX_REALIZATIONS:
                    raise InvalidPaperSessionError(
                        f"A consulta excede o limite de {_MAX_REALIZATIONS} realizações."
                    )
                start = calendar_period_start(realization.event_time, granularity)
                buckets[start].append(realization)

        items = tuple(
            _bucket(
                start,
                calendar_period_end(start, granularity),
                filters.quote_asset,
                tuple(buckets[start]),
            )
            for start in starts
        )
        sources = tuple(sorted(source_states, key=lambda item: item.session_id))
        totals = _totals(items, sources)
        query_checksum = _query_checksum(granularity, filters)
        content_checksum = _content_checksum(
            granularity,
            filters,
            sources,
            items,
            totals,
            query_checksum,
        )
        return PaperPeriodMetricsSeries(
            granularity=granularity,
            filters=filters,
            source_states=sources,
            items=items,
            totals=totals,
            query_checksum=query_checksum,
            content_checksum=content_checksum,
        )


def calendar_period_start(
    value: datetime,
    granularity: PaperPeriodGranularity,
) -> datetime:
    """Return the inclusive UTC start containing one event time."""
    try:
        instant = require_utc(value, field_name="calendar_period_value")
        if not isinstance(granularity, PaperPeriodGranularity):
            raise ValueError("granularity must be canonical")
        day = instant.replace(hour=0, minute=0, second=0, microsecond=0)
        if granularity is PaperPeriodGranularity.DAILY:
            return day
        if granularity is PaperPeriodGranularity.WEEKLY:
            return day - timedelta(days=day.weekday())
        return day.replace(day=1)
    except InvalidPaperSessionError:
        raise
    except Exception as error:
        raise InvalidPaperSessionError(str(error)) from None


def calendar_period_end(
    period_start: datetime,
    granularity: PaperPeriodGranularity,
) -> datetime:
    """Return the exclusive UTC end for one aligned calendar period."""
    try:
        start = require_utc(period_start, field_name="calendar_period_start")
        if start != calendar_period_start(start, granularity):
            raise ValueError("period_start must be aligned to granularity")
        if granularity is PaperPeriodGranularity.DAILY:
            return start + timedelta(days=1)
        if granularity is PaperPeriodGranularity.WEEKLY:
            return start + timedelta(days=7)
        if start.month == 12:
            return start.replace(year=start.year + 1, month=1)
        return start.replace(month=start.month + 1)
    except InvalidPaperSessionError:
        raise
    except Exception as error:
        raise InvalidPaperSessionError(str(error)) from None


def _journal_realizations(
    journal: PaperTradeJournal,
) -> tuple[PaperRealization, ...]:
    open_quantity = _ZERO
    open_notional = _ZERO
    open_fees = _ZERO
    open_slippage = _ZERO
    realizations: list[PaperRealization] = []

    for execution in journal.executions:
        if execution.side is OrderSide.BUY:
            open_quantity += execution.quantity
            open_notional += execution.notional
            open_fees += execution.fee
            open_slippage += execution.slippage_cost
            continue
        if open_quantity <= 0 or execution.quantity > open_quantity:
            raise PaperSessionVerificationError(
                "A realização não possui posição Spot correspondente."
            )
        fraction = execution.quantity / open_quantity
        released_notional = (
            open_notional if execution.quantity == open_quantity else open_notional * fraction
        )
        released_entry_fees = (
            open_fees if execution.quantity == open_quantity else open_fees * fraction
        )
        released_entry_slippage = (
            open_slippage if execution.quantity == open_quantity else open_slippage * fraction
        )
        released_cost_basis = released_notional + released_entry_fees
        realized_fees = released_entry_fees + execution.fee
        realized_pnl = execution.notional - execution.fee - released_cost_basis
        realizations.append(
            PaperRealization(
                session_id=journal.session_id,
                state_id=journal.state_id,
                state_checksum=journal.state_checksum,
                base_asset=journal.pair.base,
                quote_asset=journal.pair.quote,
                fill_id=execution.fill_id,
                event_time=execution.event_time,
                exit_notional=execution.notional,
                released_cost_basis=released_cost_basis,
                realized_fees=realized_fees,
                realized_slippage_cost=(released_entry_slippage + execution.slippage_cost),
                realized_pnl=realized_pnl,
            )
        )
        open_quantity -= execution.quantity
        open_notional -= released_notional
        open_fees -= released_entry_fees
        open_slippage -= released_entry_slippage
        if open_quantity == 0:
            open_notional = _ZERO
            open_fees = _ZERO
            open_slippage = _ZERO

    if sum((item.realized_pnl for item in realizations), _ZERO) != (journal.total_realized_pnl):
        raise PaperSessionVerificationError(
            "As realizações por saída divergem do PnL realizado do journal."
        )
    return tuple(realizations)


def _bucket(
    period_start: datetime,
    period_end: datetime,
    quote_asset: str,
    realizations: tuple[PaperRealization, ...],
) -> PaperPeriodMetricsBucket:
    aggregate = _aggregate(realizations)
    return PaperPeriodMetricsBucket(
        period_start=period_start,
        period_end=period_end,
        quote_asset=quote_asset,
        realizations_count=aggregate.realizations_count,
        winning_realizations_count=aggregate.winning_realizations_count,
        losing_realizations_count=aggregate.losing_realizations_count,
        breakeven_realizations_count=aggregate.breakeven_realizations_count,
        sessions_count=aggregate.sessions_count,
        symbols_count=aggregate.symbols_count,
        exit_notional=aggregate.exit_notional,
        released_cost_basis=aggregate.released_cost_basis,
        realized_fees=aggregate.realized_fees,
        realized_slippage_cost=aggregate.realized_slippage_cost,
        gross_profit=aggregate.gross_profit,
        gross_loss=aggregate.gross_loss,
        realized_pnl=aggregate.realized_pnl,
        win_rate_pct=aggregate.win_rate_pct,
        profit_factor=aggregate.profit_factor,
    )


@dataclass(frozen=True, slots=True)
class _Aggregate:
    realizations_count: int
    winning_realizations_count: int
    losing_realizations_count: int
    breakeven_realizations_count: int
    sessions_count: int
    symbols_count: int
    exit_notional: Decimal
    released_cost_basis: Decimal
    realized_fees: Decimal
    realized_slippage_cost: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    realized_pnl: Decimal
    win_rate_pct: Decimal | None
    profit_factor: Decimal | None


def _aggregate(realizations: tuple[PaperRealization, ...]) -> _Aggregate:
    count = len(realizations)
    winning = sum(item.realized_pnl > 0 for item in realizations)
    losing = sum(item.realized_pnl < 0 for item in realizations)
    breakeven = count - winning - losing
    gross_profit = sum(
        (item.realized_pnl for item in realizations if item.realized_pnl > 0),
        _ZERO,
    )
    gross_loss = sum(
        (item.realized_pnl for item in realizations if item.realized_pnl < 0),
        _ZERO,
    )
    return _Aggregate(
        realizations_count=count,
        winning_realizations_count=winning,
        losing_realizations_count=losing,
        breakeven_realizations_count=breakeven,
        sessions_count=len({item.session_id for item in realizations}),
        symbols_count=len({(item.base_asset, item.quote_asset) for item in realizations}),
        exit_notional=sum((item.exit_notional for item in realizations), _ZERO),
        released_cost_basis=sum(
            (item.released_cost_basis for item in realizations),
            _ZERO,
        ),
        realized_fees=sum((item.realized_fees for item in realizations), _ZERO),
        realized_slippage_cost=sum(
            (item.realized_slippage_cost for item in realizations),
            _ZERO,
        ),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        realized_pnl=gross_profit + gross_loss,
        win_rate_pct=(None if count == 0 else Decimal(winning) / Decimal(count) * _HUNDRED),
        profit_factor=(None if gross_loss == 0 else gross_profit / -gross_loss),
    )


def _totals(
    items: tuple[PaperPeriodMetricsBucket, ...],
    source_states: tuple[PaperPeriodSourceState, ...],
) -> PaperPeriodMetricsTotals:
    realizations_count = sum(item.realizations_count for item in items)
    winning = sum(item.winning_realizations_count for item in items)
    losing = sum(item.losing_realizations_count for item in items)
    breakeven = sum(item.breakeven_realizations_count for item in items)
    gross_profit = sum((item.gross_profit for item in items), _ZERO)
    gross_loss = sum((item.gross_loss for item in items), _ZERO)
    quote_asset = items[0].quote_asset
    return PaperPeriodMetricsTotals(
        periods_count=len(items),
        active_periods_count=sum(item.realizations_count > 0 for item in items),
        quote_asset=quote_asset,
        realizations_count=realizations_count,
        winning_realizations_count=winning,
        losing_realizations_count=losing,
        breakeven_realizations_count=breakeven,
        sessions_count=len(source_states),
        symbols_count=len({(item.base_asset, item.quote_asset) for item in source_states}),
        exit_notional=sum((item.exit_notional for item in items), _ZERO),
        released_cost_basis=sum(
            (item.released_cost_basis for item in items),
            _ZERO,
        ),
        realized_fees=sum((item.realized_fees for item in items), _ZERO),
        realized_slippage_cost=sum(
            (item.realized_slippage_cost for item in items),
            _ZERO,
        ),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        realized_pnl=gross_profit + gross_loss,
        win_rate_pct=(
            None
            if realizations_count == 0
            else Decimal(winning) / Decimal(realizations_count) * _HUNDRED
        ),
        profit_factor=(None if gross_loss == 0 else gross_profit / -gross_loss),
    )


def _validate_period_aggregate(
    value: PaperPeriodMetricsBucket,
    *,
    period_start: datetime,
    period_end: datetime,
) -> None:
    try:
        start = require_utc(period_start, field_name="period_bucket_start")
        end = require_utc(period_end, field_name="period_bucket_end")
        if start >= end:
            raise ValueError("period bucket must be increasing and half-open")
        object.__setattr__(value, "period_start", start)
        object.__setattr__(value, "period_end", end)
        _validate_aggregate_values(value)
    except InvalidPaperSessionError:
        raise
    except Exception as error:
        raise InvalidPaperSessionError(str(error)) from None


def _validate_aggregate_values(
    value: PaperPeriodMetricsBucket | PaperPeriodMetricsTotals,
) -> None:
    quote_asset = _asset(value.quote_asset, "quote_asset")
    object.__setattr__(value, "quote_asset", quote_asset)
    for field_name in (
        "realizations_count",
        "winning_realizations_count",
        "losing_realizations_count",
        "breakeven_realizations_count",
        "sessions_count",
        "symbols_count",
    ):
        _count(getattr(value, field_name), field_name)
    if value.realizations_count != (
        value.winning_realizations_count
        + value.losing_realizations_count
        + value.breakeven_realizations_count
    ):
        raise ValueError("realization outcome counts are inconsistent")
    if value.symbols_count > value.sessions_count:
        raise ValueError("symbols_count exceeds sessions_count")
    if isinstance(value, PaperPeriodMetricsBucket) and (
        value.sessions_count > value.realizations_count
    ):
        raise ValueError("bucket sessions_count exceeds realizations_count")
    if (
        isinstance(value, PaperPeriodMetricsBucket)
        and value.realizations_count == 0
        and (value.sessions_count != 0 or value.symbols_count != 0)
    ):
        raise ValueError("empty bucket cannot reference sessions or symbols")
    for field_name in (
        "exit_notional",
        "released_cost_basis",
        "realized_fees",
        "realized_slippage_cost",
        "gross_profit",
    ):
        _nonnegative(getattr(value, field_name), field_name)
    if _finite(value.gross_loss, "gross_loss") > 0:
        raise ValueError("gross_loss must be nonpositive")
    _finite(value.realized_pnl, "realized_pnl")
    if value.realized_pnl != value.gross_profit + value.gross_loss:
        raise ValueError("realized_pnl must combine gross profit and loss")
    expected_win_rate = (
        None
        if value.realizations_count == 0
        else Decimal(value.winning_realizations_count)
        / Decimal(value.realizations_count)
        * _HUNDRED
    )
    if value.win_rate_pct != expected_win_rate:
        raise ValueError("win_rate_pct is inconsistent")
    expected_factor = None if value.gross_loss == 0 else value.gross_profit / -value.gross_loss
    if value.profit_factor != expected_factor:
        raise ValueError("profit_factor is inconsistent")


def _matches_config(
    filters: PaperPeriodMetricsFilter,
    pair: TradingPair,
    timeframe: Timeframe,
    strategy: StrategyDescriptor,
) -> bool:
    return not (
        pair.quote != filters.quote_asset
        or (filters.base_asset is not None and pair.base != filters.base_asset)
        or (filters.timeframe_code is not None and timeframe.code != filters.timeframe_code)
        or (filters.strategy_name is not None and strategy.name != filters.strategy_name)
        or (filters.strategy_version is not None and strategy.version != filters.strategy_version)
    )


def _validate_aligned_range(
    filters: PaperPeriodMetricsFilter,
    granularity: PaperPeriodGranularity,
) -> None:
    if calendar_period_start(filters.period_from, granularity) != filters.period_from:
        raise InvalidPaperSessionError(
            "period_from deve estar alinhado ao início UTC da granularidade."
        )
    if calendar_period_start(filters.period_before, granularity) != (filters.period_before):
        raise InvalidPaperSessionError(
            "period_before deve estar alinhado ao início UTC da granularidade."
        )


def _period_starts(
    period_from: datetime,
    period_before: datetime,
    granularity: PaperPeriodGranularity,
) -> tuple[datetime, ...]:
    starts: list[datetime] = []
    current = period_from
    while current < period_before:
        starts.append(current)
        if len(starts) > _MAX_PERIOD_BUCKETS:
            raise InvalidPaperSessionError(
                f"A consulta excede o limite de {_MAX_PERIOD_BUCKETS} períodos."
            )
        current = calendar_period_end(current, granularity)
    if current != period_before:
        raise InvalidPaperSessionError("period_before não fecha um número inteiro de períodos.")
    return tuple(starts)


def _query_checksum(
    granularity: PaperPeriodGranularity,
    filters: PaperPeriodMetricsFilter,
) -> str:
    return _digest(
        {
            "schema_version": _SCHEMA_VERSION,
            "granularity": granularity.value,
            "filters": _filter_payload(filters),
        }
    )


def _content_checksum(
    granularity: PaperPeriodGranularity,
    filters: PaperPeriodMetricsFilter,
    source_states: tuple[PaperPeriodSourceState, ...],
    items: tuple[PaperPeriodMetricsBucket, ...],
    totals: PaperPeriodMetricsTotals,
    query_checksum: str,
) -> str:
    return _digest(
        {
            "schema_version": _SCHEMA_VERSION,
            "granularity": granularity.value,
            "filters": _filter_payload(filters),
            "source_states": [_source_payload(item) for item in source_states],
            "items": [_bucket_payload(item) for item in items],
            "totals": _totals_payload(totals),
            "query_checksum": query_checksum,
        }
    )


def _filter_payload(value: PaperPeriodMetricsFilter) -> dict[str, object]:
    return {
        "quote_asset": value.quote_asset,
        "period_from": value.period_from,
        "period_before": value.period_before,
        "session_id": value.session_id,
        "base_asset": value.base_asset,
        "timeframe_code": value.timeframe_code,
        "strategy_name": value.strategy_name,
        "strategy_version": value.strategy_version,
    }


def _source_payload(value: PaperPeriodSourceState) -> dict[str, object]:
    return {
        "session_id": value.session_id,
        "config_checksum": value.config_checksum,
        "state_id": value.state_id,
        "state_checksum": value.state_checksum,
        "base_asset": value.base_asset,
        "quote_asset": value.quote_asset,
        "last_candle_open_time": value.last_candle_open_time,
        "replayed_at": value.replayed_at,
    }


def _bucket_payload(value: PaperPeriodMetricsBucket) -> dict[str, object]:
    return {
        "period_start": value.period_start,
        "period_end": value.period_end,
        "quote_asset": value.quote_asset,
        "realizations_count": value.realizations_count,
        "winning_realizations_count": value.winning_realizations_count,
        "losing_realizations_count": value.losing_realizations_count,
        "breakeven_realizations_count": value.breakeven_realizations_count,
        "sessions_count": value.sessions_count,
        "symbols_count": value.symbols_count,
        "exit_notional": value.exit_notional,
        "released_cost_basis": value.released_cost_basis,
        "realized_fees": value.realized_fees,
        "realized_slippage_cost": value.realized_slippage_cost,
        "gross_profit": value.gross_profit,
        "gross_loss": value.gross_loss,
        "realized_pnl": value.realized_pnl,
        "win_rate_pct": value.win_rate_pct,
        "profit_factor": value.profit_factor,
    }


def _totals_payload(value: PaperPeriodMetricsTotals) -> dict[str, object]:
    return {
        "periods_count": value.periods_count,
        "active_periods_count": value.active_periods_count,
        "quote_asset": value.quote_asset,
        "realizations_count": value.realizations_count,
        "winning_realizations_count": value.winning_realizations_count,
        "losing_realizations_count": value.losing_realizations_count,
        "breakeven_realizations_count": value.breakeven_realizations_count,
        "sessions_count": value.sessions_count,
        "symbols_count": value.symbols_count,
        "exit_notional": value.exit_notional,
        "released_cost_basis": value.released_cost_basis,
        "realized_fees": value.realized_fees,
        "realized_slippage_cost": value.realized_slippage_cost,
        "gross_profit": value.gross_profit,
        "gross_loss": value.gross_loss,
        "realized_pnl": value.realized_pnl,
        "win_rate_pct": value.win_rate_pct,
        "profit_factor": value.profit_factor,
    }


def _asset(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be one canonical asset code")
    normalized = value.strip().upper()
    if _ASSET.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be one canonical asset code")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SESSION_ID.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be one lowercase SHA-256 digest")
    return value


def _digest(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _count(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return value


def _finite(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be one finite Decimal")
    return value


def _positive(value: object, field_name: str) -> Decimal:
    result = _finite(value, field_name)
    if result <= 0:
        raise ValueError(f"{field_name} must be positive")
    return result


def _nonnegative(value: object, field_name: str) -> Decimal:
    result = _finite(value, field_name)
    if result < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return result
