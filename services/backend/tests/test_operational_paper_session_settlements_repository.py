"""Phase 7-14 Gate 2C atomic official paper-session settlement tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

import app.operational_paper_session_runs as runs
import app.repositories.operational_paper_session_settlements as repository_module
from app.backtesting.domain import PortfolioSnapshot
from app.database import Database
from app.domain.errors import PersistenceError
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorization,
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalAuthorizationState,
    build_operational_paper_capital_authorization_specification,
)
from app.operational_paper_capital_eras import (
    OperationalPaperCapitalEra,
    OperationalPaperCapitalEraDesignationIntent,
)
from app.operational_paper_session_activations import (
    OperationalPaperSessionActivationCreateIntent,
    authorize_operational_paper_session_activation,
    build_operational_paper_session_activation_specification,
    operational_paper_session_activation_create_intent_fingerprint,
)
from app.operational_paper_session_materializations import (
    build_operational_paper_session_materialization_plan,
)
from app.operational_paper_session_settlements import (
    OperationalPaperSessionAlreadySettledError,
    OperationalPaperSessionSettlementEligibilityConflictError,
    OperationalPaperSessionSettlementIdempotencyConflictError,
    OperationalPaperSessionSettlementIntent,
)
from app.paper_trading.persisted_state import PaperPersistedStateBinding
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_capital_eras import (
    PostgresOperationalPaperCapitalEraRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.repositories.operational_paper_session_settlements import (
    PostgresOperationalPaperSessionSettlementRepository,
    operational_paper_session_settlement_from_row,
)
from tests.test_operational_paper_capital_authorizations_migration import (
    _seed_simulation,
)
from tests.test_operational_paper_session_activations_migration import (
    AUTHORIZED_AT,
)
from tests.test_operational_paper_session_activations_migration import (
    _insert as _insert_activation,
)
from tests.test_operational_paper_session_activations_migration import (
    _row as _activation_row,
)
from tests.test_operational_paper_session_materializations_repository import (
    PREPARED_AT,
)
from tests.test_operational_paper_session_profiles_repository import (
    BASE_TIME,
    _create,
    _resolver,
    _sources,
)
from tests.test_operational_paper_session_runs_migration import START_AT


@dataclass(frozen=True)
class _SettlementContext:
    era: OperationalPaperCapitalEra
    authorization: OperationalPaperCapitalAuthorization
    epoch: runs.OperationalPaperSessionRunEpoch


async def _context(
    database_url: str,
    database: Database,
    actor_id: UUID,
    *,
    terminal: bool = True,
) -> _SettlementContext:
    profile_repository = PostgresOperationalPaperSessionProfileRepository(database)

    profile_intent, _ = await _sources(database, actor_id)
    profile, profile_revision = await _create(
        profile_repository,
        profile_intent,
        actor_id,
        key=f"settlement-profile:{uuid4().hex}",
    )

    await profile_repository.approve(
        profile.profile_id,
        expected_revision=profile.current_revision,
        expected_checksum=profile_revision.specification_checksum,
        expected_record_version=profile.record_version,
        actor_id=actor_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=_resolver,
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(
            connection,
            actor_id,
            initial_capital=Decimal("100"),
        )

    # The official era must exist before the simulation acquires any
    # operational paper authorization/materialization/runtime use.
    era = await PostgresOperationalPaperCapitalEraRepository(database).designate(
        OperationalPaperCapitalEraDesignationIntent(simulation_id),
        actor_id=actor_id,
        idempotency_key=f"settlement-era:{uuid4().hex}",
        now=BASE_TIME + timedelta(seconds=1),
    )

    authorization_intent = OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=profile.profile_id,
            approved_revision=profile_revision.revision,
            specification_checksum=profile_revision.specification_checksum,
        ),
        simulation_id=simulation_id,
        quote_asset=profile_revision.specification.selected_instrument.pair.quote,
        authorized_capital=Decimal("40"),
    )
    authorization_specification = build_operational_paper_capital_authorization_specification(
        authorization_intent
    )
    authorization_repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    authorization = await authorization_repository.create(
        authorization_intent,
        actor_id=actor_id,
        idempotency_key=f"settlement-authorization:{uuid4().hex}",
        now=BASE_TIME + timedelta(seconds=4),
    )

    plan = build_operational_paper_session_materialization_plan(
        authorization_id=authorization.authorization_id,
        authorization_specification=authorization_specification,
        authorization_checksum=authorization.authorization_checksum,
        profile_revision=profile_revision,
    )

    materialization_repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await materialization_repository.prepare(
        plan,
        actor_id=actor_id,
        now=PREPARED_AT,
    )
    materialized = await materialization_repository.mark_materialized(
        prepared.materialization_id,
        expected_record_version=1,
        actor_id=actor_id,
        now=AUTHORIZED_AT - timedelta(minutes=1),
    )

    activation_specification = build_operational_paper_session_activation_specification(
        materialized
    )
    activation_intent = OperationalPaperSessionActivationCreateIntent(
        materialization_id=activation_specification.materialization_id,
        materialization_checksum=activation_specification.materialization_checksum,
    )
    activation = authorize_operational_paper_session_activation(
        activation_id=uuid4(),
        specification=activation_specification,
        authorized_by=actor_id,
        authorized_at=AUTHORIZED_AT,
        create_idempotency_key=f"settlement-activation:{uuid4().hex}",
        create_intent_fingerprint=(
            operational_paper_session_activation_create_intent_fingerprint(activation_intent)
        ),
    )

    with psycopg.connect(database_url) as connection:
        _insert_activation(connection, _activation_row(activation))

    run_repository = PostgresOperationalPaperSessionRunRepository(database)
    run_specification = runs.build_operational_paper_session_run_epoch_specification(activation)
    epoch = await run_repository.start(
        run_specification,
        actor_id=actor_id,
        idempotency_key=f"settlement-start:{uuid4().hex}",
        now=START_AT,
    )

    if terminal:
        command_intent = runs.OperationalPaperSessionRunEpochCommandIntent(
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=runs.OperationalPaperSessionRunCommandType.STOP,
            expected_record_version=epoch.record_version,
        )
        await run_repository.request_command(
            command_intent,
            actor_id=actor_id,
            idempotency_key=f"settlement-stop:{uuid4().hex}",
            now=START_AT + timedelta(seconds=1),
        )

        requested = await run_repository.get(epoch.epoch_id)
        assert requested is not None

        epoch = await run_repository.settle_unclaimed(
            requested.epoch_id,
            expected_record_version=requested.record_version,
            now=START_AT + timedelta(seconds=2),
        )

        assert epoch.desired_state is runs.OperationalPaperSessionRunDesiredState.STOPPED
        assert epoch.observed_state is runs.OperationalPaperSessionRunObservedState.STOPPED
        assert epoch.terminal_at is not None

    return _SettlementContext(
        era=era,
        authorization=authorization,
        epoch=epoch,
    )


def _binding(
    epoch: runs.OperationalPaperSessionRunEpoch,
) -> PaperPersistedStateBinding:
    return PaperPersistedStateBinding(
        session_id=epoch.session_id,
        config_checksum=epoch.config_checksum,
        state_id="1" * 64,
        state_checksum="2" * 64,
        dataset_version="3" * 64,
        source_checksum="4" * 64,
        timeline_id="5" * 64,
        timeline_content_checksum="6" * 64,
    )


def _portfolio(
    initial_capital: Decimal,
    delta: Decimal,
) -> PortfolioSnapshot:
    final_equity = initial_capital + delta
    peak_equity = max(initial_capital, final_equity)
    drawdown = peak_equity - final_equity
    drawdown_pct = Decimal("0") if peak_equity == 0 else drawdown / peak_equity * Decimal("100")

    return PortfolioSnapshot(
        quote_cash=final_equity,
        base_quantity=Decimal("0"),
        average_entry_price=Decimal("0"),
        realized_pnl=delta,
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("2"),
        total_slippage_cost=Decimal("3"),
        equity=final_equity,
        peak_equity=peak_equity,
        drawdown=drawdown,
        cost_basis=Decimal("0"),
        drawdown_pct=drawdown_pct,
    )


def _intent(
    epoch: runs.OperationalPaperSessionRunEpoch,
) -> OperationalPaperSessionSettlementIntent:
    return OperationalPaperSessionSettlementIntent(
        epoch.epoch_id,
        epoch.epoch_checksum,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delta", "expected_type"),
    [
        (Decimal("10"), "TRADE_PROFIT"),
        (Decimal("-10"), "TRADE_LOSS"),
        (Decimal("0"), None),
    ],
    ids=["profit", "loss", "zero"],
)
async def test_settlement_atomically_consumes_authorization_and_posts_exact_delta(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    delta: Decimal,
    expected_type: str | None,
) -> None:
    context = await _context(database_url, database, auth_user_id)
    epoch = context.epoch
    assert epoch.terminal_at is not None

    repository = PostgresOperationalPaperSessionSettlementRepository(database)
    initial_capital = context.authorization.authorized_capital
    settlement = await repository.settle(
        _intent(epoch),
        persisted_state_binding=_binding(epoch),
        initial_capital=initial_capital,
        portfolio=_portfolio(initial_capital, delta),
        actor_id=auth_user_id,
        idempotency_key=f"settlement:{expected_type or 'zero'}",
        now=(epoch.terminal_at + timedelta(seconds=1)).astimezone(UTC),
    )

    assert settlement.specification.simulation_id == context.authorization.simulation_id
    assert settlement.specification.era_id == context.era.era_id
    assert settlement.specification.epoch_id == epoch.epoch_id
    assert settlement.specification.authorization_id == context.authorization.authorization_id
    assert settlement.specification.session_id == epoch.session_id
    assert settlement.specification.config_checksum == epoch.config_checksum
    assert settlement.specification.initial_capital == initial_capital
    assert settlement.specification.settlement_delta == delta
    assert settlement.specification.realized_pnl == delta
    assert settlement.specification.unrealized_pnl == Decimal("0")
    assert settlement.specification.base_quantity == Decimal("0")

    assert await repository.get(settlement.settlement_id) == settlement
    assert await repository.get_by_session(epoch.session_id) == settlement

    with psycopg.connect(database_url) as connection:
        authorization_row = connection.execute(
            """
            select state, record_version, revoked_by
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (context.authorization.authorization_id,),
        ).fetchone()
        assert authorization_row == ("REVOKED", 2, auth_user_id)

        movements = connection.execute(
            """
            select id, type, amount
            from public.capital_movements
            where simulation_id = %s
            order by created_at, id
            """,
            (context.authorization.simulation_id,),
        ).fetchall()

        settlement_row = connection.execute(
            """
            select ledger_movement_id
            from public.operational_paper_session_settlements
            where settlement_id = %s
            """,
            (settlement.settlement_id,),
        ).fetchone()

    assert settlement_row is not None

    if expected_type is None:
        assert len(movements) == 1
        assert settlement.specification.ledger_movement_id is None
        assert settlement_row == (None,)
    else:
        assert len(movements) == 2
        movement_id, movement_type, amount = movements[-1]
        assert movement_type == expected_type
        assert amount == delta
        assert settlement.specification.ledger_movement_id == movement_id
        assert settlement_row == (movement_id,)

    assert all(row[1] != "FEE" for row in movements)


