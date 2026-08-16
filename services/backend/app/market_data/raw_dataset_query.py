"""Bounded read-only projections over transactionally cataloged RAW datasets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.errors import DomainError
from app.market_data.catalog import DatasetMetadata
from app.market_data.domain import Exchange, MarketType, TradingPair, require_utc
from app.market_data.errors import (
    InvalidRawDatasetQueryError,
    MarketDataInconsistencyError,
    RawDatasetNotFoundError,
)
from app.market_data.integrity import (
    LEGACY_RAW_DATASET_VERSION_ALGORITHM,
    RAW_DATASET_VERSION_ALGORITHM,
)
from app.market_data.operations import (
    MarketDatasetSelector,
    decode_dataset_id,
    encode_dataset_id,
)
from app.market_data.storage import compose_raw_dataset_version
from app.market_data.timeframes import get_timeframe

RAW_DATASET_DEFAULT_PAGE_SIZE = 25
RAW_DATASET_MAX_PAGE_SIZE = 100
RAW_DATASET_MAX_PAGE = 100_000

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_VERSION_ALGORITHMS = frozenset(
    {
        RAW_DATASET_VERSION_ALGORITHM,
        LEGACY_RAW_DATASET_VERSION_ALGORITHM,
    }
)


class RawDatasetCatalogReader(Protocol):
    """Bounded local-catalog snapshot surface required by the read model."""

    def list_datasets_snapshot(
        self,
        *,
        timeout_seconds: float,
    ) -> tuple[DatasetMetadata, ...]: ...

    def get_dataset_snapshot(
        self,
        key: str,
        *,
        timeout_seconds: float,
    ) -> DatasetMetadata | None: ...


@dataclass(frozen=True, slots=True)
class RawDatasetIntegritySummary:
    """Sanitized integrity state without partition paths or local locations."""

    present: bool
    schema_version: int | None
    checksum_algorithm: str | None
    partition_count: int


@dataclass(frozen=True, slots=True)
class RawDatasetSnapshot:
    """One sanitized persisted RAW dataset projection."""

    dataset: MarketDatasetSelector
    first_open_time: datetime | None
    last_open_time: datetime | None
    coverage_start: datetime | None
    coverage_end: datetime | None
    candle_count: int
    version: str
    version_algorithm: str
    updated_at: datetime
    integrity: RawDatasetIntegritySummary

    @property
    def dataset_id(self) -> str:
        return encode_dataset_id(self.dataset)


@dataclass(frozen=True, slots=True)
class RawDatasetPageQuery:
    """Bounded deterministic list query."""

    page: int = 1
    page_size: int = RAW_DATASET_DEFAULT_PAGE_SIZE
    symbol: str | None = None
    timeframe: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.page) is not int
            or not 1 <= self.page <= RAW_DATASET_MAX_PAGE
            or type(self.page_size) is not int
            or not 1 <= self.page_size <= RAW_DATASET_MAX_PAGE_SIZE
        ):
            raise InvalidRawDatasetQueryError()

        if self.symbol is not None:
            if not isinstance(self.symbol, str) or len(self.symbol) > 65:
                raise InvalidRawDatasetQueryError()
            try:
                pair = TradingPair.parse(self.symbol)
            except DomainError:
                raise InvalidRawDatasetQueryError() from None
            object.__setattr__(self, "symbol", pair.symbol)

        if self.timeframe is not None:
            if not isinstance(self.timeframe, str) or len(self.timeframe) > 16:
                raise InvalidRawDatasetQueryError()
            try:
                timeframe = get_timeframe(self.timeframe)
            except DomainError:
                raise InvalidRawDatasetQueryError() from None
            object.__setattr__(self, "timeframe", timeframe.code)


@dataclass(frozen=True, slots=True)
class RawDatasetPage:
    """One bounded stable page of RAW dataset metadata."""

    items: tuple[RawDatasetSnapshot, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


@dataclass(slots=True)
class LocalRawDatasetReadService:
    """Read only bounded local catalog snapshots; never fetch or scan data."""

    catalog: RawDatasetCatalogReader
    lock_timeout_seconds: float = 10

    def list(self, query: RawDatasetPageQuery) -> RawDatasetPage:
        RawDatasetPageQuery.__post_init__(query)

        items = tuple(
            _snapshot(metadata)
            for metadata in self.catalog.list_datasets_snapshot(
                timeout_seconds=self.lock_timeout_seconds
            )
        )

        if query.symbol is not None:
            items = tuple(item for item in items if item.dataset.pair.symbol == query.symbol)
        if query.timeframe is not None:
            items = tuple(item for item in items if item.dataset.timeframe.code == query.timeframe)

        items = tuple(sorted(items, key=lambda item: item.dataset.canonical_key))
        total = len(items)
        total_pages = (total + query.page_size - 1) // query.page_size
        start = (query.page - 1) * query.page_size
        end = start + query.page_size

        return RawDatasetPage(
            items=items[start:end],
            page=query.page,
            page_size=query.page_size,
            total=total,
            total_pages=total_pages,
        )

    def get(self, dataset_id: str) -> RawDatasetSnapshot:
        identity = decode_dataset_id(dataset_id)
        metadata = self.catalog.get_dataset_snapshot(
            identity.canonical_key,
            timeout_seconds=self.lock_timeout_seconds,
        )
        if metadata is None:
            raise RawDatasetNotFoundError()

        snapshot = _snapshot(metadata)
        if snapshot.dataset != identity:
            raise MarketDataInconsistencyError(
                "A identidade catalogada do dataset RAW diverge do identificador."
            )
        return snapshot


def _snapshot(metadata: DatasetMetadata) -> RawDatasetSnapshot:
    if not isinstance(metadata, DatasetMetadata):
        raise MarketDataInconsistencyError("A metadata RAW catalogada é inválida.")

    try:
        exchange = Exchange(metadata.exchange)
        market_type = MarketType(metadata.market_type)
        pair = TradingPair.parse(metadata.symbol)
        timeframe = get_timeframe(metadata.timeframe)
        dataset = MarketDatasetSelector(
            exchange=exchange,
            market_type=market_type,
            pair=pair,
            timeframe=timeframe,
        )
    except (ValueError, DomainError):
        raise MarketDataInconsistencyError(
            "A identidade catalogada do dataset RAW é inválida."
        ) from None

    if metadata.key != dataset.canonical_key:
        raise MarketDataInconsistencyError(
            "A chave catalogada do dataset RAW diverge da identidade."
        )

    if type(metadata.candle_count) is not int or metadata.candle_count < 0:
        raise MarketDataInconsistencyError("A contagem catalogada do dataset RAW é inválida.")

    if (
        not isinstance(metadata.version, str)
        or _SHA256_PATTERN.fullmatch(metadata.version) is None
        or metadata.version_algorithm not in _SUPPORTED_VERSION_ALGORITHMS
    ):
        raise MarketDataInconsistencyError("A versão catalogada do dataset RAW é inválida.")

    first = _optional_catalog_timestamp(
        metadata.first_open_time,
        field_name="first_open_time",
    )
    last = _optional_catalog_timestamp(
        metadata.last_open_time,
        field_name="last_open_time",
    )
    updated_at = _catalog_timestamp(metadata.updated_at, field_name="updated_at")

    if metadata.candle_count == 0:
        if first is not None or last is not None:
            raise MarketDataInconsistencyError(
                "Um dataset RAW vazio não pode declarar cobertura temporal."
            )
    else:
        if first is None or last is None or last < first:
            raise MarketDataInconsistencyError("A cobertura catalogada do dataset RAW é inválida.")
        if not timeframe.validate_open_time(first) or not timeframe.validate_open_time(last):
            raise MarketDataInconsistencyError("A cobertura RAW não está alinhada ao timeframe.")

    coverage_start = first
    coverage_end = timeframe.next_open_time(last) if last is not None else None

    manifest = metadata.partition_integrity
    if manifest is None:
        integrity = RawDatasetIntegritySummary(
            present=False,
            schema_version=None,
            checksum_algorithm=None,
            partition_count=0,
        )
    else:
        expected_prefix = (
            f"exchange={exchange.value}/"
            f"market={market_type.value}/"
            f"base={pair.base}/"
            f"quote={pair.quote}/"
            f"timeframe={timeframe.code}/"
        )
        if any(not entry.relative_path.startswith(expected_prefix) for entry in manifest.entries):
            raise MarketDataInconsistencyError(
                "O manifesto RAW referencia partição de outro dataset."
            )

        if metadata.version_algorithm == RAW_DATASET_VERSION_ALGORITHM:
            composed = compose_raw_dataset_version(
                (entry.relative_path, entry.checksum) for entry in manifest.entries
            )
            if composed != metadata.version:
                raise MarketDataInconsistencyError("O manifesto RAW diverge da versão catalogada.")

        integrity = RawDatasetIntegritySummary(
            present=True,
            schema_version=manifest.schema_version,
            checksum_algorithm=manifest.checksum_algorithm,
            partition_count=len(manifest.entries),
        )

    return RawDatasetSnapshot(
        dataset=dataset,
        first_open_time=first,
        last_open_time=last,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        candle_count=metadata.candle_count,
        version=metadata.version,
        version_algorithm=metadata.version_algorithm,
        updated_at=updated_at,
        integrity=integrity,
    )


def _optional_catalog_timestamp(
    value: str | None,
    *,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None
    return _catalog_timestamp(value, field_name=field_name)


def _catalog_timestamp(value: str, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise MarketDataInconsistencyError(f"O timestamp catalogado {field_name} é inválido.")
    try:
        parsed = datetime.fromisoformat(value)
        return require_utc(parsed, field_name=field_name)
    except (ValueError, MarketDataInconsistencyError):
        raise MarketDataInconsistencyError(
            f"O timestamp catalogado {field_name} é inválido."
        ) from None
