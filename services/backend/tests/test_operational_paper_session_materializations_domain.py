"""Pure-domain tests for operational paper-session materializations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

import app.operational_paper_session_materializations as public_contract
from app.backtesting.domain import (
    FeeModel,
    InstrumentConstraints,
    PositionSizedExecutionAssumptions,
    PositionSizingKind,
    PositionSizingPolicy,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
)
from app.indicators.regime import MarketRegimePolicy
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.market_data.timeframes import TIMEFRAMES
from app.operational_mandates import OperationalMandateInstrument
from app.operational_paper_capital_authorizations import (
    OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalAuthorizationSpecification,
    operational_paper_capital_authorization_specification_checksum,
)
from app.operational_paper_session_materializations import (
    InvalidOperationalPaperSessionMaterializationSpecificationError,
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationBoundsExceededError,
    OperationalPaperSessionMaterializationChecksumMismatchError,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationPlan,
    OperationalPaperSessionMaterializationProfileBinding,
    OperationalPaperSessionMaterializationProfileBindingConflictError,
    OperationalPaperSessionMaterializationQuoteAssetConflictError,
    OperationalPaperSessionMaterializationState,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
    build_operational_paper_session_materialization_plan,
    is_operational_paper_session_materialization_transition_allowed,
    materialize_operational_paper_session_materialization,
    operational_paper_session_materialization_specification_checksum,
    prepare_operational_paper_session_materialization,
    require_operational_paper_session_materialization_transition,
)
from app.operational_paper_session_profiles import (
    OPERATIONAL_PAPER_SESSION_PROFILE_SPEC_SCHEMA_VERSION,
    OperationalPaperSessionProfileMandateBinding,
    OperationalPaperSessionProfileRevision,
    OperationalPaperSessionProfileSpecification,
    OperationalPaperSessionProfileStrategySnapshot,
    build_operational_paper_session_profile_strategy_snapshot,
    operational_paper_session_profile_specification_checksum,
)
from app.paper_trading.domain import paper_config_checksum, paper_session_id

PROFILE_ID = UUID("10000000-0000-4000-8000-000000000001")
MANDATE_ID = UUID("20000000-0000-4000-8000-000000000002")
STRATEGY_ID = UUID("30000000-0000-4000-8000-000000000003")
AUTHORIZATION_ID = UUID("40000000-0000-4000-8000-000000000004")
SIMULATION_ID = UUID("50000000-0000-4000-8000-000000000005")
ACTOR_ID = UUID("60000000-0000-4000-8000-000000000006")
OTHER_ACTOR_ID = UUID("70000000-0000-4000-8000-000000000007")
MATERIALIZATION_ID = UUID("80000000-0000-4000-8000-000000000008")
NOW = datetime(2026, 8, 31, 18, tzinfo=UTC)
POSTGRESQL_BIGINT_MAX = (1 << 63) - 1


def _mandate_binding() -> OperationalPaperSessionProfileMandateBinding:
    return OperationalPaperSessionProfileMandateBinding(
        mandate_id=MANDATE_ID,
        approved_revision=2,
        specification_checksum="a" * 64,
    )


def _instrument(quote: str = "USDT") -> OperationalMandateInstrument:
    return OperationalMandateInstrument(
        Exchange.BINANCE,
        MarketType.SPOT,
        TradingPair("BTC", quote),
    )


def _strategy() -> OperationalPaperSessionProfileStrategySnapshot:
    return build_operational_paper_session_profile_strategy_snapshot(
        strategy_definition_id=STRATEGY_ID,
        source_revision=3,
        plugin_name="ema-cross-example",
        plugin_version="2",
        plugin_schema_version=1,
        strategy_lifecycle_version=2,
        parameters=(("fast", 12), ("ratio", Decimal("1.50"))),
        parameters_checksum="b" * 64,
    )


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


def _profile_spec(**changes: object) -> OperationalPaperSessionProfileSpecification:
    values: dict[str, object] = {
        "schema_version": OPERATIONAL_PAPER_SESSION_PROFILE_SPEC_SCHEMA_VERSION,
        "name": "Primary paper profile",
        "description": "Deterministic non-capital policy.",
        "mandate_binding": _mandate_binding(),
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
        "market_regime_policy": None,
    }
    values.update(changes)
    return OperationalPaperSessionProfileSpecification(**values)  # type: ignore[arg-type]


def _profile_revision(**spec_changes: object) -> OperationalPaperSessionProfileRevision:
    specification = _profile_spec(**spec_changes)
    return OperationalPaperSessionProfileRevision(
        profile_id=PROFILE_ID,
        revision=2,
        specification=specification,
        specification_checksum=operational_paper_session_profile_specification_checksum(
            specification
        ),
        created_by=ACTOR_ID,
        created_at=NOW,
    )


def _authorization(
    profile_revision: OperationalPaperSessionProfileRevision,
    *,
    quote_asset: str = "USDT",
    capital: Decimal = Decimal("1250.00000000"),
) -> OperationalPaperCapitalAuthorizationSpecification:
    return OperationalPaperCapitalAuthorizationSpecification(
        schema_version=OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
        profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=profile_revision.profile_id,
            approved_revision=profile_revision.revision,
            specification_checksum=profile_revision.specification_checksum,
        ),
        simulation_id=SIMULATION_ID,
        quote_asset=quote_asset,
        authorized_capital=capital,
    )


def _plan(**profile_changes: object) -> OperationalPaperSessionMaterializationPlan:
    profile = _profile_revision(**profile_changes)
    authorization = _authorization(profile)
    return build_operational_paper_session_materialization_plan(
        authorization_id=AUTHORIZATION_ID,
        authorization_specification=authorization,
        authorization_checksum=operational_paper_capital_authorization_specification_checksum(
            authorization
        ),
        profile_revision=profile,
    )


def test_public_contract_contains_no_private_exports() -> None:
    assert len(public_contract.__all__) == len(set(public_contract.__all__))
    assert all(not name.startswith("_") for name in public_contract.__all__)


def test_lifecycle_is_exact_and_materialized_is_terminal() -> None:
    states = tuple(OperationalPaperSessionMaterializationState)
    assert states == (
        OperationalPaperSessionMaterializationState.PREPARED,
        OperationalPaperSessionMaterializationState.MATERIALIZED,
    )
    assert is_operational_paper_session_materialization_transition_allowed(
        OperationalPaperSessionMaterializationState.PREPARED,
        OperationalPaperSessionMaterializationState.MATERIALIZED,
    )
    require_operational_paper_session_materialization_transition(
        OperationalPaperSessionMaterializationState.PREPARED,
        OperationalPaperSessionMaterializationState.MATERIALIZED,
    )
    for current, target in (
        (
            OperationalPaperSessionMaterializationState.PREPARED,
            OperationalPaperSessionMaterializationState.PREPARED,
        ),
        (
            OperationalPaperSessionMaterializationState.MATERIALIZED,
            OperationalPaperSessionMaterializationState.PREPARED,
        ),
        (
            OperationalPaperSessionMaterializationState.MATERIALIZED,
            OperationalPaperSessionMaterializationState.MATERIALIZED,
        ),
    ):
        assert not is_operational_paper_session_materialization_transition_allowed(current, target)
        with pytest.raises(OperationalPaperSessionMaterializationStateTransitionConflictError):
            require_operational_paper_session_materialization_transition(current, target)


def test_plan_maps_profile_and_authorized_capital_to_existing_paper_identity() -> None:
    plan = _plan()
    assert plan.config.pair == TradingPair("BTC", "USDT")
    assert plan.config.timeframe == TIMEFRAMES["1h"]
    assert plan.config.initial_capital == Decimal("1250.00000000")
    assert type(plan.config.initial_capital) is Decimal
    assert plan.config.schema_version == 1
    assert plan.specification.config_checksum == paper_config_checksum(plan.config)
    assert plan.specification.session_id == paper_session_id(plan.config)


def test_market_regime_profile_materializes_as_paper_config_schema_two() -> None:
    plan = _plan(market_regime_policy=MarketRegimePolicy())
    assert plan.config.schema_version == 2
    assert plan.config.market_regime_policy == MarketRegimePolicy()
    assert plan.specification.config_checksum == paper_config_checksum(plan.config)
    assert plan.specification.session_id == paper_session_id(plan.config)


def test_authorization_binding_rejects_invalid_evidence() -> None:
    with pytest.raises(InvalidOperationalPaperSessionMaterializationSpecificationError):
        OperationalPaperSessionMaterializationAuthorizationBinding(
            authorization_id=UUID(int=0),
            authorization_checksum="a" * 64,
        )
    with pytest.raises(InvalidOperationalPaperSessionMaterializationSpecificationError):
        OperationalPaperSessionMaterializationAuthorizationBinding(
            authorization_id=AUTHORIZATION_ID,
            authorization_checksum="BAD",
        )


def test_profile_and_mandate_bindings_reject_invalid_evidence() -> None:
    with pytest.raises(InvalidOperationalPaperSessionMaterializationSpecificationError):
        OperationalPaperSessionMaterializationProfileBinding(
            profile_id=PROFILE_ID,
            approved_revision=0,
            specification_checksum="a" * 64,
        )
    with pytest.raises(InvalidOperationalPaperSessionMaterializationSpecificationError):
        OperationalPaperSessionMaterializationMandateBinding(
            mandate_id=MANDATE_ID,
            approved_revision=2,
            specification_checksum="A" * 64,
        )


def test_authorization_checksum_tampering_fails_closed() -> None:
    profile = _profile_revision()
    authorization = _authorization(profile)
    with pytest.raises(OperationalPaperSessionMaterializationChecksumMismatchError):
        build_operational_paper_session_materialization_plan(
            authorization_id=AUTHORIZATION_ID,
            authorization_specification=authorization,
            authorization_checksum="0" * 64,
            profile_revision=profile,
        )


def test_corrupted_profile_revision_checksum_fails_closed() -> None:
    profile = _profile_revision()
    authorization = _authorization(profile)
    authorization_checksum = operational_paper_capital_authorization_specification_checksum(
        authorization
    )
    object.__setattr__(profile, "specification_checksum", "0" * 64)
    with pytest.raises(OperationalPaperSessionMaterializationChecksumMismatchError):
        build_operational_paper_session_materialization_plan(
            authorization_id=AUTHORIZATION_ID,
            authorization_specification=authorization,
            authorization_checksum=authorization_checksum,
            profile_revision=profile,
        )


def test_authorization_for_different_profile_fails_closed() -> None:
    profile = _profile_revision()
    authorization = _authorization(profile)
    authorization = replace(
        authorization,
        profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=UUID("90000000-0000-4000-8000-000000000009"),
            approved_revision=profile.revision,
            specification_checksum=profile.specification_checksum,
        ),
    )
    with pytest.raises(OperationalPaperSessionMaterializationProfileBindingConflictError):
        build_operational_paper_session_materialization_plan(
            authorization_id=AUTHORIZATION_ID,
            authorization_specification=authorization,
            authorization_checksum=operational_paper_capital_authorization_specification_checksum(
                authorization
            ),
            profile_revision=profile,
        )


def test_authorization_quote_asset_must_match_selected_instrument() -> None:
    profile = _profile_revision()
    authorization = _authorization(profile, quote_asset="USDC")
    with pytest.raises(OperationalPaperSessionMaterializationQuoteAssetConflictError):
        build_operational_paper_session_materialization_plan(
            authorization_id=AUTHORIZATION_ID,
            authorization_specification=authorization,
            authorization_checksum=operational_paper_capital_authorization_specification_checksum(
                authorization
            ),
            profile_revision=profile,
        )


@pytest.mark.parametrize("field", ["config_checksum", "session_id"])
def test_plan_rejects_config_identity_mismatch(field: str) -> None:
    plan = _plan()
    if field == "config_checksum":
        corrupted_specification = replace(
            plan.specification,
            config_checksum="0" * 64,
        )
    else:
        corrupted_specification = replace(
            plan.specification,
            session_id="0" * 64,
        )
    with pytest.raises(OperationalPaperSessionMaterializationConfigIdentityConflictError):
        OperationalPaperSessionMaterializationPlan(
            specification=corrupted_specification,
            config=plan.config,
        )


def test_prepare_creates_exact_prepared_aggregate() -> None:
    plan = _plan()
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=plan,
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    assert prepared.state is OperationalPaperSessionMaterializationState.PREPARED
    assert prepared.record_version == 1
    assert prepared.materialization_id == MATERIALIZATION_ID
    assert prepared.config_checksum == plan.specification.config_checksum
    assert prepared.session_id == plan.specification.session_id
    assert prepared.materialization_checksum == (
        operational_paper_session_materialization_specification_checksum(plan.specification)
    )
    assert prepared.authorization_binding == plan.specification.authorization_binding
    assert prepared.profile_binding == plan.specification.profile_binding
    assert prepared.mandate_binding == plan.specification.mandate_binding
    assert prepared.simulation_id == plan.specification.simulation_id
    assert prepared.prepared_by == ACTOR_ID
    assert prepared.prepared_at == NOW
    assert prepared.materialized_by is None
    assert prepared.materialized_at is None


def test_materialize_preserves_all_materialization_identity() -> None:
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=_plan(),
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    materialized = materialize_operational_paper_session_materialization(
        prepared,
        materialized_by=OTHER_ACTOR_ID,
        materialized_at=NOW + timedelta(seconds=1),
    )
    assert materialized.state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert materialized.record_version == 2
    assert materialized.materialization_id == prepared.materialization_id
    assert materialized.authorization_binding == prepared.authorization_binding
    assert materialized.profile_binding == prepared.profile_binding
    assert materialized.mandate_binding == prepared.mandate_binding
    assert materialized.simulation_id == prepared.simulation_id
    assert materialized.config_checksum == prepared.config_checksum
    assert materialized.session_id == prepared.session_id
    assert materialized.materialization_checksum == prepared.materialization_checksum
    assert materialized.prepared_by == prepared.prepared_by
    assert materialized.prepared_at == prepared.prepared_at
    assert materialized.materialized_by == OTHER_ACTOR_ID
    assert materialized.materialized_at == NOW + timedelta(seconds=1)


def test_materialized_state_is_terminal() -> None:
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=_plan(),
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    materialized = materialize_operational_paper_session_materialization(
        prepared,
        materialized_by=OTHER_ACTOR_ID,
        materialized_at=NOW,
    )
    with pytest.raises(OperationalPaperSessionMaterializationStateTransitionConflictError):
        materialize_operational_paper_session_materialization(
            materialized,
            materialized_by=OTHER_ACTOR_ID,
            materialized_at=NOW + timedelta(seconds=1),
        )


def test_materialization_cannot_predate_preparation() -> None:
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=_plan(),
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    with pytest.raises(InvalidOperationalPaperSessionMaterializationSpecificationError):
        materialize_operational_paper_session_materialization(
            prepared,
            materialized_by=OTHER_ACTOR_ID,
            materialized_at=NOW - timedelta(seconds=1),
        )


def test_partial_materialization_metadata_is_rejected() -> None:
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=_plan(),
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    with pytest.raises(InvalidOperationalPaperSessionMaterializationSpecificationError):
        replace(prepared, materialized_by=OTHER_ACTOR_ID)


def test_aggregate_rejects_tampered_materialization_checksum() -> None:
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=_plan(),
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    with pytest.raises(OperationalPaperSessionMaterializationChecksumMismatchError):
        replace(prepared, materialization_checksum="0" * 64)


def test_materialization_record_version_matches_postgresql_bigint_width() -> None:
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=_plan(),
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    maximum = replace(prepared, record_version=POSTGRESQL_BIGINT_MAX)
    assert maximum.record_version == POSTGRESQL_BIGINT_MAX
    with pytest.raises(OperationalPaperSessionMaterializationBoundsExceededError):
        materialize_operational_paper_session_materialization(
            maximum,
            materialized_by=OTHER_ACTOR_ID,
            materialized_at=NOW,
        )


def test_materialization_uuid_is_not_paper_session_identity() -> None:
    plan = _plan()
    first = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=plan,
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    second = prepare_operational_paper_session_materialization(
        materialization_id=UUID("90000000-0000-4000-8000-000000000009"),
        plan=plan,
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
    )
    assert first.materialization_id != second.materialization_id
    assert first.session_id == second.session_id
    assert first.config_checksum == second.config_checksum
    assert first.materialization_checksum == second.materialization_checksum


def test_distinct_authorization_provenance_can_share_paper_identity() -> None:
    profile = _profile_revision()
    authorization = _authorization(profile)
    authorization_checksum = operational_paper_capital_authorization_specification_checksum(
        authorization
    )
    other_authorization_id = UUID("40000000-0000-4000-8000-000000000099")

    plan_a = build_operational_paper_session_materialization_plan(
        authorization_id=AUTHORIZATION_ID,
        authorization_specification=authorization,
        authorization_checksum=authorization_checksum,
        profile_revision=profile,
    )
    plan_b = build_operational_paper_session_materialization_plan(
        authorization_id=other_authorization_id,
        authorization_specification=authorization,
        authorization_checksum=authorization_checksum,
        profile_revision=profile,
    )

    assert plan_a.config == plan_b.config
    assert plan_a.specification.config_checksum == plan_b.specification.config_checksum
    assert plan_a.specification.session_id == plan_b.specification.session_id
    assert (
        plan_a.specification.authorization_binding.authorization_id
        != plan_b.specification.authorization_binding.authorization_id
    )

    checksum_a = operational_paper_session_materialization_specification_checksum(
        plan_a.specification
    )
    checksum_b = operational_paper_session_materialization_specification_checksum(
        plan_b.specification
    )
    assert checksum_a != checksum_b
