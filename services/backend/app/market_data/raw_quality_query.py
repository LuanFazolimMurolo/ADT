"""Read-only inspection of persisted FULL_DATASET RAW quality baselines."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from app.market_data.advanced_quality import (
    SCANNER_SCHEMA_VERSION,
    SCANNER_VERSION,
    load_quality_baseline,
)
from app.market_data.datasets import (
    AdvancedQualityIssue,
    CoverageSummary,
    DatasetIdentity,
    DatasetKind,
    QualityIssueCategory,
    QualityScanBaseline,
    QualityScanScope,
)
from app.market_data.errors import (
    MarketDataError,
    MarketDataInconsistencyError,
)
from app.market_data.filesystem import ensure_safe_path
from app.market_data.integrity import (
    LEGACY_RAW_DATASET_VERSION_ALGORITHM,
    RAW_DATASET_VERSION_ALGORITHM,
)
from app.market_data.operations import (
    MarketDatasetSelector,
    decode_dataset_id,
    encode_dataset_id,
)
from app.market_data.raw_dataset_query import RawDatasetSnapshot
from app.market_data.storage import compose_raw_dataset_version
from app.market_data.timeframes import get_timeframe

RAW_QUALITY_DEFAULT_ISSUE_SAMPLE = 25
RAW_QUALITY_MAX_ISSUE_SAMPLE = 100

_SHA256_HEX = frozenset("0123456789abcdef")
_SUPPORTED_VERSION_ALGORITHMS = frozenset(
    {
        RAW_DATASET_VERSION_ALGORITHM,
        LEGACY_RAW_DATASET_VERSION_ALGORITHM,
    }
)


class RawQualityStatus(StrEnum):
    """Persisted quality-baseline freshness relative to the current RAW dataset."""

    CURRENT = "CURRENT"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"


class RawQualityDatasetReader(Protocol):
    """Catalog-backed sanitized RAW dataset projection."""

    def get(self, dataset_id: str) -> RawDatasetSnapshot: ...


class RawQualitySnapshotLocker(Protocol):
    """Shared dataset lock acquired before catalog and baseline reads."""

    def snapshot(self, dataset_key: str) -> AbstractContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class RawQualityCoverage:
    """Sanitized quality coverage counters only."""

    expected_count: int | None
    observed_count: int
    internal_gap_count: int
    missing_at_start: int
    missing_at_end: int


@dataclass(frozen=True, slots=True)
class RawQualityIssue:
    """Sanitized issue projection without partition identifiers."""

    code: str
    severity: str
    category: QualityIssueCategory
    open_time: str | None


@dataclass(frozen=True, slots=True)
class RawQualityIssueTotals:
    """Bounded aggregate issue counters."""

    total: int
    errors: int
    warnings: int
    other: int


@dataclass(frozen=True, slots=True)
class RawQualitySnapshot:
    """One sanitized persisted RAW quality-baseline projection."""

    dataset: MarketDatasetSelector
    status: RawQualityStatus
    dataset_version: str
    version_algorithm: str
    baseline_dataset_version: str | None
    baseline_version_algorithm: str | None
    scanner_schema_version: int | None
    scanner_version: str | None
    coverage: RawQualityCoverage | None
    partition_count: int | None
    issue_totals: RawQualityIssueTotals | None
    issues: tuple[RawQualityIssue, ...]

    @property
    def dataset_id(self) -> str:
        return encode_dataset_id(self.dataset)


def raw_quality_identity(
    dataset: MarketDatasetSelector,
    *,
    manifest_schema_version: int,
) -> DatasetIdentity:
    """Build the exact RAW identity used by the existing FULL quality workflow."""
    return DatasetIdentity(
        dataset.exchange,
        dataset.market_type,
        dataset.pair.symbol,
        dataset.timeframe.code,
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        manifest_schema_version,
    )


def raw_quality_baseline_path(
    root: Path,
    identity: DatasetIdentity,
) -> Path:
    """Return the deterministic FULL_DATASET RAW baseline path."""
    digest = sha256(f"{identity.key}:{QualityScanScope.FULL_DATASET.value}".encode()).hexdigest()
    return ensure_safe_path(
        root,
        root / "quality-baselines" / f"{digest}.json",
    )


@dataclass(slots=True)
class LocalRawQualityReadService:
    """Read persisted RAW quality only; never scan, repair or mutate datasets."""

    dataset_reader: RawQualityDatasetReader
    lock_manager: RawQualitySnapshotLocker
    root: Path
    manifest_schema_version: int
    issue_sample_limit: int = RAW_QUALITY_DEFAULT_ISSUE_SAMPLE

    def __post_init__(self) -> None:
        if (
            type(self.manifest_schema_version) is not int
            or self.manifest_schema_version < 1
            or type(self.issue_sample_limit) is not int
            or not 1 <= self.issue_sample_limit <= RAW_QUALITY_MAX_ISSUE_SAMPLE
        ):
            raise ValueError("invalid RAW quality read-service limits")

    def inspect(self, dataset_id: str) -> RawQualitySnapshot:
        identity = decode_dataset_id(dataset_id)

        # Required ordering: dataset snapshot lock first, catalog snapshot second.
        with self.lock_manager.snapshot(identity.canonical_key):
            snapshot = self.dataset_reader.get(dataset_id)

            if snapshot.dataset != identity:
                raise MarketDataInconsistencyError(
                    "A identidade RAW consultada diverge do dataset catalogado."
                )

            expected_identity = raw_quality_identity(
                snapshot.dataset,
                manifest_schema_version=self.manifest_schema_version,
            )
            baseline_path = raw_quality_baseline_path(
                self.root,
                expected_identity,
            )

            if not baseline_path.exists():
                return _empty_projection(snapshot, RawQualityStatus.MISSING)

            if not baseline_path.is_file():
                return _empty_projection(snapshot, RawQualityStatus.INVALID)

            try:
                baseline = load_quality_baseline(
                    baseline_path,
                    self.root,
                )
                _validate_persisted_baseline(
                    baseline,
                    expected_identity=expected_identity,
                )
            except (
                MarketDataError,
                MarketDataInconsistencyError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
            ):
                return _empty_projection(snapshot, RawQualityStatus.INVALID)

            status = (
                RawQualityStatus.STALE
                if _is_stale(baseline, snapshot)
                else RawQualityStatus.CURRENT
            )

            ordered_issues = _ordered_baseline_issues(baseline)
            sample = tuple(
                _sanitize_issue(issue) for issue in ordered_issues[: self.issue_sample_limit]
            )

            errors = sum(issue.severity == "ERROR" for issue in ordered_issues)
            warnings = sum(issue.severity == "WARNING" for issue in ordered_issues)
            total = len(ordered_issues)

            return RawQualitySnapshot(
                dataset=snapshot.dataset,
                status=status,
                dataset_version=snapshot.version,
                version_algorithm=snapshot.version_algorithm,
                baseline_dataset_version=baseline.dataset_version,
                baseline_version_algorithm=baseline.dataset_version_algorithm,
                scanner_schema_version=baseline.scanner_schema_version,
                scanner_version=baseline.scanner_version,
                coverage=RawQualityCoverage(
                    expected_count=baseline.coverage.expected_count,
                    observed_count=baseline.coverage.observed_count,
                    internal_gap_count=baseline.coverage.internal_gap_count,
                    missing_at_start=baseline.coverage.missing_at_start,
                    missing_at_end=baseline.coverage.missing_at_end,
                ),
                partition_count=len(baseline.partitions),
                issue_totals=RawQualityIssueTotals(
                    total=total,
                    errors=errors,
                    warnings=warnings,
                    other=total - errors - warnings,
                ),
                issues=sample,
            )


def _empty_projection(
    snapshot: RawDatasetSnapshot,
    status: RawQualityStatus,
) -> RawQualitySnapshot:
    return RawQualitySnapshot(
        dataset=snapshot.dataset,
        status=status,
        dataset_version=snapshot.version,
        version_algorithm=snapshot.version_algorithm,
        baseline_dataset_version=None,
        baseline_version_algorithm=None,
        scanner_schema_version=None,
        scanner_version=None,
        coverage=None,
        partition_count=None,
        issue_totals=None,
        issues=(),
    )


def _is_stale(
    baseline: QualityScanBaseline,
    snapshot: RawDatasetSnapshot,
) -> bool:
    return (
        baseline.dataset_version != snapshot.version
        or baseline.dataset_version_algorithm != snapshot.version_algorithm
        or baseline.scanner_schema_version != SCANNER_SCHEMA_VERSION
        or baseline.scanner_version != SCANNER_VERSION
    )


def _validate_persisted_baseline(
    baseline: QualityScanBaseline,
    *,
    expected_identity: DatasetIdentity,
) -> None:
    if not isinstance(baseline, QualityScanBaseline):
        raise MarketDataInconsistencyError("O baseline RAW persistido é inválido.")

    if (
        baseline.identity != expected_identity
        or baseline.scope is not QualityScanScope.FULL_DATASET
        or baseline.data_range is not None
    ):
        raise MarketDataInconsistencyError(
            "O baseline RAW persistido possui identidade ou escopo inválido."
        )

    if (
        type(baseline.scanner_schema_version) is not int
        or baseline.scanner_schema_version < 1
        or not isinstance(baseline.scanner_version, str)
        or not baseline.scanner_version
        or len(baseline.scanner_version) > 128
        or baseline.dataset_version_algorithm not in _SUPPORTED_VERSION_ALGORITHMS
        or not _valid_dataset_version(baseline.dataset_version)
        or not _valid_sha256(baseline.logical_checksum)
    ):
        raise MarketDataInconsistencyError("O baseline RAW persistido possui metadados inválidos.")

    paths = [item.summary.relative_path for item in baseline.partitions]

    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise MarketDataInconsistencyError("O baseline RAW persistido possui partições inválidas.")

    timeframe = get_timeframe(expected_identity.timeframe)

    for item in baseline.partitions:
        summary = item.summary

        if (
            not isinstance(summary.relative_path, str)
            or not summary.relative_path
            or type(summary.year) is not int
            or type(summary.month) is not int
            or not 1 <= summary.month <= 12
            or type(summary.candle_count) is not int
            or summary.candle_count < 0
            or type(item.internal_gap_count) is not int
            or item.internal_gap_count < 0
            or not _valid_sha256(summary.checksum)
            or not _valid_sha256(item.logical_checksum)
        ):
            raise MarketDataInconsistencyError(
                "O baseline RAW persistido possui resumo de partição inválido."
            )

        if summary.candle_count == 0:
            if summary.first_open_time is not None or summary.last_open_time is not None:
                raise MarketDataInconsistencyError(
                    "Uma partição RAW vazia possui cobertura inválida."
                )
        else:
            first = _baseline_timestamp(summary.first_open_time)
            last = _baseline_timestamp(summary.last_open_time)

            if (
                last < first
                or not timeframe.validate_open_time(first)
                or not timeframe.validate_open_time(last)
            ):
                raise MarketDataInconsistencyError(
                    "Uma partição do baseline RAW possui cobertura inválida."
                )

        for issue in item.issues:
            _validate_issue(issue)

    for issue in baseline.global_issues:
        _validate_issue(issue)

    composed = compose_raw_dataset_version(
        (item.summary.relative_path, item.logical_checksum) for item in baseline.partitions
    )
    if composed != baseline.logical_checksum:
        raise MarketDataInconsistencyError("O checksum lógico do baseline RAW é inválido.")

    expected_coverage = _expected_full_coverage(
        baseline,
        timeframe.duration,
    )
    if baseline.coverage != expected_coverage:
        raise MarketDataInconsistencyError("A cobertura do baseline RAW persistido é inválida.")


def _expected_full_coverage(
    baseline: QualityScanBaseline,
    duration: timedelta,
) -> CoverageSummary:
    nonempty = [item for item in baseline.partitions if item.summary.candle_count > 0]

    first = _baseline_timestamp(nonempty[0].summary.first_open_time) if nonempty else None
    last = _baseline_timestamp(nonempty[-1].summary.last_open_time) if nonempty else None

    gaps = sum(item.internal_gap_count for item in baseline.partitions)
    previous_last: datetime | None = None

    for item in nonempty:
        current_first = _baseline_timestamp(item.summary.first_open_time)
        current_last = _baseline_timestamp(item.summary.last_open_time)

        if previous_last is not None:
            if current_first <= previous_last:
                raise MarketDataInconsistencyError(
                    "As partições do baseline RAW possuem sobreposição inválida."
                )

            if current_first > previous_last + duration:
                gaps += (current_first - previous_last) // duration - 1

        previous_last = current_last

    return CoverageSummary(
        requested_start=None,
        requested_end=None,
        first_open_time=first.isoformat() if first else None,
        last_open_time=last.isoformat() if last else None,
        expected_count=None,
        observed_count=sum(item.summary.candle_count for item in baseline.partitions),
        internal_gap_count=gaps,
        missing_at_start=0,
        missing_at_end=0,
    )


def _ordered_baseline_issues(
    baseline: QualityScanBaseline,
) -> tuple[AdvancedQualityIssue, ...]:
    issues = (
        tuple(issue for partition in baseline.partitions for issue in partition.issues)
        + baseline.global_issues
    )

    return tuple(
        sorted(
            issues,
            key=lambda item: (
                item.category.value,
                item.code,
                item.partition or "",
                item.open_time or "",
            ),
        )
    )


def _sanitize_issue(issue: AdvancedQualityIssue) -> RawQualityIssue:
    return RawQualityIssue(
        code=issue.code,
        severity=issue.severity,
        category=issue.category,
        open_time=(
            _baseline_timestamp(issue.open_time).isoformat()
            if issue.open_time is not None
            else None
        ),
    )


def _validate_issue(issue: AdvancedQualityIssue) -> None:
    if (
        not isinstance(issue, AdvancedQualityIssue)
        or not isinstance(issue.code, str)
        or not issue.code
        or len(issue.code) > 128
        or not isinstance(issue.severity, str)
        or not issue.severity
        or len(issue.severity) > 32
        or not isinstance(issue.category, QualityIssueCategory)
        or (
            issue.partition is not None
            and (not isinstance(issue.partition, str) or len(issue.partition) > 4_096)
        )
    ):
        raise MarketDataInconsistencyError("O baseline RAW persistido contém issue inválida.")

    if issue.open_time is not None:
        _baseline_timestamp(issue.open_time)


def _baseline_timestamp(value: str | None) -> datetime:
    if not isinstance(value, str):
        raise MarketDataInconsistencyError("O baseline RAW persistido contém timestamp inválido.")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise MarketDataInconsistencyError(
            "O baseline RAW persistido contém timestamp inválido."
        ) from None

    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset() != timedelta(0):
        raise MarketDataInconsistencyError("O baseline RAW persistido contém timestamp não UTC.")

    return parsed.astimezone(UTC)


def _valid_dataset_version(value: object) -> bool:
    return value == "" or _valid_sha256(value)


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value.lower() == value
        and all(character in _SHA256_HEX for character in value)
    )
