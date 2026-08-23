"""Pure-domain tests for operational paper-session profiles."""

from __future__ import annotations

import re
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

import app.operational_paper_session_profiles as public_contract
from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    PositionSizedExecutionAssumptions,
    PositionSizingKind,
    PositionSizingPolicy,
    RiskLimits,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
)
from app.indicators.regime import MarketRegimePolicy
from app.market_data.domain import Exchange, MarketType, Timeframe, TradingPair
from app.market_data.timeframes import TIMEFRAMES
from app.operational_mandates import OperationalMandateInstrument
from app.operational_paper_session_profiles import (
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_CANDLES,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_EVENTS,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_ORDERS,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_WARMUP_CANDLES,
    OPERATIONAL_PAPER_SESSION_PROFILE_SPEC_SCHEMA_VERSION,
    InvalidOperationalPaperSessionProfileSpecificationError,
    InvalidOperationalPaperSessionProfileStrategySnapshotError,
    OperationalPaperSessionProfile,
    OperationalPaperSessionProfileBoundsExceededError,
    OperationalPaperSessionProfileChecksumMismatchError,
    OperationalPaperSessionProfileCreateIntent,
    OperationalPaperSessionProfileMandateBinding,
    OperationalPaperSessionProfileRevision,
    OperationalPaperSessionProfileSpecification,
    OperationalPaperSessionProfileState,
    OperationalPaperSessionProfileStateTransitionConflictError,
    build_operational_paper_session_profile_strategy_snapshot,
    is_operational_paper_session_profile_transition_allowed,
    operational_paper_session_profile_create_intent_fingerprint,
    operational_paper_session_profile_specification_bytes,
    operational_paper_session_profile_specification_checksum,
    operational_paper_session_profile_specification_payload,
    operational_paper_session_profile_specifications_equal,
    operational_paper_session_profile_strategy_snapshot_checksum,
    require_operational_paper_session_profile_transition,
    validate_operational_paper_session_profile_idempotency_key,
    validate_operational_paper_session_profile_specification_checksum,
)

PROFILE_ID = UUID("10000000-0000-4000-8000-000000000001")
MANDATE_ID = UUID("20000000-0000-4000-8000-000000000002")
STRATEGY_ID = UUID("30000000-0000-4000-8000-000000000003")
ACTOR_ID = UUID("40000000-0000-4000-8000-000000000004")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _binding(**changes: object) -> OperationalPaperSessionProfileMandateBinding:
    values: dict[str, object] = {
        "mandate_id": MANDATE_ID,
        "approved_revision": 2,
        "specification_checksum": "a" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionProfileMandateBinding(**values)  # type: ignore[arg-type]


def _instrument(
    base: str = "BTC", *, market_type: MarketType = MarketType.SPOT
) -> OperationalMandateInstrument:
    return OperationalMandateInstrument(
        Exchange.BINANCE,
        market_type,
        TradingPair(base, "USDT"),
    )


def _strategy(**changes: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "strategy_definition_id": STRATEGY_ID,
        "source_revision": 3,
        "plugin_name": "ema-cross-example",
        "plugin_version": "2",
        "plugin_schema_version": 1,
        "strategy_lifecycle_version": 2,
        "parameters": (("fast", 12), ("ratio", Decimal("1.50"))),
        "parameters_checksum": "b" * 64,
    }
    values.update(changes)
    return build_operational_paper_session_profile_strategy_snapshot(**values)  # type: ignore[arg-type]


def _execution() -> PositionSizedExecutionAssumptions:
    return PositionSizedExecutionAssumptions(
        fees=FeeModel(Decimal("1"), Decimal("2")),
        slippage=SlippageModel(fixed_bps=Decimal("3")),
        position_sizing=PositionSizingPolicy(
            PositionSizingKind.EQUITY_PERCENT,
            Decimal("25"),
            Decimal("10"),
        ),
    )


def _risk() -> StopLossRiskLimits:
    return StopLossRiskLimits(
        max_order_notional=Decimal("500"),
        max_position_notional=Decimal("1000"),
        max_open_orders=4,
        max_total_orders=50,
        max_drawdown_pct=Decimal("20"),
        minimum_quote_reserve=Decimal("20"),
        stop_loss=StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("5")),
    )


