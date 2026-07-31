"""Pure Phase 2D operational-control domain tests."""

from __future__ import annotations

import base64
import itertools
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.market_data.domain import DataRange, Exchange, MarketType, TradingPair
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
from app.market_data.operations import (
    MAX_DATASET_ID_LENGTH,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MarketDatasetSelector,
    MarketOperationFailureCode,
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    OperationProgress,
    OperationResult,
    SanitizedOperationFailure,
    WorkerLease,
    can_reconcile_transition,
    can_transition,
    canonical_operation_request_bytes,
    decode_dataset_id,
    encode_dataset_id,
    is_same_idempotent_request,
    operation_request_fingerprint,
    renew_lease,
    request_cancel,
    request_lease_recovery,
    request_pause,
    request_resume,
    require_progress_not_regressed,
    require_reconciliation_transition,
    require_transition,
    validate_operation_update,
)
from app.market_data.timeframes import get_timeframe

NOW = datetime(2026, 7, 31, 12, tzinfo=UTC)
END = NOW + timedelta(hours=4)
OPERATION_ID = UUID("11111111-1111-4111-8111-111111111111")
ADMIN_ID = UUID("22222222-2222-4222-8222-222222222222")
OWNER_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_OWNER_ID = UUID("44444444-4444-4444-8444-444444444444")
CHECKSUM_A = "a" * 64
CHECKSUM_B = "b" * 64

NORMAL_TRANSITIONS = {
    (MarketOperationState.PENDING, MarketOperationState.CLAIMED),
    (MarketOperationState.PENDING, MarketOperationState.PAUSE_REQUESTED),
    (MarketOperationState.PENDING, MarketOperationState.CANCEL_REQUESTED),
    (MarketOperationState.CLAIMED, MarketOperationState.RUNNING),
    (MarketOperationState.CLAIMED, MarketOperationState.PAUSE_REQUESTED),
    (MarketOperationState.CLAIMED, MarketOperationState.CANCEL_REQUESTED),
    (MarketOperationState.CLAIMED, MarketOperationState.FAILED),
    (MarketOperationState.RUNNING, MarketOperationState.PAUSE_REQUESTED),
    (MarketOperationState.RUNNING, MarketOperationState.CANCEL_REQUESTED),
    (MarketOperationState.RUNNING, MarketOperationState.COMPLETED),
    (MarketOperationState.RUNNING, MarketOperationState.FAILED),
    (MarketOperationState.PAUSE_REQUESTED, MarketOperationState.PAUSED),
    (MarketOperationState.PAUSE_REQUESTED, MarketOperationState.COMPLETED),
    (MarketOperationState.PAUSE_REQUESTED, MarketOperationState.FAILED),
    (MarketOperationState.PAUSED, MarketOperationState.PENDING),
    (MarketOperationState.PAUSED, MarketOperationState.CANCEL_REQUESTED),
    (MarketOperationState.CANCEL_REQUESTED, MarketOperationState.CANCELLED),
    (MarketOperationState.CANCEL_REQUESTED, MarketOperationState.COMPLETED),
    (MarketOperationState.CANCEL_REQUESTED, MarketOperationState.FAILED),
}

RECONCILIATION_TRANSITIONS = {
    (MarketOperationState.CLAIMED, MarketOperationState.RECOVERING),
    (MarketOperationState.RUNNING, MarketOperationState.CANCELLED),
    (MarketOperationState.RUNNING, MarketOperationState.RECOVERING),
    (MarketOperationState.PAUSE_REQUESTED, MarketOperationState.RECOVERING),
    (MarketOperationState.CANCEL_REQUESTED, MarketOperationState.RECOVERING),
    (MarketOperationState.RECOVERING, MarketOperationState.CLAIMED),
    (MarketOperationState.RECOVERING, MarketOperationState.RUNNING),
    (MarketOperationState.RECOVERING, MarketOperationState.PAUSED),
    (MarketOperationState.RECOVERING, MarketOperationState.COMPLETED),
    (MarketOperationState.RECOVERING, MarketOperationState.CANCELLED),
    (MarketOperationState.RECOVERING, MarketOperationState.FAILED),
}

