"""Phase 4-02 deterministic temporal-segmentation tests."""

from __future__ import annotations

import copy
import os
import time
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from app.market_data.datasets import (
    DatasetIdentity,
    DatasetKind,
    DatasetLineage,
    DatasetManifest,
    DatasetSnapshot,
    DatasetState,
    GapPolicy,
    PartitionSummary,
)
from app.market_data.domain import DataRange, Exchange, MarketType
from app.market_data.snapshots import (
    build_snapshot_id,
    expected_snapshot_partitions,
    validate_snapshot_contract,
)
from app.optimization import (
    MAX_SEGMENT_CANDLES,
    IncompatibleTemporalDocumentError,
    IncompatibleTemporalSnapshotError,
    InsufficientTemporalCoverageError,
    InvalidTemporalCandleCountError,
    InvalidTemporalCoverageError,
    InvalidTemporalTimeframeError,
    InvalidTemporalWarmupError,
    MisalignedTemporalBoundaryError,
    NonUtcTemporalTimestampError,
    TemporalCandleCountMismatchError,
    TemporalChecksumError,
    TemporalCoverage,
    TemporalIdentifierError,
    TemporalSegment,
    TemporalSegmentationError,
    TemporalSegmentationPlan,
    TemporalSegmentationPolicy,
    TemporalSegmentationService,
    TemporalSegmentGapError,
    TemporalSegmentOrderError,
    TemporalSegmentOverlapError,
    TemporalSegmentRole,
    TemporalWarmupUnavailableError,
    UnsupportedTemporalSegmentationSchemaError,
    canonical_temporal_document_bytes,
    temporal_to_document,
)
from app.optimization.canonical import document_checksum
from app.optimization.temporal_domain import (
    temporal_segment_id_from_payload,
    temporal_segment_values_payload,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def _contracts(
    *,
    snapshot_id: str | None = None,
    checksum: str = "c" * 64,
    gap_policy: GapPolicy = GapPolicy.STRICT,
    timeframe: str = "1h",
    snapshot_candles: int = 12,
) -> tuple[DatasetSnapshot, DatasetManifest]:
    identity = DatasetIdentity(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTC/USDT",
        timeframe=timeframe,
        kind=DatasetKind.DERIVED,
        source="binance-public",
        construction_policy=f"canonical_ohlcv:1:{gap_policy.value}:crypto_24_7",
        schema_version=1,
    )
    lineage = DatasetLineage(
        source_dataset_key="raw:binance:spot:BTC/USDT:1m",
        source_dataset_version="a" * 64,
        source_checksum="b" * 64,
        source_timeframe="1m",
        target_timeframe=timeframe,
        algorithm="canonical_ohlcv",
        algorithm_version="1",
        gap_policy=gap_policy,
        open_candle_policy="REJECT",
        calendar="crypto_24_7",
        materialized_at=START.isoformat(),
    )
    duration = timedelta(hours=1) if timeframe == "1h" else timedelta(minutes=5)
    manifest_candles = 14
    manifest_end = START + duration * manifest_candles
    snapshot_end = START + duration * snapshot_candles
    partition = PartitionSummary(
        relative_path="derived/year=2026/month=01/candles.parquet",
        year=2026,
        month=1,
        candle_count=manifest_candles,
        first_open_time=START.isoformat(),
        last_open_time=(manifest_end - duration).isoformat(),
        checksum="e" * 64,
    )
    manifest = DatasetManifest(
        identity=identity,
        schema_version=1,
        source_dataset_key=lineage.source_dataset_key,
        source_dataset_version=lineage.source_dataset_version,
        source_checksum=lineage.source_checksum,
        target_dataset_key=identity.key,
        target_version="d" * 64,
        target_checksum=checksum,
        source_timeframe="1m",
        target_timeframe=timeframe,
        gap_policy=gap_policy,
        calendar="crypto_24_7",
        first_open_time=START.isoformat(),
        last_open_time=(manifest_end - duration).isoformat(),
        candle_count=manifest_candles,
        partitions=(partition,),
        source_partitions=(),
        algorithm="canonical_ohlcv",
        algorithm_version="1",
        created_at=START.isoformat(),
        updated_at=START.isoformat(),
        state=DatasetState.COMPLETE,
        lineage=lineage,
    )
    snapshot_range = DataRange(START, snapshot_end)
    actual_snapshot_id = snapshot_id or build_snapshot_id(manifest, snapshot_range)
    snapshot = DatasetSnapshot(
        snapshot_id=actual_snapshot_id,
        dataset_key=identity.key,
        dataset_version=manifest.target_version,
        checksum=checksum,
        data_range=snapshot_range,
        partitions=expected_snapshot_partitions(manifest, snapshot_range),
        manifest_path="dataset-manifest.json",
        created_at=START.isoformat(),
    )
    return snapshot, manifest


def _two_partition_contracts() -> tuple[DatasetSnapshot, DatasetManifest]:
    timeframe = "1d"
    identity = DatasetIdentity(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTC/USDT",
        timeframe=timeframe,
        kind=DatasetKind.DERIVED,
        source="binance-public",
        construction_policy="canonical_ohlcv:1:STRICT:crypto_24_7",
        schema_version=1,
    )
    partitions = (
        PartitionSummary(
            "derived/year=2026/month=01/candles.parquet",
            2026,
            1,
            31,
            START.isoformat(),
            datetime(2026, 1, 31, tzinfo=UTC).isoformat(),
            "1" * 64,
        ),
        PartitionSummary(
            "derived/year=2026/month=02/candles.parquet",
            2026,
            2,
            28,
            datetime(2026, 2, 1, tzinfo=UTC).isoformat(),
            datetime(2026, 2, 28, tzinfo=UTC).isoformat(),
            "2" * 64,
        ),
    )
    lineage = DatasetLineage(
        "raw:binance:spot:BTC/USDT:1h",
        "a" * 64,
        "b" * 64,
        "1h",
        timeframe,
        "canonical_ohlcv",
        "1",
        GapPolicy.STRICT,
        "REJECT",
        "crypto_24_7",
        START.isoformat(),
    )
    manifest = DatasetManifest(
        identity=identity,
        schema_version=1,
        source_dataset_key=lineage.source_dataset_key,
        source_dataset_version=lineage.source_dataset_version,
        source_checksum=lineage.source_checksum,
        target_dataset_key=identity.key,
        target_version="d" * 64,
        target_checksum="c" * 64,
        source_timeframe="1h",
        target_timeframe=timeframe,
        gap_policy=GapPolicy.STRICT,
        calendar="crypto_24_7",
        first_open_time=START.isoformat(),
        last_open_time=datetime(2026, 2, 28, tzinfo=UTC).isoformat(),
        candle_count=59,
        partitions=partitions,
        source_partitions=(),
        algorithm="canonical_ohlcv",
        algorithm_version="1",
        created_at=START.isoformat(),
        updated_at=START.isoformat(),
        state=DatasetState.COMPLETE,
        lineage=lineage,
    )
    data_range = DataRange(START, datetime(2026, 3, 1, tzinfo=UTC))
    snapshot = DatasetSnapshot(
        snapshot_id=build_snapshot_id(manifest, data_range),
        dataset_key=manifest.target_dataset_key,
        dataset_version=manifest.target_version,
        checksum=manifest.target_checksum,
        data_range=data_range,
        partitions=expected_snapshot_partitions(manifest, data_range),
        manifest_path="dataset-manifest.json",
        created_at=START.isoformat(),
    )
    return snapshot, manifest


def _plan(
    *,
    warmup: int = 1,
    selected_start: datetime = START + timedelta(hours=1),
    checksum: str = "c" * 64,
    snapshot_candles: int = 12,
) -> TemporalSegmentationPlan:
    snapshot, manifest = _contracts(checksum=checksum, snapshot_candles=snapshot_candles)
    return TemporalSegmentationService().create(
        snapshot,
        manifest,
        DataRange(selected_start, selected_start + timedelta(hours=9)),
        train_candles=5,
        validation_candles=2,
        test_candles=2,
        warmup_candles=warmup,
    )


def _create_default(
    snapshot: DatasetSnapshot,
    manifest: DatasetManifest,
) -> TemporalSegmentationPlan:
    return TemporalSegmentationService().create(
        snapshot,
        manifest,
        DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
        train_candles=5,
        validation_candles=2,
        test_candles=2,
        warmup_candles=1,
    )


def _resign(document: dict[str, object]) -> None:
    payload = document["temporal_segmentation"]
    document["checksum"] = document_checksum(payload)


def _tampered_range(start: datetime, end: datetime) -> DataRange:
    value = DataRange(START, START + timedelta(hours=1))
    object.__setattr__(value, "start", start)
    object.__setattr__(value, "end", end)
    return value


def _forged_shifted_segment(
    plan: TemporalSegmentationPlan,
    index: int,
    shift: timedelta,
) -> TemporalSegment:
    original = plan.segments[index]
    evaluation = TemporalCoverage(
        DataRange(original.start + shift, original.end + shift),
        original.timeframe,
        original.candle_count,
    )
    context_start = evaluation.start - timedelta(hours=original.warmup_candles)
    payload = temporal_segment_values_payload(
        role=original.role,
        index=original.index,
        evaluation=evaluation,
        context_start=context_start,
        warmup_candles=original.warmup_candles,
    )
    checksum = document_checksum(payload)
    return TemporalSegment(
        role=original.role,
        index=original.index,
        evaluation=evaluation,
        context_start=context_start,
        warmup_candles=original.warmup_candles,
        plan_id=plan.plan_id,
        checksum=checksum,
        segment_id=temporal_segment_id_from_payload(plan.plan_id, checksum, payload),
    )


def _construct_segment_with_role_index(
    plan: TemporalSegmentationPlan,
    role: TemporalSegmentRole,
    index: int,
) -> TemporalSegment:
    original = plan.segments[0]
    payload = temporal_segment_values_payload(
        role=role,
        index=index,
        evaluation=original.evaluation,
        context_start=original.context_start,
        warmup_candles=original.warmup_candles,
    )
    checksum = document_checksum(payload)
    return TemporalSegment(
        role=role,
        index=index,
        evaluation=original.evaluation,
        context_start=original.context_start,
        warmup_candles=original.warmup_candles,
        plan_id=plan.plan_id,
        checksum=checksum,
        segment_id=temporal_segment_id_from_payload(plan.plan_id, checksum, payload),
    )


def test_valid_three_way_segmentation() -> None:
    plan = _plan()
    assert len(plan.segments) == 3
    assert plan.policy is TemporalSegmentationPolicy.CONTIGUOUS_THREE_WAY


def test_roles_have_canonical_order() -> None:
    assert tuple(item.role for item in _plan().segments) == (
        TemporalSegmentRole.TRAIN,
        TemporalSegmentRole.VALIDATION,
        TemporalSegmentRole.TEST,
    )


def test_ranges_are_half_open() -> None:
    plan = _plan()
    assert plan.segments[0].start == START + timedelta(hours=1)
    assert plan.segments[0].end == START + timedelta(hours=6)


def test_train_end_equals_validation_start() -> None:
    plan = _plan()
    assert plan.segments[0].end == plan.segments[1].start


def test_validation_end_equals_test_start() -> None:
    plan = _plan()
    assert plan.segments[1].end == plan.segments[2].start


def test_no_evaluation_slot_belongs_to_two_segments() -> None:
    plan = _plan()
    slots = [
        {segment.start + timedelta(hours=index) for index in range(segment.candle_count)}
        for segment in plan.segments
    ]
    assert slots[0].isdisjoint(slots[1])
    assert slots[0].isdisjoint(slots[2])
    assert slots[1].isdisjoint(slots[2])


def test_integer_counts_calculate_exact_boundaries() -> None:
    assert tuple(item.candle_count for item in _plan().segments) == (5, 2, 2)


def test_counts_consume_selected_coverage_exactly() -> None:
    plan = _plan()
    assert sum(item.candle_count for item in plan.segments) == plan.selected_coverage.candle_count


@pytest.mark.parametrize("counts", [(5, 2, 1), (5, 2, 3)])
def test_missing_or_surplus_counts_are_rejected(counts: tuple[int, int, int]) -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(TemporalCandleCountMismatchError):
        TemporalSegmentationService().create(
            snapshot,
            manifest,
            DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
            train_candles=counts[0],
            validation_candles=counts[1],
            test_candles=counts[2],
        )


@pytest.mark.parametrize("field", ["train", "validation", "test"])
@pytest.mark.parametrize("value", [0, -1, True, MAX_SEGMENT_CANDLES + 1])
def test_invalid_segment_counts_are_rejected(field: str, value: int) -> None:
    snapshot, manifest = _contracts()
    counts: dict[str, int] = {"train": 5, "validation": 2, "test": 2}
    counts[field] = value
    with pytest.raises(InvalidTemporalCandleCountError):
        TemporalSegmentationService().create(
            snapshot,
            manifest,
            DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
            train_candles=counts["train"],
            validation_candles=counts["validation"],
            test_candles=counts["test"],
        )


def test_naive_datetime_is_rejected_at_service_boundary() -> None:
    snapshot, manifest = _contracts()
    selected = _tampered_range(datetime(2026, 1, 1, 1), START + timedelta(hours=10))
    with pytest.raises(NonUtcTemporalTimestampError):
        TemporalSegmentationService().create(
            snapshot, manifest, selected, train_candles=5, validation_candles=2, test_candles=2
        )


def test_non_utc_offset_is_rejected_at_service_boundary() -> None:
    snapshot, manifest = _contracts()
    offset = timezone(timedelta(hours=-3))
    selected = _tampered_range(datetime(2026, 1, 1, 1, tzinfo=offset), START + timedelta(hours=10))
    with pytest.raises(NonUtcTemporalTimestampError):
        TemporalSegmentationService().create(
            snapshot, manifest, selected, train_candles=5, validation_candles=2, test_candles=2
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [(START, START), (START + timedelta(hours=1), START)],
)
def test_empty_or_reversed_range_is_rejected(start: datetime, end: datetime) -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(InvalidTemporalCoverageError):
        TemporalSegmentationService().create(
            snapshot,
            manifest,
            _tampered_range(start, end),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


def test_misaligned_boundary_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(MisalignedTemporalBoundaryError):
        TemporalSegmentationService().create(
            snapshot,
            manifest,
            DataRange(START + timedelta(minutes=30), START + timedelta(hours=9, minutes=30)),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (START - timedelta(hours=1), START + timedelta(hours=8)),
        (START + timedelta(hours=4), START + timedelta(hours=13)),
    ],
)
def test_selected_coverage_outside_snapshot_is_rejected(start: datetime, end: datetime) -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(InsufficientTemporalCoverageError):
        TemporalSegmentationService().create(
            snapshot,
            manifest,
            DataRange(start, end),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


def test_snapshot_and_manifest_timeframe_divergence_is_rejected() -> None:
    snapshot, manifest = _contracts()
    _other_snapshot, other_manifest = _contracts(timeframe="5m")
    with pytest.raises(IncompatibleTemporalSnapshotError):
        TemporalSegmentationService().create(
            snapshot,
            other_manifest,
            DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


def test_unknown_timeframe_is_rejected() -> None:
    snapshot, manifest = _contracts()
    bad_identity = replace(manifest.identity, timeframe="2h")
    bad_manifest = replace(
        manifest,
        identity=bad_identity,
        target_dataset_key=bad_identity.key,
        target_timeframe="2h",
    )
    bad_snapshot = replace(snapshot, dataset_key=bad_identity.key)
    with pytest.raises(InvalidTemporalTimeframeError):
        TemporalSegmentationService().create(
            bad_snapshot,
            bad_manifest,
            snapshot.data_range,
            train_candles=4,
            validation_candles=4,
            test_candles=4,
        )


def test_different_snapshot_id_changes_plan_id() -> None:
    first = _plan(snapshot_candles=12)
    second = _plan(snapshot_candles=13)
    assert first.snapshot.snapshot_id != second.snapshot.snapshot_id
    assert first.plan_id != second.plan_id


def test_different_snapshot_checksum_changes_plan_id() -> None:
    assert _plan(checksum="a" * 64).plan_id != _plan(checksum="b" * 64).plan_id


def test_arbitrary_snapshot_id_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError, match="snapshot_id"):
        _create_default(replace(snapshot, snapshot_id="arbitrary-snapshot"), manifest)


def test_invalid_snapshot_id_format_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError):
        _create_default(replace(snapshot, snapshot_id="!"), manifest)


def test_official_snapshot_id_matches_manifest_and_coverage() -> None:
    snapshot, manifest = _contracts()
    assert snapshot.snapshot_id == "0e0dd077-66d0-c358-dc18-d489b0fcb159"
    assert snapshot.snapshot_id == build_snapshot_id(manifest, snapshot.data_range)
    validate_snapshot_contract(snapshot, manifest)


def test_divergent_snapshot_manifest_path_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError, match="caminho"):
        _create_default(replace(snapshot, manifest_path="other.json"), manifest)


