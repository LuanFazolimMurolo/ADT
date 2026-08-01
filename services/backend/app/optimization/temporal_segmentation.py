"""Pure service for strict deterministic three-way temporal segmentation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from app.market_data.datasets import DatasetManifest, DatasetSnapshot, GapPolicy
from app.market_data.domain import DataRange, require_utc
from app.market_data.errors import MarketDataInconsistencyError, UnsupportedTimeframeError
from app.market_data.snapshots import (
    manifest_coverage,
    validate_snapshot_contract,
)
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
    TemporalWarmupUnavailableError,
)
from app.optimization.temporal_domain import (
    CANONICAL_TEMPORAL_ROLES,
    MAX_SEGMENT_CANDLES,
    MAX_TEMPORAL_COVERAGE_CANDLES,
    MAX_WARMUP_CANDLES,
    TEMPORAL_SEGMENTATION_SCHEMA_VERSION,
    TemporalCoverage,
    TemporalSegment,
    TemporalSegmentationPlan,
    TemporalSegmentationPolicy,
    TemporalSegmentRole,
    TemporalSnapshotReference,
    temporal_plan_values_payload,
    temporal_segment_envelope_payload,
    temporal_segment_id_from_payload,
    temporal_segment_values_payload,
    validate_temporal_segmentation_plan,
)


class TemporalSegmentationService:
    """Build and revalidate plans without reading candles or executing backtests."""

    def create(
        self,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
        selected_coverage: DataRange,
        *,
        train_candles: int,
        validation_candles: int,
        test_candles: int,
        warmup_candles: int = 0,
    ) -> TemporalSegmentationPlan:
        """Partition an explicit selected snapshot range by exact integer counts."""

        reference = _snapshot_reference(snapshot, manifest)
        selected = _coverage(selected_coverage, reference.timeframe)
        _validate_requested_count(train_candles, "train")
        _validate_requested_count(validation_candles, "validation")
        _validate_requested_count(test_candles, "test")
        _validate_requested_warmup(warmup_candles)
        if (
            selected.start < reference.available_coverage.start
            or selected.end > reference.available_coverage.end
        ):
            raise InsufficientTemporalCoverageError(
                "selected coverage must be contained in snapshot coverage"
            )
        counts = (train_candles, validation_candles, test_candles)
        if sum(counts) != selected.candle_count:
            raise TemporalCandleCountMismatchError()

        timeframe = get_timeframe(reference.timeframe)
        try:
            first_context_start = selected.start - timeframe.duration * warmup_candles
        except OverflowError:
            raise InvalidTemporalWarmupError("warmup arithmetic overflowed") from None
        if first_context_start < reference.available_coverage.start:
            raise TemporalWarmupUnavailableError()

        segment_values: list[tuple[TemporalSegmentRole, int, TemporalCoverage, datetime]] = []
        semantic_envelopes: list[dict[str, object]] = []
        cursor = selected.start
        for index, (role, count) in enumerate(zip(CANONICAL_TEMPORAL_ROLES, counts, strict=True)):
            try:
                end = cursor + timeframe.duration * count
                context_start = cursor - timeframe.duration * warmup_candles
            except OverflowError:
                raise InvalidTemporalCoverageError("temporal arithmetic overflowed") from None
            evaluation = TemporalCoverage(DataRange(cursor, end), reference.timeframe, count)
            payload = temporal_segment_values_payload(
                role=role,
                index=index,
                evaluation=evaluation,
                context_start=context_start,
                warmup_candles=warmup_candles,
            )
            segment_values.append((role, index, evaluation, context_start))
            semantic_envelopes.append({"segment": payload, "checksum": document_checksum(payload)})
            cursor = end
        if cursor != selected.end:
            raise TemporalCandleCountMismatchError(
                "calculated segment boundaries diverge from selected coverage"
            )

        identity_payload = temporal_plan_values_payload(
            snapshot=reference,
            selected=selected,
            train_candles=train_candles,
            validation_candles=validation_candles,
            test_candles=test_candles,
            warmup_candles=warmup_candles,
            policy=TemporalSegmentationPolicy.CONTIGUOUS_THREE_WAY,
            schema_version=TEMPORAL_SEGMENTATION_SCHEMA_VERSION,
            segments=semantic_envelopes,
        )
        plan_id = deterministic_id("adt-temporal-segmentation-plan-v1", identity_payload)

        segments: list[TemporalSegment] = []
        for values, semantic in zip(segment_values, semantic_envelopes, strict=True):
            role, index, evaluation, context_start = values
            segment_payload_object = semantic["segment"]
            segment_checksum = semantic["checksum"]
            if not isinstance(segment_payload_object, dict) or not isinstance(
                segment_checksum, str
            ):
                raise AssertionError("internal temporal envelope is invalid")
            segment_id = temporal_segment_id_from_payload(
                plan_id, segment_checksum, segment_payload_object
            )
            segments.append(
                TemporalSegment(
                    role=role,
                    index=index,
                    evaluation=evaluation,
                    context_start=context_start,
                    warmup_candles=warmup_candles,
                    plan_id=plan_id,
                    checksum=segment_checksum,
                    segment_id=segment_id,
                )
            )

        final_payload = temporal_plan_values_payload(
            snapshot=reference,
            selected=selected,
            train_candles=train_candles,
            validation_candles=validation_candles,
            test_candles=test_candles,
            warmup_candles=warmup_candles,
            policy=TemporalSegmentationPolicy.CONTIGUOUS_THREE_WAY,
            schema_version=TEMPORAL_SEGMENTATION_SCHEMA_VERSION,
            segments=[temporal_segment_envelope_payload(item) for item in segments],
        )
        return TemporalSegmentationPlan(
            snapshot=reference,
            selected_coverage=selected,
            train_candles=train_candles,
            validation_candles=validation_candles,
            test_candles=test_candles,
            warmup_candles=warmup_candles,
            segments=tuple(segments),
            checksum=document_checksum(final_payload),
            plan_id=plan_id,
        )

    def validate(self, plan: TemporalSegmentationPlan) -> TemporalSegmentationPlan:
        """Defense-in-depth validation for future consuming services."""

        if not isinstance(plan, TemporalSegmentationPlan):
            raise IncompatibleTemporalDocumentError("temporal plan contract is invalid")
        validate_temporal_segmentation_plan(plan)
        return plan

    def validate_for_snapshot(
        self,
        plan: TemporalSegmentationPlan,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
    ) -> TemporalSegmentationPlan:
        """Prevent a valid plan from being applied to a different snapshot."""

        self.validate(plan)
        current = _snapshot_reference(snapshot, manifest)
        if current != plan.snapshot:
            raise IncompatibleTemporalSnapshotError(
                "temporal plan is bound to a different immutable snapshot"
            )
        return plan

    def from_document(
        self,
        envelope: Mapping[str, object],
        *,
        snapshot: DatasetSnapshot | None = None,
        manifest: DatasetManifest | None = None,
    ) -> TemporalSegmentationPlan:
        """Strictly decode a plan and optionally bind it to current snapshot contracts."""

        from app.optimization.temporal_documents import decode_temporal_document

        plan = decode_temporal_document(envelope)
        if (snapshot is None) != (manifest is None):
            raise IncompatibleTemporalSnapshotError(
                "snapshot and manifest must be supplied together"
            )
        if snapshot is not None and manifest is not None:
            self.validate_for_snapshot(plan, snapshot, manifest)
        return plan


def _snapshot_reference(
    snapshot: DatasetSnapshot,
    manifest: DatasetManifest,
) -> TemporalSnapshotReference:
    if not isinstance(snapshot, DatasetSnapshot) or not isinstance(manifest, DatasetManifest):
        raise IncompatibleTemporalSnapshotError("snapshot and manifest contracts are required")
    if not isinstance(manifest.target_timeframe, str):
        raise InvalidTemporalTimeframeError("snapshot timeframe must be a canonical string")
    try:
        get_timeframe(manifest.target_timeframe)
    except UnsupportedTimeframeError:
        raise InvalidTemporalTimeframeError("snapshot timeframe is unsupported") from None
    try:
        validate_snapshot_contract(snapshot, manifest)
    except MarketDataInconsistencyError as error:
        raise IncompatibleTemporalSnapshotError(error.message) from None
    if manifest.gap_policy is not GapPolicy.STRICT:
        raise IncompatibleTemporalSnapshotError(
            "temporal segmentation requires a STRICT dataset manifest"
        )
    coverage = _coverage(snapshot.data_range, manifest.target_timeframe)
    _validate_strict_manifest_cardinality(manifest)
    return TemporalSnapshotReference(
        snapshot_id=snapshot.snapshot_id,
        snapshot_checksum=snapshot.checksum,
        dataset_key=snapshot.dataset_key,
        dataset_version=snapshot.dataset_version,
        identity=manifest.identity,
        gap_policy=manifest.gap_policy,
        available_coverage=coverage,
    )


def _coverage(data_range: DataRange, timeframe_code: str) -> TemporalCoverage:
    if not isinstance(data_range, DataRange):
        raise InvalidTemporalCoverageError("coverage must use DataRange")
    try:
        start = require_utc(data_range.start, field_name="coverage start")
        end = require_utc(data_range.end, field_name="coverage end")
    except MarketDataInconsistencyError:
        raise NonUtcTemporalTimestampError() from None
    if start >= end:
        raise InvalidTemporalCoverageError()
    try:
        timeframe = get_timeframe(timeframe_code)
    except UnsupportedTimeframeError:
        raise InvalidTemporalTimeframeError() from None
    if not timeframe.validate_open_time(start) or not timeframe.validate_open_time(end):
        raise MisalignedTemporalBoundaryError()
    quotient, remainder = divmod(end - start, timeframe.duration)
    if remainder != timedelta(0):
        raise MisalignedTemporalBoundaryError("coverage duration is not a timeframe multiple")
    if quotient < 1 or quotient > MAX_TEMPORAL_COVERAGE_CANDLES:
        raise InvalidTemporalCoverageError("coverage candle count exceeds the safe plan limit")
    return TemporalCoverage(data_range, timeframe.code, quotient)


def _validate_strict_manifest_cardinality(manifest: DatasetManifest) -> None:
    first, last = manifest_coverage(manifest)
    timeframe = get_timeframe(manifest.target_timeframe)
    try:
        manifest_end = last + timeframe.duration
    except OverflowError:
        raise IncompatibleTemporalSnapshotError("snapshot manifest coverage overflowed") from None
    expected_count, remainder = divmod(manifest_end - first, timeframe.duration)
    if (
        remainder != timedelta(0)
        or isinstance(manifest.candle_count, bool)
        or not isinstance(manifest.candle_count, int)
        or manifest.candle_count != expected_count
    ):
        raise IncompatibleTemporalSnapshotError(
            "STRICT manifest candle count diverges from its temporal coverage"
        )


def _validate_requested_count(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTemporalCandleCountError(f"{label} candle count must be an integer")
    if value < 1 or value > MAX_SEGMENT_CANDLES:
        raise InvalidTemporalCandleCountError(
            f"{label} candle count must be between 1 and {MAX_SEGMENT_CANDLES}"
        )


def _validate_requested_warmup(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTemporalWarmupError("warmup must be an integer")
    if value < 0 or value > MAX_WARMUP_CANDLES:
        raise InvalidTemporalWarmupError(f"warmup must be between 0 and {MAX_WARMUP_CANDLES}")


__all__ = ["TemporalSegmentationService"]