ALL_STATE_PAIRS = tuple(itertools.product(tuple(MarketOperationState), tuple(MarketOperationState)))


def _selector(symbol: str = "BTC/USDT") -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair.parse(symbol),
        timeframe=get_timeframe("1h"),
    )


def _request(
    *,
    operation_type: MarketOperationType = MarketOperationType.RAW_BACKFILL,
    selector: MarketDatasetSelector | None = None,
    checksum: str = CHECKSUM_A,
    idempotency_key: str = "phase-2d:test-request",
    requested_by: UUID = ADMIN_ID,
    data_range: DataRange | None = None,
) -> MarketOperationRequest:
    return MarketOperationRequest(
        operation_type=operation_type,
        dataset=selector or _selector(),
        data_range=data_range or DataRange(NOW, END),
        plan_checksum=checksum,
        idempotency_key=idempotency_key,
        requested_by=requested_by,
    )


def _plan(*, checksum: str = CHECKSUM_A) -> OperationPlanSummary:
    return OperationPlanSummary(
        checksum=checksum,
        chunks_planned=2,
        estimated_candles=4,
        estimated_requests=2,
        created_at=NOW,
    )


def _progress(
    *,
    chunks_completed: int = 0,
    chunks_failed: int = 0,
    candles_received: int = 0,
    candles_persisted: int = 0,
    requests_completed: int = 0,
    updated_at: datetime = NOW,
) -> OperationProgress:
    return OperationProgress(
        chunks_planned=2,
        chunks_completed=chunks_completed,
        chunks_failed=chunks_failed,
        candles_estimated=4,
        candles_received=candles_received,
        candles_persisted=candles_persisted,
        requests_completed=requests_completed,
        updated_at=updated_at,
    )


def _lease(
    *,
    operation_id: UUID = OPERATION_ID,
    owner_id: UUID = OWNER_ID,
    claimed_at: datetime = NOW,
    heartbeat_at: datetime = NOW,
    lease_expires_at: datetime = NOW + timedelta(minutes=1),
) -> WorkerLease:
    return WorkerLease(
        operation_id=operation_id,
        owner_id=owner_id,
        claimed_at=claimed_at,
        heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
    )


def _snapshot(
    state: MarketOperationState = MarketOperationState.PENDING,
    *,
    request: MarketOperationRequest | None = None,
    plan: OperationPlanSummary | None = None,
    progress: OperationProgress | None = None,
    operation_id: UUID = OPERATION_ID,
    record_version: int = 0,
    created_at: datetime = NOW,
    updated_at: datetime = NOW,
    local_job_id: str | None = None,
    lease: WorkerLease | None = None,
) -> MarketOperationSnapshot:
    selected_progress = progress or _progress(updated_at=updated_at)
    selected_lease = lease
    if selected_lease is None and state in {
        MarketOperationState.CLAIMED,
        MarketOperationState.RUNNING,
    }:
        selected_lease = _lease(operation_id=operation_id)
    started_at = (
        selected_lease.claimed_at
        if state
        in {
            MarketOperationState.CLAIMED,
            MarketOperationState.RUNNING,
            MarketOperationState.CANCELLED,
            MarketOperationState.COMPLETED,
            MarketOperationState.FAILED,
        }
        and selected_lease is not None
        else None
    )
    if started_at is None and state in {
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
    }:
        started_at = created_at
    result = None
    failure = None
    finished_at = None
    if state is MarketOperationState.COMPLETED:
        result = OperationResult(CHECKSUM_A, CHECKSUM_B, updated_at)
        finished_at = updated_at
    if state is MarketOperationState.FAILED:
        failure = SanitizedOperationFailure(
            MarketOperationFailureCode.INTERNAL_ERROR,
            updated_at,
        )
        finished_at = updated_at
    if state is MarketOperationState.CANCELLED:
        failure = SanitizedOperationFailure(
            MarketOperationFailureCode.CANCELLED_BY_ADMIN,
            updated_at,
        )
        finished_at = updated_at
    return MarketOperationSnapshot(
        operation_id=operation_id,
        request=request or _request(),
        plan=plan or _plan(),
        state=state,
        progress=selected_progress,
        created_at=created_at,
        updated_at=updated_at,
        record_version=record_version,
        local_job_id=local_job_id,
        lease=selected_lease,
        result=result,
        failure=failure,
        finished_at=finished_at,
        started_at=started_at,
    )


