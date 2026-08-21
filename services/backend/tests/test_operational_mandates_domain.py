"""Deterministic tests for the pure operational-mandate domain."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.market_data.domain import Exchange, MarketType, TradingPair
from app.operational_mandates import (
    MAX_OPERATIONAL_MANDATE_DESCRIPTION_LENGTH,
    MAX_OPERATIONAL_MANDATE_IDEMPOTENCY_KEY_LENGTH,
    MAX_OPERATIONAL_MANDATE_INSTRUMENTS,
    MAX_OPERATIONAL_MANDATE_NAME_LENGTH,
    OPERATIONAL_MANDATE_SPEC_SCHEMA_VERSION,
    InvalidOperationalMandateSpecificationError,
    OperationalMandate,
    OperationalMandateBoundsExceededError,
    OperationalMandateChecksumMismatchError,
    OperationalMandateInstrument,
    OperationalMandateRevision,
    OperationalMandateSpecification,
    OperationalMandateState,
    OperationalMandateStateTransitionConflictError,
    UnsupportedOperationalMandateCapabilityError,
    is_operational_mandate_transition_allowed,
    operational_mandate_create_request_fingerprint,
    operational_mandate_specification_bytes,
    operational_mandate_specification_checksum,
    operational_mandate_specification_payload,
    operational_mandate_specifications_equal,
    require_operational_mandate_transition,
    validate_operational_mandate_idempotency_key,
)
from app.operational_mandates.errors import (
    OperationalMandateIdempotencyConflictError,
    OperationalMandateNotFoundError,
    OperationalMandateRecordVersionConflictError,
    OperationalMandateRevisionConflictError,
)

MANDATE_ID = UUID("10000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("20000000-0000-4000-8000-000000000002")
SECOND_ACTOR_ID = UUID("30000000-0000-4000-8000-000000000003")
CREATED_AT = datetime(2026, 8, 21, 12, tzinfo=UTC)
APPROVED_AT = CREATED_AT + timedelta(minutes=1)
ARCHIVED_AT = CREATED_AT + timedelta(minutes=2)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _instrument(
    base: str = "BTC",
    quote: str = "USDT",
    *,
    market_type: MarketType = MarketType.SPOT,
) -> OperationalMandateInstrument:
    return OperationalMandateInstrument(
        exchange=Exchange.BINANCE,
        market_type=market_type,
        pair=TradingPair(base, quote),
    )


def _specification(
    *,
    name: str = "Primary mandate",
    description: str = "Controlled Binance Spot authority.",
    instruments: tuple[OperationalMandateInstrument, ...] | None = None,
) -> OperationalMandateSpecification:
    return OperationalMandateSpecification(
        schema_version=OPERATIONAL_MANDATE_SPEC_SCHEMA_VERSION,
        name=name,
        description=description,
        instruments=instruments or (_instrument(),),
    )


def _aggregate(**changes: object) -> OperationalMandate:
    values: dict[str, object] = {
        "mandate_id": MANDATE_ID,
        "state": OperationalMandateState.DRAFT,
        "current_revision": 1,
        "record_version": 1,
        "approved_revision": None,
        "approved_checksum": None,
        "created_by": ACTOR_ID,
        "created_at": CREATED_AT,
        "approved_by": None,
        "approved_at": None,
        "archived_by": None,
        "archived_at": None,
        "create_idempotency_key": "mandate-create:1",
        "create_request_fingerprint": "a" * 64,
    }
    values.update(changes)
    return OperationalMandate(**values)  # type: ignore[arg-type]


def test_binance_spot_canonical_instrument_is_accepted() -> None:
    instrument = _instrument("btc", "usdt")

    assert instrument.canonical_key == ("binance", "spot", "BTC", "USDT")
    assert {field.name for field in fields(instrument)} == {
        "exchange",
        "market_type",
        "pair",
    }


@pytest.mark.parametrize(
    "market_type",
    [MarketType.FOREX, MarketType.EQUITY, MarketType.FUTURES],
)
def test_unsupported_market_families_are_rejected(market_type: MarketType) -> None:
    with pytest.raises(UnsupportedOperationalMandateCapabilityError):
        _specification(instruments=(_instrument(market_type=market_type),))


def test_corrupted_frozen_pair_is_revalidated() -> None:
    pair = TradingPair("BTC", "USDT")
    object.__setattr__(pair, "base", "btc")

    with pytest.raises(InvalidOperationalMandateSpecificationError):
        OperationalMandateInstrument(Exchange.BINANCE, MarketType.SPOT, pair)


@pytest.mark.parametrize("length", [1, MAX_OPERATIONAL_MANDATE_NAME_LENGTH])
def test_name_valid_bounds_are_accepted(length: int) -> None:
    assert _specification(name="N" * length).name == "N" * length


@pytest.mark.parametrize("name", ["", " " * 3, "N" * 121])
def test_name_invalid_bounds_are_rejected(name: str) -> None:
    with pytest.raises(OperationalMandateBoundsExceededError):
        _specification(name=name)


@pytest.mark.parametrize("length", [0, MAX_OPERATIONAL_MANDATE_DESCRIPTION_LENGTH])
def test_description_valid_bounds_are_accepted(length: int) -> None:
    assert _specification(description="D" * length).description == "D" * length


def test_description_over_limit_is_rejected() -> None:
    with pytest.raises(OperationalMandateBoundsExceededError):
        _specification(description="D" * 1001)


def test_zero_instruments_is_rejected() -> None:
    with pytest.raises(OperationalMandateBoundsExceededError):
        OperationalMandateSpecification(1, "Name", "", ())


def test_one_hundred_instruments_are_accepted() -> None:
    instruments = tuple(_instrument(f"ASSET{index}") for index in range(100))

    assert len(_specification(instruments=instruments).instruments) == 100


def test_input_bound_is_checked_before_deduplication() -> None:
    duplicate_input = tuple(_instrument() for _ in range(MAX_OPERATIONAL_MANDATE_INSTRUMENTS + 1))

    with pytest.raises(OperationalMandateBoundsExceededError):
        _specification(instruments=duplicate_input)


def test_nfc_equivalent_text_normalizes_identically() -> None:
    decomposed = "Cafe\u0301 mandate"
    composed = unicodedata.normalize("NFC", decomposed)

    assert _specification(name=decomposed) == _specification(name=composed)


def test_line_endings_and_outer_whitespace_normalize_deterministically() -> None:
    first = _specification(name="  Name  ", description="  Line 1\r\nLine 2\r  ")
    second = _specification(name="Name", description="Line 1\nLine 2\n")

    assert first.name == "Name"
    assert first.description == "Line 1\nLine 2"
    assert first == second


def test_internal_whitespace_remains_semantically_significant() -> None:
    first = _specification(name="Alpha  Mandate")
    second = _specification(name="Alpha Mandate")

    assert not operational_mandate_specifications_equal(first, second)


@pytest.mark.parametrize(
    ("name", "description"),
    [("Invalid\x00name", ""), ("Name", "Invalid\x00description")],
)
def test_nul_in_human_text_is_rejected(name: str, description: str) -> None:
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        _specification(name=name, description=description)


def test_instrument_input_order_and_duplicates_do_not_change_semantics() -> None:
    btc = _instrument("BTC")
    eth = _instrument("ETH")
    first = _specification(instruments=(eth, btc, eth))
    second = _specification(instruments=(btc, eth))

    assert first.instruments == (btc, eth)
    assert first == second
    assert operational_mandate_specifications_equal(first, second)


def test_canonical_payload_has_exact_semantic_shape() -> None:
    payload = operational_mandate_specification_payload(_specification())

    assert payload == {
        "schema_version": 1,
        "name": "Primary mandate",
        "description": "Controlled Binance Spot authority.",
        "instruments": [
            {
                "exchange": "binance",
                "market_type": "spot",
                "base": "BTC",
                "quote": "USDT",
            }
        ],
    }
    assert operational_mandate_specification_bytes(_specification()).startswith(b'{"description"')


def test_checksum_is_stable_lowercase_sha256() -> None:
    checksum = operational_mandate_specification_checksum(_specification())

    assert SHA256_PATTERN.fullmatch(checksum)
    assert checksum == operational_mandate_specification_checksum(_specification())


def test_checksum_ignores_order_duplicates_and_nfc_representation() -> None:
    btc = _instrument("BTC")
    eth = _instrument("ETH")
    first = _specification(name="Cafe\u0301", instruments=(eth, btc, eth))
    second = _specification(name="Café", instruments=(btc, eth))

    assert operational_mandate_specification_checksum(
        first
    ) == operational_mandate_specification_checksum(second)


@pytest.mark.parametrize(
    "changed",
    [
        _specification(name="Changed name"),
        _specification(description="Changed description"),
        _specification(instruments=(_instrument("ETH"),)),
    ],
)
def test_semantic_change_changes_checksum(changed: OperationalMandateSpecification) -> None:
    assert operational_mandate_specification_checksum(
        changed
    ) != operational_mandate_specification_checksum(_specification())


@pytest.mark.parametrize(
    "key",
    ["request-1", "A", "A" * MAX_OPERATIONAL_MANDATE_IDEMPOTENCY_KEY_LENGTH],
)
def test_safe_idempotency_keys_are_preserved_exactly(key: str) -> None:
    assert validate_operational_mandate_idempotency_key(key) == key


@pytest.mark.parametrize("key", ["", "A" * 129])
def test_idempotency_key_bounds_are_rejected(key: str) -> None:
    with pytest.raises(OperationalMandateBoundsExceededError):
        validate_operational_mandate_idempotency_key(key)


@pytest.mark.parametrize(
    "key",
    [" leading", "trailing ", "contains space", "unsafe/value", True, b"bytes", 42],
)
def test_invalid_idempotency_key_is_rejected_without_normalization(key: object) -> None:
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        validate_operational_mandate_idempotency_key(key)


def test_create_fingerprint_is_deterministic_and_distinct_from_checksum() -> None:
    specification = _specification()
    fingerprint = operational_mandate_create_request_fingerprint(specification)

    assert SHA256_PATTERN.fullmatch(fingerprint)
    assert fingerprint == operational_mandate_create_request_fingerprint(specification)
    assert fingerprint != operational_mandate_specification_checksum(specification)


def test_create_fingerprint_tracks_semantics() -> None:
    first = _specification(name="Cafe\u0301", instruments=(_instrument("ETH"), _instrument()))
    equivalent = _specification(name="Café", instruments=(_instrument(), _instrument("ETH")))
    changed = _specification(name="Different")

    assert operational_mandate_create_request_fingerprint(
        first
    ) == operational_mandate_create_request_fingerprint(equivalent)
    assert operational_mandate_create_request_fingerprint(
        first
    ) != operational_mandate_create_request_fingerprint(changed)


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (OperationalMandateState.DRAFT, OperationalMandateState.DRAFT, False),
        (OperationalMandateState.DRAFT, OperationalMandateState.APPROVED, True),
        (OperationalMandateState.DRAFT, OperationalMandateState.ARCHIVED, True),
        (OperationalMandateState.APPROVED, OperationalMandateState.DRAFT, False),
        (OperationalMandateState.APPROVED, OperationalMandateState.APPROVED, False),
        (OperationalMandateState.APPROVED, OperationalMandateState.ARCHIVED, True),
        (OperationalMandateState.ARCHIVED, OperationalMandateState.DRAFT, False),
        (OperationalMandateState.ARCHIVED, OperationalMandateState.APPROVED, False),
        (OperationalMandateState.ARCHIVED, OperationalMandateState.ARCHIVED, False),
    ],
)
def test_lifecycle_matrix(
    current: OperationalMandateState,
    target: OperationalMandateState,
    allowed: bool,
) -> None:
    assert is_operational_mandate_transition_allowed(current, target) is allowed
    if allowed:
        require_operational_mandate_transition(current, target)
    else:
        with pytest.raises(OperationalMandateStateTransitionConflictError):
            require_operational_mandate_transition(current, target)


def test_valid_revision_one_is_accepted() -> None:
    specification = _specification()
    revision = OperationalMandateRevision(
        mandate_id=MANDATE_ID,
        revision=1,
        specification=specification,
        specification_checksum=operational_mandate_specification_checksum(specification),
        created_by=ACTOR_ID,
        created_at=CREATED_AT,
    )

    assert revision.revision == 1
    assert revision.created_at.tzinfo is UTC


@pytest.mark.parametrize("revision", [True, 0, -1])
def test_invalid_revision_number_is_rejected(revision: object) -> None:
    specification = _specification()
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        OperationalMandateRevision(
            mandate_id=MANDATE_ID,
            revision=revision,  # type: ignore[arg-type]
            specification=specification,
            specification_checksum=operational_mandate_specification_checksum(specification),
            created_by=ACTOR_ID,
            created_at=CREATED_AT,
        )


def test_revision_rejects_invalid_or_mismatched_checksum() -> None:
    specification = _specification()
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        OperationalMandateRevision(MANDATE_ID, 1, specification, "INVALID", ACTOR_ID, CREATED_AT)
    with pytest.raises(OperationalMandateChecksumMismatchError):
        OperationalMandateRevision(MANDATE_ID, 1, specification, "0" * 64, ACTOR_ID, CREATED_AT)


@pytest.mark.parametrize(
    ("mandate_id", "created_by"),
    [("not-uuid", ACTOR_ID), (MANDATE_ID, "not-uuid")],
)
def test_revision_rejects_non_uuid_ids(mandate_id: object, created_by: object) -> None:
    specification = _specification()
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        OperationalMandateRevision(
            mandate_id=mandate_id,  # type: ignore[arg-type]
            revision=1,
            specification=specification,
            specification_checksum=operational_mandate_specification_checksum(specification),
            created_by=created_by,  # type: ignore[arg-type]
            created_at=CREATED_AT,
        )


@pytest.mark.parametrize(
    "created_at",
    [
        datetime(2026, 8, 21, 12),
        datetime(2026, 8, 21, 9, tzinfo=timezone(timedelta(hours=-3))),
    ],
)
def test_revision_rejects_naive_or_non_utc_timestamp(created_at: datetime) -> None:
    specification = _specification()
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        OperationalMandateRevision(
            MANDATE_ID,
            1,
            specification,
            operational_mandate_specification_checksum(specification),
            ACTOR_ID,
            created_at,
        )


def test_valid_aggregate_state_shapes_are_accepted() -> None:
    draft = _aggregate()
    approved = _aggregate(
        state=OperationalMandateState.APPROVED,
        record_version=2,
        approved_revision=1,
        approved_checksum="b" * 64,
        approved_by=SECOND_ACTOR_ID,
        approved_at=APPROVED_AT,
    )
    archived_draft = _aggregate(
        state=OperationalMandateState.ARCHIVED,
        record_version=2,
        archived_by=SECOND_ACTOR_ID,
        archived_at=ARCHIVED_AT,
    )
    archived_approved = _aggregate(
        state=OperationalMandateState.ARCHIVED,
        record_version=3,
        approved_revision=1,
        approved_checksum="b" * 64,
        approved_by=SECOND_ACTOR_ID,
        approved_at=APPROVED_AT,
        archived_by=ACTOR_ID,
        archived_at=ARCHIVED_AT,
    )

    assert draft.state is OperationalMandateState.DRAFT
    assert approved.state is OperationalMandateState.APPROVED
    assert archived_draft.approved_revision is None
    assert archived_approved.approved_revision == 1


@pytest.mark.parametrize(
    "changes",
    [
        {
            "approved_revision": 1,
            "approved_checksum": "b" * 64,
            "approved_by": ACTOR_ID,
            "approved_at": APPROVED_AT,
        },
        {"archived_by": ACTOR_ID, "archived_at": ARCHIVED_AT},
        {"state": OperationalMandateState.APPROVED},
        {
            "state": OperationalMandateState.APPROVED,
            "approved_revision": 1,
            "approved_checksum": "b" * 64,
            "approved_by": ACTOR_ID,
            "approved_at": APPROVED_AT,
            "archived_by": ACTOR_ID,
            "archived_at": ARCHIVED_AT,
        },
        {"state": OperationalMandateState.ARCHIVED},
        {"approved_revision": 1},
        {"archived_by": ACTOR_ID},
    ],
)
def test_invalid_aggregate_state_or_partial_metadata_is_rejected(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        _aggregate(**changes)


def test_approved_revision_must_equal_current_revision() -> None:
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        _aggregate(
            state=OperationalMandateState.APPROVED,
            current_revision=2,
            approved_revision=1,
            approved_checksum="b" * 64,
            approved_by=ACTOR_ID,
            approved_at=APPROVED_AT,
        )


@pytest.mark.parametrize("field", ["current_revision", "record_version"])
@pytest.mark.parametrize("value", [True, 0, -1])
def test_aggregate_versions_must_be_positive_exact_ints(field: str, value: object) -> None:
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        _aggregate(**{field: value})


def test_aggregate_revalidates_fingerprint_and_idempotency_key() -> None:
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        _aggregate(create_request_fingerprint="A" * 64)
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        _aggregate(create_idempotency_key="invalid key")


@pytest.mark.parametrize(
    "changes",
    [
        {"created_at": datetime(2026, 8, 21, 12)},
        {
            "state": OperationalMandateState.APPROVED,
            "approved_revision": 1,
            "approved_checksum": "b" * 64,
            "approved_by": ACTOR_ID,
            "approved_at": CREATED_AT - timedelta(seconds=1),
        },
        {
            "state": OperationalMandateState.ARCHIVED,
            "archived_by": ACTOR_ID,
            "archived_at": CREATED_AT - timedelta(seconds=1),
        },
        {
            "state": OperationalMandateState.ARCHIVED,
            "approved_revision": 1,
            "approved_checksum": "b" * 64,
            "approved_by": ACTOR_ID,
            "approved_at": APPROVED_AT,
            "archived_by": ACTOR_ID,
            "archived_at": CREATED_AT,
        },
    ],
)
def test_aggregate_rejects_timestamp_contract_violations(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidOperationalMandateSpecificationError):
        _aggregate(**changes)


def test_error_taxonomy_has_stable_codes_and_statuses() -> None:
    assert OperationalMandateNotFoundError.code == "operational_mandate_not_found"
    assert OperationalMandateNotFoundError.status_code == 404
    assert OperationalMandateRevisionConflictError.status_code == 409
    assert OperationalMandateRecordVersionConflictError.status_code == 409
    assert OperationalMandateIdempotencyConflictError.status_code == 409
    assert InvalidOperationalMandateSpecificationError.status_code == 400