def test_missing_snapshot_partition_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError, match="parti"):
        _create_default(replace(snapshot, partitions=()), manifest)


def test_extra_snapshot_partition_is_rejected() -> None:
    snapshot, manifest = _contracts()
    extra = "partitions/year=2026/month=02/candles.parquet"
    with pytest.raises(IncompatibleTemporalSnapshotError, match="parti"):
        _create_default(replace(snapshot, partitions=(*snapshot.partitions, extra)), manifest)


def test_snapshot_partitions_out_of_order_are_rejected() -> None:
    snapshot, manifest = _two_partition_contracts()
    selected = DataRange(START + timedelta(days=1), START + timedelta(days=10))
    with pytest.raises(IncompatibleTemporalSnapshotError, match="parti"):
        TemporalSegmentationService().create(
            replace(snapshot, partitions=tuple(reversed(snapshot.partitions))),
            manifest,
            selected,
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_key", "derived:other"),
        ("dataset_version", "f" * 64),
        ("checksum", "f" * 64),
    ],
)
def test_snapshot_manifest_identity_divergence_is_rejected(
    field: str,
    value: str,
) -> None:
    snapshot, manifest = _contracts()
    object.__setattr__(snapshot, field, value)
    with pytest.raises(IncompatibleTemporalSnapshotError):
        _create_default(snapshot, manifest)


