"""Pure-domain tests for operational paper-session run control."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

import app.operational_paper_session_runs as public_contract
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationProfileBinding,
)
from app.operational_paper_session_runs import (
    MAX_OPERATIONAL_PAPER_SESSION_RUN_IDEMPOTENCY_KEY_LENGTH,
    OPERATIONAL_PAPER_SESSION_RUN_COMMAND_CONTRACT_VERSION,
    OPERATIONAL_PAPER_SESSION_RUN_CONTRACT_VERSION,
    OPERATIONAL_PAPER_SESSION_RUN_SCHEMA_VERSION,
    OPERATIONAL_PAPER_SESSION_RUN_START_CONTRACT_VERSION,
    InvalidOperationalPaperSessionRunSpecificationError,
    OperationalPaperSessionRunBoundsExceededError,
    OperationalPaperSessionRunCommandConflictError,
    OperationalPaperSessionRunCommandType,
    OperationalPaperSessionRunDesiredState,
    OperationalPaperSessionRunEpoch,
    OperationalPaperSessionRunEpochCommandIntent,
    OperationalPaperSessionRunEpochSpecification,
    OperationalPaperSessionRunEpochStartIntent,
    OperationalPaperSessionRunFailureCode,
    OperationalPaperSessionRunLeaseError,
    OperationalPaperSessionRunObservedState,
    OperationalPaperSessionRunStateTransitionConflictError,
    claim_operational_paper_session_run_epoch,
    fail_claimed_operational_paper_session_run_epoch,
    fail_unclaimed_operational_paper_session_run_epoch,
    is_operational_paper_session_run_command_allowed,
    is_operational_paper_session_run_transition_allowed,
    mark_operational_paper_session_run_epoch_running,
    mark_operational_paper_session_run_epoch_starting,
    mark_operational_paper_session_run_epoch_stopping,
    operational_paper_session_run_command_target,
    operational_paper_session_run_epoch_command_intent_fingerprint,
    operational_paper_session_run_epoch_is_terminal,
    operational_paper_session_run_epoch_specification_checksum,
    operational_paper_session_run_epoch_specification_payload,
    operational_paper_session_run_epoch_start_intent_fingerprint,
    recover_operational_paper_session_run_epoch,
    renew_operational_paper_session_run_worker_claim,
    request_operational_paper_session_run_epoch_command,
    require_operational_paper_session_run_transition,
    settle_operational_paper_session_run_epoch_paused,
    settle_operational_paper_session_run_epoch_stopped,
    settle_unclaimed_operational_paper_session_run_epoch,
    start_operational_paper_session_run_epoch,
    validate_operational_paper_session_run_idempotency_key,
)

EPOCH_ID = UUID("10000000-0000-4000-8000-000000000001")
COMMAND_ID = UUID("11000000-0000-4000-8000-000000000001")
ACTIVATION_ID = UUID("20000000-0000-4000-8000-000000000001")
MATERIALIZATION_ID = UUID("21000000-0000-4000-8000-000000000001")
AUTHORIZATION_ID = UUID("30000000-0000-4000-8000-000000000001")
PROFILE_ID = UUID("40000000-0000-4000-8000-000000000001")
MANDATE_ID = UUID("50000000-0000-4000-8000-000000000001")
SIMULATION_ID = UUID("60000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("70000000-0000-4000-8000-000000000001")

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _authorization_binding(
    **changes: object,
) -> OperationalPaperSessionMaterializationAuthorizationBinding:
    values: dict[str, object] = {
        "authorization_id": AUTHORIZATION_ID,
        "authorization_checksum": "c" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionMaterializationAuthorizationBinding(
        **values  # type: ignore[arg-type]
    )


def _profile_binding(
    **changes: object,
) -> OperationalPaperSessionMaterializationProfileBinding:
    values: dict[str, object] = {
        "profile_id": PROFILE_ID,
        "approved_revision": 2,
        "specification_checksum": "d" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionMaterializationProfileBinding(
        **values  # type: ignore[arg-type]
    )


def _mandate_binding(
    **changes: object,
) -> OperationalPaperSessionMaterializationMandateBinding:
    values: dict[str, object] = {
        "mandate_id": MANDATE_ID,
        "approved_revision": 3,
        "specification_checksum": "e" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionMaterializationMandateBinding(
        **values  # type: ignore[arg-type]
    )


def _specification(
    **changes: object,
) -> OperationalPaperSessionRunEpochSpecification:
    values: dict[str, object] = {
        "schema_version": OPERATIONAL_PAPER_SESSION_RUN_SCHEMA_VERSION,
        "run_contract_version": OPERATIONAL_PAPER_SESSION_RUN_CONTRACT_VERSION,
        "activation_id": ACTIVATION_ID,
        "activation_checksum": "a" * 64,
        "materialization_id": MATERIALIZATION_ID,
        "materialization_checksum": "b" * 64,
        "authorization_binding": _authorization_binding(),
        "profile_binding": _profile_binding(),
        "mandate_binding": _mandate_binding(),
        "simulation_id": SIMULATION_ID,
        "session_id": "f" * 64,
        "config_checksum": "1" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionRunEpochSpecification(
        **values  # type: ignore[arg-type]
    )


def _start_intent(
    **changes: object,
) -> OperationalPaperSessionRunEpochStartIntent:
    values: dict[str, object] = {
        "activation_id": ACTIVATION_ID,
        "activation_checksum": "a" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionRunEpochStartIntent(
        **values  # type: ignore[arg-type]
    )


def _started_epoch():
    return start_operational_paper_session_run_epoch(
        epoch_id=EPOCH_ID,
        command_id=COMMAND_ID,
        specification=_specification(),
        start_intent=_start_intent(),
        requested_by=ACTOR_ID,
        requested_at=NOW,
        idempotency_key="run:start:1",
    )


def test_public_contract_contains_no_private_or_duplicate_exports() -> None:
    assert len(public_contract.__all__) == 54
    assert len(public_contract.__all__) == len(
        set(public_contract.__all__)
    )
    assert all(
        not name.startswith("_")
        for name in public_contract.__all__
    )


def test_contract_constants_are_exact() -> None:
    assert OPERATIONAL_PAPER_SESSION_RUN_SCHEMA_VERSION == 1
    assert OPERATIONAL_PAPER_SESSION_RUN_CONTRACT_VERSION == 1
    assert OPERATIONAL_PAPER_SESSION_RUN_START_CONTRACT_VERSION == 1
    assert OPERATIONAL_PAPER_SESSION_RUN_COMMAND_CONTRACT_VERSION == 1
    assert MAX_OPERATIONAL_PAPER_SESSION_RUN_IDEMPOTENCY_KEY_LENGTH == 128


def test_enums_are_exact() -> None:
    assert tuple(OperationalPaperSessionRunDesiredState) == (
        OperationalPaperSessionRunDesiredState.RUNNING,
        OperationalPaperSessionRunDesiredState.PAUSED,
        OperationalPaperSessionRunDesiredState.STOPPED,
    )

    assert tuple(OperationalPaperSessionRunObservedState) == (
        OperationalPaperSessionRunObservedState.PENDING,
        OperationalPaperSessionRunObservedState.STARTING,
        OperationalPaperSessionRunObservedState.RUNNING,
        OperationalPaperSessionRunObservedState.PAUSED,
        OperationalPaperSessionRunObservedState.RECOVERING,
        OperationalPaperSessionRunObservedState.STOPPING,
        OperationalPaperSessionRunObservedState.STOPPED,
        OperationalPaperSessionRunObservedState.FAILED,
    )

    assert tuple(OperationalPaperSessionRunCommandType) == (
        OperationalPaperSessionRunCommandType.START,
        OperationalPaperSessionRunCommandType.PAUSE,
        OperationalPaperSessionRunCommandType.RESUME,
        OperationalPaperSessionRunCommandType.STOP,
    )

    assert tuple(OperationalPaperSessionRunFailureCode) == (
        OperationalPaperSessionRunFailureCode.AUTHORITY_LOST,
        OperationalPaperSessionRunFailureCode.ACTIVATION_REVOKED,
        OperationalPaperSessionRunFailureCode.CONFIG_UNAVAILABLE,
        OperationalPaperSessionRunFailureCode.CONFIG_IDENTITY_CONFLICT,
        OperationalPaperSessionRunFailureCode.PLUGIN_UNAVAILABLE,
        OperationalPaperSessionRunFailureCode.RAW_NOT_READY,
        OperationalPaperSessionRunFailureCode.LOCAL_RUNNER_BUSY,
        OperationalPaperSessionRunFailureCode.LOCAL_STATE_INVALID,
        OperationalPaperSessionRunFailureCode.LEASE_LOST,
        OperationalPaperSessionRunFailureCode.INTERNAL_ERROR,
    )


def test_specification_payload_is_exact_and_checksum_deterministic() -> None:
    specification = _specification()

    assert operational_paper_session_run_epoch_specification_payload(
        specification
    ) == {
        "schema_version": 1,
        "run_contract_version": 1,
        "activation_id": str(ACTIVATION_ID),
        "activation_checksum": "a" * 64,
        "materialization_id": str(MATERIALIZATION_ID),
        "materialization_checksum": "b" * 64,
        "authorization_id": str(AUTHORIZATION_ID),
        "authorization_checksum": "c" * 64,
        "profile_id": str(PROFILE_ID),
        "profile_approved_revision": 2,
        "profile_specification_checksum": "d" * 64,
        "mandate_id": str(MANDATE_ID),
        "mandate_approved_revision": 3,
        "mandate_specification_checksum": "e" * 64,
        "simulation_id": str(SIMULATION_ID),
        "session_id": "f" * 64,
        "config_checksum": "1" * 64,
    }

    checksum = operational_paper_session_run_epoch_specification_checksum(
        specification
    )

    assert SHA256.fullmatch(checksum)
    assert checksum == (
        operational_paper_session_run_epoch_specification_checksum(
            _specification()
        )
    )


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("activation_id", UUID("20000000-0000-4000-8000-000000000002")),
        ("activation_checksum", "2" * 64),
        ("materialization_id", UUID("21000000-0000-4000-8000-000000000002")),
        ("materialization_checksum", "3" * 64),
        ("simulation_id", UUID("60000000-0000-4000-8000-000000000002")),
        ("session_id", "4" * 64),
        ("config_checksum", "5" * 64),
    ],
)
def test_identity_dimensions_change_epoch_checksum(
    field_name: str,
    changed_value: object,
) -> None:
    baseline = (
        operational_paper_session_run_epoch_specification_checksum(
            _specification()
        )
    )

    changed = (
        operational_paper_session_run_epoch_specification_checksum(
            _specification(
                **{field_name: changed_value}
            )
        )
    )

    assert changed != baseline


def test_start_intent_is_minimal_and_fingerprint_deterministic() -> None:
    intent = _start_intent()

    assert {field.name for field in fields(intent)} == {
        "activation_id",
        "activation_checksum",
    }

    fingerprint = (
        operational_paper_session_run_epoch_start_intent_fingerprint(
            intent
        )
    )

    assert SHA256.fullmatch(fingerprint)

    assert fingerprint == (
        operational_paper_session_run_epoch_start_intent_fingerprint(
            _start_intent()
        )
    )

    assert fingerprint != (
        operational_paper_session_run_epoch_start_intent_fingerprint(
            _start_intent(
                activation_checksum="9" * 64
            )
        )
    )


def test_start_intent_excludes_generated_and_runtime_state() -> None:
    names = {
        field.name
        for field in fields(_start_intent())
    }

    forbidden = {
        "epoch_id",
        "epoch_checksum",
        "record_version",
        "fencing_token",
        "worker_id",
        "worker_claim",
        "heartbeat_at",
        "lease_expires_at",
        "desired_state",
        "observed_state",
        "failure",
        "terminal_at",
        "raw_data",
        "filesystem_state",
        "plugin_availability",
    }

    assert forbidden.isdisjoint(names)


@pytest.mark.parametrize(
    "key",
    [
        "run:start:1",
        "A",
        "A" * 128,
    ],
)
def test_safe_idempotency_key_is_preserved(
    key: str,
) -> None:
    assert (
        validate_operational_paper_session_run_idempotency_key(
            key
        )
        == key
    )


@pytest.mark.parametrize(
    "key",
    [
        "",
        "A" * 129,
        "unsafe key",
        "unsafe/value",
        1,
    ],
)
def test_invalid_idempotency_key_is_rejected(
    key: object,
) -> None:
    with pytest.raises(
        (
            InvalidOperationalPaperSessionRunSpecificationError,
            OperationalPaperSessionRunBoundsExceededError,
        )
    ):
        validate_operational_paper_session_run_idempotency_key(
            key
        )


def test_start_creates_exact_initial_epoch_and_command() -> None:
    epoch, command = _started_epoch()

    assert epoch.epoch_id == EPOCH_ID
    assert epoch.record_version == 1
    assert epoch.fencing_token == 0
    assert (
        epoch.desired_state
        is OperationalPaperSessionRunDesiredState.RUNNING
    )
    assert (
        epoch.observed_state
        is OperationalPaperSessionRunObservedState.PENDING
    )
    assert epoch.worker_claim is None
    assert epoch.failure is None
    assert epoch.terminal_at is None

    assert command.command_id == COMMAND_ID
    assert (
        command.command_type
        is OperationalPaperSessionRunCommandType.START
    )
    assert command.expected_record_version is None
    assert command.resulting_record_version == 1
    assert command.epoch_id == epoch.epoch_id
    assert command.epoch_checksum == epoch.epoch_checksum
    assert command.actor_id == ACTOR_ID
    assert command.requested_at == NOW


def test_epoch_and_intents_are_frozen() -> None:
    epoch, _ = _started_epoch()

    with pytest.raises(FrozenInstanceError):
        epoch.record_version = 2  # type: ignore[misc]

    intent = _start_intent()

    with pytest.raises(FrozenInstanceError):
        intent.activation_checksum = "9" * 64  # type: ignore[misc]


def test_command_intent_cannot_encode_start() -> None:
    epoch, _ = _started_epoch()

    with pytest.raises(
        InvalidOperationalPaperSessionRunSpecificationError
    ):
        OperationalPaperSessionRunEpochCommandIntent(
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=OperationalPaperSessionRunCommandType.START,
            expected_record_version=epoch.record_version,
        )


def test_command_targets_are_exact() -> None:
    assert (
        operational_paper_session_run_command_target(
            OperationalPaperSessionRunCommandType.START
        )
        is OperationalPaperSessionRunDesiredState.RUNNING
    )
    assert (
        operational_paper_session_run_command_target(
            OperationalPaperSessionRunCommandType.PAUSE
        )
        is OperationalPaperSessionRunDesiredState.PAUSED
    )
    assert (
        operational_paper_session_run_command_target(
            OperationalPaperSessionRunCommandType.RESUME
        )
        is OperationalPaperSessionRunDesiredState.RUNNING
    )
    assert (
        operational_paper_session_run_command_target(
            OperationalPaperSessionRunCommandType.STOP
        )
        is OperationalPaperSessionRunDesiredState.STOPPED
    )

    with pytest.raises(
        OperationalPaperSessionRunCommandConflictError
    ):
        operational_paper_session_run_command_target(
            "PAUSE"  # type: ignore[arg-type]
        )


def test_observed_state_transition_matrix_is_exact() -> None:
    allowed = {
        OperationalPaperSessionRunObservedState.PENDING: {
            OperationalPaperSessionRunObservedState.STARTING,
            OperationalPaperSessionRunObservedState.PAUSED,
            OperationalPaperSessionRunObservedState.STOPPED,
            OperationalPaperSessionRunObservedState.FAILED,
        },
        OperationalPaperSessionRunObservedState.STARTING: {
            OperationalPaperSessionRunObservedState.RUNNING,
            OperationalPaperSessionRunObservedState.PAUSED,
            OperationalPaperSessionRunObservedState.RECOVERING,
            OperationalPaperSessionRunObservedState.STOPPING,
            OperationalPaperSessionRunObservedState.FAILED,
        },
        OperationalPaperSessionRunObservedState.RUNNING: {
            OperationalPaperSessionRunObservedState.PAUSED,
            OperationalPaperSessionRunObservedState.RECOVERING,
            OperationalPaperSessionRunObservedState.STOPPING,
            OperationalPaperSessionRunObservedState.FAILED,
        },
        OperationalPaperSessionRunObservedState.PAUSED: {
            OperationalPaperSessionRunObservedState.STARTING,
            OperationalPaperSessionRunObservedState.STOPPED,
            OperationalPaperSessionRunObservedState.FAILED,
        },
        OperationalPaperSessionRunObservedState.RECOVERING: {
            OperationalPaperSessionRunObservedState.STARTING,
            OperationalPaperSessionRunObservedState.PAUSED,
            OperationalPaperSessionRunObservedState.STOPPING,
            OperationalPaperSessionRunObservedState.STOPPED,
            OperationalPaperSessionRunObservedState.FAILED,
        },
        OperationalPaperSessionRunObservedState.STOPPING: {
            OperationalPaperSessionRunObservedState.RECOVERING,
            OperationalPaperSessionRunObservedState.STOPPED,
            OperationalPaperSessionRunObservedState.FAILED,
        },
        OperationalPaperSessionRunObservedState.STOPPED: set(),
        OperationalPaperSessionRunObservedState.FAILED: set(),
    }

    all_states = tuple(
        OperationalPaperSessionRunObservedState
    )

    for current in all_states:
        for target in all_states:
            expected = target in allowed[current]

            assert (
                is_operational_paper_session_run_transition_allowed(
                    current,
                    target,
                )
                is expected
            )

            if expected:
                require_operational_paper_session_run_transition(
                    current,
                    target,
                )
            else:
                with pytest.raises(
                    OperationalPaperSessionRunStateTransitionConflictError
                ):
                    require_operational_paper_session_run_transition(
                        current,
                        target,
                    )


def test_transition_helpers_reject_non_state_values() -> None:
    assert not (
        is_operational_paper_session_run_transition_allowed(
            "RUNNING",  # type: ignore[arg-type]
            OperationalPaperSessionRunObservedState.PAUSED,
        )
    )

    assert not (
        is_operational_paper_session_run_transition_allowed(
            OperationalPaperSessionRunObservedState.RUNNING,
            "PAUSED",  # type: ignore[arg-type]
        )
    )

    with pytest.raises(
        OperationalPaperSessionRunStateTransitionConflictError
    ):
        require_operational_paper_session_run_transition(
            "RUNNING",  # type: ignore[arg-type]
            OperationalPaperSessionRunObservedState.PAUSED,
        )


def test_terminal_observed_states_have_no_outgoing_transition() -> None:
    for current in (
        OperationalPaperSessionRunObservedState.STOPPED,
        OperationalPaperSessionRunObservedState.FAILED,
    ):
        for target in OperationalPaperSessionRunObservedState:
            assert not (
                is_operational_paper_session_run_transition_allowed(
                    current,
                    target,
                )
            )


def test_command_intent_is_minimal() -> None:
    epoch, _ = _started_epoch()

    intent = OperationalPaperSessionRunEpochCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=OperationalPaperSessionRunCommandType.PAUSE,
        expected_record_version=epoch.record_version,
    )

    assert {
        field.name
        for field in fields(intent)
    } == {
        "epoch_id",
        "epoch_checksum",
        "command_type",
        "expected_record_version",
    }


def test_command_intent_fingerprint_is_deterministic() -> None:
    epoch, _ = _started_epoch()

    intent = OperationalPaperSessionRunEpochCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=OperationalPaperSessionRunCommandType.PAUSE,
        expected_record_version=epoch.record_version,
    )

    fingerprint = (
        operational_paper_session_run_epoch_command_intent_fingerprint(
            intent
        )
    )

    rebuilt = OperationalPaperSessionRunEpochCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=OperationalPaperSessionRunCommandType.PAUSE,
        expected_record_version=epoch.record_version,
    )

    assert SHA256.fullmatch(fingerprint)

    assert fingerprint == (
        operational_paper_session_run_epoch_command_intent_fingerprint(
            rebuilt
        )
    )


@pytest.mark.parametrize(
    "changed",
    [
        {
            "command_type":
                OperationalPaperSessionRunCommandType.STOP
        },
        {
            "expected_record_version": 2
        },
        {
            "epoch_checksum": "9" * 64
        },
        {
            "epoch_id": UUID(
                "10000000-0000-4000-8000-000000000099"
            )
        },
    ],
)
def test_command_intent_dimensions_change_fingerprint(
    changed: dict[str, object],
) -> None:
    epoch, _ = _started_epoch()

    values: dict[str, object] = {
        "epoch_id": epoch.epoch_id,
        "epoch_checksum": epoch.epoch_checksum,
        "command_type":
            OperationalPaperSessionRunCommandType.PAUSE,
        "expected_record_version": epoch.record_version,
    }

    baseline_intent = (
        OperationalPaperSessionRunEpochCommandIntent(
            **values  # type: ignore[arg-type]
        )
    )

    baseline = (
        operational_paper_session_run_epoch_command_intent_fingerprint(
            baseline_intent
        )
    )

    values.update(changed)

    changed_intent = (
        OperationalPaperSessionRunEpochCommandIntent(
            **values  # type: ignore[arg-type]
        )
    )

    changed_fingerprint = (
        operational_paper_session_run_epoch_command_intent_fingerprint(
            changed_intent
        )
    )

    assert changed_fingerprint != baseline


@pytest.mark.parametrize(
    "changes",
    [
        {
            "epoch_id": UUID(int=0)
        },
        {
            "epoch_checksum": "BAD"
        },
        {
            "epoch_checksum": "A" * 64
        },
        {
            "command_type": "PAUSE"
        },
        {
            "expected_record_version": 0
        },
        {
            "expected_record_version": True
        },
    ],
)
def test_invalid_command_intent_fails_closed(
    changes: dict[str, object],
) -> None:
    epoch, _ = _started_epoch()

    values: dict[str, object] = {
        "epoch_id": epoch.epoch_id,
        "epoch_checksum": epoch.epoch_checksum,
        "command_type":
            OperationalPaperSessionRunCommandType.PAUSE,
        "expected_record_version": epoch.record_version,
    }

    values.update(changes)

    with pytest.raises(
        InvalidOperationalPaperSessionRunSpecificationError
    ):
        OperationalPaperSessionRunEpochCommandIntent(
            **values  # type: ignore[arg-type]
        )


def _command_intent(
    epoch: object,
    command_type: OperationalPaperSessionRunCommandType,
) -> OperationalPaperSessionRunEpochCommandIntent:
    return OperationalPaperSessionRunEpochCommandIntent(
        epoch_id=epoch.epoch_id,  # type: ignore[attr-defined]
        epoch_checksum=epoch.epoch_checksum,  # type: ignore[attr-defined]
        command_type=command_type,
        expected_record_version=epoch.record_version,  # type: ignore[attr-defined]
    )


def test_pause_request_changes_desired_state_and_records_command() -> None:
    epoch, _ = _started_epoch()

    intent = _command_intent(
        epoch,
        OperationalPaperSessionRunCommandType.PAUSE,
    )

    updated, command = (
        request_operational_paper_session_run_epoch_command(
            epoch,
            command_id=UUID(
                "12000000-0000-4000-8000-000000000001"
            ),
            intent=intent,
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=1),
            idempotency_key="run:pause:1",
        )
    )

    assert (
        updated.desired_state
        is OperationalPaperSessionRunDesiredState.PAUSED
    )
    assert (
        updated.observed_state
        is OperationalPaperSessionRunObservedState.PENDING
    )
    assert updated.record_version == 2
    assert updated.fencing_token == 0
    assert updated.epoch_checksum == epoch.epoch_checksum
    assert updated.worker_claim is None

    assert (
        command.command_type
        is OperationalPaperSessionRunCommandType.PAUSE
    )
    assert (
        command.desired_state
        is OperationalPaperSessionRunDesiredState.PAUSED
    )
    assert command.expected_record_version == 1
    assert command.resulting_record_version == 2
    assert command.actor_id == ACTOR_ID
    assert command.requested_at == NOW + timedelta(seconds=1)
    assert command.idempotency_key == "run:pause:1"
    assert command.intent_fingerprint == (
        operational_paper_session_run_epoch_command_intent_fingerprint(
            intent
        )
    )


def test_pending_pause_settles_without_worker_or_fence() -> None:
    epoch, _ = _started_epoch()

    epoch, _ = request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=UUID(
            "12000000-0000-4000-8000-000000000002"
        ),
        intent=_command_intent(
            epoch,
            OperationalPaperSessionRunCommandType.PAUSE,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=1),
        idempotency_key="run:pause:2",
    )

    paused = settle_unclaimed_operational_paper_session_run_epoch(
        epoch,
        observed_at=NOW + timedelta(seconds=2),
    )

    assert (
        paused.desired_state
        is OperationalPaperSessionRunDesiredState.PAUSED
    )
    assert (
        paused.observed_state
        is OperationalPaperSessionRunObservedState.PAUSED
    )
    assert paused.record_version == 3
    assert paused.fencing_token == 0
    assert paused.worker_claim is None
    assert paused.terminal_at is None


def test_resume_changes_desired_state_but_does_not_fake_running() -> None:
    epoch, _ = _started_epoch()

    epoch, _ = request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=UUID(
            "12000000-0000-4000-8000-000000000003"
        ),
        intent=_command_intent(
            epoch,
            OperationalPaperSessionRunCommandType.PAUSE,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=1),
        idempotency_key="run:pause:3",
    )

    epoch = settle_unclaimed_operational_paper_session_run_epoch(
        epoch,
        observed_at=NOW + timedelta(seconds=2),
    )

    previous_version = epoch.record_version

    intent = _command_intent(
        epoch,
        OperationalPaperSessionRunCommandType.RESUME,
    )

    resumed, command = (
        request_operational_paper_session_run_epoch_command(
            epoch,
            command_id=UUID(
                "13000000-0000-4000-8000-000000000001"
            ),
            intent=intent,
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=3),
            idempotency_key="run:resume:1",
        )
    )

    assert (
        resumed.desired_state
        is OperationalPaperSessionRunDesiredState.RUNNING
    )
    assert (
        resumed.observed_state
        is OperationalPaperSessionRunObservedState.PAUSED
    )
    assert resumed.record_version == previous_version + 1
    assert resumed.worker_claim is None

    assert (
        command.command_type
        is OperationalPaperSessionRunCommandType.RESUME
    )
    assert command.expected_record_version == previous_version
    assert command.resulting_record_version == previous_version + 1


def test_stop_from_paused_settles_terminally_without_worker() -> None:
    epoch, _ = _started_epoch()

    epoch, _ = request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=UUID(
            "12000000-0000-4000-8000-000000000004"
        ),
        intent=_command_intent(
            epoch,
            OperationalPaperSessionRunCommandType.PAUSE,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=1),
        idempotency_key="run:pause:4",
    )

    epoch = settle_unclaimed_operational_paper_session_run_epoch(
        epoch,
        observed_at=NOW + timedelta(seconds=2),
    )

    epoch, command = (
        request_operational_paper_session_run_epoch_command(
            epoch,
            command_id=UUID(
                "14000000-0000-4000-8000-000000000001"
            ),
            intent=_command_intent(
                epoch,
                OperationalPaperSessionRunCommandType.STOP,
            ),
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=3),
            idempotency_key="run:stop:1",
        )
    )

    assert (
        epoch.desired_state
        is OperationalPaperSessionRunDesiredState.STOPPED
    )
    assert (
        epoch.observed_state
        is OperationalPaperSessionRunObservedState.PAUSED
    )
    assert (
        command.command_type
        is OperationalPaperSessionRunCommandType.STOP
    )

    stopped = settle_unclaimed_operational_paper_session_run_epoch(
        epoch,
        observed_at=NOW + timedelta(seconds=4),
    )

    assert (
        stopped.observed_state
        is OperationalPaperSessionRunObservedState.STOPPED
    )
    assert (
        stopped.desired_state
        is OperationalPaperSessionRunDesiredState.STOPPED
    )
    assert stopped.worker_claim is None
    assert stopped.failure is None
    assert stopped.terminal_at == NOW + timedelta(seconds=4)


def test_allowed_commands_follow_current_desired_state() -> None:
    epoch, _ = _started_epoch()

    assert is_operational_paper_session_run_command_allowed(
        epoch,
        OperationalPaperSessionRunCommandType.PAUSE,
    )
    assert is_operational_paper_session_run_command_allowed(
        epoch,
        OperationalPaperSessionRunCommandType.STOP,
    )
    assert not is_operational_paper_session_run_command_allowed(
        epoch,
        OperationalPaperSessionRunCommandType.RESUME,
    )
    assert not is_operational_paper_session_run_command_allowed(
        epoch,
        OperationalPaperSessionRunCommandType.START,
    )

    paused_requested, _ = (
        request_operational_paper_session_run_epoch_command(
            epoch,
            command_id=UUID(
                "12000000-0000-4000-8000-000000000005"
            ),
            intent=_command_intent(
                epoch,
                OperationalPaperSessionRunCommandType.PAUSE,
            ),
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=1),
            idempotency_key="run:pause:5",
        )
    )

    assert not is_operational_paper_session_run_command_allowed(
        paused_requested,
        OperationalPaperSessionRunCommandType.PAUSE,
    )
    assert is_operational_paper_session_run_command_allowed(
        paused_requested,
        OperationalPaperSessionRunCommandType.RESUME,
    )
    assert is_operational_paper_session_run_command_allowed(
        paused_requested,
        OperationalPaperSessionRunCommandType.STOP,
    )

    stopped_requested, _ = (
        request_operational_paper_session_run_epoch_command(
            paused_requested,
            command_id=UUID(
                "14000000-0000-4000-8000-000000000002"
            ),
            intent=_command_intent(
                paused_requested,
                OperationalPaperSessionRunCommandType.STOP,
            ),
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=2),
            idempotency_key="run:stop:2",
        )
    )

    for command_type in OperationalPaperSessionRunCommandType:
        assert not is_operational_paper_session_run_command_allowed(
            stopped_requested,
            command_type,
        )


def test_duplicate_pause_is_rejected_after_desired_state_changes() -> None:
    epoch, _ = _started_epoch()

    epoch, _ = request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=UUID(
            "12000000-0000-4000-8000-000000000006"
        ),
        intent=_command_intent(
            epoch,
            OperationalPaperSessionRunCommandType.PAUSE,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=1),
        idempotency_key="run:pause:6",
    )

    with pytest.raises(
        OperationalPaperSessionRunCommandConflictError
    ):
        request_operational_paper_session_run_epoch_command(
            epoch,
            command_id=UUID(
                "12000000-0000-4000-8000-000000000007"
            ),
            intent=_command_intent(
                epoch,
                OperationalPaperSessionRunCommandType.PAUSE,
            ),
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=2),
            idempotency_key="run:pause:7",
        )


@pytest.mark.parametrize(
    "change",
    [
        {
            "epoch_id": UUID(
                "10000000-0000-4000-8000-000000000099"
            )
        },
        {
            "epoch_checksum": "9" * 64
        },
        {
            "expected_record_version": 2
        },
    ],
)
def test_divergent_command_intent_is_rejected(
    change: dict[str, object],
) -> None:
    epoch, _ = _started_epoch()

    values: dict[str, object] = {
        "epoch_id": epoch.epoch_id,
        "epoch_checksum": epoch.epoch_checksum,
        "command_type":
            OperationalPaperSessionRunCommandType.PAUSE,
        "expected_record_version": epoch.record_version,
    }

    values.update(change)

    intent = OperationalPaperSessionRunEpochCommandIntent(
        **values  # type: ignore[arg-type]
    )

    with pytest.raises(
        OperationalPaperSessionRunCommandConflictError
    ):
        request_operational_paper_session_run_epoch_command(
            epoch,
            command_id=UUID(
                "12000000-0000-4000-8000-000000000008"
            ),
            intent=intent,
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=1),
            idempotency_key="run:pause:8",
        )


def test_command_timestamp_cannot_predate_epoch_start() -> None:
    epoch, _ = _started_epoch()

    with pytest.raises(
        InvalidOperationalPaperSessionRunSpecificationError
    ):
        request_operational_paper_session_run_epoch_command(
            epoch,
            command_id=UUID(
                "12000000-0000-4000-8000-000000000009"
            ),
            intent=_command_intent(
                epoch,
                OperationalPaperSessionRunCommandType.PAUSE,
            ),
            actor_id=ACTOR_ID,
            requested_at=NOW - timedelta(seconds=1),
            idempotency_key="run:pause:9",
        )


def test_terminal_epoch_rejects_new_commands() -> None:
    epoch, _ = _started_epoch()

    epoch, _ = request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=UUID(
            "14000000-0000-4000-8000-000000000003"
        ),
        intent=_command_intent(
            epoch,
            OperationalPaperSessionRunCommandType.STOP,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=1),
        idempotency_key="run:stop:3",
    )

    epoch = settle_unclaimed_operational_paper_session_run_epoch(
        epoch,
        observed_at=NOW + timedelta(seconds=2),
    )

    intent = OperationalPaperSessionRunEpochCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=OperationalPaperSessionRunCommandType.STOP,
        expected_record_version=epoch.record_version,
    )

    with pytest.raises(
        OperationalPaperSessionRunCommandConflictError
    ):
        request_operational_paper_session_run_epoch_command(
            epoch,
            command_id=UUID(
                "14000000-0000-4000-8000-000000000004"
            ),
            intent=intent,
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=3),
            idempotency_key="run:stop:4",
        )


@pytest.mark.parametrize(
    "start_intent",
    [
        OperationalPaperSessionRunEpochStartIntent(
            activation_id=UUID(
                "20000000-0000-4000-8000-000000000099"
            ),
            activation_checksum="a" * 64,
        ),
        OperationalPaperSessionRunEpochStartIntent(
            activation_id=ACTIVATION_ID,
            activation_checksum="9" * 64,
        ),
    ],
)
def test_start_rejects_activation_identity_mismatch(
    start_intent: OperationalPaperSessionRunEpochStartIntent,
) -> None:
    with pytest.raises(
        OperationalPaperSessionRunCommandConflictError
    ):
        start_operational_paper_session_run_epoch(
            epoch_id=EPOCH_ID,
            command_id=COMMAND_ID,
            specification=_specification(),
            start_intent=start_intent,
            requested_by=ACTOR_ID,
            requested_at=NOW,
            idempotency_key="run:start:mismatch",
        )


WORKER_1 = UUID("80000000-0000-4000-8000-000000000001")
WORKER_2 = UUID("80000000-0000-4000-8000-000000000002")


def test_initial_worker_claim_creates_starting_state_and_first_fence() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    assert (
        claimed.observed_state
        is OperationalPaperSessionRunObservedState.STARTING
    )
    assert (
        claimed.desired_state
        is OperationalPaperSessionRunDesiredState.RUNNING
    )
    assert claimed.record_version == 2
    assert claimed.fencing_token == 1

    assert claimed.worker_claim is not None
    assert claimed.worker_claim.epoch_id == epoch.epoch_id
    assert claimed.worker_claim.worker_id == WORKER_1
    assert claimed.worker_claim.fencing_token == 1
    assert claimed.worker_claim.claimed_at == NOW + timedelta(seconds=1)
    assert claimed.worker_claim.heartbeat_at == NOW + timedelta(seconds=1)
    assert (
        claimed.worker_claim.lease_expires_at
        == NOW + timedelta(seconds=61)
    )


def test_duplicate_claim_is_rejected() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        claim_operational_paper_session_run_epoch(
            claimed,
            worker_id=WORKER_2,
            claimed_at=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=62),
        )


def test_claim_requires_desired_running() -> None:
    epoch, _ = _started_epoch()

    epoch, _ = request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=UUID(
            "15000000-0000-4000-8000-000000000001"
        ),
        intent=_command_intent(
            epoch,
            OperationalPaperSessionRunCommandType.PAUSE,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=1),
        idempotency_key="run:pause:claim-block",
    )

    assert (
        epoch.desired_state
        is OperationalPaperSessionRunDesiredState.PAUSED
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        claim_operational_paper_session_run_epoch(
            epoch,
            worker_id=WORKER_1,
            claimed_at=NOW + timedelta(seconds=2),
            lease_expires_at=NOW + timedelta(seconds=62),
        )


def test_lease_renewal_preserves_worker_fence_and_claim_time() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    renewed = renew_operational_paper_session_run_worker_claim(
        claimed,
        worker_id=WORKER_1,
        fencing_token=1,
        heartbeat_at=NOW + timedelta(seconds=20),
        lease_expires_at=NOW + timedelta(seconds=120),
    )

    assert renewed.record_version == claimed.record_version + 1
    assert renewed.fencing_token == 1
    assert renewed.worker_claim is not None
    assert renewed.worker_claim.worker_id == WORKER_1
    assert renewed.worker_claim.fencing_token == 1
    assert (
        renewed.worker_claim.claimed_at
        == claimed.worker_claim.claimed_at
    )
    assert (
        renewed.worker_claim.heartbeat_at
        == NOW + timedelta(seconds=20)
    )
    assert (
        renewed.worker_claim.lease_expires_at
        == NOW + timedelta(seconds=120)
    )


@pytest.mark.parametrize(
    ("worker_id", "fencing_token"),
    [
        (
            UUID("80000000-0000-4000-8000-000000000099"),
            1,
        ),
        (
            WORKER_1,
            2,
        ),
    ],
)
def test_lease_renewal_rejects_wrong_owner_or_fence(
    worker_id: UUID,
    fencing_token: int,
) -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        renew_operational_paper_session_run_worker_claim(
            claimed,
            worker_id=worker_id,
            fencing_token=fencing_token,
            heartbeat_at=NOW + timedelta(seconds=20),
            lease_expires_at=NOW + timedelta(seconds=120),
        )


def test_expired_lease_cannot_be_renewed() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        renew_operational_paper_session_run_worker_claim(
            claimed,
            worker_id=WORKER_1,
            fencing_token=1,
            heartbeat_at=NOW + timedelta(seconds=61),
            lease_expires_at=NOW + timedelta(seconds=120),
        )


def test_lease_renewal_must_extend_existing_expiry() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        renew_operational_paper_session_run_worker_claim(
            claimed,
            worker_id=WORKER_1,
            fencing_token=1,
            heartbeat_at=NOW + timedelta(seconds=20),
            lease_expires_at=NOW + timedelta(seconds=60),
        )


def test_recovery_requires_expired_lease() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        recover_operational_paper_session_run_epoch(
            claimed,
            worker_id=WORKER_2,
            recovered_at=NOW + timedelta(seconds=60),
            lease_expires_at=NOW + timedelta(seconds=120),
        )


def test_recovery_replaces_worker_and_increments_fence() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=10),
    )

    recovered = recover_operational_paper_session_run_epoch(
        claimed,
        worker_id=WORKER_2,
        recovered_at=NOW + timedelta(seconds=11),
        lease_expires_at=NOW + timedelta(seconds=71),
    )

    assert (
        recovered.observed_state
        is OperationalPaperSessionRunObservedState.RECOVERING
    )
    assert recovered.record_version == claimed.record_version + 1
    assert recovered.fencing_token == 2

    assert recovered.worker_claim is not None
    assert recovered.worker_claim.worker_id == WORKER_2
    assert recovered.worker_claim.fencing_token == 2
    assert (
        recovered.worker_claim.claimed_at
        == NOW + timedelta(seconds=11)
    )


def test_stale_worker_is_rejected_after_recovery() -> None:
    epoch, _ = _started_epoch()

    epoch = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=10),
    )

    recovered = recover_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_2,
        recovered_at=NOW + timedelta(seconds=11),
        lease_expires_at=NOW + timedelta(seconds=71),
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        mark_operational_paper_session_run_epoch_starting(
            recovered,
            worker_id=WORKER_1,
            fencing_token=1,
            observed_at=NOW + timedelta(seconds=12),
        )


def test_recovery_owner_can_return_to_starting_then_running() -> None:
    epoch, _ = _started_epoch()

    epoch = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=10),
    )

    recovered = recover_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_2,
        recovered_at=NOW + timedelta(seconds=11),
        lease_expires_at=NOW + timedelta(seconds=71),
    )

    starting = mark_operational_paper_session_run_epoch_starting(
        recovered,
        worker_id=WORKER_2,
        fencing_token=2,
        observed_at=NOW + timedelta(seconds=12),
    )

    assert (
        starting.observed_state
        is OperationalPaperSessionRunObservedState.STARTING
    )
    assert starting.fencing_token == 2
    assert starting.worker_claim is not None
    assert starting.worker_claim.worker_id == WORKER_2

    running = mark_operational_paper_session_run_epoch_running(
        starting,
        worker_id=WORKER_2,
        fencing_token=2,
        observed_at=NOW + timedelta(seconds=13),
    )

    assert (
        running.observed_state
        is OperationalPaperSessionRunObservedState.RUNNING
    )
    assert running.fencing_token == 2
    assert running.worker_claim is not None
    assert running.worker_claim.worker_id == WORKER_2


def _running_epoch(
    *,
    worker_id: UUID = WORKER_1,
    lease_seconds: int = 61,
) -> OperationalPaperSessionRunEpoch:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=worker_id,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=lease_seconds),
    )

    return mark_operational_paper_session_run_epoch_running(
        claimed,
        worker_id=worker_id,
        fencing_token=1,
        observed_at=NOW + timedelta(seconds=2),
    )


def _paused_epoch() -> OperationalPaperSessionRunEpoch:
    epoch = _running_epoch()

    epoch, _ = request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=UUID(
            "16000000-0000-4000-8000-000000000001"
        ),
        intent=_command_intent(
            epoch,
            OperationalPaperSessionRunCommandType.PAUSE,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=3),
        idempotency_key="run:pause:cycle",
    )

    return settle_operational_paper_session_run_epoch_paused(
        epoch,
        worker_id=WORKER_1,
        fencing_token=1,
        observed_at=NOW + timedelta(seconds=4),
    )


def test_running_pause_releases_claim_and_preserves_fence() -> None:
    running = _running_epoch()
    previous_version = running.record_version

    requested, command = (
        request_operational_paper_session_run_epoch_command(
            running,
            command_id=UUID(
                "16000000-0000-4000-8000-000000000002"
            ),
            intent=_command_intent(
                running,
                OperationalPaperSessionRunCommandType.PAUSE,
            ),
            actor_id=ACTOR_ID,
            requested_at=NOW + timedelta(seconds=3),
            idempotency_key="run:pause:running",
        )
    )

    assert requested.record_version == previous_version + 1
    assert requested.worker_claim is not None
    assert requested.fencing_token == 1

    paused = settle_operational_paper_session_run_epoch_paused(
        requested,
        worker_id=WORKER_1,
        fencing_token=1,
        observed_at=NOW + timedelta(seconds=4),
    )

    assert (
        paused.desired_state
        is OperationalPaperSessionRunDesiredState.PAUSED
    )
    assert (
        paused.observed_state
        is OperationalPaperSessionRunObservedState.PAUSED
    )
    assert paused.record_version == previous_version + 2
    assert paused.fencing_token == 1
    assert paused.worker_claim is None
    assert paused.terminal_at is None
    assert (
        command.command_type
        is OperationalPaperSessionRunCommandType.PAUSE
    )


def test_paused_resume_requires_new_claim_and_new_fence() -> None:
    paused = _paused_epoch()

    assert paused.fencing_token == 1
    assert paused.worker_claim is None

    resumed, _ = request_operational_paper_session_run_epoch_command(
        paused,
        command_id=UUID(
            "17000000-0000-4000-8000-000000000001"
        ),
        intent=_command_intent(
            paused,
            OperationalPaperSessionRunCommandType.RESUME,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=5),
        idempotency_key="run:resume:cycle",
    )

    assert (
        resumed.desired_state
        is OperationalPaperSessionRunDesiredState.RUNNING
    )
    assert (
        resumed.observed_state
        is OperationalPaperSessionRunObservedState.PAUSED
    )
    assert resumed.worker_claim is None
    assert resumed.fencing_token == 1

    reclaimed = claim_operational_paper_session_run_epoch(
        resumed,
        worker_id=WORKER_2,
        claimed_at=NOW + timedelta(seconds=6),
        lease_expires_at=NOW + timedelta(seconds=66),
    )

    assert (
        reclaimed.observed_state
        is OperationalPaperSessionRunObservedState.STARTING
    )
    assert reclaimed.fencing_token == 2
    assert reclaimed.worker_claim is not None
    assert reclaimed.worker_claim.worker_id == WORKER_2
    assert reclaimed.worker_claim.fencing_token == 2

    running = mark_operational_paper_session_run_epoch_running(
        reclaimed,
        worker_id=WORKER_2,
        fencing_token=2,
        observed_at=NOW + timedelta(seconds=7),
    )

    assert (
        running.observed_state
        is OperationalPaperSessionRunObservedState.RUNNING
    )
    assert running.fencing_token == 2


def test_old_worker_cannot_act_after_pause_resume_reclaim() -> None:
    paused = _paused_epoch()

    resumed, _ = request_operational_paper_session_run_epoch_command(
        paused,
        command_id=UUID(
            "17000000-0000-4000-8000-000000000002"
        ),
        intent=_command_intent(
            paused,
            OperationalPaperSessionRunCommandType.RESUME,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=5),
        idempotency_key="run:resume:stale-worker",
    )

    reclaimed = claim_operational_paper_session_run_epoch(
        resumed,
        worker_id=WORKER_2,
        claimed_at=NOW + timedelta(seconds=6),
        lease_expires_at=NOW + timedelta(seconds=66),
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        mark_operational_paper_session_run_epoch_running(
            reclaimed,
            worker_id=WORKER_1,
            fencing_token=1,
            observed_at=NOW + timedelta(seconds=7),
        )

    running = mark_operational_paper_session_run_epoch_running(
        reclaimed,
        worker_id=WORKER_2,
        fencing_token=2,
        observed_at=NOW + timedelta(seconds=7),
    )

    assert (
        running.observed_state
        is OperationalPaperSessionRunObservedState.RUNNING
    )


def test_active_stop_uses_stopping_boundary_then_terminal() -> None:
    running = _running_epoch()

    requested, _ = request_operational_paper_session_run_epoch_command(
        running,
        command_id=UUID(
            "18000000-0000-4000-8000-000000000001"
        ),
        intent=_command_intent(
            running,
            OperationalPaperSessionRunCommandType.STOP,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=3),
        idempotency_key="run:stop:active",
    )

    assert (
        requested.desired_state
        is OperationalPaperSessionRunDesiredState.STOPPED
    )
    assert (
        requested.observed_state
        is OperationalPaperSessionRunObservedState.RUNNING
    )

    stopping = mark_operational_paper_session_run_epoch_stopping(
        requested,
        worker_id=WORKER_1,
        fencing_token=1,
        observed_at=NOW + timedelta(seconds=4),
    )

    assert (
        stopping.observed_state
        is OperationalPaperSessionRunObservedState.STOPPING
    )
    assert stopping.worker_claim is not None

    stopped = settle_operational_paper_session_run_epoch_stopped(
        stopping,
        worker_id=WORKER_1,
        fencing_token=1,
        observed_at=NOW + timedelta(seconds=5),
    )

    assert (
        stopped.observed_state
        is OperationalPaperSessionRunObservedState.STOPPED
    )
    assert (
        stopped.desired_state
        is OperationalPaperSessionRunDesiredState.STOPPED
    )
    assert stopped.worker_claim is None
    assert stopped.failure is None
    assert stopped.fencing_token == 1
    assert stopped.terminal_at == NOW + timedelta(seconds=5)
    assert operational_paper_session_run_epoch_is_terminal(stopped)


def test_stopping_crash_recovers_same_epoch_with_higher_fence() -> None:
    running = _running_epoch(lease_seconds=10)

    requested, _ = request_operational_paper_session_run_epoch_command(
        running,
        command_id=UUID(
            "18000000-0000-4000-8000-000000000002"
        ),
        intent=_command_intent(
            running,
            OperationalPaperSessionRunCommandType.STOP,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=3),
        idempotency_key="run:stop:crash",
    )

    stopping = mark_operational_paper_session_run_epoch_stopping(
        requested,
        worker_id=WORKER_1,
        fencing_token=1,
        observed_at=NOW + timedelta(seconds=4),
    )

    original_epoch_id = stopping.epoch_id
    original_checksum = stopping.epoch_checksum

    recovered = recover_operational_paper_session_run_epoch(
        stopping,
        worker_id=WORKER_2,
        recovered_at=NOW + timedelta(seconds=11),
        lease_expires_at=NOW + timedelta(seconds=71),
    )

    assert recovered.epoch_id == original_epoch_id
    assert recovered.epoch_checksum == original_checksum
    assert recovered.fencing_token == 2
    assert (
        recovered.observed_state
        is OperationalPaperSessionRunObservedState.RECOVERING
    )
    assert (
        recovered.desired_state
        is OperationalPaperSessionRunDesiredState.STOPPED
    )

    stopping_again = mark_operational_paper_session_run_epoch_stopping(
        recovered,
        worker_id=WORKER_2,
        fencing_token=2,
        observed_at=NOW + timedelta(seconds=12),
    )

    stopped = settle_operational_paper_session_run_epoch_stopped(
        stopping_again,
        worker_id=WORKER_2,
        fencing_token=2,
        observed_at=NOW + timedelta(seconds=13),
    )

    assert stopped.epoch_id == original_epoch_id
    assert stopped.fencing_token == 2
    assert (
        stopped.observed_state
        is OperationalPaperSessionRunObservedState.STOPPED
    )


def test_pause_requested_crash_recovers_and_settles_paused() -> None:
    running = _running_epoch(lease_seconds=10)

    requested, _ = request_operational_paper_session_run_epoch_command(
        running,
        command_id=UUID(
            "16000000-0000-4000-8000-000000000003"
        ),
        intent=_command_intent(
            running,
            OperationalPaperSessionRunCommandType.PAUSE,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=3),
        idempotency_key="run:pause:crash",
    )

    recovered = recover_operational_paper_session_run_epoch(
        requested,
        worker_id=WORKER_2,
        recovered_at=NOW + timedelta(seconds=11),
        lease_expires_at=NOW + timedelta(seconds=71),
    )

    assert (
        recovered.desired_state
        is OperationalPaperSessionRunDesiredState.PAUSED
    )
    assert (
        recovered.observed_state
        is OperationalPaperSessionRunObservedState.RECOVERING
    )
    assert recovered.fencing_token == 2

    paused = settle_operational_paper_session_run_epoch_paused(
        recovered,
        worker_id=WORKER_2,
        fencing_token=2,
        observed_at=NOW + timedelta(seconds=12),
    )

    assert (
        paused.observed_state
        is OperationalPaperSessionRunObservedState.PAUSED
    )
    assert paused.worker_claim is None
    assert paused.fencing_token == 2


def test_claimed_failure_is_terminal_and_sanitized() -> None:
    running = _running_epoch()

    failed = fail_claimed_operational_paper_session_run_epoch(
        running,
        worker_id=WORKER_1,
        fencing_token=1,
        code=OperationalPaperSessionRunFailureCode.AUTHORITY_LOST,
        failed_at=NOW + timedelta(seconds=3),
    )

    assert (
        failed.observed_state
        is OperationalPaperSessionRunObservedState.FAILED
    )
    assert failed.worker_claim is None
    assert failed.failure is not None
    assert (
        failed.failure.code
        is OperationalPaperSessionRunFailureCode.AUTHORITY_LOST
    )
    assert failed.failure.failed_at == NOW + timedelta(seconds=3)
    assert failed.terminal_at == NOW + timedelta(seconds=3)
    assert operational_paper_session_run_epoch_is_terminal(failed)


def test_stale_worker_cannot_terminalize_after_recovery() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=10),
    )

    recovered = recover_operational_paper_session_run_epoch(
        claimed,
        worker_id=WORKER_2,
        recovered_at=NOW + timedelta(seconds=11),
        lease_expires_at=NOW + timedelta(seconds=71),
    )

    with pytest.raises(OperationalPaperSessionRunLeaseError):
        fail_claimed_operational_paper_session_run_epoch(
            recovered,
            worker_id=WORKER_1,
            fencing_token=1,
            code=OperationalPaperSessionRunFailureCode.LEASE_LOST,
            failed_at=NOW + timedelta(seconds=12),
        )


@pytest.mark.parametrize(
    "code",
    [
        OperationalPaperSessionRunFailureCode.RAW_NOT_READY,
        OperationalPaperSessionRunFailureCode.CONFIG_UNAVAILABLE,
    ],
)
def test_unclaimed_failure_terminalizes_before_worker_claim(
    code: OperationalPaperSessionRunFailureCode,
) -> None:
    epoch, _ = _started_epoch()

    failed = fail_unclaimed_operational_paper_session_run_epoch(
        epoch,
        code=code,
        failed_at=NOW + timedelta(seconds=1),
    )

    assert (
        failed.observed_state
        is OperationalPaperSessionRunObservedState.FAILED
    )
    assert failed.worker_claim is None
    assert failed.fencing_token == 0
    assert failed.failure is not None
    assert failed.failure.code is code
    assert failed.terminal_at == NOW + timedelta(seconds=1)
    assert operational_paper_session_run_epoch_is_terminal(failed)


def test_terminal_unclaimed_settlement_is_stable_noop() -> None:
    epoch, _ = _started_epoch()

    requested, _ = request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=UUID(
            "18000000-0000-4000-8000-000000000003"
        ),
        intent=_command_intent(
            epoch,
            OperationalPaperSessionRunCommandType.STOP,
        ),
        actor_id=ACTOR_ID,
        requested_at=NOW + timedelta(seconds=1),
        idempotency_key="run:stop:terminal-noop",
    )

    stopped = settle_unclaimed_operational_paper_session_run_epoch(
        requested,
        observed_at=NOW + timedelta(seconds=2),
    )

    replay = settle_unclaimed_operational_paper_session_run_epoch(
        stopped,
        observed_at=NOW + timedelta(seconds=3),
    )

    assert replay == stopped
    assert replay.record_version == stopped.record_version
    assert replay.terminal_at == stopped.terminal_at


def test_epoch_specification_field_set_is_exact_and_runtime_free() -> None:
    names = {
        field.name
        for field in fields(_specification())
    }

    assert names == {
        "schema_version",
        "run_contract_version",
        "activation_id",
        "activation_checksum",
        "materialization_id",
        "materialization_checksum",
        "authorization_binding",
        "profile_binding",
        "mandate_binding",
        "simulation_id",
        "session_id",
        "config_checksum",
    }

    forbidden = {
        "epoch_id",
        "epoch_checksum",
        "desired_state",
        "observed_state",
        "record_version",
        "fencing_token",
        "worker_id",
        "worker_claim",
        "claimed_at",
        "heartbeat_at",
        "lease_expires_at",
        "failure",
        "terminal_at",
        "raw_data",
        "filesystem_state",
        "plugin_availability",
        "current_authorization_state",
        "current_profile_state",
        "current_mandate_state",
        "current_simulation_state",
        "authorized_capital",
    }

    assert forbidden.isdisjoint(names)


def test_epoch_aggregate_field_set_is_exact() -> None:
    epoch, _ = _started_epoch()

    assert {
        field.name
        for field in fields(epoch)
    } == {
        "epoch_id",
        "schema_version",
        "run_contract_version",
        "desired_state",
        "observed_state",
        "record_version",
        "fencing_token",
        "activation_id",
        "activation_checksum",
        "materialization_id",
        "materialization_checksum",
        "authorization_binding",
        "profile_binding",
        "mandate_binding",
        "simulation_id",
        "session_id",
        "config_checksum",
        "epoch_checksum",
        "start_requested_by",
        "start_requested_at",
        "start_idempotency_key",
        "start_intent_fingerprint",
        "worker_claim",
        "failure",
        "terminal_at",
    }


def test_worker_claim_and_failure_payloads_are_closed() -> None:
    epoch, _ = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    assert claimed.worker_claim is not None

    assert {
        field.name
        for field in fields(claimed.worker_claim)
    } == {
        "epoch_id",
        "worker_id",
        "fencing_token",
        "claimed_at",
        "heartbeat_at",
        "lease_expires_at",
    }

    failed_epoch, _ = _started_epoch()

    failed = fail_unclaimed_operational_paper_session_run_epoch(
        failed_epoch,
        code=OperationalPaperSessionRunFailureCode.RAW_NOT_READY,
        failed_at=NOW + timedelta(seconds=1),
    )

    assert failed.failure is not None

    assert {
        field.name
        for field in fields(failed.failure)
    } == {
        "code",
        "failed_at",
    }

    assert not hasattr(failed.failure, "message")
    assert not hasattr(failed.failure, "detail")
    assert not hasattr(failed.failure, "exception")
    assert not hasattr(failed.failure, "traceback")


def test_epoch_checksum_excludes_generated_and_runtime_metadata() -> None:
    first, _ = _started_epoch()

    second, _ = start_operational_paper_session_run_epoch(
        epoch_id=UUID(
            "10000000-0000-4000-8000-000000000099"
        ),
        command_id=UUID(
            "11000000-0000-4000-8000-000000000099"
        ),
        specification=_specification(),
        start_intent=_start_intent(),
        requested_by=UUID(
            "70000000-0000-4000-8000-000000000099"
        ),
        requested_at=NOW + timedelta(seconds=10),
        idempotency_key="run:start:metadata-independent",
    )

    assert first.epoch_id != second.epoch_id
    assert first.start_requested_by != second.start_requested_by
    assert first.start_requested_at != second.start_requested_at
    assert first.start_idempotency_key != second.start_idempotency_key

    assert first.epoch_checksum == second.epoch_checksum
    assert (
        first.activation_checksum
        == second.activation_checksum
    )


def test_corrupted_frozen_specification_is_revalidated_by_checksum_helper() -> None:
    specification = _specification()

    object.__setattr__(
        specification,
        "session_id",
        "BAD",
    )

    with pytest.raises(
        InvalidOperationalPaperSessionRunSpecificationError
    ):
        operational_paper_session_run_epoch_specification_checksum(
            specification
        )


def test_corrupted_frozen_epoch_is_revalidated_by_public_helper() -> None:
    epoch, _ = _started_epoch()

    object.__setattr__(
        epoch,
        "observed_state",
        OperationalPaperSessionRunObservedState.STOPPED,
    )

    with pytest.raises(
        InvalidOperationalPaperSessionRunSpecificationError
    ):
        operational_paper_session_run_epoch_is_terminal(
            epoch
        )


def test_pure_domain_contracts_exclude_financial_process_and_secret_fields() -> None:
    epoch, command = _started_epoch()

    claimed = claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=WORKER_1,
        claimed_at=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=61),
    )

    assert claimed.worker_claim is not None

    failed_epoch, _ = _started_epoch()

    failed = fail_unclaimed_operational_paper_session_run_epoch(
        failed_epoch,
        code=OperationalPaperSessionRunFailureCode.INTERNAL_ERROR,
        failed_at=NOW + timedelta(seconds=1),
    )

    assert failed.failure is not None

    contracts = (
        _specification(),
        _start_intent(),
        command,
        epoch,
        claimed.worker_claim,
        failed.failure,
    )

    forbidden_names = {
        "amount",
        "capital",
        "authorized_capital",
        "available_balance",
        "cash_balance",
        "quote_balance",
        "filesystem_path",
        "data_dir",
        "adt_data_dir",
        "hostname",
        "pid",
        "process_id",
        "database_url",
        "password",
        "credentials",
        "api_key",
        "secret",
        "raw_exception",
        "sql",
    }

    for contract in contracts:
        contract_fields = tuple(fields(contract))
        names = {
            field.name.lower()
            for field in contract_fields
        }

        assert forbidden_names.isdisjoint(names)

        for field in contract_fields:
            assert "float" not in str(field.type).lower()
