"""Pure-domain tests for operational paper-session activations."""

from __future__ import annotations

import hashlib
import re
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

import app.operational_paper_session_activations as public_contract
from app.backtesting.serialization import canonical_json_bytes
from app.operational_paper_session_activations import (
    MAX_OPERATIONAL_PAPER_SESSION_ACTIVATION_IDEMPOTENCY_KEY_LENGTH,
    OPERATIONAL_PAPER_SESSION_ACTIVATION_CONTRACT_VERSION,
    OPERATIONAL_PAPER_SESSION_ACTIVATION_CREATE_CONTRACT_VERSION,
    OPERATIONAL_PAPER_SESSION_ACTIVATION_SCHEMA_VERSION,
    InvalidOperationalPaperSessionActivationSpecificationError,
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationBoundsExceededError,
    OperationalPaperSessionActivationChecksumMismatchError,
    OperationalPaperSessionActivationCreateIntent,
    OperationalPaperSessionActivationSpecification,
    OperationalPaperSessionActivationState,
    OperationalPaperSessionActivationStateTransitionConflictError,
    authorize_operational_paper_session_activation,
    build_operational_paper_session_activation_specification,
    is_operational_paper_session_activation_transition_allowed,
    operational_paper_session_activation_create_intent_fingerprint,
    operational_paper_session_activation_specification_bytes,
    operational_paper_session_activation_specification_checksum,
    operational_paper_session_activation_specification_payload,
    operational_paper_session_activation_specifications_equal,
    require_operational_paper_session_activation_transition,
    revoke_operational_paper_session_activation,
    validate_operational_paper_session_activation_idempotency_key,
    validate_operational_paper_session_activation_specification_checksum,
)
from app.operational_paper_session_materializations import (
    OPERATIONAL_PAPER_SESSION_MATERIALIZATION_CONTRACT_VERSION,
    OPERATIONAL_PAPER_SESSION_MATERIALIZATION_SCHEMA_VERSION,
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationProfileBinding,
    OperationalPaperSessionMaterializationSpecification,
    OperationalPaperSessionMaterializationState,
    operational_paper_session_materialization_specification_checksum,
)