def test_operation_enums_are_closed_to_the_approved_contract() -> None:
    assert {item.value for item in MarketOperationType} == {
        "RAW_BACKFILL",
        "RAW_INCREMENTAL_UPDATE",
    }
    assert {item.value for item in MarketOperationState} == {
        "PENDING",
        "CLAIMED",
        "RUNNING",
        "PAUSE_REQUESTED",
        "PAUSED",
        "CANCEL_REQUESTED",
        "CANCELLED",
        "COMPLETED",
        "FAILED",
        "RECOVERING",
    }
    assert {item.value for item in MarketOperationFailureCode} == {
        "INVALID_REQUEST",
        "PLAN_CONFLICT",
        "DATASET_BUSY",
        "LEASE_LOST",
        "WORKER_UNAVAILABLE",
        "LOCAL_STATE_INVALID",
        "NETWORK_FAILURE",
        "RATE_LIMITED",
        "CANCELLED_BY_ADMIN",
        "INTERNAL_ERROR",
    }


@pytest.mark.parametrize(("current", "target"), ALL_STATE_PAIRS)
def test_normal_transition_matrix_is_closed(
    current: MarketOperationState,
    target: MarketOperationState,
) -> None:
    expected = (current, target) in NORMAL_TRANSITIONS

    assert can_transition(current, target) is expected
    if expected:
        require_transition(current, target)
    elif current in {
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
    }:
        with pytest.raises(MarketOperationTerminalError):
            require_transition(current, target)
    else:
        with pytest.raises(InvalidOperationTransitionError):
            require_transition(current, target)


@pytest.mark.parametrize(("current", "target"), ALL_STATE_PAIRS)
def test_reconciliation_transition_matrix_is_closed(
    current: MarketOperationState,
    target: MarketOperationState,
) -> None:
    expected = (current, target) in RECONCILIATION_TRANSITIONS

    assert can_reconcile_transition(current, target) is expected
    if expected:
        require_reconciliation_transition(current, target)
    elif current in {
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
    }:
        with pytest.raises(MarketOperationTerminalError):
            require_reconciliation_transition(current, target)
    else:
        with pytest.raises(InvalidOperationTransitionError):
            require_reconciliation_transition(current, target)


def test_reconciliation_does_not_weaken_normal_cooperative_transitions() -> None:
    assert not can_transition(MarketOperationState.RUNNING, MarketOperationState.CANCELLED)
    assert can_reconcile_transition(
        MarketOperationState.RUNNING,
        MarketOperationState.CANCELLED,
    )
    assert not can_transition(MarketOperationState.RECOVERING, MarketOperationState.COMPLETED)
    assert can_reconcile_transition(
        MarketOperationState.RECOVERING,
        MarketOperationState.COMPLETED,
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (MarketOperationState.PENDING, MarketOperationState.PAUSE_REQUESTED),
        (MarketOperationState.CLAIMED, MarketOperationState.PAUSE_REQUESTED),
        (MarketOperationState.RUNNING, MarketOperationState.PAUSE_REQUESTED),
        (MarketOperationState.PAUSE_REQUESTED, MarketOperationState.PAUSE_REQUESTED),
        (MarketOperationState.PAUSED, MarketOperationState.PAUSED),
    ],
)
def test_pause_request_is_cooperative_and_idempotent(
    state: MarketOperationState,
    expected: MarketOperationState,
) -> None:
    assert request_pause(state) is expected


