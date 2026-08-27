"""Pure-domain tests for operational paper-capital authorizations."""

from __future__ import annotations

import re
from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

import app.operational_paper_capital_authorizations as public_contract
from app.operational_paper_capital_authorizations import (
    MAX_OPERATIONAL_PAPER_CAPITAL_AUTHORIZED_CAPITAL,
    OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_QUANTUM,
    OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
    InvalidOperationalPaperCapitalAuthorizationSpecificationError,
    OperationalPaperCapitalAuthorizationBoundsExceededError,
    OperationalPaperCapitalAuthorizationChecksumMismatchError,
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalAuthorizationSpecification,
    OperationalPaperCapitalAuthorizationState,
    OperationalPaperCapitalAuthorizationStateTransitionConflictError,
    build_operational_paper_capital_authorization_specification,
    is_operational_paper_capital_authorization_transition_allowed,
    operational_paper_capital_authorization_create_intent_fingerprint,
    operational_paper_capital_authorization_specification_bytes,
    operational_paper_capital_authorization_specification_checksum,
    operational_paper_capital_authorization_specification_payload,
    operational_paper_capital_authorization_specifications_equal,
    require_operational_paper_capital_authorization_transition,
    validate_operational_paper_capital_authorization_idempotency_key,
    validate_operational_paper_capital_authorization_specification_checksum,
)

PROFILE_ID = UUID("10000000-0000-4000-8000-000000000001")
SIMULATION_ID = UUID("20000000-0000-4000-8000-000000000002")
OTHER_SIMULATION_ID = UUID("30000000-0000-4000-8000-000000000003")
ACTOR_ID = UUID("40000000-0000-4000-8000-000000000004")
NOW = datetime(2026, 8, 27, 18, tzinfo=UTC)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
POSTGRESQL_BIGINT_MAX = (1 << 63) - 1


def _binding(**changes: object) -> OperationalPaperCapitalAuthorizationProfileBinding:
    values: dict[str, object] = {
        "profile_id": PROFILE_ID,
        "approved_revision": 2,
        "specification_checksum": "a" * 64,
    }
    values.update(changes)
    return OperationalPaperCapitalAuthorizationProfileBinding(**values)  # type: ignore[arg-type]


def _spec(**changes: object) -> OperationalPaperCapitalAuthorizationSpecification:
    values: dict[str, object] = {
        "schema_version": OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
        "profile_binding": _binding(),
        "simulation_id": SIMULATION_ID,
        "quote_asset": "USDT",
        "authorized_capital": Decimal("1250.00000000"),
    }
    values.update(changes)
    return OperationalPaperCapitalAuthorizationSpecification(**values)  # type: ignore[arg-type]


def _intent(**changes: object) -> OperationalPaperCapitalAuthorizationCreateIntent:
    values: dict[str, object] = {
        "profile_binding": _binding(),
        "simulation_id": SIMULATION_ID,
        "quote_asset": "USDT",
        "authorized_capital": Decimal("1250.00000000"),
    }
    values.update(changes)
    return OperationalPaperCapitalAuthorizationCreateIntent(**values)  # type: ignore[arg-type]


def test_public_contract_contains_no_private_exports() -> None:
    assert len(public_contract.__all__) == len(set(public_contract.__all__))
    assert all(not name.startswith("_") for name in public_contract.__all__)


