"""Bounded live asset catalog and public-price service for Phase 5-01."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.market_data.domain import (
    Exchange,
    Instrument,
    MarketPrice,
    MarketType,
    TradingPair,
    require_utc,
    validate_instrument,
)
from app.market_data.errors import (
    AssetCatalogLimitError,
    InactiveInstrumentError,
    MarketDataInconsistencyError,
    UnknownInstrumentError,
)

Clock = Callable[[], datetime]
_MAX_SEARCH_LENGTH = 64
_MAX_PAGE_SIZE = 100
_MAX_PAGE_NUMBER = 100_000


class AssetMarketAdapter(Protocol):
    """Minimal source contract needed by the live asset API."""

    @property
    def exchange(self) -> Exchange: ...

    @property
    def market_type(self) -> MarketType: ...

    async def list_instruments(self) -> tuple[Instrument, ...]: ...

    async def fetch_price(self, instrument: Instrument) -> MarketPrice: ...


@dataclass(frozen=True, slots=True)
class AssetCatalogQuery:
    """Canonical deterministic filters for one asset catalog page."""

    active_only: bool = True
    quote_asset: str | None = None
    search: str | None = None
    page: int = 1
    page_size: int = 50

    def __post_init__(self) -> None:
        if type(self.active_only) is not bool:
            raise MarketDataInconsistencyError("active_only deve ser booleano.")
        _require_exact_int(self.page, field_name="page", minimum=1, maximum=_MAX_PAGE_NUMBER)
        _require_exact_int(
            self.page_size,
            field_name="page_size",
            minimum=1,
            maximum=_MAX_PAGE_SIZE,
        )
        quote_asset = _normalize_optional_asset(self.quote_asset)
        search = _normalize_optional_search(self.search)
        object.__setattr__(self, "quote_asset", quote_asset)
        object.__setattr__(self, "search", search)


@dataclass(frozen=True, slots=True)
class AssetCatalogSnapshot:
    """One immutable source snapshot retained only for a bounded TTL."""

    exchange: Exchange
    market_type: MarketType
    instruments: tuple[Instrument, ...]
    fetched_at: datetime
    expires_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.exchange, Exchange) or not isinstance(self.market_type, MarketType):
            raise MarketDataInconsistencyError("A identidade do catálogo é inválida.")
        if not isinstance(self.instruments, tuple) or not self.instruments:
            raise MarketDataInconsistencyError("Os instrumentos do catálogo são inválidos.")
        if not isinstance(self.fetched_at, datetime) or not isinstance(self.expires_at, datetime):
            raise MarketDataInconsistencyError("A validade temporal do catálogo é inválida.")
        fetched_at = require_utc(self.fetched_at, field_name="fetched_at")
        expires_at = require_utc(self.expires_at, field_name="expires_at")
        if expires_at <= fetched_at:
            raise MarketDataInconsistencyError("A expiração do catálogo é inválida.")
        if not isinstance(self.source, str):
            raise MarketDataInconsistencyError("A fonte do catálogo é inválida.")
        source = self.source.strip()
        if not source or source != self.source or len(source) > 128:
            raise MarketDataInconsistencyError("A fonte do catálogo é inválida.")
        for instrument in self.instruments:
            validate_instrument(instrument)
        expected_order = tuple(sorted(self.instruments, key=lambda item: item.symbol))
        if expected_order != self.instruments:
            raise MarketDataInconsistencyError("O catálogo deve estar ordenado por símbolo.")
        seen: set[str] = set()
        seen_native: set[str] = set()
        for instrument in self.instruments:
            if (
                instrument.exchange is not self.exchange
                or instrument.market_type is not self.market_type
            ):
                raise MarketDataInconsistencyError("O catálogo mistura mercados incompatíveis.")
            if instrument.symbol in seen or instrument.native_symbol in seen_native:
                raise MarketDataInconsistencyError("O catálogo contém símbolos duplicados.")
            seen.add(instrument.symbol)
            seen_native.add(instrument.native_symbol)
        object.__setattr__(self, "fetched_at", fetched_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, slots=True)
class AssetCatalogPage:
    """One stable paginated projection of a catalog snapshot."""

    items: tuple[Instrument, ...]
    page: int
    page_size: int
    total: int
    fetched_at: datetime
    expires_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple):
            raise MarketDataInconsistencyError("A página contém instrumentos inválidos.")
        for item in self.items:
            validate_instrument(item)
        _require_exact_int(self.page, field_name="page", minimum=1, maximum=_MAX_PAGE_NUMBER)
        _require_exact_int(
            self.page_size,
            field_name="page_size",
            minimum=1,
            maximum=_MAX_PAGE_SIZE,
        )
        _require_exact_int(self.total, field_name="total", minimum=0, maximum=1_000_000)
        if len(self.items) > self.page_size or len(self.items) > self.total:
            raise MarketDataInconsistencyError("A página excede seus limites declarados.")
        if tuple(sorted(self.items, key=lambda item: item.symbol)) != self.items:
            raise MarketDataInconsistencyError("A página de ativos está fora de ordem.")
        if len({item.symbol for item in self.items}) != len(self.items):
            raise MarketDataInconsistencyError("A página contém ativos duplicados.")
        if not isinstance(self.fetched_at, datetime) or not isinstance(self.expires_at, datetime):
            raise MarketDataInconsistencyError("A validade temporal da página é inválida.")
        fetched_at = require_utc(self.fetched_at, field_name="fetched_at")
        expires_at = require_utc(self.expires_at, field_name="expires_at")
        if expires_at <= fetched_at:
            raise MarketDataInconsistencyError("A validade temporal da página é inválida.")
        if not isinstance(self.source, str):
            raise MarketDataInconsistencyError("A fonte da página é obrigatória.")
        source = self.source.strip()
        if not source or source != self.source or len(source) > 128:
            raise MarketDataInconsistencyError("A fonte da página é obrigatória.")
        object.__setattr__(self, "fetched_at", fetched_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "source", source)

    @property
    def total_pages(self) -> int:
        return math.ceil(self.total / self.page_size) if self.total else 0


class AssetMarketService:
    """Expose a bounded cached catalog and uncached current prices."""

    def __init__(
        self,
        adapter: AssetMarketAdapter,
        *,
        catalog_ttl_seconds: float = 300.0,
        max_instruments: int = 10_000,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(adapter.exchange, Exchange) or not isinstance(
            adapter.market_type, MarketType
        ):
            raise ValueError("adapter identity is invalid")
        if (
            isinstance(catalog_ttl_seconds, bool)
            or not isinstance(catalog_ttl_seconds, (int, float))
            or not math.isfinite(float(catalog_ttl_seconds))
            or not 1.0 <= float(catalog_ttl_seconds) <= 86_400.0
        ):
            raise ValueError("catalog_ttl_seconds must be between 1 and 86400")
        _require_exact_int(
            max_instruments,
            field_name="max_instruments",
            minimum=1,
            maximum=100_000,
        )
        self._adapter = adapter
        self._ttl = timedelta(seconds=float(catalog_ttl_seconds))
        self._max_instruments = max_instruments
        self._clock = clock or (lambda: datetime.now(UTC))
        self._snapshot: AssetCatalogSnapshot | None = None
        self._refresh_lock = asyncio.Lock()

    async def list_assets(self, query: AssetCatalogQuery) -> AssetCatalogPage:
        _validate_query(query)
        snapshot = await self._get_snapshot()
        instruments = tuple(
            item
            for item in snapshot.instruments
            if (not query.active_only or item.active)
            and (query.quote_asset is None or item.pair.quote == query.quote_asset)
            and (query.search is None or _matches_search(item, query.search))
        )
        start = (query.page - 1) * query.page_size
        end = start + query.page_size
        return AssetCatalogPage(
            items=instruments[start:end],
            page=query.page,
            page_size=query.page_size,
            total=len(instruments),
            fetched_at=snapshot.fetched_at,
            expires_at=snapshot.expires_at,
            source=snapshot.source,
        )

    async def get_asset(self, pair: TradingPair) -> Instrument:
        _validate_pair(pair)
        snapshot = await self._get_snapshot()
        for instrument in snapshot.instruments:
            if instrument.pair == pair:
                return instrument
        raise UnknownInstrumentError()

    async def get_price(self, pair: TradingPair) -> MarketPrice:
        instrument = await self.get_asset(pair)
        if not instrument.active:
            raise InactiveInstrumentError()
        price = await self._adapter.fetch_price(instrument)
        if not isinstance(price, MarketPrice):
            raise MarketDataInconsistencyError("A fonte retornou uma cotação inválida.")
        MarketPrice.__post_init__(price)
        if price.instrument != instrument:
            raise MarketDataInconsistencyError(
                "A cotação não corresponde ao instrumento solicitado."
            )
        return price

    async def refresh(self) -> AssetCatalogSnapshot:
        """Force one source refresh; intended for controlled operational use."""
        async with self._refresh_lock:
            return await self._refresh_snapshot()

    def invalidate(self) -> None:
        """Drop only the in-memory snapshot; no source request is performed."""
        self._snapshot = None

    async def _get_snapshot(self) -> AssetCatalogSnapshot:
        now = self._now()
        snapshot = self._snapshot
        if snapshot is not None and now < snapshot.expires_at:
            return snapshot
        async with self._refresh_lock:
            now = self._now()
            snapshot = self._snapshot
            if snapshot is not None and now < snapshot.expires_at:
                return snapshot
            return await self._refresh_snapshot(now=now)

    async def _refresh_snapshot(
        self,
        *,
        now: datetime | None = None,
    ) -> AssetCatalogSnapshot:
        fetched_at = now or self._now()
        raw_instruments = await self._adapter.list_instruments()
        if not isinstance(raw_instruments, tuple):
            raise MarketDataInconsistencyError("A fonte retornou um catálogo inválido.")
        if not raw_instruments:
            raise MarketDataInconsistencyError("A fonte retornou um catálogo vazio.")
        if len(raw_instruments) > self._max_instruments:
            raise AssetCatalogLimitError()
        for instrument in raw_instruments:
            validate_instrument(instrument)
        instruments = tuple(sorted(raw_instruments, key=lambda item: item.symbol))
        snapshot = AssetCatalogSnapshot(
            exchange=self._adapter.exchange,
            market_type=self._adapter.market_type,
            instruments=instruments,
            fetched_at=fetched_at,
            expires_at=fetched_at + self._ttl,
            source=(
                f"{self._adapter.exchange.value}_{self._adapter.market_type.value}_exchange_info"
            ),
        )
        self._snapshot = snapshot
        return snapshot

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise MarketDataInconsistencyError("O relógio do catálogo é inválido.")
        return require_utc(value, field_name="catalog_clock")


def _validate_query(query: object) -> None:
    if not isinstance(query, AssetCatalogQuery):
        raise MarketDataInconsistencyError("A consulta do catálogo é inválida.")
    AssetCatalogQuery.__post_init__(query)


def _validate_pair(pair: object) -> None:
    if (
        not isinstance(pair, TradingPair)
        or not isinstance(pair.base, str)
        or not isinstance(pair.quote, str)
    ):
        raise UnknownInstrumentError()
    try:
        canonical = TradingPair(pair.base, pair.quote)
    except MarketDataInconsistencyError:
        raise UnknownInstrumentError() from None
    if canonical != pair:
        raise UnknownInstrumentError()


def _matches_search(instrument: Instrument, search: str) -> bool:
    normalized = search.upper()
    return normalized in instrument.symbol or normalized in instrument.native_symbol.upper()


def _normalize_optional_asset(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MarketDataInconsistencyError("O ativo cotado é inválido.")
    normalized = value.strip().upper()
    validation_base = "ADTBASE" if normalized != "ADTBASE" else "ADTQUOTE"
    try:
        marker = TradingPair(validation_base, normalized)
    except MarketDataInconsistencyError:
        raise MarketDataInconsistencyError("O ativo cotado é inválido.") from None
    return marker.quote


def _normalize_optional_search(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MarketDataInconsistencyError("A busca do catálogo é inválida.")
    normalized = value.strip().upper()
    if not normalized:
        return None
    if len(normalized) > _MAX_SEARCH_LENGTH or any(ord(char) < 32 for char in normalized):
        raise MarketDataInconsistencyError("A busca do catálogo é inválida.")
    return normalized


def _require_exact_int(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MarketDataInconsistencyError(f"{field_name} é inválido.")
    return value