@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.CANCEL_REQUESTED,
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
        MarketOperationState.RECOVERING,
    ],
)
def test_pause_rejects_incompatible_states(state: MarketOperationState) -> None:
    with pytest.raises((InvalidOperationTransitionError, MarketOperationTerminalError)):
        request_pause(state)


def test_resume_is_paused_to_pending_and_retry_is_idempotent() -> None:
    assert request_resume(MarketOperationState.PAUSED) is MarketOperationState.PENDING
    assert request_resume(MarketOperationState.PENDING) is MarketOperationState.PENDING


@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.CLAIMED,
        MarketOperationState.RUNNING,
        MarketOperationState.PAUSE_REQUESTED,
        MarketOperationState.CANCEL_REQUESTED,
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
        MarketOperationState.RECOVERING,
    ],
)
def test_resume_rejects_non_resumable_states(state: MarketOperationState) -> None:
    with pytest.raises((InvalidOperationTransitionError, MarketOperationTerminalError)):
        request_resume(state)


@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.PENDING,
        MarketOperationState.CLAIMED,
        MarketOperationState.RUNNING,
        MarketOperationState.PAUSED,
    ],
)
def test_cancel_request_never_claims_immediate_cancellation(
    state: MarketOperationState,
) -> None:
    assert request_cancel(state) is MarketOperationState.CANCEL_REQUESTED


def test_cancel_retry_is_idempotent_after_request_or_confirmation() -> None:
    assert (
        request_cancel(MarketOperationState.CANCEL_REQUESTED)
        is MarketOperationState.CANCEL_REQUESTED
    )
    assert request_cancel(MarketOperationState.CANCELLED) is MarketOperationState.CANCELLED


@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.PAUSE_REQUESTED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
        MarketOperationState.RECOVERING,
    ],
)
def test_cancel_rejects_incompatible_states(state: MarketOperationState) -> None:
    with pytest.raises((InvalidOperationTransitionError, MarketOperationTerminalError)):
        request_cancel(state)


@pytest.mark.parametrize("symbol", ["BTC/USDT", "A_B/C", "A/B_C", "BTC.2/USDT-X"])
def test_dataset_id_round_trip_for_valid_canonical_symbols(symbol: str) -> None:
    identity = _selector(symbol)

    encoded = encode_dataset_id(identity)

    assert "=" not in encoded
    assert "/" not in encoded
    assert base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode() == (
        identity.canonical_key
    )
    assert decode_dataset_id(encoded) == identity


def test_collision_prone_symbols_have_distinct_dataset_ids() -> None:
    assert encode_dataset_id(_selector("A_B/C")) != encode_dataset_id(_selector("A/B_C"))


@pytest.mark.parametrize(
    "invalid_id",
    [
        "",
        "with=padding",
        "with whitespace",
        "a" * (MAX_DATASET_ID_LENGTH + 1),
        "é",
    ],
)
def test_dataset_id_rejects_invalid_surface_encoding(invalid_id: str) -> None:
    with pytest.raises(InvalidDatasetIdError):
        decode_dataset_id(invalid_id)


def test_dataset_id_rejects_decoded_noncanonical_or_unsupported_identity() -> None:
    raw_values = (
        "BINANCE:spot:BTC/USDT:1h",
        "binance:forex:BTC/USDT:1h",
        "binance:spot:btc/usdt:1h",
        "binance:spot:BTC/USDT:2h",
        "binance:spot:BTC/USDT:1h:extra",
    )
    for raw in raw_values:
        encoded = base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()
        with pytest.raises(InvalidDatasetIdError):
            decode_dataset_id(encoded)


def test_dataset_id_rejects_excessive_decoded_payload() -> None:
    encoded = base64.urlsafe_b64encode(b"x" * 129).rstrip(b"=").decode()

    with pytest.raises(InvalidDatasetIdError):
        decode_dataset_id(encoded)


