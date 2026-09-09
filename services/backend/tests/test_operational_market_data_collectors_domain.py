from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

import app.operational_market_data_collectors as collectors

T0 = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

ACTOR = UUID("11111111-1111-4111-8111-111111111111")
EPOCH = UUID("22222222-2222-4222-8222-222222222222")
COMMAND = UUID("33333333-3333-4333-8333-333333333333")
WORKER_A = UUID("44444444-4444-4444-8444-444444444444")
WORKER_B = UUID("55555555-5555-4555-8555-555555555555")


def target(
    symbol: str = "BTC/USDT",
    timeframe: str = "1m",
    bootstrap_candles: int = 500,
) -> collectors.OperationalMarketDataCollectorTarget:
    return collectors.OperationalMarketDataCollectorTarget(
        symbol=symbol,
        timeframe=timeframe,
        bootstrap_candles=bootstrap_candles,
    )


def specification() -> collectors.OperationalMarketDataCollectorSpecification:
    return collectors.OperationalMarketDataCollectorSpecification(
        schema_version=(collectors.OPERATIONAL_MARKET_DATA_COLLECTOR_SCHEMA_VERSION),
        collector_contract_version=(collectors.OPERATIONAL_MARKET_DATA_COLLECTOR_CONTRACT_VERSION),
        scope=(collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW),
        targets=(
            target("BTC/USDT", "1m", 500),
            target("ETH/USDT", "5m", 300),
        ),
        interval_seconds=60,
        overlap_candles=2,
    )


def start_epoch() -> collectors.OperationalMarketDataCollectorEpoch:
    spec = specification()

    checksum = collectors.operational_market_data_collector_specification_checksum(spec)

    epoch, _command = collectors.start_operational_market_data_collector_epoch(
        epoch_id=EPOCH,
        command_id=COMMAND,
        specification=spec,
        start_intent=collectors.OperationalMarketDataCollectorStartIntent(
            specification_checksum=checksum,
        ),
        requested_by=ACTOR,
        requested_at=T0,
        idempotency_key="collector-start-1",
    )

    return epoch


def claim_epoch(
    epoch: collectors.OperationalMarketDataCollectorEpoch | None = None,
) -> collectors.OperationalMarketDataCollectorEpoch:
    return collectors.claim_operational_market_data_collector_epoch(
        start_epoch() if epoch is None else epoch,
        worker_id=WORKER_A,
        claimed_at=T0 + timedelta(seconds=1),
        lease_expires_at=T0 + timedelta(seconds=31),
    )


def running_epoch() -> collectors.OperationalMarketDataCollectorEpoch:
    claimed = claim_epoch()

    return collectors.mark_operational_market_data_collector_epoch_running(
        claimed,
        worker_id=WORKER_A,
        fencing_token=claimed.fencing_token,
        observed_at=T0 + timedelta(seconds=2),
    )


def command_intent(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    command_type: collectors.OperationalMarketDataCollectorCommandType,
) -> collectors.OperationalMarketDataCollectorCommandIntent:
    return collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=command_type,
        expected_record_version=epoch.record_version,
    )


def request(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    command_type: collectors.OperationalMarketDataCollectorCommandType,
    *,
    command_id: UUID = COMMAND,
    when: datetime = T0 + timedelta(seconds=3),
) -> collectors.OperationalMarketDataCollectorEpoch:
    updated, _command = collectors.request_operational_market_data_collector_command(
        epoch,
        command_id=command_id,
        intent=command_intent(epoch, command_type),
        actor_id=ACTOR,
        requested_at=when,
        idempotency_key=f"collector-{command_type.value.lower()}-1",
    )

    return updated


def test_target_is_canonicalized() -> None:
    value = target(" btc/usdt ", "1m", 10)

    assert value.symbol == "BTC/USDT"
    assert value.timeframe == "1m"
    assert value.key == "BTC/USDT:1m"


@pytest.mark.parametrize(
    "timeframe",
    [
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "4h",
        "12h",
        "1d",
        "1w",
    ],
)
def test_target_accepts_canonical_timeframes(timeframe: str) -> None:
    assert target(timeframe=timeframe).timeframe == timeframe


@pytest.mark.parametrize(
    ("symbol", "timeframe"),
    [
        ("BTCUSDT", "1m"),
        ("BTC/BTC", "1m"),
        ("BTC/USDT", "2m"),
    ],
)
def test_target_rejects_invalid_identity(
    symbol: str,
    timeframe: str,
) -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        target(symbol, timeframe, 10)


@pytest.mark.parametrize(
    "bootstrap",
    [0, 1_000_001],
)
def test_target_rejects_bootstrap_bounds(
    bootstrap: int,
) -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorBoundsExceededError):
        target("BTC/USDT", "1m", bootstrap)


def test_specification_requires_sorted_unique_targets() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorSpecification(
            schema_version=1,
            collector_contract_version=1,
            scope=(collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW),
            targets=(
                target("ETH/USDT", "5m"),
                target("BTC/USDT", "1m"),
            ),
            interval_seconds=60,
            overlap_candles=2,
        )


def test_specification_rejects_duplicate_target() -> None:
    item = target()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorSpecification(
            schema_version=1,
            collector_contract_version=1,
            scope=(collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW),
            targets=(item, item),
            interval_seconds=60,
            overlap_candles=2,
        )


