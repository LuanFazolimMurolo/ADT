"""Pure-domain tests for official operational paper-capital eras."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

import app.operational_paper_capital_eras as public_contract
from app.operational_paper_capital_eras import (
    MAX_OPERATIONAL_PAPER_CAPITAL_ERA_IDEMPOTENCY_KEY_LENGTH,
    OPERATIONAL_PAPER_CAPITAL_ERA_DESIGNATION_CONTRACT_VERSION,
    OPERATIONAL_PAPER_CAPITAL_ERA_SCHEMA_VERSION,
    InvalidOperationalPaperCapitalEraSpecificationError,
    OperationalPaperCapitalEra,
    OperationalPaperCapitalEraBoundsExceededError,
    OperationalPaperCapitalEraChecksumMismatchError,
    OperationalPaperCapitalEraDesignationIntent,
    OperationalPaperCapitalEraSpecification,
    build_operational_paper_capital_era_specification,
    operational_paper_capital_era_designation_intent_fingerprint,
    operational_paper_capital_era_specification_checksum,
    operational_paper_capital_era_specification_payload,
    operational_paper_capital_era_specifications_equal,
    validate_operational_paper_capital_era_idempotency_key,
    validate_operational_paper_capital_era_specification_checksum,
)

SIMULATION_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_SIMULATION_ID = UUID("20000000-0000-4000-8000-000000000002")
ERA_ID = UUID("30000000-0000-4000-8000-000000000003")
ACTOR_ID = UUID("40000000-0000-4000-8000-000000000004")
STARTED_AT = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
DESIGNATED_AT = STARTED_AT + timedelta(minutes=5)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _intent(**changes: object) -> OperationalPaperCapitalEraDesignationIntent:
    values: dict[str, object] = {
        "simulation_id": SIMULATION_ID,
    }
    values.update(changes)
    return OperationalPaperCapitalEraDesignationIntent(**values)  # type: ignore[arg-type]


def _spec(**changes: object) -> OperationalPaperCapitalEraSpecification:
    values: dict[str, object] = {
        "schema_version": OPERATIONAL_PAPER_CAPITAL_ERA_SCHEMA_VERSION,
        "designation_contract_version": OPERATIONAL_PAPER_CAPITAL_ERA_DESIGNATION_CONTRACT_VERSION,
        "simulation_id": SIMULATION_ID,
        "currency": "USDT",
        "initial_capital": Decimal("10000.00000000"),
        "simulation_started_at": STARTED_AT,
    }
    values.update(changes)
    return OperationalPaperCapitalEraSpecification(**values)  # type: ignore[arg-type]


def _era(**changes: object) -> OperationalPaperCapitalEra:
    specification = _spec()
    checksum = operational_paper_capital_era_specification_checksum(specification)
    intent = _intent()

    values: dict[str, object] = {
        "era_id": ERA_ID,
        "schema_version": specification.schema_version,
        "designation_contract_version": specification.designation_contract_version,
        "simulation_id": specification.simulation_id,
        "currency": specification.currency,
        "initial_capital": specification.initial_capital,
        "simulation_started_at": specification.simulation_started_at,
        "era_checksum": checksum,
        "designated_by": ACTOR_ID,
        "designated_at": DESIGNATED_AT,
        "designation_idempotency_key": "era:designate:1",
        "designation_intent_fingerprint": (
            operational_paper_capital_era_designation_intent_fingerprint(intent)
        ),
    }
    values.update(changes)
    return OperationalPaperCapitalEra(**values)  # type: ignore[arg-type]


def test_public_contract_contains_no_private_exports() -> None:
    assert len(public_contract.__all__) == len(set(public_contract.__all__))
    assert all(not name.startswith("_") for name in public_contract.__all__)
    assert all(hasattr(public_contract, name) for name in public_contract.__all__)


def test_specification_shape_is_exact_and_has_no_balance_authority() -> None:
    names = tuple(field.name for field in fields(OperationalPaperCapitalEraSpecification))

    assert names == (
        "schema_version",
        "designation_contract_version",
        "simulation_id",
        "currency",
        "initial_capital",
        "simulation_started_at",
    )

    assert "current_balance" not in names
    assert "available_capital" not in names
    assert "reserved_capital" not in names
    assert "equity" not in names


def test_specification_payload_is_exact_and_checksum_is_deterministic() -> None:
    specification = _spec()

    payload = operational_paper_capital_era_specification_payload(specification)

    assert payload == {
        "schema_version": OPERATIONAL_PAPER_CAPITAL_ERA_SCHEMA_VERSION,
        "designation_contract_version": (
            OPERATIONAL_PAPER_CAPITAL_ERA_DESIGNATION_CONTRACT_VERSION
        ),
        "simulation_id": str(SIMULATION_ID),
        "currency": "USDT",
        "initial_capital": Decimal("10000.00000000"),
        "simulation_started_at": STARTED_AT.isoformat(),
    }

    first = operational_paper_capital_era_specification_checksum(specification)
    second = operational_paper_capital_era_specification_checksum(_spec())

    assert first == second
    assert SHA256.fullmatch(first)


def test_specification_checksum_changes_with_each_mutable_dimension() -> None:
    baseline = _spec()
    baseline_checksum = operational_paper_capital_era_specification_checksum(
        baseline
    )

    variants = (
        replace(baseline, simulation_id=OTHER_SIMULATION_ID),
        replace(baseline, currency="BRL"),
        replace(baseline, initial_capital=Decimal("9999.00000000")),
        replace(
            baseline,
            simulation_started_at=STARTED_AT + timedelta(seconds=1),
        ),
    )

    for variant in variants:
        assert operational_paper_capital_era_specification_checksum(
            variant
        ) != baseline_checksum
        assert not operational_paper_capital_era_specifications_equal(
            baseline,
            variant,
        )


def test_designation_intent_shape_is_minimal() -> None:
    names = tuple(
        field.name
        for field in fields(OperationalPaperCapitalEraDesignationIntent)
    )

    assert names == ("simulation_id",)


def test_designation_intent_fingerprint_is_deterministic_and_identity_sensitive() -> None:
    baseline = _intent()
    same = _intent()
    different = _intent(simulation_id=OTHER_SIMULATION_ID)

    first = operational_paper_capital_era_designation_intent_fingerprint(
        baseline
    )
    second = operational_paper_capital_era_designation_intent_fingerprint(
        same
    )
    changed = operational_paper_capital_era_designation_intent_fingerprint(
        different
    )

    assert first == second
    assert first != changed
    assert SHA256.fullmatch(first)
    assert SHA256.fullmatch(changed)


def test_builder_uses_intent_identity_and_canonicalizes_authoritative_values() -> None:
    intent = _intent(simulation_id=OTHER_SIMULATION_ID)

    specification = build_operational_paper_capital_era_specification(
        intent,
        currency=" usdt ",
        initial_capital=Decimal("10000.00000000"),
        simulation_started_at=STARTED_AT,
    )

    assert specification.schema_version == (
        OPERATIONAL_PAPER_CAPITAL_ERA_SCHEMA_VERSION
    )
    assert specification.designation_contract_version == (
        OPERATIONAL_PAPER_CAPITAL_ERA_DESIGNATION_CONTRACT_VERSION
    )
    assert specification.simulation_id == OTHER_SIMULATION_ID
    assert specification.currency == "USDT"
    assert specification.initial_capital == Decimal("10000.00000000")
    assert specification.simulation_started_at == STARTED_AT


@pytest.mark.parametrize(
    "invalid_capital",
    (
        True,
        1.0,
        Decimal("0"),
        Decimal("-1.00000000"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("1.000000001"),
    ),
)
def test_initial_capital_rejects_invalid_numeric_values(
    invalid_capital: object,
) -> None:
    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        build_operational_paper_capital_era_specification(
            _intent(),
            currency="USDT",
            initial_capital=invalid_capital,
            simulation_started_at=STARTED_AT,
        )


def test_initial_capital_rejects_value_above_domain_bound() -> None:
    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        build_operational_paper_capital_era_specification(
            _intent(),
            currency="USDT",
            initial_capital=Decimal("1000000000000.00000000"),
            simulation_started_at=STARTED_AT,
        )


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    (
        (
            {"simulation_id": UUID(int=0)},
            InvalidOperationalPaperCapitalEraSpecificationError,
        ),
        (
            {"schema_version": 0},
            InvalidOperationalPaperCapitalEraSpecificationError,
        ),
        (
            {"schema_version": True},
            InvalidOperationalPaperCapitalEraSpecificationError,
        ),
        (
            {"designation_contract_version": 0},
            InvalidOperationalPaperCapitalEraSpecificationError,
        ),
        (
            {"designation_contract_version": True},
            InvalidOperationalPaperCapitalEraSpecificationError,
        ),
    ),
)
def test_specification_rejects_invalid_identity_and_versions(
    changes: dict[str, object],
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        _spec(**changes)


@pytest.mark.parametrize(
    "invalid_currency",
    (
        "",
        "   ",
        "USD\x00T",
        "US DT",
        "US/DT",
        123,
    ),
)
def test_specification_rejects_invalid_currency(
    invalid_currency: object,
) -> None:
    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        _spec(currency=invalid_currency)


def test_specification_canonicalizes_currency() -> None:
    specification = _spec(currency=" usdt ")

    assert specification.currency == "USDT"


def test_specification_requires_strict_utc_timestamp() -> None:
    from datetime import timezone

    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        _spec(
            simulation_started_at=datetime(
                2026,
                9,
                11,
                12,
                0,
            )
        )

    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        _spec(
            simulation_started_at=datetime(
                2026,
                9,
                11,
                12,
                0,
                tzinfo=timezone(timedelta(hours=1)),
            )
        )


def test_era_shape_is_exact_and_has_no_financial_or_lifecycle_authority() -> None:
    names = tuple(field.name for field in fields(OperationalPaperCapitalEra))

    assert names == (
        "era_id",
        "schema_version",
        "designation_contract_version",
        "simulation_id",
        "currency",
        "initial_capital",
        "simulation_started_at",
        "era_checksum",
        "designated_by",
        "designated_at",
        "designation_idempotency_key",
        "designation_intent_fingerprint",
    )

    assert "current_balance" not in names
    assert "available_capital" not in names
    assert "state" not in names
    assert "record_version" not in names


def test_era_is_frozen_and_slotted() -> None:
    era = _era()

    assert not hasattr(era, "__dict__")

    with pytest.raises(FrozenInstanceError):
        era.currency = "BRL"  # type: ignore[misc]


def test_era_preserves_designation_provenance() -> None:
    era = _era()

    assert era.era_id == ERA_ID
    assert era.simulation_id == SIMULATION_ID
    assert era.designated_by == ACTOR_ID
    assert era.designated_at == DESIGNATED_AT
    assert era.designation_idempotency_key == "era:designate:1"
    assert SHA256.fullmatch(era.era_checksum)
    assert SHA256.fullmatch(era.designation_intent_fingerprint)


def test_era_rejects_checksum_mismatch() -> None:
    valid = _era()
    replacement = "0" if valid.era_checksum[0] != "0" else "1"
    bad_checksum = replacement + valid.era_checksum[1:]

    with pytest.raises(OperationalPaperCapitalEraChecksumMismatchError):
        _era(era_checksum=bad_checksum)


def test_era_rejects_designation_before_simulation_start() -> None:
    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        _era(designated_at=STARTED_AT - timedelta(seconds=1))


@pytest.mark.parametrize(
    "changes",
    (
        {"designated_by": UUID(int=0)},
        {"designation_intent_fingerprint": "not-a-sha256"},
    ),
)
def test_era_rejects_invalid_provenance(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        _era(**changes)


def test_era_rejects_oversized_idempotency_key() -> None:
    with pytest.raises(OperationalPaperCapitalEraBoundsExceededError):
        _era(
            designation_idempotency_key=(
                "x"
                * (
                    MAX_OPERATIONAL_PAPER_CAPITAL_ERA_IDEMPOTENCY_KEY_LENGTH
                    + 1
                )
            )
        )


def test_specification_checksum_validator_accepts_exact_checksum() -> None:
    specification = _spec()
    checksum = operational_paper_capital_era_specification_checksum(
        specification
    )

    validated = validate_operational_paper_capital_era_specification_checksum(
        specification,
        checksum,
    )

    assert validated == specification


def test_specifications_equal_for_equivalent_canonical_values() -> None:
    left = _spec(currency=" usdt ")
    right = _spec(currency="USDT")

    assert operational_paper_capital_era_specifications_equal(left, right)


def test_idempotency_key_rejects_empty_value() -> None:
    with pytest.raises(OperationalPaperCapitalEraBoundsExceededError):
        validate_operational_paper_capital_era_idempotency_key("")


def test_idempotency_key_rejects_unsafe_token() -> None:
    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        validate_operational_paper_capital_era_idempotency_key("bad key")


def test_idempotency_key_accepts_exact_maximum_length() -> None:
    value = "x" * MAX_OPERATIONAL_PAPER_CAPITAL_ERA_IDEMPOTENCY_KEY_LENGTH

    assert validate_operational_paper_capital_era_idempotency_key(value) == value


def test_era_rejects_zero_identity() -> None:
    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        _era(era_id=UUID(int=0))