def test_snapshot_coverage_divergence_is_rejected() -> None:
    snapshot, manifest = _contracts()
    divergent = replace(
        snapshot,
        data_range=DataRange(
            snapshot.data_range.start, snapshot.data_range.end + timedelta(hours=1)
        ),
    )
    with pytest.raises(IncompatibleTemporalSnapshotError):
        _create_default(divergent, manifest)


def test_legitimate_snapshot_reference_keeps_plan_hashes_deterministic() -> None:
    first = _plan()
    second = _plan()
    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.plan_id == second.plan_id
    assert first.checksum == second.checksum


def test_zero_warmup_has_context_equal_to_evaluation_start() -> None:
    plan = _plan(warmup=0)
    assert all(item.context_start == item.start for item in plan.segments)


def test_positive_warmup_is_retrospective() -> None:
    plan = _plan(warmup=1)
    assert all(item.context_start == item.start - timedelta(hours=1) for item in plan.segments)


@pytest.mark.parametrize("warmup", [-1, True])
def test_invalid_warmup_is_rejected(warmup: int) -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(InvalidTemporalWarmupError):
        TemporalSegmentationService().create(
            snapshot,
            manifest,
            DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
            warmup_candles=warmup,
        )


def test_unavailable_train_warmup_is_rejected_without_truncation() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(TemporalWarmupUnavailableError):
        TemporalSegmentationService().create(
            snapshot,
            manifest,
            DataRange(START, START + timedelta(hours=9)),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
            warmup_candles=1,
        )


