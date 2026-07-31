"""Typed Phase 2C dataset, quality, lineage and snapshot contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.market_data.domain import (
    Candle,
    DataQualityIssue,
    DataRange,
    Exchange,
    MarketType,
)


class DatasetKind(StrEnum):
    RAW = "RAW"
    DERIVED = "DERIVED"


class DatasetState(StrEnum):
    COMPLETE = "COMPLETE"
    INVALID = "INVALID"
    STALE = "STALE"


class GapPolicy(StrEnum):
    STRICT = "STRICT"
    SKIP_INCOMPLETE = "SKIP_INCOMPLETE"
    MARK_INCOMPLETE = "MARK_INCOMPLETE"


class QualityScanMode(StrEnum):
    FULL = "FULL"
    INCREMENTAL = "INCREMENTAL"


class QualityScanScope(StrEnum):
    FULL_DATASET = "FULL_DATASET"
    RANGE = "RANGE"


class QualityIssueCategory(StrEnum):
    STRUCTURE = "STRUCTURE"
    CONTENT = "CONTENT"
    COVERAGE = "COVERAGE"
    CATALOG = "CATALOG"
    LINEAGE = "LINEAGE"
    OPERATIONAL_ARTIFACT = "OPERATIONAL_ARTIFACT"


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    exchange: Exchange
    market_type: MarketType
    symbol: str
    timeframe: str
    kind: DatasetKind
    source: str
    construction_policy: str
    schema_version: int

    @property
    def key(self) -> str:
        return (
            f"{self.kind.value.lower()}:{self.exchange.value}:{self.market_type.value}:"
            f"{self.symbol}:{self.timeframe}:{self.source}:"
            f"{self.construction_policy}:v{self.schema_version}"
        )


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    value: str
    checksum: str
    source_version: str | None = None


@dataclass(frozen=True, slots=True)
class PartitionSummary:
    relative_path: str
    year: int
    month: int
    candle_count: int
    first_open_time: str | None
    last_open_time: str | None
    checksum: str


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    requested_start: str | None
    requested_end: str | None
    first_open_time: str | None
    last_open_time: str | None
    expected_count: int | None
    observed_count: int
    internal_gap_count: int
    missing_at_start: int
    missing_at_end: int


@dataclass(frozen=True, slots=True)
class DatasetLineage:
    source_dataset_key: str
    source_dataset_version: str
    source_checksum: str
    source_timeframe: str
    target_timeframe: str
    algorithm: str
    algorithm_version: str
    gap_policy: GapPolicy
    open_candle_policy: str
    calendar: str
    materialized_at: str


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    identity: DatasetIdentity
    schema_version: int
    source_dataset_key: str
    source_dataset_version: str
    source_checksum: str
    target_dataset_key: str
    target_version: str
    target_checksum: str
    source_timeframe: str
    target_timeframe: str
    gap_policy: GapPolicy
    calendar: str
    first_open_time: str | None
    last_open_time: str | None
    candle_count: int
    partitions: tuple[PartitionSummary, ...]
    source_partitions: tuple[PartitionSummary, ...]
    algorithm: str
    algorithm_version: str
    created_at: str
    updated_at: str
    state: DatasetState
    lineage: DatasetLineage


@dataclass(frozen=True, slots=True)
class ResamplingPlan:
    source: DatasetIdentity
    target: DatasetIdentity
    data_range: DataRange
    source_dataset_version: str
    source_checksum: str
    source_candles: int
    expected_groups: int
    estimated_partitions: int
    group_size: int
    gap_policy: GapPolicy
    calendar: str
    algorithm: str = "canonical_ohlcv"
    algorithm_version: str = "1"


@dataclass(frozen=True, slots=True)
class ResamplingResult:
    plan: ResamplingPlan
    candles: tuple[Candle, ...]
    skipped_ranges: tuple[DataRange, ...]
    source_count: int
    materialized_count: int
    checksum: str


@dataclass(frozen=True, slots=True)
class QualityScanPlan:
    identity: DatasetIdentity
    mode: QualityScanMode
    data_range: DataRange | None = None
    known_partition_checksums: tuple[tuple[str, str], ...] = ()
    scope: QualityScanScope | None = None
    baseline: QualityScanBaseline | None = None
    resampling_plan: ResamplingPlan | None = None


@dataclass(frozen=True, slots=True)
class AdvancedQualityIssue:
    code: str
    severity: str
    category: QualityIssueCategory
    partition: str | None = None
    open_time: str | None = None


@dataclass(frozen=True, slots=True)
class QualityPartitionBaseline:
    summary: PartitionSummary
    logical_checksum: str
    internal_gap_count: int
    issues: tuple[AdvancedQualityIssue, ...]


@dataclass(frozen=True, slots=True)
class QualityScanBaseline:
    identity: DatasetIdentity
    dataset_version: str
    scanner_schema_version: int
    scanner_version: str
    scope: QualityScanScope
    data_range: DataRange | None
    partitions: tuple[QualityPartitionBaseline, ...]
    coverage: CoverageSummary
    logical_checksum: str
    global_issues: tuple[AdvancedQualityIssue, ...]


@dataclass(frozen=True, slots=True)
class QualityScanResult:
    plan: QualityScanPlan
    coverage: CoverageSummary
    partitions: tuple[PartitionSummary, ...]
    issues: tuple[AdvancedQualityIssue, ...]
    logical_checksum: str
    scanned_at: str
    changed_partitions: tuple[str, ...]
    base_issues: tuple[DataQualityIssue, ...] = ()
    baseline: QualityScanBaseline | None = None
    effective_scope: QualityScanScope = QualityScanScope.FULL_DATASET
    baseline_used: bool = False

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "ERROR" for issue in self.issues)


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    snapshot_id: str
    dataset_key: str
    dataset_version: str
    checksum: str
    data_range: DataRange
    partitions: tuple[str, ...]
    manifest_path: str
    created_at: str
