"""Sanitized administrator RAW dataset response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from app.api.schemas.common import ApiSchema
from app.market_data.datasets import QualityIssueCategory
from app.market_data.domain import Exchange, MarketType
from app.market_data.raw_dataset_query import RawDatasetPage, RawDatasetSnapshot
from app.market_data.raw_gap_query import RawGapPage, RawGapRange
from app.market_data.raw_quality_query import (
    RawQualityCoverage,
    RawQualityIssue,
    RawQualityIssueTotals,
    RawQualitySnapshot,
    RawQualityStatus,
)


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


class RawGapRangeResponse(ApiSchema):
    """One sanitized half-open missing-candle range."""

    start: datetime
    end: datetime
    missing_candles: int

    @classmethod
    def from_domain(cls, gap: RawGapRange) -> Self:
        return cls(
            start=gap.start,
            end=gap.end,
            missing_candles=gap.missing_candles,
        )


class RawGapPageResponse(ApiSchema):
    """Bounded deterministic RAW gap inspection response."""

    dataset_id: str
    exchange: Exchange
    market_type: MarketType
    symbol: str
    timeframe: str
    dataset_version: str
    version_algorithm: str
    checked_start: datetime
    checked_end: datetime
    expected_candles: int
    observed_candles: int
    missing_candles: int
    total_gap_count: int
    page: int
    page_size: int
    total_pages: int
    items: list[RawGapRangeResponse]

    @classmethod
    def from_domain(cls, page: RawGapPage) -> Self:
        return cls(
            dataset_id=page.dataset_id,
            exchange=page.dataset.exchange,
            market_type=page.dataset.market_type,
            symbol=page.dataset.pair.symbol,
            timeframe=page.dataset.timeframe.code,
            dataset_version=page.dataset_version,
            version_algorithm=page.version_algorithm,
            checked_start=page.checked_start,
            checked_end=page.checked_end,
            expected_candles=page.expected_candles,
            observed_candles=page.observed_candles,
            missing_candles=page.missing_candles,
            total_gap_count=page.total_gap_count,
            page=page.page,
            page_size=page.page_size,
            total_pages=page.total_pages,
            items=[RawGapRangeResponse.from_domain(item) for item in page.items],
        )


class RawQualityCoverageResponse(ApiSchema):
    """Sanitized persisted RAW quality coverage counters."""

    expected_count: int | None
    observed_count: int
    internal_gap_count: int
    missing_at_start: int
    missing_at_end: int

    @classmethod
    def from_domain(
        cls,
        coverage: RawQualityCoverage,
    ) -> Self:
        return cls(
            expected_count=coverage.expected_count,
            observed_count=coverage.observed_count,
            internal_gap_count=coverage.internal_gap_count,
            missing_at_start=coverage.missing_at_start,
            missing_at_end=coverage.missing_at_end,
        )


class RawQualityIssueResponse(ApiSchema):
    """Sanitized quality issue without partition identifiers."""

    code: str
    severity: str
    category: QualityIssueCategory
    open_time: datetime | None

    @classmethod
    def from_domain(
        cls,
        issue: RawQualityIssue,
    ) -> Self:
        return cls(
            code=issue.code,
            severity=issue.severity,
            category=issue.category,
            open_time=(
                datetime.fromisoformat(issue.open_time) if issue.open_time is not None else None
            ),
        )


class RawQualityIssueTotalsResponse(ApiSchema):
    """Aggregate quality issue counters."""

    total: int
    errors: int
    warnings: int
    other: int

    @classmethod
    def from_domain(
        cls,
        totals: RawQualityIssueTotals,
    ) -> Self:
        return cls(
            total=totals.total,
            errors=totals.errors,
            warnings=totals.warnings,
            other=totals.other,
        )


class RawQualityResponse(ApiSchema):
    """Persisted RAW quality status without local storage details."""

    dataset_id: str
    exchange: Exchange
    market_type: MarketType
    symbol: str
    timeframe: str
    status: RawQualityStatus
    dataset_version: str
    version_algorithm: str
    baseline_dataset_version: str | None
    baseline_version_algorithm: str | None
    scanner_schema_version: int | None
    scanner_version: str | None
    coverage: RawQualityCoverageResponse | None
    partition_count: int | None
    issue_totals: RawQualityIssueTotalsResponse | None
    issues: list[RawQualityIssueResponse]

    @classmethod
    def from_domain(
        cls,
        snapshot: RawQualitySnapshot,
    ) -> Self:
        return cls(
            dataset_id=snapshot.dataset_id,
            exchange=snapshot.dataset.exchange,
            market_type=snapshot.dataset.market_type,
            symbol=snapshot.dataset.pair.symbol,
            timeframe=snapshot.dataset.timeframe.code,
            status=snapshot.status,
            dataset_version=snapshot.dataset_version,
            version_algorithm=snapshot.version_algorithm,
            baseline_dataset_version=(snapshot.baseline_dataset_version),
            baseline_version_algorithm=(snapshot.baseline_version_algorithm),
            scanner_schema_version=snapshot.scanner_schema_version,
            scanner_version=snapshot.scanner_version,
            coverage=(
                RawQualityCoverageResponse.from_domain(snapshot.coverage)
                if snapshot.coverage is not None
                else None
            ),
            partition_count=snapshot.partition_count,
            issue_totals=(
                RawQualityIssueTotalsResponse.from_domain(snapshot.issue_totals)
                if snapshot.issue_totals is not None
                else None
            ),
            issues=[RawQualityIssueResponse.from_domain(item) for item in snapshot.issues],
        )