def test_validation_context_may_use_train_history_only() -> None:
    plan = _plan(warmup=2, selected_start=START + timedelta(hours=2))
    validation = plan.segments[1]
    assert plan.segments[0].start <= validation.context_start < validation.start


def test_test_context_may_use_validation_history_only() -> None:
    plan = _plan(warmup=1)
    test = plan.segments[2]
    assert plan.segments[1].start <= test.context_start < test.start


def test_warmup_does_not_change_scored_count() -> None:
    assert tuple(item.candle_count for item in _plan(warmup=0).segments) == tuple(
        item.candle_count for item in _plan(warmup=1).segments
    )


def test_context_never_extends_past_evaluation_end() -> None:
    assert all(item.context_range.end == item.evaluation.end for item in _plan().segments)


def test_plan_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(_plan(), "warmup_candles", 2)


def test_segments_are_required_to_be_a_tuple() -> None:
    plan = _plan()
    with pytest.raises(TemporalSegmentOrderError, match="tuple"):
        replace(plan, segments=cast(tuple[TemporalSegment, ...], list(plan.segments)))


def test_direct_construction_rejects_segments_out_of_order() -> None:
    plan = _plan()
    with pytest.raises(TemporalSegmentOrderError):
        replace(plan, segments=(plan.segments[1], plan.segments[0], plan.segments[2]))