@pytest.mark.parametrize(
    ("interval", "overlap"),
    [
        (0, 0),
        (3601, 0),
        (60, -1),
        (60, 101),
    ],
)
def test_specification_rejects_policy_bounds(
    interval: int,
    overlap: int,
) -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorBoundsExceededError):
        collectors.OperationalMarketDataCollectorSpecification(
            schema_version=1,
            collector_contract_version=1,
            scope=(collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW),
            targets=(target(),),
            interval_seconds=interval,
            overlap_candles=overlap,
        )


def test_specification_checksum_is_deterministic() -> None:
    first = specification()
    second = specification()

    assert collectors.operational_market_data_collector_specification_bytes(
        first
    ) == collectors.operational_market_data_collector_specification_bytes(second)

    assert collectors.operational_market_data_collector_specification_checksum(
        first
    ) == collectors.operational_market_data_collector_specification_checksum(second)

    assert collectors.operational_market_data_collector_specifications_equal(
        first,
        second,
    )


def test_specification_checksum_rejects_mismatch() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorChecksumMismatchError):
        collectors.validate_operational_market_data_collector_specification_checksum(
            specification(),
            "0" * 64,
        )


def test_start_creates_pending_running_epoch_and_command() -> None:
    spec = specification()

    checksum = collectors.operational_market_data_collector_specification_checksum(spec)

    epoch, command = collectors.start_operational_market_data_collector_epoch(
        epoch_id=EPOCH,
        command_id=COMMAND,
        specification=spec,
        start_intent=collectors.OperationalMarketDataCollectorStartIntent(
            specification_checksum=checksum,
        ),
        requested_by=ACTOR,
        requested_at=T0,
        idempotency_key="collector-start",
    )

    assert epoch.desired_state is collectors.OperationalMarketDataCollectorDesiredState.RUNNING
    assert epoch.observed_state is collectors.OperationalMarketDataCollectorObservedState.PENDING
    assert epoch.record_version == 1
    assert epoch.fencing_token == 0
    assert epoch.worker_claim is None

    assert command.command_type is collectors.OperationalMarketDataCollectorCommandType.START
    assert command.expected_record_version is None
    assert command.resulting_record_version == 1


def test_start_intent_must_match_specification() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
        collectors.start_operational_market_data_collector_epoch(
            epoch_id=EPOCH,
            command_id=COMMAND,
            specification=specification(),
            start_intent=collectors.OperationalMarketDataCollectorStartIntent(
                specification_checksum="0" * 64,
            ),
            requested_by=ACTOR,
            requested_at=T0,
            idempotency_key="collector-start",
        )


def test_epoch_checksum_binds_epoch_identity() -> None:
    epoch = start_epoch()

    other_checksum = collectors.operational_market_data_collector_epoch_checksum(
        UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        epoch.specification_checksum,
    )

    assert other_checksum != epoch.epoch_checksum


