"""Deterministic global FULL/RANGE quality scans with incremental baselines."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.market_data.catalog import DatasetMetadata, JsonMarketDataCatalog, dataset_key
from app.market_data.datasets import (
    AdvancedQualityIssue,
    CoverageSummary,
    DatasetIdentity,
    DatasetKind,
    DatasetState,
    GapPolicy,
    PartitionSummary,
    QualityIssueCategory,
    QualityPartitionBaseline,
    QualityScanBaseline,
    QualityScanMode,
    QualityScanPlan,
    QualityScanResult,
    QualityScanScope,
)
from app.market_data.derived import DerivedDatasetService
from app.market_data.domain import (
    DataQualityIssue,
    DataRange,
    Instrument,
    Timeframe,
    TradingPair,
)
from app.market_data.errors import (
    MarketDataError,
    MarketDataInconsistencyError,
    MarketDataStorageError,
)
from app.market_data.filesystem import ensure_safe_path, fsync_directory
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.storage import ParquetCandleStore, canonical_candle_bytes
from app.market_data.timeframes import get_timeframe

Clock = Callable[[], datetime]
SCANNER_SCHEMA_VERSION = 1
SCANNER_VERSION = "phase2c-2"


class AdvancedMarketDataQualityScanner:
    """Audit RAW or DERIVED datasets without loading the full history."""

    def __init__(
        self,
        *,
        store: ParquetCandleStore,
        catalog: JsonMarketDataCatalog,
        max_issues: int = 1_000,
        clock: Clock | None = None,
        derived_service: DerivedDatasetService | None = None,
    ) -> None:
        if max_issues < 1:
            raise ValueError("max_issues must be positive")
        self._store = store
        self._catalog = catalog
        self._max_issues = max_issues
        self._clock = clock or (lambda: datetime.now(UTC))
        self._validator = MarketDataQualityValidator(clock=self._clock)
        self._derived_service = derived_service

    def scan(self, plan: QualityScanPlan) -> QualityScanResult:
        if plan.identity.kind is DatasetKind.DERIVED:
            return self._scan_derived(plan)
        return self._scan_raw(plan)

    def _scan_raw(self, plan: QualityScanPlan) -> QualityScanResult:
        scope = _scope(plan)
        if scope is QualityScanScope.RANGE and plan.data_range is None:
            raise MarketDataInconsistencyError("O scan RANGE exige um intervalo.")
        timeframe = get_timeframe(plan.identity.timeframe)
        pair = TradingPair.parse(plan.identity.symbol)
        instrument = Instrument(
            plan.identity.exchange,
            plan.identity.market_type,
            pair,
            f"{pair.base}{pair.quote}",
            True,
        )
        key = dataset_key(instrument, timeframe)
        metadata = self._catalog.get_dataset(key)
        dataset_version = metadata.version if metadata else ""
        paths = self._store.partition_paths(
            instrument.exchange,
            instrument.market_type,
            pair,
            timeframe,
            plan.data_range if scope is QualityScanScope.RANGE else None,
        )
        current_physical = {
            path.relative_to(self._store.root).as_posix(): _file_checksum(path) for path in paths
        }
        baseline_used = plan.mode is QualityScanMode.INCREMENTAL
        changed: set[str]
        partition_results: dict[str, QualityPartitionBaseline]
        removed: set[str] = set()
        if baseline_used:
            baseline = _validate_baseline(plan, scope)
            partition_results = {item.summary.relative_path: item for item in baseline.partitions}
            prior = {
                item.summary.relative_path: item.summary.checksum for item in baseline.partitions
            }
            changed = {
                relative
                for relative, checksum in current_physical.items()
                if prior.get(relative) != checksum
            }
            changed.update(set(current_physical) - set(prior))
            removed = set(prior) - set(current_physical)
            for relative in changed | removed:
                partition_results.pop(relative, None)
        else:
            partition_results = {}
            changed = set(current_physical)

        path_by_relative = {path.relative_to(self._store.root).as_posix(): path for path in paths}
        base_issues: list[DataQualityIssue] = []
        for relative in sorted(changed):
            path = path_by_relative[relative]
            scanned, raw_issues = self._scan_raw_partition(
                path,
                plan,
                instrument,
                timeframe,
                current_physical[relative],
            )
            partition_results[relative] = scanned
            base_issues.extend(raw_issues)

        partitions = tuple(
            sorted(
                partition_results.values(),
                key=lambda item: (item.summary.year, item.summary.month),
            )
        )
        coverage, cross_issues = _global_coverage(
            partitions,
            timeframe.duration,
            plan.data_range if scope is QualityScanScope.RANGE else None,
        )
        global_issues = list(cross_issues)
        for relative in sorted(removed):
            global_issues.append(
                _issue(
                    "missing_partition",
                    "ERROR",
                    QualityIssueCategory.STRUCTURE,
                    relative,
                )
            )
        if scope is QualityScanScope.RANGE:
            expected_months = _expected_partition_months(plan.data_range)
            present = {(item.summary.year, item.summary.month) for item in partitions}
            for year, month in sorted(expected_months - present):
                global_issues.append(
                    _issue(
                        "missing_partition",
                        "ERROR",
                        QualityIssueCategory.STRUCTURE,
                        f"year={year:04d}/month={month:02d}",
                    )
                )
        logical_checksum = _composed_logical_checksum(partitions)
        if scope is QualityScanScope.FULL_DATASET:
            self._catalog_findings(
                global_issues,
                key,
                metadata,
                coverage,
                partitions,
                instrument,
                timeframe,
            )
        self._operational_artifacts(global_issues)
        partition_issues = tuple(issue for item in partitions for issue in item.issues)
        ordered = _ordered_issues(
            (*partition_issues, *global_issues),
            self._max_issues,
        )
        baseline = QualityScanBaseline(
            identity=plan.identity,
            dataset_version=dataset_version,
            scanner_schema_version=SCANNER_SCHEMA_VERSION,
            scanner_version=SCANNER_VERSION,
            scope=scope,
            data_range=plan.data_range if scope is QualityScanScope.RANGE else None,
            partitions=partitions,
            coverage=coverage,
            logical_checksum=logical_checksum,
            global_issues=tuple(_ordered_issues(global_issues, self._max_issues)),
        )
        return QualityScanResult(
            plan=plan,
            coverage=coverage,
            partitions=tuple(item.summary for item in partitions),
            issues=ordered,
            logical_checksum=logical_checksum,
            scanned_at=self._clock().astimezone(UTC).isoformat(),
            changed_partitions=tuple(sorted(changed | removed)),
            base_issues=tuple(base_issues),
            baseline=baseline,
            effective_scope=scope,
            baseline_used=baseline_used,
        )

    def _scan_raw_partition(
        self,
        path: Path,
        plan: QualityScanPlan,
        instrument: Instrument,
        timeframe: Timeframe,
        physical_checksum: str,
    ) -> tuple[QualityPartitionBaseline, tuple[DataQualityIssue, ...]]:
        relative = path.relative_to(self._store.root).as_posix()
        year, month = _year_month(path)
        issues: list[AdvancedQualityIssue] = []
        try:
            schema_valid = self._store.verify_schema(path)
        except Exception:
            schema_valid = False
        if not schema_valid:
            issues.append(
                _issue(
                    "parquet_schema_corrupt",
                    "ERROR",
                    QualityIssueCategory.STRUCTURE,
                    relative,
                )
            )
            return _empty_partition(relative, year, month, physical_checksum, tuple(issues)), ()
        try:
            all_rows = self._store.read_partition(
                path,
                exchange=instrument.exchange,
                market_type=instrument.market_type,
                pair=instrument.pair,
                timeframe=timeframe,
            )
        except MarketDataError:
            issues.append(
                _issue(
                    "partition_content_corrupt",
                    "ERROR",
                    QualityIssueCategory.CONTENT,
                    relative,
                )
            )
            return _empty_partition(relative, year, month, physical_checksum, tuple(issues)), ()
        rows = tuple(
            candle
            for candle in all_rows
            if _scope(plan) is QualityScanScope.FULL_DATASET
            or plan.data_range is None
            or plan.data_range.start <= candle.open_time < plan.data_range.end
        )
        report = self._validator.validate(rows, timeframe=timeframe)
        issues.extend(
            _issue(
                item.code,
                item.severity.value.upper(),
                _category(item.code),
                relative,
                item.open_time.isoformat() if item.open_time else None,
            )
            for item in report.issues
        )
        issues.extend(
            _issue(
                "incompatible_close_time",
                "ERROR",
                QualityIssueCategory.CONTENT,
                relative,
                candle.open_time.isoformat(),
            )
            for candle in rows
            if candle.close_time
            != candle.open_time + timeframe.duration - timedelta(milliseconds=1)
        )
        logical = hashlib.sha256()
        gaps = 0
        previous = None
        for candle in rows:
            logical.update(canonical_candle_bytes(candle))
            if previous is not None and candle.open_time > previous + timeframe.duration:
                gaps += (candle.open_time - previous) // timeframe.duration - 1
            previous = candle.open_time
        return (
            QualityPartitionBaseline(
                PartitionSummary(
                    relative,
                    year,
                    month,
                    len(rows),
                    rows[0].open_time.isoformat() if rows else None,
                    rows[-1].open_time.isoformat() if rows else None,
                    physical_checksum,
                ),
                logical.hexdigest(),
                gaps,
                tuple(_ordered_issues(issues, self._max_issues)),
            ),
            report.issues,
        )

    def _scan_derived(self, plan: QualityScanPlan) -> QualityScanResult:
        if (
            self._derived_service is None
            or plan.resampling_plan is None
            or plan.mode is QualityScanMode.INCREMENTAL
            or _scope(plan) is not QualityScanScope.FULL_DATASET
        ):
            raise MarketDataInconsistencyError(
                "O scan DERIVED exige plano de resampling, modo FULL e escopo FULL_DATASET."
            )
        manifest = self._derived_service.verify(plan.resampling_plan)
        issues: list[AdvancedQualityIssue] = []
        if manifest.identity != plan.identity or manifest.target_dataset_key != plan.identity.key:
            issues.append(
                _issue(
                    "derived_identity_divergence",
                    "ERROR",
                    QualityIssueCategory.LINEAGE,
                )
            )
        if manifest.state is DatasetState.STALE:
            issues.append(_issue("derived_source_stale", "ERROR", QualityIssueCategory.LINEAGE))
        elif manifest.state is DatasetState.INVALID:
            issues.append(_issue("derived_dataset_invalid", "ERROR", QualityIssueCategory.LINEAGE))
        if (
            manifest.lineage.source_dataset_key != manifest.source_dataset_key
            or manifest.lineage.source_dataset_version != manifest.source_dataset_version
            or manifest.lineage.source_checksum != manifest.source_checksum
        ):
            issues.append(
                _issue(
                    "derived_lineage_divergence",
                    "ERROR",
                    QualityIssueCategory.LINEAGE,
                )
            )
        first = manifest.first_open_time
        last = manifest.last_open_time
        gap_count = 0
        if first is not None and last is not None:
            timeframe = get_timeframe(manifest.target_timeframe)
            span_count = (
                datetime.fromisoformat(last) - datetime.fromisoformat(first)
            ) // timeframe.duration + 1
            gap_count = max(0, span_count - manifest.candle_count)
            if gap_count:
                issues.append(
                    _issue(
                        "gap",
                        "ERROR" if manifest.gap_policy is GapPolicy.STRICT else "WARNING",
                        QualityIssueCategory.COVERAGE,
                    )
                )
        coverage = CoverageSummary(
            None,
            None,
            first,
            last,
            None,
            manifest.candle_count,
            gap_count,
            0,
            0,
        )
        self._operational_artifacts(issues)
        return QualityScanResult(
            plan,
            coverage,
            manifest.partitions,
            tuple(_ordered_issues(issues, self._max_issues)),
            manifest.target_checksum,
            self._clock().astimezone(UTC).isoformat(),
            (),
            (),
            None,
            QualityScanScope.FULL_DATASET,
            False,
        )

    def _catalog_findings(
        self,
        issues: list[AdvancedQualityIssue],
        key: str,
        metadata: DatasetMetadata | None,
        coverage: CoverageSummary,
        partitions: tuple[QualityPartitionBaseline, ...],
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> None:
        if metadata is None:
            issues.append(
                _issue(
                    "catalog_dataset_missing",
                    "ERROR",
                    QualityIssueCategory.CATALOG,
                )
            )
        elif (
            metadata.candle_count != coverage.observed_count
            or metadata.first_open_time != coverage.first_open_time
            or metadata.last_open_time != coverage.last_open_time
        ):
            issues.append(
                _issue(
                    "catalog_storage_divergence",
                    "ERROR",
                    QualityIssueCategory.CATALOG,
                )
            )
        try:
            receipts = self._catalog.list_chunk_receipts()
        except MarketDataError:
            receipts = ()
            issues.append(_issue("receipt_divergence", "ERROR", QualityIssueCategory.CATALOG))
        for receipt in receipts:
            if receipt.dataset_key == key and not _valid_receipt(receipt):
                issues.append(_issue("receipt_divergence", "ERROR", QualityIssueCategory.CATALOG))
        # A FULL scan also calculates the canonical catalog checksum, streaming
        # each partition again rather than retaining its rows.
        canonical = hashlib.sha256()
        for item in partitions:
            path = ensure_safe_path(
                self._store.root,
                self._store.root / item.summary.relative_path,
            )
            try:
                rows = self._store.read_partition(
                    path,
                    exchange=instrument.exchange,
                    market_type=instrument.market_type,
                    pair=instrument.pair,
                    timeframe=timeframe,
                )
            except MarketDataError:
                rows = ()
            for candle in rows:
                canonical.update(canonical_candle_bytes(candle))
        if metadata is not None and metadata.version != canonical.hexdigest():
            issues.append(
                _issue(
                    "logical_checksum_divergence",
                    "ERROR",
                    QualityIssueCategory.CATALOG,
                )
            )

    def _operational_artifacts(self, issues: list[AdvancedQualityIssue]) -> None:
        patterns = (
            ("*.tmp-*", "abandoned_temporary"),
            ("*.bak-*", "abandoned_backup"),
            (".transactions/journal-*.json", "abandoned_journal"),
        )
        for pattern, code in patterns:
            for path in sorted(self._store.root.rglob(pattern)):
                issues.append(
                    _issue(
                        code,
                        "WARNING",
                        QualityIssueCategory.OPERATIONAL_ARTIFACT,
                        path.relative_to(self._store.root).as_posix(),
                    )
                )


def save_quality_baseline(path: Path, baseline: QualityScanBaseline, root: Path) -> None:
    safe = ensure_safe_path(root, path)
    safe.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(baseline)
    payload["data_range"] = (
        {
            "start": baseline.data_range.start.isoformat(),
            "end": baseline.data_range.end.isoformat(),
        }
        if baseline.data_range
        else None
    )
    encoded = _canonical_json(payload)
    envelope = _canonical_json(
        {"baseline": payload, "checksum": hashlib.sha256(encoded).hexdigest()}
    )
    temporary = safe.with_name(f".{safe.name}.tmp")
    with temporary.open("wb") as stream:
        stream.write(envelope)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, safe)
    fsync_directory(safe.parent)


def load_quality_baseline(path: Path, root: Path) -> QualityScanBaseline:
    safe = ensure_safe_path(root, path)
    try:
        envelope = json.loads(safe.read_text(encoding="utf-8"))
        raw = envelope["baseline"]
        if hashlib.sha256(_canonical_json(raw)).hexdigest() != envelope["checksum"]:
            raise ValueError
        return _decode_baseline(raw)
    except (OSError, ValueError, KeyError, TypeError):
        raise MarketDataStorageError("O baseline de qualidade é inválido.") from None


def _validate_baseline(
    plan: QualityScanPlan,
    scope: QualityScanScope,
) -> QualityScanBaseline:
    baseline = plan.baseline
    if (
        baseline is None
        or baseline.identity != plan.identity
        or baseline.scanner_schema_version != SCANNER_SCHEMA_VERSION
        or baseline.scanner_version != SCANNER_VERSION
        or baseline.scope is not scope
        or baseline.data_range != (plan.data_range if scope is QualityScanScope.RANGE else None)
        or len(baseline.dataset_version) not in {0, 64}
    ):
        raise MarketDataInconsistencyError("O scan INCREMENTAL exige baseline compatível e válido.")
    paths = [item.summary.relative_path for item in baseline.partitions]
    checksums = [
        checksum
        for item in baseline.partitions
        for checksum in (item.summary.checksum, item.logical_checksum)
    ]
    if (
        len(paths) != len(set(paths))
        or paths != sorted(paths)
        or any(
            len(checksum) != 64
            or checksum.lower() != checksum
            or any(character not in "0123456789abcdef" for character in checksum)
            for checksum in checksums
        )
        or _composed_logical_checksum(baseline.partitions) != baseline.logical_checksum
    ):
        raise MarketDataInconsistencyError("O scan INCREMENTAL exige baseline compatível e válido.")
    try:
        expected_coverage, _issues = _global_coverage(
            baseline.partitions,
            get_timeframe(plan.identity.timeframe).duration,
            plan.data_range if scope is QualityScanScope.RANGE else None,
        )
    except MarketDataError:
        raise MarketDataInconsistencyError(
            "O scan INCREMENTAL exige baseline compatível e válido."
        ) from None
    if baseline.coverage != expected_coverage:
        raise MarketDataInconsistencyError("O scan INCREMENTAL exige baseline compatível e válido.")
    return baseline


def _global_coverage(
    partitions: tuple[QualityPartitionBaseline, ...],
    duration: timedelta,
    data_range: DataRange | None,
) -> tuple[CoverageSummary, tuple[AdvancedQualityIssue, ...]]:
    nonempty = [item for item in partitions if item.summary.candle_count]
    first = _required_datetime(nonempty[0].summary.first_open_time) if nonempty else None
    last = _required_datetime(nonempty[-1].summary.last_open_time) if nonempty else None
    gaps = sum(item.internal_gap_count for item in partitions)
    issues: list[AdvancedQualityIssue] = []
    previous_last = None
    for item in nonempty:
        current_first = _required_datetime(item.summary.first_open_time)
        if previous_last is not None and current_first > previous_last + duration:
            count = (current_first - previous_last) // duration - 1
            gaps += count
            issues.append(
                _issue(
                    "gap",
                    "ERROR",
                    QualityIssueCategory.COVERAGE,
                    item.summary.relative_path,
                    current_first.isoformat(),
                )
            )
        previous_last = _required_datetime(item.summary.last_open_time)
    expected = None
    missing_start = 0
    missing_end = 0
    if data_range is not None:
        expected = (data_range.end - data_range.start) // duration
        missing_start = (
            expected if first is None else max(0, (first - data_range.start) // duration)
        )
        missing_end = (
            0 if last is None else max(0, (data_range.end - (last + duration)) // duration)
        )
        if missing_start or missing_end:
            issues.append(
                _issue(
                    "incomplete_range",
                    "ERROR",
                    QualityIssueCategory.COVERAGE,
                )
            )
    return (
        CoverageSummary(
            data_range.start.isoformat() if data_range else None,
            data_range.end.isoformat() if data_range else None,
            first.isoformat() if first else None,
            last.isoformat() if last else None,
            expected,
            sum(item.summary.candle_count for item in partitions),
            gaps,
            missing_start,
            missing_end,
        ),
        tuple(issues),
    )


def _empty_partition(
    relative: str,
    year: int,
    month: int,
    checksum: str,
    issues: tuple[AdvancedQualityIssue, ...],
) -> QualityPartitionBaseline:
    return QualityPartitionBaseline(
        PartitionSummary(relative, year, month, 0, None, None, checksum),
        hashlib.sha256(b"").hexdigest(),
        0,
        issues,
    )


def _composed_logical_checksum(
    partitions: tuple[QualityPartitionBaseline, ...],
) -> str:
    digest = hashlib.sha256()
    for item in partitions:
        digest.update(item.summary.relative_path.encode())
        digest.update(b"\0")
        digest.update(item.logical_checksum.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _scope(plan: QualityScanPlan) -> QualityScanScope:
    return plan.scope or (
        QualityScanScope.RANGE if plan.data_range is not None else QualityScanScope.FULL_DATASET
    )


def _required_datetime(value: str | None) -> datetime:
    if value is None:
        raise MarketDataStorageError("O baseline possui limites inválidos.")
    return datetime.fromisoformat(value)


def _ordered_issues(
    issues: Iterable[AdvancedQualityIssue],
    limit: int,
) -> tuple[AdvancedQualityIssue, ...]:
    return tuple(
        sorted(
            issues,
            key=lambda item: (
                item.category.value,
                item.code,
                item.partition or "",
                item.open_time or "",
            ),
        )[:limit]
    )


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        raise MarketDataStorageError() from None
    return digest.hexdigest()


def _issue(
    code: str,
    severity: str,
    category: QualityIssueCategory,
    partition: str | None = None,
    open_time: str | None = None,
) -> AdvancedQualityIssue:
    return AdvancedQualityIssue(code, severity, category, partition, open_time)


def _category(code: str) -> QualityIssueCategory:
    if code in {"gap", "missing_interval", "incomplete_range"}:
        return QualityIssueCategory.COVERAGE
    if code in {"duplicate", "out_of_order", "misaligned_timestamp"}:
        return QualityIssueCategory.STRUCTURE
    return QualityIssueCategory.CONTENT


def _year_month(path: Path) -> tuple[int, int]:
    try:
        year = int(next(part for part in path.parts if part.startswith("year="))[5:])
        month = int(next(part for part in path.parts if part.startswith("month="))[6:])
    except (StopIteration, ValueError):
        raise MarketDataStorageError("O caminho da partição é inválido.") from None
    return year, month


def _expected_partition_months(data_range: DataRange | None) -> set[tuple[int, int]]:
    if data_range is None:
        return set()
    cursor = datetime(data_range.start.year, data_range.start.month, 1, tzinfo=UTC)
    result: set[tuple[int, int]] = set()
    while cursor < data_range.end:
        result.add((cursor.year, cursor.month))
        cursor = (
            datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
            if cursor.month == 12
            else datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
        )
    return result


def _valid_receipt(receipt: object) -> bool:
    from app.market_data.catalog import ChunkCommitReceipt

    if not isinstance(receipt, ChunkCommitReceipt):
        return False
    sha_values = (receipt.version, receipt.checksum)
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in sha_values
    ):
        return False
    if (
        receipt.chunk_index < 0
        or min(
            receipt.fetched_count,
            receipt.stored_count,
            receipt.duplicate_count,
            receipt.request_count,
        )
        < 0
    ):
        return False
    try:
        start = datetime.fromisoformat(receipt.start)
        end = datetime.fromisoformat(receipt.end)
        committed_at = datetime.fromisoformat(receipt.committed_at)
    except ValueError:
        return False
    return (
        start.tzinfo is not None
        and start.utcoffset() == timedelta(0)
        and end.tzinfo is not None
        and end.utcoffset() == timedelta(0)
        and committed_at.tzinfo is not None
        and committed_at.utcoffset() == timedelta(0)
        and start < end
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _decode_baseline(raw: object) -> QualityScanBaseline:
    if not isinstance(raw, dict):
        raise ValueError
    identity_raw = raw["identity"]
    from app.market_data.domain import Exchange, MarketType

    identity = DatasetIdentity(
        Exchange(identity_raw["exchange"]),
        MarketType(identity_raw["market_type"]),
        identity_raw["symbol"],
        identity_raw["timeframe"],
        DatasetKind(identity_raw["kind"]),
        identity_raw["source"],
        identity_raw["construction_policy"],
        identity_raw["schema_version"],
    )
    raw_range = raw["data_range"]
    data_range = (
        DataRange(
            datetime.fromisoformat(raw_range["start"]),
            datetime.fromisoformat(raw_range["end"]),
        )
        if raw_range
        else None
    )
    partitions = tuple(
        QualityPartitionBaseline(
            PartitionSummary(**item["summary"]),
            item["logical_checksum"],
            item["internal_gap_count"],
            tuple(
                AdvancedQualityIssue(
                    issue["code"],
                    issue["severity"],
                    QualityIssueCategory(issue["category"]),
                    issue.get("partition"),
                    issue.get("open_time"),
                )
                for issue in item["issues"]
            ),
        )
        for item in raw["partitions"]
    )
    return QualityScanBaseline(
        identity,
        raw["dataset_version"],
        raw["scanner_schema_version"],
        raw["scanner_version"],
        QualityScanScope(raw["scope"]),
        data_range,
        partitions,
        CoverageSummary(**raw["coverage"]),
        raw["logical_checksum"],
        tuple(
            AdvancedQualityIssue(
                issue["code"],
                issue["severity"],
                QualityIssueCategory(issue["category"]),
                issue.get("partition"),
                issue.get("open_time"),
            )
            for issue in raw["global_issues"]
        ),
    )