def _spec(**changes: object) -> OperationalPaperSessionProfileSpecification:
    values: dict[str, object] = {
        "schema_version": OPERATIONAL_PAPER_SESSION_PROFILE_SPEC_SCHEMA_VERSION,
        "name": "Primary paper profile",
        "description": "Deterministic non-capital policy.",
        "mandate_binding": _binding(),
        "selected_instrument": _instrument(),
        "timeframe": TIMEFRAMES["1h"],
        "start_at": NOW,
        "warmup_candles": 20,
        "strategy_snapshot": _strategy(),
        "execution": _execution(),
        "instrument_constraints": InstrumentConstraints(
            Decimal("0.001"),
            Decimal("0.001"),
            Decimal("0.01"),
            Decimal("10"),
            Decimal("10000"),
        ),
        "risk_limits": _risk(),
        "history_window": 512,
        "max_candles": 10_000,
        "max_orders": 1_000,
        "max_events": 10_000,
        "engine_version": "paper-engine-v1",
        "market_regime_policy": MarketRegimePolicy(),
    }
    values.update(changes)
    return OperationalPaperSessionProfileSpecification(**values)  # type: ignore[arg-type]


def _intent(**changes: object) -> OperationalPaperSessionProfileCreateIntent:
    specification = _spec()
    values = {
        field.name: getattr(specification, field.name)
        for field in fields(specification)
        if field.name not in {"schema_version", "strategy_snapshot"}
    }
    values.update(
        {
            "strategy_definition_id": STRATEGY_ID,
            "expected_strategy_definition_revision": 3,
            "expected_strategy_parameters_checksum": "b" * 64,
        }
    )
    values.update(changes)
    return OperationalPaperSessionProfileCreateIntent(**values)  # type: ignore[arg-type]


def _aggregate(**changes: object) -> OperationalPaperSessionProfile:
    values: dict[str, object] = {
        "profile_id": PROFILE_ID,
        "state": OperationalPaperSessionProfileState.DRAFT,
        "current_revision": 1,
        "record_version": 1,
        "approved_revision": None,
        "approved_checksum": None,
        "created_by": ACTOR_ID,
        "created_at": NOW,
        "approved_by": None,
        "approved_at": None,
        "archived_by": None,
        "archived_at": None,
        "create_idempotency_key": "create:1",
        "create_intent_fingerprint": "d" * 64,
    }
    values.update(changes)
    return OperationalPaperSessionProfile(**values)  # type: ignore[arg-type]


def test_lifecycle_is_exact_and_terminal() -> None:
    states = tuple(OperationalPaperSessionProfileState)
    assert states == (
        OperationalPaperSessionProfileState.DRAFT,
        OperationalPaperSessionProfileState.APPROVED,
        OperationalPaperSessionProfileState.ARCHIVED,
    )
    allowed = {
        (OperationalPaperSessionProfileState.DRAFT, OperationalPaperSessionProfileState.APPROVED),
        (OperationalPaperSessionProfileState.DRAFT, OperationalPaperSessionProfileState.ARCHIVED),
        (
            OperationalPaperSessionProfileState.APPROVED,
            OperationalPaperSessionProfileState.ARCHIVED,
        ),
    }
    for current in states:
        for target in states:
            assert is_operational_paper_session_profile_transition_allowed(current, target) is (
                (current, target) in allowed
            )
            if (current, target) in allowed:
                require_operational_paper_session_profile_transition(current, target)
            else:
                with pytest.raises(OperationalPaperSessionProfileStateTransitionConflictError):
                    require_operational_paper_session_profile_transition(current, target)