def test_pause_changes_desired_state_only() -> None:
    epoch = running_epoch()

    paused_requested = request(
        epoch,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    assert (
        paused_requested.desired_state
        is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    )
    assert (
        paused_requested.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.RUNNING
    )
    assert paused_requested.worker_claim is not None


def test_pause_settles_only_at_boundary_and_releases_claim() -> None:
    epoch = request(
        running_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    settled = collectors.settle_operational_market_data_collector_epoch_paused(
        epoch,
        worker_id=WORKER_A,
        fencing_token=epoch.fencing_token,
        observed_at=T0 + timedelta(seconds=4),
    )

    assert settled.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    assert settled.observed_state is collectors.OperationalMarketDataCollectorObservedState.PAUSED
    assert settled.worker_claim is None


def test_resume_changes_desired_state_without_physical_start() -> None:
    running = running_epoch()

    pause_requested = request(
        running,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    paused = collectors.settle_operational_market_data_collector_epoch_paused(
        pause_requested,
        worker_id=WORKER_A,
        fencing_token=pause_requested.fencing_token,
        observed_at=T0 + timedelta(seconds=4),
    )

    resumed = request(
        paused,
        collectors.OperationalMarketDataCollectorCommandType.RESUME,
        when=T0 + timedelta(seconds=5),
    )

    assert resumed.desired_state is collectors.OperationalMarketDataCollectorDesiredState.RUNNING
    assert resumed.observed_state is collectors.OperationalMarketDataCollectorObservedState.PAUSED
    assert resumed.worker_claim is None


def test_stop_unclaimed_pending_epoch_settles_terminal() -> None:
    stop_requested = request(
        start_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.STOP,
    )

    stopped = collectors.settle_unclaimed_operational_market_data_collector_epoch(
        stop_requested,
        observed_at=T0 + timedelta(seconds=4),
    )

    assert stopped.observed_state is collectors.OperationalMarketDataCollectorObservedState.STOPPED
    assert stopped.terminal_at == T0 + timedelta(seconds=4)
    assert collectors.operational_market_data_collector_epoch_is_terminal(stopped)


def test_claim_advances_fence_and_moves_to_starting() -> None:
    epoch = claim_epoch()

    assert epoch.fencing_token == 1
    assert epoch.record_version == 2

    assert epoch.observed_state is collectors.OperationalMarketDataCollectorObservedState.STARTING

    assert epoch.worker_claim is not None
    assert epoch.worker_claim.worker_id == WORKER_A


def test_mark_running_requires_current_worker_and_fence() -> None:
    epoch = claim_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.mark_operational_market_data_collector_epoch_running(
            epoch,
            worker_id=WORKER_B,
            fencing_token=epoch.fencing_token,
            observed_at=T0 + timedelta(seconds=2),
        )


def test_renewal_heartbeat_must_strictly_advance() -> None:
    epoch = claim_epoch()
    assert epoch.worker_claim is not None

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.renew_operational_market_data_collector_worker_claim(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            heartbeat_at=epoch.worker_claim.heartbeat_at,
            lease_expires_at=T0 + timedelta(seconds=40),
        )


def test_renewal_advances_record_version_without_changing_fence() -> None:
    epoch = claim_epoch()

    renewed = collectors.renew_operational_market_data_collector_worker_claim(
        epoch,
        worker_id=WORKER_A,
        fencing_token=epoch.fencing_token,
        heartbeat_at=T0 + timedelta(seconds=10),
        lease_expires_at=T0 + timedelta(seconds=41),
    )

    assert renewed.record_version == epoch.record_version + 1
    assert renewed.fencing_token == epoch.fencing_token

    assert renewed.worker_claim is not None
    assert renewed.worker_claim.heartbeat_at == (T0 + timedelta(seconds=10))


def test_recovery_requires_expired_claim_and_advances_fence() -> None:
    epoch = running_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.recover_operational_market_data_collector_epoch(
            epoch,
            worker_id=WORKER_B,
            recovered_at=T0 + timedelta(seconds=20),
            lease_expires_at=T0 + timedelta(seconds=50),
        )

    recovered = collectors.recover_operational_market_data_collector_epoch(
        epoch,
        worker_id=WORKER_B,
        recovered_at=T0 + timedelta(seconds=31),
        lease_expires_at=T0 + timedelta(seconds=61),
    )

    assert (
        recovered.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.RECOVERING
    )
    assert recovered.fencing_token == 2

    assert recovered.worker_claim is not None
    assert recovered.worker_claim.worker_id == WORKER_B


def test_stale_worker_is_fenced_after_recovery() -> None:
    epoch = running_epoch()

    recovered = collectors.recover_operational_market_data_collector_epoch(
        epoch,
        worker_id=WORKER_B,
        recovered_at=T0 + timedelta(seconds=31),
        lease_expires_at=T0 + timedelta(seconds=61),
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.mark_operational_market_data_collector_epoch_starting(
            recovered,
            worker_id=WORKER_A,
            fencing_token=1,
            observed_at=T0 + timedelta(seconds=32),
        )


def test_recovered_running_epoch_returns_via_starting() -> None:
    recovered = collectors.recover_operational_market_data_collector_epoch(
        running_epoch(),
        worker_id=WORKER_B,
        recovered_at=T0 + timedelta(seconds=31),
        lease_expires_at=T0 + timedelta(seconds=61),
    )

    starting = collectors.mark_operational_market_data_collector_epoch_starting(
        recovered,
        worker_id=WORKER_B,
        fencing_token=recovered.fencing_token,
        observed_at=T0 + timedelta(seconds=32),
    )

    running = collectors.mark_operational_market_data_collector_epoch_running(
        starting,
        worker_id=WORKER_B,
        fencing_token=starting.fencing_token,
        observed_at=T0 + timedelta(seconds=33),
    )

    assert running.observed_state is collectors.OperationalMarketDataCollectorObservedState.RUNNING


def test_stop_claimed_epoch_uses_stopping_boundary() -> None:
    stop_requested = request(
        running_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.STOP,
    )

    stopping = collectors.mark_operational_market_data_collector_epoch_stopping(
        stop_requested,
        worker_id=WORKER_A,
        fencing_token=stop_requested.fencing_token,
        observed_at=T0 + timedelta(seconds=4),
    )

    stopped = collectors.settle_operational_market_data_collector_epoch_stopped(
        stopping,
        worker_id=WORKER_A,
        fencing_token=stopping.fencing_token,
        observed_at=T0 + timedelta(seconds=5),
    )

    assert stopped.observed_state is collectors.OperationalMarketDataCollectorObservedState.STOPPED
    assert stopped.worker_claim is None
    assert stopped.terminal_at == T0 + timedelta(seconds=5)


def test_terminal_epoch_rejects_new_commands() -> None:
    stop_requested = request(
        start_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.STOP,
    )

    stopped = collectors.settle_unclaimed_operational_market_data_collector_epoch(
        stop_requested,
        observed_at=T0 + timedelta(seconds=4),
    )

    assert not collectors.is_operational_market_data_collector_command_allowed(
        stopped,
        collectors.OperationalMarketDataCollectorCommandType.RESUME,
    )


def test_claimed_failure_is_terminal_and_releases_claim() -> None:
    epoch = running_epoch()

    failed = collectors.fail_claimed_operational_market_data_collector_epoch(
        epoch,
        worker_id=WORKER_A,
        fencing_token=epoch.fencing_token,
        failure_code=(collectors.OperationalMarketDataCollectorFailureCode.LOCAL_STATE_INVALID),
        failed_at=T0 + timedelta(seconds=5),
    )

    assert failed.observed_state is collectors.OperationalMarketDataCollectorObservedState.FAILED
    assert failed.worker_claim is None
    assert failed.failure is not None

    assert (
        failed.failure.code
        is collectors.OperationalMarketDataCollectorFailureCode.LOCAL_STATE_INVALID
    )

    assert failed.terminal_at == T0 + timedelta(seconds=5)


def test_unclaimed_failure_is_terminal() -> None:
    failed = collectors.fail_unclaimed_operational_market_data_collector_epoch(
        start_epoch(),
        failure_code=(collectors.OperationalMarketDataCollectorFailureCode.COLLECTOR_SPEC_INVALID),
        failed_at=T0 + timedelta(seconds=1),
    )

    assert failed.observed_state is collectors.OperationalMarketDataCollectorObservedState.FAILED

    assert collectors.operational_market_data_collector_epoch_is_terminal(failed)


def test_non_start_command_rejects_tampered_fingerprint() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorCommand(
            command_id=COMMAND,
            command_contract_version=(
                collectors.OPERATIONAL_MARKET_DATA_COLLECTOR_COMMAND_CONTRACT_VERSION
            ),
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.PAUSED),
            expected_record_version=epoch.record_version,
            resulting_record_version=epoch.record_version + 1,
            actor_id=ACTOR,
            requested_at=T0 + timedelta(seconds=1),
            idempotency_key="pause-tampered",
            intent_fingerprint="0" * 64,
        )


def test_command_fingerprint_binds_record_version() -> None:
    epoch = start_epoch()

    first = command_intent(
        epoch,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    second = replace(
        first,
        expected_record_version=first.expected_record_version + 1,
    )

    assert collectors.operational_market_data_collector_command_intent_fingerprint(
        first
    ) != collectors.operational_market_data_collector_command_intent_fingerprint(second)


@pytest.mark.parametrize(
    ("current", "target_state", "allowed"),
    [
        ("PENDING", "STARTING", True),
        ("PENDING", "RUNNING", False),
        ("RUNNING", "PAUSED", True),
        ("RUNNING", "STOPPED", False),
        ("STOPPING", "STOPPED", True),
        ("STOPPED", "STARTING", False),
        ("FAILED", "STARTING", False),
    ],
)
def test_observed_transition_graph(
    current: str,
    target_state: str,
    allowed: bool,
) -> None:
    current_state = collectors.OperationalMarketDataCollectorObservedState(current)

    target = collectors.OperationalMarketDataCollectorObservedState(target_state)

    assert (
        collectors.is_operational_market_data_collector_transition_allowed(
            current_state,
            target,
        )
        is allowed
    )


# GATE_2A_ADVERSARIAL_TESTS


def test_invalid_idempotency_keys_fail_closed() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorBoundsExceededError):
        collectors.validate_operational_market_data_collector_idempotency_key("")

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.validate_operational_market_data_collector_idempotency_key("contains space")

    with pytest.raises(collectors.OperationalMarketDataCollectorBoundsExceededError):
        collectors.validate_operational_market_data_collector_idempotency_key("x" * 129)


def test_start_fingerprint_binds_specification_checksum() -> None:
    first = collectors.OperationalMarketDataCollectorStartIntent(specification_checksum="a" * 64)
    second = collectors.OperationalMarketDataCollectorStartIntent(specification_checksum="b" * 64)

    assert collectors.operational_market_data_collector_start_intent_fingerprint(
        first
    ) != collectors.operational_market_data_collector_start_intent_fingerprint(second)


def test_tampered_epoch_checksum_is_rejected_on_revalidation() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorChecksumMismatchError):
        replace(
            start_epoch(),
            epoch_checksum="0" * 64,
        )


def test_tampered_specification_checksum_is_rejected_on_epoch() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorChecksumMismatchError):
        replace(
            start_epoch(),
            specification_checksum="0" * 64,
        )


def test_stale_command_record_version_is_rejected() -> None:
    epoch = start_epoch()

    intent = collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
        expected_record_version=epoch.record_version + 1,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
        collectors.request_operational_market_data_collector_command(
            epoch,
            command_id=COMMAND,
            intent=intent,
            actor_id=ACTOR,
            requested_at=T0 + timedelta(seconds=1),
            idempotency_key="stale-version",
        )


def test_wrong_epoch_checksum_command_is_rejected() -> None:
    epoch = start_epoch()

    intent = collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum="0" * 64,
        command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
        expected_record_version=epoch.record_version,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
        collectors.request_operational_market_data_collector_command(
            epoch,
            command_id=COMMAND,
            intent=intent,
            actor_id=ACTOR,
            requested_at=T0 + timedelta(seconds=1),
            idempotency_key="wrong-checksum",
        )


def test_pause_requested_pending_epoch_cannot_be_claimed() -> None:
    epoch = request(
        start_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.claim_operational_market_data_collector_epoch(
            epoch,
            worker_id=WORKER_A,
            claimed_at=T0 + timedelta(seconds=4),
            lease_expires_at=T0 + timedelta(seconds=34),
        )


def test_resume_after_pause_allows_new_claim_with_higher_fence() -> None:
    running = running_epoch()

    pause_requested = request(
        running,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    paused = collectors.settle_operational_market_data_collector_epoch_paused(
        pause_requested,
        worker_id=WORKER_A,
        fencing_token=pause_requested.fencing_token,
        observed_at=T0 + timedelta(seconds=4),
    )

    resumed = request(
        paused,
        collectors.OperationalMarketDataCollectorCommandType.RESUME,
        when=T0 + timedelta(seconds=5),
    )

    reclaimed = collectors.claim_operational_market_data_collector_epoch(
        resumed,
        worker_id=WORKER_B,
        claimed_at=T0 + timedelta(seconds=6),
        lease_expires_at=T0 + timedelta(seconds=36),
    )

    assert reclaimed.fencing_token == 2
    assert reclaimed.worker_claim is not None
    assert reclaimed.worker_claim.worker_id == WORKER_B


def test_renew_rejects_stale_fencing_token() -> None:
    epoch = claim_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.renew_operational_market_data_collector_worker_claim(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token + 1,
            heartbeat_at=T0 + timedelta(seconds=10),
            lease_expires_at=T0 + timedelta(seconds=41),
        )


def test_renew_requires_lease_extension() -> None:
    epoch = claim_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.renew_operational_market_data_collector_worker_claim(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            heartbeat_at=T0 + timedelta(seconds=10),
            lease_expires_at=T0 + timedelta(seconds=31),
        )


def test_recovery_with_pause_intent_settles_without_new_cycle() -> None:
    epoch = request(
        running_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    recovered = collectors.recover_operational_market_data_collector_epoch(
        epoch,
        worker_id=WORKER_B,
        recovered_at=T0 + timedelta(seconds=31),
        lease_expires_at=T0 + timedelta(seconds=61),
    )

    assert recovered.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED

    paused = collectors.settle_operational_market_data_collector_epoch_paused(
        recovered,
        worker_id=WORKER_B,
        fencing_token=recovered.fencing_token,
        observed_at=T0 + timedelta(seconds=32),
    )

    assert paused.observed_state is collectors.OperationalMarketDataCollectorObservedState.PAUSED
    assert paused.worker_claim is None


def test_recovery_with_stop_intent_settles_without_new_cycle() -> None:
    epoch = request(
        running_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.STOP,
    )

    recovered = collectors.recover_operational_market_data_collector_epoch(
        epoch,
        worker_id=WORKER_B,
        recovered_at=T0 + timedelta(seconds=31),
        lease_expires_at=T0 + timedelta(seconds=61),
    )

    stopped = collectors.settle_operational_market_data_collector_epoch_stopped(
        recovered,
        worker_id=WORKER_B,
        fencing_token=recovered.fencing_token,
        observed_at=T0 + timedelta(seconds=32),
    )

    assert stopped.observed_state is collectors.OperationalMarketDataCollectorObservedState.STOPPED
    assert stopped.worker_claim is None


def test_pause_settlement_requires_pause_intent() -> None:
    epoch = running_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorStateTransitionConflictError):
        collectors.settle_operational_market_data_collector_epoch_paused(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            observed_at=T0 + timedelta(seconds=4),
        )


def test_stop_cannot_skip_stopping_from_running() -> None:
    epoch = request(
        running_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.STOP,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorStateTransitionConflictError):
        collectors.settle_operational_market_data_collector_epoch_stopped(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            observed_at=T0 + timedelta(seconds=4),
        )


def test_unclaimed_running_epoch_cannot_be_settled() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorStateTransitionConflictError):
        collectors.settle_unclaimed_operational_market_data_collector_epoch(
            epoch,
            observed_at=T0 + timedelta(seconds=1),
        )


def test_claimed_failure_rejects_expired_lease() -> None:
    epoch = running_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.fail_claimed_operational_market_data_collector_epoch(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
            failed_at=T0 + timedelta(seconds=31),
        )


def test_terminal_failure_cannot_be_reopened() -> None:
    failed = collectors.fail_unclaimed_operational_market_data_collector_epoch(
        start_epoch(),
        failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
        failed_at=T0 + timedelta(seconds=1),
    )

    assert not collectors.is_operational_market_data_collector_transition_allowed(
        failed.observed_state,
        collectors.OperationalMarketDataCollectorObservedState.STARTING,
    )

    assert not collectors.is_operational_market_data_collector_command_allowed(
        failed,
        collectors.OperationalMarketDataCollectorCommandType.RESUME,
    )


def test_failure_before_epoch_start_is_rejected() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.fail_unclaimed_operational_market_data_collector_epoch(
            start_epoch(),
            failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
            failed_at=T0 - timedelta(seconds=1),
        )


# GATE_2A_DEFENSIVE_COVERAGE_TESTS


def test_idempotency_key_rejects_non_string() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.validate_operational_market_data_collector_idempotency_key(123)


def test_target_rejects_non_string_symbol() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorTarget(
            symbol=123,  # type: ignore[arg-type]
            timeframe="1m",
            bootstrap_candles=10,
        )


def test_target_rejects_non_string_timeframe() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorTarget(
            symbol="BTC/USDT",
            timeframe=123,  # type: ignore[arg-type]
            bootstrap_candles=10,
        )


def test_target_rejects_bool_bootstrap() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorTarget(
            symbol="BTC/USDT",
            timeframe="1m",
            bootstrap_candles=True,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", 2),
        ("collector_contract_version", 2),
        ("scope", "BINANCE_SPOT_RAW"),
        ("targets", [target()]),
        ("interval_seconds", True),
        ("overlap_candles", True),
    ],
)
def test_specification_rejects_invalid_field_types_or_versions(
    field_name: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {
        "schema_version": 1,
        "collector_contract_version": 1,
        "scope": (collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW),
        "targets": (target(),),
        "interval_seconds": 60,
        "overlap_candles": 2,
    }
    kwargs[field_name] = value

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorSpecification(
            **kwargs  # type: ignore[arg-type]
        )


def test_specification_rejects_empty_target_set() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorBoundsExceededError):
        collectors.OperationalMarketDataCollectorSpecification(
            schema_version=1,
            collector_contract_version=1,
            scope=(collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW),
            targets=(),
            interval_seconds=60,
            overlap_candles=2,
        )


def test_checksum_validator_rejects_invalid_checksum_shape() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.validate_operational_market_data_collector_specification_checksum(
            specification(),
            "not-a-sha256",
        )


def test_start_intent_rejects_invalid_checksum_shape() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorStartIntent(specification_checksum="bad")


def test_start_fingerprint_rejects_wrong_object_type() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.operational_market_data_collector_start_intent_fingerprint(
            object()  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("epoch_id", "checksum"),
    [
        (UUID(int=0), "a" * 64),
        (EPOCH, "bad"),
    ],
)
def test_epoch_checksum_rejects_invalid_identity(
    epoch_id: UUID,
    checksum: str,
) -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.operational_market_data_collector_epoch_checksum(
            epoch_id,
            checksum,
        )


def test_command_target_rejects_unknown_command_type() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
        collectors.operational_market_data_collector_command_target(
            "PAUSE"  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("epoch_id", "epoch_checksum", "command_type", "version"),
    [
        (
            UUID(int=0),
            "a" * 64,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
            1,
        ),
        (
            EPOCH,
            "bad",
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
            1,
        ),
        (
            EPOCH,
            "a" * 64,
            collectors.OperationalMarketDataCollectorCommandType.START,
            1,
        ),
        (
            EPOCH,
            "a" * 64,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
            0,
        ),
    ],
)
def test_command_intent_rejects_invalid_contract(
    epoch_id: UUID,
    epoch_checksum: str,
    command_type: collectors.OperationalMarketDataCollectorCommandType,
    version: int,
) -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorCommandIntent(
            epoch_id=epoch_id,
            epoch_checksum=epoch_checksum,
            command_type=command_type,
            expected_record_version=version,
        )


@pytest.mark.parametrize(
    ("worker_id", "fence"),
    [
        (UUID(int=0), 1),
        (WORKER_A, 0),
    ],
)
def test_worker_claim_rejects_invalid_identity_or_fence(
    worker_id: UUID,
    fence: int,
) -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.OperationalMarketDataCollectorWorkerClaim(
            epoch_id=EPOCH,
            worker_id=worker_id,
            fencing_token=fence,
            claimed_at=T0,
            heartbeat_at=T0,
            lease_expires_at=T0 + timedelta(seconds=30),
        )


def test_worker_claim_rejects_naive_timestamp() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.OperationalMarketDataCollectorWorkerClaim(
            epoch_id=EPOCH,
            worker_id=WORKER_A,
            fencing_token=1,
            claimed_at=datetime(2026, 9, 8, 12, 0),
            heartbeat_at=T0,
            lease_expires_at=T0 + timedelta(seconds=30),
        )


def test_worker_claim_rejects_invalid_timestamp_order() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.OperationalMarketDataCollectorWorkerClaim(
            epoch_id=EPOCH,
            worker_id=WORKER_A,
            fencing_token=1,
            claimed_at=T0 + timedelta(seconds=2),
            heartbeat_at=T0 + timedelta(seconds=1),
            lease_expires_at=T0 + timedelta(seconds=30),
        )


def test_worker_claim_belongs_to_fails_closed_for_bad_inputs() -> None:
    claim = collectors.OperationalMarketDataCollectorWorkerClaim(
        epoch_id=EPOCH,
        worker_id=WORKER_A,
        fencing_token=1,
        claimed_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=30),
    )

    assert not claim.belongs_to(UUID(int=0), 1)
    assert not claim.belongs_to(WORKER_A, 0)


