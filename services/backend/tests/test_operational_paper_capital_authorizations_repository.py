"""Gate 2C cross-app reservation-conflict translation tests."""

from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest

from app.database import Database
from app.database.pool import _is_persistence_unavailable_operational_error
from app.domain.models import LedgerMovementType, SimulationStatus
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalReservationConflictError,
    build_operational_paper_capital_authorization_specification,
    operational_paper_capital_authorization_create_intent_fingerprint,
    operational_paper_capital_authorization_specification_checksum,
)
from app.repositories.capital_movements import CapitalMovementRepository
from app.repositories.simulations import SimulationRepository
from tests.test_operational_paper_capital_authorizations_migration import (
    BASE_TIME,
    _seed_approved_profile,
    _seed_simulation,
)


def _seed_authorized_reservation(
    database_url: str,
    actor_id: UUID,
    *,
    amount: Decimal = Decimal("80"),
) -> UUID:
    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(
            connection,
            actor_id,
            initial_capital=Decimal("100"),
        )

        profile_row = connection.execute(
            """
            select approved_revision, approved_checksum
            from public.operational_paper_session_profiles
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone()

        assert profile_row is not None
        approved_revision, approved_checksum = profile_row
        assert isinstance(approved_revision, int)
        assert isinstance(approved_checksum, str)

        binding = OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=profile_id,
            approved_revision=approved_revision,
            specification_checksum=approved_checksum,
        )
        intent = OperationalPaperCapitalAuthorizationCreateIntent(
            profile_binding=binding,
            simulation_id=simulation_id,
            quote_asset="USDT",
            authorized_capital=amount,
        )
        specification = build_operational_paper_capital_authorization_specification(intent)
        authorization_checksum = operational_paper_capital_authorization_specification_checksum(
            specification
        )
        intent_fingerprint = operational_paper_capital_authorization_create_intent_fingerprint(
            intent
        )
        authorization_id = uuid4()

        connection.execute(
            """
            insert into public.operational_paper_capital_authorizations (
                authorization_id,
                schema_version,
                state,
                record_version,
                profile_id,
                profile_approved_revision,
                profile_specification_checksum,
                simulation_id,
                quote_asset,
                authorized_capital,
                authorization_checksum,
                created_by,
                created_at,
                create_idempotency_key,
                create_intent_fingerprint
            )
            values (
                %s, 1, 'AUTHORIZED', 1,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            """,
            (
                authorization_id,
                profile_id,
                approved_revision,
                approved_checksum,
                simulation_id,
                intent.quote_asset,
                intent.authorized_capital,
                authorization_checksum,
                actor_id,
                BASE_TIME,
                f"capital-auth:{authorization_id}",
                intent_fingerprint,
            ),
        )

    return simulation_id


@pytest.mark.asyncio
async def test_capital_movement_reservation_conflict_is_translated(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id = _seed_authorized_reservation(database_url, auth_user_id)

    repository = CapitalMovementRepository(database)
    with pytest.raises(OperationalPaperCapitalReservationConflictError):
        await repository.create(
            simulation_id=simulation_id,
            movement_type=LedgerMovementType.ADMIN_WITHDRAWAL,
            amount=Decimal("-30"),
            reason="Gate 2C reservation conflict probe",
            created_by=auth_user_id,
            metadata=None,
        )


@pytest.mark.asyncio
async def test_simulation_terminalization_reservation_conflict_is_translated(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    simulation_id = _seed_authorized_reservation(database_url, auth_user_id)

    repository = SimulationRepository(database)
    with pytest.raises(OperationalPaperCapitalReservationConflictError):
        await repository.transition(
            simulation_id,
            target_status=SimulationStatus.COMPLETED,
        )


def test_pool_operational_error_classification_is_sqlstate_aware() -> None:
    assert _is_persistence_unavailable_operational_error(
        psycopg.errors.ConnectionFailure("connection failure")
    )
    assert _is_persistence_unavailable_operational_error(
        psycopg.errors.CannotConnectNow("cannot connect now")
    )
    assert not _is_persistence_unavailable_operational_error(
        psycopg.errors.ObjectNotInPrerequisiteState("state conflict")
    )
    assert not _is_persistence_unavailable_operational_error(
        psycopg.errors.SerializationFailure("serialization conflict")
    )
    assert not _is_persistence_unavailable_operational_error(
        psycopg.errors.lookup("28000")("authorization failure")
    )
    assert not _is_persistence_unavailable_operational_error(
        psycopg.errors.lookup("57014")("query cancelled")
    )


@pytest.mark.asyncio
async def test_authorization_repository_get_round_trips_strict_persisted_row(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationState,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    simulation_id = _seed_authorized_reservation(database_url, auth_user_id)

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select authorization_id, profile_id
            from public.operational_paper_capital_authorizations
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert row is not None
    authorization_id, profile_id = row

    authorization = await PostgresOperationalPaperCapitalAuthorizationRepository(database).get(
        authorization_id
    )

    assert authorization is not None
    assert authorization.authorization_id == authorization_id
    assert authorization.state is OperationalPaperCapitalAuthorizationState.AUTHORIZED
    assert authorization.record_version == 1
    assert authorization.profile_binding.profile_id == profile_id
    assert authorization.profile_binding.approved_revision == 1
    assert authorization.simulation_id == simulation_id
    assert authorization.quote_asset == "USDT"
    assert authorization.authorized_capital == Decimal("80")
    assert authorization.revoked_by is None
    assert authorization.revoked_at is None


@pytest.mark.asyncio
async def test_authorization_repository_get_missing_and_invalid_input_contract(
    database: Database,
) -> None:
    from uuid import UUID, uuid4

    from app.operational_paper_capital_authorizations import (
        InvalidOperationalPaperCapitalAuthorizationSpecificationError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    assert await repository.get(uuid4()) is None

    class _DatabaseMustNotBeAccessed:
        def transaction(self) -> object:
            raise AssertionError("invalid authorization id reached PostgreSQL")

    guarded_repository = PostgresOperationalPaperCapitalAuthorizationRepository(
        _DatabaseMustNotBeAccessed()  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        await guarded_repository.get(UUID(int=0))


def test_authorization_row_reconstruction_rejects_checksum_corruption() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.domain.errors import PersistenceError
    from app.repositories.operational_paper_capital_authorizations import (
        operational_paper_capital_authorization_from_row,
    )

    row: dict[str, object] = {
        "authorization_id": uuid4(),
        "schema_version": 1,
        "state": "AUTHORIZED",
        "record_version": 1,
        "profile_id": uuid4(),
        "profile_approved_revision": 1,
        "profile_specification_checksum": "a" * 64,
        "simulation_id": uuid4(),
        "quote_asset": "USDT",
        "authorized_capital": Decimal("10"),
        "authorization_checksum": "0" * 64,
        "created_by": uuid4(),
        "created_at": datetime(2026, 8, 27, 12, tzinfo=UTC),
        "revoked_by": None,
        "revoked_at": None,
        "create_idempotency_key": "capital-auth:strict-row",
        "create_intent_fingerprint": "b" * 64,
    }

    with pytest.raises(PersistenceError):
        operational_paper_capital_authorization_from_row(row)


def _create_intent_sources(
    database_url: str,
    actor_id: UUID,
    *,
    amount: Decimal = Decimal("40"),
) -> tuple[object, UUID, UUID]:
    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationCreateIntent,
        OperationalPaperCapitalAuthorizationProfileBinding,
    )

    profile_id = _seed_approved_profile(database_url, actor_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        profile_row = connection.execute(
            """
            select approved_revision, approved_checksum
            from public.operational_paper_session_profiles
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone()
        assert profile_row is not None
        approved_revision, approved_checksum = profile_row
        assert isinstance(approved_revision, int)
        assert isinstance(approved_checksum, str)
        simulation_id = _seed_simulation(
            connection,
            actor_id,
            initial_capital=Decimal("100"),
        )

    intent = OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=profile_id,
            approved_revision=approved_revision,
            specification_checksum=approved_checksum,
        ),
        simulation_id=simulation_id,
        quote_asset="USDT",
        authorized_capital=amount,
    )
    return intent, profile_id, simulation_id