@pytest.mark.parametrize(
    "changes",
    [
        {"mandate_id": UUID(int=0)},
        {"approved_revision": 0},
        {"approved_revision": True},
        {"specification_checksum": "A" * 64},
        {"specification_checksum": "bad"},
    ],
)
def test_mandate_binding_rejects_invalid_evidence(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _binding(**changes)


def test_instrument_and_timeframe_are_canonical() -> None:
    assert (
        _spec(selected_instrument=_instrument("eth")).selected_instrument.pair.symbol == "ETH/USDT"
    )
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(selected_instrument=_instrument(market_type=MarketType.FUTURES))
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(timeframe=Timeframe("2h", timedelta(hours=2)))


def test_strategy_snapshot_is_order_independent_and_typed() -> None:
    first = _strategy(parameters=(("ratio", Decimal("1.500")), ("fast", 12)))
    second = _strategy(parameters=(("fast", 12), ("ratio", Decimal("1.5"))))
    assert first.parameters == second.parameters
    assert first.snapshot_checksum == second.snapshot_checksum
    assert SHA256.fullmatch(operational_paper_session_profile_strategy_snapshot_checksum(first))
    with pytest.raises(InvalidOperationalPaperSessionProfileStrategySnapshotError):
        _strategy(parameters=(("ratio", 1.5),))
    with pytest.raises(InvalidOperationalPaperSessionProfileStrategySnapshotError):
        _strategy(parameters=(("ratio", Decimal("NaN")),))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy_definition_id", UUID("50000000-0000-4000-8000-000000000005")),
        ("source_revision", 4),
        ("plugin_name", "no-op"),
        ("plugin_version", "1"),
        ("plugin_schema_version", 2),
        ("strategy_lifecycle_version", 1),
        ("parameters", (("fast", 13),)),
        ("parameters_checksum", "c" * 64),
    ],
)
def test_every_strategy_identity_dimension_changes_checksum(field: str, value: object) -> None:
    assert _strategy(**{field: value}).snapshot_checksum != _strategy().snapshot_checksum


def test_snapshot_checksum_mismatch_and_corruption_are_rejected() -> None:
    snapshot = _strategy()
    with pytest.raises(OperationalPaperSessionProfileChecksumMismatchError):
        replace(snapshot, snapshot_checksum="0" * 64)
    object.__setattr__(snapshot, "plugin_name", "corrupted name")
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(strategy_snapshot=snapshot)


def test_specification_payload_checksum_and_normalization_are_deterministic() -> None:
    first = _spec(name="  Cafe\u0301  ", description=" line\r\n")
    second = _spec(name="Café", description="line")
    assert first.name == "Café"
    assert operational_paper_session_profile_specifications_equal(first, second)
    assert operational_paper_session_profile_specification_bytes(
        first
    ) == operational_paper_session_profile_specification_bytes(second)
    checksum = operational_paper_session_profile_specification_checksum(first)
    assert SHA256.fullmatch(checksum)
    payload = operational_paper_session_profile_specification_payload(first)
    assert payload["mandate_binding"] == {
        "mandate_id": str(MANDATE_ID),
        "approved_revision": 2,
        "specification_checksum": "a" * 64,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "Changed"),
        ("description", "Changed"),
        ("mandate_binding", _binding(approved_revision=3)),
        ("selected_instrument", _instrument("ETH")),
        ("timeframe", TIMEFRAMES["4h"]),
        ("start_at", NOW + timedelta(hours=1)),
        ("warmup_candles", 21),
        ("strategy_snapshot", _strategy(source_revision=4)),
        ("execution", ExecutionAssumptions(FeeModel(Decimal("2"), Decimal("2")), SlippageModel())),
        (
            "instrument_constraints",
            InstrumentConstraints(Decimal(".01"), Decimal(".01"), Decimal(".1"), Decimal("20")),
        ),
        ("risk_limits", RiskLimits(max_open_orders=10, max_total_orders=60)),
        ("history_window", 513),
        ("max_candles", 10_001),
        ("max_orders", 1_001),
        ("max_events", 10_001),
        ("engine_version", "paper-engine-v2"),
        ("market_regime_policy", None),
    ],
)
def test_every_specification_semantic_field_changes_checksum(field: str, value: object) -> None:
    assert operational_paper_session_profile_specification_checksum(
        _spec(**{field: value})
    ) != operational_paper_session_profile_specification_checksum(_spec())