@pytest.mark.parametrize(
    ("role", "index"),
    [
        (TemporalSegmentRole.TEST, 0),
        (TemporalSegmentRole.TRAIN, 1),
        (TemporalSegmentRole.VALIDATION, 2),
    ],
)
def test_direct_segment_construction_rejects_noncanonical_role_index(
    role: TemporalSegmentRole,
    index: int,
) -> None:
    with pytest.raises(TemporalSegmentOrderError, match="canonically"):
        _construct_segment_with_role_index(_plan(), role, index)


@pytest.mark.parametrize(
    ("field", "value"),
    [("role", TemporalSegmentRole.TEST), ("index", 2)],
)
def test_service_detects_corrupted_segment_role_index(field: str, value: object) -> None:
    plan = _plan()
    object.__setattr__(plan.segments[0], field, value)
    with pytest.raises(TemporalSegmentOrderError, match="canonically"):
        TemporalSegmentationService().validate(plan)


def test_legitimate_segments_retain_deterministic_hashes() -> None:
    first = _plan()
    second = _plan()
    assert tuple((item.checksum, item.segment_id) for item in first.segments) == tuple(
        (item.checksum, item.segment_id) for item in second.segments
    )


def test_direct_construction_rejects_overlap() -> None:
    plan = _plan()
    overlapping = _forged_shifted_segment(plan, 1, -timedelta(hours=1))
    with pytest.raises(TemporalSegmentOverlapError):
        replace(plan, segments=(plan.segments[0], overlapping, plan.segments[2]))


