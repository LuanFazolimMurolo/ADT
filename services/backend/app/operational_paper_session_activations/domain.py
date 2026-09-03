"""Pure operational paper-session activation domain contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.backtesting.serialization import canonical_json_bytes
from app.operational_paper_session_activations.errors import (
    InvalidOperationalPaperSessionActivationSpecificationError,
    OperationalPaperSessionActivationBoundsExceededError,
    OperationalPaperSessionActivationChecksumMismatchError,
    OperationalPaperSessionActivationStateTransitionConflictError,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationProfileBinding,
    OperationalPaperSessionMaterializationSpecification,
    OperationalPaperSessionMaterializationState,
    validate_operational_paper_session_materialization_specification_checksum,
)

OPERATIONAL_PAPER_SESSION_ACTIVATION_SCHEMA_VERSION: Final = 1
OPERATIONAL_PAPER_SESSION_ACTIVATION_CONTRACT_VERSION: Final = 1
OPERATIONAL_PAPER_SESSION_ACTIVATION_CREATE_CONTRACT_VERSION: Final = 1
MAX_OPERATIONAL_PAPER_SESSION_ACTIVATION_IDEMPOTENCY_KEY_LENGTH: Final = 128

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POSTGRESQL_BIGINT_MAX: Final = (1 << 63) - 1


class OperationalPaperSessionActivationState(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    REVOKED = "REVOKED"


_ALLOWED_TRANSITIONS: Final = {
    OperationalPaperSessionActivationState.AUTHORIZED: frozenset(
        {OperationalPaperSessionActivationState.REVOKED}
    ),
    OperationalPaperSessionActivationState.REVOKED: frozenset(),
}


def is_operational_paper_session_activation_transition_allowed(
    current: OperationalPaperSessionActivationState,
    target: OperationalPaperSessionActivationState,
) -> bool:
    if not isinstance(current, OperationalPaperSessionActivationState):
        return False
    if not isinstance(target, OperationalPaperSessionActivationState):
        return False
    return target in _ALLOWED_TRANSITIONS[current]


def require_operational_paper_session_activation_transition(
    current: OperationalPaperSessionActivationState,
    target: OperationalPaperSessionActivationState,
) -> None:
    if not is_operational_paper_session_activation_transition_allowed(current, target):
        raise OperationalPaperSessionActivationStateTransitionConflictError()


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionActivationSpecification:
    schema_version: int
    activation_contract_version: int
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
                or self.schema_version != OPERATIONAL_PAPER_SESSION_ACTIVATION_SCHEMA_VERSION
            ):
                raise ValueError
            if (
                type(self.activation_contract_version) is not int
                or self.activation_contract_version
                != OPERATIONAL_PAPER_SESSION_ACTIVATION_CONTRACT_VERSION
            ):
                raise ValueError
            materialization_id = _require_uuid(self.materialization_id)
            materialization_checksum = _require_sha256(self.materialization_checksum)
            authorization_binding = _revalidate_authorization_binding(self.authorization_binding)
            profile_binding = _revalidate_profile_binding(self.profile_binding)
            mandate_binding = _revalidate_mandate_binding(self.mandate_binding)
            simulation_id = _require_uuid(self.simulation_id)
            session_id = _require_sha256(self.session_id)
            config_checksum = _require_sha256(self.config_checksum)
        except OperationalPaperSessionActivationBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionActivationSpecificationError() from None
        object.__setattr__(self, "materialization_id", materialization_id)
        object.__setattr__(self, "materialization_checksum", materialization_checksum)
        object.__setattr__(self, "authorization_binding", authorization_binding)
        object.__setattr__(self, "profile_binding", profile_binding)
        object.__setattr__(self, "mandate_binding", mandate_binding)
        object.__setattr__(self, "simulation_id", simulation_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "config_checksum", config_checksum)


def operational_paper_session_activation_specification_payload(
    specification: OperationalPaperSessionActivationSpecification,
) -> dict[str, object]:
    canonical = _revalidate_specification(specification)
    return {
        "schema_version": canonical.schema_version,
        "activation_contract_version": canonical.activation_contract_version,
        "materialization_id": str(canonical.materialization_id),
        "materialization_checksum": canonical.materialization_checksum,
        "authorization_id": str(canonical.authorization_binding.authorization_id),
        "authorization_checksum": canonical.authorization_binding.authorization_checksum,
        "profile_id": str(canonical.profile_binding.profile_id),
        "profile_approved_revision": canonical.profile_binding.approved_revision,
        "profile_specification_checksum": canonical.profile_binding.specification_checksum,
        "mandate_id": str(canonical.mandate_binding.mandate_id),
        "mandate_approved_revision": canonical.mandate_binding.approved_revision,
        "mandate_specification_checksum": canonical.mandate_binding.specification_checksum,
        "simulation_id": str(canonical.simulation_id),
        "session_id": canonical.session_id,
        "config_checksum": canonical.config_checksum,
    }


def operational_paper_session_activation_specification_bytes(
    specification: OperationalPaperSessionActivationSpecification,
) -> bytes:
    return canonical_json_bytes(
        operational_paper_session_activation_specification_payload(specification)
    )


def operational_paper_session_activation_specification_checksum(
    specification: OperationalPaperSessionActivationSpecification,
) -> str:
    return hashlib.sha256(
        operational_paper_session_activation_specification_bytes(specification)
    ).hexdigest()


def validate_operational_paper_session_activation_specification_checksum(
    specification: OperationalPaperSessionActivationSpecification,
    expected_checksum: object,
) -> OperationalPaperSessionActivationSpecification:
    canonical = _revalidate_specification(specification)
    try:
        checksum = _require_sha256(expected_checksum)
    except Exception:
        raise InvalidOperationalPaperSessionActivationSpecificationError() from None
    if operational_paper_session_activation_specification_checksum(canonical) != checksum:
        raise OperationalPaperSessionActivationChecksumMismatchError()
    return canonical


def operational_paper_session_activation_specifications_equal(
    left: OperationalPaperSessionActivationSpecification,
    right: OperationalPaperSessionActivationSpecification,
) -> bool:
    return operational_paper_session_activation_specification_bytes(
        left
    ) == operational_paper_session_activation_specification_bytes(right)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionActivationCreateIntent:
    materialization_id: UUID
    materialization_checksum: str

    def __post_init__(self) -> None:
        try:
            materialization_id = _require_uuid(self.materialization_id)
            materialization_checksum = _require_sha256(self.materialization_checksum)
        except Exception:
            raise InvalidOperationalPaperSessionActivationSpecificationError() from None
        object.__setattr__(self, "materialization_id", materialization_id)
        object.__setattr__(self, "materialization_checksum", materialization_checksum)


def operational_paper_session_activation_create_intent_fingerprint(
    intent: OperationalPaperSessionActivationCreateIntent,
) -> str:
    canonical = _revalidate_create_intent(intent)
    payload = {
        "contract_version": OPERATIONAL_PAPER_SESSION_ACTIVATION_CREATE_CONTRACT_VERSION,
        "materialization_id": str(canonical.materialization_id),
        "materialization_checksum": canonical.materialization_checksum,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def validate_operational_paper_session_activation_idempotency_key(
    value: object,
) -> str:
    if not isinstance(value, str):
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    if not (1 <= len(value) <= MAX_OPERATIONAL_PAPER_SESSION_ACTIVATION_IDEMPOTENCY_KEY_LENGTH):
        raise OperationalPaperSessionActivationBoundsExceededError()
    if _SAFE_TOKEN.fullmatch(value) is None:
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return value


def build_operational_paper_session_activation_specification(
    materialization: OperationalPaperSessionMaterialization,
) -> OperationalPaperSessionActivationSpecification:
    try:
        if not isinstance(materialization, OperationalPaperSessionMaterialization):
            raise ValueError
        if materialization.state is not OperationalPaperSessionMaterializationState.MATERIALIZED:
            raise ValueError
        materialization_specification = OperationalPaperSessionMaterializationSpecification(
            schema_version=materialization.schema_version,
            materialization_contract_version=materialization.materialization_contract_version,
            authorization_binding=materialization.authorization_binding,
            profile_binding=materialization.profile_binding,
            mandate_binding=materialization.mandate_binding,
            simulation_id=materialization.simulation_id,
            config_checksum=materialization.config_checksum,
            session_id=materialization.session_id,
        )
        materialization_specification = (
            validate_operational_paper_session_materialization_specification_checksum(
                materialization_specification,
                materialization.materialization_checksum,
            )
        )
    except Exception:
        raise InvalidOperationalPaperSessionActivationSpecificationError() from None
    return OperationalPaperSessionActivationSpecification(
        schema_version=OPERATIONAL_PAPER_SESSION_ACTIVATION_SCHEMA_VERSION,
        activation_contract_version=OPERATIONAL_PAPER_SESSION_ACTIVATION_CONTRACT_VERSION,
        materialization_id=materialization.materialization_id,
        materialization_checksum=materialization.materialization_checksum,
        authorization_binding=materialization_specification.authorization_binding,
        profile_binding=materialization_specification.profile_binding,
        mandate_binding=materialization_specification.mandate_binding,
        simulation_id=materialization_specification.simulation_id,
        session_id=materialization_specification.session_id,
        config_checksum=materialization_specification.config_checksum,
    )


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionActivation:
    activation_id: UUID
    schema_version: int
    activation_contract_version: int
    state: OperationalPaperSessionActivationState
    record_version: int
    materialization_id: UUID
    materialization_checksum: str
    authorization_binding: OperationalPaperSessionMaterializationAuthorizationBinding
    profile_binding: OperationalPaperSessionMaterializationProfileBinding
    mandate_binding: OperationalPaperSessionMaterializationMandateBinding
    simulation_id: UUID
    session_id: str
    config_checksum: str
    activation_checksum: str
    authorized_by: UUID
    authorized_at: datetime
    revoked_by: UUID | None
    revoked_at: datetime | None
    create_idempotency_key: str
    create_intent_fingerprint: str

    def __post_init__(self) -> None:
        try:
            activation_id = _require_uuid(self.activation_id)
            if not isinstance(self.state, OperationalPaperSessionActivationState):
                raise ValueError
            record_version = _require_positive_bigint(self.record_version)
            specification = OperationalPaperSessionActivationSpecification(
                schema_version=self.schema_version,
                activation_contract_version=self.activation_contract_version,
                materialization_id=self.materialization_id,
                materialization_checksum=self.materialization_checksum,
                authorization_binding=self.authorization_binding,
                profile_binding=self.profile_binding,
                mandate_binding=self.mandate_binding,
                simulation_id=self.simulation_id,
                session_id=self.session_id,
                config_checksum=self.config_checksum,
            )
            specification = validate_operational_paper_session_activation_specification_checksum(
                specification,
                self.activation_checksum,
            )
            activation_checksum = _require_sha256(self.activation_checksum)
            authorized_by = _require_uuid(self.authorized_by)
            authorized_at = _require_utc(self.authorized_at)
            create_idempotency_key = validate_operational_paper_session_activation_idempotency_key(
                self.create_idempotency_key
            )
            create_intent_fingerprint = _require_sha256(self.create_intent_fingerprint)
            revoked = _collective_presence(self.revoked_by, self.revoked_at)
            revoked_by: UUID | None = None
            revoked_at: datetime | None = None
            if revoked:
                if self.revoked_by is None or self.revoked_at is None:
                    raise ValueError
                revoked_by = _require_uuid(self.revoked_by)
                revoked_at = _require_utc(self.revoked_at)
                if revoked_at < authorized_at:
                    raise ValueError
            valid_state = (
                self.state is OperationalPaperSessionActivationState.AUTHORIZED and not revoked
            ) or (self.state is OperationalPaperSessionActivationState.REVOKED and revoked)
            if not valid_state:
                raise ValueError
        except OperationalPaperSessionActivationBoundsExceededError:
            raise
        except OperationalPaperSessionActivationChecksumMismatchError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionActivationSpecificationError() from None
        object.__setattr__(self, "activation_id", activation_id)
        object.__setattr__(self, "record_version", record_version)
        object.__setattr__(self, "materialization_id", specification.materialization_id)
        object.__setattr__(self, "materialization_checksum", specification.materialization_checksum)
        object.__setattr__(self, "authorization_binding", specification.authorization_binding)
        object.__setattr__(self, "profile_binding", specification.profile_binding)
        object.__setattr__(self, "mandate_binding", specification.mandate_binding)
        object.__setattr__(self, "simulation_id", specification.simulation_id)
        object.__setattr__(self, "session_id", specification.session_id)
        object.__setattr__(self, "config_checksum", specification.config_checksum)
        object.__setattr__(self, "activation_checksum", activation_checksum)
        object.__setattr__(self, "authorized_by", authorized_by)
        object.__setattr__(self, "authorized_at", authorized_at)
        object.__setattr__(self, "revoked_by", revoked_by)
        object.__setattr__(self, "revoked_at", revoked_at)
        object.__setattr__(self, "create_idempotency_key", create_idempotency_key)
        object.__setattr__(self, "create_intent_fingerprint", create_intent_fingerprint)


def authorize_operational_paper_session_activation(
    *,
    activation_id: UUID,
    specification: OperationalPaperSessionActivationSpecification,
    authorized_by: UUID,
    authorized_at: datetime,
    create_idempotency_key: str,
    create_intent_fingerprint: str,
) -> OperationalPaperSessionActivation:
    canonical = _revalidate_specification(specification)
    return OperationalPaperSessionActivation(
        activation_id=activation_id,
        schema_version=canonical.schema_version,
        activation_contract_version=canonical.activation_contract_version,
        state=OperationalPaperSessionActivationState.AUTHORIZED,
        record_version=1,
        materialization_id=canonical.materialization_id,
        materialization_checksum=canonical.materialization_checksum,
        authorization_binding=canonical.authorization_binding,
        profile_binding=canonical.profile_binding,
        mandate_binding=canonical.mandate_binding,
        simulation_id=canonical.simulation_id,
        session_id=canonical.session_id,
        config_checksum=canonical.config_checksum,
        activation_checksum=operational_paper_session_activation_specification_checksum(canonical),
        authorized_by=authorized_by,
        authorized_at=authorized_at,
        revoked_by=None,
        revoked_at=None,
        create_idempotency_key=create_idempotency_key,
        create_intent_fingerprint=create_intent_fingerprint,
    )


def revoke_operational_paper_session_activation(
    activation: OperationalPaperSessionActivation,
    *,
    revoked_by: UUID,
    revoked_at: datetime,
) -> OperationalPaperSessionActivation:
    canonical = _revalidate_activation(activation)
    require_operational_paper_session_activation_transition(
        canonical.state,
        OperationalPaperSessionActivationState.REVOKED,
    )
    return OperationalPaperSessionActivation(
        activation_id=canonical.activation_id,
        schema_version=canonical.schema_version,
        activation_contract_version=canonical.activation_contract_version,
        state=OperationalPaperSessionActivationState.REVOKED,
        record_version=canonical.record_version + 1,
        materialization_id=canonical.materialization_id,
        materialization_checksum=canonical.materialization_checksum,
        authorization_binding=canonical.authorization_binding,
        profile_binding=canonical.profile_binding,
        mandate_binding=canonical.mandate_binding,
        simulation_id=canonical.simulation_id,
        session_id=canonical.session_id,
        config_checksum=canonical.config_checksum,
        activation_checksum=canonical.activation_checksum,
        authorized_by=canonical.authorized_by,
        authorized_at=canonical.authorized_at,
        revoked_by=revoked_by,
        revoked_at=revoked_at,
        create_idempotency_key=canonical.create_idempotency_key,
        create_intent_fingerprint=canonical.create_intent_fingerprint,
    )


def _revalidate_authorization_binding(
    value: object,
) -> OperationalPaperSessionMaterializationAuthorizationBinding:
    if not isinstance(value, OperationalPaperSessionMaterializationAuthorizationBinding):
        raise ValueError
    return OperationalPaperSessionMaterializationAuthorizationBinding(
        authorization_id=value.authorization_id,
        authorization_checksum=value.authorization_checksum,
    )


def _revalidate_profile_binding(
    value: object,
) -> OperationalPaperSessionMaterializationProfileBinding:
    if not isinstance(value, OperationalPaperSessionMaterializationProfileBinding):
        raise ValueError
    return OperationalPaperSessionMaterializationProfileBinding(
        profile_id=value.profile_id,
        approved_revision=value.approved_revision,
        specification_checksum=value.specification_checksum,
    )


def _revalidate_mandate_binding(
    value: object,
) -> OperationalPaperSessionMaterializationMandateBinding:
    if not isinstance(value, OperationalPaperSessionMaterializationMandateBinding):
        raise ValueError
    return OperationalPaperSessionMaterializationMandateBinding(
        mandate_id=value.mandate_id,
        approved_revision=value.approved_revision,
        specification_checksum=value.specification_checksum,
    )


def _revalidate_specification(
    value: object,
) -> OperationalPaperSessionActivationSpecification:
    if not isinstance(value, OperationalPaperSessionActivationSpecification):
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return OperationalPaperSessionActivationSpecification(
        schema_version=value.schema_version,
        activation_contract_version=value.activation_contract_version,
        materialization_id=value.materialization_id,
        materialization_checksum=value.materialization_checksum,
        authorization_binding=value.authorization_binding,
        profile_binding=value.profile_binding,
        mandate_binding=value.mandate_binding,
        simulation_id=value.simulation_id,
        session_id=value.session_id,
        config_checksum=value.config_checksum,
    )


def _revalidate_create_intent(
    value: object,
) -> OperationalPaperSessionActivationCreateIntent:
    if not isinstance(value, OperationalPaperSessionActivationCreateIntent):
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return OperationalPaperSessionActivationCreateIntent(
        materialization_id=value.materialization_id,
        materialization_checksum=value.materialization_checksum,
    )


def _revalidate_activation(value: object) -> OperationalPaperSessionActivation:
    if not isinstance(value, OperationalPaperSessionActivation):
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return OperationalPaperSessionActivation(
        activation_id=value.activation_id,
        schema_version=value.schema_version,
        activation_contract_version=value.activation_contract_version,
        state=value.state,
        record_version=value.record_version,
        materialization_id=value.materialization_id,
        materialization_checksum=value.materialization_checksum,
        authorization_binding=value.authorization_binding,
        profile_binding=value.profile_binding,
        mandate_binding=value.mandate_binding,
        simulation_id=value.simulation_id,
        session_id=value.session_id,
        config_checksum=value.config_checksum,
        activation_checksum=value.activation_checksum,
        authorized_by=value.authorized_by,
        authorized_at=value.authorized_at,
        revoked_by=value.revoked_by,
        revoked_at=value.revoked_at,
        create_idempotency_key=value.create_idempotency_key,
        create_intent_fingerprint=value.create_intent_fingerprint,
    )


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError
    return value


def _require_positive_bigint(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError
    if value > _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionActivationBoundsExceededError()
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


def _collective_presence(*values: object | None) -> bool:
    present = tuple(value is not None for value in values)
    if any(present) and not all(present):
        raise ValueError
    return all(present)