@pytest.mark.parametrize(
    "changes",
    [
        {"start_at": datetime(2026, 8, 23, 12)},
        {"start_at": datetime(2026, 8, 23, 9, tzinfo=timezone(timedelta(hours=-3)))},
        {"start_at": NOW + timedelta(minutes=1)},
        {"warmup_candles": -1},
        {"warmup_candles": True},
        {"warmup_candles": 513, "history_window": 512},
        {"warmup_candles": 1, "strategy_snapshot": _strategy(strategy_lifecycle_version=1)},
        {
            "execution": ExecutionAssumptions(
                FeeModel(Decimal("1"), Decimal("2")), SlippageModel(), force_close_at_end=True
            )
        },
    ],
)
def test_temporal_and_paper_execution_invariants(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(**changes)


def test_sizing_risk_stop_and_regime_semantics_remain_distinct() -> None:
    specification = _spec()
    execution = specification.execution
    assert isinstance(execution, PositionSizedExecutionAssumptions)
    assert execution.position_sizing.minimum_quote_reserve == Decimal("10")
    assert specification.risk_limits.minimum_quote_reserve == Decimal("20")
    assert isinstance(specification.risk_limits, StopLossRiskLimits)
    assert specification.risk_limits.stop_loss.value == Decimal("5")
    assert specification.market_regime_policy == MarketRegimePolicy()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stop_on_max_drawdown", 1),
        ("stop_on_max_drawdown", "true"),
        ("allow_all_in", 1),
        ("allow_all_in", "corrupt"),
        ("max_open_orders", True),
        ("max_total_orders", False),
    ],
)
def test_corrupted_risk_limit_primitives_are_rejected(field: str, value: object) -> None:
    risk_limits = _risk()
    object.__setattr__(risk_limits, field, value)

    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(risk_limits=risk_limits)


def test_corrupted_execution_false_like_boolean_is_rejected() -> None:
    execution = _execution()
    object.__setattr__(execution, "force_close_at_end", 0)

    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(execution=execution)


def test_corrupted_instrument_constraints_are_rejected() -> None:
    constraints = _spec().instrument_constraints
    object.__setattr__(constraints, "minimum_quantity", 1)

    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(instrument_constraints=constraints)


def test_corrupted_position_sizing_policy_is_rejected() -> None:
    execution = _execution()
    object.__setattr__(execution.position_sizing, "minimum_quote_reserve", 0)

    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(execution=execution)


def test_corrupted_stop_loss_policy_is_rejected() -> None:
    risk_limits = _risk()
    object.__setattr__(risk_limits.stop_loss, "kind", "fixed_percent")

    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(risk_limits=risk_limits)


def test_corrupted_market_regime_policy_is_rejected() -> None:
    policy = MarketRegimePolicy()
    object.__setattr__(policy, "fast_ema_period", True)

    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(market_regime_policy=policy)


@pytest.mark.parametrize(
    ("field", "maximum", "compatibility"),
    [
        (
            "warmup_candles",
            MAX_OPERATIONAL_PAPER_SESSION_PROFILE_WARMUP_CANDLES,
            {
                "history_window": MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW,
                "max_candles": MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW,
            },
        ),
        (
            "history_window",
            MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW,
            {"max_candles": MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW},
        ),
        ("max_candles", MAX_OPERATIONAL_PAPER_SESSION_PROFILE_CANDLES, {}),
        ("max_orders", MAX_OPERATIONAL_PAPER_SESSION_PROFILE_ORDERS, {}),
        ("max_events", MAX_OPERATIONAL_PAPER_SESSION_PROFILE_EVENTS, {}),
    ],
)
def test_profile_integer_upper_bounds(
    field: str,
    maximum: int,
    compatibility: dict[str, int],
) -> None:
    assert getattr(_spec(**compatibility, **{field: maximum}), field) == maximum
    with pytest.raises(OperationalPaperSessionProfileBoundsExceededError):
        _spec(**compatibility, **{field: maximum + 1})


@pytest.mark.parametrize(
    "field",
    ["warmup_candles", "history_window", "max_candles", "max_orders", "max_events"],
)
def test_profile_integer_bounds_reject_bool(field: str) -> None:
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _spec(**{field: True})