def test_lifecycle_is_exact_and_revoked_is_terminal() -> None:
    states = tuple(OperationalPaperCapitalAuthorizationState)
    assert states == (
        OperationalPaperCapitalAuthorizationState.AUTHORIZED,
        OperationalPaperCapitalAuthorizationState.REVOKED,
    )
    assert is_operational_paper_capital_authorization_transition_allowed(
        OperationalPaperCapitalAuthorizationState.AUTHORIZED,
        OperationalPaperCapitalAuthorizationState.REVOKED,
    )
    require_operational_paper_capital_authorization_transition(
        OperationalPaperCapitalAuthorizationState.AUTHORIZED,
        OperationalPaperCapitalAuthorizationState.REVOKED,
    )
    for current, target in (
        (
            OperationalPaperCapitalAuthorizationState.AUTHORIZED,
            OperationalPaperCapitalAuthorizationState.AUTHORIZED,
        ),
        (
            OperationalPaperCapitalAuthorizationState.REVOKED,
            OperationalPaperCapitalAuthorizationState.AUTHORIZED,
        ),
        (
            OperationalPaperCapitalAuthorizationState.REVOKED,
            OperationalPaperCapitalAuthorizationState.REVOKED,
        ),
    ):
        assert not is_operational_paper_capital_authorization_transition_allowed(
            current,
            target,
        )
        with pytest.raises(OperationalPaperCapitalAuthorizationStateTransitionConflictError):
            require_operational_paper_capital_authorization_transition(
                current,
                target,
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"profile_id": UUID(int=0)},
        {"approved_revision": 0},
        {"approved_revision": True},
        {"specification_checksum": "A" * 64},
        {"specification_checksum": "bad"},
    ],
)
def test_profile_binding_rejects_invalid_evidence(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        _binding(**changes)


def test_profile_revision_matches_postgresql_bigint_width() -> None:
    assert (
        _binding(approved_revision=POSTGRESQL_BIGINT_MAX).approved_revision == POSTGRESQL_BIGINT_MAX
    )
    with pytest.raises(OperationalPaperCapitalAuthorizationBoundsExceededError):
        _binding(approved_revision=POSTGRESQL_BIGINT_MAX + 1)


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1"),
        Decimal("1.25"),
        Decimal("1.2500000000"),
        Decimal("0.00000001"),
        Decimal("999999999999.99999999"),
    ],
)
def test_authorized_capital_accepts_exact_numeric_20_8_values(
    value: Decimal,
) -> None:
    specification = _spec(authorized_capital=value)
    assert specification.authorized_capital == value


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("0.000000001"),
        Decimal("999999999999.999999999"),
        Decimal("1000000000000"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        1,
        1.0,
        "1",
    ],
)
def test_authorized_capital_rejects_non_numeric_20_8_values(
    value: object,
) -> None:
    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        _spec(authorized_capital=value)


def test_numeric_contract_constants_are_exact() -> None:
    assert OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_QUANTUM == Decimal("0.00000001")
    assert MAX_OPERATIONAL_PAPER_CAPITAL_AUTHORIZED_CAPITAL == Decimal("999999999999.99999999")


def test_quote_asset_is_canonical_uppercase() -> None:
    assert _spec(quote_asset=" usdt ").quote_asset == "USDT"
    for invalid in ("", " ", "BTC/USDT", "US DT", "A" * 33):
        with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
            _spec(quote_asset=invalid)


def test_specification_payload_and_decimal_normalization_are_deterministic() -> None:
    first = _spec(
        quote_asset="usdt",
        authorized_capital=Decimal("1250.00000000"),
    )
    second = _spec(
        quote_asset="USDT",
        authorized_capital=Decimal("1250"),
    )
    assert operational_paper_capital_authorization_specifications_equal(
        first,
        second,
    )
    assert operational_paper_capital_authorization_specification_bytes(
        first
    ) == operational_paper_capital_authorization_specification_bytes(second)
    payload = operational_paper_capital_authorization_specification_payload(first)
    assert payload == {
        "schema_version": 1,
        "profile_id": str(PROFILE_ID),
        "profile_approved_revision": 2,
        "profile_specification_checksum": "a" * 64,
        "simulation_id": str(SIMULATION_ID),
        "quote_asset": "USDT",
        "authorized_capital": Decimal("1250"),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_binding", _binding(approved_revision=3)),
        ("simulation_id", OTHER_SIMULATION_ID),
        ("quote_asset", "BRL"),
        ("authorized_capital", Decimal("1251")),
    ],
)
def test_every_authorization_semantic_dimension_changes_checksum(
    field: str,
    value: object,
) -> None:
    baseline = operational_paper_capital_authorization_specification_checksum(_spec())
    changed = operational_paper_capital_authorization_specification_checksum(
        _spec(**{field: value})
    )
    assert SHA256.fullmatch(baseline)
    assert changed != baseline


