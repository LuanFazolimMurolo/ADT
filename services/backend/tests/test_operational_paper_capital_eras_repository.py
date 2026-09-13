"""Phase 7-14 Gate 2C official paper-capital era repository tests."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest

from app.database import Database
from app.domain.errors import PersistenceError
from app.operational_paper_capital_eras import (
    InvalidOperationalPaperCapitalEraSpecificationError,
    OperationalPaperCapitalEraDesignationIntent,
    OperationalPaperCapitalEraEligibilityConflictError,
    OperationalPaperCapitalEraIdempotencyConflictError,
    OperationalPaperCapitalEraSimulationAlreadyDesignatedError,
    operational_paper_capital_era_designation_intent_fingerprint,
)
from app.repositories.operational_paper_capital_eras import (
    PostgresOperationalPaperCapitalEraRepository,
    operational_paper_capital_era_from_row,
)
from tests.test_phase1a_postgres import _add_initial_capital, _add_simulation


def _seed_eligible_simulation(
    database_url: str,
    actor_id: UUID,
    *,
    initial_capital: Decimal = Decimal("100"),
) -> tuple[UUID, datetime]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _add_simulation(
            connection,
            creator_id=actor_id,
            initial_capital=initial_capital,
        )
        _add_initial_capital(
            connection,
            simulation_id=simulation_id,
            creator_id=actor_id,
            amount=initial_capital,
        )

        row = connection.execute(
            """
            select started_at
            from public.simulation_runs
            where id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert row is not None
    started_at = row[0]
    assert isinstance(started_at, datetime)
    assert started_at.tzinfo is not None

    return simulation_id, started_at