@pytest.mark.asyncio
async def test_settlement_exact_replay_does_not_double_post_or_reconsume(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    context = await _context(database_url, database, auth_user_id)
    epoch = context.epoch
    assert epoch.terminal_at is not None

    repository = PostgresOperationalPaperSessionSettlementRepository(database)
    intent = _intent(epoch)
    binding = _binding(epoch)
    initial_capital = context.authorization.authorized_capital
    portfolio = _portfolio(initial_capital, Decimal("10"))
    key = "settlement:replay"
    now = (epoch.terminal_at + timedelta(seconds=1)).astimezone(UTC)

    created = await repository.settle(
        intent,
        persisted_state_binding=binding,
        initial_capital=initial_capital,
        portfolio=portfolio,
        actor_id=auth_user_id,
        idempotency_key=key,
        now=now,
    )
    replayed = await repository.settle(
        intent,
        persisted_state_binding=binding,
        initial_capital=initial_capital,
        portfolio=portfolio,
        actor_id=auth_user_id,
        idempotency_key=key,
        now=now + timedelta(seconds=10),
    )

    assert replayed == created

    with psycopg.connect(database_url) as connection:
        counts = connection.execute(
            """
            select
                (
                    select count(*)
                    from public.operational_paper_session_settlements
                    where session_id = %s
                ),
                (
                    select count(*)
                    from public.capital_movements
                    where simulation_id = %s
                      and type in ('TRADE_PROFIT', 'TRADE_LOSS')
                )
            """,
            (
                epoch.session_id,
                context.authorization.simulation_id,
            ),
        ).fetchone()

        authorization_row = connection.execute(
            """
            select state, record_version
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (context.authorization.authorization_id,),
        ).fetchone()

    assert counts == (1, 1)
    assert authorization_row == ("REVOKED", 2)


@pytest.mark.asyncio
async def test_concurrent_same_key_settlement_replays_one_financial_effect(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    context = await _context(database_url, database, auth_user_id)
    epoch = context.epoch
    assert epoch.terminal_at is not None

    repository = PostgresOperationalPaperSessionSettlementRepository(database)
    intent = _intent(epoch)
    binding = _binding(epoch)
    initial_capital = context.authorization.authorized_capital
    portfolio = _portfolio(initial_capital, Decimal("10"))
    now = (epoch.terminal_at + timedelta(seconds=1)).astimezone(UTC)

    async def settle() -> object:
        return await repository.settle(
            intent,
            persisted_state_binding=binding,
            initial_capital=initial_capital,
            portfolio=portfolio,
            actor_id=auth_user_id,
            idempotency_key="settlement:race:same",
            now=now,
        )

    first, second = await asyncio.gather(settle(), settle())

    assert first == second

    with psycopg.connect(database_url) as connection:
        counts = connection.execute(
            """
            select
                (
                    select count(*)
                    from public.operational_paper_session_settlements
                    where session_id = %s
                ),
                (
                    select count(*)
                    from public.capital_movements
                    where simulation_id = %s
                      and type in ('TRADE_PROFIT', 'TRADE_LOSS')
                )
            """,
            (
                epoch.session_id,
                context.authorization.simulation_id,
            ),
        ).fetchone()

        authorization = connection.execute(
            """
            select state, record_version
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (context.authorization.authorization_id,),
        ).fetchone()

    assert counts == (1, 1)
    assert authorization == ("REVOKED", 2)


@pytest.mark.asyncio
async def test_concurrent_distinct_keys_have_one_settlement_winner(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    context = await _context(database_url, database, auth_user_id)
    epoch = context.epoch
    assert epoch.terminal_at is not None

    repository = PostgresOperationalPaperSessionSettlementRepository(database)
    intent = _intent(epoch)
    binding = _binding(epoch)
    initial_capital = context.authorization.authorized_capital
    portfolio = _portfolio(initial_capital, Decimal("10"))
    now = (epoch.terminal_at + timedelta(seconds=1)).astimezone(UTC)

    async def settle(key: str) -> object:
        return await repository.settle(
            intent,
            persisted_state_binding=binding,
            initial_capital=initial_capital,
            portfolio=portfolio,
            actor_id=auth_user_id,
            idempotency_key=key,
            now=now,
        )

    results = await asyncio.gather(
        settle("settlement:race:first"),
        settle("settlement:race:second"),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(
        failures[0],
        OperationalPaperSessionAlreadySettledError,
    )

    with psycopg.connect(database_url) as connection:
        counts = connection.execute(
            """
            select
                (
                    select count(*)
                    from public.operational_paper_session_settlements
                    where session_id = %s
                ),
                (
                    select count(*)
                    from public.capital_movements
                    where simulation_id = %s
                      and type in ('TRADE_PROFIT', 'TRADE_LOSS')
                )
            """,
            (
                epoch.session_id,
                context.authorization.simulation_id,
            ),
        ).fetchone()

        authorization = connection.execute(
            """
            select state, record_version
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (context.authorization.authorization_id,),
        ).fetchone()

    assert counts == (1, 1)
    assert authorization == ("REVOKED", 2)


@pytest.mark.asyncio
async def test_settlement_divergent_idempotency_replay_conflicts_before_epoch_lookup(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    context = await _context(database_url, database, auth_user_id)
    epoch = context.epoch
    assert epoch.terminal_at is not None

    repository = PostgresOperationalPaperSessionSettlementRepository(database)
    initial_capital = context.authorization.authorized_capital
    key = "settlement:divergent"

    await repository.settle(
        _intent(epoch),
        persisted_state_binding=_binding(epoch),
        initial_capital=initial_capital,
        portfolio=_portfolio(initial_capital, Decimal("10")),
        actor_id=auth_user_id,
        idempotency_key=key,
        now=(epoch.terminal_at + timedelta(seconds=1)).astimezone(UTC),
    )

    divergent = OperationalPaperSessionSettlementIntent(
        uuid4(),
        "9" * 64,
    )

    with pytest.raises(OperationalPaperSessionSettlementIdempotencyConflictError):
        await repository.settle(
            divergent,
            persisted_state_binding=_binding(epoch),
            initial_capital=initial_capital,
            portfolio=_portfolio(initial_capital, Decimal("10")),
            actor_id=auth_user_id,
            idempotency_key=key,
            now=(epoch.terminal_at + timedelta(seconds=2)).astimezone(UTC),
        )


@pytest.mark.asyncio
async def test_settlement_same_session_with_new_key_is_already_settled(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    context = await _context(database_url, database, auth_user_id)
    epoch = context.epoch
    assert epoch.terminal_at is not None

    repository = PostgresOperationalPaperSessionSettlementRepository(database)
    initial_capital = context.authorization.authorized_capital
    binding = _binding(epoch)
    portfolio = _portfolio(initial_capital, Decimal("10"))

    await repository.settle(
        _intent(epoch),
        persisted_state_binding=binding,
        initial_capital=initial_capital,
        portfolio=portfolio,
        actor_id=auth_user_id,
        idempotency_key="settlement:first",
        now=(epoch.terminal_at + timedelta(seconds=1)).astimezone(UTC),
    )

    with pytest.raises(OperationalPaperSessionAlreadySettledError):
        await repository.settle(
            _intent(epoch),
            persisted_state_binding=binding,
            initial_capital=initial_capital,
            portfolio=portfolio,
            actor_id=auth_user_id,
            idempotency_key="settlement:second",
            now=(epoch.terminal_at + timedelta(seconds=2)).astimezone(UTC),
        )


@pytest.mark.asyncio
async def test_nonterminal_epoch_is_rejected_without_consuming_or_posting(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    context = await _context(
        database_url,
        database,
        auth_user_id,
        terminal=False,
    )
    epoch = context.epoch
    initial_capital = context.authorization.authorized_capital

    repository = PostgresOperationalPaperSessionSettlementRepository(database)

    with pytest.raises(OperationalPaperSessionSettlementEligibilityConflictError):
        await repository.settle(
            _intent(epoch),
            persisted_state_binding=_binding(epoch),
            initial_capital=initial_capital,
            portfolio=_portfolio(initial_capital, Decimal("10")),
            actor_id=auth_user_id,
            idempotency_key="settlement:nonterminal",
            now=(START_AT + timedelta(seconds=10)).astimezone(UTC),
        )

    with psycopg.connect(database_url) as connection:
        authorization_row = connection.execute(
            """
            select state, record_version
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (context.authorization.authorization_id,),
        ).fetchone()
        trade_count = connection.execute(
            """
            select count(*)
            from public.capital_movements
            where simulation_id = %s
              and type in ('TRADE_PROFIT', 'TRADE_LOSS')
            """,
            (context.authorization.simulation_id,),
        ).fetchone()
        settlement_count = connection.execute(
            "select count(*) from public.operational_paper_session_settlements"
        ).fetchone()

    assert authorization_row == ("AUTHORIZED", 1)
    assert trade_count == (0,)
    assert settlement_count == (0,)


@pytest.mark.asyncio
async def test_failure_after_consumption_rolls_back_authorization_and_movement(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _context(database_url, database, auth_user_id)
    epoch = context.epoch
    assert epoch.terminal_at is not None
    initial_capital = context.authorization.authorized_capital

    async def _fail_insert(*args: object, **kwargs: object) -> object:
        raise PersistenceError()

    monkeypatch.setattr(
        repository_module,
        "insert_evidence",
        _fail_insert,
    )

    repository = PostgresOperationalPaperSessionSettlementRepository(database)

    with pytest.raises(PersistenceError):
        await repository.settle(
            _intent(epoch),
            persisted_state_binding=_binding(epoch),
            initial_capital=initial_capital,
            portfolio=_portfolio(initial_capital, Decimal("10")),
            actor_id=auth_user_id,
            idempotency_key="settlement:rollback",
            now=(epoch.terminal_at + timedelta(seconds=1)).astimezone(UTC),
        )

    with psycopg.connect(database_url) as connection:
        authorization_row = connection.execute(
            """
            select state, record_version, revoked_by, revoked_at
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (context.authorization.authorization_id,),
        ).fetchone()
        trade_count = connection.execute(
            """
            select count(*)
            from public.capital_movements
            where simulation_id = %s
              and type in ('TRADE_PROFIT', 'TRADE_LOSS')
            """,
            (context.authorization.simulation_id,),
        ).fetchone()
        settlement_count = connection.execute(
            "select count(*) from public.operational_paper_session_settlements"
        ).fetchone()

    assert authorization_row == ("AUTHORIZED", 1, None, None)
    assert trade_count == (0,)
    assert settlement_count == (0,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt_field",
    ["settlement_checksum", "settle_intent_fingerprint"],
)
async def test_settlement_hydrator_rejects_persisted_integrity_corruption(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    corrupt_field: str,
) -> None:
    context = await _context(database_url, database, auth_user_id)
    epoch = context.epoch
    assert epoch.terminal_at is not None
    initial_capital = context.authorization.authorized_capital

    repository = PostgresOperationalPaperSessionSettlementRepository(database)
    settlement = await repository.settle(
        _intent(epoch),
        persisted_state_binding=_binding(epoch),
        initial_capital=initial_capital,
        portfolio=_portfolio(initial_capital, Decimal("10")),
        actor_id=auth_user_id,
        idempotency_key=f"settlement:corrupt:{corrupt_field}",
        now=(epoch.terminal_at + timedelta(seconds=1)).astimezone(UTC),
    )

    with psycopg.connect(
        database_url,
        row_factory=dict_row,
    ) as connection:
        row = connection.execute(
            """
            select *
            from public.operational_paper_session_settlements
            where settlement_id = %s
            """,
            (settlement.settlement_id,),
        ).fetchone()

    assert row is not None
    corrupted: dict[str, object] = dict(row)
    corrupted[corrupt_field] = "0" * 64

    with pytest.raises(PersistenceError):
        operational_paper_session_settlement_from_row(corrupted)


@pytest.mark.asyncio
async def test_get_missing_contract(
    database: Database,
) -> None:
    repository = PostgresOperationalPaperSessionSettlementRepository(database)

    assert await repository.get(uuid4()) is None
    assert await repository.get_by_session("0" * 64) is None
