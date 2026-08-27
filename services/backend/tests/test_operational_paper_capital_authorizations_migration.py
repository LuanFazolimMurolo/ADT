"""Phase 7-08 operational paper-capital authorization persistence invariants."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from queue import Queue
from time import monotonic, sleep
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection

from tests.postgres_support import add_auth_user
from tests.test_operational_paper_session_profiles_migration import (
    BASE_TIME,
    PROFILE_CHECKSUM_A,
    _approve_profile,
    _create_profile,
)

AUTH_TABLE = "public.operational_paper_capital_authorizations"
AUTHORIZATION_CHECKSUM = "1" * 64
INTENT_FINGERPRINT = "2" * 64
DATA_API_ROLES = ("anon", "authenticated", "service_role")


def _seed_approved_profile(
    database_url: str,
    actor_id: UUID,
    *,
    profile_id: UUID | None = None,
) -> UUID:
    profile, _, _ = _create_profile(
        database_url,
        actor_id,
        profile_id=profile_id,
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        _approve_profile(
            connection,
            profile_id=profile,
            actor_id=actor_id,
        )
    return profile


def _seed_simulation(
    connection: Connection[Any],
    actor_id: UUID,
    *,
    initial_capital: Decimal = Decimal("100"),
    currency: str = "USDT",
) -> UUID:
    simulation_id = uuid4()
    connection.execute(
        """
        insert into public.simulation_runs (
            id, name, status, currency, initial_capital, started_at, created_by
        )
        values (%s, '7-08 capital authorization test', 'ACTIVE', %s, %s, %s, %s)
        """,
        (simulation_id, currency, initial_capital, BASE_TIME, actor_id),
    )
    connection.execute(
        """
        insert into public.capital_movements (
            simulation_id, type, amount, reason, created_by
        )
        values (%s, 'INITIAL_CAPITAL', %s, '7-08 opening capital', %s)
        """,
        (simulation_id, initial_capital, actor_id),
    )
    return simulation_id


def _insert_authorization(
    connection: Connection[Any],
    *,
    actor_id: UUID,
    profile_id: UUID,
    simulation_id: UUID,
    amount: Decimal,
    authorization_id: UUID | None = None,
    idempotency_key: str | None = None,
    profile_revision: int = 1,
    profile_checksum: str = PROFILE_CHECKSUM_A,
    quote_asset: str = "USDT",
) -> UUID:
    value = authorization_id or uuid4()
    connection.execute(
        """
        insert into public.operational_paper_capital_authorizations (
            authorization_id, schema_version, state, record_version,
            profile_id, profile_approved_revision,
            profile_specification_checksum, simulation_id, quote_asset,
            authorized_capital, authorization_checksum,
            created_by, created_at, create_idempotency_key,
            create_intent_fingerprint
        )
        values (
            %s, 1, 'AUTHORIZED', 1,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        """,
        (
            value,
            profile_id,
            profile_revision,
            profile_checksum,
            simulation_id,
            quote_asset,
            amount,
            AUTHORIZATION_CHECKSUM,
            actor_id,
            BASE_TIME + timedelta(seconds=4),
            idempotency_key or f"capital-auth:{value}",
            INTENT_FINGERPRINT,
        ),
    )
    return value


def test_schema_is_backend_only_and_has_expected_core_objects(
    database_url: str,
) -> None:
    expected_columns = {
        "authorization_id",
        "schema_version",
        "state",
        "record_version",
        "profile_id",
        "profile_approved_revision",
        "profile_specification_checksum",
        "simulation_id",
        "quote_asset",
        "authorized_capital",
        "authorization_checksum",
        "created_by",
        "created_at",
        "revoked_by",
        "revoked_at",
        "create_idempotency_key",
        "create_intent_fingerprint",
    }
    with psycopg.connect(database_url, autocommit=True) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'operational_paper_capital_authorizations'
                """
            ).fetchall()
        }
        assert columns == expected_columns

        rls = connection.execute(
            """
            select relrowsecurity
            from pg_catalog.pg_class
            where oid = 'public.operational_paper_capital_authorizations'::regclass
            """
        ).fetchone()
        assert rls == (True,)

        policy_count = connection.execute(
            """
            select count(*)
            from pg_catalog.pg_policies
            where schemaname = 'public'
              and tablename = 'operational_paper_capital_authorizations'
            """
        ).fetchone()
        assert policy_count == (0,)

        for role in DATA_API_ROLES:
            privileges = connection.execute(
                """
                select
                    has_table_privilege(%s, %s, 'SELECT'),
                    has_table_privilege(%s, %s, 'INSERT'),
                    has_table_privilege(%s, %s, 'UPDATE'),
                    has_table_privilege(%s, %s, 'DELETE')
                """,
                (
                    role,
                    AUTH_TABLE,
                    role,
                    AUTH_TABLE,
                    role,
                    AUTH_TABLE,
                    role,
                    AUTH_TABLE,
                ),
            ).fetchone()
            assert privileges == (False, False, False, False)