def test_capital_and_materialization_are_structurally_absent() -> None:
    specification_fields = {
        field.name for field in fields(OperationalPaperSessionProfileSpecification)
    }
    assert {"initial_capital", "capital", "allocation", "portfolio_balance"}.isdisjoint(
        specification_fields
    )
    public_names = set(public_contract.__all__)
    assert all("session_id" not in name.lower() for name in public_names)
    assert "PaperSessionConfig" not in public_names


def test_create_intent_fingerprint_is_stable_and_tracks_all_inputs() -> None:
    baseline = operational_paper_session_profile_create_intent_fingerprint(_intent())
    assert SHA256.fullmatch(baseline)
    assert baseline == operational_paper_session_profile_create_intent_fingerprint(_intent())
    changes = (
        {"name": "Changed"},
        {"description": "Changed"},
        {"mandate_binding": _binding(approved_revision=3)},
        {"selected_instrument": _instrument("ETH")},
        {"timeframe": TIMEFRAMES["4h"]},
        {"start_at": NOW + timedelta(hours=1)},
        {"warmup_candles": 21},
        {"strategy_definition_id": UUID("50000000-0000-4000-8000-000000000005")},
        {"expected_strategy_definition_revision": 4},
        {"expected_strategy_parameters_checksum": "c" * 64},
        {"execution": ExecutionAssumptions(FeeModel(Decimal("2"), Decimal("2")), SlippageModel())},
        {
            "instrument_constraints": InstrumentConstraints(
                Decimal(".01"), Decimal(".01"), Decimal(".1"), Decimal("20")
            )
        },
        {"risk_limits": RiskLimits(max_open_orders=10, max_total_orders=60)},
        {"history_window": 513},
        {"max_candles": 10_001},
        {"max_orders": 1_001},
        {"max_events": 10_001},
        {"engine_version": "paper-engine-v2"},
        {"market_regime_policy": None},
    )
    assert all(
        operational_paper_session_profile_create_intent_fingerprint(_intent(**change)) != baseline
        for change in changes
    )
    assert "strategy_snapshot" not in {field.name for field in fields(_intent())}
    assert "actor_id" not in {field.name for field in fields(_intent())}
    assert "profile_id" not in {field.name for field in fields(_intent())}


@pytest.mark.parametrize("key", ["create:1", "A", "A" * 128])
def test_safe_idempotency_key_is_preserved(key: str) -> None:
    assert validate_operational_paper_session_profile_idempotency_key(key) == key


@pytest.mark.parametrize("key", ["", "A" * 129, "unsafe key", "unsafe/value"])
def test_invalid_idempotency_key_is_rejected(key: str) -> None:
    with pytest.raises(
        (
            InvalidOperationalPaperSessionProfileSpecificationError,
            OperationalPaperSessionProfileBoundsExceededError,
        )
    ):
        validate_operational_paper_session_profile_idempotency_key(key)


def test_revision_verifies_canonical_checksum() -> None:
    specification = _spec()
    revision = OperationalPaperSessionProfileRevision(
        PROFILE_ID,
        1,
        specification,
        operational_paper_session_profile_specification_checksum(specification),
        ACTOR_ID,
        NOW,
    )
    assert revision.revision == 1
    with pytest.raises(OperationalPaperSessionProfileChecksumMismatchError):
        replace(revision, specification_checksum="0" * 64)


def test_specification_checksum_verification_helper() -> None:
    specification = _spec()
    checksum = operational_paper_session_profile_specification_checksum(specification)

    first = validate_operational_paper_session_profile_specification_checksum(
        specification, checksum
    )
    second = validate_operational_paper_session_profile_specification_checksum(
        specification, checksum
    )

    assert first == specification
    assert second == first
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        validate_operational_paper_session_profile_specification_checksum(
            specification, checksum.upper()
        )
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        validate_operational_paper_session_profile_specification_checksum(
            specification, "malformed"
        )
    with pytest.raises(OperationalPaperSessionProfileChecksumMismatchError):
        validate_operational_paper_session_profile_specification_checksum(specification, "0" * 64)
    with pytest.raises(OperationalPaperSessionProfileChecksumMismatchError):
        validate_operational_paper_session_profile_specification_checksum(
            _spec(name="Changed"), checksum
        )


