"""Pure settlement evidence, eligibility and exact financial contract tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import UUID

import pytest

import app.operational_paper_session_settlements as public_contract
from app.backtesting.domain import PortfolioSnapshot
from app.backtesting.serialization import canonical_json_bytes
from app.operational_paper_capital_authorizations.domain import (
    OperationalPaperCapitalAuthorization,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalAuthorizationSpecification,
    OperationalPaperCapitalAuthorizationState,
    operational_paper_capital_authorization_specification_checksum,
)
from app.operational_paper_capital_eras.domain import (
    OperationalPaperCapitalEra,
    OperationalPaperCapitalEraDesignationIntent,
    OperationalPaperCapitalEraSpecification,
    operational_paper_capital_era_designation_intent_fingerprint,
    operational_paper_capital_era_specification_checksum,
)
from app.operational_paper_session_materializations.domain import (
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationProfileBinding,
)
from app.operational_paper_session_runs.domain import (
    OperationalPaperSessionRunDesiredState,
    OperationalPaperSessionRunEpoch,
    OperationalPaperSessionRunEpochSpecification,
    OperationalPaperSessionRunEpochStartIntent,
    OperationalPaperSessionRunFailure,
    OperationalPaperSessionRunFailureCode,
    OperationalPaperSessionRunObservedState,
    operational_paper_session_run_epoch_specification_checksum,
    operational_paper_session_run_epoch_start_intent_fingerprint,
)
from app.operational_paper_session_settlements import (
    MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_AMOUNT,
    MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_IDEMPOTENCY_KEY_LENGTH,
    OPERATIONAL_PAPER_SESSION_SETTLEMENT_CONTRACT_VERSION,
    OPERATIONAL_PAPER_SESSION_SETTLEMENT_QUANTUM,
    OPERATIONAL_PAPER_SESSION_SETTLEMENT_SCHEMA_VERSION,
    InvalidOperationalPaperSessionSettlementSpecificationError,
    OperationalPaperSessionSettlement,
    OperationalPaperSessionSettlementBoundsExceededError,
    OperationalPaperSessionSettlementChecksumMismatchError,
    OperationalPaperSessionSettlementEligibilityConflictError,
    OperationalPaperSessionSettlementIntent,
    OperationalPaperSessionSettlementSpecification,
    build_operational_paper_session_settlement_specification,
    operational_paper_session_settlement_intent_fingerprint,
    operational_paper_session_settlement_specification_bytes,
    operational_paper_session_settlement_specification_checksum,
    operational_paper_session_settlement_specification_payload,
    operational_paper_session_settlement_specifications_equal,
    validate_operational_paper_session_settlement_idempotency_key,
    validate_operational_paper_session_settlement_specification_checksum,
)
from app.paper_trading.persisted_state import PaperPersistedStateBinding

SIMULATION_ID, ERA_ID, EPOCH_ID, AUTHORIZATION_ID, ACTOR_ID, MOVEMENT_ID = (
    UUID(int=index) for index in range(1, 7)
)
STARTED_AT = datetime(2026, 9, 11, 12, tzinfo=UTC)
TERMINAL_AT = STARTED_AT + timedelta(hours=1)
D = Decimal
INVALID = InvalidOperationalPaperSessionSettlementSpecificationError
ELIGIBILITY = OperationalPaperSessionSettlementEligibilityConflictError
FINANCIAL_FIELDS = (
    "initial_capital",
    "final_quote_cash",
    "realized_pnl",
    "unrealized_pnl",
    "base_quantity",
    "average_entry_price",
    "cost_basis",
    "total_fees",
    "total_slippage_cost",
    "settlement_delta",
)
SHA_FIELDS = (
    "era_checksum",
    "epoch_checksum",
    "authorization_checksum",
    "session_id",
    "config_checksum",
    "state_id",
    "state_checksum",
    "dataset_version",
    "source_checksum",
    "timeline_id",
    "timeline_content_checksum",
)
UUID_FIELDS = ("era_id", "simulation_id", "epoch_id", "authorization_id", "ledger_movement_id")
SPEC_FIELDS = (
    "schema_version",
    "settlement_contract_version",
    "era_id",
    "era_checksum",
    "simulation_id",
    "epoch_id",
    "epoch_checksum",
    "epoch_terminal_at",
    "authorization_id",
    "authorization_checksum",
    "session_id",
    "config_checksum",
    "state_id",
    "state_checksum",
    "dataset_version",
    "source_checksum",
    "timeline_id",
    "timeline_content_checksum",
    *FINANCIAL_FIELDS,
    "ledger_movement_id",
)


def _spec(**changes: object) -> OperationalPaperSessionSettlementSpecification:
    values: dict[str, object] = {
        "schema_version": 1,
        "settlement_contract_version": 1,
        "era_id": ERA_ID,
        "era_checksum": "a" * 64,
        "simulation_id": SIMULATION_ID,
        "epoch_id": EPOCH_ID,
        "epoch_checksum": "b" * 64,
        "epoch_terminal_at": TERMINAL_AT,
        "authorization_id": AUTHORIZATION_ID,
        "authorization_checksum": "c" * 64,
        "session_id": "d" * 64,
        "config_checksum": "e" * 64,
        "state_id": "f" * 64,
        "state_checksum": "1" * 64,
        "dataset_version": "2" * 64,
        "source_checksum": "3" * 64,
        "timeline_id": "4" * 64,
        "timeline_content_checksum": "5" * 64,
        "initial_capital": D("1000"),
        "final_quote_cash": D("1010"),
        "realized_pnl": D("10"),
        "unrealized_pnl": D("0"),
        "base_quantity": D("0"),
        "average_entry_price": D("0"),
        "cost_basis": D("0"),
        "total_fees": D("2"),
        "total_slippage_cost": D("3"),
        "settlement_delta": D("10"),
        "ledger_movement_id": MOVEMENT_ID,
    }
    values.update(changes)
    return OperationalPaperSessionSettlementSpecification(**values)  # type: ignore[arg-type]


def _intent(**changes: object) -> OperationalPaperSessionSettlementIntent:
    values: dict[str, object] = {"epoch_id": EPOCH_ID, "epoch_checksum": "b" * 64}
    values.update(changes)
    return OperationalPaperSessionSettlementIntent(**values)  # type: ignore[arg-type]


def _settlement(**changes: object) -> OperationalPaperSessionSettlement:
    spec = _spec()
    values: dict[str, object] = {
        "settlement_id": UUID(int=10),
        "specification": spec,
        "settlement_checksum": operational_paper_session_settlement_specification_checksum(spec),
        "settled_by": ACTOR_ID,
        "settled_at": TERMINAL_AT,
        "settle_idempotency_key": "settlement:1",
        "settle_intent_fingerprint": operational_paper_session_settlement_intent_fingerprint(
            _intent()
        ),
    }
    values.update(changes)
    return OperationalPaperSessionSettlement(**values)  # type: ignore[arg-type]


def _era(**changes: object) -> OperationalPaperCapitalEra:
    spec = OperationalPaperCapitalEraSpecification(
        1, 1, SIMULATION_ID, "USDT", D("10000"), STARTED_AT
    )
    era = OperationalPaperCapitalEra(
        ERA_ID,
        spec.schema_version,
        spec.designation_contract_version,
        spec.simulation_id,
        spec.currency,
        spec.initial_capital,
        spec.simulation_started_at,
        operational_paper_capital_era_specification_checksum(spec),
        ACTOR_ID,
        STARTED_AT,
        "era:1",
        operational_paper_capital_era_designation_intent_fingerprint(
            OperationalPaperCapitalEraDesignationIntent(SIMULATION_ID)
        ),
    )
    return replace(era, **changes)


def _authorization(**changes: object) -> OperationalPaperCapitalAuthorization:
    spec = OperationalPaperCapitalAuthorizationSpecification(
        1,
        OperationalPaperCapitalAuthorizationProfileBinding(UUID(int=20), 2, "a" * 64),
        SIMULATION_ID,
        "USDT",
        D("1000"),
    )
    authorization = OperationalPaperCapitalAuthorization(
        AUTHORIZATION_ID,
        1,
        OperationalPaperCapitalAuthorizationState.AUTHORIZED,
        1,
        spec.profile_binding,
        SIMULATION_ID,
        spec.quote_asset,
        spec.authorized_capital,
        operational_paper_capital_authorization_specification_checksum(spec),
        ACTOR_ID,
        STARTED_AT + timedelta(minutes=1),
        None,
        None,
        "authorization:1",
        "a" * 64,
    )
    return replace(authorization, **changes)


def _epoch(**changes: object) -> OperationalPaperSessionRunEpoch:
    authorization = _authorization()
    spec = OperationalPaperSessionRunEpochSpecification(
        1,
        1,
        UUID(int=30),
        "a" * 64,
        UUID(int=31),
        "b" * 64,
        OperationalPaperSessionMaterializationAuthorizationBinding(
            authorization.authorization_id, authorization.authorization_checksum
        ),
        OperationalPaperSessionMaterializationProfileBinding(UUID(int=20), 2, "a" * 64),
        OperationalPaperSessionMaterializationMandateBinding(UUID(int=40), 1, "c" * 64),
        SIMULATION_ID,
        "d" * 64,
        "e" * 64,
    )
    epoch = OperationalPaperSessionRunEpoch(
        epoch_id=EPOCH_ID,
        desired_state=OperationalPaperSessionRunDesiredState.STOPPED,
        observed_state=OperationalPaperSessionRunObservedState.STOPPED,
        record_version=3,
        fencing_token=0,
        **{field.name: getattr(spec, field.name) for field in fields(spec)},
        epoch_checksum=operational_paper_session_run_epoch_specification_checksum(spec),
        start_requested_by=ACTOR_ID,
        start_requested_at=STARTED_AT + timedelta(minutes=2),
        start_idempotency_key="start:1",
        start_intent_fingerprint=operational_paper_session_run_epoch_start_intent_fingerprint(
            OperationalPaperSessionRunEpochStartIntent(spec.activation_id, spec.activation_checksum)
        ),
        terminal_at=TERMINAL_AT,
    )
    return replace(epoch, **changes)


def _portfolio(**changes: object) -> PortfolioSnapshot:
    portfolio = PortfolioSnapshot(
        quote_cash=D("1010"),
        base_quantity=D("0"),
        average_entry_price=D("0"),
        realized_pnl=D("10"),
        unrealized_pnl=D("0"),
        total_fees=D("2"),
        total_slippage_cost=D("3"),
        equity=D("1010"),
        peak_equity=D("1010"),
        drawdown=D("0"),
    )
    return replace(portfolio, **changes)


def _binding(**changes: object) -> PaperPersistedStateBinding:
    spec = _spec()
    return replace(
        PaperPersistedStateBinding(
            **{
                field.name: getattr(spec, field.name)
                for field in fields(PaperPersistedStateBinding)
            }
        ),
        **changes,
    )


def _build(**changes: object) -> OperationalPaperSessionSettlementSpecification:
    epoch = _epoch()
    values: dict[str, object] = {
        "intent": OperationalPaperSessionSettlementIntent(epoch.epoch_id, epoch.epoch_checksum),
        "era": _era(),
        "epoch": epoch,
        "authorization": _authorization(),
        "persisted_state_binding": _binding(),
        "initial_capital": D("1000"),
        "portfolio": _portfolio(),
        "ledger_movement_id": MOVEMENT_ID,
    }
    values.update(changes)
    return build_operational_paper_session_settlement_specification(**values)  # type: ignore[arg-type]


def test_public_contract_is_explicit_and_has_no_private_or_duplicate_exports() -> None:
    names = public_contract.__all__
    assert len(names) == len(set(names))
    assert all(not name.startswith("_") and hasattr(public_contract, name) for name in names)
    assert {
        "OperationalPaperSessionSettlement",
        "OperationalPaperSessionSettlementIntent",
        "OperationalPaperSessionSettlementSpecification",
    } <= set(names)
    assert not any("Movement" in name or name.endswith("State") for name in names)
    assert OPERATIONAL_PAPER_SESSION_SETTLEMENT_SCHEMA_VERSION == 1
    assert OPERATIONAL_PAPER_SESSION_SETTLEMENT_CONTRACT_VERSION == 1
    assert OPERATIONAL_PAPER_SESSION_SETTLEMENT_QUANTUM == D("0.00000001")
    assert MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_AMOUNT == D("999999999999.99999999")
    assert MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_IDEMPOTENCY_KEY_LENGTH == 128


def test_exact_shapes_preserve_minimal_intent_and_no_parallel_financial_authority() -> None:
    assert tuple(field.name for field in fields(_intent())) == ("epoch_id", "epoch_checksum")
    assert tuple(field.name for field in fields(_spec())) == SPEC_FIELDS
    entity_fields = tuple(field.name for field in fields(_settlement()))
    assert entity_fields == (
        "settlement_id",
        "specification",
        "settlement_checksum",
        "settled_by",
        "settled_at",
        "settle_idempotency_key",
        "settle_intent_fingerprint",
    )
    forbidden = {
        "current_balance",
        "available_capital",
        "reserved_capital",
        "state",
        "record_version",
        "movements",
        "orders",
        "fills",
        "equity",
    }
    assert not forbidden.intersection(SPEC_FIELDS + entity_fields)


@pytest.mark.parametrize("factory", (_intent, _spec, _settlement))
def test_contracts_are_frozen_and_slotted(
    factory: Callable[
        [],
        OperationalPaperSessionSettlementIntent
        | OperationalPaperSessionSettlementSpecification
        | OperationalPaperSessionSettlement,
    ],
) -> None:
    value = factory()
    assert not hasattr(value, "__dict__")
    name = fields(value)[0].name
    with pytest.raises(FrozenInstanceError):
        setattr(value, name, getattr(value, name))


@pytest.mark.parametrize("name", ("schema_version", "settlement_contract_version"))
@pytest.mark.parametrize("value", (0, 2, True, 1.0, "1", None))
def test_versions_require_exact_supported_integer(name: str, value: object) -> None:
    with pytest.raises(INVALID):
        _spec(**{name: value})


@pytest.mark.parametrize("name", UUID_FIELDS)
def test_spec_uuid_requires_nonzero_uuid_object(name: str) -> None:
    for value in (UUID(int=0), str(EPOCH_ID), True, 1):
        with pytest.raises(INVALID):
            _spec(**{name: value})


@pytest.mark.parametrize("name", SHA_FIELDS)
def test_all_evidence_identities_require_lowercase_sha256(name: str) -> None:
    for value in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n", None, 1):
        with pytest.raises(INVALID):
            _spec(**{name: value})


@pytest.mark.parametrize(
    "changes",
    (
        {"epoch_id": UUID(int=0)},
        {"epoch_id": str(EPOCH_ID)},
        {"epoch_checksum": "B" * 64},
        {"epoch_checksum": "b" * 63},
    ),
)
def test_intent_rejects_invalid_epoch_identity(changes: dict[str, object]) -> None:
    with pytest.raises(INVALID):
        _intent(**changes)


def test_intent_fingerprint_is_versioned_canonical_and_identity_sensitive() -> None:
    fingerprint = operational_paper_session_settlement_intent_fingerprint(_intent())
    expected = hashlib.sha256(
        canonical_json_bytes(
            {
                "contract_version": 1,
                "epoch_id": str(EPOCH_ID),
                "epoch_checksum": "b" * 64,
            }
        )
    ).hexdigest()
    assert fingerprint == expected
    assert fingerprint == operational_paper_session_settlement_intent_fingerprint(_intent())
    for changed in (_intent(epoch_id=UUID(int=99)), _intent(epoch_checksum="c" * 64)):
        assert operational_paper_session_settlement_intent_fingerprint(changed) != fingerprint
    with pytest.raises(INVALID):
        operational_paper_session_settlement_intent_fingerprint(object())  # type: ignore[arg-type]


@pytest.mark.parametrize("key", (None, True, 1, "has space", "a/b", " leading", "a\n", "á", "_a"))
def test_idempotency_key_rejects_unsafe_or_nonstring_value(key: object) -> None:
    with pytest.raises(INVALID):
        validate_operational_paper_session_settlement_idempotency_key(key)


@pytest.mark.parametrize("key", ("", "a" * 129))
def test_idempotency_key_bounds_revalidated_by_entity(key: str) -> None:
    with pytest.raises(OperationalPaperSessionSettlementBoundsExceededError):
        validate_operational_paper_session_settlement_idempotency_key(key)
    with pytest.raises(OperationalPaperSessionSettlementBoundsExceededError):
        _settlement(settle_idempotency_key=key)


@pytest.mark.parametrize("key", ("a", "a" * 128, "A0.b_c:d-e"))
def test_idempotency_valid_token_is_preserved(key: str) -> None:
    assert validate_operational_paper_session_settlement_idempotency_key(key) == key
    assert _settlement(settle_idempotency_key=key).settle_idempotency_key == key


@pytest.mark.parametrize(
    "value",
    (
        TERMINAL_AT.replace(tzinfo=None),
        TERMINAL_AT.replace(tzinfo=timezone(timedelta(hours=1))),
        TERMINAL_AT.isoformat(),
        None,
    ),
)
def test_specification_and_entity_require_strict_utc(value: object) -> None:
    with pytest.raises(INVALID):
        _spec(epoch_terminal_at=value)
    with pytest.raises(INVALID):
        _settlement(settled_at=value)


def test_entity_cannot_predate_verified_epoch_terminal_time() -> None:
    with pytest.raises(INVALID):
        _settlement(settled_at=TERMINAL_AT - timedelta(microseconds=1))
    assert _settlement(settled_at=TERMINAL_AT).settled_at == TERMINAL_AT
    assert _settlement(settled_at=TERMINAL_AT + timedelta(days=1)).settled_at > TERMINAL_AT


def test_payload_and_canonical_bytes_include_all_evidence_and_nullable_link() -> None:
    spec = _spec()
    payload = operational_paper_session_settlement_specification_payload(spec)
    assert tuple(payload) == SPEC_FIELDS
    for name in SPEC_FIELDS:
        expected = getattr(spec, name)
        if isinstance(expected, UUID):
            expected = str(expected)
        elif isinstance(expected, datetime):
            expected = expected.isoformat()
        assert payload[name] == expected
    encoded = operational_paper_session_settlement_specification_bytes(spec)
    assert encoded == canonical_json_bytes(payload)
    assert json.loads(encoded)["final_quote_cash"] == "1010"
    assert (
        operational_paper_session_settlement_specification_checksum(spec)
        == hashlib.sha256(encoded).hexdigest()
    )
    equivalent = _spec(total_fees=D("2.000000000"), base_quantity=D("-0E-1000"))
    assert operational_paper_session_settlement_specifications_equal(spec, equivalent)
    zero = _spec(
        final_quote_cash=D("1000"),
        realized_pnl=D("0"),
        settlement_delta=D("0"),
        ledger_movement_id=None,
    )
    assert (
        json.loads(operational_paper_session_settlement_specification_bytes(zero))[
            "ledger_movement_id"
        ]
        is None
    )


@pytest.mark.parametrize(
    "name", SHA_FIELDS + UUID_FIELDS + ("epoch_terminal_at", "total_fees", "total_slippage_cost")
)
def test_checksum_changes_with_each_independently_variable_evidence_dimension(name: str) -> None:
    baseline = _spec()
    previous = getattr(baseline, name)
    if isinstance(previous, UUID):
        changed: object = UUID(int=99)
    elif isinstance(previous, str):
        changed = "9" * 64
    elif isinstance(previous, datetime):
        changed = previous + timedelta(seconds=1)
    else:
        changed = D("4")
    variant = replace(baseline, **{name: changed})
    assert operational_paper_session_settlement_specification_checksum(
        variant
    ) != operational_paper_session_settlement_specification_checksum(baseline)
    assert not operational_paper_session_settlement_specifications_equal(baseline, variant)


def test_checksum_binds_interdependent_financial_evidence() -> None:
    baseline = _spec()
    variants = (
        _spec(initial_capital=D("1001"), final_quote_cash=D("1011")),
        _spec(final_quote_cash=D("1011"), realized_pnl=D("11"), settlement_delta=D("11")),
        _spec(final_quote_cash=D("990"), realized_pnl=D("-10"), settlement_delta=D("-10")),
        _spec(
            final_quote_cash=D("1000"),
            realized_pnl=D("0"),
            settlement_delta=D("0"),
            ledger_movement_id=None,
        ),
    )
    checksums = {
        operational_paper_session_settlement_specification_checksum(s)
        for s in (baseline, *variants)
    }
    assert len(checksums) == 5


def test_checksum_validator_and_entity_reject_mismatch() -> None:
    spec = _spec()
    checksum = operational_paper_session_settlement_specification_checksum(spec)
    assert (
        validate_operational_paper_session_settlement_specification_checksum(spec, checksum) == spec
    )
    with pytest.raises(OperationalPaperSessionSettlementChecksumMismatchError):
        validate_operational_paper_session_settlement_specification_checksum(spec, "0" * 64)
    with pytest.raises(OperationalPaperSessionSettlementChecksumMismatchError):
        _settlement(settlement_checksum="0" * 64)
    for malformed in (None, "A" * 64, "x"):
        with pytest.raises(INVALID):
            validate_operational_paper_session_settlement_specification_checksum(spec, malformed)
        with pytest.raises(INVALID):
            _settlement(settlement_checksum=malformed)


@pytest.mark.parametrize("name", FINANCIAL_FIELDS)
def test_every_authoritative_amount_requires_exact_finite_bounded_decimal(name: str) -> None:
    for value in (
        True,
        False,
        1.0,
        1,
        "1",
        None,
        D("NaN"),
        D("sNaN"),
        D("Infinity"),
        D("-Infinity"),
        D("1.000000001"),
        D("1000000000000"),
        D("-1000000000000"),
    ):
        with pytest.raises(INVALID):
            _spec(**{name: value})


@pytest.mark.parametrize(
    "name",
    (
        "initial_capital",
        "final_quote_cash",
        "base_quantity",
        "average_entry_price",
        "cost_basis",
        "total_fees",
        "total_slippage_cost",
    ),
)
def test_nonnegative_evidence_rejects_negative_amounts(name: str) -> None:
    with pytest.raises(INVALID):
        _spec(**{name: D("-0.00000001")})


def test_initial_capital_must_be_positive() -> None:
    with pytest.raises(INVALID):
        _spec(initial_capital=D("0"), final_quote_cash=D("10"))


@pytest.mark.parametrize(
    "name", ("base_quantity", "average_entry_price", "cost_basis", "unrealized_pnl")
)
def test_settlement_requires_all_four_flat_portfolio_values(name: str) -> None:
    with pytest.raises(ELIGIBILITY):
        _spec(**{name: D("0.00000001")})
    if name == "unrealized_pnl":
        with pytest.raises(ELIGIBILITY):
            _spec(**{name: D("-0.00000001")})


@pytest.mark.parametrize(
    "changes",
    (
        {"settlement_delta": D("9")},
        {"realized_pnl": D("9")},
        {"final_quote_cash": D("1010.00000001")},
        {"initial_capital": D("999")},
        {"settlement_delta": D("9"), "realized_pnl": D("9")},
    ),
)
def test_both_settlement_equalities_are_exact(changes: dict[str, object]) -> None:
    with pytest.raises(INVALID):
        _spec(**changes)


@pytest.mark.parametrize("delta", (D("10"), D("-10"), D("0")))
def test_profit_loss_and_zero_keep_fees_as_evidence_only(delta: Decimal) -> None:
    spec = _spec(
        final_quote_cash=D("1000") + delta,
        realized_pnl=delta,
        settlement_delta=delta,
        ledger_movement_id=None if delta == 0 else MOVEMENT_ID,
        total_fees=D("99"),
        total_slippage_cost=D("98"),
    )
    assert spec.settlement_delta == delta
    assert spec.total_fees == D("99") and spec.total_slippage_cost == D("98")
    entity = _settlement(
        specification=spec,
        settlement_checksum=operational_paper_session_settlement_specification_checksum(spec),
    )
    assert entity.specification == spec


def test_zero_delta_forbids_movement_and_nonzero_requires_nonzero_uuid() -> None:
    with pytest.raises(INVALID):
        _spec(final_quote_cash=D("1000"), realized_pnl=D("0"), settlement_delta=D("0"))
    for delta in (D("10"), D("-10")):
        for movement in (None, UUID(int=0)):
            with pytest.raises(INVALID):
                _spec(
                    final_quote_cash=D("1000") + delta,
                    realized_pnl=delta,
                    settlement_delta=delta,
                    ledger_movement_id=movement,
                )


def test_numeric_boundaries_and_quantum_are_exact_under_hostile_decimal_context() -> None:
    maximum = D("999999999999.99999999")
    # Construct outside the hostile context to avoid rounding the test inputs.
    negative_maximum = maximum.copy_negate()
    with localcontext() as context:
        context.prec = 6
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        loss = _spec(
            initial_capital=maximum,
            final_quote_cash=D("0"),
            realized_pnl=negative_maximum,
            settlement_delta=negative_maximum,
            total_fees=maximum,
            total_slippage_cost=maximum,
        )
        assert loss.settlement_delta == negative_maximum
        tiny = _spec(
            initial_capital=D("999999999999.99999998"),
            final_quote_cash=maximum,
            realized_pnl=D("0.00000001"),
            settlement_delta=D("0.00000001"),
        )
        assert tiny.settlement_delta == D("0.00000001")
        assert operational_paper_session_settlement_specification_checksum(tiny)
        smallest = _spec(
            initial_capital=D("0.00000001"),
            final_quote_cash=D("0"),
            realized_pnl=D("-0.00000001"),
            settlement_delta=D("-0.00000001"),
        )
        assert smallest.final_quote_cash == 0


def test_entity_preserves_provenance_and_actor_scoped_replay_metadata() -> None:
    source = _spec()
    entity = _settlement(specification=source)
    assert entity.specification == source
    assert entity.specification is not source
    assert entity.settled_by == ACTOR_ID
    assert entity.settle_idempotency_key == "settlement:1"
    assert (
        entity.settle_intent_fingerprint
        == operational_paper_session_settlement_intent_fingerprint(_intent())
    )
    other_actor = replace(entity, settled_by=UUID(int=99))
    assert other_actor.settlement_checksum == entity.settlement_checksum
    assert other_actor.settle_intent_fingerprint == entity.settle_intent_fingerprint
    assert other_actor.settled_by != entity.settled_by


@pytest.mark.parametrize(
    "changes",
    (
        {"settlement_id": UUID(int=0)},
        {"settled_by": UUID(int=0)},
        {"settled_by": str(ACTOR_ID)},
        {"settle_intent_fingerprint": "A" * 64},
        {"settle_intent_fingerprint": "0" * 64},
        {"settle_idempotency_key": "unsafe key"},
        {"specification": object()},
    ),
)
def test_entity_rejects_invalid_identity_and_replay_evidence(changes: dict[str, object]) -> None:
    with pytest.raises(INVALID):
        _settlement(**changes)


def test_entity_fingerprint_must_match_exact_specification_epoch() -> None:
    spec = _spec(epoch_id=UUID(int=99))
    with pytest.raises(INVALID):
        _settlement(
            specification=spec,
            settlement_checksum=operational_paper_session_settlement_specification_checksum(spec),
        )


def test_public_helpers_revalidate_tampered_frozen_values() -> None:
    bad = _spec()
    object.__setattr__(bad, "initial_capital", True)
    for helper in (
        operational_paper_session_settlement_specification_payload,
        operational_paper_session_settlement_specification_bytes,
        operational_paper_session_settlement_specification_checksum,
    ):
        with pytest.raises(INVALID):
            helper(bad)
        with pytest.raises(INVALID):
            helper(object())  # type: ignore[arg-type]
    with pytest.raises(INVALID):
        _settlement(specification=bad)
    with pytest.raises(INVALID):
        operational_paper_session_settlement_specifications_equal(_spec(), bad)
    intent = _intent()
    object.__setattr__(intent, "epoch_id", UUID(int=0))
    with pytest.raises(INVALID):
        operational_paper_session_settlement_intent_fingerprint(intent)


def test_entity_rechecks_valid_but_changed_nested_evidence_checksum() -> None:
    spec = _spec()
    checksum = operational_paper_session_settlement_specification_checksum(spec)
    object.__setattr__(spec, "state_checksum", "9" * 64)
    with pytest.raises(OperationalPaperSessionSettlementChecksumMismatchError):
        _settlement(specification=spec, settlement_checksum=checksum)


def test_builder_preserves_exact_existing_authorities_without_mutating_them() -> None:
    era, epoch, authorization, binding, portfolio = (
        _era(),
        _epoch(),
        _authorization(),
        _binding(),
        _portfolio(),
    )
    spec = _build(
        era=era,
        epoch=epoch,
        authorization=authorization,
        persisted_state_binding=binding,
        portfolio=portfolio,
    )
    assert spec.era_id == era.era_id and spec.era_checksum == era.era_checksum
    assert spec.simulation_id == epoch.simulation_id == authorization.simulation_id
    assert spec.epoch_id == epoch.epoch_id and spec.epoch_checksum == epoch.epoch_checksum
    assert spec.epoch_terminal_at == epoch.terminal_at
    assert spec.authorization_id == authorization.authorization_id
    assert spec.authorization_checksum == authorization.authorization_checksum
    for field in fields(binding):
        assert getattr(spec, field.name) == getattr(binding, field.name)
    assert spec.initial_capital == authorization.authorized_capital == D("1000")
    assert spec.initial_capital != era.initial_capital
    assert spec.final_quote_cash == portfolio.quote_cash
    assert spec.settlement_delta == portfolio.realized_pnl
    assert spec.ledger_movement_id == MOVEMENT_ID
    assert (era, epoch, authorization, binding, portfolio) == (
        _era(),
        _epoch(),
        _authorization(),
        _binding(),
        _portfolio(),
    )


@pytest.mark.parametrize(
    "desired,observed",
    (
        (
            OperationalPaperSessionRunDesiredState.RUNNING,
            OperationalPaperSessionRunObservedState.PENDING,
        ),
        (
            OperationalPaperSessionRunDesiredState.STOPPED,
            OperationalPaperSessionRunObservedState.PENDING,
        ),
        (
            OperationalPaperSessionRunDesiredState.PAUSED,
            OperationalPaperSessionRunObservedState.PAUSED,
        ),
        (
            OperationalPaperSessionRunDesiredState.STOPPED,
            OperationalPaperSessionRunObservedState.PAUSED,
        ),
    ),
)
def test_builder_requires_both_stopped_states(
    desired: OperationalPaperSessionRunDesiredState,
    observed: OperationalPaperSessionRunObservedState,
) -> None:
    epoch = _epoch(desired_state=desired, observed_state=observed, terminal_at=None)
    with pytest.raises(ELIGIBILITY):
        _build(epoch=epoch)


def test_builder_rejects_failed_epoch_even_if_desired_stopped() -> None:
    failure_code = next(iter(OperationalPaperSessionRunFailureCode))
    epoch = _epoch(
        observed_state=OperationalPaperSessionRunObservedState.FAILED,
        failure=OperationalPaperSessionRunFailure(failure_code, TERMINAL_AT),
    )
    with pytest.raises(ELIGIBILITY):
        _build(epoch=epoch)


def test_builder_requires_authorization_still_authorized() -> None:
    revoked = _authorization(
        state=OperationalPaperCapitalAuthorizationState.REVOKED,
        revoked_by=ACTOR_ID,
        revoked_at=TERMINAL_AT,
    )
    with pytest.raises(ELIGIBILITY):
        _build(authorization=revoked)


@pytest.mark.parametrize("field", ("session_id", "config_checksum"))
def test_builder_rejects_persisted_binding_for_other_session_or_config(field: str) -> None:
    with pytest.raises(ELIGIBILITY):
        _build(persisted_state_binding=_binding(**{field: "9" * 64}))


def test_builder_rejects_wrong_intent_or_capital() -> None:
    epoch = _epoch()
    for intent in (
        OperationalPaperSessionSettlementIntent(UUID(int=99), epoch.epoch_checksum),
        OperationalPaperSessionSettlementIntent(epoch.epoch_id, "9" * 64),
    ):
        with pytest.raises(ELIGIBILITY):
            _build(intent=intent)
    with pytest.raises(ELIGIBILITY):
        _build(initial_capital=D("1001"))
    with pytest.raises(INVALID):
        _build(initial_capital=1000.0)


def test_builder_rejects_wrong_authorization_identity() -> None:
    with pytest.raises(ELIGIBILITY):
        _build(authorization=_authorization(authorization_id=UUID(int=99)))


@pytest.mark.parametrize(
    "source,name",
    (
        ("era", "era_checksum"),
        ("epoch", "epoch_checksum"),
        ("authorization", "authorization_checksum"),
        ("persisted_state_binding", "state_checksum"),
        ("portfolio", "quote_cash"),
    ),
)
def test_builder_revalidates_source_objects(source: str, name: str) -> None:
    factories = {
        "era": _era,
        "epoch": _epoch,
        "authorization": _authorization,
        "persisted_state_binding": _binding,
        "portfolio": _portfolio,
    }
    value = factories[source]()
    object.__setattr__(value, name, True if source == "portfolio" else "INVALID")
    with pytest.raises(INVALID):
        _build(**{source: value})
    with pytest.raises(INVALID):
        _build(**{source: object()})


def test_builder_rejects_open_position_without_forced_close() -> None:
    portfolio = _portfolio(base_quantity=D("1"), cost_basis=D("10"), average_entry_price=D("10"))
    with pytest.raises(ELIGIBILITY):
        _build(portfolio=portfolio)
    assert portfolio.base_quantity == D("1")
    with pytest.raises(ELIGIBILITY):
        _build(portfolio=_portfolio(unrealized_pnl=D("1")))


def test_builder_chronology_uses_only_supplied_authority_timestamps() -> None:
    with pytest.raises(ELIGIBILITY):
        _build(era=_era(designated_at=STARTED_AT + timedelta(minutes=2)))
    with pytest.raises(ELIGIBILITY):
        _build(authorization=_authorization(created_at=STARTED_AT + timedelta(minutes=3)))


@pytest.mark.parametrize("delta", (D("10"), D("-10"), D("0")))
def test_builder_derives_delta_and_links_optional_movement(delta: Decimal) -> None:
    cash = D("1000") + delta
    portfolio = _portfolio(quote_cash=cash, realized_pnl=delta, equity=cash, peak_equity=cash)
    spec = _build(portfolio=portfolio, ledger_movement_id=None if delta == 0 else MOVEMENT_ID)
    assert spec.settlement_delta == delta
    assert spec.total_fees == portfolio.total_fees
    assert spec.total_slippage_cost == portfolio.total_slippage_cost


@pytest.mark.parametrize(
    "changes",
    (
        {"simulation_id": UUID(int=99)},
        {
            "authorization_binding": OperationalPaperSessionMaterializationAuthorizationBinding(
                UUID(int=99),
                _authorization().authorization_checksum,
            )
        },
        {
            "authorization_binding": OperationalPaperSessionMaterializationAuthorizationBinding(
                AUTHORIZATION_ID,
                "9" * 64,
            )
        },
        {
            "profile_binding": OperationalPaperSessionMaterializationProfileBinding(
                UUID(int=99),
                2,
                "a" * 64,
            )
        },
        {
            "profile_binding": OperationalPaperSessionMaterializationProfileBinding(
                UUID(int=20),
                3,
                "a" * 64,
            )
        },
        {
            "profile_binding": OperationalPaperSessionMaterializationProfileBinding(
                UUID(int=20),
                2,
                "9" * 64,
            )
        },
    ),
)
def test_builder_rejects_incompatible_provenance_with_valid_epoch_checksum(
    changes: dict[str, object],
) -> None:
    epoch = _epoch()
    spec = OperationalPaperSessionRunEpochSpecification(
        **{
            field.name: getattr(epoch, field.name)
            for field in fields(OperationalPaperSessionRunEpochSpecification)
        }
    )
    spec = replace(spec, **changes)
    epoch = replace(
        epoch,
        **changes,
        epoch_checksum=operational_paper_session_run_epoch_specification_checksum(spec),
    )
    intent = OperationalPaperSessionSettlementIntent(epoch.epoch_id, epoch.epoch_checksum)
    with pytest.raises(ELIGIBILITY):
        _build(epoch=epoch, intent=intent)


@pytest.mark.parametrize(
    "changes",
    (
        {"simulation_id": UUID(int=99)},
        {"quote_asset": "BRL"},
        {"authorized_capital": D("999")},
    ),
)
def test_builder_rejects_different_authorization_with_valid_checksum(
    changes: dict[str, object],
) -> None:
    authorization = _authorization()
    spec = OperationalPaperCapitalAuthorizationSpecification(
        **{
            field.name: getattr(authorization, field.name)
            for field in fields(OperationalPaperCapitalAuthorizationSpecification)
        }
    )
    spec = replace(spec, **changes)
    authorization = replace(
        authorization,
        **changes,
        authorization_checksum=operational_paper_capital_authorization_specification_checksum(spec),
    )
    epoch = _epoch()
    epoch_spec = OperationalPaperSessionRunEpochSpecification(
        **{
            field.name: getattr(epoch, field.name)
            for field in fields(OperationalPaperSessionRunEpochSpecification)
        }
    )
    # Keep the epoch authorization checksum consistent to exercise the other
    # simulation/currency/config-capital bindings independently.
    binding = OperationalPaperSessionMaterializationAuthorizationBinding(
        authorization.authorization_id,
        authorization.authorization_checksum,
    )
    epoch_spec = replace(epoch_spec, authorization_binding=binding)
    epoch = replace(
        epoch,
        authorization_binding=binding,
        epoch_checksum=operational_paper_session_run_epoch_specification_checksum(epoch_spec),
    )
    intent = OperationalPaperSessionSettlementIntent(epoch.epoch_id, epoch.epoch_checksum)
    with pytest.raises(ELIGIBILITY):
        _build(authorization=authorization, epoch=epoch, intent=intent)


def test_builder_rejects_an_era_for_another_simulation_with_valid_checksum() -> None:
    era = _era()
    spec = OperationalPaperCapitalEraSpecification(
        **{
            field.name: getattr(era, field.name)
            for field in fields(OperationalPaperCapitalEraSpecification)
        }
    )
    spec = replace(spec, simulation_id=UUID(int=99))
    era = replace(
        era,
        simulation_id=spec.simulation_id,
        era_checksum=operational_paper_capital_era_specification_checksum(spec),
        designation_intent_fingerprint=operational_paper_capital_era_designation_intent_fingerprint(
            OperationalPaperCapitalEraDesignationIntent(spec.simulation_id)
        ),
    )
    with pytest.raises(ELIGIBILITY):
        _build(era=era)
