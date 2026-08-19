"""Phase 7-05 worker-runtime observability persistence invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection

BASE_TIME = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

RUNTIME_TABLE = "public.market_data_worker_runtimes"
EVENT_TABLE = "public.market_data_worker_events"
EVENT_SEQUENCE = "public.market_data_worker_events_id_seq"

DATA_API_ROLES = ("anon", "authenticated", "service_role")
WORKER_TABLES = (RUNTIME_TABLE, EVENT_TABLE)


def _insert_runtime(
    connection: Connection[Any],
    *,
    runtime_id: UUID | None = None,
) -> UUID:
    value = runtime_id or uuid4()
    connection.execute(
        """
        insert into public.market_data_worker_runtimes (
            id,
            lifecycle_state,
            activity_state,
            started_at,
            heartbeat_at
        )
        values (%s, 'RUNNING', 'IDLE', %s, %s)
        """,
        (value, BASE_TIME, BASE_TIME),
    )
    return value


def test_schema_has_rls_no_policies_and_backend_only_acl(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        rls = connection.execute(
            """
            select class.relname, class.relrowsecurity
            from pg_catalog.pg_class as class
            join pg_catalog.pg_namespace as namespace
              on namespace.oid = class.relnamespace
            where namespace.nspname = 'public'
              and class.relname in (
                  'market_data_worker_runtimes',
                  'market_data_worker_events'
              )
            order by class.relname
            """
        ).fetchall()

        assert rls == [
            ("market_data_worker_events", True),
            ("market_data_worker_runtimes", True),
        ]

        policies = connection.execute(
            """
            select tablename, policyname
            from pg_catalog.pg_policies
            where schemaname = 'public'
              and tablename in (
                  'market_data_worker_runtimes',
                  'market_data_worker_events'
              )
            order by tablename, policyname
            """
        ).fetchall()

        assert policies == []

        for role in DATA_API_ROLES:
            for table in WORKER_TABLES:
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
                        table,
                        role,
                        table,
                        role,
                        table,
                        role,
                        table,
                    ),
                ).fetchone()

                assert privileges == (False, False, False, False)

            sequence_privileges = connection.execute(
                """
                select
                    has_sequence_privilege(%s, %s, 'USAGE'),
                    has_sequence_privilege(%s, %s, 'SELECT'),
                    has_sequence_privilege(%s, %s, 'UPDATE')
                """,
                (
                    role,
                    EVENT_SEQUENCE,
                    role,
                    EVENT_SEQUENCE,
                    role,
                    EVENT_SEQUENCE,
                ),
            ).fetchone()

            assert sequence_privileges == (False, False, False)

            for function_name in (
                "public.validate_market_data_worker_runtime_insert()",
                "public.protect_market_data_worker_runtime()",
                "public.reject_market_data_worker_event_change()",
            ):
                assert connection.execute(
                    "select has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, function_name),
                ).fetchone() == (False,)

        for table in WORKER_TABLES:
            assert connection.execute(
                """
                select has_table_privilege(
                    current_user,
                    %s,
                    'SELECT,INSERT,UPDATE,DELETE'
                )
                """,
                (table,),
            ).fetchone() == (True,)


@pytest.mark.parametrize("role", DATA_API_ROLES)
@pytest.mark.parametrize("table", WORKER_TABLES)
def test_data_api_roles_cannot_access_worker_tables(
    database_url: str,
    role: str,
    table: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"set role {role}")

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(f"select count(*) from {table}")

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(f"delete from {table} where false")


@pytest.mark.parametrize(
    (
        "lifecycle_state",
        "activity_state",
        "heartbeat_at",
        "stopped_at",
        "failure_code",
    ),
    [
        ("STOPPED", "IDLE", BASE_TIME, BASE_TIME, None),
        ("FAILED", "IDLE", BASE_TIME, BASE_TIME, "DATABASE_FAILURE"),
        ("RUNNING", "ACTIVE", BASE_TIME, None, None),
        ("RUNNING", "IDLE", BASE_TIME + timedelta(seconds=1), None, None),
        ("RUNNING", "IDLE", BASE_TIME, BASE_TIME, None),
        ("RUNNING", "IDLE", BASE_TIME, None, "DATABASE_FAILURE"),
    ],
)
def test_runtime_epoch_requires_running_idle_initial_state(
    database_url: str,
    lifecycle_state: str,
    activity_state: str,
    heartbeat_at: datetime,
    stopped_at: datetime | None,
    failure_code: str | None,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                insert into public.market_data_worker_runtimes (
                    id,
                    lifecycle_state,
                    activity_state,
                    started_at,
                    heartbeat_at,
                    stopped_at,
                    failure_code
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    lifecycle_state,
                    activity_state,
                    BASE_TIME,
                    heartbeat_at,
                    stopped_at,
                    failure_code,
                ),
            )


def test_multiple_running_runtime_epochs_are_allowed(
    database_url: str,
) -> None:
    first = uuid4()
    second = uuid4()

    with psycopg.connect(database_url, autocommit=True) as connection:
        _insert_runtime(connection, runtime_id=first)
        _insert_runtime(connection, runtime_id=second)

        assert connection.execute(
            """
            select count(*)
            from public.market_data_worker_runtimes
            where id in (%s, %s)
              and lifecycle_state = 'RUNNING'
            """,
            (first, second),
        ).fetchone() == (2,)


def test_runtime_heartbeat_is_monotonic_and_terminal_history_is_immutable(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        runtime_id = _insert_runtime(connection)

        active_at = BASE_TIME + timedelta(seconds=1)
        connection.execute(
            """
            update public.market_data_worker_runtimes
            set activity_state = 'ACTIVE',
                heartbeat_at = %s
            where id = %s
            """,
            (active_at, runtime_id),
        )

        with pytest.raises(psycopg.Error) as regression:
            connection.execute(
                """
                update public.market_data_worker_runtimes
                set heartbeat_at = %s
                where id = %s
                """,
                (BASE_TIME, runtime_id),
            )

        assert regression.value.sqlstate == "22007"

        stopped_at = BASE_TIME + timedelta(seconds=2)
        connection.execute(
            """
            update public.market_data_worker_runtimes
            set lifecycle_state = 'STOPPED',
                activity_state = 'IDLE',
                heartbeat_at = %s,
                stopped_at = %s
            where id = %s
            """,
            (stopped_at, stopped_at, runtime_id),
        )

        assert connection.execute(
            """
            select
                lifecycle_state,
                activity_state,
                heartbeat_at,
                stopped_at,
                failure_code
            from public.market_data_worker_runtimes
            where id = %s
            """,
            (runtime_id,),
        ).fetchone() == (
            "STOPPED",
            "IDLE",
            stopped_at,
            stopped_at,
            None,
        )

        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.market_data_worker_runtimes
                set heartbeat_at = %s
                where id = %s
                """,
                (stopped_at + timedelta(seconds=1), runtime_id),
            )

        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                delete from public.market_data_worker_runtimes
                where id = %s
                """,
                (runtime_id,),
            )


@pytest.mark.parametrize(
    "invalid_failure_code",
    [None, "NETWORK_FAILURE", "raw internal exception"],
)
def test_failed_runtime_requires_closed_sanitized_failure_code(
    database_url: str,
    invalid_failure_code: str | None,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        runtime_id = _insert_runtime(connection)
        failed_at = BASE_TIME + timedelta(seconds=1)

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                update public.market_data_worker_runtimes
                set lifecycle_state = 'FAILED',
                    activity_state = 'IDLE',
                    heartbeat_at = %s,
                    stopped_at = %s,
                    failure_code = %s
                where id = %s
                """,
                (
                    failed_at,
                    failed_at,
                    invalid_failure_code,
                    runtime_id,
                ),
            )