def test_valid_authorization_reserves_without_writing_the_ledger(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)
    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)
        authorization_id = _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=profile_id,
            simulation_id=simulation_id,
            amount=Decimal("40"),
        )

        row = connection.execute(
            """
            select state, record_version, authorized_capital
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (authorization_id,),
        ).fetchone()
        assert row == ("AUTHORIZED", 1, Decimal("40.00000000"))

        ledger = connection.execute(
            """
            select count(*), sum(amount)
            from public.capital_movements
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()
        assert ledger == (1, Decimal("100.00000000"))


def test_authorization_cannot_exceed_available_capital(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)
    first_profile = _seed_approved_profile(database_url, actor_id)
    second_profile = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)
        _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=first_profile,
            simulation_id=simulation_id,
            amount=Decimal("70"),
        )

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=second_profile,
                simulation_id=simulation_id,
                amount=Decimal("30.00000001"),
            )

        reserved = connection.execute(
            """
            select count(*), sum(authorized_capital)
            from public.operational_paper_capital_authorizations
            where simulation_id = %s and state = 'AUTHORIZED'
            """,
            (simulation_id,),
        ).fetchone()
        assert reserved == (1, Decimal("70.00000000"))


def test_reserved_capital_protects_ledger_and_simulation_terminalization(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)
    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)
        authorization_id = _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=profile_id,
            simulation_id=simulation_id,
            amount=Decimal("80"),
        )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                insert into public.capital_movements (
                    simulation_id, type, amount, reason, created_by
                )
                values (%s, 'ADMIN_WITHDRAWAL', %s, 'reserved floor', %s)
                """,
                (simulation_id, Decimal("-20.00000001"), actor_id),
            )

        connection.execute(
            """
            insert into public.capital_movements (
                simulation_id, type, amount, reason, created_by
            )
            values (%s, 'ADMIN_WITHDRAWAL', %s, 'exact reserved floor', %s)
            """,
            (simulation_id, Decimal("-20"), actor_id),
        )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.simulation_runs
                set status = 'COMPLETED', ended_at = %s
                where id = %s
                """,
                (BASE_TIME + timedelta(seconds=10), simulation_id),
            )

        connection.execute(
            """
            update public.operational_paper_capital_authorizations
            set state = 'REVOKED',
                record_version = record_version + 1,
                revoked_by = %s,
                revoked_at = %s
            where authorization_id = %s
            """,
            (
                actor_id,
                BASE_TIME + timedelta(seconds=6),
                authorization_id,
            ),
        )

        connection.execute(
            """
            update public.simulation_runs
            set status = 'COMPLETED', ended_at = %s
            where id = %s
            """,
            (BASE_TIME + timedelta(seconds=10), simulation_id),
        )

        simulation = connection.execute(
            "select status from public.simulation_runs where id = %s",
            (simulation_id,),
        ).fetchone()
        assert simulation == ("COMPLETED",)


def test_authorization_lifecycle_is_one_way_and_binding_is_immutable(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)
    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)
        authorization_id = _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=profile_id,
            simulation_id=simulation_id,
            amount=Decimal("25"),
        )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_capital_authorizations
                set state = 'REVOKED',
                    record_version = record_version + 1,
                    authorized_capital = %s,
                    revoked_by = %s,
                    revoked_at = %s
                where authorization_id = %s
                """,
                (
                    Decimal("26"),
                    actor_id,
                    BASE_TIME + timedelta(seconds=6),
                    authorization_id,
                ),
            )

        connection.execute(
            """
            update public.operational_paper_capital_authorizations
            set state = 'REVOKED',
                record_version = record_version + 1,
                revoked_by = %s,
                revoked_at = %s
            where authorization_id = %s
            """,
            (
                actor_id,
                BASE_TIME + timedelta(seconds=6),
                authorization_id,
            ),
        )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_capital_authorizations
                set record_version = record_version + 1
                where authorization_id = %s
                """,
                (authorization_id,),
            )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                delete from public.operational_paper_capital_authorizations
                where authorization_id = %s
                """,
                (authorization_id,),
            )

        row = connection.execute(
            """
            select state, record_version, authorized_capital
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (authorization_id,),
        ).fetchone()
        assert row == ("REVOKED", 2, Decimal("25.00000000"))