def test_worker_claim_is_active_rejects_time_before_heartbeat() -> None:
    claim = collectors.OperationalMarketDataCollectorWorkerClaim(
        epoch_id=EPOCH,
        worker_id=WORKER_A,
        fencing_token=1,
        claimed_at=T0 + timedelta(seconds=1),
        heartbeat_at=T0 + timedelta(seconds=1),
        lease_expires_at=T0 + timedelta(seconds=31),
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        claim.is_active(T0)


def test_worker_claim_is_active_rejects_naive_time() -> None:
    claim = collectors.OperationalMarketDataCollectorWorkerClaim(
        epoch_id=EPOCH,
        worker_id=WORKER_A,
        fencing_token=1,
        claimed_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=30),
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        claim.is_active(datetime(2026, 9, 8, 12, 0))


def test_failure_rejects_invalid_code() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorFailure(
            code="INTERNAL_ERROR",  # type: ignore[arg-type]
            failed_at=T0,
        )


def test_failure_rejects_naive_time() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorFailure(
            code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
            failed_at=datetime(2026, 9, 8, 12, 0),
        )


def _valid_pause_fingerprint(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
) -> str:
    intent = collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
        expected_record_version=epoch.record_version,
    )
    return collectors.operational_market_data_collector_command_intent_fingerprint(intent)


