"""Persisted read-only RAW quality inspection for Phase 7-04."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from app.cli import _quality_baseline_path
from app.market_data.advanced_quality import (
    SCANNER_SCHEMA_VERSION,
    SCANNER_VERSION,
    save_quality_baseline,
)
from app.market_data.datasets import (
    AdvancedQualityIssue,
    CoverageSummary,
    DatasetIdentity,
    DatasetKind,
    PartitionSummary,
    QualityIssueCategory,
    QualityPartitionBaseline,
    QualityScanBaseline,
    QualityScanScope,
)
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.market_data.integrity import (
    LEGACY_RAW_DATASET_VERSION_ALGORITHM,
    RAW_DATASET_VERSION_ALGORITHM,
)
from app.market_data.operations import MarketDatasetSelector
from app.market_data.raw_dataset_query import (
    RawDatasetIntegritySummary,
    RawDatasetSnapshot,
)
from app.market_data.raw_quality_query import (
    LocalRawQualityReadService,
    RawQualityStatus,
    raw_quality_baseline_path,
    raw_quality_identity,
)
from app.market_data.storage import compose_raw_dataset_version
from app.market_data.timeframes import get_timeframe

UPDATED_AT = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _selector() -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1h"),
    )


def _snapshot(
    *,
    version: str = "a" * 64,
    version_algorithm: str = RAW_DATASET_VERSION_ALGORITHM,
) -> RawDatasetSnapshot:
    selector = _selector()

    return RawDatasetSnapshot(
        dataset=selector,
        first_open_time=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
        last_open_time=datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
        coverage_start=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
        coverage_end=datetime(2026, 8, 1, 3, 0, tzinfo=UTC),
        candle_count=3,
        version=version,
        version_algorithm=version_algorithm,
        updated_at=UPDATED_AT,
        integrity=RawDatasetIntegritySummary(
            present=False,
            schema_version=None,
            checksum_algorithm=None,
            partition_count=0,
        ),
    )


def _identity(
    *,
    symbol: str = "BTC/USDT",
) -> DatasetIdentity:
    selector = _selector()

    return DatasetIdentity(
        selector.exchange,
        selector.market_type,
        symbol,
        selector.timeframe.code,
        DatasetKind.RAW,
        "canonical_parquet",
        "source_native",
        1,
    )


def _baseline(
    *,
    identity: DatasetIdentity | None = None,
    dataset_version: str = "a" * 64,
    dataset_version_algorithm: str = RAW_DATASET_VERSION_ALGORITHM,
    scanner_schema_version: int = SCANNER_SCHEMA_VERSION,
    scanner_version: str = SCANNER_VERSION,
    scope: QualityScanScope = QualityScanScope.FULL_DATASET,
) -> QualityScanBaseline:
    selected_identity = identity or _identity()
    relative_path = (
        "exchange=binance/market=spot/base=BTC/quote=USDT/"
        "timeframe=1h/year=2026/month=08/candles.parquet"
    )
    logical_checksum = "b" * 64

    partition_issue = AdvancedQualityIssue(
        code="gap",
        severity="ERROR",
        category=QualityIssueCategory.COVERAGE,
        partition=relative_path,
        open_time="2026-08-01T01:00:00+00:00",
    )
    global_issue = AdvancedQualityIssue(
        code="catalog_warning",
        severity="WARNING",
        category=QualityIssueCategory.CATALOG,
        partition="do-not-expose/catalog/location",
        open_time=None,
    )

    partition = QualityPartitionBaseline(
        summary=PartitionSummary(
            relative_path=relative_path,
            year=2026,
            month=8,
            candle_count=3,
            first_open_time="2026-08-01T00:00:00+00:00",
            last_open_time="2026-08-01T02:00:00+00:00",
            checksum="c" * 64,
        ),
        logical_checksum=logical_checksum,
        internal_gap_count=0,
        issues=(partition_issue,),
    )

    return QualityScanBaseline(
        identity=selected_identity,
        dataset_version=dataset_version,
        scanner_schema_version=scanner_schema_version,
        scanner_version=scanner_version,
        scope=scope,
        data_range=None,
        partitions=(partition,),
        coverage=CoverageSummary(
            requested_start=None,
            requested_end=None,
            first_open_time="2026-08-01T00:00:00+00:00",
            last_open_time="2026-08-01T02:00:00+00:00",
            expected_count=None,
            observed_count=3,
            internal_gap_count=0,
            missing_at_start=0,
            missing_at_end=0,
        ),
        logical_checksum=compose_raw_dataset_version(((relative_path, logical_checksum),)),
        global_issues=(global_issue,),
        dataset_version_algorithm=dataset_version_algorithm,
    )


class FakeDatasetReader:
    def __init__(
        self,
        snapshot: RawDatasetSnapshot,
        events: list[str],
    ) -> None:
        self.snapshot = snapshot
        self.events = events

    def get(self, dataset_id: str) -> RawDatasetSnapshot:
        self.events.append("catalog_read")
        assert dataset_id == self.snapshot.dataset_id
        return self.snapshot


class FakeSnapshotLocker:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    @contextmanager
    def snapshot(self, dataset_key: str) -> Iterator[None]:
        assert dataset_key == _selector().canonical_key
        self.events.append("dataset_lock_enter")
        try:
            yield
        finally:
            self.events.append("dataset_lock_exit")


def _service(
    root: Path,
    *,
    snapshot: RawDatasetSnapshot | None = None,
    issue_sample_limit: int = 25,
) -> tuple[
    LocalRawQualityReadService,
    RawDatasetSnapshot,
    list[str],
]:
    selected_snapshot = snapshot or _snapshot()
    events: list[str] = []

    return (
        LocalRawQualityReadService(
            dataset_reader=FakeDatasetReader(selected_snapshot, events),
            lock_manager=FakeSnapshotLocker(events),
            root=root,
            manifest_schema_version=1,
            issue_sample_limit=issue_sample_limit,
        ),
        selected_snapshot,
        events,
    )


def _persist(
    root: Path,
    baseline: QualityScanBaseline,
    *,
    expected_identity: DatasetIdentity | None = None,
) -> Path:
    path = raw_quality_baseline_path(
        root,
        expected_identity or baseline.identity,
    )
    save_quality_baseline(path, baseline, root)
    return path


def test_baseline_path_matches_existing_cli_workflow(tmp_path: Path) -> None:
    identity = _identity()

    assert raw_quality_baseline_path(tmp_path, identity) == _quality_baseline_path(
        tmp_path,
        identity.key,
        QualityScanScope.FULL_DATASET,
    )


def test_current_baseline_is_sanitized_and_read_only(tmp_path: Path) -> None:
    service, snapshot, events = _service(tmp_path)
    path = _persist(tmp_path, _baseline())

    before = path.read_bytes()
    before_mtime_ns = path.stat().st_mtime_ns

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.CURRENT
    assert result.dataset == snapshot.dataset
    assert result.dataset_version == snapshot.version
    assert result.baseline_dataset_version == snapshot.version
    assert result.version_algorithm == snapshot.version_algorithm
    assert result.baseline_version_algorithm == snapshot.version_algorithm
    assert result.scanner_schema_version == SCANNER_SCHEMA_VERSION
    assert result.scanner_version == SCANNER_VERSION
    assert result.partition_count == 1

    assert result.coverage is not None
    assert result.coverage.expected_count is None
    assert result.coverage.observed_count == 3
    assert result.coverage.internal_gap_count == 0

    assert result.issue_totals is not None
    assert result.issue_totals.total == 2
    assert result.issue_totals.errors == 1
    assert result.issue_totals.warnings == 1
    assert result.issue_totals.other == 0

    assert len(result.issues) == 2
    assert all(not hasattr(issue, "partition") for issue in result.issues)
    assert all(not hasattr(issue, "relative_path") for issue in result.issues)

    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime_ns

    assert events == [
        "dataset_lock_enter",
        "catalog_read",
        "dataset_lock_exit",
    ]


def test_missing_baseline_returns_missing_without_fabrication(
    tmp_path: Path,
) -> None:
    service, snapshot, _events = _service(tmp_path)

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.MISSING
    assert result.baseline_dataset_version is None
    assert result.scanner_version is None
    assert result.coverage is None
    assert result.partition_count is None
    assert result.issue_totals is None
    assert result.issues == ()


def test_dataset_version_mismatch_is_stale(tmp_path: Path) -> None:
    service, snapshot, _events = _service(tmp_path)
    _persist(
        tmp_path,
        _baseline(dataset_version="d" * 64),
    )

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.STALE
    assert result.baseline_dataset_version == "d" * 64
    assert result.dataset_version == "a" * 64


def test_version_algorithm_mismatch_is_stale(tmp_path: Path) -> None:
    service, snapshot, _events = _service(tmp_path)
    _persist(
        tmp_path,
        _baseline(
            dataset_version_algorithm=LEGACY_RAW_DATASET_VERSION_ALGORITHM,
        ),
    )

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.STALE


def test_old_scanner_version_is_stale(tmp_path: Path) -> None:
    service, snapshot, _events = _service(tmp_path)
    _persist(
        tmp_path,
        _baseline(scanner_version="phase2c-old"),
    )

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.STALE
    assert result.scanner_version == "phase2c-old"


def test_corrupted_envelope_returns_invalid(tmp_path: Path) -> None:
    service, snapshot, _events = _service(tmp_path)
    path = _persist(tmp_path, _baseline())

    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["checksum"] = "0" * 64
    path.write_text(
        json.dumps(envelope),
        encoding="utf-8",
    )

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.INVALID
    assert result.coverage is None
    assert result.issues == ()


def test_semantic_identity_mismatch_returns_invalid(tmp_path: Path) -> None:
    service, snapshot, _events = _service(tmp_path)
    expected_identity = raw_quality_identity(
        snapshot.dataset,
        manifest_schema_version=1,
    )
    invalid_identity = replace(
        expected_identity,
        symbol="ETH/USDT",
    )

    _persist(
        tmp_path,
        _baseline(identity=invalid_identity),
        expected_identity=expected_identity,
    )

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.INVALID


def test_non_full_dataset_baseline_returns_invalid(tmp_path: Path) -> None:
    service, snapshot, _events = _service(tmp_path)

    _persist(
        tmp_path,
        _baseline(scope=QualityScanScope.RANGE),
    )

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.INVALID


def test_issue_sample_is_bounded_deterministically(tmp_path: Path) -> None:
    service, snapshot, _events = _service(
        tmp_path,
        issue_sample_limit=1,
    )
    _persist(tmp_path, _baseline())

    result = service.inspect(snapshot.dataset_id)

    assert result.status is RawQualityStatus.CURRENT
    assert result.issue_totals is not None
    assert result.issue_totals.total == 2
    assert len(result.issues) == 1

    # CATALOG sorts before COVERAGE using the scanner's canonical issue ordering.
    assert result.issues[0].category is QualityIssueCategory.CATALOG
    assert result.issues[0].code == "catalog_warning"


def test_service_has_no_scanner_dependency(tmp_path: Path) -> None:
    service, _snapshot_value, _events = _service(tmp_path)

    assert not hasattr(service, "scanner")
    assert not hasattr(service, "quality_scanner")