def _wait_until_backend_is_locked(
    database_url: str,
    backend_pid: int,
) -> bool:
    deadline = monotonic() + 10
    with psycopg.connect(database_url, autocommit=True) as connection:
        while monotonic() < deadline:
            wait_state = connection.execute(
                """
                select wait_event_type
                from pg_catalog.pg_stat_activity
                where pid = %s
                """,
                (backend_pid,),
            ).fetchone()
            if wait_state == ("Lock",):
                return True
            sleep(0.01)
    return False


def test_concurrent_authorizations_cannot_overreserve(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    first_profile = _seed_approved_profile(database_url, actor_id)
    second_profile = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)

    second_backend_pid: Queue[int] = Queue(maxsize=1)

    def reserve_second() -> None:
        with psycopg.connect(database_url, autocommit=True) as connection:
            pid_row = connection.execute("select pg_backend_pid()").fetchone()
            assert pid_row is not None
            second_backend_pid.put(pid_row[0])

            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=second_profile,
                simulation_id=simulation_id,
                amount=Decimal("70"),
            )

    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        psycopg.connect(database_url, autocommit=True) as first_connection,
    ):
        with first_connection.transaction():
            first_authorization_id = _insert_authorization(
                first_connection,
                actor_id=actor_id,
                profile_id=first_profile,
                simulation_id=simulation_id,
                amount=Decimal("70"),
            )

            future = executor.submit(reserve_second)
            waiting_pid = second_backend_pid.get(timeout=10)
            assert _wait_until_backend_is_locked(database_url, waiting_pid)

        with pytest.raises(psycopg.Error):
            future.result()

    with psycopg.connect(database_url, autocommit=True) as connection:
        rows = connection.execute(
            """
            select
                authorization_id,
                profile_id,
                authorized_capital,
                state
            from public.operational_paper_capital_authorizations
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchall()

    assert rows == [
        (
            first_authorization_id,
            first_profile,
            Decimal("70.00000000"),
            "AUTHORIZED",
        )
    ]


# B2B-2A-END


def test_authorization_commit_makes_concurrent_withdrawal_fail(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)

    second_backend_pid: Queue[int] = Queue(maxsize=1)

    def withdraw_second() -> None:
        with psycopg.connect(database_url, autocommit=True) as connection:
            pid_row = connection.execute("select pg_backend_pid()").fetchone()
            assert pid_row is not None
            second_backend_pid.put(pid_row[0])
            connection.execute(
                """
                insert into public.capital_movements (
                    simulation_id, type, amount, reason, created_by
                )
                values (%s, 'ADMIN_WITHDRAWAL', %s, 'concurrent reservation floor', %s)
                """,
                (simulation_id, Decimal("-30"), actor_id),
            )

    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        psycopg.connect(database_url, autocommit=True) as first_connection,
    ):
        with first_connection.transaction():
            authorization_id = _insert_authorization(
                first_connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("80"),
            )

            future = executor.submit(withdraw_second)
            waiting_pid = second_backend_pid.get(timeout=10)
            assert _wait_until_backend_is_locked(database_url, waiting_pid)

        with pytest.raises(psycopg.Error):
            future.result()

    with psycopg.connect(database_url, autocommit=True) as connection:
        balance = connection.execute(
            """
            select sum(amount)
            from public.capital_movements
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()
        authorization = connection.execute(
            """
            select state, authorized_capital
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (authorization_id,),
        ).fetchone()

    assert balance == (Decimal("100.00000000"),)
    assert authorization == ("AUTHORIZED", Decimal("80.00000000"))


def test_withdrawal_commit_makes_concurrent_authorization_fail(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)

    second_backend_pid: Queue[int] = Queue(maxsize=1)

    def reserve_second() -> None:
        with psycopg.connect(database_url, autocommit=True) as connection:
            pid_row = connection.execute("select pg_backend_pid()").fetchone()
            assert pid_row is not None
            second_backend_pid.put(pid_row[0])
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("80"),
            )

    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        psycopg.connect(database_url, autocommit=True) as first_connection,
    ):
        with first_connection.transaction():
            first_connection.execute(
                """
                insert into public.capital_movements (
                    simulation_id, type, amount, reason, created_by
                )
                values (%s, 'ADMIN_WITHDRAWAL', %s, 'wins financial mutex first', %s)
                """,
                (simulation_id, Decimal("-30"), actor_id),
            )

            future = executor.submit(reserve_second)
            waiting_pid = second_backend_pid.get(timeout=10)
            assert _wait_until_backend_is_locked(database_url, waiting_pid)

        with pytest.raises(psycopg.Error):
            future.result()

    with psycopg.connect(database_url, autocommit=True) as connection:
        balance = connection.execute(
            """
            select sum(amount)
            from public.capital_movements
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()
        authorization_count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert balance == (Decimal("70.00000000"),)
    assert authorization_count == (0,)


# B2B-2B-END


def test_authorization_commit_blocks_concurrent_simulation_terminalization(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)

    second_backend_pid: Queue[int] = Queue(maxsize=1)

    def terminalize_second() -> None:
        with psycopg.connect(database_url, autocommit=True) as connection:
            pid_row = connection.execute("select pg_backend_pid()").fetchone()
            assert pid_row is not None
            second_backend_pid.put(pid_row[0])
            connection.execute(
                """
                update public.simulation_runs
                set status = 'COMPLETED', ended_at = %s
                where id = %s
                """,
                (BASE_TIME + timedelta(seconds=20), simulation_id),
            )

    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        psycopg.connect(database_url, autocommit=True) as first_connection,
    ):
        with first_connection.transaction():
            authorization_id = _insert_authorization(
                first_connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("40"),
            )

            future = executor.submit(terminalize_second)
            waiting_pid = second_backend_pid.get(timeout=10)
            assert _wait_until_backend_is_locked(database_url, waiting_pid)

        with pytest.raises(psycopg.Error):
            future.result()

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation = connection.execute(
            """
            select status, ended_at
            from public.simulation_runs
            where id = %s
            """,
            (simulation_id,),
        ).fetchone()
        authorization = connection.execute(
            """
            select state, authorized_capital
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (authorization_id,),
        ).fetchone()

    assert simulation == ("ACTIVE", None)
    assert authorization == ("AUTHORIZED", Decimal("40.00000000"))


def test_simulation_terminalization_commit_blocks_concurrent_authorization(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)

    second_backend_pid: Queue[int] = Queue(maxsize=1)

    def reserve_second() -> None:
        with psycopg.connect(database_url, autocommit=True) as connection:
            pid_row = connection.execute("select pg_backend_pid()").fetchone()
            assert pid_row is not None
            second_backend_pid.put(pid_row[0])
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("40"),
            )

    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        psycopg.connect(database_url, autocommit=True) as first_connection,
    ):
        with first_connection.transaction():
            first_connection.execute(
                """
                update public.simulation_runs
                set status = 'COMPLETED', ended_at = %s
                where id = %s
                """,
                (BASE_TIME + timedelta(seconds=20), simulation_id),
            )

            future = executor.submit(reserve_second)
            waiting_pid = second_backend_pid.get(timeout=10)
            assert _wait_until_backend_is_locked(database_url, waiting_pid)

        with pytest.raises(psycopg.Error):
            future.result()

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation = connection.execute(
            """
            select status, ended_at
            from public.simulation_runs
            where id = %s
            """,
            (simulation_id,),
        ).fetchone()
        authorization_count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert simulation == (
        "COMPLETED",
        BASE_TIME + timedelta(seconds=20),
    )
    assert authorization_count == (0,)


# B2B-2C-END


def test_schema_preserves_exact_profile_fk_and_financial_check(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        rows = connection.execute(
            """
            select contype, pg_catalog.pg_get_constraintdef(oid)
            from pg_catalog.pg_constraint
            where conrelid =
                'public.operational_paper_capital_authorizations'::regclass
            """
        ).fetchall()

    definitions = [definition.lower() for _, definition in rows]

    assert any(
        "foreign key (profile_id, profile_approved_revision, profile_specification_checksum)"
        in definition
        and "operational_paper_session_profile_revisions" in definition
        and "(profile_id, revision, specification_checksum)" in definition
        for definition in definitions
    )

    assert any(
        "authorized_capital" in definition
        and "nan" in definition
        and "infinity" in definition
        and "authorized_capital >" in definition
        for definition in definitions
    )


def test_profile_must_be_approved_and_binding_must_be_exact(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    profile_id, _, _ = _create_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("10"),
            )

        _approve_profile(
            connection,
            profile_id=profile_id,
            actor_id=actor_id,
        )

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("10"),
                profile_revision=2,
            )

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("10"),
                profile_checksum="0" * 64,
            )

        count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone()

    assert count == (0,)


def test_simulation_must_be_active_and_currency_binding_must_match(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(
            connection,
            actor_id,
            currency="BRL",
        )

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("10"),
            )

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("10"),
                quote_asset="BRL",
            )

        connection.execute(
            """
            update public.simulation_runs
            set status = 'COMPLETED', ended_at = %s
            where id = %s
            """,
            (
                BASE_TIME + timedelta(seconds=30),
                simulation_id,
            ),
        )

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("10"),
            )

        count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone()

    assert count == (0,)


def test_authorized_capital_database_bounds_are_enforced(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    invalid_amounts = (
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0"),
        Decimal("-0.00000001"),
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)

        for amount in invalid_amounts:
            profile_id = _seed_approved_profile(database_url, actor_id)
            with pytest.raises(psycopg.Error):
                _insert_authorization(
                    connection,
                    actor_id=actor_id,
                    profile_id=profile_id,
                    simulation_id=simulation_id,
                    amount=amount,
                )

        minimum_profile = _seed_approved_profile(database_url, actor_id)
        minimum_id = _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=minimum_profile,
            simulation_id=simulation_id,
            amount=Decimal("0.00000001"),
        )
        minimum_row = connection.execute(
            """
            select authorized_capital
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (minimum_id,),
        ).fetchone()
        assert minimum_row == (Decimal("0.00000001"),)

        connection.execute(
            """
            update public.operational_paper_capital_authorizations
            set state = 'REVOKED',
                record_version = record_version + 1,
                revoked_by = %s,
                revoked_at = %s
            where authorization_id = %s
            """,
            (
                actor_id,
                BASE_TIME + timedelta(seconds=20),
                minimum_id,
            ),
        )
        connection.execute(
            """
            update public.simulation_runs
            set status = 'COMPLETED', ended_at = %s
            where id = %s
            """,
            (
                BASE_TIME + timedelta(seconds=30),
                simulation_id,
            ),
        )

    maximum = Decimal("999999999999.99999999")
    maximum_profile = _seed_approved_profile(database_url, actor_id)
    overflow_profile = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        maximum_simulation = _seed_simulation(
            connection,
            actor_id,
            initial_capital=maximum,
        )
        maximum_id = _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=maximum_profile,
            simulation_id=maximum_simulation,
            amount=maximum,
        )
        maximum_row = connection.execute(
            """
            select authorized_capital
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (maximum_id,),
        ).fetchone()
        assert maximum_row == (maximum,)

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=overflow_profile,
                simulation_id=maximum_simulation,
                amount=Decimal("1000000000000.00000000"),
            )


# B2B-3B1-END


def test_active_authorization_is_unique_per_profile_and_history_is_retained(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)
        first_id = _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=profile_id,
            simulation_id=simulation_id,
            amount=Decimal("30"),
            idempotency_key="history:first",
        )

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=actor_id,
                profile_id=profile_id,
                simulation_id=simulation_id,
                amount=Decimal("20"),
                idempotency_key="history:blocked",
            )

        connection.execute(
            """
            update public.operational_paper_capital_authorizations
            set state = 'REVOKED',
                record_version = record_version + 1,
                revoked_by = %s,
                revoked_at = %s
            where authorization_id = %s
            """,
            (
                actor_id,
                BASE_TIME + timedelta(seconds=6),
                first_id,
            ),
        )

        second_id = _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=profile_id,
            simulation_id=simulation_id,
            amount=Decimal("20"),
            idempotency_key="history:second",
        )

        first_row = connection.execute(
            """
            select state, record_version, authorized_capital
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (first_id,),
        ).fetchone()
        second_row = connection.execute(
            """
            select state, record_version, authorized_capital
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (second_id,),
        ).fetchone()
        history_count = connection.execute(
            """
            select count(*)
            from public.operational_paper_capital_authorizations
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone()

    assert first_row == ("REVOKED", 2, Decimal("30.00000000"))
    assert second_row == ("AUTHORIZED", 1, Decimal("20.00000000"))
    assert history_count == (2,)


def test_create_idempotency_key_is_actor_scoped(
    database_url: str,
) -> None:
    first_actor = uuid4()
    second_actor = uuid4()

    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, first_actor)
        add_auth_user(connection, second_actor)

    first_profile = _seed_approved_profile(database_url, first_actor)
    second_profile = _seed_approved_profile(database_url, first_actor)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, first_actor)
        shared_key = "capital-auth:shared-key"

        _insert_authorization(
            connection,
            actor_id=first_actor,
            profile_id=first_profile,
            simulation_id=simulation_id,
            amount=Decimal("10"),
            idempotency_key=shared_key,
        )

        with pytest.raises(psycopg.Error):
            _insert_authorization(
                connection,
                actor_id=first_actor,
                profile_id=second_profile,
                simulation_id=simulation_id,
                amount=Decimal("10"),
                idempotency_key=shared_key,
            )

        _insert_authorization(
            connection,
            actor_id=second_actor,
            profile_id=second_profile,
            simulation_id=simulation_id,
            amount=Decimal("10"),
            idempotency_key=shared_key,
        )

        scope = connection.execute(
            """
            select count(*), count(distinct created_by)
            from public.operational_paper_capital_authorizations
            where create_idempotency_key = %s
            """,
            (shared_key,),
        ).fetchone()

    assert scope == (2, 2)