def test_start_command_rejects_expected_record_version() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorCommand(
            command_id=COMMAND,
            command_contract_version=1,
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=(collectors.OperationalMarketDataCollectorCommandType.START),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.RUNNING),
            expected_record_version=1,
            resulting_record_version=1,
            actor_id=ACTOR,
            requested_at=T0,
            idempotency_key="bad-start-version",
            intent_fingerprint=epoch.start_intent_fingerprint,
        )


def test_start_command_requires_record_version_one() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorCommand(
            command_id=COMMAND,
            command_contract_version=1,
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=(collectors.OperationalMarketDataCollectorCommandType.START),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.RUNNING),
            expected_record_version=None,
            resulting_record_version=2,
            actor_id=ACTOR,
            requested_at=T0,
            idempotency_key="bad-start-result",
            intent_fingerprint=epoch.start_intent_fingerprint,
        )


def test_non_start_command_requires_exact_resulting_version() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorCommand(
            command_id=COMMAND,
            command_contract_version=1,
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.PAUSED),
            expected_record_version=epoch.record_version,
            resulting_record_version=epoch.record_version + 2,
            actor_id=ACTOR,
            requested_at=T0 + timedelta(seconds=1),
            idempotency_key="bad-result-version",
            intent_fingerprint=_valid_pause_fingerprint(epoch),
        )


