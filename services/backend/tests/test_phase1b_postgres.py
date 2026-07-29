"""Repository and persistence integration tests against disposable PostgreSQL."""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest

from app.database import Database
from app.domain.errors import (
    ActiveSimulationExistsError,
    InsufficientBalanceError,
    InvalidFinancialAmountError,
    SettingNotFoundError,
    SimulationTerminalError,
)
from app.domain.models import (
    AdministrativeMovementType,
    JsonObject,
    LedgerMovementType,
    SimulationStatus,
)
from app.repositories.admins import AdminRepository
from app.repositories.capital_movements import CapitalMovementRepository
from app.repositories.public_simulations import PublicSimulationRepository
from app.repositories.settings import SettingsRepository
from app.repositories.simulations import SimulationRepository
from app.services.capital_movements import CapitalMovementService
from app.services.settings import SettingsService
from app.services.simulations import SimulationService


def _simulation_service(database: Database) -> SimulationService:
    return SimulationService(SimulationRepository(database))


def _movement_service(database: Database) -> CapitalMovementService:
    return CapitalMovementService(CapitalMovementRepository(database))


async def test_database_pool_health_transaction_and_clean_shutdown(database_url: str) -> None:
    """The pool opens lazily, serves transactions, and becomes unusable after close."""
    database = Database(database_url, min_size=1, max_size=2, timeout=2)
    assert database.is_open is False

    await database.open()
    assert database.is_open is True
    assert await database.health_check() is True

    async with database.transaction() as connection:
        cursor = await connection.execute("select 42 as answer")
        assert await cursor.fetchone() == {"answer": 42}

    await database.close()
    assert database.is_open is False
    assert await database.health_check() is False


async def test_database_transaction_rolls_back_on_error(
    database: Database,
    database_url: str,
) -> None:
    """An exception inside the explicit transaction cannot leave a partial write."""
    audit_id = uuid4()

    with pytest.raises(RuntimeError, match="force rollback"):
        async with database.transaction() as connection:
            await connection.execute(
                """
                insert into public.audit_logs (id, action, entity_type)
                values (%s, 'TEST_TRANSACTION', 'TEST')
                """,
                (audit_id,),
            )
            raise RuntimeError("force rollback")

    with psycopg.connect(database_url, autocommit=True) as connection:
        row = connection.execute(
            "select exists(select 1 from public.audit_logs where id = %s)",
            (audit_id,),
        ).fetchone()
    assert row == (False,)


async def test_admin_repository_uses_database_allow_list(
    database: Database,
    admin_user_id: UUID,
) -> None:
    """Admin status comes from app_admins, not from caller-provided metadata."""
    repository = AdminRepository(database)

    assert await repository.is_admin(admin_user_id) is True
    assert await repository.is_admin(uuid4()) is False


async def test_simulation_and_initial_capital_are_created_atomically(
    database: Database,
    database_url: str,
    auth_user_id: UUID,
) -> None:
    """One service call persists the ACTIVE run and its opening ledger entry."""
    service = _simulation_service(database)

    details = await service.create(
        name="  Carteira de teste  ",
        initial_capital=Decimal("1000.125"),
        currency="brl",
        created_by=auth_user_id,
    )

    assert details.simulation.name == "Carteira de teste"
    assert details.simulation.status is SimulationStatus.ACTIVE
    assert details.simulation.currency == "BRL"
    assert details.simulation.initial_capital == Decimal("1000.12500000")
    assert details.current_balance == Decimal("1000.12500000")
    assert details.total_profit_loss == Decimal("0E-8")

    with psycopg.connect(database_url, autocommit=True) as connection:
        persisted = connection.execute(
            """
            select
                simulation.status,
                movement.type,
                movement.amount,
                movement.created_by
            from public.simulation_runs as simulation
            join public.capital_movements as movement
              on movement.simulation_id = simulation.id
            where simulation.id = %s
            """,
            (details.simulation.id,),
        ).fetchall()
    assert persisted == [
        (
            "ACTIVE",
            "INITIAL_CAPITAL",
            Decimal("1000.12500000"),
            auth_user_id,
        )
    ]


