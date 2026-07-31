"""Pure Phase 2D operational-control domain models and rules."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.errors import DomainError
from app.market_data.domain import (
    DataRange,
    Exchange,
    MarketType,
    Timeframe,
    TradingPair,
    require_utc,
)
from app.market_data.errors import (
    InvalidDatasetIdError,
    InvalidMarketOperationRequestError,
    InvalidOperationLeaseError,
    InvalidOperationTransitionError,
    MarketOperationTerminalError,
    OperationIdempotencyConflictError,
    OperationProgressRegressionError,
    OperationVersionConflictError,
)
from app.market_data.timeframes import get_timeframe

OPERATION_CONTRACT_VERSION = 1
MAX_DATASET_ID_LENGTH = 192
MAX_DATASET_ID_BYTES = 128
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_LOCAL_JOB_ID_LENGTH = 64

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_LOCAL_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class MarketOperationType(StrEnum):
    """Closed MVP operation types."""

    RAW_BACKFILL = "RAW_BACKFILL"
    RAW_INCREMENTAL_UPDATE = "RAW_INCREMENTAL_UPDATE"


class MarketOperationState(StrEnum):
    """Persisted Phase 2D operation states."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RECOVERING = "RECOVERING"


class MarketOperationFailureCode(StrEnum):
    """Closed, storage-safe operational failure codes."""

    INVALID_REQUEST = "INVALID_REQUEST"
    PLAN_CONFLICT = "PLAN_CONFLICT"
    DATASET_BUSY = "DATASET_BUSY"
    LEASE_LOST = "LEASE_LOST"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    LOCAL_STATE_INVALID = "LOCAL_STATE_INVALID"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    RATE_LIMITED = "RATE_LIMITED"
    CANCELLED_BY_ADMIN = "CANCELLED_BY_ADMIN"
    INTERNAL_ERROR = "INTERNAL_ERROR"


TERMINAL_OPERATION_STATES = frozenset(
    {
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
    }
)

_LEASE_STATES = frozenset(
    {
        MarketOperationState.CLAIMED,
        MarketOperationState.RUNNING,
        MarketOperationState.PAUSE_REQUESTED,
        MarketOperationState.CANCEL_REQUESTED,
        MarketOperationState.RECOVERING,
    }
)

# Normal worker/admin transitions. Recovery-only transitions are deliberately
# kept out so normal lifecycle code cannot bypass cooperative boundaries.
_NORMAL_TRANSITIONS: dict[MarketOperationState, frozenset[MarketOperationState]] = {
    MarketOperationState.PENDING: frozenset(
        {
            MarketOperationState.CLAIMED,
            MarketOperationState.PAUSE_REQUESTED,
            MarketOperationState.CANCEL_REQUESTED,
        }
    ),
    MarketOperationState.CLAIMED: frozenset(
        {
            MarketOperationState.RUNNING,
            MarketOperationState.PAUSE_REQUESTED,
            MarketOperationState.CANCEL_REQUESTED,
            MarketOperationState.FAILED,
        }
    ),
    MarketOperationState.RUNNING: frozenset(
        {
            MarketOperationState.PAUSE_REQUESTED,
            MarketOperationState.CANCEL_REQUESTED,
            MarketOperationState.COMPLETED,
            MarketOperationState.FAILED,
        }
    ),
    MarketOperationState.PAUSE_REQUESTED: frozenset(
        {
            MarketOperationState.PAUSED,
            MarketOperationState.COMPLETED,
            MarketOperationState.FAILED,
        }
    ),
    MarketOperationState.PAUSED: frozenset(
        {
            MarketOperationState.PENDING,
            MarketOperationState.CANCEL_REQUESTED,
        }
    ),
    MarketOperationState.CANCEL_REQUESTED: frozenset(
        {
            MarketOperationState.CANCELLED,
            MarketOperationState.COMPLETED,
            MarketOperationState.FAILED,
        }
    ),
    MarketOperationState.CANCELLED: frozenset(),
    MarketOperationState.COMPLETED: frozenset(),
    MarketOperationState.FAILED: frozenset(),
    MarketOperationState.RECOVERING: frozenset(),
}