def test_command_rejects_desired_state_mismatch() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorCommand(
            command_id=COMMAND,
            command_contract_version=1,
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.RUNNING),
            expected_record_version=epoch.record_version,
            resulting_record_version=epoch.record_version + 1,
            actor_id=ACTOR,
            requested_at=T0 + timedelta(seconds=1),
            idempotency_key="bad-target-state",
            intent_fingerprint=_valid_pause_fingerprint(epoch),
        )


def test_command_rejects_zero_actor() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorCommand(
            command_id=COMMAND,
            command_contract_version=1,
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.PAUSED),
            expected_record_version=epoch.record_version,
            resulting_record_version=epoch.record_version + 1,
            actor_id=UUID(int=0),
            requested_at=T0 + timedelta(seconds=1),
            idempotency_key="zero-actor",
            intent_fingerprint=_valid_pause_fingerprint(epoch),
        )


def test_command_rejects_naive_requested_at() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.OperationalMarketDataCollectorCommand(
            command_id=COMMAND,
            command_contract_version=1,
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.PAUSED),
            expected_record_version=epoch.record_version,
            resulting_record_version=epoch.record_version + 1,
            actor_id=ACTOR,
            requested_at=datetime(2026, 9, 8, 12, 0),
            idempotency_key="naive-command-time",
            intent_fingerprint=_valid_pause_fingerprint(epoch),
        )