def test_checksum_validation_rejects_malformed_and_mismatch() -> None:
    specification = _spec()
    checksum = operational_paper_capital_authorization_specification_checksum(specification)
    assert (
        validate_operational_paper_capital_authorization_specification_checksum(
            specification,
            checksum,
        )
        == specification
    )
    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        validate_operational_paper_capital_authorization_specification_checksum(
            specification,
            checksum.upper(),
        )
    with pytest.raises(OperationalPaperCapitalAuthorizationChecksumMismatchError):
        validate_operational_paper_capital_authorization_specification_checksum(
            specification,
            "0" * 64,
        )


def test_create_intent_builds_same_authorization_semantics() -> None:
    intent = _intent()
    specification = build_operational_paper_capital_authorization_specification(intent)
    assert specification == _spec()


def test_create_intent_fingerprint_is_deterministic_and_tracks_all_intent() -> None:
    baseline = operational_paper_capital_authorization_create_intent_fingerprint(_intent())
    assert SHA256.fullmatch(baseline)
    assert baseline == (
        operational_paper_capital_authorization_create_intent_fingerprint(_intent())
    )
    for change in (
        {"profile_binding": _binding(approved_revision=3)},
        {"simulation_id": OTHER_SIMULATION_ID},
        {"quote_asset": "BRL"},
        {"authorized_capital": Decimal("1251")},
    ):
        assert (
            operational_paper_capital_authorization_create_intent_fingerprint(_intent(**change))
            != baseline
        )


def test_create_intent_excludes_mutable_and_generated_metadata() -> None:
    names = {field.name for field in fields(_intent())}
    assert {
        "authorization_id",
        "state",
        "record_version",
        "created_by",
        "created_at",
        "revoked_by",
        "revoked_at",
        "available_capital",
        "gross_balance",
        "reserved_capital",
    }.isdisjoint(names)


@pytest.mark.parametrize("key", ["create:1", "A", "A" * 128])
def test_safe_idempotency_key_is_preserved(key: str) -> None:
    assert validate_operational_paper_capital_authorization_idempotency_key(key) == key


@pytest.mark.parametrize(
    "key",
    ["", "A" * 129, "unsafe key", "unsafe/value"],
)
def test_invalid_idempotency_key_is_rejected(key: str) -> None:
    with pytest.raises(
        (
            InvalidOperationalPaperCapitalAuthorizationSpecificationError,
            OperationalPaperCapitalAuthorizationBoundsExceededError,
        )
    ):
        validate_operational_paper_capital_authorization_idempotency_key(key)


AUTHORIZATION_ID = UUID("50000000-0000-4000-8000-000000000005")
OTHER_AUTHORIZATION_ID = UUID("60000000-0000-4000-8000-000000000006")
OTHER_ACTOR_ID = UUID("70000000-0000-4000-8000-000000000007")


def _aggregate(**changes: object) -> public_contract.OperationalPaperCapitalAuthorization:
    specification = _spec()
    values: dict[str, object] = {
        "authorization_id": AUTHORIZATION_ID,
        "schema_version": OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
        "state": OperationalPaperCapitalAuthorizationState.AUTHORIZED,
        "record_version": 1,
        "profile_binding": specification.profile_binding,
        "simulation_id": specification.simulation_id,
        "quote_asset": specification.quote_asset,
        "authorized_capital": specification.authorized_capital,
        "authorization_checksum": (
            operational_paper_capital_authorization_specification_checksum(specification)
        ),
        "created_by": ACTOR_ID,
        "created_at": NOW,
        "revoked_by": None,
        "revoked_at": None,
        "create_idempotency_key": "create:1",
        "create_intent_fingerprint": (
            operational_paper_capital_authorization_create_intent_fingerprint(_intent())
        ),
    }
    values.update(changes)
    return public_contract.OperationalPaperCapitalAuthorization(**values)  # type: ignore[arg-type]


