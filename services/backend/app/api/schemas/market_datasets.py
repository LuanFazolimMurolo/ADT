"""Sanitized administrator RAW dataset response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from app.api.schemas.common import ApiSchema
from app.market_data.domain import Exchange, MarketType
from app.market_data.raw_dataset_query import RawDatasetPage, RawDatasetSnapshot


class RawDatasetIntegrityResponse(ApiSchema):
    """Integrity summary that intentionally omits partition paths."""

    present: bool
    schema_version: int | None
    checksum_algorithm: str | None
    partition_count: int


class RawDatasetResponse(ApiSchema):
    """Persisted RAW metadata without filesystem implementation details."""

    dataset_id: str
    exchange: Exchange
    market_type: MarketType
    symbol: str
    base_asset: str
    quote_asset: str
    timeframe: str
    first_open_time: datetime | None
    last_open_time: datetime | None
    coverage_start: datetime | None
    coverage_end: datetime | None
    candle_count: int
    version: str
    version_algorithm: str
    updated_at: datetime
    integrity: RawDatasetIntegrityResponse

    @classmethod
    def from_domain(cls, snapshot: RawDatasetSnapshot) -> Self:
        return cls(
            dataset_id=snapshot.dataset_id,
            exchange=snapshot.dataset.exchange,
            market_type=snapshot.dataset.market_type,
            symbol=snapshot.dataset.pair.symbol,
            base_asset=snapshot.dataset.pair.base,
            quote_asset=snapshot.dataset.pair.quote,
            timeframe=snapshot.dataset.timeframe.code,
            first_open_time=snapshot.first_open_time,
            last_open_time=snapshot.last_open_time,
            coverage_start=snapshot.coverage_start,
            coverage_end=snapshot.coverage_end,
            candle_count=snapshot.candle_count,
            version=snapshot.version,
            version_algorithm=snapshot.version_algorithm,
            updated_at=snapshot.updated_at,
            integrity=RawDatasetIntegrityResponse(
                present=snapshot.integrity.present,
                schema_version=snapshot.integrity.schema_version,
                checksum_algorithm=snapshot.integrity.checksum_algorithm,
                partition_count=snapshot.integrity.partition_count,
            ),
        )


class RawDatasetPageResponse(ApiSchema):
    """Bounded deterministic administrator RAW dataset page."""

    items: list[RawDatasetResponse]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_domain(cls, page: RawDatasetPage) -> Self:
        return cls(
            items=[RawDatasetResponse.from_domain(item) for item in page.items],
            page=page.page,
            page_size=page.page_size,
            total=page.total,
            total_pages=page.total_pages,
        )
