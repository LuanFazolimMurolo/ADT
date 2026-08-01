"""Immutable deterministic temporal-segmentation contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.market_data.datasets import DatasetIdentity, DatasetKind, GapPolicy
from app.market_data.domain import (
    DataRange,
    Exchange,
    MarketType,
    Timeframe,
    TradingPair,
    require_utc,
)
from app.market_data.errors import MarketDataInconsistencyError, UnsupportedTimeframeError
from app.market_data.snapshots import validate_snapshot_identity
from app.market_data.timeframes import get_timeframe
from app.optimization.canonical import deterministic_id, document_checksum
from app.optimization.errors import (
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
    TemporalIdentifierError,
    TemporalSegmentGapError,
    TemporalSegmentOrderError,
    TemporalSegmentOverlapError,
    TemporalWarmupUnavailableError,
    UnsupportedTemporalSegmentationSchemaError,
)

TEMPORAL_SEGMENTATION_SCHEMA_VERSION = 1
SUPPORTED_TEMPORAL_SEGMENTATION_SCHEMA_VERSIONS = frozenset({TEMPORAL_SEGMENTATION_SCHEMA_VERSION})
MAX_SEGMENT_CANDLES = 10_000_000
MAX_TEMPORAL_COVERAGE_CANDLES = MAX_SEGMENT_CANDLES * 3
MAX_WARMUP_CANDLES = 10_000_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TemporalSegmentRole(StrEnum):
    """Canonical evaluation role of one temporal segment."""

    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"


CANONICAL_TEMPORAL_ROLES = (
    TemporalSegmentRole.TRAIN,
    TemporalSegmentRole.VALIDATION,
    TemporalSegmentRole.TEST,
)


class TemporalSegmentationPolicy(StrEnum):
    """Supported temporal partitioning policy."""

    CONTIGUOUS_THREE_WAY = "CONTIGUOUS_THREE_WAY"


@dataclass(frozen=True, slots=True)
class TemporalCoverage:
    """An aligned half-open range with its exact timeframe slot count."""

    data_range: DataRange
    timeframe: str
    candle_count: int

    def __post_init__(self) -> None:
        validate_temporal_coverage(self)

    @property
    def start(self) -> datetime:
        return self.data_range.start

    @property
    def end(self) -> datetime:
        return self.data_range.end

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class TemporalSnapshotReference:
    """Minimal immutable snapshot and manifest identity needed by Phase 4-02."""

    snapshot_id: str
    snapshot_checksum: str
    dataset_key: str
    dataset_version: str
    identity: DatasetIdentity
    gap_policy: GapPolicy
    available_coverage: TemporalCoverage

    def __post_init__(self) -> None:
        validate_temporal_snapshot_reference(self)

    @property
    def timeframe(self) -> str:
        return self.identity.timeframe


@dataclass(frozen=True, slots=True)
class TemporalSegment:
    """One scored interval plus strictly retrospective read context."""

    role: TemporalSegmentRole
    index: int
    evaluation: TemporalCoverage
    context_start: datetime
    warmup_candles: int
    plan_id: str
    checksum: str
    segment_id: str

    def __post_init__(self) -> None:
        validate_temporal_segment(self)

    @property
    def start(self) -> datetime:
        return self.evaluation.start

    @property
    def end(self) -> datetime:
        return self.evaluation.end

    @property
    def candle_count(self) -> int:
        return self.evaluation.candle_count

    @property
    def timeframe(self) -> str:
        return self.evaluation.timeframe

    @property
    def duration(self) -> timedelta:
        return self.evaluation.duration

    @property
    def context_range(self) -> DataRange:
        return DataRange(self.context_start, self.end)


@dataclass(frozen=True, slots=True)
class TemporalSegmentationPlan:
    """Canonical strict TRAIN -> VALIDATION -> TEST partition of one snapshot."""

    snapshot: TemporalSnapshotReference
    selected_coverage: TemporalCoverage
    train_candles: int
    validation_candles: int
    test_candles: int
    warmup_candles: int
    segments: tuple[TemporalSegment, ...]
    checksum: str
    plan_id: str
    policy: TemporalSegmentationPolicy = TemporalSegmentationPolicy.CONTIGUOUS_THREE_WAY
    schema_version: int = TEMPORAL_SEGMENTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_temporal_segmentation_plan(self)


def validate_temporal_coverage(coverage: TemporalCoverage) -> None:
    """Revalidate an aligned exact-count temporal coverage."""

    if not isinstance(coverage.data_range, DataRange):
        raise InvalidTemporalCoverageError("temporal coverage must use DataRange")
    start = _require_utc(coverage.data_range.start, "coverage start")
    end = _require_utc(coverage.data_range.end, "coverage end")
    if start >= end:
        raise InvalidTemporalCoverageError("temporal coverage must be non-empty")
    timeframe = _resolve_timeframe(coverage.timeframe)
    if not timeframe.validate_open_time(start) or not timeframe.validate_open_time(end):
        raise MisalignedTemporalBoundaryError()
    _validate_coverage_count(coverage.candle_count)
    quotient, remainder = divmod(end - start, timeframe.duration)
    if remainder != timedelta(0):
        raise MisalignedTemporalBoundaryError("coverage duration is not a timeframe multiple")
    if quotient != coverage.candle_count:
        raise TemporalCandleCountMismatchError(
            "coverage candle count diverges from its exact duration"
        )


def validate_temporal_snapshot_reference(reference: TemporalSnapshotReference) -> None:
    """Revalidate the minimal Phase 2C snapshot binding."""

    _validate_nonempty_text(reference.snapshot_id, "snapshot id", maximum=128)
    _validate_sha256(reference.snapshot_checksum, "snapshot checksum")
    _validate_nonempty_text(reference.dataset_key, "dataset key", maximum=512)
    _validate_nonempty_text(reference.dataset_version, "dataset version", maximum=256)
    if not isinstance(reference.identity, DatasetIdentity):
        raise IncompatibleTemporalSnapshotError("snapshot dataset identity is invalid")
    if (
        not isinstance(reference.identity.exchange, Exchange)
        or not isinstance(reference.identity.market_type, MarketType)
        or reference.identity.kind is not DatasetKind.DERIVED
    ):
        raise IncompatibleTemporalSnapshotError("snapshot dataset identity enums are invalid")
    try:
        _validate_nonempty_text(reference.identity.symbol, "snapshot instrument", maximum=65)
        pair = TradingPair.parse(reference.identity.symbol)
    except (AttributeError, MarketDataInconsistencyError):
        raise IncompatibleTemporalSnapshotError("snapshot instrument is invalid") from None
    if pair.symbol != reference.identity.symbol:
        raise IncompatibleTemporalSnapshotError("snapshot instrument is not canonical")
    _resolve_timeframe(reference.identity.timeframe)
    _validate_nonempty_text(reference.identity.source, "dataset source", maximum=128)
    _validate_nonempty_text(
        reference.identity.construction_policy, "construction policy", maximum=256
    )
    if (
        isinstance(reference.identity.schema_version, bool)
        or not isinstance(reference.identity.schema_version, int)
        or reference.identity.schema_version < 1
    ):
        raise IncompatibleTemporalSnapshotError("dataset identity schema version is invalid")
    if reference.identity.key != reference.dataset_key:
        raise IncompatibleTemporalSnapshotError("snapshot dataset key diverges from identity")
    if reference.gap_policy is not GapPolicy.STRICT:
        raise IncompatibleTemporalSnapshotError(
            "temporal slot counts require a STRICT snapshot dataset"
        )
    if not isinstance(reference.available_coverage, TemporalCoverage):
        raise IncompatibleTemporalSnapshotError("snapshot coverage contract is invalid")
    validate_temporal_coverage(reference.available_coverage)
    if reference.identity.timeframe != reference.available_coverage.timeframe:
        raise IncompatibleTemporalSnapshotError("snapshot timeframe diverges from identity")
    try:
        validate_snapshot_identity(
            reference.snapshot_id,
            reference.dataset_key,
            reference.dataset_version,
            reference.snapshot_checksum,
            reference.available_coverage.data_range,
        )
    except MarketDataInconsistencyError as error:
        raise IncompatibleTemporalSnapshotError(error.message) from None


def validate_temporal_segment(segment: TemporalSegment) -> None:
    """Revalidate one segment including its checksum and plan-bound identity."""

    if not isinstance(segment.role, TemporalSegmentRole):
        raise TemporalSegmentOrderError("segment role is invalid")
    if isinstance(segment.index, bool) or not isinstance(segment.index, int):
        raise TemporalSegmentOrderError("segment index must be an integer")
    if segment.index < 0 or segment.index > 2:
        raise TemporalSegmentOrderError("segment index is outside the three-way policy")
    if CANONICAL_TEMPORAL_ROLES[segment.index] is not segment.role:
        raise TemporalSegmentOrderError("segment role and index are not canonically associated")
    if not isinstance(segment.evaluation, TemporalCoverage):
        raise InvalidTemporalCoverageError("segment evaluation coverage is invalid")
    validate_temporal_coverage(segment.evaluation)
    _validate_positive_count(segment.candle_count, "segment candle count")
    _validate_warmup(segment.warmup_candles)
    context_start = _require_utc(segment.context_start, "context start")
    timeframe = _resolve_timeframe(segment.timeframe)
    if not timeframe.validate_open_time(context_start):
        raise MisalignedTemporalBoundaryError("context start is not timeframe-aligned")
    try:
        expected_context_start = segment.start - timeframe.duration * segment.warmup_candles
    except OverflowError:
        raise InvalidTemporalWarmupError("warmup arithmetic overflowed") from None
    if context_start != expected_context_start:
        raise InvalidTemporalWarmupError("context start diverges from retrospective warmup")
    if context_start > segment.start:
        raise InvalidTemporalWarmupError("warmup context cannot read future candles")
    _validate_sha256(segment.plan_id, "plan id", identifier=True)
    expected_checksum = document_checksum(temporal_segment_payload(segment))
    if segment.checksum != expected_checksum:
        raise TemporalChecksumError("segment checksum does not match its payload")
    expected_id = temporal_segment_id(segment.plan_id, segment.checksum, segment)
    if segment.segment_id != expected_id:
        raise TemporalIdentifierError("segment id does not match its plan and payload")


def validate_temporal_segmentation_plan(plan: TemporalSegmentationPlan) -> None:
    """Revalidate all structural, temporal and cryptographic plan invariants."""

    if (
        isinstance(plan.schema_version, bool)
        or not isinstance(plan.schema_version, int)
        or plan.schema_version not in SUPPORTED_TEMPORAL_SEGMENTATION_SCHEMA_VERSIONS
    ):
        raise UnsupportedTemporalSegmentationSchemaError(
            f"unsupported temporal schema version: {plan.schema_version}"
        )
    if plan.policy is not TemporalSegmentationPolicy.CONTIGUOUS_THREE_WAY:
        raise IncompatibleTemporalDocumentError("temporal segmentation policy is unsupported")
    if not isinstance(plan.snapshot, TemporalSnapshotReference):
        raise IncompatibleTemporalSnapshotError("snapshot reference contract is invalid")
    validate_temporal_snapshot_reference(plan.snapshot)
    if not isinstance(plan.selected_coverage, TemporalCoverage):
        raise InvalidTemporalCoverageError("selected coverage contract is invalid")
    validate_temporal_coverage(plan.selected_coverage)
    if plan.selected_coverage.timeframe != plan.snapshot.timeframe:
        raise InvalidTemporalTimeframeError("selected timeframe diverges from snapshot")
    if (
        plan.selected_coverage.start < plan.snapshot.available_coverage.start
        or plan.selected_coverage.end > plan.snapshot.available_coverage.end
    ):
        raise InsufficientTemporalCoverageError("selected coverage exceeds snapshot coverage")

    counts = (plan.train_candles, plan.validation_candles, plan.test_candles)
    for label, count in zip(("train", "validation", "test"), counts, strict=True):
        _validate_positive_count(count, f"{label} candle count")
    _validate_warmup(plan.warmup_candles)
    if sum(counts) != plan.selected_coverage.candle_count:
        raise TemporalCandleCountMismatchError()
    if not isinstance(plan.segments, tuple):
        raise TemporalSegmentOrderError("segments must be a tuple")
    if len(plan.segments) != 3:
        raise TemporalSegmentOrderError("the policy requires exactly three segments")

    expected_start = plan.selected_coverage.start
    for index, (segment, role, count) in enumerate(
        zip(plan.segments, CANONICAL_TEMPORAL_ROLES, counts, strict=True)
    ):
        if not isinstance(segment, TemporalSegment):
            raise TemporalSegmentOrderError("segment contract is invalid")
        validate_temporal_segment(segment)
        if segment.role is not role or segment.index != index:
            raise TemporalSegmentOrderError()
        if segment.plan_id != plan.plan_id:
            raise TemporalIdentifierError("segment is bound to another plan")
        if segment.timeframe != plan.selected_coverage.timeframe:
            raise InvalidTemporalTimeframeError("segment timeframe diverges from plan")
        if segment.candle_count != count:
            raise TemporalCandleCountMismatchError("segment candle count diverges from plan")
        if segment.warmup_candles != plan.warmup_candles:
            raise InvalidTemporalWarmupError("segment warmup diverges from plan")
        if segment.start < expected_start:
            raise TemporalSegmentOverlapError()
        if segment.start > expected_start:
            raise TemporalSegmentGapError()
        expected_start = segment.end
        if segment.context_start < plan.snapshot.available_coverage.start:
            raise TemporalWarmupUnavailableError()
    if expected_start != plan.selected_coverage.end:
        if expected_start < plan.selected_coverage.end:
            raise TemporalSegmentGapError("segments do not consume the selected coverage")
        raise TemporalSegmentOverlapError("segments exceed the selected coverage")

    _validate_sha256(plan.plan_id, "plan id", identifier=True)
    if plan.plan_id != temporal_plan_id(plan):
        raise TemporalIdentifierError("plan id does not match its semantic payload")
    if plan.checksum != document_checksum(temporal_plan_payload(plan)):
        raise TemporalChecksumError("plan checksum does not match its canonical payload")


def temporal_coverage_payload(coverage: TemporalCoverage) -> dict[str, object]:
    return {
        "start": _canonical_datetime(coverage.start),
        "end": _canonical_datetime(coverage.end),
        "timeframe": coverage.timeframe,
        "candle_count": coverage.candle_count,
        "duration_milliseconds": _duration_milliseconds(coverage.duration),
    }


def temporal_snapshot_payload(reference: TemporalSnapshotReference) -> dict[str, object]:
    return {
        "snapshot_id": reference.snapshot_id,
        "snapshot_checksum": reference.snapshot_checksum,
        "dataset_key": reference.dataset_key,
        "dataset_version": reference.dataset_version,
        "identity": {
            "exchange": reference.identity.exchange.value,
            "market_type": reference.identity.market_type.value,
            "symbol": reference.identity.symbol,
            "timeframe": reference.identity.timeframe,
            "kind": reference.identity.kind.value,
            "source": reference.identity.source,
            "construction_policy": reference.identity.construction_policy,
            "schema_version": reference.identity.schema_version,
        },
        "gap_policy": reference.gap_policy.value,
        "available_coverage": temporal_coverage_payload(reference.available_coverage),
    }


def temporal_segment_payload(segment: TemporalSegment) -> dict[str, object]:
    return _temporal_segment_values_payload(
        role=segment.role,
        index=segment.index,
        evaluation=segment.evaluation,
        context_start=segment.context_start,
        warmup_candles=segment.warmup_candles,
    )


def temporal_segment_envelope_payload(segment: TemporalSegment) -> dict[str, object]:
    return {
        "segment": temporal_segment_payload(segment),
        "checksum": segment.checksum,
        "segment_id": segment.segment_id,
    }


def temporal_plan_identity_payload(plan: TemporalSegmentationPlan) -> dict[str, object]:
    return _temporal_plan_values_payload(
        snapshot=plan.snapshot,
        selected=plan.selected_coverage,
        train_candles=plan.train_candles,
        validation_candles=plan.validation_candles,
        test_candles=plan.test_candles,
        warmup_candles=plan.warmup_candles,
        policy=plan.policy,
        schema_version=plan.schema_version,
        segments=[
            {"segment": temporal_segment_payload(segment), "checksum": segment.checksum}
            for segment in plan.segments
        ],
    )


def temporal_plan_payload(plan: TemporalSegmentationPlan) -> dict[str, object]:
    return _temporal_plan_values_payload(
        snapshot=plan.snapshot,
        selected=plan.selected_coverage,
        train_candles=plan.train_candles,
        validation_candles=plan.validation_candles,
        test_candles=plan.test_candles,
        warmup_candles=plan.warmup_candles,
        policy=plan.policy,
        schema_version=plan.schema_version,
        segments=[temporal_segment_envelope_payload(segment) for segment in plan.segments],
    )


def temporal_plan_id(plan: TemporalSegmentationPlan) -> str:
    return deterministic_id(
        "adt-temporal-segmentation-plan-v1", temporal_plan_identity_payload(plan)
    )


def temporal_segment_id(plan_id: str, checksum: str, segment: TemporalSegment) -> str:
    return temporal_segment_id_from_payload(plan_id, checksum, temporal_segment_payload(segment))


def temporal_segment_id_from_payload(
    plan_id: str,
    checksum: str,
    payload: dict[str, object],
) -> str:
    return deterministic_id(
        "adt-temporal-segment-v1",
        {"plan_id": plan_id, "segment_checksum": checksum, "segment": payload},
    )


def temporal_segment_values_payload(
    *,
    role: TemporalSegmentRole,
    index: int,
    evaluation: TemporalCoverage,
    context_start: datetime,
    warmup_candles: int,
) -> dict[str, object]:
    return _temporal_segment_values_payload(
        role=role,
        index=index,
        evaluation=evaluation,
        context_start=context_start,
        warmup_candles=warmup_candles,
    )


def temporal_plan_values_payload(
    *,
    snapshot: TemporalSnapshotReference,
    selected: TemporalCoverage,
    train_candles: int,
    validation_candles: int,
    test_candles: int,
    warmup_candles: int,
    policy: TemporalSegmentationPolicy,
    schema_version: int,
    segments: list[dict[str, object]],
) -> dict[str, object]:
    return _temporal_plan_values_payload(
        snapshot=snapshot,
        selected=selected,
        train_candles=train_candles,
        validation_candles=validation_candles,
        test_candles=test_candles,
        warmup_candles=warmup_candles,
        policy=policy,
        schema_version=schema_version,
        segments=segments,
    )


def _temporal_segment_values_payload(
    *,
    role: TemporalSegmentRole,
    index: int,
    evaluation: TemporalCoverage,
    context_start: datetime,
    warmup_candles: int,
) -> dict[str, object]:
    return {
        "schema_version": TEMPORAL_SEGMENTATION_SCHEMA_VERSION,
        "role": role.value,
        "index": index,
        "evaluation": temporal_coverage_payload(evaluation),
        "context": {
            "start": _canonical_datetime(context_start),
            "end": _canonical_datetime(evaluation.end),
            "warmup_candles": warmup_candles,
        },
    }


def _temporal_plan_values_payload(
    *,
    snapshot: TemporalSnapshotReference,
    selected: TemporalCoverage,
    train_candles: int,
    validation_candles: int,
    test_candles: int,
    warmup_candles: int,
    policy: TemporalSegmentationPolicy,
    schema_version: int,
    segments: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "policy": policy.value,
        "snapshot": temporal_snapshot_payload(snapshot),
        "selected_coverage": temporal_coverage_payload(selected),
        "candle_counts": {
            "train": train_candles,
            "validation": validation_candles,
            "test": test_candles,
        },
        "warmup_candles": warmup_candles,
        "segments": segments,
    }


def _resolve_timeframe(code: object) -> Timeframe:
    if not isinstance(code, str):
        raise InvalidTemporalTimeframeError("timeframe must be a string")
    try:
        return get_timeframe(code)
    except UnsupportedTimeframeError:
        raise InvalidTemporalTimeframeError(f"unsupported temporal timeframe: {code}") from None


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise NonUtcTemporalTimestampError(f"{label} must be a datetime")
    try:
        return require_utc(value, field_name=label)
    except MarketDataInconsistencyError:
        raise NonUtcTemporalTimestampError(f"{label} must be explicitly UTC") from None


def _validate_positive_count(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTemporalCandleCountError(f"{label} must be an integer")
    if value < 1 or value > MAX_SEGMENT_CANDLES:
        raise InvalidTemporalCandleCountError(
            f"{label} must be between 1 and {MAX_SEGMENT_CANDLES}"
        )


def _validate_coverage_count(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTemporalCandleCountError("coverage candle count must be an integer")
    if value < 1 or value > MAX_TEMPORAL_COVERAGE_CANDLES:
        raise InvalidTemporalCandleCountError(
            "coverage candle count exceeds the safe three-segment limit"
        )


def _validate_warmup(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTemporalWarmupError("warmup must be an integer")
    if value < 0 or value > MAX_WARMUP_CANDLES:
        raise InvalidTemporalWarmupError(f"warmup must be between 0 and {MAX_WARMUP_CANDLES}")


def _validate_nonempty_text(value: object, label: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise IncompatibleTemporalSnapshotError(f"{label} is invalid")


def _validate_sha256(value: object, label: str, *, identifier: bool = False) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        if identifier:
            raise TemporalIdentifierError(f"{label} must be lowercase SHA-256")
        raise TemporalChecksumError(f"{label} must be lowercase SHA-256")


def _canonical_datetime(value: datetime) -> str:
    return _require_utc(value, "canonical datetime").isoformat()


def _duration_milliseconds(value: timedelta) -> int:
    return value.days * 86_400_000 + value.seconds * 1_000 + value.microseconds // 1_000


__all__ = [
    "CANONICAL_TEMPORAL_ROLES",
    "MAX_SEGMENT_CANDLES",
    "MAX_TEMPORAL_COVERAGE_CANDLES",
    "MAX_WARMUP_CANDLES",
    "SUPPORTED_TEMPORAL_SEGMENTATION_SCHEMA_VERSIONS",
    "TEMPORAL_SEGMENTATION_SCHEMA_VERSION",
    "TemporalCoverage",
    "TemporalSegment",
    "TemporalSegmentRole",
    "TemporalSegmentationPlan",
    "TemporalSegmentationPolicy",
    "TemporalSnapshotReference",
    "validate_temporal_segmentation_plan",
]
