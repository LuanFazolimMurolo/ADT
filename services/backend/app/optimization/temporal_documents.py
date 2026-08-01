"""Strict versioned document codec for temporal-segmentation plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from app.market_data.datasets import DatasetIdentity, DatasetKind, GapPolicy
from app.market_data.domain import DataRange, Exchange, MarketType, require_utc
from app.market_data.errors import InvalidDataRangeError, MarketDataInconsistencyError
from app.optimization.canonical import canonical_json_bytes, document_checksum
from app.optimization.errors import (
    IncompatibleSearchSpaceDocumentError,
    IncompatibleTemporalDocumentError,
    NonUtcTemporalTimestampError,
    TemporalChecksumError,
    UnsupportedTemporalSegmentationSchemaError,
)
from app.optimization.temporal_domain import (
    SUPPORTED_TEMPORAL_SEGMENTATION_SCHEMA_VERSIONS,
    TEMPORAL_SEGMENTATION_SCHEMA_VERSION,
    TemporalCoverage,
    TemporalSegment,
    TemporalSegmentationPlan,
    TemporalSegmentationPolicy,
    TemporalSegmentRole,
    TemporalSnapshotReference,
    temporal_plan_payload,
    validate_temporal_segmentation_plan,
)

TemporalDocumentEnvelope = dict[str, object]


def temporal_to_document(plan: TemporalSegmentationPlan) -> TemporalDocumentEnvelope:
    """Return a fresh JSON-compatible envelope after full revalidation."""

    validate_temporal_segmentation_plan(plan)
    return {
        "temporal_segmentation": temporal_plan_payload(plan),
        "checksum": plan.checksum,
        "plan_id": plan.plan_id,
    }


def canonical_temporal_document_bytes(plan: TemporalSegmentationPlan) -> bytes:
    return canonical_json_bytes(temporal_to_document(plan))


def decode_temporal_document(envelope: Mapping[str, object]) -> TemporalSegmentationPlan:
    """Strictly reconstruct and cryptographically verify a canonical plan."""

    root = _mapping(envelope, "temporal envelope")
    _exact_fields(root, {"temporal_segmentation", "checksum", "plan_id"}, "temporal envelope")
    payload = _mapping(root["temporal_segmentation"], "temporal payload")
    checksum = _text(root["checksum"], "plan checksum")
    plan_id = _text(root["plan_id"], "plan id")
    if _document_checksum(payload) != checksum:
        raise TemporalChecksumError("plan document checksum does not match its payload")

    _exact_fields(
        payload,
        {
            "schema_version",
            "policy",
            "snapshot",
            "selected_coverage",
            "candle_counts",
            "warmup_candles",
            "segments",
        },
        "temporal payload",
    )
    schema_version = _integer(payload["schema_version"], "schema version")
    if schema_version not in SUPPORTED_TEMPORAL_SEGMENTATION_SCHEMA_VERSIONS:
        raise UnsupportedTemporalSegmentationSchemaError(
            f"unsupported temporal schema version: {schema_version}"
        )
    try:
        policy = TemporalSegmentationPolicy(_text(payload["policy"], "policy"))
    except ValueError:
        raise IncompatibleTemporalDocumentError("unknown temporal policy") from None
    snapshot = _decode_snapshot(payload["snapshot"])
    selected = _decode_coverage(payload["selected_coverage"], "selected coverage")
    counts = _mapping(payload["candle_counts"], "candle counts")
    _exact_fields(counts, {"train", "validation", "test"}, "candle counts")
    train_candles = _integer(counts["train"], "train candle count")
    validation_candles = _integer(counts["validation"], "validation candle count")
    test_candles = _integer(counts["test"], "test candle count")
    warmup_candles = _integer(payload["warmup_candles"], "warmup candles")
    raw_segments = _sequence(payload["segments"], "segments")
    segments = tuple(_decode_segment(item, plan_id) for item in raw_segments)
    return TemporalSegmentationPlan(
        snapshot=snapshot,
        selected_coverage=selected,
        train_candles=train_candles,
        validation_candles=validation_candles,
        test_candles=test_candles,
        warmup_candles=warmup_candles,
        segments=segments,
        checksum=checksum,
        plan_id=plan_id,
        policy=policy,
        schema_version=schema_version,
    )


def _decode_snapshot(raw: object) -> TemporalSnapshotReference:
    value = _mapping(raw, "snapshot")
    _exact_fields(
        value,
        {
            "snapshot_id",
            "snapshot_checksum",
            "dataset_key",
            "dataset_version",
            "identity",
            "gap_policy",
            "available_coverage",
        },
        "snapshot",
    )
    identity_value = _mapping(value["identity"], "dataset identity")
    _exact_fields(
        identity_value,
        {
            "exchange",
            "market_type",
            "symbol",
            "timeframe",
            "kind",
            "source",
            "construction_policy",
            "schema_version",
        },
        "dataset identity",
    )
    try:
        identity = DatasetIdentity(
            exchange=Exchange(_text(identity_value["exchange"], "exchange")),
            market_type=MarketType(_text(identity_value["market_type"], "market type")),
            symbol=_text(identity_value["symbol"], "symbol"),
            timeframe=_text(identity_value["timeframe"], "timeframe"),
            kind=DatasetKind(_text(identity_value["kind"], "dataset kind")),
            source=_text(identity_value["source"], "dataset source"),
            construction_policy=_text(identity_value["construction_policy"], "construction policy"),
            schema_version=_integer(identity_value["schema_version"], "identity schema version"),
        )
        gap_policy = GapPolicy(_text(value["gap_policy"], "gap policy"))
    except ValueError:
        raise IncompatibleTemporalDocumentError("snapshot enum value is unknown") from None
    return TemporalSnapshotReference(
        snapshot_id=_text(value["snapshot_id"], "snapshot id"),
        snapshot_checksum=_text(value["snapshot_checksum"], "snapshot checksum"),
        dataset_key=_text(value["dataset_key"], "dataset key"),
        dataset_version=_text(value["dataset_version"], "dataset version"),
        identity=identity,
        gap_policy=gap_policy,
        available_coverage=_decode_coverage(value["available_coverage"], "available coverage"),
    )


def _decode_coverage(raw: object, label: str) -> TemporalCoverage:
    value = _mapping(raw, label)
    _exact_fields(
        value,
        {"start", "end", "timeframe", "candle_count", "duration_milliseconds"},
        label,
    )
    start = _datetime(value["start"], f"{label} start")
    end = _datetime(value["end"], f"{label} end")
    try:
        data_range = DataRange(start, end)
    except InvalidDataRangeError as error:
        raise IncompatibleTemporalDocumentError(error.message) from None
    coverage = TemporalCoverage(
        data_range=data_range,
        timeframe=_text(value["timeframe"], f"{label} timeframe"),
        candle_count=_integer(value["candle_count"], f"{label} candle count"),
    )
    expected_duration = (
        coverage.duration.days * 86_400_000
        + coverage.duration.seconds * 1_000
        + coverage.duration.microseconds // 1_000
    )
    if _integer(value["duration_milliseconds"], f"{label} duration") != expected_duration:
        raise IncompatibleTemporalDocumentError(f"{label} duration is inconsistent")
    return coverage


def _decode_segment(raw: object, plan_id: str) -> TemporalSegment:
    envelope = _mapping(raw, "segment envelope")
    _exact_fields(envelope, {"segment", "checksum", "segment_id"}, "segment envelope")
    payload = _mapping(envelope["segment"], "segment payload")
    checksum = _text(envelope["checksum"], "segment checksum")
    if _document_checksum(payload) != checksum:
        raise TemporalChecksumError("segment checksum does not match its payload")
    _exact_fields(
        payload, {"schema_version", "role", "index", "evaluation", "context"}, "segment payload"
    )
    schema_version = _integer(payload["schema_version"], "segment schema version")
    if schema_version != TEMPORAL_SEGMENTATION_SCHEMA_VERSION:
        raise UnsupportedTemporalSegmentationSchemaError(
            f"unsupported segment schema version: {schema_version}"
        )
    try:
        role = TemporalSegmentRole(_text(payload["role"], "segment role"))
    except ValueError:
        raise IncompatibleTemporalDocumentError("unknown temporal segment role") from None
    evaluation = _decode_coverage(payload["evaluation"], "segment evaluation")
    context = _mapping(payload["context"], "segment context")
    _exact_fields(context, {"start", "end", "warmup_candles"}, "segment context")
    context_end = _datetime(context["end"], "context end")
    if context_end != evaluation.end:
        raise IncompatibleTemporalDocumentError("context end must equal evaluation end")
    return TemporalSegment(
        role=role,
        index=_integer(payload["index"], "segment index"),
        evaluation=evaluation,
        context_start=_datetime(context["start"], "context start"),
        warmup_candles=_integer(context["warmup_candles"], "segment warmup"),
        plan_id=plan_id,
        checksum=checksum,
        segment_id=_text(envelope["segment_id"], "segment id"),
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise IncompatibleTemporalDocumentError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise IncompatibleTemporalDocumentError(f"{label} keys must be strings")
    return value


def _document_checksum(payload: Mapping[str, object]) -> str:
    try:
        return document_checksum(dict(payload))
    except IncompatibleSearchSpaceDocumentError as error:
        raise IncompatibleTemporalDocumentError("temporal payload is not canonical JSON") from error


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise IncompatibleTemporalDocumentError(f"{label} must be an array")
    return value


def _exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise IncompatibleTemporalDocumentError(
            f"{label} fields are incompatible; missing={missing}, extra={extra}"
        )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise IncompatibleTemporalDocumentError(f"{label} must be a string")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IncompatibleTemporalDocumentError(f"{label} must be an integer")
    return value


def _datetime(value: object, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise IncompatibleTemporalDocumentError(f"{label} is not ISO-8601") from None
    try:
        normalized = require_utc(parsed, field_name=label)
    except MarketDataInconsistencyError:
        raise NonUtcTemporalTimestampError(f"{label} must be explicitly UTC") from None
    if text != normalized.isoformat():
        raise IncompatibleTemporalDocumentError(f"{label} is not canonical UTC text")
    return normalized


__all__ = [
    "TemporalDocumentEnvelope",
    "canonical_temporal_document_bytes",
    "decode_temporal_document",
    "temporal_to_document",
]
