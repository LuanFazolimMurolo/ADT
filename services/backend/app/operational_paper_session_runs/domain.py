"""Pure operational paper-session run-control domain contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.backtesting.serialization import canonical_json_bytes
from app.operational_paper_session_activations import (
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationSpecification,
    OperationalPaperSessionActivationState,
    validate_operational_paper_session_activation_specification_checksum,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationProfileBinding,
)
from app.operational_paper_session_runs.errors import (
    InvalidOperationalPaperSessionRunSpecificationError,
    OperationalPaperSessionRunBoundsExceededError,
    OperationalPaperSessionRunChecksumMismatchError,
    OperationalPaperSessionRunCommandConflictError,
    OperationalPaperSessionRunLeaseError,
    OperationalPaperSessionRunStateTransitionConflictError,
)

OPERATIONAL_PAPER_SESSION_RUN_SCHEMA_VERSION: Final = 1
OPERATIONAL_PAPER_SESSION_RUN_CONTRACT_VERSION: Final = 1
OPERATIONAL_PAPER_SESSION_RUN_START_CONTRACT_VERSION: Final = 1
OPERATIONAL_PAPER_SESSION_RUN_COMMAND_CONTRACT_VERSION: Final = 1

MAX_OPERATIONAL_PAPER_SESSION_RUN_IDEMPOTENCY_KEY_LENGTH: Final = 128

_POSTGRESQL_BIGINT_MAX: Final = (1 << 63) - 1
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OperationalPaperSessionRunDesiredState(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class OperationalPaperSessionRunObservedState(StrEnum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class OperationalPaperSessionRunCommandType(StrEnum):
    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"


class OperationalPaperSessionRunFailureCode(StrEnum):
    AUTHORITY_LOST = "AUTHORITY_LOST"
    ACTIVATION_REVOKED = "ACTIVATION_REVOKED"
    CONFIG_UNAVAILABLE = "CONFIG_UNAVAILABLE"
    CONFIG_IDENTITY_CONFLICT = "CONFIG_IDENTITY_CONFLICT"
    PLUGIN_UNAVAILABLE = "PLUGIN_UNAVAILABLE"
    RAW_NOT_READY = "RAW_NOT_READY"
    LOCAL_RUNNER_BUSY = "LOCAL_RUNNER_BUSY"
    LOCAL_STATE_INVALID = "LOCAL_STATE_INVALID"
    LEASE_LOST = "LEASE_LOST"
    INTERNAL_ERROR = "INTERNAL_ERROR"


TERMINAL_OPERATIONAL_PAPER_SESSION_RUN_STATES: Final = frozenset(
    {
        OperationalPaperSessionRunObservedState.STOPPED,
        OperationalPaperSessionRunObservedState.FAILED,
    }
)


_ALLOWED_OBSERVED_TRANSITIONS: Final = {
    OperationalPaperSessionRunObservedState.PENDING: frozenset(
        {
            OperationalPaperSessionRunObservedState.STARTING,
            OperationalPaperSessionRunObservedState.PAUSED,
            OperationalPaperSessionRunObservedState.STOPPED,
            OperationalPaperSessionRunObservedState.FAILED,
        }
    ),
    OperationalPaperSessionRunObservedState.STARTING: frozenset(
        {
            OperationalPaperSessionRunObservedState.RUNNING,
            OperationalPaperSessionRunObservedState.PAUSED,
            OperationalPaperSessionRunObservedState.RECOVERING,
            OperationalPaperSessionRunObservedState.STOPPING,
            OperationalPaperSessionRunObservedState.FAILED,
        }
    ),
    OperationalPaperSessionRunObservedState.RUNNING: frozenset(
        {
            OperationalPaperSessionRunObservedState.PAUSED,
            OperationalPaperSessionRunObservedState.RECOVERING,
            OperationalPaperSessionRunObservedState.STOPPING,
            OperationalPaperSessionRunObservedState.FAILED,
        }
    ),
    OperationalPaperSessionRunObservedState.PAUSED: frozenset(
        {
            OperationalPaperSessionRunObservedState.STARTING,
            OperationalPaperSessionRunObservedState.STOPPED,
            OperationalPaperSessionRunObservedState.FAILED,
        }
    ),
    OperationalPaperSessionRunObservedState.RECOVERING: frozenset(
        {
            OperationalPaperSessionRunObservedState.STARTING,
            OperationalPaperSessionRunObservedState.PAUSED,
            OperationalPaperSessionRunObservedState.STOPPING,
            OperationalPaperSessionRunObservedState.STOPPED,
            OperationalPaperSessionRunObservedState.FAILED,
        }
    ),
    OperationalPaperSessionRunObservedState.STOPPING: frozenset(
        {
            OperationalPaperSessionRunObservedState.RECOVERING,
            OperationalPaperSessionRunObservedState.STOPPED,
            OperationalPaperSessionRunObservedState.FAILED,
        }
    ),
    OperationalPaperSessionRunObservedState.STOPPED: frozenset(),
    OperationalPaperSessionRunObservedState.FAILED: frozenset(),
}


def is_operational_paper_session_run_transition_allowed(
    current: OperationalPaperSessionRunObservedState,
    target: OperationalPaperSessionRunObservedState,
) -> bool:
    if not isinstance(current, OperationalPaperSessionRunObservedState):
        return False
    if not isinstance(target, OperationalPaperSessionRunObservedState):
        return False
    return target in _ALLOWED_OBSERVED_TRANSITIONS[current]


def require_operational_paper_session_run_transition(
    current: OperationalPaperSessionRunObservedState,
    target: OperationalPaperSessionRunObservedState,
) -> None:
    if not is_operational_paper_session_run_transition_allowed(current, target):
        raise OperationalPaperSessionRunStateTransitionConflictError()


def operational_paper_session_run_command_target(
    command_type: OperationalPaperSessionRunCommandType,
) -> OperationalPaperSessionRunDesiredState:
    if command_type is OperationalPaperSessionRunCommandType.START:
        return OperationalPaperSessionRunDesiredState.RUNNING
    if command_type is OperationalPaperSessionRunCommandType.PAUSE:
        return OperationalPaperSessionRunDesiredState.PAUSED
    if command_type is OperationalPaperSessionRunCommandType.RESUME:
        return OperationalPaperSessionRunDesiredState.RUNNING
    if command_type is OperationalPaperSessionRunCommandType.STOP:
        return OperationalPaperSessionRunDesiredState.STOPPED
    raise OperationalPaperSessionRunCommandConflictError()


def validate_operational_paper_session_run_idempotency_key(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidOperationalPaperSessionRunSpecificationError()
    if not (1 <= len(value) <= MAX_OPERATIONAL_PAPER_SESSION_RUN_IDEMPOTENCY_KEY_LENGTH):
        raise OperationalPaperSessionRunBoundsExceededError()
    if _SAFE_TOKEN.fullmatch(value) is None:
        raise InvalidOperationalPaperSessionRunSpecificationError()
    return value


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunEpochSpecification:
    schema_version: int
    run_contract_version: int
    activation_id: UUID
    activation_checksum: str
    materialization_id: UUID
    materialization_checksum: str
    authorization_binding: OperationalPaperSessionMaterializationAuthorizationBinding
    profile_binding: OperationalPaperSessionMaterializationProfileBinding
    mandate_binding: OperationalPaperSessionMaterializationMandateBinding
    simulation_id: UUID
    session_id: str
    config_checksum: str

    def __post_init__(self) -> None:
        try:
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_PAPER_SESSION_RUN_SCHEMA_VERSION
            ):
                raise ValueError
            if (
                type(self.run_contract_version) is not int
                or self.run_contract_version != OPERATIONAL_PAPER_SESSION_RUN_CONTRACT_VERSION
            ):
                raise ValueError

            activation_id = _require_uuid(self.activation_id)
            activation_checksum = _require_sha256(self.activation_checksum)
            materialization_id = _require_uuid(self.materialization_id)
            materialization_checksum = _require_sha256(self.materialization_checksum)

            authorization_binding = _revalidate_authorization_binding(self.authorization_binding)
            profile_binding = _revalidate_profile_binding(self.profile_binding)
            mandate_binding = _revalidate_mandate_binding(self.mandate_binding)

            simulation_id = _require_uuid(self.simulation_id)
            session_id = _require_sha256(self.session_id)
            config_checksum = _require_sha256(self.config_checksum)
        except OperationalPaperSessionRunBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionRunSpecificationError() from None

        object.__setattr__(self, "activation_id", activation_id)
        object.__setattr__(self, "activation_checksum", activation_checksum)
        object.__setattr__(self, "materialization_id", materialization_id)
        object.__setattr__(self, "materialization_checksum", materialization_checksum)
        object.__setattr__(self, "authorization_binding", authorization_binding)
        object.__setattr__(self, "profile_binding", profile_binding)
        object.__setattr__(self, "mandate_binding", mandate_binding)
        object.__setattr__(self, "simulation_id", simulation_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "config_checksum", config_checksum)


def operational_paper_session_run_epoch_specification_payload(
    specification: OperationalPaperSessionRunEpochSpecification,
) -> dict[str, object]:
    canonical = _revalidate_epoch_specification(specification)

    return {
        "schema_version": canonical.schema_version,
        "run_contract_version": canonical.run_contract_version,
        "activation_id": str(canonical.activation_id),
        "activation_checksum": canonical.activation_checksum,
        "materialization_id": str(canonical.materialization_id),
        "materialization_checksum": canonical.materialization_checksum,
        "authorization_id": str(canonical.authorization_binding.authorization_id),
        "authorization_checksum": (canonical.authorization_binding.authorization_checksum),
        "profile_id": str(canonical.profile_binding.profile_id),
        "profile_approved_revision": canonical.profile_binding.approved_revision,
        "profile_specification_checksum": (canonical.profile_binding.specification_checksum),
        "mandate_id": str(canonical.mandate_binding.mandate_id),
        "mandate_approved_revision": canonical.mandate_binding.approved_revision,
        "mandate_specification_checksum": (canonical.mandate_binding.specification_checksum),
        "simulation_id": str(canonical.simulation_id),
        "session_id": canonical.session_id,
        "config_checksum": canonical.config_checksum,
    }


def operational_paper_session_run_epoch_specification_bytes(
    specification: OperationalPaperSessionRunEpochSpecification,
) -> bytes:
    return canonical_json_bytes(
        operational_paper_session_run_epoch_specification_payload(specification)
    )


def operational_paper_session_run_epoch_specification_checksum(
    specification: OperationalPaperSessionRunEpochSpecification,
) -> str:
    return hashlib.sha256(
        operational_paper_session_run_epoch_specification_bytes(specification)
    ).hexdigest()


def validate_operational_paper_session_run_epoch_specification_checksum(
    specification: OperationalPaperSessionRunEpochSpecification,
    expected_checksum: object,
) -> OperationalPaperSessionRunEpochSpecification:
    canonical = _revalidate_epoch_specification(specification)

    try:
        checksum = _require_sha256(expected_checksum)
    except Exception:
        raise InvalidOperationalPaperSessionRunSpecificationError() from None

    if operational_paper_session_run_epoch_specification_checksum(canonical) != checksum:
        raise OperationalPaperSessionRunChecksumMismatchError()

    return canonical


def operational_paper_session_run_epoch_specifications_equal(
    left: OperationalPaperSessionRunEpochSpecification,
    right: OperationalPaperSessionRunEpochSpecification,
) -> bool:
    return operational_paper_session_run_epoch_specification_bytes(
        left
    ) == operational_paper_session_run_epoch_specification_bytes(right)


def build_operational_paper_session_run_epoch_specification(
    activation: OperationalPaperSessionActivation,
) -> OperationalPaperSessionRunEpochSpecification:
    try:
        if not isinstance(activation, OperationalPaperSessionActivation):
            raise ValueError

        if activation.state is not OperationalPaperSessionActivationState.AUTHORIZED:
            raise ValueError

        activation_specification = OperationalPaperSessionActivationSpecification(
            schema_version=activation.schema_version,
            activation_contract_version=activation.activation_contract_version,
            materialization_id=activation.materialization_id,
            materialization_checksum=activation.materialization_checksum,
            authorization_binding=activation.authorization_binding,
            profile_binding=activation.profile_binding,
            mandate_binding=activation.mandate_binding,
            simulation_id=activation.simulation_id,
            session_id=activation.session_id,
            config_checksum=activation.config_checksum,
        )

        activation_specification = (
            validate_operational_paper_session_activation_specification_checksum(
                activation_specification,
                activation.activation_checksum,
            )
        )
    except Exception:
        raise InvalidOperationalPaperSessionRunSpecificationError() from None

    return OperationalPaperSessionRunEpochSpecification(
        schema_version=OPERATIONAL_PAPER_SESSION_RUN_SCHEMA_VERSION,
        run_contract_version=OPERATIONAL_PAPER_SESSION_RUN_CONTRACT_VERSION,
        activation_id=activation.activation_id,
        activation_checksum=activation.activation_checksum,
        materialization_id=activation_specification.materialization_id,
        materialization_checksum=activation_specification.materialization_checksum,
        authorization_binding=activation_specification.authorization_binding,
        profile_binding=activation_specification.profile_binding,
        mandate_binding=activation_specification.mandate_binding,
        simulation_id=activation_specification.simulation_id,
        session_id=activation_specification.session_id,
        config_checksum=activation_specification.config_checksum,
    )


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunEpochStartIntent:
    activation_id: UUID
    activation_checksum: str

    def __post_init__(self) -> None:
        try:
            activation_id = _require_uuid(self.activation_id)
            activation_checksum = _require_sha256(self.activation_checksum)
        except Exception:
            raise InvalidOperationalPaperSessionRunSpecificationError() from None

        object.__setattr__(self, "activation_id", activation_id)
        object.__setattr__(self, "activation_checksum", activation_checksum)


def operational_paper_session_run_epoch_start_intent_fingerprint(
    intent: OperationalPaperSessionRunEpochStartIntent,
) -> str:
    canonical = _revalidate_start_intent(intent)

    payload = {
        "contract_version": OPERATIONAL_PAPER_SESSION_RUN_START_CONTRACT_VERSION,
        "activation_id": str(canonical.activation_id),
        "activation_checksum": canonical.activation_checksum,
    }

    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunEpochCommandIntent:
    epoch_id: UUID
    epoch_checksum: str
    command_type: OperationalPaperSessionRunCommandType
    expected_record_version: int

    def __post_init__(self) -> None:
        try:
            epoch_id = _require_uuid(self.epoch_id)
            epoch_checksum = _require_sha256(self.epoch_checksum)

            if (
                not isinstance(
                    self.command_type,
                    OperationalPaperSessionRunCommandType,
                )
                or self.command_type is OperationalPaperSessionRunCommandType.START
            ):
                raise ValueError

            expected_record_version = _require_positive_bigint(self.expected_record_version)
        except OperationalPaperSessionRunBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionRunSpecificationError() from None

        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(self, "epoch_checksum", epoch_checksum)
        object.__setattr__(
            self,
            "expected_record_version",
            expected_record_version,
        )


def operational_paper_session_run_epoch_command_intent_fingerprint(
    intent: OperationalPaperSessionRunEpochCommandIntent,
) -> str:
    canonical = _revalidate_command_intent(intent)

    payload = {
        "contract_version": OPERATIONAL_PAPER_SESSION_RUN_COMMAND_CONTRACT_VERSION,
        "epoch_id": str(canonical.epoch_id),
        "epoch_checksum": canonical.epoch_checksum,
        "command_type": canonical.command_type.value,
        "expected_record_version": canonical.expected_record_version,
    }

    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunWorkerClaim:
    epoch_id: UUID
    worker_id: UUID = field(repr=False)
    fencing_token: int
    claimed_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        try:
            epoch_id = _require_uuid(self.epoch_id)
            worker_id = _require_uuid(self.worker_id)
            fencing_token = _require_positive_bigint(self.fencing_token)

            claimed_at = _require_utc(self.claimed_at)
            heartbeat_at = _require_utc(self.heartbeat_at)
            lease_expires_at = _require_utc(self.lease_expires_at)

            if not claimed_at <= heartbeat_at < lease_expires_at:
                raise ValueError
        except OperationalPaperSessionRunBoundsExceededError:
            raise
        except Exception:
            raise OperationalPaperSessionRunLeaseError() from None

        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(self, "worker_id", worker_id)
        object.__setattr__(self, "fencing_token", fencing_token)
        object.__setattr__(self, "claimed_at", claimed_at)
        object.__setattr__(self, "heartbeat_at", heartbeat_at)
        object.__setattr__(self, "lease_expires_at", lease_expires_at)

    def belongs_to(
        self,
        worker_id: UUID,
        fencing_token: int,
    ) -> bool:
        try:
            canonical_worker_id = _require_uuid(worker_id)
            canonical_fencing_token = _require_positive_bigint(fencing_token)
        except Exception:
            return False

        return (
            self.worker_id == canonical_worker_id and self.fencing_token == canonical_fencing_token
        )

    def is_active(self, now: datetime) -> bool:
        try:
            current = _require_utc(now)
        except Exception:
            raise OperationalPaperSessionRunLeaseError() from None

        if current < self.heartbeat_at:
            raise OperationalPaperSessionRunLeaseError()

        return current < self.lease_expires_at

    def is_expired(self, now: datetime) -> bool:
        return not self.is_active(now)


def _revalidate_authorization_binding(
    value: object,
) -> OperationalPaperSessionMaterializationAuthorizationBinding:
    if not isinstance(
        value,
        OperationalPaperSessionMaterializationAuthorizationBinding,
    ):
        raise ValueError

    return OperationalPaperSessionMaterializationAuthorizationBinding(
        authorization_id=value.authorization_id,
        authorization_checksum=value.authorization_checksum,
    )


def _revalidate_profile_binding(
    value: object,
) -> OperationalPaperSessionMaterializationProfileBinding:
    if not isinstance(
        value,
        OperationalPaperSessionMaterializationProfileBinding,
    ):
        raise ValueError

    return OperationalPaperSessionMaterializationProfileBinding(
        profile_id=value.profile_id,
        approved_revision=value.approved_revision,
        specification_checksum=value.specification_checksum,
    )


def _revalidate_mandate_binding(
    value: object,
) -> OperationalPaperSessionMaterializationMandateBinding:
    if not isinstance(
        value,
        OperationalPaperSessionMaterializationMandateBinding,
    ):
        raise ValueError

    return OperationalPaperSessionMaterializationMandateBinding(
        mandate_id=value.mandate_id,
        approved_revision=value.approved_revision,
        specification_checksum=value.specification_checksum,
    )


def _revalidate_epoch_specification(
    value: object,
) -> OperationalPaperSessionRunEpochSpecification:
    if not isinstance(value, OperationalPaperSessionRunEpochSpecification):
        raise InvalidOperationalPaperSessionRunSpecificationError()

    return OperationalPaperSessionRunEpochSpecification(
        schema_version=value.schema_version,
        run_contract_version=value.run_contract_version,
        activation_id=value.activation_id,
        activation_checksum=value.activation_checksum,
        materialization_id=value.materialization_id,
        materialization_checksum=value.materialization_checksum,
        authorization_binding=value.authorization_binding,
        profile_binding=value.profile_binding,
        mandate_binding=value.mandate_binding,
        simulation_id=value.simulation_id,
        session_id=value.session_id,
        config_checksum=value.config_checksum,
    )


def _revalidate_start_intent(
    value: object,
) -> OperationalPaperSessionRunEpochStartIntent:
    if not isinstance(value, OperationalPaperSessionRunEpochStartIntent):
        raise InvalidOperationalPaperSessionRunSpecificationError()

    return OperationalPaperSessionRunEpochStartIntent(
        activation_id=value.activation_id,
        activation_checksum=value.activation_checksum,
    )


def _revalidate_command_intent(
    value: object,
) -> OperationalPaperSessionRunEpochCommandIntent:
    if not isinstance(value, OperationalPaperSessionRunEpochCommandIntent):
        raise InvalidOperationalPaperSessionRunSpecificationError()

    return OperationalPaperSessionRunEpochCommandIntent(
        epoch_id=value.epoch_id,
        epoch_checksum=value.epoch_checksum,
        command_type=value.command_type,
        expected_record_version=value.expected_record_version,
    )


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError
    return value


def _require_positive_bigint(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError
    if value > _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionRunBoundsExceededError()
    return value


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


def _require_utc(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError

    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunFailure:
    code: OperationalPaperSessionRunFailureCode
    failed_at: datetime

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.code, OperationalPaperSessionRunFailureCode):
                raise ValueError
            failed_at = _require_utc(self.failed_at)
        except Exception:
            raise InvalidOperationalPaperSessionRunSpecificationError() from None

        object.__setattr__(self, "failed_at", failed_at)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunEpochCommand:
    command_id: UUID
    command_contract_version: int
    epoch_id: UUID
    epoch_checksum: str
    command_type: OperationalPaperSessionRunCommandType
    desired_state: OperationalPaperSessionRunDesiredState
    expected_record_version: int | None
    resulting_record_version: int
    actor_id: UUID
    requested_at: datetime
    idempotency_key: str = field(repr=False)
    intent_fingerprint: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            command_id = _require_uuid(self.command_id)

            if (
                type(self.command_contract_version) is not int
                or self.command_contract_version
                != OPERATIONAL_PAPER_SESSION_RUN_COMMAND_CONTRACT_VERSION
            ):
                raise ValueError

            epoch_id = _require_uuid(self.epoch_id)
            epoch_checksum = _require_sha256(self.epoch_checksum)

            if not isinstance(
                self.command_type,
                OperationalPaperSessionRunCommandType,
            ):
                raise ValueError

            if not isinstance(
                self.desired_state,
                OperationalPaperSessionRunDesiredState,
            ):
                raise ValueError

            if self.desired_state is not operational_paper_session_run_command_target(
                self.command_type
            ):
                raise ValueError

            resulting_record_version = _require_positive_bigint(self.resulting_record_version)

            expected_record_version: int | None

            if self.command_type is OperationalPaperSessionRunCommandType.START:
                if self.expected_record_version is not None:
                    raise ValueError
                if resulting_record_version != 1:
                    raise ValueError
                expected_record_version = None
            else:
                expected_record_version = _require_positive_bigint(self.expected_record_version)
                if resulting_record_version != expected_record_version + 1:
                    raise ValueError

            actor_id = _require_uuid(self.actor_id)
            requested_at = _require_utc(self.requested_at)

            idempotency_key = validate_operational_paper_session_run_idempotency_key(
                self.idempotency_key
            )
            intent_fingerprint = _require_sha256(self.intent_fingerprint)

            if self.command_type is not OperationalPaperSessionRunCommandType.START:
                if expected_record_version is None:
                    raise ValueError

                expected_fingerprint = (
                    operational_paper_session_run_epoch_command_intent_fingerprint(
                        OperationalPaperSessionRunEpochCommandIntent(
                            epoch_id=epoch_id,
                            epoch_checksum=epoch_checksum,
                            command_type=self.command_type,
                            expected_record_version=expected_record_version,
                        )
                    )
                )

                if intent_fingerprint != expected_fingerprint:
                    raise ValueError
        except OperationalPaperSessionRunBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionRunSpecificationError() from None

        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(self, "epoch_checksum", epoch_checksum)
        object.__setattr__(
            self,
            "expected_record_version",
            expected_record_version,
        )
        object.__setattr__(
            self,
            "resulting_record_version",
            resulting_record_version,
        )
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(
            self,
            "idempotency_key",
            idempotency_key,
        )
        object.__setattr__(
            self,
            "intent_fingerprint",
            intent_fingerprint,
        )


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunEpoch:
    epoch_id: UUID
    schema_version: int
    run_contract_version: int
    desired_state: OperationalPaperSessionRunDesiredState
    observed_state: OperationalPaperSessionRunObservedState
    record_version: int
    fencing_token: int
    activation_id: UUID
    activation_checksum: str
    materialization_id: UUID
    materialization_checksum: str
    authorization_binding: OperationalPaperSessionMaterializationAuthorizationBinding
    profile_binding: OperationalPaperSessionMaterializationProfileBinding
    mandate_binding: OperationalPaperSessionMaterializationMandateBinding
    simulation_id: UUID
    session_id: str
    config_checksum: str
    epoch_checksum: str
    start_requested_by: UUID
    start_requested_at: datetime
    start_idempotency_key: str = field(repr=False)
    start_intent_fingerprint: str = field(repr=False)
    worker_claim: OperationalPaperSessionRunWorkerClaim | None = field(
        default=None,
        repr=False,
    )
    failure: OperationalPaperSessionRunFailure | None = None
    terminal_at: datetime | None = None

    def __post_init__(self) -> None:
        try:
            epoch_id = _require_uuid(self.epoch_id)

            if not isinstance(
                self.desired_state,
                OperationalPaperSessionRunDesiredState,
            ):
                raise ValueError

            if not isinstance(
                self.observed_state,
                OperationalPaperSessionRunObservedState,
            ):
                raise ValueError

            if (
                self.observed_state is OperationalPaperSessionRunObservedState.STOPPING
                and self.desired_state is not OperationalPaperSessionRunDesiredState.STOPPED
            ):
                raise ValueError

            record_version = _require_positive_bigint(self.record_version)
            fencing_token = _require_nonnegative_bigint(self.fencing_token)

            specification = OperationalPaperSessionRunEpochSpecification(
                schema_version=self.schema_version,
                run_contract_version=self.run_contract_version,
                activation_id=self.activation_id,
                activation_checksum=self.activation_checksum,
                materialization_id=self.materialization_id,
                materialization_checksum=self.materialization_checksum,
                authorization_binding=self.authorization_binding,
                profile_binding=self.profile_binding,
                mandate_binding=self.mandate_binding,
                simulation_id=self.simulation_id,
                session_id=self.session_id,
                config_checksum=self.config_checksum,
            )

            specification = validate_operational_paper_session_run_epoch_specification_checksum(
                specification,
                self.epoch_checksum,
            )

            epoch_checksum = _require_sha256(self.epoch_checksum)

            start_requested_by = _require_uuid(self.start_requested_by)
            start_requested_at = _require_utc(self.start_requested_at)

            start_idempotency_key = validate_operational_paper_session_run_idempotency_key(
                self.start_idempotency_key
            )

            start_intent_fingerprint = _require_sha256(self.start_intent_fingerprint)

            expected_start_fingerprint = (
                operational_paper_session_run_epoch_start_intent_fingerprint(
                    OperationalPaperSessionRunEpochStartIntent(
                        activation_id=specification.activation_id,
                        activation_checksum=specification.activation_checksum,
                    )
                )
            )

            if start_intent_fingerprint != expected_start_fingerprint:
                raise ValueError

            worker_claim = (
                None if self.worker_claim is None else _revalidate_worker_claim(self.worker_claim)
            )

            if worker_claim is not None:
                if (
                    worker_claim.epoch_id != epoch_id
                    or worker_claim.fencing_token != fencing_token
                    or worker_claim.claimed_at < start_requested_at
                ):
                    raise OperationalPaperSessionRunLeaseError()

                if self.observed_state not in {
                    OperationalPaperSessionRunObservedState.STARTING,
                    OperationalPaperSessionRunObservedState.RUNNING,
                    OperationalPaperSessionRunObservedState.RECOVERING,
                    OperationalPaperSessionRunObservedState.STOPPING,
                }:
                    raise OperationalPaperSessionRunLeaseError()

            elif self.observed_state in {
                OperationalPaperSessionRunObservedState.STARTING,
                OperationalPaperSessionRunObservedState.RUNNING,
                OperationalPaperSessionRunObservedState.RECOVERING,
                OperationalPaperSessionRunObservedState.STOPPING,
            }:
                raise OperationalPaperSessionRunLeaseError()

            failure = None if self.failure is None else _revalidate_failure(self.failure)

            terminal_at = None if self.terminal_at is None else _require_utc(self.terminal_at)

            if self.observed_state is OperationalPaperSessionRunObservedState.FAILED:
                if (
                    failure is None
                    or terminal_at is None
                    or failure.failed_at != terminal_at
                    or worker_claim is not None
                ):
                    raise ValueError

            elif self.observed_state is OperationalPaperSessionRunObservedState.STOPPED:
                if (
                    self.desired_state is not OperationalPaperSessionRunDesiredState.STOPPED
                    or failure is not None
                    or terminal_at is None
                    or worker_claim is not None
                ):
                    raise ValueError

            elif failure is not None or terminal_at is not None:
                raise ValueError

            if terminal_at is not None and terminal_at < start_requested_at:
                raise ValueError

        except OperationalPaperSessionRunBoundsExceededError:
            raise
        except OperationalPaperSessionRunChecksumMismatchError:
            raise
        except OperationalPaperSessionRunLeaseError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionRunSpecificationError() from None

        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(self, "record_version", record_version)
        object.__setattr__(self, "fencing_token", fencing_token)

        object.__setattr__(
            self,
            "activation_id",
            specification.activation_id,
        )
        object.__setattr__(
            self,
            "activation_checksum",
            specification.activation_checksum,
        )
        object.__setattr__(
            self,
            "materialization_id",
            specification.materialization_id,
        )
        object.__setattr__(
            self,
            "materialization_checksum",
            specification.materialization_checksum,
        )
        object.__setattr__(
            self,
            "authorization_binding",
            specification.authorization_binding,
        )
        object.__setattr__(
            self,
            "profile_binding",
            specification.profile_binding,
        )
        object.__setattr__(
            self,
            "mandate_binding",
            specification.mandate_binding,
        )
        object.__setattr__(
            self,
            "simulation_id",
            specification.simulation_id,
        )
        object.__setattr__(
            self,
            "session_id",
            specification.session_id,
        )
        object.__setattr__(
            self,
            "config_checksum",
            specification.config_checksum,
        )
        object.__setattr__(
            self,
            "epoch_checksum",
            epoch_checksum,
        )
        object.__setattr__(
            self,
            "start_requested_by",
            start_requested_by,
        )
        object.__setattr__(
            self,
            "start_requested_at",
            start_requested_at,
        )
        object.__setattr__(
            self,
            "start_idempotency_key",
            start_idempotency_key,
        )
        object.__setattr__(
            self,
            "start_intent_fingerprint",
            start_intent_fingerprint,
        )
        object.__setattr__(
            self,
            "worker_claim",
            worker_claim,
        )
        object.__setattr__(self, "failure", failure)
        object.__setattr__(self, "terminal_at", terminal_at)


def start_operational_paper_session_run_epoch(
    *,
    epoch_id: UUID,
    command_id: UUID,
    specification: OperationalPaperSessionRunEpochSpecification,
    start_intent: OperationalPaperSessionRunEpochStartIntent,
    requested_by: UUID,
    requested_at: datetime,
    idempotency_key: str,
) -> tuple[
    OperationalPaperSessionRunEpoch,
    OperationalPaperSessionRunEpochCommand,
]:
    canonical = _revalidate_epoch_specification(specification)
    intent = _revalidate_start_intent(start_intent)

    if (
        intent.activation_id != canonical.activation_id
        or intent.activation_checksum != canonical.activation_checksum
    ):
        raise OperationalPaperSessionRunCommandConflictError()

    epoch_id = _require_uuid(epoch_id)
    command_id = _require_uuid(command_id)
    requested_by = _require_uuid(requested_by)
    requested_at = _require_utc(requested_at)

    idempotency_key = validate_operational_paper_session_run_idempotency_key(idempotency_key)

    epoch_checksum = operational_paper_session_run_epoch_specification_checksum(canonical)

    fingerprint = operational_paper_session_run_epoch_start_intent_fingerprint(intent)

    epoch = OperationalPaperSessionRunEpoch(
        epoch_id=epoch_id,
        schema_version=canonical.schema_version,
        run_contract_version=canonical.run_contract_version,
        desired_state=OperationalPaperSessionRunDesiredState.RUNNING,
        observed_state=OperationalPaperSessionRunObservedState.PENDING,
        record_version=1,
        fencing_token=0,
        activation_id=canonical.activation_id,
        activation_checksum=canonical.activation_checksum,
        materialization_id=canonical.materialization_id,
        materialization_checksum=canonical.materialization_checksum,
        authorization_binding=canonical.authorization_binding,
        profile_binding=canonical.profile_binding,
        mandate_binding=canonical.mandate_binding,
        simulation_id=canonical.simulation_id,
        session_id=canonical.session_id,
        config_checksum=canonical.config_checksum,
        epoch_checksum=epoch_checksum,
        start_requested_by=requested_by,
        start_requested_at=requested_at,
        start_idempotency_key=idempotency_key,
        start_intent_fingerprint=fingerprint,
    )

    command = OperationalPaperSessionRunEpochCommand(
        command_id=command_id,
        command_contract_version=(OPERATIONAL_PAPER_SESSION_RUN_COMMAND_CONTRACT_VERSION),
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=OperationalPaperSessionRunCommandType.START,
        desired_state=OperationalPaperSessionRunDesiredState.RUNNING,
        expected_record_version=None,
        resulting_record_version=1,
        actor_id=requested_by,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
        intent_fingerprint=fingerprint,
    )

    return epoch, command


def operational_paper_session_run_epoch_is_terminal(
    epoch: OperationalPaperSessionRunEpoch,
) -> bool:
    canonical = _revalidate_epoch(epoch)
    return canonical.observed_state in (TERMINAL_OPERATIONAL_PAPER_SESSION_RUN_STATES)


def _revalidate_worker_claim(
    value: object,
) -> OperationalPaperSessionRunWorkerClaim:
    if not isinstance(value, OperationalPaperSessionRunWorkerClaim):
        raise OperationalPaperSessionRunLeaseError()

    return OperationalPaperSessionRunWorkerClaim(
        epoch_id=value.epoch_id,
        worker_id=value.worker_id,
        fencing_token=value.fencing_token,
        claimed_at=value.claimed_at,
        heartbeat_at=value.heartbeat_at,
        lease_expires_at=value.lease_expires_at,
    )


def _revalidate_failure(
    value: object,
) -> OperationalPaperSessionRunFailure:
    if not isinstance(value, OperationalPaperSessionRunFailure):
        raise InvalidOperationalPaperSessionRunSpecificationError()

    return OperationalPaperSessionRunFailure(
        code=value.code,
        failed_at=value.failed_at,
    )


def _revalidate_epoch(
    value: object,
) -> OperationalPaperSessionRunEpoch:
    if not isinstance(value, OperationalPaperSessionRunEpoch):
        raise InvalidOperationalPaperSessionRunSpecificationError()

    return replace(value)


def _require_nonnegative_bigint(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError
    if value > _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionRunBoundsExceededError()
    return value


_ALLOWED_DESIRED_COMMANDS: Final = {
    OperationalPaperSessionRunDesiredState.RUNNING: frozenset(
        {
            OperationalPaperSessionRunCommandType.PAUSE,
            OperationalPaperSessionRunCommandType.STOP,
        }
    ),
    OperationalPaperSessionRunDesiredState.PAUSED: frozenset(
        {
            OperationalPaperSessionRunCommandType.RESUME,
            OperationalPaperSessionRunCommandType.STOP,
        }
    ),
    OperationalPaperSessionRunDesiredState.STOPPED: frozenset(),
}


def is_operational_paper_session_run_command_allowed(
    epoch: OperationalPaperSessionRunEpoch,
    command_type: OperationalPaperSessionRunCommandType,
) -> bool:
    try:
        canonical = _revalidate_epoch(epoch)
    except Exception:
        return False

    if not isinstance(
        command_type,
        OperationalPaperSessionRunCommandType,
    ):
        return False

    if command_type is OperationalPaperSessionRunCommandType.START:
        return False

    if operational_paper_session_run_epoch_is_terminal(canonical):
        return False

    return command_type in _ALLOWED_DESIRED_COMMANDS[canonical.desired_state]


def request_operational_paper_session_run_epoch_command(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    command_id: UUID,
    intent: OperationalPaperSessionRunEpochCommandIntent,
    actor_id: UUID,
    requested_at: datetime,
    idempotency_key: str,
) -> tuple[
    OperationalPaperSessionRunEpoch,
    OperationalPaperSessionRunEpochCommand,
]:
    canonical = _revalidate_epoch(epoch)
    command_intent = _revalidate_command_intent(intent)

    if operational_paper_session_run_epoch_is_terminal(canonical):
        raise OperationalPaperSessionRunCommandConflictError()

    if (
        command_intent.epoch_id != canonical.epoch_id
        or command_intent.epoch_checksum != canonical.epoch_checksum
        or command_intent.expected_record_version != canonical.record_version
    ):
        raise OperationalPaperSessionRunCommandConflictError()

    if not is_operational_paper_session_run_command_allowed(
        canonical,
        command_intent.command_type,
    ):
        raise OperationalPaperSessionRunCommandConflictError()

    command_id = _require_uuid(command_id)
    actor_id = _require_uuid(actor_id)
    requested_at = _require_utc(requested_at)

    if requested_at < canonical.start_requested_at:
        raise InvalidOperationalPaperSessionRunSpecificationError()

    idempotency_key = validate_operational_paper_session_run_idempotency_key(idempotency_key)

    desired_state = operational_paper_session_run_command_target(command_intent.command_type)

    resulting_record_version = canonical.record_version + 1

    if resulting_record_version > _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionRunBoundsExceededError()

    fingerprint = operational_paper_session_run_epoch_command_intent_fingerprint(command_intent)

    updated = replace(
        canonical,
        desired_state=desired_state,
        record_version=resulting_record_version,
    )

    command = OperationalPaperSessionRunEpochCommand(
        command_id=command_id,
        command_contract_version=(OPERATIONAL_PAPER_SESSION_RUN_COMMAND_CONTRACT_VERSION),
        epoch_id=canonical.epoch_id,
        epoch_checksum=canonical.epoch_checksum,
        command_type=command_intent.command_type,
        desired_state=desired_state,
        expected_record_version=canonical.record_version,
        resulting_record_version=resulting_record_version,
        actor_id=actor_id,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
        intent_fingerprint=fingerprint,
    )

    return updated, command


def claim_operational_paper_session_run_epoch(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    claimed_at: datetime,
    lease_expires_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if operational_paper_session_run_epoch_is_terminal(canonical):
        raise OperationalPaperSessionRunCommandConflictError()

    if (
        canonical.desired_state is not OperationalPaperSessionRunDesiredState.RUNNING
        or canonical.observed_state
        not in {
            OperationalPaperSessionRunObservedState.PENDING,
            OperationalPaperSessionRunObservedState.PAUSED,
        }
        or canonical.worker_claim is not None
    ):
        raise OperationalPaperSessionRunLeaseError()

    worker_id = _require_uuid(worker_id)
    claimed_at = _require_utc(claimed_at)
    lease_expires_at = _require_utc(lease_expires_at)

    if claimed_at < canonical.start_requested_at or lease_expires_at <= claimed_at:
        raise OperationalPaperSessionRunLeaseError()

    fencing_token = _next_fencing_token(canonical.fencing_token)

    claim = OperationalPaperSessionRunWorkerClaim(
        epoch_id=canonical.epoch_id,
        worker_id=worker_id,
        fencing_token=fencing_token,
        claimed_at=claimed_at,
        heartbeat_at=claimed_at,
        lease_expires_at=lease_expires_at,
    )

    require_operational_paper_session_run_transition(
        canonical.observed_state,
        OperationalPaperSessionRunObservedState.STARTING,
    )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.STARTING,
        record_version=_next_record_version(canonical.record_version),
        fencing_token=fencing_token,
        worker_claim=claim,
    )


def renew_operational_paper_session_run_worker_claim(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    heartbeat_at: datetime,
    lease_expires_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    claim = _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=heartbeat_at,
    )

    heartbeat_at = _require_utc(heartbeat_at)
    lease_expires_at = _require_utc(lease_expires_at)

    if (
        heartbeat_at <= claim.heartbeat_at
        or lease_expires_at <= heartbeat_at
        or lease_expires_at <= claim.lease_expires_at
    ):
        raise OperationalPaperSessionRunLeaseError()

    renewed = OperationalPaperSessionRunWorkerClaim(
        epoch_id=claim.epoch_id,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        claimed_at=claim.claimed_at,
        heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
    )

    return replace(
        canonical,
        record_version=_next_record_version(canonical.record_version),
        worker_claim=renewed,
    )


def recover_operational_paper_session_run_epoch(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    recovered_at: datetime,
    lease_expires_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if operational_paper_session_run_epoch_is_terminal(canonical):
        raise OperationalPaperSessionRunCommandConflictError()

    previous_claim = canonical.worker_claim

    if previous_claim is None or canonical.observed_state not in {
        OperationalPaperSessionRunObservedState.STARTING,
        OperationalPaperSessionRunObservedState.RUNNING,
        OperationalPaperSessionRunObservedState.RECOVERING,
        OperationalPaperSessionRunObservedState.STOPPING,
    }:
        raise OperationalPaperSessionRunLeaseError()

    recovered_at = _require_utc(recovered_at)

    if not previous_claim.is_expired(recovered_at):
        raise OperationalPaperSessionRunLeaseError()

    worker_id = _require_uuid(worker_id)
    lease_expires_at = _require_utc(lease_expires_at)

    if lease_expires_at <= recovered_at:
        raise OperationalPaperSessionRunLeaseError()

    fencing_token = _next_fencing_token(canonical.fencing_token)

    recovered_claim = OperationalPaperSessionRunWorkerClaim(
        epoch_id=canonical.epoch_id,
        worker_id=worker_id,
        fencing_token=fencing_token,
        claimed_at=recovered_at,
        heartbeat_at=recovered_at,
        lease_expires_at=lease_expires_at,
    )

    if canonical.observed_state is not OperationalPaperSessionRunObservedState.RECOVERING:
        require_operational_paper_session_run_transition(
            canonical.observed_state,
            OperationalPaperSessionRunObservedState.RECOVERING,
        )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.RECOVERING,
        record_version=_next_record_version(canonical.record_version),
        fencing_token=fencing_token,
        worker_claim=recovered_claim,
    )


def mark_operational_paper_session_run_epoch_starting(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.observed_state is not OperationalPaperSessionRunObservedState.RECOVERING
        or canonical.desired_state is not OperationalPaperSessionRunDesiredState.RUNNING
    ):
        raise OperationalPaperSessionRunStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_paper_session_run_transition(
        canonical.observed_state,
        OperationalPaperSessionRunObservedState.STARTING,
    )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.STARTING,
        record_version=_next_record_version(canonical.record_version),
    )


def _require_current_active_claim(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    now: datetime,
) -> OperationalPaperSessionRunWorkerClaim:
    claim = epoch.worker_claim

    if claim is None:
        raise OperationalPaperSessionRunLeaseError()

    canonical_worker_id = _require_uuid(worker_id)
    canonical_fencing_token = _require_positive_bigint(fencing_token)
    current = _require_utc(now)

    if not claim.belongs_to(
        canonical_worker_id,
        canonical_fencing_token,
    ) or not claim.is_active(current):
        raise OperationalPaperSessionRunLeaseError()

    return claim


def _next_record_version(current: int) -> int:
    canonical = _require_positive_bigint(current)

    if canonical >= _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionRunBoundsExceededError()

    return canonical + 1


def _next_fencing_token(current: int) -> int:
    canonical = _require_nonnegative_bigint(current)

    if canonical >= _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionRunBoundsExceededError()

    return canonical + 1


def mark_operational_paper_session_run_epoch_running(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.observed_state is not OperationalPaperSessionRunObservedState.STARTING
        or canonical.desired_state is not OperationalPaperSessionRunDesiredState.RUNNING
    ):
        raise OperationalPaperSessionRunStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_paper_session_run_transition(
        canonical.observed_state,
        OperationalPaperSessionRunObservedState.RUNNING,
    )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.RUNNING,
        record_version=_next_record_version(canonical.record_version),
    )


def settle_operational_paper_session_run_epoch_paused(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.desired_state is not OperationalPaperSessionRunDesiredState.PAUSED
        or canonical.observed_state
        not in {
            OperationalPaperSessionRunObservedState.STARTING,
            OperationalPaperSessionRunObservedState.RUNNING,
            OperationalPaperSessionRunObservedState.RECOVERING,
        }
    ):
        raise OperationalPaperSessionRunStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_paper_session_run_transition(
        canonical.observed_state,
        OperationalPaperSessionRunObservedState.PAUSED,
    )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.PAUSED,
        record_version=_next_record_version(canonical.record_version),
        worker_claim=None,
    )


def mark_operational_paper_session_run_epoch_stopping(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.desired_state is not OperationalPaperSessionRunDesiredState.STOPPED
        or canonical.observed_state
        not in {
            OperationalPaperSessionRunObservedState.STARTING,
            OperationalPaperSessionRunObservedState.RUNNING,
            OperationalPaperSessionRunObservedState.RECOVERING,
        }
    ):
        raise OperationalPaperSessionRunStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_paper_session_run_transition(
        canonical.observed_state,
        OperationalPaperSessionRunObservedState.STOPPING,
    )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.STOPPING,
        record_version=_next_record_version(canonical.record_version),
    )


def settle_operational_paper_session_run_epoch_stopped(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.desired_state is not OperationalPaperSessionRunDesiredState.STOPPED
        or canonical.observed_state is not OperationalPaperSessionRunObservedState.STOPPING
    ):
        raise OperationalPaperSessionRunStateTransitionConflictError()

    observed_at = _require_utc(observed_at)

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_paper_session_run_transition(
        canonical.observed_state,
        OperationalPaperSessionRunObservedState.STOPPED,
    )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.STOPPED,
        record_version=_next_record_version(canonical.record_version),
        worker_claim=None,
        terminal_at=observed_at,
    )


def settle_unclaimed_operational_paper_session_run_epoch(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    observed_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if operational_paper_session_run_epoch_is_terminal(canonical):
        return canonical

    if canonical.worker_claim is not None:
        raise OperationalPaperSessionRunLeaseError()

    observed_at = _require_utc(observed_at)

    if observed_at < canonical.start_requested_at:
        raise InvalidOperationalPaperSessionRunSpecificationError()

    if canonical.desired_state is OperationalPaperSessionRunDesiredState.PAUSED:
        if canonical.observed_state is OperationalPaperSessionRunObservedState.PAUSED:
            return canonical

        if canonical.observed_state is not OperationalPaperSessionRunObservedState.PENDING:
            raise OperationalPaperSessionRunStateTransitionConflictError()

        require_operational_paper_session_run_transition(
            canonical.observed_state,
            OperationalPaperSessionRunObservedState.PAUSED,
        )

        return replace(
            canonical,
            observed_state=OperationalPaperSessionRunObservedState.PAUSED,
            record_version=_next_record_version(canonical.record_version),
        )

    if canonical.desired_state is OperationalPaperSessionRunDesiredState.STOPPED:
        if canonical.observed_state not in {
            OperationalPaperSessionRunObservedState.PENDING,
            OperationalPaperSessionRunObservedState.PAUSED,
        }:
            raise OperationalPaperSessionRunStateTransitionConflictError()

        require_operational_paper_session_run_transition(
            canonical.observed_state,
            OperationalPaperSessionRunObservedState.STOPPED,
        )

        return replace(
            canonical,
            observed_state=OperationalPaperSessionRunObservedState.STOPPED,
            record_version=_next_record_version(canonical.record_version),
            terminal_at=observed_at,
        )

    raise OperationalPaperSessionRunStateTransitionConflictError()


def fail_claimed_operational_paper_session_run_epoch(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    code: OperationalPaperSessionRunFailureCode,
    failed_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if operational_paper_session_run_epoch_is_terminal(canonical):
        raise OperationalPaperSessionRunStateTransitionConflictError()

    if canonical.observed_state not in {
        OperationalPaperSessionRunObservedState.STARTING,
        OperationalPaperSessionRunObservedState.RUNNING,
        OperationalPaperSessionRunObservedState.RECOVERING,
        OperationalPaperSessionRunObservedState.STOPPING,
    }:
        raise OperationalPaperSessionRunStateTransitionConflictError()

    if not isinstance(code, OperationalPaperSessionRunFailureCode):
        raise InvalidOperationalPaperSessionRunSpecificationError()

    failed_at = _require_utc(failed_at)

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=failed_at,
    )

    require_operational_paper_session_run_transition(
        canonical.observed_state,
        OperationalPaperSessionRunObservedState.FAILED,
    )

    failure = OperationalPaperSessionRunFailure(
        code=code,
        failed_at=failed_at,
    )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.FAILED,
        record_version=_next_record_version(canonical.record_version),
        worker_claim=None,
        failure=failure,
        terminal_at=failed_at,
    )


def fail_unclaimed_operational_paper_session_run_epoch(
    epoch: OperationalPaperSessionRunEpoch,
    *,
    code: OperationalPaperSessionRunFailureCode,
    failed_at: datetime,
) -> OperationalPaperSessionRunEpoch:
    canonical = _revalidate_epoch(epoch)

    if operational_paper_session_run_epoch_is_terminal(canonical):
        raise OperationalPaperSessionRunStateTransitionConflictError()

    if canonical.worker_claim is not None or canonical.observed_state not in {
        OperationalPaperSessionRunObservedState.PENDING,
        OperationalPaperSessionRunObservedState.PAUSED,
    }:
        raise OperationalPaperSessionRunLeaseError()

    if not isinstance(code, OperationalPaperSessionRunFailureCode):
        raise InvalidOperationalPaperSessionRunSpecificationError()

    failed_at = _require_utc(failed_at)

    if failed_at < canonical.start_requested_at:
        raise InvalidOperationalPaperSessionRunSpecificationError()

    require_operational_paper_session_run_transition(
        canonical.observed_state,
        OperationalPaperSessionRunObservedState.FAILED,
    )

    failure = OperationalPaperSessionRunFailure(
        code=code,
        failed_at=failed_at,
    )

    return replace(
        canonical,
        observed_state=OperationalPaperSessionRunObservedState.FAILED,
        record_version=_next_record_version(canonical.record_version),
        failure=failure,
        terminal_at=failed_at,
    )