def test_valid_authorized_and_revoked_aggregate_metadata() -> None:
    authorized = _aggregate()
    assert authorized.state is OperationalPaperCapitalAuthorizationState.AUTHORIZED
    assert authorized.revoked_by is None
    assert authorized.revoked_at is None

    revoked_at = datetime(2026, 8, 27, 18, 1, tzinfo=UTC)
    revoked = _aggregate(
        state=OperationalPaperCapitalAuthorizationState.REVOKED,
        record_version=2,
        revoked_by=OTHER_ACTOR_ID,
        revoked_at=revoked_at,
    )
    assert revoked.state is OperationalPaperCapitalAuthorizationState.REVOKED
    assert revoked.record_version == 2
    assert revoked.revoked_by == OTHER_ACTOR_ID
    assert revoked.revoked_at == revoked_at


@pytest.mark.parametrize(
    "changes",
    [
        {
            "revoked_by": OTHER_ACTOR_ID,
            "revoked_at": datetime(2026, 8, 27, 18, 1, tzinfo=UTC),
        },
        {
            "state": OperationalPaperCapitalAuthorizationState.REVOKED,
        },
        {
            "state": OperationalPaperCapitalAuthorizationState.REVOKED,
            "revoked_by": OTHER_ACTOR_ID,
        },
        {
            "state": OperationalPaperCapitalAuthorizationState.REVOKED,
            "revoked_at": datetime(2026, 8, 27, 18, 1, tzinfo=UTC),
        },
        {
            "state": OperationalPaperCapitalAuthorizationState.REVOKED,
            "revoked_by": OTHER_ACTOR_ID,
            "revoked_at": datetime(2026, 8, 27, 17, 59, tzinfo=UTC),
        },
        {"state": "AUTHORIZED"},
        {"schema_version": 2},
        {"authorization_id": UUID(int=0)},
        {"record_version": 0},
        {"record_version": True},
        {"create_intent_fingerprint": "A" * 64},
        {"create_intent_fingerprint": "bad"},
    ],
)
def test_invalid_aggregate_metadata_matrix(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        _aggregate(**changes)


def test_aggregate_record_version_matches_postgresql_bigint_width() -> None:
    aggregate = _aggregate(record_version=POSTGRESQL_BIGINT_MAX)
    assert aggregate.record_version == POSTGRESQL_BIGINT_MAX

    with pytest.raises(OperationalPaperCapitalAuthorizationBoundsExceededError):
        _aggregate(record_version=POSTGRESQL_BIGINT_MAX + 1)


def test_aggregate_requires_exact_utc_timestamps() -> None:
    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        _aggregate(created_at=datetime(2026, 8, 27, 18))

    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        _aggregate(created_at=datetime.fromisoformat("2026-08-27T15:00:00-03:00"))

    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        _aggregate(
            state=OperationalPaperCapitalAuthorizationState.REVOKED,
            revoked_by=OTHER_ACTOR_ID,
            revoked_at=datetime.fromisoformat("2026-08-27T15:01:00-03:00"),
        )


def test_aggregate_checksum_is_exact_specification_evidence() -> None:
    aggregate = _aggregate()
    specification = _spec()
    assert aggregate.authorization_checksum == (
        operational_paper_capital_authorization_specification_checksum(specification)
    )
    assert SHA256.fullmatch(aggregate.authorization_checksum)

    with pytest.raises(OperationalPaperCapitalAuthorizationChecksumMismatchError):
        replace(
            aggregate,
            authorization_checksum="0" * 64,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"profile_binding": _binding(approved_revision=3)},
        {"simulation_id": OTHER_SIMULATION_ID},
        {"quote_asset": "BRL"},
        {"authorized_capital": Decimal("1251")},
    ],
)
def test_aggregate_rejects_semantic_drift_without_new_checksum(
    changes: dict[str, object],
) -> None:
    with pytest.raises(OperationalPaperCapitalAuthorizationChecksumMismatchError):
        _aggregate(**changes)