@pytest.mark.asyncio
async def test_authorization_repository_create_persists_exact_reservation_without_ledger_write(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationCreateIntent,
        OperationalPaperCapitalAuthorizationState,
        operational_paper_capital_authorization_create_intent_fingerprint,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    raw_intent, profile_id, simulation_id = _create_intent_sources(
        database_url,
        auth_user_id,
    )
    assert isinstance(raw_intent, OperationalPaperCapitalAuthorizationCreateIntent)

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    authorization = await repository.create(
        raw_intent,
        actor_id=auth_user_id,
        idempotency_key="capital-create:happy",
        now=datetime(2026, 8, 27, 14, tzinfo=UTC),
    )

    assert authorization.state is OperationalPaperCapitalAuthorizationState.AUTHORIZED
    assert authorization.record_version == 1
    assert authorization.profile_binding.profile_id == profile_id
    assert authorization.simulation_id == simulation_id
    assert authorization.quote_asset == "USDT"
    assert authorization.authorized_capital == Decimal("40")
    assert authorization.create_intent_fingerprint == (
        operational_paper_capital_authorization_create_intent_fingerprint(raw_intent)
    )

    with psycopg.connect(database_url) as connection:
        counts = connection.execute(
            """
            select
                (
                    select count(*)
                    from public.operational_paper_capital_authorizations
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
async def test_authorization_repository_exact_create_replay_returns_committed_row_once(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationCreateIntent,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    raw_intent, _, simulation_id = _create_intent_sources(
        database_url,
        auth_user_id,
    )
    assert isinstance(raw_intent, OperationalPaperCapitalAuthorizationCreateIntent)

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    now = datetime(2026, 8, 27, 14, tzinfo=UTC)

    created = await repository.create(
        raw_intent,
        actor_id=auth_user_id,
        idempotency_key="capital-create:replay",
        now=now,
    )
    replayed = await repository.create(
        raw_intent,
        actor_id=auth_user_id,
        idempotency_key="capital-create:replay",
        now=now + timedelta(hours=1),
    )

    assert replayed == created

    with psycopg.connect(database_url) as connection:
        count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert count == (1,)


@pytest.mark.asyncio
async def test_authorization_repository_divergent_create_replay_conflicts_without_second_row(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationCreateIntent,
        OperationalPaperCapitalAuthorizationIdempotencyConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    raw_intent, _, simulation_id = _create_intent_sources(
        database_url,
        auth_user_id,
    )
    assert isinstance(raw_intent, OperationalPaperCapitalAuthorizationCreateIntent)

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    now = datetime(2026, 8, 27, 14, tzinfo=UTC)
    key = "capital-create:divergent"

    created = await repository.create(
        raw_intent,
        actor_id=auth_user_id,
        idempotency_key=key,
        now=now,
    )

    divergent = replace(raw_intent, authorized_capital=Decimal("41"))

    with pytest.raises(OperationalPaperCapitalAuthorizationIdempotencyConflictError):
        await repository.create(
            divergent,
            actor_id=auth_user_id,
            idempotency_key=key,
            now=now,
        )

    persisted = await repository.get(created.authorization_id)
    assert persisted == created

    with psycopg.connect(database_url) as connection:
        count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert count == (1,)


@pytest.mark.asyncio
async def test_authorization_repository_create_input_validation_precedes_database() -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    from app.operational_paper_capital_authorizations import (
        InvalidOperationalPaperCapitalAuthorizationSpecificationError,
        OperationalPaperCapitalAuthorizationCreateIntent,
        OperationalPaperCapitalAuthorizationProfileBinding,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    class _DatabaseMustNotBeAccessed:
        def transaction(self) -> object:
            raise AssertionError("invalid create input reached PostgreSQL")

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(
        _DatabaseMustNotBeAccessed()  # type: ignore[arg-type]
    )
    intent = OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=UUID("11111111-1111-1111-1111-111111111111"),
            approved_revision=1,
            specification_checksum="a" * 64,
        ),
        simulation_id=UUID("22222222-2222-2222-2222-222222222222"),
        quote_asset="USDT",
        authorized_capital=Decimal("10"),
    )

    with pytest.raises(InvalidOperationalPaperCapitalAuthorizationSpecificationError):
        await repository.create(
            intent,
            actor_id=UUID(int=0),
            idempotency_key="capital-create:invalid",
            now=datetime(2026, 8, 27, 14, tzinfo=UTC),
        )


class _C2B2RaceCursor:
    def __init__(
        self,
        cursor: object,
        observation: "_C2B2RaceDatabase",
        generation: int,
        *,
        replay_select: bool,
    ) -> None:
        self._cursor = cursor
        self._observation = observation
        self._generation = generation
        self._replay_select = replay_select

    async def fetchone(self) -> object:
        row = await self._cursor.fetchone()
        if self._replay_select:
            if self._generation in self._observation.initial_generations and row is None:
                self._observation.initial_misses += 1
                self._observation.replay_results.append((self._generation, True, True))
                if self._observation.initial_misses == 2:
                    self._observation.initial_reads_complete.set()
                await self._observation.initial_reads_complete.wait()
            else:
                self._observation.replay_results.append(
                    (
                        self._generation,
                        self._generation in self._observation.initial_generations,
                        row is None,
                    )
                )
                if row is not None:
                    self._observation.events.append(("replay-select", self._generation))
        return row

    def __getattr__(self, name: str) -> object:
        return getattr(self._cursor, name)


class _C2B2RaceConnection:
    def __init__(
        self,
        connection: object,
        observation: "_C2B2RaceDatabase",
        generation: int,
    ) -> None:
        self._connection = connection
        self._observation = observation
        self._generation = generation

    async def execute(
        self,
        query: object,
        params: object = None,
        **kwargs: object,
    ) -> object:
        cursor = await self._connection.execute(query, params, **kwargs)
        normalized = " ".join(str(query).lower().split())
        replay_select = (
            "from public.operational_paper_capital_authorizations" in normalized
            and "created_by = %s" in normalized
            and "create_idempotency_key = %s" in normalized
            and normalized.startswith("select")
        )
        if not replay_select:
            return cursor
        return _C2B2RaceCursor(
            cursor,
            self._observation,
            self._generation,
            replay_select=True,
        )


class _C2B2RaceDatabase:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._generation = 0
        self.initial_generations: list[int] = []
        self.initial_misses = 0
        import asyncio

        self.initial_reads_complete = asyncio.Event()
        self.unique_violations: list[tuple[int, str]] = []
        self.replay_results: list[tuple[int, bool, bool]] = []
        self.events: list[tuple[str, int]] = []

    def transaction(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def scope():
            self._generation += 1
            generation = self._generation
            if len(self.initial_generations) < 2:
                self.initial_generations.append(generation)
            self.events.append(("enter", generation))
            try:
                async with self._database.transaction() as connection:
                    yield _C2B2RaceConnection(connection, self, generation)
            except psycopg.errors.UniqueViolation as error:
                self.unique_violations.append((generation, error.diag.constraint_name or ""))
                self.events.append(("rollback", generation))
                raise
            finally:
                self.events.append(("exit", generation))

        return scope()


def _c2b2_seed_create_context(
    database_url: str,
    actor_id: UUID,
) -> tuple[UUID, int, str, UUID]:
    from tests.test_operational_paper_capital_authorizations_migration import (
        _seed_approved_profile,
        _seed_simulation,
    )

    profile_id = _seed_approved_profile(database_url, actor_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(
            connection,
            actor_id,
            initial_capital=Decimal("100"),
        )
        row = connection.execute(
            """
            select approved_revision, approved_checksum
            from public.operational_paper_session_profiles
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone()

    assert row is not None
    approved_revision, approved_checksum = row
    assert isinstance(approved_revision, int)
    assert isinstance(approved_checksum, str)
    return profile_id, approved_revision, approved_checksum, simulation_id


def _c2b2_create_intent(
    *,
    profile_id: UUID,
    approved_revision: int,
    approved_checksum: str,
    simulation_id: UUID,
    amount: Decimal,
):
    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationCreateIntent,
        OperationalPaperCapitalAuthorizationProfileBinding,
    )

    return OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=profile_id,
            approved_revision=approved_revision,
            specification_checksum=approved_checksum,
        ),
        simulation_id=simulation_id,
        quote_asset="USDT",
        authorized_capital=amount,
    )