def test_dataset_id_error_never_echoes_untrusted_input() -> None:
    untrusted = "connection-secret"
    with pytest.raises(InvalidDatasetIdError) as captured:
        decode_dataset_id(untrusted)

    assert untrusted not in str(captured.value)


def test_operation_request_fingerprint_is_deterministic_and_canonical() -> None:
    first = _request(idempotency_key="first", requested_by=ADMIN_ID)
    second = _request(idempotency_key="second", requested_by=OTHER_OWNER_ID)

    assert operation_request_fingerprint(first) == operation_request_fingerprint(second)
    assert len(operation_request_fingerprint(first)) == 64
    assert canonical_operation_request_bytes(first) == canonical_operation_request_bytes(second)
    assert canonical_operation_request_bytes(first) == (
        b'{"contract_version":1,"dataset":{"exchange":"binance","market":"spot",'
        b'"symbol":"BTC/USDT","timeframe":"1h"},"end":"2026-07-31T16:00:00+00:00",'
        b'"operation_type":"RAW_BACKFILL","plan_checksum":"'
        + CHECKSUM_A.encode()
        + b'","start":"2026-07-31T12:00:00+00:00"}'
    )


@pytest.mark.parametrize(
    "changed",
    [
        _request(operation_type=MarketOperationType.RAW_INCREMENTAL_UPDATE),
        _request(checksum=CHECKSUM_B),
        _request(data_range=DataRange(NOW, END + timedelta(hours=1))),
        _request(selector=_selector("ETH/USDT")),
    ],
)
def test_fingerprint_changes_for_every_identity_field(
    changed: MarketOperationRequest,
) -> None:
    assert operation_request_fingerprint(_request()) != operation_request_fingerprint(changed)


def test_idempotency_same_key_same_request_matches() -> None:
    assert is_same_idempotent_request(_request(), _request())


def test_idempotency_same_key_different_fingerprint_conflicts() -> None:
    with pytest.raises(OperationIdempotencyConflictError):
        is_same_idempotent_request(_request(), _request(checksum=CHECKSUM_B))


def test_idempotency_different_key_is_not_the_same_submission() -> None:
    assert not is_same_idempotent_request(
        _request(idempotency_key="one"),
        _request(idempotency_key="two"),
    )


@pytest.mark.parametrize(
    "key",
    [
        "",
        " ",
        "contains space",
        "line\nbreak",
        "x" * (MAX_IDEMPOTENCY_KEY_LENGTH + 1),
        "-cannot-start-with-symbol",
    ],
)
def test_idempotency_key_is_bounded_and_control_free(key: str) -> None:
    with pytest.raises(InvalidMarketOperationRequestError):
        _request(idempotency_key=key)


def test_idempotency_key_is_omitted_from_representations() -> None:
    secret_like_key = "super-secret-token"
    request = _request(idempotency_key=secret_like_key)
    snapshot = _snapshot(request=request)

    assert secret_like_key not in repr(request)
    assert secret_like_key not in repr(snapshot)
    assert secret_like_key not in canonical_operation_request_bytes(request).decode()


def test_operation_models_are_immutable() -> None:
    request = _request()
    snapshot = _snapshot(request=request)

    with pytest.raises(FrozenInstanceError):
        request.plan_checksum = CHECKSUM_B  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.state = MarketOperationState.RUNNING  # type: ignore[misc]


def test_unknown_contract_version_is_an_explicit_conflict() -> None:
    with pytest.raises(OperationVersionConflictError):
        MarketOperationRequest(
            operation_type=MarketOperationType.RAW_BACKFILL,
            dataset=_selector(),
            data_range=DataRange(NOW, END),
            plan_checksum=CHECKSUM_A,
            idempotency_key="version-conflict",
            requested_by=ADMIN_ID,
            contract_version=2,
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 7, 31, 12),
        datetime(2026, 7, 31, 12, tzinfo=timezone(timedelta(hours=-3))),
    ],
)
def test_operation_models_reject_naive_or_non_utc_timestamps(timestamp: datetime) -> None:
    with pytest.raises(InvalidMarketOperationRequestError):
        OperationPlanSummary(CHECKSUM_A, 2, 4, 2, timestamp)
    with pytest.raises(InvalidMarketOperationRequestError):
        SanitizedOperationFailure(MarketOperationFailureCode.INTERNAL_ERROR, timestamp)