def test_direct_construction_rejects_gap() -> None:
    plan = _plan()
    gapped = _forged_shifted_segment(plan, 1, timedelta(hours=1))
    with pytest.raises(TemporalSegmentGapError):
        replace(plan, segments=(plan.segments[0], gapped, plan.segments[2]))


def test_direct_construction_rejects_cardinality_divergence() -> None:
    with pytest.raises(TemporalCandleCountMismatchError):
        replace(_plan(), test_candles=3)


def test_service_detects_low_level_plan_corruption() -> None:
    plan = _plan()
    object.__setattr__(plan, "warmup_candles", 2)
    with pytest.raises(InvalidTemporalWarmupError):
        TemporalSegmentationService().validate(plan)


def test_service_detects_low_level_snapshot_corruption() -> None:
    plan = _plan()
    object.__setattr__(plan.snapshot, "snapshot_checksum", "a" * 64)
    with pytest.raises(IncompatibleTemporalSnapshotError):
        TemporalSegmentationService().validate(plan)


def test_document_is_canonical_and_stable() -> None:
    assert canonical_temporal_document_bytes(_plan()) == canonical_temporal_document_bytes(_plan())


def test_mapping_order_does_not_change_decoding() -> None:
    plan = _plan()
    document = temporal_to_document(plan)
    reversed_document = dict(reversed(list(document.items())))
    assert TemporalSegmentationService().from_document(reversed_document) == plan


def test_local_timezone_does_not_change_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _plan()
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    try:
        if hasattr(time, "tzset"):
            time.tzset()
        actual = _plan()
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        if hasattr(time, "tzset"):
            time.tzset()
    assert actual == expected


def test_clock_is_not_part_of_plan_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", lambda: 1.0)
    first = _plan()
    monkeypatch.setattr(time, "time", lambda: 9_999_999.0)
    assert _plan() == first


def test_repeated_creation_has_no_randomness() -> None:
    assert _plan() == _plan()


def test_checksum_is_stable() -> None:
    assert _plan().checksum == _plan().checksum


def test_plan_id_is_stable() -> None:
    assert _plan().plan_id == _plan().plan_id


def test_segment_ids_are_stable_and_distinct() -> None:
    first = _plan()
    second = _plan()
    assert tuple(item.segment_id for item in first.segments) == tuple(
        item.segment_id for item in second.segments
    )
    assert len({item.segment_id for item in first.segments}) == 3


def test_boundary_change_changes_plan_hashes() -> None:
    first = _plan(selected_start=START + timedelta(hours=1))
    second = _plan(selected_start=START + timedelta(hours=2))
    assert (first.plan_id, first.checksum) != (second.plan_id, second.checksum)


def test_warmup_change_changes_plan_and_segment_hashes() -> None:
    first = _plan(warmup=0)
    second = _plan(warmup=1)
    assert first.plan_id != second.plan_id
    assert first.segments[0].segment_id != second.segments[0].segment_id