async def test_second_active_simulation_is_a_domain_conflict(
    database: Database,
    database_url: str,
    auth_user_id: UUID,
) -> None:
    """The unique ACTIVE index is translated without leaking PostgreSQL details."""
    service = _simulation_service(database)
    await service.create(
        name="First",
        initial_capital=Decimal("100"),
        currency="BRL",
        created_by=auth_user_id,
    )

    with pytest.raises(ActiveSimulationExistsError) as captured_error:
        await service.create(
            name="Second",
            initial_capital=Decimal("200"),
            currency="BRL",
            created_by=auth_user_id,
        )

    assert "simulation_runs_single_active_uidx" not in str(captured_error.value)
    with psycopg.connect(database_url, autocommit=True) as connection:
        counts = connection.execute(
            """
            select
                (select count(*) from public.simulation_runs),
                (select count(*) from public.capital_movements)
            """
        ).fetchone()
    assert counts == (1, 1)


@pytest.mark.parametrize(
    "invalid_capital",
    [
        Decimal("0"),
        Decimal("-0.00000001"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
    ids=["zero", "negative", "nan", "infinity", "negative-infinity"],
)
async def test_simulation_service_rejects_invalid_initial_capital(
    database: Database,
    database_url: str,
    auth_user_id: UUID,
    invalid_capital: Decimal,
) -> None:
    """Input validation rejects invalid values before opening a persisted run."""
    service = _simulation_service(database)

    with pytest.raises(InvalidFinancialAmountError):
        await service.create(
            name="Invalid",
            initial_capital=invalid_capital,
            currency="BRL",
            created_by=auth_user_id,
        )

    with psycopg.connect(database_url, autocommit=True) as connection:
        count = connection.execute("select count(*) from public.simulation_runs").fetchone()
    assert count == (0,)


@pytest.mark.parametrize(
    "invalid_capital",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("1000000000000"),
    ],
    ids=["zero", "negative", "nan", "infinity", "negative-infinity", "overflow"],
)
async def test_repository_translates_database_capital_constraints(
    database: Database,
    database_url: str,
    auth_user_id: UUID,
    invalid_capital: Decimal,
) -> None:
    """PostgreSQL remains authoritative when callers bypass service validation."""
    repository = SimulationRepository(database)

    with pytest.raises(InvalidFinancialAmountError):
        await repository.create_with_initial_capital(
            name="Database rejection",
            initial_capital=invalid_capital,
            currency="BRL",
            created_by=auth_user_id,
        )

    with psycopg.connect(database_url, autocommit=True) as connection:
        counts = connection.execute(
            """
            select
                (select count(*) from public.simulation_runs),
                (select count(*) from public.capital_movements)
            """
        ).fetchone()
    assert counts == (0, 0)


async def test_simulation_listing_returns_items_total_and_newest_first(
    database: Database,
    auth_user_id: UUID,
) -> None:
    """Pagination preserves the full count and stable newest-first ordering."""
    service = _simulation_service(database)
    first = await service.create(
        name="Older",
        initial_capital=Decimal("100"),
        currency="BRL",
        created_by=auth_user_id,
    )
    await service.complete(first.simulation.id)
    second = await service.create(
        name="Newer",
        initial_capital=Decimal("200"),
        currency="USD",
        created_by=auth_user_id,
    )

    items, total = await service.list(limit=1, offset=0)

    assert total == 2
    assert [item.simulation.id for item in items] == [second.simulation.id]


async def test_public_repository_reads_only_the_safe_calculated_view(
    database: Database,
    auth_user_id: UUID,
) -> None:
    """The public repository returns calculated totals without an internal UUID."""
    simulation = await _simulation_service(database).create(
        name="Public paper run",
        initial_capital=Decimal("100"),
        currency="BRL",
        created_by=auth_user_id,
    )
    async with database.transaction() as connection:
        for movement_type, amount, reason in (
            ("TRADE_PROFIT", Decimal("12.5"), "Paper profit"),
            ("FEE", Decimal("-2.5"), "Paper fee"),
            ("ADMIN_DEPOSIT", Decimal("10"), "Paper deposit"),
        ):
            await connection.execute(
                """
                insert into public.capital_movements (
                    simulation_id,
                    type,
                    amount,
                    reason,
                    created_by
                )
                values (%s, %s, %s, %s, %s)
                """,
                (
                    simulation.simulation.id,
                    movement_type,
                    amount,
                    reason,
                    auth_user_id,
                ),
            )

    summary = await PublicSimulationRepository(database).get_active()

    assert summary is not None
    assert summary.simulation_name == "Public paper run"
    assert summary.current_balance == Decimal("120.00000000")
    assert summary.total_profit_loss == Decimal("10.00000000")
    assert not hasattr(summary, "simulation_id")


async def test_admin_movements_are_appended_and_balances_are_calculated(
    database: Database,
    auth_user_id: UUID,
) -> None:
    """Deposit, withdrawal, and adjustment map to immutable ledger types."""
    simulation = await _simulation_service(database).create(
        name="Movement run",
        initial_capital=Decimal("100"),
        currency="BRL",
        created_by=auth_user_id,
    )
    service = _movement_service(database)

    deposit = await service.create(
        simulation_id=simulation.simulation.id,
        movement_type=AdministrativeMovementType.DEPOSIT,
        amount=Decimal("25"),
        reason="  Paper deposit  ",
        created_by=auth_user_id,
    )
    withdrawal = await service.create(
        simulation_id=simulation.simulation.id,
        movement_type=AdministrativeMovementType.WITHDRAWAL,
        amount=Decimal("-40"),
        reason="Paper withdrawal",
        created_by=auth_user_id,
    )
    adjustment = await service.create(
        simulation_id=simulation.simulation.id,
        movement_type=AdministrativeMovementType.ADJUSTMENT,
        amount=Decimal("-10"),
        reason="Paper correction",
        created_by=auth_user_id,
    )

    assert deposit.type is LedgerMovementType.ADMIN_DEPOSIT
    assert deposit.reason == "Paper deposit"
    assert withdrawal.type is LedgerMovementType.ADMIN_WITHDRAWAL
    assert adjustment.type is LedgerMovementType.ADJUSTMENT

    listed, total = await service.list(
        simulation.simulation.id,
        limit=20,
        offset=0,
    )
    details = await _simulation_service(database).get(simulation.simulation.id)

    assert total == 4
    assert [movement.type for movement in listed] == [
        LedgerMovementType.INITIAL_CAPITAL,
        LedgerMovementType.ADMIN_DEPOSIT,
        LedgerMovementType.ADMIN_WITHDRAWAL,
        LedgerMovementType.ADJUSTMENT,
    ]
    assert details.current_balance == Decimal("75.00000000")


async def test_insufficient_withdrawal_is_a_safe_domain_conflict(
    database: Database,
    database_url: str,
    auth_user_id: UUID,
) -> None:
    """The trigger rejection is translated and does not append a movement."""
    simulation = await _simulation_service(database).create(
        name="Protected balance",
        initial_capital=Decimal("100"),
        currency="BRL",
        created_by=auth_user_id,
    )

    with pytest.raises(InsufficientBalanceError) as captured_error:
        await _movement_service(database).create(
            simulation_id=simulation.simulation.id,
            movement_type=AdministrativeMovementType.WITHDRAWAL,
            amount=Decimal("-100.00000001"),
            reason="Rejected withdrawal",
            created_by=auth_user_id,
        )

    assert "balance negative" not in str(captured_error.value)
    with psycopg.connect(database_url, autocommit=True) as connection:
        ledger = connection.execute(
            """
            select type, amount
            from public.capital_movements
            where simulation_id = %s
            order by created_at, id
            """,
            (simulation.simulation.id,),
        ).fetchall()
    assert ledger == [("INITIAL_CAPITAL", Decimal("100.00000000"))]


async def test_movement_metadata_is_written_to_immutable_audit_log(
    database: Database,
    database_url: str,
    auth_user_id: UUID,
) -> None:
    """Optional movement metadata is persisted in the append-only audit ledger."""
    simulation = await _simulation_service(database).create(
        name="Metadata run",
        initial_capital=Decimal("100"),
        currency="BRL",
        created_by=auth_user_id,
    )
    service = _movement_service(database)
    metadata: JsonObject = {
        "source": "local-test",
        "approved": True,
        "sequence": 7,
    }

    movement = await service.create(
        simulation_id=simulation.simulation.id,
        movement_type=AdministrativeMovementType.DEPOSIT,
        amount=Decimal("5"),
        reason="Metadata deposit",
        created_by=auth_user_id,
        metadata=metadata,
    )
    listed, total = await service.list(
        simulation.simulation.id,
        limit=20,
        offset=0,
    )

    assert movement.metadata == metadata
    assert total == 2
    assert listed[-1].metadata == metadata
    with psycopg.connect(database_url, autocommit=True) as connection:
        audit = connection.execute(
            """
            select actor_user_id, action, entity_type, entity_id, metadata
            from public.audit_logs
            where entity_id = %s
            """,
            (movement.id,),
        ).fetchone()
    assert audit == (
        auth_user_id,
        "CAPITAL_MOVEMENT_METADATA_RECORDED",
        "CAPITAL_MOVEMENT",
        movement.id,
        metadata,
    )


def test_public_movement_type_excludes_initial_capital() -> None:
    """The service-facing movement enum has no INITIAL_CAPITAL option."""
    with pytest.raises(ValueError):
        AdministrativeMovementType("INITIAL_CAPITAL")


@pytest.mark.parametrize(
    ("target_status", "method_name"),
    [
        (SimulationStatus.COMPLETED, "complete"),
        (SimulationStatus.CANCELLED, "cancel"),
    ],
)
async def test_simulation_terminal_transitions_are_one_way(
    database: Database,
    auth_user_id: UUID,
    target_status: SimulationStatus,
    method_name: str,
) -> None:
    """Complete/cancel sets ended_at and cannot be repeated."""
    service = _simulation_service(database)
    simulation = await service.create(
        name="Terminal run",
        initial_capital=Decimal("100"),
        currency="BRL",
        created_by=auth_user_id,
    )
    transition = cast(
        object,
        getattr(service, method_name),
    )
    assert callable(transition)

    transitioned = await transition(simulation.simulation.id)

    assert transitioned.simulation.status is target_status
    assert transitioned.simulation.ended_at is not None
    with pytest.raises(SimulationTerminalError):
        await transition(simulation.simulation.id)


async def test_terminal_simulation_rejects_new_admin_movement(
    database: Database,
    auth_user_id: UUID,
) -> None:
    """Services do not append new ledger entries after a run has ended."""
    simulation_service = _simulation_service(database)
    simulation = await simulation_service.create(
        name="Ended run",
        initial_capital=Decimal("100"),
        currency="BRL",
        created_by=auth_user_id,
    )
    await simulation_service.complete(simulation.simulation.id)

    with pytest.raises(SimulationTerminalError):
        await _movement_service(database).create(
            simulation_id=simulation.simulation.id,
            movement_type=AdministrativeMovementType.DEPOSIT,
            amount=Decimal("10"),
            reason="Too late",
            created_by=auth_user_id,
        )


async def test_settings_list_and_update_only_value_and_actor(
    database: Database,
    auth_user_id: UUID,
) -> None:
    """Setting updates preserve identity/description and record the administrator."""
    service = SettingsService(SettingsRepository(database))
    before_items = await service.list()
    before = next(item for item in before_items if item.key == "paper_trading_enabled")

    updated = await service.update_value(
        "paper_trading_enabled",
        value=False,
        updated_by=auth_user_id,
    )
    after_items = await service.list()

    assert [item.key for item in after_items] == sorted(item.key for item in after_items)
    assert updated.key == before.key
    assert updated.value is False
    assert updated.description == before.description
    assert updated.is_public is before.is_public
    assert updated.created_at == before.created_at
    assert updated.updated_by == auth_user_id
    assert updated.updated_at >= before.updated_at


async def test_missing_setting_is_not_created(
    database: Database,
    auth_user_id: UUID,
) -> None:
    """PATCH semantics cannot silently upsert an unknown configuration key."""
    service = SettingsService(SettingsRepository(database))

    with pytest.raises(SettingNotFoundError):
        await service.update_value(
            "missing-setting",
            value={"unexpected": True},
            updated_by=auth_user_id,
        )

    assert len(await service.list()) == 4