def test_revocation_requires_exact_record_version_and_metadata(
    database_url: str,
) -> None:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)

    profile_id = _seed_approved_profile(database_url, actor_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, actor_id)
        authorization_id = _insert_authorization(
            connection,
            actor_id=actor_id,
            profile_id=profile_id,
            simulation_id=simulation_id,
            amount=Decimal("15"),
        )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_capital_authorizations
                set state = 'REVOKED',
                    record_version = record_version + 1
                where authorization_id = %s
                """,
                (authorization_id,),
            )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_capital_authorizations
                set state = 'REVOKED',
                    record_version = record_version,
                    revoked_by = %s,
                    revoked_at = %s
                where authorization_id = %s
                """,
                (
                    actor_id,
                    BASE_TIME + timedelta(seconds=6),
                    authorization_id,
                ),
            )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_capital_authorizations
                set state = 'REVOKED',
                    record_version = record_version + 2,
                    revoked_by = %s,
                    revoked_at = %s
                where authorization_id = %s
                """,
                (
                    actor_id,
                    BASE_TIME + timedelta(seconds=6),
                    authorization_id,
                ),
            )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_capital_authorizations
                set state = 'REVOKED',
                    record_version = record_version + 1,
                    revoked_by = %s,
                    revoked_at = %s
                where authorization_id = %s
                """,
                (
                    actor_id,
                    BASE_TIME + timedelta(seconds=3),
                    authorization_id,
                ),
            )

        connection.execute(
            """
            update public.operational_paper_capital_authorizations
            set state = 'REVOKED',
                record_version = record_version + 1,
                revoked_by = %s,
                revoked_at = %s
            where authorization_id = %s
            """,
            (
                actor_id,
                BASE_TIME + timedelta(seconds=6),
                authorization_id,
            ),
        )

        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_capital_authorizations
                set record_version = record_version + 1
                where authorization_id = %s
                """,
                (authorization_id,),
            )

        row = connection.execute(
            """
            select state, record_version, revoked_by, revoked_at
            from public.operational_paper_capital_authorizations
            where authorization_id = %s
            """,
            (authorization_id,),
        ).fetchone()

    assert row == (
        "REVOKED",
        2,
        actor_id,
        BASE_TIME + timedelta(seconds=6),
    )


def test_data_api_roles_cannot_execute_capital_authorization_functions(
    database_url: str,
) -> None:
    roles = ("anon", "authenticated", "service_role")
    functions = (
        "public.validate_operational_paper_capital_authorization_insert()",
        "public.protect_operational_paper_capital_authorization()",
        "public.reject_simulation_terminalization_with_authorized_capital()",
        "public.validate_capital_movement()",
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        for function in functions:
            public_execute = connection.execute(
                """
                select exists (
                    select 1
                    from pg_catalog.pg_proc as procedure
                    cross join lateral pg_catalog.aclexplode(
                        coalesce(
                            procedure.proacl,
                            pg_catalog.acldefault('f', procedure.proowner)
                        )
                    ) as privilege
                    where procedure.oid = %s::regprocedure
                      and privilege.grantee = 0
                      and privilege.privilege_type = 'EXECUTE'
                )
                """,
                (function,),
            ).fetchone()
            assert public_execute == (False,)

            for role in roles:
                privilege = connection.execute(
                    """
                    select pg_catalog.has_function_privilege(
                        %s,
                        %s,
                        'EXECUTE'
                    )
                    """,
                    (role, function),
                ).fetchone()
                assert privilege == (False,)


# B2B-3B2-END