ACTIVATION_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_ACTIVATION_ID = UUID("10000000-0000-4000-8000-000000000002")
MATERIALIZATION_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_MATERIALIZATION_ID = UUID("20000000-0000-4000-8000-000000000002")
AUTHORIZATION_ID = UUID("30000000-0000-4000-8000-000000000001")
OTHER_AUTHORIZATION_ID = UUID("30000000-0000-4000-8000-000000000002")
PROFILE_ID = UUID("40000000-0000-4000-8000-000000000001")
OTHER_PROFILE_ID = UUID("40000000-0000-4000-8000-000000000002")
MANDATE_ID = UUID("50000000-0000-4000-8000-000000000001")
OTHER_MANDATE_ID = UUID("50000000-0000-4000-8000-000000000002")
SIMULATION_ID = UUID("60000000-0000-4000-8000-000000000001")
OTHER_SIMULATION_ID = UUID("60000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("70000000-0000-4000-8000-000000000001")
OTHER_ACTOR_ID = UUID("70000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 3, 18, tzinfo=UTC)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
POSTGRESQL_BIGINT_MAX = (1 << 63) - 1


def _authorization_binding(
    **changes: object,
) -> OperationalPaperSessionMaterializationAuthorizationBinding:
    values: dict[str, object] = {
        "authorization_id": AUTHORIZATION_ID,
        "authorization_checksum": "a" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionMaterializationAuthorizationBinding(**values)  # type: ignore[arg-type]


def _profile_binding(
    **changes: object,
) -> OperationalPaperSessionMaterializationProfileBinding:
    values: dict[str, object] = {
        "profile_id": PROFILE_ID,
        "approved_revision": 2,
        "specification_checksum": "b" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionMaterializationProfileBinding(**values)  # type: ignore[arg-type]


def _mandate_binding(
    **changes: object,
) -> OperationalPaperSessionMaterializationMandateBinding:
    values: dict[str, object] = {
        "mandate_id": MANDATE_ID,
        "approved_revision": 3,
        "specification_checksum": "c" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionMaterializationMandateBinding(**values)  # type: ignore[arg-type]


def _materialization_specification(
    **changes: object,
) -> OperationalPaperSessionMaterializationSpecification:
    values: dict[str, object] = {
        "schema_version": OPERATIONAL_PAPER_SESSION_MATERIALIZATION_SCHEMA_VERSION,
        "materialization_contract_version": (
            OPERATIONAL_PAPER_SESSION_MATERIALIZATION_CONTRACT_VERSION
        ),
        "authorization_binding": _authorization_binding(),
        "profile_binding": _profile_binding(),
        "mandate_binding": _mandate_binding(),
        "simulation_id": SIMULATION_ID,
        "config_checksum": "d" * 64,
        "session_id": "e" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionMaterializationSpecification(**values)  # type: ignore[arg-type]


def _materialization(
    *,
    state: OperationalPaperSessionMaterializationState = (
        OperationalPaperSessionMaterializationState.MATERIALIZED
    ),
) -> OperationalPaperSessionMaterialization:
    specification = _materialization_specification()
    materialized = state is OperationalPaperSessionMaterializationState.MATERIALIZED
    return OperationalPaperSessionMaterialization(
        materialization_id=MATERIALIZATION_ID,
        schema_version=specification.schema_version,
        materialization_contract_version=specification.materialization_contract_version,
        state=state,
        record_version=2 if materialized else 1,
        authorization_binding=specification.authorization_binding,
        profile_binding=specification.profile_binding,
        mandate_binding=specification.mandate_binding,
        simulation_id=specification.simulation_id,
        config_checksum=specification.config_checksum,
        session_id=specification.session_id,
        materialization_checksum=(
            operational_paper_session_materialization_specification_checksum(specification)
        ),
        prepared_by=ACTOR_ID,
        prepared_at=NOW - timedelta(seconds=1),
        materialized_by=ACTOR_ID if materialized else None,
        materialized_at=NOW if materialized else None,
    )


def _specification(**changes: object) -> OperationalPaperSessionActivationSpecification:
    materialization = _materialization()
    values: dict[str, object] = {
        "schema_version": OPERATIONAL_PAPER_SESSION_ACTIVATION_SCHEMA_VERSION,
        "activation_contract_version": OPERATIONAL_PAPER_SESSION_ACTIVATION_CONTRACT_VERSION,
        "materialization_id": materialization.materialization_id,
        "materialization_checksum": materialization.materialization_checksum,
        "authorization_binding": materialization.authorization_binding,
        "profile_binding": materialization.profile_binding,
        "mandate_binding": materialization.mandate_binding,
        "simulation_id": materialization.simulation_id,
        "session_id": materialization.session_id,
        "config_checksum": materialization.config_checksum,
    }
    values.update(changes)
    return OperationalPaperSessionActivationSpecification(**values)  # type: ignore[arg-type]


def _intent(**changes: object) -> OperationalPaperSessionActivationCreateIntent:
    specification = _specification()
    values: dict[str, object] = {
        "materialization_id": specification.materialization_id,
        "materialization_checksum": specification.materialization_checksum,
    }
    values.update(changes)
    return OperationalPaperSessionActivationCreateIntent(**values)  # type: ignore[arg-type]


def _aggregate(**changes: object) -> OperationalPaperSessionActivation:
    specification = _specification()
    values: dict[str, object] = {
        "activation_id": ACTIVATION_ID,
        "schema_version": specification.schema_version,
        "activation_contract_version": specification.activation_contract_version,
        "state": OperationalPaperSessionActivationState.AUTHORIZED,
        "record_version": 1,
        "materialization_id": specification.materialization_id,
        "materialization_checksum": specification.materialization_checksum,
        "authorization_binding": specification.authorization_binding,
        "profile_binding": specification.profile_binding,
        "mandate_binding": specification.mandate_binding,
        "simulation_id": specification.simulation_id,
        "session_id": specification.session_id,
        "config_checksum": specification.config_checksum,
        "activation_checksum": (
            operational_paper_session_activation_specification_checksum(specification)
        ),
        "authorized_by": ACTOR_ID,
        "authorized_at": NOW,
        "revoked_by": None,
        "revoked_at": None,
        "create_idempotency_key": "activation:create:1",
        "create_intent_fingerprint": (
            operational_paper_session_activation_create_intent_fingerprint(_intent())
        ),
    }
    values.update(changes)
    return OperationalPaperSessionActivation(**values)  # type: ignore[arg-type]


def _authorized() -> OperationalPaperSessionActivation:
    specification = _specification()
    return authorize_operational_paper_session_activation(
        activation_id=ACTIVATION_ID,
        specification=specification,
        authorized_by=ACTOR_ID,
        authorized_at=NOW,
        create_idempotency_key="activation:create:1",
        create_intent_fingerprint=(
            operational_paper_session_activation_create_intent_fingerprint(_intent())
        ),
    )


def test_public_contract_contains_no_private_or_duplicate_exports() -> None:
    assert len(public_contract.__all__) == len(set(public_contract.__all__))
    assert all(not name.startswith("_") for name in public_contract.__all__)


def test_lifecycle_is_exact_and_revoked_is_terminal() -> None:
    assert tuple(OperationalPaperSessionActivationState) == (
        OperationalPaperSessionActivationState.AUTHORIZED,
        OperationalPaperSessionActivationState.REVOKED,
    )
    assert is_operational_paper_session_activation_transition_allowed(
        OperationalPaperSessionActivationState.AUTHORIZED,
        OperationalPaperSessionActivationState.REVOKED,
    )
    require_operational_paper_session_activation_transition(
        OperationalPaperSessionActivationState.AUTHORIZED,
        OperationalPaperSessionActivationState.REVOKED,
    )
    for current, target in (
        (
            OperationalPaperSessionActivationState.AUTHORIZED,
            OperationalPaperSessionActivationState.AUTHORIZED,
        ),
        (
            OperationalPaperSessionActivationState.REVOKED,
            OperationalPaperSessionActivationState.AUTHORIZED,
        ),
        (
            OperationalPaperSessionActivationState.REVOKED,
            OperationalPaperSessionActivationState.REVOKED,
        ),
    ):
        assert not is_operational_paper_session_activation_transition_allowed(current, target)
        with pytest.raises(OperationalPaperSessionActivationStateTransitionConflictError):
            require_operational_paper_session_activation_transition(current, target)


def test_transition_helpers_reject_non_state_values() -> None:
    assert not is_operational_paper_session_activation_transition_allowed(  # type: ignore[arg-type]
        "AUTHORIZED",
        OperationalPaperSessionActivationState.REVOKED,
    )
    with pytest.raises(OperationalPaperSessionActivationStateTransitionConflictError):
        require_operational_paper_session_activation_transition(  # type: ignore[arg-type]
            OperationalPaperSessionActivationState.AUTHORIZED,
            "REVOKED",
        )


def test_build_specification_reuses_exact_materialization_bindings() -> None:
    materialization = _materialization()
    specification = build_operational_paper_session_activation_specification(materialization)
    assert specification.materialization_id == materialization.materialization_id
    assert specification.materialization_checksum == materialization.materialization_checksum
    assert specification.authorization_binding == materialization.authorization_binding
    assert specification.profile_binding == materialization.profile_binding
    assert specification.mandate_binding == materialization.mandate_binding
    assert specification.simulation_id == materialization.simulation_id
    assert specification.session_id == materialization.session_id
    assert specification.config_checksum == materialization.config_checksum
    assert isinstance(
        specification.authorization_binding,
        OperationalPaperSessionMaterializationAuthorizationBinding,
    )


def test_only_materialized_session_can_build_activation_specification() -> None:
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        build_operational_paper_session_activation_specification(
            _materialization(state=OperationalPaperSessionMaterializationState.PREPARED)
        )


def test_materialization_checksum_is_revalidated_before_specification_build() -> None:
    materialization = _materialization()
    object.__setattr__(materialization, "materialization_checksum", "0" * 64)
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        build_operational_paper_session_activation_specification(materialization)


def test_specification_payload_and_bytes_are_canonical_and_deterministic() -> None:
    specification = _specification()
    rebuilt = OperationalPaperSessionActivationSpecification(
        **{  # type: ignore[arg-type]
            field.name: getattr(specification, field.name) for field in fields(specification)
        }
    )
    payload = operational_paper_session_activation_specification_payload(specification)
    assert payload == {
        "schema_version": 1,
        "activation_contract_version": 1,
        "materialization_id": str(MATERIALIZATION_ID),
        "materialization_checksum": specification.materialization_checksum,
        "authorization_id": str(AUTHORIZATION_ID),
        "authorization_checksum": "a" * 64,
        "profile_id": str(PROFILE_ID),
        "profile_approved_revision": 2,
        "profile_specification_checksum": "b" * 64,
        "mandate_id": str(MANDATE_ID),
        "mandate_approved_revision": 3,
        "mandate_specification_checksum": "c" * 64,
        "simulation_id": str(SIMULATION_ID),
        "session_id": "e" * 64,
        "config_checksum": "d" * 64,
    }
    assert operational_paper_session_activation_specifications_equal(specification, rebuilt)
    assert operational_paper_session_activation_specification_bytes(
        specification
    ) == operational_paper_session_activation_specification_bytes(rebuilt)
    assert operational_paper_session_activation_specification_checksum(
        specification
    ) == operational_paper_session_activation_specification_checksum(rebuilt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("materialization_id", OTHER_MATERIALIZATION_ID),
        ("materialization_checksum", "f" * 64),
        (
            "authorization_binding",
            _authorization_binding(authorization_id=OTHER_AUTHORIZATION_ID),
        ),
        (
            "authorization_binding",
            _authorization_binding(authorization_checksum="f" * 64),
        ),
        ("profile_binding", _profile_binding(profile_id=OTHER_PROFILE_ID)),
        ("profile_binding", _profile_binding(approved_revision=4)),
        ("profile_binding", _profile_binding(specification_checksum="f" * 64)),
        ("mandate_binding", _mandate_binding(mandate_id=OTHER_MANDATE_ID)),
        ("mandate_binding", _mandate_binding(approved_revision=4)),
        ("mandate_binding", _mandate_binding(specification_checksum="f" * 64)),
        ("simulation_id", OTHER_SIMULATION_ID),
        ("session_id", "f" * 64),
        ("config_checksum", "f" * 64),
    ],
)
def test_every_identity_bearing_dimension_changes_activation_checksum(
    field: str,
    value: object,
) -> None:
    baseline = operational_paper_session_activation_specification_checksum(_specification())
    changed = operational_paper_session_activation_specification_checksum(
        _specification(**{field: value})
    )
    assert SHA256.fullmatch(baseline)
    assert changed != baseline


def test_schema_and_contract_versions_are_checksum_dimensions() -> None:
    payload = operational_paper_session_activation_specification_payload(_specification())
    baseline = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    for key in ("schema_version", "activation_contract_version"):
        changed = {**payload, key: 2}
        assert hashlib.sha256(canonical_json_bytes(changed)).hexdigest() != baseline


def test_checksum_validation_rejects_malformed_and_mismatch() -> None:
    specification = _specification()
    checksum = operational_paper_session_activation_specification_checksum(specification)
    assert (
        validate_operational_paper_session_activation_specification_checksum(
            specification,
            checksum,
        )
        == specification
    )
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        validate_operational_paper_session_activation_specification_checksum(
            specification,
            checksum.upper(),
        )
    with pytest.raises(OperationalPaperSessionActivationChecksumMismatchError):
        validate_operational_paper_session_activation_specification_checksum(
            specification,
            "0" * 64,
        )


def test_create_intent_is_minimal_and_fingerprint_is_deterministic() -> None:
    intent = _intent()
    assert {field.name for field in fields(intent)} == {
        "materialization_id",
        "materialization_checksum",
    }
    fingerprint = operational_paper_session_activation_create_intent_fingerprint(intent)
    assert SHA256.fullmatch(fingerprint)
    assert fingerprint == operational_paper_session_activation_create_intent_fingerprint(_intent())
    assert fingerprint != operational_paper_session_activation_create_intent_fingerprint(
        _intent(materialization_id=OTHER_MATERIALIZATION_ID)
    )
    assert fingerprint != operational_paper_session_activation_create_intent_fingerprint(
        _intent(materialization_checksum="f" * 64)
    )


def test_create_intent_fingerprint_is_distinct_from_activation_checksum() -> None:
    fingerprint = operational_paper_session_activation_create_intent_fingerprint(_intent())
    checksum = operational_paper_session_activation_specification_checksum(_specification())
    assert fingerprint != checksum
    assert OPERATIONAL_PAPER_SESSION_ACTIVATION_CREATE_CONTRACT_VERSION == 1


def test_create_intent_excludes_generated_current_and_runtime_state() -> None:
    names = {field.name for field in fields(_intent())}
    assert {
        "activation_id",
        "activation_checksum",
        "state",
        "record_version",
        "actor",
        "timestamp",
        "available_balance",
        "filesystem_result",
        "plugin_availability",
        "runtime_state",
        "heartbeat",
        "lease",
        "process_identity",
    }.isdisjoint(names)


@pytest.mark.parametrize("key", ["activation:1", "A", "A" * 128])
def test_safe_idempotency_key_is_preserved(key: str) -> None:
    assert validate_operational_paper_session_activation_idempotency_key(key) == key


@pytest.mark.parametrize("key", ["", "A" * 129, "unsafe key", "unsafe/value", 1])
def test_invalid_idempotency_key_is_rejected(key: object) -> None:
    with pytest.raises(
        (
            InvalidOperationalPaperSessionActivationSpecificationError,
            OperationalPaperSessionActivationBoundsExceededError,
        )
    ):
        validate_operational_paper_session_activation_idempotency_key(key)


def test_authorize_creates_exact_initial_aggregate() -> None:
    specification = _specification()
    activation = _authorized()
    assert activation.activation_id == ACTIVATION_ID
    assert activation.state is OperationalPaperSessionActivationState.AUTHORIZED
    assert activation.record_version == 1
    assert activation.materialization_id == specification.materialization_id
    assert activation.materialization_checksum == specification.materialization_checksum
    assert activation.authorization_binding == specification.authorization_binding
    assert activation.profile_binding == specification.profile_binding
    assert activation.mandate_binding == specification.mandate_binding
    assert activation.simulation_id == specification.simulation_id
    assert activation.session_id == specification.session_id
    assert activation.config_checksum == specification.config_checksum
    assert activation.activation_checksum == (
        operational_paper_session_activation_specification_checksum(specification)
    )
    assert activation.authorized_by == ACTOR_ID
    assert activation.authorized_at == NOW
    assert activation.revoked_by is None
    assert activation.revoked_at is None


def test_activation_uuid_is_not_paper_session_identity() -> None:
    activation = _authorized()
    assert str(activation.activation_id) != activation.session_id
    assert len(activation.session_id) == 64


def test_revoke_preserves_grant_semantics_and_idempotency_evidence() -> None:
    authorized = _authorized()
    revoked_at = NOW + timedelta(seconds=1)
    revoked = revoke_operational_paper_session_activation(
        authorized,
        revoked_by=OTHER_ACTOR_ID,
        revoked_at=revoked_at,
    )
    assert revoked.state is OperationalPaperSessionActivationState.REVOKED
    assert revoked.record_version == authorized.record_version + 1
    assert revoked.revoked_by == OTHER_ACTOR_ID
    assert revoked.revoked_at == revoked_at
    for field in (
        "activation_id",
        "schema_version",
        "activation_contract_version",
        "materialization_id",
        "materialization_checksum",
        "authorization_binding",
        "profile_binding",
        "mandate_binding",
        "simulation_id",
        "session_id",
        "config_checksum",
        "activation_checksum",
        "authorized_by",
        "authorized_at",
        "create_idempotency_key",
        "create_intent_fingerprint",
    ):
        assert getattr(revoked, field) == getattr(authorized, field)


def test_revoked_activation_is_terminal() -> None:
    revoked = revoke_operational_paper_session_activation(
        _authorized(),
        revoked_by=OTHER_ACTOR_ID,
        revoked_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(OperationalPaperSessionActivationStateTransitionConflictError):
        revoke_operational_paper_session_activation(
            revoked,
            revoked_by=OTHER_ACTOR_ID,
            revoked_at=NOW + timedelta(seconds=2),
        )


def test_valid_aggregate_metadata_exclusions_do_not_change_activation_checksum() -> None:
    first = _aggregate()
    second = _aggregate(
        activation_id=OTHER_ACTIVATION_ID,
        record_version=7,
        authorized_by=OTHER_ACTOR_ID,
        authorized_at=NOW + timedelta(seconds=5),
        create_idempotency_key="activation:create:2",
        create_intent_fingerprint="f" * 64,
    )
    revoked = revoke_operational_paper_session_activation(
        first,
        revoked_by=OTHER_ACTOR_ID,
        revoked_at=NOW + timedelta(seconds=1),
    )
    assert first.activation_checksum == second.activation_checksum
    assert second.activation_checksum == revoked.activation_checksum


@pytest.mark.parametrize(
    "changes",
    [
        {"activation_id": UUID(int=0)},
        {"activation_id": "not-a-uuid"},
        {"schema_version": 2},
        {"schema_version": True},
        {"activation_contract_version": 2},
        {"state": "AUTHORIZED"},
        {"record_version": 0},
        {"record_version": True},
        {"authorized_by": UUID(int=0)},
        {"create_intent_fingerprint": "A" * 64},
        {"create_intent_fingerprint": "bad"},
        {"revoked_by": OTHER_ACTOR_ID},
        {"revoked_at": NOW + timedelta(seconds=1)},
        {"state": OperationalPaperSessionActivationState.REVOKED},
    ],
)
def test_invalid_aggregate_metadata_fails_closed(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        _aggregate(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"materialization_id": UUID(int=0)},
        {"materialization_id": "not-a-uuid"},
        {"materialization_checksum": "A" * 64},
        {"materialization_checksum": "bad"},
        {"session_id": "A" * 64},
        {"session_id": "bad"},
        {"config_checksum": "A" * 64},
        {"config_checksum": "bad"},
        {"schema_version": 2},
        {"schema_version": True},
        {"activation_contract_version": 2},
        {"activation_contract_version": True},
    ],
)
def test_invalid_specification_identity_and_versions_fail_closed(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        _specification(**changes)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("materialization_id", OTHER_MATERIALIZATION_ID),
        ("materialization_checksum", "f" * 64),
        (
            "authorization_binding",
            _authorization_binding(authorization_checksum="f" * 64),
        ),
        ("profile_binding", _profile_binding(approved_revision=4)),
        ("mandate_binding", _mandate_binding(approved_revision=4)),
        ("simulation_id", OTHER_SIMULATION_ID),
        ("session_id", "f" * 64),
        ("config_checksum", "f" * 64),
    ],
)
def test_aggregate_rejects_semantic_drift_without_new_checksum(
    field: str,
    value: object,
) -> None:
    with pytest.raises(OperationalPaperSessionActivationChecksumMismatchError):
        _aggregate(**{field: value})


def test_aggregate_rejects_malformed_and_mismatched_activation_checksum() -> None:
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        _aggregate(activation_checksum="A" * 64)
    with pytest.raises(OperationalPaperSessionActivationChecksumMismatchError):
        _aggregate(activation_checksum="0" * 64)


def test_nested_materialization_bindings_are_revalidated() -> None:
    authorization_binding = _authorization_binding()
    object.__setattr__(authorization_binding, "authorization_checksum", "BAD")
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        _specification(authorization_binding=authorization_binding)


def test_corrupted_frozen_values_are_revalidated_by_helpers() -> None:
    specification = _specification()
    object.__setattr__(specification, "session_id", "BAD")
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        operational_paper_session_activation_specification_checksum(specification)

    intent = _intent()
    object.__setattr__(intent, "materialization_id", UUID(int=0))
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        operational_paper_session_activation_create_intent_fingerprint(intent)


def test_timestamps_require_exact_utc_and_chronology() -> None:
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        _aggregate(authorized_at=datetime(2026, 9, 3, 18))
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        _aggregate(authorized_at=datetime.fromisoformat("2026-09-03T15:00:00-03:00"))
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        revoke_operational_paper_session_activation(
            _authorized(),
            revoked_by=OTHER_ACTOR_ID,
            revoked_at=NOW - timedelta(seconds=1),
        )
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        revoke_operational_paper_session_activation(
            _authorized(),
            revoked_by=OTHER_ACTOR_ID,
            revoked_at=datetime.fromisoformat("2026-09-03T15:01:00-03:00"),
        )


def test_record_version_matches_postgresql_bigint_width() -> None:
    assert _aggregate(record_version=POSTGRESQL_BIGINT_MAX).record_version == (
        POSTGRESQL_BIGINT_MAX
    )
    with pytest.raises(OperationalPaperSessionActivationBoundsExceededError):
        _aggregate(record_version=POSTGRESQL_BIGINT_MAX + 1)
    with pytest.raises(OperationalPaperSessionActivationBoundsExceededError):
        revoke_operational_paper_session_activation(
            _aggregate(record_version=POSTGRESQL_BIGINT_MAX),
            revoked_by=OTHER_ACTOR_ID,
            revoked_at=NOW + timedelta(seconds=1),
        )


def test_contracts_are_frozen_slotted_and_have_no_mutable_collections() -> None:
    specification = _specification()
    intent = _intent()
    activation = _authorized()
    with pytest.raises(FrozenInstanceError):
        specification.session_id = "f" * 64  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        intent.materialization_id = OTHER_MATERIALIZATION_ID  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        activation.state = OperationalPaperSessionActivationState.REVOKED  # type: ignore[misc]
    assert not hasattr(specification, "__dict__")
    assert not hasattr(intent, "__dict__")
    assert not hasattr(activation, "__dict__")
    assert all(
        not isinstance(getattr(specification, field.name), (dict, list, set))
        for field in fields(specification)
    )


def test_runtime_and_external_authority_are_structurally_absent() -> None:
    specification_fields = {field.name for field in fields(_specification())}
    intent_fields = {field.name for field in fields(_intent())}
    aggregate_fields = {field.name for field in fields(_authorized())}
    forbidden = {
        "runner_state",
        "runtime_epoch",
        "desired_state",
        "heartbeat",
        "lease",
        "process_id",
        "worker_id",
        "raw_data",
        "filesystem_state",
        "plugin_availability",
        "current_authorization_state",
        "current_profile_state",
        "current_mandate_state",
        "current_simulation_state",
        "authorized_capital",
    }
    assert forbidden.isdisjoint(specification_fields)
    assert forbidden.isdisjoint(intent_fields)
    assert forbidden.isdisjoint(aggregate_fields)


def test_contract_constants_are_exact() -> None:
    assert OPERATIONAL_PAPER_SESSION_ACTIVATION_SCHEMA_VERSION == 1
    assert OPERATIONAL_PAPER_SESSION_ACTIVATION_CONTRACT_VERSION == 1
    assert OPERATIONAL_PAPER_SESSION_ACTIVATION_CREATE_CONTRACT_VERSION == 1
    assert MAX_OPERATIONAL_PAPER_SESSION_ACTIVATION_IDEMPOTENCY_KEY_LENGTH == 128