@pytest.mark.asyncio
async def test_era_repository_designate_persists_exact_evidence_without_ledger_write(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id, started_at = _seed_eligible_simulation(
        database_url,
        auth_user_id,
        initial_capital=Decimal("100.125"),
    )
    intent = OperationalPaperCapitalEraDesignationIntent(
        simulation_id=simulation_id,
    )
    now = started_at.astimezone(UTC) + timedelta(seconds=1)

    repository = PostgresOperationalPaperCapitalEraRepository(database)
    era = await repository.designate(
        intent,
        actor_id=auth_user_id,
        idempotency_key="official-era:happy",
        now=now,
    )

    assert era.simulation_id == simulation_id
    assert era.designated_by == auth_user_id
    assert era.designated_at == now
    assert era.initial_capital == Decimal("100.125")
    assert era.designation_idempotency_key == "official-era:happy"
    assert era.designation_intent_fingerprint == (
        operational_paper_capital_era_designation_intent_fingerprint(intent)
    )
    assert len(era.era_checksum) == 64

    by_id = await repository.get(era.era_id)
    by_simulation = await repository.get_by_simulation(simulation_id)

    assert by_id == era
    assert by_simulation == era

    with psycopg.connect(database_url) as connection:
        counts = connection.execute(
            """
            select
                (
                    select count(*)
                    from public.operational_paper_capital_eras
                    where simulation_id = %s
                ),
                (
                    select count(*)
                    from public.capital_movements
                    where simulation_id = %s
                )
            """,
            (simulation_id, simulation_id),
        ).fetchone()

    assert counts == (1, 1)


@pytest.mark.asyncio
async def test_era_repository_get_missing_and_invalid_identity_contract(
    database: Database,
) -> None:
    repository = PostgresOperationalPaperCapitalEraRepository(database)

    assert await repository.get(uuid4()) is None
    assert await repository.get_by_simulation(uuid4()) is None

    class _DatabaseMustNotBeAccessed:
        def transaction(self) -> object:
            raise AssertionError("invalid era identity reached PostgreSQL")

    guarded = PostgresOperationalPaperCapitalEraRepository(
        _DatabaseMustNotBeAccessed()  # type: ignore[arg-type]
    )

    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        await guarded.get(UUID(int=0))

    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        await guarded.get_by_simulation(UUID(int=0))


@pytest.mark.asyncio
async def test_era_repository_designate_input_validation_precedes_database() -> None:
    class _DatabaseMustNotBeAccessed:
        def transaction(self) -> object:
            raise AssertionError("invalid designation input reached PostgreSQL")

    repository = PostgresOperationalPaperCapitalEraRepository(
        _DatabaseMustNotBeAccessed()  # type: ignore[arg-type]
    )
    intent = OperationalPaperCapitalEraDesignationIntent(
        simulation_id=UUID("11111111-1111-1111-1111-111111111111"),
    )

    with pytest.raises(InvalidOperationalPaperCapitalEraSpecificationError):
        await repository.designate(
            intent,
            actor_id=UUID(int=0),
            idempotency_key="official-era:invalid",
            now=datetime.fromisoformat("2026-09-12T18:00:00+00:00"),
        )


@pytest.mark.asyncio
async def test_era_repository_exact_designation_replay_returns_committed_row_once(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id, started_at = _seed_eligible_simulation(
        database_url,
        auth_user_id,
    )
    intent = OperationalPaperCapitalEraDesignationIntent(
        simulation_id=simulation_id,
    )
    repository = PostgresOperationalPaperCapitalEraRepository(database)
    key = "official-era:replay"

    created = await repository.designate(
        intent,
        actor_id=auth_user_id,
        idempotency_key=key,
        now=started_at.astimezone(UTC) + timedelta(seconds=1),
    )
    replayed = await repository.designate(
        intent,
        actor_id=auth_user_id,
        idempotency_key=key,
        now=started_at.astimezone(UTC) + timedelta(hours=1),
    )

    assert replayed == created

    with psycopg.connect(database_url) as connection:
        count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_eras
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert count == (1,)


@pytest.mark.asyncio
async def test_era_repository_divergent_replay_conflicts_before_other_simulation_access(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id, started_at = _seed_eligible_simulation(
        database_url,
        auth_user_id,
    )
    repository = PostgresOperationalPaperCapitalEraRepository(database)
    key = "official-era:divergent"

    created = await repository.designate(
        OperationalPaperCapitalEraDesignationIntent(simulation_id),
        actor_id=auth_user_id,
        idempotency_key=key,
        now=started_at.astimezone(UTC) + timedelta(seconds=1),
    )

    divergent = OperationalPaperCapitalEraDesignationIntent(uuid4())

    with pytest.raises(OperationalPaperCapitalEraIdempotencyConflictError):
        await repository.designate(
            divergent,
            actor_id=auth_user_id,
            idempotency_key=key,
            now=started_at.astimezone(UTC) + timedelta(seconds=2),
        )

    assert await repository.get(created.era_id) == created


@pytest.mark.asyncio
async def test_era_repository_same_simulation_with_new_key_is_already_designated(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id, started_at = _seed_eligible_simulation(
        database_url,
        auth_user_id,
    )
    intent = OperationalPaperCapitalEraDesignationIntent(simulation_id)
    repository = PostgresOperationalPaperCapitalEraRepository(database)

    await repository.designate(
        intent,
        actor_id=auth_user_id,
        idempotency_key="official-era:first",
        now=started_at.astimezone(UTC) + timedelta(seconds=1),
    )

    with pytest.raises(OperationalPaperCapitalEraSimulationAlreadyDesignatedError):
        await repository.designate(
            intent,
            actor_id=auth_user_id,
            idempotency_key="official-era:second",
            now=started_at.astimezone(UTC) + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_era_repository_rejects_simulation_with_prior_financial_use(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id, started_at = _seed_eligible_simulation(
        database_url,
        auth_user_id,
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into public.capital_movements (
                simulation_id,
                type,
                amount,
                reason,
                created_by
            )
            values (%s, 'ADMIN_DEPOSIT', %s, %s, %s)
            """,
            (
                simulation_id,
                Decimal("1"),
                "Gate 2C prior-use probe",
                auth_user_id,
            ),
        )

    repository = PostgresOperationalPaperCapitalEraRepository(database)

    with pytest.raises(OperationalPaperCapitalEraEligibilityConflictError):
        await repository.designate(
            OperationalPaperCapitalEraDesignationIntent(simulation_id),
            actor_id=auth_user_id,
            idempotency_key="official-era:used",
            now=started_at.astimezone(UTC) + timedelta(seconds=1),
        )

    with psycopg.connect(database_url) as connection:
        count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_eras
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert count == (0,)


@pytest.mark.asyncio
async def test_era_repository_rejects_terminal_simulation(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id, started_at = _seed_eligible_simulation(
        database_url,
        auth_user_id,
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            update public.simulation_runs
            set status = 'COMPLETED',
                ended_at = %s
            where id = %s
            """,
            (
                started_at + timedelta(seconds=1),
                simulation_id,
            ),
        )

    repository = PostgresOperationalPaperCapitalEraRepository(database)

    with pytest.raises(OperationalPaperCapitalEraEligibilityConflictError):
        await repository.designate(
            OperationalPaperCapitalEraDesignationIntent(simulation_id),
            actor_id=auth_user_id,
            idempotency_key="official-era:terminal",
            now=started_at.astimezone(UTC) + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_era_repository_rejects_prior_operational_authorization(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from tests.test_operational_paper_session_materializations_repository import (
        _plan_context,
    )

    plan = await _plan_context(
        database_url,
        database,
        auth_user_id,
    )

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select started_at
            from public.simulation_runs
            where id = %s
            """,
            (plan.specification.simulation_id,),
        ).fetchone()

    assert row is not None
    started_at = row[0]
    assert isinstance(started_at, datetime)

    repository = PostgresOperationalPaperCapitalEraRepository(database)

    with pytest.raises(OperationalPaperCapitalEraEligibilityConflictError):
        await repository.designate(
            OperationalPaperCapitalEraDesignationIntent(plan.specification.simulation_id),
            actor_id=auth_user_id,
            idempotency_key="official-era:operational-used",
            now=started_at.astimezone(UTC) + timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_era_row_reconstruction_rejects_checksum_corruption(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id, started_at = _seed_eligible_simulation(
        database_url,
        auth_user_id,
    )

    era = await PostgresOperationalPaperCapitalEraRepository(database).designate(
        OperationalPaperCapitalEraDesignationIntent(simulation_id),
        actor_id=auth_user_id,
        idempotency_key="official-era:checksum",
        now=started_at.astimezone(UTC) + timedelta(seconds=1),
    )

    row: dict[str, object] = {field.name: getattr(era, field.name) for field in fields(era)}
    row["era_checksum"] = "0" * 64

    assert row["era_checksum"] != era.era_checksum

    with pytest.raises(PersistenceError):
        operational_paper_capital_era_from_row(row)