@pytest.mark.parametrize(
    "failure_code",
    [
        "DATABASE_FAILURE",
        "LOCAL_STATE_FAILURE",
        "UNEXPECTED_FAILURE",
    ],
)
def test_failed_runtime_accepts_only_closed_failure_taxonomy(
    database_url: str,
    failure_code: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        runtime_id = _insert_runtime(connection)
        failed_at = BASE_TIME + timedelta(seconds=1)

        connection.execute(
            """
            update public.market_data_worker_runtimes
            set lifecycle_state = 'FAILED',
                activity_state = 'IDLE',
                heartbeat_at = %s,
                stopped_at = %s,
                failure_code = %s
            where id = %s
            """,
            (failed_at, failed_at, failure_code, runtime_id),
        )

        assert connection.execute(
            """
            select lifecycle_state, activity_state, failure_code
            from public.market_data_worker_runtimes
            where id = %s
            """,
            (runtime_id,),
        ).fetchone() == ("FAILED", "IDLE", failure_code)


def test_worker_events_are_append_only_and_event_type_is_closed(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        runtime_id = _insert_runtime(connection)

        event_id = connection.execute(
            """
            insert into public.market_data_worker_events (
                runtime_id,
                event_type,
                occurred_at
            )
            values (%s, 'RUNTIME_STARTED', %s)
            returning id
            """,
            (runtime_id, BASE_TIME),
        ).fetchone()

        assert event_id is not None
        persisted_event_id = event_id[0]

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                insert into public.market_data_worker_events (
                    runtime_id,
                    event_type,
                    occurred_at
                )
                values (%s, 'ARBITRARY_EVENT', %s)
                """,
                (runtime_id, BASE_TIME),
            )

        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.market_data_worker_events
                set event_type = 'RUNTIME_STOPPED'
                where id = %s
                """,
                (persisted_event_id,),
            )

        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                delete from public.market_data_worker_events
                where id = %s
                """,
                (persisted_event_id,),
            )


def test_worker_event_shape_is_closed(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        runtime_id = _insert_runtime(connection)

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                insert into public.market_data_worker_events (
                    runtime_id,
                    event_type,
                    operation_state,
                    occurred_at
                )
                values (%s, 'RUNTIME_STARTED', 'COMPLETED', %s)
                """,
                (runtime_id, BASE_TIME),
            )

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                insert into public.market_data_worker_events (
                    runtime_id,
                    event_type,
                    operation_state,
                    occurred_at
                )
                values (%s, 'OPERATION_SETTLED', 'COMPLETED', %s)
                """,
                (runtime_id, BASE_TIME),
            )

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                insert into public.market_data_worker_events (
                    runtime_id,
                    operation_id,
                    event_type,
                    operation_state,
                    occurred_at
                )
                values (%s, %s, 'OPERATION_SETTLED', 'RUNNING', %s)
                """,
                (runtime_id, uuid4(), BASE_TIME),
            )


def test_worker_event_foreign_keys_are_enforced(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        runtime_id = _insert_runtime(connection)

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                insert into public.market_data_worker_events (
                    runtime_id,
                    event_type,
                    occurred_at
                )
                values (%s, 'RUNTIME_STARTED', %s)
                """,
                (uuid4(), BASE_TIME),
            )

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                insert into public.market_data_worker_events (
                    runtime_id,
                    operation_id,
                    event_type,
                    operation_state,
                    occurred_at
                )
                values (%s, %s, 'OPERATION_SETTLED', 'COMPLETED', %s)
                """,
                (runtime_id, uuid4(), BASE_TIME),
            )