def test_specification_checksum_excludes_revision_metadata() -> None:
    specification = _spec()
    checksum = operational_paper_session_profile_specification_checksum(specification)
    first = OperationalPaperSessionProfileRevision(
        PROFILE_ID,
        1,
        specification,
        checksum,
        ACTOR_ID,
        NOW,
    )
    second = OperationalPaperSessionProfileRevision(
        UUID("50000000-0000-4000-8000-000000000005"),
        7,
        specification,
        checksum,
        MANDATE_ID,
        NOW + timedelta(days=1),
    )

    assert first.specification_checksum == second.specification_checksum == checksum


def test_valid_aggregate_metadata_matrix() -> None:
    approved_at = NOW + timedelta(minutes=1)
    archived_at = NOW + timedelta(minutes=2)
    draft = _aggregate()
    approved = _aggregate(
        state=OperationalPaperSessionProfileState.APPROVED,
        record_version=2,
        approved_revision=1,
        approved_checksum="e" * 64,
        approved_by=MANDATE_ID,
        approved_at=approved_at,
    )
    archived_from_draft = _aggregate(
        state=OperationalPaperSessionProfileState.ARCHIVED,
        record_version=2,
        archived_by=MANDATE_ID,
        archived_at=archived_at,
    )
    archived_after_approval = _aggregate(
        state=OperationalPaperSessionProfileState.ARCHIVED,
        record_version=3,
        approved_revision=1,
        approved_checksum="e" * 64,
        approved_by=MANDATE_ID,
        approved_at=approved_at,
        archived_by=STRATEGY_ID,
        archived_at=archived_at,
    )

    assert draft.state is OperationalPaperSessionProfileState.DRAFT
    assert approved.approved_at == approved_at
    assert archived_from_draft.approved_revision is None
    assert archived_after_approval.archived_at == archived_at


@pytest.mark.parametrize(
    "changes",
    [
        {"state": OperationalPaperSessionProfileState.APPROVED},
        {
            "state": OperationalPaperSessionProfileState.APPROVED,
            "approved_revision": 1,
            "approved_checksum": "e" * 64,
            "approved_at": NOW + timedelta(minutes=1),
        },
        {
            "state": OperationalPaperSessionProfileState.APPROVED,
            "approved_revision": 1,
            "approved_checksum": "e" * 64,
            "approved_by": MANDATE_ID,
        },
        {
            "approved_revision": 1,
            "approved_checksum": "e" * 64,
            "approved_by": MANDATE_ID,
            "approved_at": NOW + timedelta(minutes=1),
        },
        {"archived_by": MANDATE_ID, "archived_at": NOW + timedelta(minutes=1)},
        {"state": OperationalPaperSessionProfileState.ARCHIVED},
        {
            "state": OperationalPaperSessionProfileState.ARCHIVED,
            "archived_at": NOW + timedelta(minutes=1),
        },
        {
            "state": OperationalPaperSessionProfileState.ARCHIVED,
            "archived_by": MANDATE_ID,
        },
        {
            "state": OperationalPaperSessionProfileState.ARCHIVED,
            "archived_by": MANDATE_ID,
            "archived_at": NOW - timedelta(seconds=1),
        },
        {
            "state": OperationalPaperSessionProfileState.APPROVED,
            "approved_revision": 1,
            "approved_checksum": "e" * 64,
            "approved_by": MANDATE_ID,
            "approved_at": NOW - timedelta(seconds=1),
        },
        {
            "state": OperationalPaperSessionProfileState.ARCHIVED,
            "approved_revision": 1,
            "approved_checksum": "e" * 64,
            "approved_by": MANDATE_ID,
            "approved_at": NOW + timedelta(minutes=2),
            "archived_by": STRATEGY_ID,
            "archived_at": NOW + timedelta(minutes=1),
        },
        {
            "state": OperationalPaperSessionProfileState.APPROVED,
            "current_revision": 2,
            "approved_revision": 1,
            "approved_checksum": "e" * 64,
            "approved_by": MANDATE_ID,
            "approved_at": NOW + timedelta(minutes=1),
        },
    ],
)
def test_invalid_aggregate_metadata_matrix(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        _aggregate(**changes)
