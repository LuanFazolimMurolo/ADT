"""Administrative market-data operation request and response contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from app.api.schemas.common import ApiSchema
from app.market_data.domain import DataRange, Exchange, MarketType
from app.market_data.operations import (
    MAX_DATASET_ID_LENGTH,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MarketDatasetSelector,
    MarketOperationFailureCode,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    OperationProgress,
    OperationResult,
    SanitizedOperationFailure,
    WorkerLease,
    decode_dataset_id,
    encode_dataset_id,
)
from app.services.market_operations import (
    IncrementalMarketOperationPlanPreview,
    MarketOperationPlanPreview,
)

_DATASET_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _validate_utc_datetime(value: datetime) -> datetime:
    """Require explicit UTC rather than silently normalizing another offset."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must use UTC.")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_validate_utc_datetime)]
DatasetId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=MAX_DATASET_ID_LENGTH,
        pattern=_DATASET_ID_PATTERN,
    ),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
        pattern=_IDEMPOTENCY_KEY_PATTERN,
    ),
]
PlanChecksum = Annotated[
    str,
    StringConstraints(min_length=64, max_length=64, pattern=_SHA256_PATTERN),
]


class _MarketOperationRangeRequest(ApiSchema):
    """Shared exact RAW interval fields for preview and confirmed submission."""

    dataset_id: DatasetId
    range_start: UtcDateTime
    range_end: UtcDateTime

    @model_validator(mode="after")
    def validate_range_order(self) -> Self:
        if self.range_start >= self.range_end:
            raise ValueError("range_start must be before range_end.")
        return self

    def dataset(self) -> MarketDatasetSelector:
        return decode_dataset_id(self.dataset_id)

    def data_range(self) -> DataRange:
        return DataRange(self.range_start, self.range_end)


class MarketOperationBackfillPreviewRequest(_MarketOperationRangeRequest):
    """Bounded RAW backfill preview request."""


class MarketOperationIncrementalPreviewRequest(ApiSchema):
    """Local-state-aware incremental preview request."""

    dataset_id: DatasetId
    overlap_candles: int = Field(ge=0, le=100)
    start: UtcDateTime | None = None

    def dataset(self) -> MarketDatasetSelector:
        return decode_dataset_id(self.dataset_id)


class MarketOperationSubmitRequest(_MarketOperationRangeRequest):
    """Explicit confirmation of one backend-generated operation preview."""

    operation_type: MarketOperationType
    plan_checksum: PlanChecksum
    idempotency_key: IdempotencyKey
    confirmed: Literal[True]


class MarketOperationControlRequest(ApiSchema):
    """Optimistic-concurrency guard for cooperative lifecycle controls."""

    expected_version: int = Field(ge=1)


class MarketOperationDatasetResponse(ApiSchema):
    """Canonical dataset identity without exposing any filesystem path."""

    dataset_id: str
    exchange: Exchange
    market_type: MarketType
    symbol: str
    timeframe: str

    @classmethod
    def from_domain(cls, dataset: MarketDatasetSelector) -> Self:
        MarketDatasetSelector.__post_init__(dataset)
        return cls(
            dataset_id=encode_dataset_id(dataset),
            exchange=dataset.exchange,
            market_type=dataset.market_type,
            symbol=dataset.pair.symbol,
            timeframe=dataset.timeframe.code,
        )


class MarketOperationPlanResponse(ApiSchema):
    """Bounded plan summary safe for administrator confirmation."""

    checksum: str
    chunks_planned: int
    estimated_candles: int
    estimated_requests: int
    created_at: datetime

    @classmethod
    def from_domain(cls, plan: OperationPlanSummary) -> Self:
        OperationPlanSummary.__post_init__(plan)
        return cls(
            checksum=plan.checksum,
            chunks_planned=plan.chunks_planned,
            estimated_candles=plan.estimated_candles,
            estimated_requests=plan.estimated_requests,
            created_at=plan.created_at,
        )


class MarketOperationPlanPreviewResponse(ApiSchema):
    """One administrator-confirmable backend-owned operation plan."""

    operation_type: MarketOperationType
    dataset: MarketOperationDatasetResponse
    range_start: datetime
    range_end: datetime
    plan: MarketOperationPlanResponse

    @classmethod
    def from_domain(cls, preview: MarketOperationPlanPreview) -> Self:
        if not isinstance(preview, MarketOperationPlanPreview):
            raise ValueError("Market operation preview is invalid.")
        return cls(
            operation_type=preview.operation_type,
            dataset=MarketOperationDatasetResponse.from_domain(preview.dataset),
            range_start=preview.data_range.start,
            range_end=preview.data_range.end,
            plan=MarketOperationPlanResponse.from_domain(preview.plan),
        )