@pytest.mark.asyncio
async def test_authorization_same_intent_create_race_uses_new_replay_transaction(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    import asyncio
    from datetime import UTC, datetime

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url, auth_user_id
    )
    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )
    observation = _C2B2RaceDatabase(database)
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(observation)
    now = datetime(2026, 8, 27, 14, tzinfo=UTC)

    results = await asyncio.gather(
        repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-equal-race",
            now=now,
        ),
        repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-equal-race",
            now=now,
        ),
    )

    assert results[0] == results[1]
    assert len(observation.initial_generations) == 2
    assert len(set(observation.initial_generations)) == 2
    assert observation.initial_misses == 2
    assert len(observation.unique_violations) == 1

    losing_generation, constraint = observation.unique_violations[0]
    assert constraint == "op_pc_auth_actor_idempotency_key"
    assert ("rollback", losing_generation) in observation.events

    replay_generations = [
        generation
        for generation, initial, missed in observation.replay_results
        if not initial and not missed
    ]
    assert len(replay_generations) == 1
    replay_generation = replay_generations[0]
    assert replay_generation != losing_generation
    assert observation.events.index(("exit", losing_generation)) < observation.events.index(
        ("replay-select", replay_generation)
    )

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where created_by = %s and create_idempotency_key = %s
            """,
            (auth_user_id, "capital-equal-race"),
        ).fetchone()
    assert row == (1,)


@pytest.mark.asyncio
async def test_authorization_divergent_create_race_replays_then_conflicts(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    import asyncio
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorization,
        OperationalPaperCapitalAuthorizationIdempotencyConflictError,
    )

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url, auth_user_id
    )
    first_intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )
    second_intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("11"),
    )
    observation = _C2B2RaceDatabase(database)
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(observation)
    now = datetime(2026, 8, 27, 14, tzinfo=UTC)

    results = await asyncio.gather(
        repository.create(
            first_intent,
            actor_id=auth_user_id,
            idempotency_key="capital-different-race",
            now=now,
        ),
        repository.create(
            second_intent,
            actor_id=auth_user_id,
            idempotency_key="capital-different-race",
            now=now,
        ),
        return_exceptions=True,
    )

    assert len(observation.initial_generations) == 2
    assert len(set(observation.initial_generations)) == 2
    assert observation.initial_misses == 2
    assert len(observation.unique_violations) == 1

    losing_generation, constraint = observation.unique_violations[0]
    assert constraint == "op_pc_auth_actor_idempotency_key"
    assert ("rollback", losing_generation) in observation.events

    replay_generations = [
        generation
        for generation, initial, missed in observation.replay_results
        if not initial and not missed
    ]
    assert len(replay_generations) == 1
    assert observation.events.index(("exit", losing_generation)) < observation.events.index(
        ("replay-select", replay_generations[0])
    )

    assert sum(isinstance(item, OperationalPaperCapitalAuthorization) for item in results) == 1
    assert (
        sum(
            isinstance(
                item,
                OperationalPaperCapitalAuthorizationIdempotencyConflictError,
            )
            for item in results
        )
        == 1
    )

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where created_by = %s and create_idempotency_key = %s
            """,
            (auth_user_id, "capital-different-race"),
        ).fetchone()
    assert row == (1,)