def test_worker_lease_active_expired_and_owner() -> None:
    lease = _lease()

    assert lease.belongs_to(OWNER_ID)
    assert not lease.belongs_to(OTHER_OWNER_ID)
    assert lease.is_active(NOW + timedelta(seconds=30))
    assert lease.is_expired(NOW + timedelta(minutes=1))


def test_worker_lease_rejects_incoherent_timestamps() -> None:
    with pytest.raises(InvalidOperationLeaseError):
        _lease(
            heartbeat_at=NOW + timedelta(minutes=2),
            lease_expires_at=NOW + timedelta(minutes=1),
        )
    with pytest.raises(InvalidOperationLeaseError):
        _lease(claimed_at=NOW + timedelta(seconds=1), heartbeat_at=NOW)


def test_worker_lease_rejects_clock_before_heartbeat() -> None:
    lease = _lease(heartbeat_at=NOW + timedelta(seconds=10))

    with pytest.raises(InvalidOperationLeaseError):
        lease.is_active(NOW)


def test_lease_renewal_is_owner_checked_and_monotonic() -> None:
    lease = _lease()

    renewed = renew_lease(
        lease,
        owner_id=OWNER_ID,
        now=NOW + timedelta(seconds=30),
        lease_expires_at=NOW + timedelta(minutes=2),
    )

    assert renewed.claimed_at == lease.claimed_at
    assert renewed.heartbeat_at == NOW + timedelta(seconds=30)
    assert renewed.lease_expires_at == NOW + timedelta(minutes=2)

    with pytest.raises(InvalidOperationLeaseError):
        renew_lease(
            lease,
            owner_id=OTHER_OWNER_ID,
            now=NOW + timedelta(seconds=30),
            lease_expires_at=NOW + timedelta(minutes=2),
        )
    with pytest.raises(InvalidOperationLeaseError):
        renew_lease(
            lease,
            owner_id=OWNER_ID,
            now=NOW + timedelta(minutes=1),
            lease_expires_at=NOW + timedelta(minutes=2),
        )
    with pytest.raises(InvalidOperationLeaseError):
        renew_lease(
            lease,
            owner_id=OWNER_ID,
            now=NOW + timedelta(seconds=30),
            lease_expires_at=NOW + timedelta(seconds=45),
        )


def test_expired_active_lease_requests_recovery() -> None:
    running = _snapshot(
        MarketOperationState.RUNNING,
        lease=_lease(lease_expires_at=NOW + timedelta(seconds=10)),
    )

    assert (
        request_lease_recovery(running, now=NOW + timedelta(seconds=10))
        is MarketOperationState.RECOVERING
    )


def test_active_or_missing_lease_cannot_claim_recovery() -> None:
    running = _snapshot(MarketOperationState.RUNNING)
    pause_without_lease = _snapshot(MarketOperationState.PAUSE_REQUESTED)

    with pytest.raises(InvalidOperationLeaseError):
        request_lease_recovery(running, now=NOW + timedelta(seconds=30))
    with pytest.raises(InvalidOperationLeaseError):
        request_lease_recovery(pause_without_lease, now=NOW + timedelta(minutes=2))
    with pytest.raises(InvalidOperationTransitionError):
        request_lease_recovery(_snapshot(), now=NOW + timedelta(minutes=2))