def test_document_round_trip_is_exact() -> None:
    plan = _plan()
    decoded = TemporalSegmentationService().from_document(temporal_to_document(plan))
    assert decoded == plan
    assert temporal_to_document(decoded) == temporal_to_document(plan)


def test_unsupported_document_version_is_rejected() -> None:
    document = copy.deepcopy(temporal_to_document(_plan()))
    payload = cast(dict[str, object], document["temporal_segmentation"])
    payload["schema_version"] = 2
    _resign(document)
    with pytest.raises(UnsupportedTemporalSegmentationSchemaError):
        TemporalSegmentationService().from_document(document)


def test_missing_document_field_is_rejected() -> None:
    document = copy.deepcopy(temporal_to_document(_plan()))
    payload = cast(dict[str, object], document["temporal_segmentation"])
    del payload["warmup_candles"]
    _resign(document)
    with pytest.raises(IncompatibleTemporalDocumentError, match="missing"):
        TemporalSegmentationService().from_document(document)


def test_extra_document_field_is_rejected() -> None:
    document = copy.deepcopy(temporal_to_document(_plan()))
    payload = cast(dict[str, object], document["temporal_segmentation"])
    payload["unexpected"] = 1
    _resign(document)
    with pytest.raises(IncompatibleTemporalDocumentError, match="extra"):
        TemporalSegmentationService().from_document(document)


def test_unknown_role_is_rejected() -> None:
    document = copy.deepcopy(temporal_to_document(_plan()))
    payload = cast(dict[str, object], document["temporal_segmentation"])
    segments = cast(list[dict[str, object]], payload["segments"])
    segment_payload = cast(dict[str, object], segments[0]["segment"])
    segment_payload["role"] = "UNKNOWN"
    segments[0]["checksum"] = document_checksum(segment_payload)
    _resign(document)
    with pytest.raises(IncompatibleTemporalDocumentError, match="unknown"):
        TemporalSegmentationService().from_document(document)


def test_tampered_plan_checksum_is_rejected() -> None:
    document = temporal_to_document(_plan())
    document["checksum"] = "0" * 64
    with pytest.raises(TemporalChecksumError):
        TemporalSegmentationService().from_document(document)


def test_tampered_segment_checksum_is_rejected() -> None:
    document = copy.deepcopy(temporal_to_document(_plan()))
    payload = cast(dict[str, object], document["temporal_segmentation"])
    segments = cast(list[dict[str, object]], payload["segments"])
    segments[0]["checksum"] = "0" * 64
    _resign(document)
    with pytest.raises(TemporalChecksumError):
        TemporalSegmentationService().from_document(document)


def test_tampered_plan_id_is_rejected() -> None:
    document = temporal_to_document(_plan())
    document["plan_id"] = "0" * 64
    with pytest.raises(TemporalIdentifierError):
        TemporalSegmentationService().from_document(document)


def test_tampered_segment_id_is_rejected() -> None:
    document = copy.deepcopy(temporal_to_document(_plan()))
    payload = cast(dict[str, object], document["temporal_segmentation"])
    segments = cast(list[dict[str, object]], payload["segments"])
    segments[0]["segment_id"] = "0" * 64
    _resign(document)
    with pytest.raises(TemporalIdentifierError):
        TemporalSegmentationService().from_document(document)


def test_document_rejects_re_signed_arbitrary_snapshot_id() -> None:
    document = copy.deepcopy(temporal_to_document(_plan()))
    payload = cast(dict[str, object], document["temporal_segmentation"])
    snapshot = cast(dict[str, object], payload["snapshot"])
    snapshot["snapshot_id"] = "arbitrary-snapshot"
    _resign(document)
    with pytest.raises(IncompatibleTemporalSnapshotError, match="snapshot_id"):
        TemporalSegmentationService().from_document(document)


def test_faithful_phase2c_snapshot_and_manifest_are_bound() -> None:
    snapshot, manifest = _contracts()
    plan = _plan()
    assert TemporalSegmentationService().validate_for_snapshot(plan, snapshot, manifest) is plan


def test_plan_cannot_be_applied_to_another_snapshot() -> None:
    snapshot, manifest = _contracts(snapshot_candles=13)
    with pytest.raises(IncompatibleTemporalSnapshotError):
        TemporalSegmentationService().validate_for_snapshot(_plan(), snapshot, manifest)