def _c2b2b1_authorization_count(
    database_url: str,
    simulation_id: UUID,
) -> int:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            (
                "select count(*) "
                "from public.operational_paper_capital_authorizations "
                "where simulation_id = %s"
            ),
            (simulation_id,),
        ).fetchone()

    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_authorization_create_maps_missing_simulation(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.domain.errors import SimulationNotFoundError
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    profile_id, approved_revision, approved_checksum, _ = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )
    missing_simulation_id = uuid4()
    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=missing_simulation_id,
        amount=Decimal("10"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(SimulationNotFoundError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-missing-simulation",
            now=datetime(2026, 8, 27, 15, tzinfo=UTC),
        )

    assert _c2b2b1_authorization_count(database_url, missing_simulation_id) == 0


@pytest.mark.asyncio
async def test_authorization_create_maps_missing_profile(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationProfileStateConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    _, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )
    intent = _c2b2_create_intent(
        profile_id=uuid4(),
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(OperationalPaperCapitalAuthorizationProfileStateConflictError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-missing-profile",
            now=datetime(2026, 8, 27, 15, tzinfo=UTC),
        )

    assert _c2b2b1_authorization_count(database_url, simulation_id) == 0


@pytest.mark.asyncio
async def test_authorization_create_maps_profile_binding_mismatch(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationProfileStateConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )
    wrong_checksum = "0" * 64
    if wrong_checksum == approved_checksum:
        wrong_checksum = "1" * 64

    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=wrong_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(OperationalPaperCapitalAuthorizationProfileStateConflictError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-binding-mismatch",
            now=datetime(2026, 8, 27, 15, tzinfo=UTC),
        )

    assert _c2b2b1_authorization_count(database_url, simulation_id) == 0


@pytest.mark.asyncio
async def test_authorization_create_maps_quote_asset_mismatch(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationCreateIntent,
        OperationalPaperCapitalAuthorizationCurrencyMismatchError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )
    base_intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )
    intent = OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=base_intent.profile_binding,
        simulation_id=simulation_id,
        quote_asset="BTC",
        authorized_capital=Decimal("10"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(OperationalPaperCapitalAuthorizationCurrencyMismatchError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-quote-mismatch",
            now=datetime(2026, 8, 27, 15, tzinfo=UTC),
        )

    assert _c2b2b1_authorization_count(database_url, simulation_id) == 0


@pytest.mark.asyncio
async def test_authorization_create_maps_insufficient_available_capital(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )
    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("101"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-insufficient",
            now=datetime(2026, 8, 27, 15, tzinfo=UTC),
        )

    assert _c2b2b1_authorization_count(database_url, simulation_id) == 0


@pytest.mark.asyncio
async def test_authorization_create_maps_active_profile_conflict(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationActiveProfileConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )
    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    now = datetime(2026, 8, 27, 15, tzinfo=UTC)

    await repository.create(
        intent,
        actor_id=auth_user_id,
        idempotency_key="capital-map-active-first",
        now=now,
    )

    with pytest.raises(OperationalPaperCapitalAuthorizationActiveProfileConflictError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-active-second",
            now=now,
        )

    assert _c2b2b1_authorization_count(database_url, simulation_id) == 1


@pytest.mark.asyncio
async def test_authorization_create_maps_simulation_not_active(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.domain.errors import SimulationTerminalError
    from app.domain.models import SimulationStatus
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )
    from app.repositories.simulations import SimulationRepository

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )

    await SimulationRepository(database).transition(
        simulation_id,
        target_status=SimulationStatus.COMPLETED,
    )

    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(SimulationTerminalError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-simulation-not-active",
            now=datetime(2026, 8, 27, 16, tzinfo=UTC),
        )

    assert _c2b2b1_authorization_count(database_url, simulation_id) == 0


@pytest.mark.asyncio
async def test_authorization_create_maps_profile_not_approved(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationProfileStateConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )
    from tests.test_operational_paper_capital_authorizations_migration import (
        _seed_simulation,
    )
    from tests.test_operational_paper_session_profiles_migration import (
        _create_profile,
    )

    profile_id, _, _ = _create_profile(
        database_url,
        auth_user_id,
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        row = connection.execute(
            (
                "select profile.current_revision, revision.specification_checksum "
                "from public.operational_paper_session_profiles as profile "
                "join public.operational_paper_session_profile_revisions as revision "
                "on revision.profile_id = profile.profile_id "
                "and revision.revision = profile.current_revision "
                "where profile.profile_id = %s"
            ),
            (profile_id,),
        ).fetchone()
        simulation_id = _seed_simulation(
            connection,
            auth_user_id,
            initial_capital=Decimal("100"),
        )

    assert row is not None
    current_revision, specification_checksum = row
    assert isinstance(current_revision, int)
    assert isinstance(specification_checksum, str)

    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=current_revision,
        approved_checksum=specification_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(OperationalPaperCapitalAuthorizationProfileStateConflictError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-profile-not-approved",
            now=datetime(2026, 8, 27, 16, tzinfo=UTC),
        )

    assert _c2b2b1_authorization_count(database_url, simulation_id) == 0


def test_authorization_database_error_translator_maps_profile_revision_missing(
    database_url: str,
) -> None:
    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationProfileStateConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        _raise_authorization_database_error,
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error) as captured:
            connection.execute(
                "do $$ begin "
                "raise exception using errcode = '23503', "
                "message = "
                "'operational_paper_capital_authorization_profile_revision_missing'; "
                "end $$"
            )

    assert captured.value.diag.message_primary == (
        "operational_paper_capital_authorization_profile_revision_missing"
    )

    with pytest.raises(OperationalPaperCapitalAuthorizationProfileStateConflictError):
        _raise_authorization_database_error(captured.value)


@pytest.mark.asyncio
async def test_authorization_create_maps_currency_mismatch(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationCurrencyMismatchError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )
    from tests.test_operational_paper_capital_authorizations_migration import (
        _seed_approved_profile,
        _seed_simulation,
    )

    profile_id = _seed_approved_profile(
        database_url,
        auth_user_id,
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        row = connection.execute(
            (
                "select approved_revision, approved_checksum "
                "from public.operational_paper_session_profiles "
                "where profile_id = %s"
            ),
            (profile_id,),
        ).fetchone()

        simulation_id = _seed_simulation(
            connection,
            auth_user_id,
            initial_capital=Decimal("100"),
            currency="BTC",
        )

    assert row is not None
    approved_revision, approved_checksum = row
    assert isinstance(approved_revision, int)
    assert isinstance(approved_checksum, str)

    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("10"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(OperationalPaperCapitalAuthorizationCurrencyMismatchError):
        await repository.create(
            intent,
            actor_id=auth_user_id,
            idempotency_key="capital-map-currency-mismatch",
            now=datetime(2026, 8, 27, 16, tzinfo=UTC),
        )

    assert _c2b2b1_authorization_count(database_url, simulation_id) == 0


class _C2CObservedConnection:
    def __init__(self, connection, events: list[str]) -> None:
        self._connection = connection
        self._events = events

    async def execute(self, query, params=None):
        normalized = " ".join(str(query).lower().split())
        if "from public.simulation_runs" in normalized and "for update" in normalized:
            self._events.append("simulation-lock")
        if (
            "from public.operational_paper_capital_authorizations" in normalized
            and "for update" in normalized
        ):
            self._events.append("authorization-lock")
        return await self._connection.execute(query, params)


class _C2CObservedTransaction:
    def __init__(self, transaction, events: list[str]) -> None:
        self._transaction = transaction
        self._events = events

    async def __aenter__(self):
        connection = await self._transaction.__aenter__()
        return _C2CObservedConnection(connection, self._events)

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._transaction.__aexit__(exc_type, exc, traceback)


class _C2CObservedDatabase:
    def __init__(self, database: Database) -> None:
        self._database = database
        self.events: list[str] = []

    def transaction(self):
        return _C2CObservedTransaction(
            self._database.transaction(),
            self.events,
        )


async def _c2c_create_authorization(
    database_url: str,
    database: Database,
    actor_id: UUID,
    *,
    idempotency_key: str,
):
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationCreateIntent,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    raw_intent, _, _ = _create_intent_sources(
        database_url,
        actor_id,
        amount=Decimal("20"),
    )
    assert isinstance(raw_intent, OperationalPaperCapitalAuthorizationCreateIntent)

    return await PostgresOperationalPaperCapitalAuthorizationRepository(database).create(
        raw_intent,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        now=datetime(2026, 8, 27, 14, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_authorization_revoke_locks_simulation_before_authorization_and_persists(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationState,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    authorization = await _c2c_create_authorization(
        database_url,
        database,
        auth_user_id,
        idempotency_key="capital-revoke-success",
    )
    observation = _C2CObservedDatabase(database)
    repository = PostgresOperationalPaperCapitalAuthorizationRepository(observation)
    revoked_at = datetime(2026, 8, 27, 17, tzinfo=UTC)

    revoked = await repository.revoke(
        authorization.authorization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=revoked_at,
    )

    assert observation.events == ["simulation-lock", "authorization-lock"]
    assert revoked.state is OperationalPaperCapitalAuthorizationState.REVOKED
    assert revoked.record_version == 2
    assert revoked.revoked_by == auth_user_id
    assert revoked.revoked_at == revoked_at
    assert revoked.authorized_capital == authorization.authorized_capital
    assert revoked.profile_binding == authorization.profile_binding
    assert revoked.simulation_id == authorization.simulation_id

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select state, record_version, revoked_by, revoked_at
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (authorization.authorization_id,),
        ).fetchone()

    assert row == ("REVOKED", 2, auth_user_id, revoked_at)


@pytest.mark.asyncio
async def test_authorization_revoke_missing_is_not_found(
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationNotFoundError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)

    with pytest.raises(OperationalPaperCapitalAuthorizationNotFoundError):
        await repository.revoke(
            uuid4(),
            expected_record_version=1,
            actor_id=auth_user_id,
            now=datetime(2026, 8, 27, 17, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_authorization_revoke_rejects_record_version_conflict_after_lock_order(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationRecordVersionConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    authorization = await _c2c_create_authorization(
        database_url,
        database,
        auth_user_id,
        idempotency_key="capital-revoke-version-conflict",
    )
    observation = _C2CObservedDatabase(database)
    repository = PostgresOperationalPaperCapitalAuthorizationRepository(observation)

    with pytest.raises(OperationalPaperCapitalAuthorizationRecordVersionConflictError):
        await repository.revoke(
            authorization.authorization_id,
            expected_record_version=2,
            actor_id=auth_user_id,
            now=datetime(2026, 8, 27, 17, tzinfo=UTC),
        )

    assert observation.events == ["simulation-lock", "authorization-lock"]


@pytest.mark.asyncio
async def test_authorization_revoke_exact_terminal_replay_returns_committed_row(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationState,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    authorization = await _c2c_create_authorization(
        database_url,
        database,
        auth_user_id,
        idempotency_key="capital-revoke-replay",
    )
    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    first_now = datetime(2026, 8, 27, 17, tzinfo=UTC)

    first = await repository.revoke(
        authorization.authorization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=first_now,
    )
    replay = await repository.revoke(
        authorization.authorization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=first_now + timedelta(minutes=5),
    )

    assert first.state is OperationalPaperCapitalAuthorizationState.REVOKED
    assert replay == first
    assert replay.record_version == 2
    assert replay.revoked_at == first_now


@pytest.mark.asyncio
async def test_authorization_revoke_terminal_replay_rejects_different_actor(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorizationStateTransitionConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )
    from tests.postgres_support import add_auth_user

    authorization = await _c2c_create_authorization(
        database_url,
        database,
        auth_user_id,
        idempotency_key="capital-revoke-terminal-conflict",
    )
    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    now = datetime(2026, 8, 27, 17, tzinfo=UTC)

    await repository.revoke(
        authorization.authorization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=now,
    )

    other_actor = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, other_actor)

    with pytest.raises(OperationalPaperCapitalAuthorizationStateTransitionConflictError):
        await repository.revoke(
            authorization.authorization_id,
            expected_record_version=1,
            actor_id=other_actor,
            now=now,
        )


class _C2C2RaceConnection:
    def __init__(self, connection, observation):
        self._connection = connection
        self._observation = observation

    async def execute(self, query, params=None):
        normalized = " ".join(str(query).lower().split())
        if "from public.simulation_runs" in normalized and "for update" in normalized:
            await self._observation.arrive_at_simulation_lock()
        return await self._connection.execute(query, params)


class _C2C2RaceTransaction:
    def __init__(self, transaction, observation):
        self._transaction = transaction
        self._observation = observation

    async def __aenter__(self):
        connection = await self._transaction.__aenter__()
        return _C2C2RaceConnection(connection, self._observation)

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._transaction.__aexit__(exc_type, exc, traceback)


class _C2C2RaceDatabase:
    def __init__(self, database):
        import asyncio

        self._database = database
        self._arrival_lock = asyncio.Lock()
        self._release = asyncio.Event()
        self.simulation_lock_arrivals = 0

    async def arrive_at_simulation_lock(self):
        async with self._arrival_lock:
            self.simulation_lock_arrivals += 1
            if self.simulation_lock_arrivals == 2:
                self._release.set()

        await self._release.wait()

    def transaction(self):
        return _C2C2RaceTransaction(
            self._database.transaction(),
            self,
        )


@pytest.mark.asyncio
async def test_authorization_revoke_concurrent_same_actor_replays_committed_terminal(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    import asyncio
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorization,
        OperationalPaperCapitalAuthorizationState,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )
    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("25"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    authorization = await repository.create(
        intent,
        actor_id=auth_user_id,
        idempotency_key="capital-revoke-concurrent-same",
        now=datetime(2026, 8, 27, 16, tzinfo=UTC),
    )

    observation = _C2C2RaceDatabase(database)
    race_repository = PostgresOperationalPaperCapitalAuthorizationRepository(observation)
    revoked_at = datetime(2026, 8, 27, 16, 1, tzinfo=UTC)

    results = await asyncio.gather(
        race_repository.revoke(
            authorization.authorization_id,
            expected_record_version=1,
            actor_id=auth_user_id,
            now=revoked_at,
        ),
        race_repository.revoke(
            authorization.authorization_id,
            expected_record_version=1,
            actor_id=auth_user_id,
            now=revoked_at,
        ),
    )

    assert observation.simulation_lock_arrivals == 2
    assert all(isinstance(result, OperationalPaperCapitalAuthorization) for result in results)
    assert all(
        result.state is OperationalPaperCapitalAuthorizationState.REVOKED for result in results
    )
    assert all(result.record_version == 2 for result in results)
    assert all(result.revoked_by == auth_user_id for result in results)
    assert all(result.revoked_at == revoked_at for result in results)

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select state, record_version, revoked_by, revoked_at
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (authorization.authorization_id,),
        ).fetchone()

    assert row == ("REVOKED", 2, auth_user_id, revoked_at)


@pytest.mark.asyncio
async def test_authorization_revoke_concurrent_different_actor_has_one_winner(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    import asyncio
    from datetime import UTC, datetime

    from app.operational_paper_capital_authorizations import (
        OperationalPaperCapitalAuthorization,
        OperationalPaperCapitalAuthorizationStateTransitionConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )
    from tests.postgres_support import add_auth_user

    second_actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, second_actor_id)

    profile_id, approved_revision, approved_checksum, simulation_id = _c2b2_seed_create_context(
        database_url,
        auth_user_id,
    )
    intent = _c2b2_create_intent(
        profile_id=profile_id,
        approved_revision=approved_revision,
        approved_checksum=approved_checksum,
        simulation_id=simulation_id,
        amount=Decimal("25"),
    )

    repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    authorization = await repository.create(
        intent,
        actor_id=auth_user_id,
        idempotency_key="capital-revoke-concurrent-different",
        now=datetime(2026, 8, 27, 16, 10, tzinfo=UTC),
    )

    observation = _C2C2RaceDatabase(database)
    race_repository = PostgresOperationalPaperCapitalAuthorizationRepository(observation)
    revoked_at = datetime(2026, 8, 27, 16, 11, tzinfo=UTC)

    results = await asyncio.gather(
        race_repository.revoke(
            authorization.authorization_id,
            expected_record_version=1,
            actor_id=auth_user_id,
            now=revoked_at,
        ),
        race_repository.revoke(
            authorization.authorization_id,
            expected_record_version=1,
            actor_id=second_actor_id,
            now=revoked_at,
        ),
        return_exceptions=True,
    )

    assert observation.simulation_lock_arrivals == 2

    successes = [
        result for result in results if isinstance(result, OperationalPaperCapitalAuthorization)
    ]
    conflicts = [
        result
        for result in results
        if isinstance(
            result,
            OperationalPaperCapitalAuthorizationStateTransitionConflictError,
        )
    ]

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert successes[0].record_version == 2
    assert successes[0].revoked_by in {auth_user_id, second_actor_id}

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select state, record_version, revoked_by, revoked_at
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (authorization.authorization_id,),
        ).fetchone()

    assert row is not None
    state, record_version, revoked_by, persisted_revoked_at = row
    assert state == "REVOKED"
    assert record_version == 2
    assert revoked_by in {auth_user_id, second_actor_id}
    assert persisted_revoked_at == revoked_at
    assert successes[0].revoked_by == revoked_by


def _c2c3_database_error(
    database_url: str,
    *,
    sqlstate: str,
    message: str,
) -> psycopg.Error:
    from psycopg import sql

    statement = sql.SQL(
        "do $$ begin raise exception using errcode = {}, message = {}; end $$"
    ).format(
        sql.Literal(sqlstate),
        sql.Literal(message),
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error) as captured:
            connection.execute(statement)
    return captured.value


def test_authorization_database_error_translator_maps_revoke_record_version_conflict(
    database_url: str,
) -> None:
    from app.operational_paper_capital_authorizations.errors import (
        OperationalPaperCapitalAuthorizationRecordVersionConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        _raise_authorization_database_error,
    )

    error = _c2c3_database_error(
        database_url,
        sqlstate="40001",
        message="operational_paper_capital_authorization_record_version_conflict",
    )

    with pytest.raises(OperationalPaperCapitalAuthorizationRecordVersionConflictError):
        _raise_authorization_database_error(error)


@pytest.mark.parametrize(
    ("sqlstate", "message"),
    (
        ("55000", "operational_paper_capital_authorization_terminal"),
        ("55000", "operational_paper_capital_authorization_transition_forbidden"),
        (
            "23514",
            "operational_paper_capital_authorization_revocation_metadata_required",
        ),
    ),
)
def test_authorization_database_error_translator_maps_revoke_state_conflicts(
    database_url: str,
    sqlstate: str,
    message: str,
) -> None:
    from app.operational_paper_capital_authorizations.errors import (
        OperationalPaperCapitalAuthorizationStateTransitionConflictError,
    )
    from app.repositories.operational_paper_capital_authorizations import (
        _raise_authorization_database_error,
    )

    error = _c2c3_database_error(
        database_url,
        sqlstate=sqlstate,
        message=message,
    )

    with pytest.raises(OperationalPaperCapitalAuthorizationStateTransitionConflictError):
        _raise_authorization_database_error(error)
