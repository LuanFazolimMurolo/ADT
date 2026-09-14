"""Gate 2E official-paper financial-finality guards."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

import app.operational_paper_session_runs as runs
import tests.test_operational_paper_session_settlements_repository as settlement_fixtures
from app.domain.errors import OfficialPaperSimulationFinalityConflictError
from app.domain.models import SimulationStatus
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.repositories.operational_paper_session_settlements import (
    PostgresOperationalPaperSessionSettlementRepository,
)
from app.repositories.simulations import SimulationRepository
from tests.test_operational_paper_session_settlements_domain import (
    _binding,
    _portfolio,
)


def _run_specification(
    epoch: runs.OperationalPaperSessionRunEpoch,
) -> runs.OperationalPaperSessionRunEpochSpecification:
    return runs.OperationalPaperSessionRunEpochSpecification(
        epoch.schema_version,
        epoch.run_contract_version,
        epoch.activation_id,
        epoch.activation_checksum,
        epoch.materialization_id,
        epoch.materialization_checksum,
        epoch.authorization_binding,
        epoch.profile_binding,
        epoch.mandate_binding,
        epoch.simulation_id,
        epoch.session_id,
        epoch.config_checksum,
    )


def _binding_for(
    epoch: runs.OperationalPaperSessionRunEpoch,
):
    return replace(
        _binding(),
        session_id=epoch.session_id,
        config_checksum=epoch.config_checksum,
    )


def _flat_portfolio(initial_capital: Decimal):
    zero = Decimal("0")
    return replace(
        _portfolio(),
        quote_cash=initial_capital,
        base_quantity=zero,
        average_entry_price=zero,
        realized_pnl=zero,
        unrealized_pnl=zero,
        total_fees=zero,
        total_slippage_cost=zero,
        equity=initial_capital,
        peak_equity=initial_capital,
        drawdown=zero,
        cost_basis=zero,
        drawdown_pct=zero,
    )


async def _revoke_authorization(
    database,
    context,
    actor_id,
    *,
    now,
):
    return await PostgresOperationalPaperCapitalAuthorizationRepository(database).revoke(
        context.authorization.authorization_id,
        expected_record_version=context.authorization.record_version,
        actor_id=actor_id,
        now=now,
    )


async def _settle_context(
    database,
    context,
    actor_id,
):
    epoch = context.epoch
    assert epoch.terminal_at is not None

    return await PostgresOperationalPaperSessionSettlementRepository(database).settle(
        settlement_fixtures._intent(epoch),
        persisted_state_binding=_binding_for(epoch),
        initial_capital=context.authorization.authorized_capital,
        portfolio=_flat_portfolio(context.authorization.authorized_capital),
        actor_id=actor_id,
        idempotency_key="gate-2e:settle",
        now=epoch.terminal_at + timedelta(seconds=1),
    )


@pytest.mark.asyncio
async def test_settled_session_rejects_new_start_but_historical_replay_survives(
    database_url,
    database,
    auth_user_id,
) -> None:
    context = await settlement_fixtures._context(
        database_url,
        database,
        auth_user_id,
    )
    epoch = context.epoch

    settlement = await _settle_context(
        database,
        context,
        auth_user_id,
    )

    repository = PostgresOperationalPaperSessionRunRepository(database)
    start_intent = runs.OperationalPaperSessionRunEpochStartIntent(
        activation_id=epoch.activation_id,
        activation_checksum=epoch.activation_checksum,
    )

    replay = await repository.resolve_start_replay(
        start_intent,
        actor_id=epoch.start_requested_by,
        idempotency_key=epoch.start_idempotency_key,
    )
    assert replay == epoch

    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError):
        await repository.start(
            _run_specification(epoch),
            actor_id=auth_user_id,
            idempotency_key="gate-2e:new-start-after-settlement",
            now=settlement.settled_at + timedelta(seconds=1),
        )

    async with database.transaction() as connection:
        cursor = await connection.execute(
            """
            select count(*) as total
            from public.operational_paper_session_run_epochs
            where session_id = %s
            """,
            (epoch.session_id,),
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["total"] == 1


@pytest.mark.asyncio
async def test_official_simulation_nonterminal_epoch_blocks_terminalization(
    database_url,
    database,
    auth_user_id,
) -> None:
    context = await settlement_fixtures._context(
        database_url,
        database,
        auth_user_id,
        terminal=False,
    )

    epoch = context.epoch
    await _revoke_authorization(
        database,
        context,
        auth_user_id,
        now=epoch.start_requested_at + timedelta(seconds=1),
    )

    with pytest.raises(OfficialPaperSimulationFinalityConflictError):
        await SimulationRepository(database).transition(
            epoch.simulation_id,
            target_status=SimulationStatus.COMPLETED,
        )


@pytest.mark.asyncio
async def test_official_simulation_stopped_unsettled_blocks_terminalization(
    database_url,
    database,
    auth_user_id,
) -> None:
    context = await settlement_fixtures._context(
        database_url,
        database,
        auth_user_id,
    )

    epoch = context.epoch
    assert epoch.terminal_at is not None

    await _revoke_authorization(
        database,
        context,
        auth_user_id,
        now=epoch.terminal_at + timedelta(seconds=1),
    )

    with pytest.raises(OfficialPaperSimulationFinalityConflictError):
        await SimulationRepository(database).transition(
            epoch.simulation_id,
            target_status=SimulationStatus.COMPLETED,
        )


@pytest.mark.asyncio
async def test_official_simulation_failed_unsettled_blocks_terminalization(
    database_url,
    database,
    auth_user_id,
) -> None:
    context = await settlement_fixtures._context(
        database_url,
        database,
        auth_user_id,
        terminal=False,
    )

    epoch = context.epoch
    failed_at = epoch.start_requested_at + timedelta(seconds=1)

    failed = await PostgresOperationalPaperSessionRunRepository(database).fail_unclaimed(
        epoch.epoch_id,
        expected_record_version=epoch.record_version,
        code=runs.OperationalPaperSessionRunFailureCode.INTERNAL_ERROR,
        now=failed_at,
    )

    assert failed.observed_state is runs.OperationalPaperSessionRunObservedState.FAILED
    assert failed.terminal_at == failed_at

    await _revoke_authorization(
        database,
        context,
        auth_user_id,
        now=failed_at + timedelta(seconds=1),
    )

    with pytest.raises(OfficialPaperSimulationFinalityConflictError):
        await SimulationRepository(database).transition(
            failed.simulation_id,
            target_status=SimulationStatus.CANCELLED,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_status",
    [
        SimulationStatus.COMPLETED,
        SimulationStatus.CANCELLED,
    ],
)
async def test_official_simulation_fully_settled_history_can_terminalize(
    database_url,
    database,
    auth_user_id,
    target_status,
) -> None:
    context = await settlement_fixtures._context(
        database_url,
        database,
        auth_user_id,
    )

    settlement = await _settle_context(
        database,
        context,
        auth_user_id,
    )

    assert settlement.specification.simulation_id == context.epoch.simulation_id

    transitioned = await SimulationRepository(database).transition(
        context.epoch.simulation_id,
        target_status=target_status,
    )

    assert transitioned.simulation.status is target_status
    assert transitioned.simulation.ended_at is not None


@pytest.mark.asyncio
async def test_nonofficial_simulation_terminalization_is_unchanged(
    database,
    auth_user_id,
) -> None:
    simulations = SimulationRepository(database)

    created = await simulations.create_with_initial_capital(
        name="Gate 2E non-official control",
        initial_capital=Decimal("100"),
        currency="USDT",
        created_by=auth_user_id,
    )

    transitioned = await simulations.transition(
        created.simulation.id,
        target_status=SimulationStatus.COMPLETED,
    )

    assert transitioned.simulation.status is SimulationStatus.COMPLETED
    assert transitioned.simulation.ended_at is not None