_RECONCILIATION_TRANSITIONS: dict[MarketOperationState, frozenset[MarketOperationState]] = {
    MarketOperationState.CLAIMED: frozenset({MarketOperationState.RECOVERING}),
    MarketOperationState.RUNNING: frozenset(
        {
            MarketOperationState.CANCELLED,
            MarketOperationState.RECOVERING,
        }
    ),
    MarketOperationState.PAUSE_REQUESTED: frozenset({MarketOperationState.RECOVERING}),
    MarketOperationState.CANCEL_REQUESTED: frozenset({MarketOperationState.RECOVERING}),
    MarketOperationState.RECOVERING: frozenset(
        {
            MarketOperationState.CLAIMED,
            MarketOperationState.RUNNING,
            MarketOperationState.PAUSED,
            MarketOperationState.COMPLETED,
            MarketOperationState.CANCELLED,
            MarketOperationState.FAILED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class MarketDatasetSelector:
    """Validated canonical RAW dataset identity, never a filesystem path."""

    exchange: Exchange
    market_type: MarketType
    pair: TradingPair
    timeframe: Timeframe

    def __post_init__(self) -> None:
        if (
            self.exchange is not Exchange.BINANCE
            or self.market_type is not MarketType.SPOT
            or not isinstance(self.pair, TradingPair)
            or not isinstance(self.timeframe, Timeframe)
        ):
            raise InvalidMarketOperationRequestError()
        try:
            canonical_timeframe = get_timeframe(self.timeframe.code)
        except DomainError:
            raise InvalidMarketOperationRequestError() from None
        object.__setattr__(self, "timeframe", canonical_timeframe)

    @property
    def canonical_key(self) -> str:
        """Return the stable four-field identity encoded by ``dataset_id``."""
        return (
            f"{self.exchange.value}:{self.market_type.value}:"
            f"{self.pair.symbol}:{self.timeframe.code}"
        )


def encode_dataset_id(identity: MarketDatasetSelector) -> str:
    """Encode one validated identity as canonical unpadded base64url."""
    raw = identity.canonical_key.encode("utf-8")
    if len(raw) > MAX_DATASET_ID_BYTES:
        raise InvalidDatasetIdError()
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if not encoded or len(encoded) > MAX_DATASET_ID_LENGTH:
        raise InvalidDatasetIdError()
    return encoded


def decode_dataset_id(dataset_id: str) -> MarketDatasetSelector:
    """Decode and fully revalidate one canonical HTTP dataset identifier."""
    if (
        not isinstance(dataset_id, str)
        or not dataset_id
        or len(dataset_id) > MAX_DATASET_ID_LENGTH
        or _DATASET_ID_PATTERN.fullmatch(dataset_id) is None
    ):
        raise InvalidDatasetIdError()
    padding = "=" * (-len(dataset_id) % 4)
    try:
        raw = base64.b64decode(
            dataset_id + padding,
            altchars=b"-_",
            validate=True,
        )
        if not raw or len(raw) > MAX_DATASET_ID_BYTES:
            raise ValueError
        decoded = raw.decode("utf-8")
        exchange_value, market_value, symbol, timeframe_code = decoded.split(":")
        identity = MarketDatasetSelector(
            exchange=Exchange(exchange_value),
            market_type=MarketType(market_value),
            pair=TradingPair.parse(symbol),
            timeframe=get_timeframe(timeframe_code),
        )
    except (ValueError, UnicodeDecodeError, binascii.Error, DomainError):
        raise InvalidDatasetIdError() from None
    if encode_dataset_id(identity) != dataset_id:
        raise InvalidDatasetIdError()
    return identity


@dataclass(frozen=True, slots=True)
class MarketOperationRequest:
    """Immutable administrative intent used for idempotent creation."""

    operation_type: MarketOperationType
    dataset: MarketDatasetSelector
    data_range: DataRange
    plan_checksum: str
    idempotency_key: str = field(repr=False)
    requested_by: UUID
    contract_version: int = OPERATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation_type, MarketOperationType)
            or not isinstance(self.dataset, MarketDatasetSelector)
            or not isinstance(self.data_range, DataRange)
        ):
            raise InvalidMarketOperationRequestError()
        _require_sha256(self.plan_checksum)
        _require_idempotency_key(self.idempotency_key)
        _require_uuid(self.requested_by)
        if self.contract_version != OPERATION_CONTRACT_VERSION:
            raise OperationVersionConflictError()


@dataclass(frozen=True, slots=True)
class OperationPlanSummary:
    """Bounded, persistence-safe summary of the effective local plan."""

    checksum: str
    chunks_planned: int
    estimated_candles: int
    estimated_requests: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_sha256(self.checksum)
        if (
            not _is_int(self.chunks_planned)
            or not _is_int(self.estimated_candles)
            or not _is_int(self.estimated_requests)
            or self.chunks_planned < 1
            or self.estimated_candles < 1
        ):
            raise InvalidMarketOperationRequestError()
        if self.estimated_requests < 0:
            raise InvalidMarketOperationRequestError()
        object.__setattr__(
            self,
            "created_at",
            _operation_utc(self.created_at),
        )


@dataclass(frozen=True, slots=True)
class OperationProgress:
    """Monotonic public operation counters."""

    chunks_planned: int
    chunks_completed: int
    chunks_failed: int
    candles_estimated: int
    candles_received: int
    candles_persisted: int
    requests_completed: int
    updated_at: datetime

    def __post_init__(self) -> None:
        counters = (
            self.chunks_planned,
            self.chunks_completed,
            self.chunks_failed,
            self.candles_estimated,
            self.candles_received,
            self.candles_persisted,
            self.requests_completed,
        )
        if any(not _is_int(value) or value < 0 for value in counters):
            raise InvalidMarketOperationRequestError()
        if self.chunks_completed + self.chunks_failed > self.chunks_planned:
            raise InvalidMarketOperationRequestError()
        if self.candles_persisted > self.candles_received:
            raise InvalidMarketOperationRequestError()
        object.__setattr__(self, "updated_at", _operation_utc(self.updated_at))


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Sanitized successful local result."""

    dataset_version: str
    dataset_checksum: str
    completed_at: datetime

    def __post_init__(self) -> None:
        _require_sha256(self.dataset_version)
        _require_sha256(self.dataset_checksum)
        object.__setattr__(self, "completed_at", _operation_utc(self.completed_at))


@dataclass(frozen=True, slots=True)
class SanitizedOperationFailure:
    """Closed failure payload with no arbitrary diagnostic text."""

    code: MarketOperationFailureCode
    failed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.code, MarketOperationFailureCode):
            raise InvalidMarketOperationRequestError()
        object.__setattr__(self, "failed_at", _operation_utc(self.failed_at))


@dataclass(frozen=True, slots=True)
class WorkerLease:
    """Pure temporal ownership proof for one operation and worker."""

    operation_id: UUID
    owner_id: UUID
    claimed_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id)
        _require_uuid(self.owner_id)
        claimed_at = _operation_utc(self.claimed_at)
        heartbeat_at = _operation_utc(self.heartbeat_at)
        lease_expires_at = _operation_utc(self.lease_expires_at)
        if not claimed_at <= heartbeat_at < lease_expires_at:
            raise InvalidOperationLeaseError()
        object.__setattr__(self, "claimed_at", claimed_at)
        object.__setattr__(self, "heartbeat_at", heartbeat_at)
        object.__setattr__(self, "lease_expires_at", lease_expires_at)

    def belongs_to(self, owner_id: UUID) -> bool:
        return self.owner_id == owner_id

    def is_active(self, now: datetime) -> bool:
        current = _operation_utc(now)
        if current < self.heartbeat_at:
            raise InvalidOperationLeaseError()
        return current < self.lease_expires_at

    def is_expired(self, now: datetime) -> bool:
        return not self.is_active(now)


@dataclass(frozen=True, slots=True)
class MarketOperationSnapshot:
    """Immutable public and repository-neutral operation state."""

    operation_id: UUID
    request: MarketOperationRequest
    plan: OperationPlanSummary
    state: MarketOperationState
    progress: OperationProgress
    created_at: datetime
    updated_at: datetime
    record_version: int
    local_job_id: str | None = None
    lease: WorkerLease | None = field(default=None, repr=False)
    result: OperationResult | None = None
    failure: SanitizedOperationFailure | None = None
    finished_at: datetime | None = None
    started_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id)
        if (
            not isinstance(self.request, MarketOperationRequest)
            or not isinstance(self.plan, OperationPlanSummary)
            or not isinstance(self.state, MarketOperationState)
            or not isinstance(self.progress, OperationProgress)
            or (self.lease is not None and not isinstance(self.lease, WorkerLease))
            or (self.result is not None and not isinstance(self.result, OperationResult))
            or (
                self.failure is not None and not isinstance(self.failure, SanitizedOperationFailure)
            )
        ):
            raise InvalidMarketOperationRequestError()
        created_at = _operation_utc(self.created_at)
        updated_at = _operation_utc(self.updated_at)
        started_at = _operation_utc(self.started_at) if self.started_at is not None else None
        finished_at = _operation_utc(self.finished_at) if self.finished_at is not None else None
        if (
            updated_at < created_at
            or self.plan.created_at > created_at
            or self.progress.updated_at > updated_at
            or (started_at is not None and not created_at <= started_at <= updated_at)
            or (finished_at is not None and not created_at <= finished_at <= updated_at)
        ):
            raise InvalidMarketOperationRequestError()
        if not _is_int(self.record_version) or self.record_version < 0:
            raise InvalidMarketOperationRequestError()
        if self.plan.checksum != self.request.plan_checksum:
            raise InvalidMarketOperationRequestError()
        if (
            self.progress.chunks_planned != self.plan.chunks_planned
            or self.progress.candles_estimated != self.plan.estimated_candles
        ):
            raise InvalidMarketOperationRequestError()
        if self.local_job_id is not None and (
            len(self.local_job_id) > MAX_LOCAL_JOB_ID_LENGTH
            or _LOCAL_JOB_ID_PATTERN.fullmatch(self.local_job_id) is None
        ):
            raise InvalidMarketOperationRequestError()
        if self.lease is not None:
            if self.lease.operation_id != self.operation_id or self.state not in _LEASE_STATES:
                raise InvalidOperationLeaseError()
            if self.lease.claimed_at < created_at:
                raise InvalidOperationLeaseError()
            if started_at is not None and started_at > self.lease.claimed_at:
                raise InvalidOperationLeaseError()
        if self.state in {MarketOperationState.CLAIMED, MarketOperationState.RUNNING} and (
            self.lease is None or started_at is None
        ):
            raise InvalidOperationLeaseError()
        self._validate_outcome(finished_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)

    def _validate_outcome(self, finished_at: datetime | None) -> None:
        if self.state not in TERMINAL_OPERATION_STATES:
            if self.result is not None or self.failure is not None or finished_at is not None:
                raise InvalidMarketOperationRequestError()
            return
        if finished_at is None:
            raise InvalidMarketOperationRequestError()
        if self.result is not None and self.failure is not None:
            raise InvalidMarketOperationRequestError()
        if self.state is MarketOperationState.COMPLETED:
            if self.result is None or self.failure is not None:
                raise InvalidMarketOperationRequestError()
            if self.result.completed_at != finished_at:
                raise InvalidMarketOperationRequestError()
            return
        if self.result is not None or self.failure is None or self.failure.failed_at != finished_at:
            raise InvalidMarketOperationRequestError()
        if (
            self.state is MarketOperationState.CANCELLED
            and self.failure.code is not MarketOperationFailureCode.CANCELLED_BY_ADMIN
        ):
            raise InvalidMarketOperationRequestError()
        if (
            self.state is MarketOperationState.FAILED
            and self.failure.code is MarketOperationFailureCode.CANCELLED_BY_ADMIN
        ):
            raise InvalidMarketOperationRequestError()


def operation_request_fingerprint(request: MarketOperationRequest) -> str:
    """Hash the canonical contract fields independently of JSON formatting."""
    return hashlib.sha256(canonical_operation_request_bytes(request)).hexdigest()


def canonical_operation_request_bytes(request: MarketOperationRequest) -> bytes:
    """Serialize fingerprint fields as stable canonical JSON."""
    payload = {
        "contract_version": request.contract_version,
        "dataset": {
            "exchange": request.dataset.exchange.value,
            "market": request.dataset.market_type.value,
            "symbol": request.dataset.pair.symbol,
            "timeframe": request.dataset.timeframe.code,
        },
        "end": request.data_range.end.isoformat(),
        "operation_type": request.operation_type.value,
        "plan_checksum": request.plan_checksum,
        "start": request.data_range.start.isoformat(),
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def is_same_idempotent_request(
    existing: MarketOperationRequest,
    candidate: MarketOperationRequest,
) -> bool:
    """Resolve one lookup by key or reject divergent payload reuse."""
    if existing.idempotency_key != candidate.idempotency_key:
        return False
    if operation_request_fingerprint(existing) != operation_request_fingerprint(candidate):
        raise OperationIdempotencyConflictError()
    return True


def can_transition(
    current: MarketOperationState,
    target: MarketOperationState,
) -> bool:
    """Return whether a normal cooperative transition is allowed."""
    return target in _NORMAL_TRANSITIONS[current]


def require_transition(
    current: MarketOperationState,
    target: MarketOperationState,
) -> None:
    """Require a normal transition without recovery-only shortcuts."""
    if current in TERMINAL_OPERATION_STATES:
        raise MarketOperationTerminalError()
    if not can_transition(current, target):
        raise InvalidOperationTransitionError()


def can_reconcile_transition(
    current: MarketOperationState,
    target: MarketOperationState,
) -> bool:
    """Return whether durable evidence permits a recovery-only transition."""
    return target in _RECONCILIATION_TRANSITIONS.get(current, frozenset())


def require_reconciliation_transition(
    current: MarketOperationState,
    target: MarketOperationState,
) -> None:
    """Require a transition reserved for verified durable reconciliation."""
    if current in TERMINAL_OPERATION_STATES:
        raise MarketOperationTerminalError()
    if not can_reconcile_transition(current, target):
        raise InvalidOperationTransitionError()


def request_pause(state: MarketOperationState) -> MarketOperationState:
    """Return the idempotent administrative pause decision."""
    if state in {MarketOperationState.PAUSE_REQUESTED, MarketOperationState.PAUSED}:
        return state
    require_transition(state, MarketOperationState.PAUSE_REQUESTED)
    return MarketOperationState.PAUSE_REQUESTED


def request_resume(state: MarketOperationState) -> MarketOperationState:
    """Return the idempotent administrative resume decision."""
    if state is MarketOperationState.PENDING:
        return state
    require_transition(state, MarketOperationState.PENDING)
    return MarketOperationState.PENDING


def request_cancel(state: MarketOperationState) -> MarketOperationState:
    """Return a cooperative cancel request, never a premature terminal state."""
    if state in {MarketOperationState.CANCEL_REQUESTED, MarketOperationState.CANCELLED}:
        return state
    require_transition(state, MarketOperationState.CANCEL_REQUESTED)
    return MarketOperationState.CANCEL_REQUESTED


def renew_lease(
    lease: WorkerLease,
    *,
    owner_id: UUID,
    now: datetime,
    lease_expires_at: datetime,
) -> WorkerLease:
    """Calculate an owner-checked renewal without persistence or a clock read."""
    current = _operation_utc(now)
    expires = _operation_utc(lease_expires_at)
    if not lease.belongs_to(owner_id) or lease.is_expired(current):
        raise InvalidOperationLeaseError()
    if current < lease.heartbeat_at or expires <= current or expires <= lease.lease_expires_at:
        raise InvalidOperationLeaseError()
    return WorkerLease(
        operation_id=lease.operation_id,
        owner_id=lease.owner_id,
        claimed_at=lease.claimed_at,
        heartbeat_at=current,
        lease_expires_at=expires,
    )


def request_lease_recovery(
    operation: MarketOperationSnapshot,
    *,
    now: datetime,
) -> MarketOperationState:
    """Enter recovery only when an active operation has an expired lease."""
    if operation.state not in {
        MarketOperationState.CLAIMED,
        MarketOperationState.RUNNING,
        MarketOperationState.PAUSE_REQUESTED,
        MarketOperationState.CANCEL_REQUESTED,
    }:
        raise InvalidOperationTransitionError()
    if operation.lease is None or not operation.lease.is_expired(now):
        raise InvalidOperationLeaseError()
    require_reconciliation_transition(
        operation.state,
        MarketOperationState.RECOVERING,
    )
    return MarketOperationState.RECOVERING


def require_progress_not_regressed(
    previous: OperationProgress,
    current: OperationProgress,
) -> None:
    """Require equal plan totals and monotonic completed counters/time."""
    if (
        current.chunks_planned != previous.chunks_planned
        or current.candles_estimated != previous.candles_estimated
        or current.updated_at < previous.updated_at
    ):
        raise OperationProgressRegressionError()
    monotonic_pairs = (
        (previous.chunks_completed, current.chunks_completed),
        (previous.chunks_failed, current.chunks_failed),
        (previous.candles_received, current.candles_received),
        (previous.candles_persisted, current.candles_persisted),
        (previous.requests_completed, current.requests_completed),
    )
    if any(after < before for before, after in monotonic_pairs):
        raise OperationProgressRegressionError()


def validate_operation_update(
    previous: MarketOperationSnapshot,
    current: MarketOperationSnapshot,
    *,
    reconciliation: bool = False,
) -> None:
    """Validate immutable identity, optimistic version and monotonic evolution."""
    if previous.state in TERMINAL_OPERATION_STATES:
        raise MarketOperationTerminalError()
    if (
        current.operation_id != previous.operation_id
        or current.request != previous.request
        or current.plan != previous.plan
        or current.created_at != previous.created_at
        or (previous.started_at is not None and current.started_at != previous.started_at)
    ):
        raise InvalidMarketOperationRequestError()
    if previous.local_job_id is not None and current.local_job_id != previous.local_job_id:
        raise InvalidMarketOperationRequestError()
    if current.record_version != previous.record_version + 1:
        raise OperationVersionConflictError()
    if current.updated_at < previous.updated_at:
        raise OperationProgressRegressionError()
    require_progress_not_regressed(previous.progress, current.progress)
    if current.state == previous.state:
        return
    if reconciliation:
        require_reconciliation_transition(previous.state, current.state)
    else:
        require_transition(previous.state, current.state)


def _require_sha256(value: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise InvalidMarketOperationRequestError()


def _require_idempotency_key(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > MAX_IDEMPOTENCY_KEY_LENGTH
        or _IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None
    ):
        raise InvalidMarketOperationRequestError()


def _require_uuid(value: UUID) -> None:
    if not isinstance(value, UUID):
        raise InvalidMarketOperationRequestError()


def _operation_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidMarketOperationRequestError()
    try:
        return require_utc(value, field_name="operation_timestamp")
    except DomainError:
        raise InvalidMarketOperationRequestError() from None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