def test_aggregate_metadata_does_not_change_authorization_checksum() -> None:
    first = _aggregate()
    second = _aggregate(
        authorization_id=OTHER_AUTHORIZATION_ID,
        record_version=7,
        created_by=OTHER_ACTOR_ID,
        create_idempotency_key="create:2",
        create_intent_fingerprint="b" * 64,
    )
    revoked = _aggregate(
        authorization_id=OTHER_AUTHORIZATION_ID,
        state=OperationalPaperCapitalAuthorizationState.REVOKED,
        record_version=2,
        revoked_by=OTHER_ACTOR_ID,
        revoked_at=datetime(2026, 8, 27, 18, 1, tzinfo=UTC),
    )

    assert first.authorization_checksum == second.authorization_checksum
    assert second.authorization_checksum == revoked.authorization_checksum


def test_corrupted_profile_binding_is_revalidated() -> None:
    binding = _binding()
    object.__setattr__(binding, "approved_revision", 0)

    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        _spec(profile_binding=binding)

    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        _intent(profile_binding=binding)


def test_checksum_helper_revalidates_corrupted_frozen_specification() -> None:
    specification = _spec()
    object.__setattr__(specification, "quote_asset", "bad asset")

    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        operational_paper_capital_authorization_specification_checksum(specification)


def test_fingerprint_helper_revalidates_corrupted_frozen_intent() -> None:
    intent = _intent()
    object.__setattr__(intent, "simulation_id", UUID(int=0))

    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        operational_paper_capital_authorization_create_intent_fingerprint(intent)


def test_canonical_identity_regression_values_are_frozen() -> None:
    assert operational_paper_capital_authorization_specification_checksum(_spec()) == (
        "ee5d4dff7abd542909752f09b41c224656c786802738b21c19f256f2591a455b"
    )
    assert operational_paper_capital_authorization_create_intent_fingerprint(_intent()) == (
        "533eb308a33c85a3637aef9787a60a14ae648188b903de702c3c1e694b284d95"
    )


def test_materialization_runtime_and_second_ledger_are_structurally_absent() -> None:
    specification_fields = {
        field.name for field in fields(OperationalPaperCapitalAuthorizationSpecification)
    }
    intent_fields = {
        field.name for field in fields(OperationalPaperCapitalAuthorizationCreateIntent)
    }
    aggregate_fields = {
        field.name for field in fields(public_contract.OperationalPaperCapitalAuthorization)
    }

    forbidden_financial_state = {
        "gross_balance",
        "reserved_capital",
        "available_capital",
        "current_balance",
        "movement_id",
        "capital_movement",
        "ledger_balance",
    }

    forbidden_runtime_state = {
        "session_id",
        "materialization_id",
        "config_path",
        "state_path",
        "runner_id",
        "collector_id",
    }

    for names in (
        specification_fields,
        intent_fields,
        aggregate_fields,
    ):
        assert forbidden_financial_state.isdisjoint(names)
        assert forbidden_runtime_state.isdisjoint(names)

    public_names = {name.lower() for name in public_contract.__all__}
    assert all("session_id" not in name for name in public_names)
    assert all("materializ" not in name for name in public_names)
    assert all("papersessionconfig" not in name for name in public_names)
    assert all("runner" not in name for name in public_names)
    assert all("collector" not in name for name in public_names)