@pytest.mark.parametrize(
    "changes",
    [
        {"chunks_planned": -1},
        {"chunks_completed": -1},
        {"chunks_failed": -1},
        {"candles_estimated": -1},
        {"candles_received": -1},
        {"candles_persisted": -1},
        {"requests_completed": -1},
        {"chunks_completed": 2, "chunks_failed": 1},
        {"candles_received": 1, "candles_persisted": 2},
    ],
)
def test_progress_rejects_invalid_counters(changes: dict[str, int]) -> None:
    values: dict[str, object] = {
        "chunks_planned": 2,
        "chunks_completed": 0,
        "chunks_failed": 0,
        "candles_estimated": 4,
        "candles_received": 0,
        "candles_persisted": 0,
        "requests_completed": 0,
        "updated_at": NOW,
    }
    values.update(changes)

    with pytest.raises(InvalidMarketOperationRequestError):
        OperationProgress(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field_name",
    [
        "chunks_completed",
        "chunks_failed",
        "candles_received",
        "candles_persisted",
        "requests_completed",
    ],
)
def test_progress_rejects_regression(field_name: str) -> None:
    previous = _progress(
        chunks_completed=1,
        chunks_failed=1,
        candles_received=4,
        candles_persisted=3,
        requests_completed=2,
    )
    current = replace(
        previous,
        **{field_name: getattr(previous, field_name) - 1},
        updated_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(OperationProgressRegressionError):
        require_progress_not_regressed(previous, current)


def test_progress_accepts_monotonic_update() -> None:
    previous = _progress()
    current = _progress(
        chunks_completed=1,
        candles_received=2,
        candles_persisted=2,
        requests_completed=1,
        updated_at=NOW + timedelta(seconds=1),
    )

    require_progress_not_regressed(previous, current)


@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
    ],
)
def test_terminal_snapshots_require_finished_at(state: MarketOperationState) -> None:
    terminal = _snapshot(state)

    with pytest.raises(InvalidMarketOperationRequestError):
        replace(terminal, finished_at=None)


def test_success_and_failure_are_mutually_exclusive() -> None:
    completed = _snapshot(MarketOperationState.COMPLETED)

    with pytest.raises(InvalidMarketOperationRequestError):
        replace(
            completed,
            failure=SanitizedOperationFailure(
                MarketOperationFailureCode.INTERNAL_ERROR,
                completed.updated_at,
            ),
        )


def test_nonterminal_snapshot_rejects_final_outcome() -> None:
    pending = _snapshot()

    with pytest.raises(InvalidMarketOperationRequestError):
        replace(
            pending,
            result=OperationResult(CHECKSUM_A, CHECKSUM_B, NOW),
            finished_at=NOW,
        )


def test_failed_and_cancelled_snapshots_require_coherent_sanitized_codes() -> None:
    failed = _snapshot(MarketOperationState.FAILED)
    cancelled = _snapshot(MarketOperationState.CANCELLED)

    with pytest.raises(InvalidMarketOperationRequestError):
        replace(
            failed,
            failure=SanitizedOperationFailure(
                MarketOperationFailureCode.CANCELLED_BY_ADMIN,
                NOW,
            ),
        )
    with pytest.raises(InvalidMarketOperationRequestError):
        replace(
            cancelled,
            failure=SanitizedOperationFailure(
                MarketOperationFailureCode.INTERNAL_ERROR,
                NOW,
            ),
        )


def test_snapshot_validates_lease_operation_and_state() -> None:
    with pytest.raises(InvalidOperationLeaseError):
        _snapshot(
            MarketOperationState.CLAIMED,
            lease=_lease(operation_id=UUID("55555555-5555-4555-8555-555555555555")),
        )
    with pytest.raises(InvalidOperationLeaseError):
        _snapshot(MarketOperationState.PENDING, lease=_lease())