def test_epoch_rejects_claim_while_pending() -> None:
    epoch = start_epoch()

    claim = collectors.OperationalMarketDataCollectorWorkerClaim(
        epoch_id=epoch.epoch_id,
        worker_id=WORKER_A,
        fencing_token=1,
        claimed_at=T0 + timedelta(seconds=1),
        heartbeat_at=T0 + timedelta(seconds=1),
        lease_expires_at=T0 + timedelta(seconds=31),
    )

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        replace(
            epoch,
            fencing_token=1,
            worker_claim=claim,
        )


def test_epoch_running_requires_worker_claim() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        replace(
            running_epoch(),
            worker_claim=None,
        )


def test_failed_epoch_requires_failure_record() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        replace(
            start_epoch(),
            observed_state=(collectors.OperationalMarketDataCollectorObservedState.FAILED),
            terminal_at=T0 + timedelta(seconds=1),
        )


def test_stopped_epoch_requires_terminal_time() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        replace(
            start_epoch(),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.STOPPED),
            observed_state=(collectors.OperationalMarketDataCollectorObservedState.STOPPED),
        )


def test_nonterminal_epoch_rejects_failure_record() -> None:
    failure = collectors.OperationalMarketDataCollectorFailure(
        code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
        failed_at=T0 + timedelta(seconds=1),
    )

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        replace(
            start_epoch(),
            failure=failure,
        )


def test_nonterminal_epoch_rejects_terminal_time() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        replace(
            start_epoch(),
            terminal_at=T0 + timedelta(seconds=1),
        )


def test_request_rejects_wrong_epoch_identity() -> None:
    epoch = start_epoch()

    intent = collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        epoch_checksum=epoch.epoch_checksum,
        command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
        expected_record_version=epoch.record_version,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
        collectors.request_operational_market_data_collector_command(
            epoch,
            command_id=COMMAND,
            intent=intent,
            actor_id=ACTOR,
            requested_at=T0 + timedelta(seconds=1),
            idempotency_key="wrong-epoch-id",
        )


def test_request_rejects_time_before_epoch_start() -> None:
    epoch = start_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.request_operational_market_data_collector_command(
            epoch,
            command_id=COMMAND,
            intent=command_intent(
                epoch,
                collectors.OperationalMarketDataCollectorCommandType.PAUSE,
            ),
            actor_id=ACTOR,
            requested_at=T0 - timedelta(seconds=1),
            idempotency_key="before-start",
        )