def test_non_strict_snapshot_is_rejected() -> None:
    snapshot, manifest = _contracts(gap_policy=GapPolicy.SKIP_INCOMPLETE)
    with pytest.raises(IncompatibleTemporalSnapshotError, match="STRICT"):
        TemporalSegmentationService().create(
            snapshot,
            manifest,
            DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


def test_temporal_service_does_not_execute_backtest(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.backtesting.engine import DeterministicBacktestEngine

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("backtest execution is outside Phase 4-02")

    monkeypatch.setattr(DeterministicBacktestEngine, "run", forbidden)
    _plan()


def test_temporal_service_publishes_no_result_artifact(tmp_path: Path) -> None:
    before = tuple(tmp_path.iterdir())
    _plan()
    assert tuple(tmp_path.iterdir()) == before


def test_document_returns_defensive_fresh_objects() -> None:
    plan = _plan()
    first = temporal_to_document(plan)
    second = temporal_to_document(plan)
    assert first == second
    assert first is not second


def test_selected_coverage_may_be_smaller_than_snapshot() -> None:
    plan = _plan()
    assert plan.snapshot.available_coverage.start < plan.selected_coverage.start
    assert plan.selected_coverage.end < plan.snapshot.available_coverage.end


def test_context_is_not_counted_as_evaluation() -> None:
    plan = _plan(warmup=2, selected_start=START + timedelta(hours=2))
    train = plan.segments[0]
    assert train.context_range.end - train.context_range.start == timedelta(hours=7)
    assert train.duration == timedelta(hours=5)


def test_manifest_checksum_mismatch_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError):
        TemporalSegmentationService().create(
            replace(snapshot, checksum="e" * 64),
            manifest,
            DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


def test_target_checksum_divergence_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError):
        _create_default(snapshot, replace(manifest, target_checksum="f" * 64))


def test_incomplete_manifest_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError, match="COMPLETE"):
        TemporalSegmentationService().create(
            snapshot,
            replace(manifest, state=DatasetState.STALE),
            DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


def test_manifest_coverage_count_divergence_is_rejected() -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError, match="candle count"):
        TemporalSegmentationService().create(
            snapshot,
            replace(manifest, candle_count=11),
            DataRange(START + timedelta(hours=1), START + timedelta(hours=10)),
            train_candles=5,
            validation_candles=2,
            test_candles=2,
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "identity_object",
        "timeframe_list",
        "timeframe_none",
        "state_string",
        "gap_policy_string",
        "partitions_list",
        "partition_non_text",
        "dataset_key_non_text",
        "checksum_non_text",
        "snapshot_object",
        "manifest_object",
    ],
)
def test_malformed_snapshot_inputs_raise_stable_temporal_domain_errors(
    corruption: str,
) -> None:
    snapshot, manifest = _contracts()
    snapshot_input = snapshot
    manifest_input = manifest
    if corruption == "identity_object":
        object.__setattr__(manifest, "identity", object())
    elif corruption == "timeframe_list":
        object.__setattr__(manifest, "target_timeframe", [])
    elif corruption == "timeframe_none":
        object.__setattr__(manifest, "target_timeframe", None)
    elif corruption == "state_string":
        object.__setattr__(manifest, "state", "COMPLETE")
    elif corruption == "gap_policy_string":
        object.__setattr__(manifest, "gap_policy", "STRICT")
    elif corruption == "partitions_list":
        object.__setattr__(snapshot, "partitions", list(snapshot.partitions))
    elif corruption == "partition_non_text":
        object.__setattr__(snapshot, "partitions", (1,))
    elif corruption == "dataset_key_non_text":
        object.__setattr__(snapshot, "dataset_key", [])
    elif corruption == "checksum_non_text":
        object.__setattr__(snapshot, "checksum", None)
    elif corruption == "snapshot_object":
        snapshot_input = cast(DatasetSnapshot, object())
    elif corruption == "manifest_object":
        manifest_input = cast(DatasetManifest, object())
    with pytest.raises(TemporalSegmentationError):
        _create_default(snapshot_input, manifest_input)


def test_document_requires_snapshot_and_manifest_together() -> None:
    snapshot, _manifest = _contracts()
    with pytest.raises(IncompatibleTemporalSnapshotError):
        TemporalSegmentationService().from_document(
            temporal_to_document(_plan()), snapshot=snapshot
        )


def test_environment_timezone_is_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", original or "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    assert _plan().selected_coverage.start.tzinfo is UTC