@pytest.mark.parametrize(
    "changed_request",
    [
        _request(selector=_selector("ETH/USDT")),
        _request(operation_type=MarketOperationType.RAW_INCREMENTAL_UPDATE),
        _request(data_range=DataRange(NOW, END + timedelta(hours=1))),
        _request(checksum=CHECKSUM_B),
    ],
)
def test_operation_update_protects_identity_type_interval_and_plan(
    changed_request: MarketOperationRequest,
) -> None:
    previous = _snapshot()
    valid = _snapshot(
        MarketOperationState.CLAIMED,
        record_version=1,
        updated_at=NOW + timedelta(seconds=1),
        progress=_progress(updated_at=NOW + timedelta(seconds=1)),
    )
    validate_operation_update(previous, valid)

    with pytest.raises(InvalidMarketOperationRequestError):
        validate_operation_update(
            previous,
            replace(valid, request=changed_request),
        )


def test_operation_update_requires_exact_successor_version() -> None:
    previous = _snapshot()
    valid = _snapshot(
        MarketOperationState.CLAIMED,
        record_version=1,
        updated_at=NOW + timedelta(seconds=1),
        progress=_progress(updated_at=NOW + timedelta(seconds=1)),
    )

    with pytest.raises(OperationVersionConflictError):
        validate_operation_update(previous, replace(valid, record_version=2))


def test_operation_update_rejects_progress_regression_and_terminal_changes() -> None:
    running = _snapshot(
        MarketOperationState.RUNNING,
        record_version=3,
        progress=_progress(
            chunks_completed=1,
            candles_received=2,
            candles_persisted=2,
            requests_completed=1,
        ),
    )
    regressed = _snapshot(
        MarketOperationState.RUNNING,
        record_version=4,
        progress=_progress(updated_at=NOW + timedelta(seconds=1)),
        updated_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(OperationProgressRegressionError):
        validate_operation_update(running, regressed)

    completed = _snapshot(MarketOperationState.COMPLETED)
    with pytest.raises(MarketOperationTerminalError):
        validate_operation_update(completed, completed)


def test_operation_update_requires_explicit_reconciliation_mode() -> None:
    running = _snapshot(MarketOperationState.RUNNING)
    cancelled = _snapshot(
        MarketOperationState.CANCELLED,
        record_version=1,
        updated_at=NOW + timedelta(seconds=1),
        progress=_progress(updated_at=NOW + timedelta(seconds=1)),
    )

    with pytest.raises(InvalidOperationTransitionError):
        validate_operation_update(running, cancelled)
    validate_operation_update(running, cancelled, reconciliation=True)


def test_plan_and_snapshot_require_matching_immutable_totals() -> None:
    with pytest.raises(InvalidMarketOperationRequestError):
        _snapshot(plan=_plan(checksum=CHECKSUM_B))
    with pytest.raises(InvalidMarketOperationRequestError):
        _snapshot(
            progress=OperationProgress(
                chunks_planned=3,
                chunks_completed=0,
                chunks_failed=0,
                candles_estimated=4,
                candles_received=0,
                candles_persisted=0,
                requests_completed=0,
                updated_at=NOW,
            )
        )


def test_snapshot_rejects_excessive_local_job_id_and_incoherent_finished_time() -> None:
    with pytest.raises(InvalidMarketOperationRequestError):
        _snapshot(local_job_id="x" * 65)
    completed = _snapshot(MarketOperationState.COMPLETED)
    with pytest.raises(InvalidMarketOperationRequestError):
        replace(
            completed,
            result=OperationResult(
                CHECKSUM_A,
                CHECKSUM_B,
                completed.updated_at + timedelta(seconds=1),
            ),
            finished_at=completed.updated_at + timedelta(seconds=1),
        )


def test_failure_models_and_errors_contain_no_arbitrary_secret_fields() -> None:
    failure = SanitizedOperationFailure(MarketOperationFailureCode.INTERNAL_ERROR, NOW)
    secret = "postgresql://user:secret@example.invalid/database"

    assert tuple(failure.__dataclass_fields__) == ("code", "failed_at")
    assert secret not in repr(failure)
    for error_type in (
        InvalidMarketOperationRequestError,
        InvalidOperationLeaseError,
        InvalidOperationTransitionError,
        OperationIdempotencyConflictError,
        OperationProgressRegressionError,
        OperationVersionConflictError,
    ):
        assert secret not in str(error_type())