class IncrementalMarketOperationPlanPreviewResponse(ApiSchema):
    """Incremental preview with an explicit no-op result."""

    action: Literal["RUN", "NOOP"]
    preview: MarketOperationPlanPreviewResponse | None
    last_open_time: datetime | None
    latest_closed_end: datetime

    @classmethod
    def from_domain(cls, preview: IncrementalMarketOperationPlanPreview) -> Self:
        if not isinstance(preview, IncrementalMarketOperationPlanPreview):
            raise ValueError("Incremental market operation preview is invalid.")
        return cls(
            action=preview.action,
            preview=(
                None
                if preview.preview is None
                else MarketOperationPlanPreviewResponse.from_domain(preview.preview)
            ),
            last_open_time=preview.last_open_time,
            latest_closed_end=preview.latest_closed_end,
        )


class MarketOperationProgressResponse(ApiSchema):
    """Monotonic sanitized execution counters."""

    chunks_planned: int
    chunks_completed: int
    chunks_failed: int
    candles_estimated: int
    candles_received: int
    candles_persisted: int
    requests_completed: int
    updated_at: datetime

    @classmethod
    def from_domain(cls, progress: OperationProgress) -> Self:
        OperationProgress.__post_init__(progress)
        return cls.model_validate(progress)


class MarketOperationLeaseResponse(ApiSchema):
    """Lease timing exposed without the internal worker-owner identifier."""

    claimed_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime

    @classmethod
    def from_domain(cls, lease: WorkerLease) -> Self:
        WorkerLease.__post_init__(lease)
        return cls(
            claimed_at=lease.claimed_at,
            heartbeat_at=lease.heartbeat_at,
            lease_expires_at=lease.lease_expires_at,
        )


class MarketOperationResultResponse(ApiSchema):
    """Sanitized successful dataset result."""

    dataset_version: str
    dataset_checksum: str
    completed_at: datetime

    @classmethod
    def from_domain(cls, result: OperationResult) -> Self:
        OperationResult.__post_init__(result)
        return cls.model_validate(result)


class MarketOperationFailureResponse(ApiSchema):
    """Closed failure code without arbitrary diagnostic text."""

    code: MarketOperationFailureCode
    failed_at: datetime

    @classmethod
    def from_domain(cls, failure: SanitizedOperationFailure) -> Self:
        SanitizedOperationFailure.__post_init__(failure)
        return cls.model_validate(failure)


class MarketOperationResponse(ApiSchema):
    """Sanitized persisted operation projection for the administrator UI."""

    operation_id: UUID
    operation_type: MarketOperationType
    state: MarketOperationState
    dataset: MarketOperationDatasetResponse
    range_start: datetime
    range_end: datetime
    plan: MarketOperationPlanResponse
    progress: MarketOperationProgressResponse
    requested_by: UUID
    contract_version: int
    record_version: int
    local_job_id: str | None
    lease: MarketOperationLeaseResponse | None
    result: MarketOperationResultResponse | None
    failure: MarketOperationFailureResponse | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_domain(cls, operation: MarketOperationSnapshot) -> Self:
        MarketOperationSnapshot.__post_init__(operation)
        return cls(
            operation_id=operation.operation_id,
            operation_type=operation.request.operation_type,
            state=operation.state,
            dataset=MarketOperationDatasetResponse.from_domain(operation.request.dataset),
            range_start=operation.request.data_range.start,
            range_end=operation.request.data_range.end,
            plan=MarketOperationPlanResponse.from_domain(operation.plan),
            progress=MarketOperationProgressResponse.from_domain(operation.progress),
            requested_by=operation.request.requested_by,
            contract_version=operation.request.contract_version,
            record_version=operation.record_version,
            local_job_id=operation.local_job_id,
            lease=(
                None
                if operation.lease is None
                else MarketOperationLeaseResponse.from_domain(operation.lease)
            ),
            result=(
                None
                if operation.result is None
                else MarketOperationResultResponse.from_domain(operation.result)
            ),
            failure=(
                None
                if operation.failure is None
                else MarketOperationFailureResponse.from_domain(operation.failure)
            ),
            created_at=operation.created_at,
            updated_at=operation.updated_at,
            started_at=operation.started_at,
            finished_at=operation.finished_at,
        )


class MarketOperationListResponse(ApiSchema):
    """Bounded offset page without an unbounded COUNT query."""

    items: list[MarketOperationResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=1_000_000)
    count: int = Field(ge=0, le=100)
    has_more: bool