def test_terminal_epoch_cannot_be_claimed() -> None:
    stop_requested = request(
        start_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.STOP,
    )
    stopped = collectors.settle_unclaimed_operational_market_data_collector_epoch(
        stop_requested,
        observed_at=T0 + timedelta(seconds=4),
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
        collectors.claim_operational_market_data_collector_epoch(
            stopped,
            worker_id=WORKER_A,
            claimed_at=T0 + timedelta(seconds=5),
            lease_expires_at=T0 + timedelta(seconds=35),
        )


def test_paused_desired_state_cannot_be_claimed_until_resume() -> None:
    pause_requested = request(
        start_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    paused = collectors.settle_unclaimed_operational_market_data_collector_epoch(
        pause_requested,
        observed_at=T0 + timedelta(seconds=4),
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.claim_operational_market_data_collector_epoch(
            paused,
            worker_id=WORKER_A,
            claimed_at=T0 + timedelta(seconds=5),
            lease_expires_at=T0 + timedelta(seconds=35),
        )


def test_claim_rejects_time_before_start() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.claim_operational_market_data_collector_epoch(
            start_epoch(),
            worker_id=WORKER_A,
            claimed_at=T0 - timedelta(seconds=1),
            lease_expires_at=T0 + timedelta(seconds=30),
        )


def test_claim_rejects_nonpositive_lease_window() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.claim_operational_market_data_collector_epoch(
            start_epoch(),
            worker_id=WORKER_A,
            claimed_at=T0 + timedelta(seconds=1),
            lease_expires_at=T0 + timedelta(seconds=1),
        )


def test_renew_rejects_lease_before_heartbeat() -> None:
    epoch = claim_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.renew_operational_market_data_collector_worker_claim(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            heartbeat_at=T0 + timedelta(seconds=10),
            lease_expires_at=T0 + timedelta(seconds=9),
        )


def test_recover_rejects_terminal_epoch() -> None:
    stop_requested = request(
        start_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.STOP,
    )
    stopped = collectors.settle_unclaimed_operational_market_data_collector_epoch(
        stop_requested,
        observed_at=T0 + timedelta(seconds=4),
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
        collectors.recover_operational_market_data_collector_epoch(
            stopped,
            worker_id=WORKER_B,
            recovered_at=T0 + timedelta(seconds=40),
            lease_expires_at=T0 + timedelta(seconds=70),
        )


def test_recover_requires_existing_claim() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.recover_operational_market_data_collector_epoch(
            start_epoch(),
            worker_id=WORKER_B,
            recovered_at=T0 + timedelta(seconds=40),
            lease_expires_at=T0 + timedelta(seconds=70),
        )


def test_recover_requires_future_lease() -> None:
    epoch = running_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.recover_operational_market_data_collector_epoch(
            epoch,
            worker_id=WORKER_B,
            recovered_at=T0 + timedelta(seconds=31),
            lease_expires_at=T0 + timedelta(seconds=31),
        )


def test_mark_starting_rejects_pause_desired_state() -> None:
    paused_requested = request(
        running_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    recovered = collectors.recover_operational_market_data_collector_epoch(
        paused_requested,
        worker_id=WORKER_B,
        recovered_at=T0 + timedelta(seconds=31),
        lease_expires_at=T0 + timedelta(seconds=61),
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorStateTransitionConflictError):
        collectors.mark_operational_market_data_collector_epoch_starting(
            recovered,
            worker_id=WORKER_B,
            fencing_token=recovered.fencing_token,
            observed_at=T0 + timedelta(seconds=32),
        )


def test_mark_running_rejects_already_running_state() -> None:
    epoch = running_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorStateTransitionConflictError):
        collectors.mark_operational_market_data_collector_epoch_running(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            observed_at=T0 + timedelta(seconds=4),
        )


def test_mark_stopping_requires_stop_intent() -> None:
    epoch = running_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorStateTransitionConflictError):
        collectors.mark_operational_market_data_collector_epoch_stopping(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            observed_at=T0 + timedelta(seconds=4),
        )


def test_unclaimed_settlement_rejects_existing_claim() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.settle_unclaimed_operational_market_data_collector_epoch(
            running_epoch(),
            observed_at=T0 + timedelta(seconds=4),
        )


def test_unclaimed_settlement_rejects_naive_datetime_safely() -> None:
    pause_requested = request(
        start_epoch(),
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
    )

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.settle_unclaimed_operational_market_data_collector_epoch(
            pause_requested,
            observed_at=datetime(2026, 9, 8, 12, 0),
        )


def test_unclaimed_failure_rejects_existing_claim() -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.fail_unclaimed_operational_market_data_collector_epoch(
            running_epoch(),
            failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
            failed_at=T0 + timedelta(seconds=4),
        )


def test_unclaimed_failure_rejects_terminal_epoch() -> None:
    failed = collectors.fail_unclaimed_operational_market_data_collector_epoch(
        start_epoch(),
        failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
        failed_at=T0 + timedelta(seconds=1),
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorStateTransitionConflictError):
        collectors.fail_unclaimed_operational_market_data_collector_epoch(
            failed,
            failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
            failed_at=T0 + timedelta(seconds=2),
        )


def test_unclaimed_failure_rejects_invalid_failure_code() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.fail_unclaimed_operational_market_data_collector_epoch(
            start_epoch(),
            failure_code="INTERNAL_ERROR",  # type: ignore[arg-type]
            failed_at=T0 + timedelta(seconds=1),
        )


def test_unclaimed_failure_rejects_naive_datetime_safely() -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.fail_unclaimed_operational_market_data_collector_epoch(
            start_epoch(),
            failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
            failed_at=datetime(2026, 9, 8, 12, 0),
        )


def test_claimed_failure_rejects_invalid_failure_code() -> None:
    epoch = running_epoch()

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        collectors.fail_claimed_operational_market_data_collector_epoch(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token,
            failure_code="INTERNAL_ERROR",  # type: ignore[arg-type]
            failed_at=T0 + timedelta(seconds=4),
        )


def test_claimed_failure_rejects_stale_fence() -> None:
    epoch = running_epoch()

    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        collectors.fail_claimed_operational_market_data_collector_epoch(
            epoch,
            worker_id=WORKER_A,
            fencing_token=epoch.fencing_token + 1,
            failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
            failed_at=T0 + timedelta(seconds=4),
        )
