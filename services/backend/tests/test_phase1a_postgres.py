"""Local PostgreSQL integration tests for the unapplied Phase 1A migration.

The suite starts an isolated PostgreSQL cluster discovered through ``pg_config``.
It never connects to Supabase or reads database connection settings from the
environment.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from queue import Queue
from time import monotonic, sleep
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection, sql

from tests.postgres_support import add_auth_user as _add_auth_user


def _add_simulation(
    connection: Connection[Any],
    *,
    creator_id: UUID,
    initial_capital: Decimal = Decimal("100"),
    status: str = "ACTIVE",
) -> UUID:
    simulation_id = uuid4()
    connection.execute(
        """
        insert into public.simulation_runs (
            id,
            name,
            status,
            initial_capital,
            created_by
        )
        values (%s, %s, %s, %s, %s)
        """,
        (simulation_id, "Local PostgreSQL test", status, initial_capital, creator_id),
    )
    return simulation_id


def _add_initial_capital(
    connection: Connection[Any],
    *,
    simulation_id: UUID,
    creator_id: UUID,
    amount: Decimal = Decimal("100"),
) -> UUID:
    movement_id = uuid4()
    connection.execute(
        """
        insert into public.capital_movements (
            id,
            simulation_id,
            type,
            amount,
            reason,
            created_by
        )
        values (%s, %s, 'INITIAL_CAPITAL', %s, %s, %s)
        """,
        (movement_id, simulation_id, amount, "Initial paper capital", creator_id),
    )
    return movement_id


def _seed_simulation(
    database_url: str,
    *,
    initial_capital: Decimal = Decimal("100"),
) -> tuple[UUID, UUID]:
    creator_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        simulation_id = _add_simulation(
            connection,
            creator_id=creator_id,
            initial_capital=initial_capital,
        )
        _add_initial_capital(
            connection,
            simulation_id=simulation_id,
            creator_id=creator_id,
            amount=initial_capital,
        )
    return creator_id, simulation_id


def test_withdrawal_that_would_make_balance_negative_is_rejected(database_url: str) -> None:
    """A movement cannot take a simulation below a zero numeric balance."""
    creator_id, simulation_id = _seed_simulation(database_url)

    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                insert into public.capital_movements (
                    simulation_id,
                    type,
                    amount,
                    reason,
                    created_by
                )
                values (%s, 'ADMIN_WITHDRAWAL', %s, %s, %s)
                """,
                (
                    simulation_id,
                    Decimal("-100.00000001"),
                    "Rejected test withdrawal",
                    creator_id,
                ),
            )

        balance = connection.execute(
            """
            select sum(amount)
            from public.capital_movements
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert balance == (Decimal("100.00000000"),)


def test_concurrent_withdrawals_cannot_overdraw_balance(database_url: str) -> None:
    """The simulation row lock serializes two otherwise valid withdrawals."""
    creator_id, simulation_id = _seed_simulation(database_url)
    second_backend_pid: Queue[int] = Queue(maxsize=1)

    def withdraw() -> None:
        with psycopg.connect(database_url, autocommit=True) as connection:
            pid_row = connection.execute("select pg_backend_pid()").fetchone()
            assert pid_row is not None
            second_backend_pid.put(pid_row[0])
            with connection.transaction():
                connection.execute(
                    """
                    insert into public.capital_movements (
                        simulation_id,
                        type,
                        amount,
                        reason,
                        created_by
                    )
                    values (%s, 'ADMIN_WITHDRAWAL', %s, %s, %s)
                    """,
                    (
                        simulation_id,
                        Decimal("-80"),
                        "Concurrent test withdrawal",
                        creator_id,
                    ),
                )

    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        psycopg.connect(database_url, autocommit=True) as first_connection,
    ):
        with first_connection.transaction():
            first_connection.execute(
                """
                insert into public.capital_movements (
                    simulation_id,
                    type,
                    amount,
                    reason,
                    created_by
                )
                values (%s, 'ADMIN_WITHDRAWAL', %s, %s, %s)
                """,
                (
                    simulation_id,
                    Decimal("-80"),
                    "First concurrent test withdrawal",
                    creator_id,
                ),
            )
            future = executor.submit(withdraw)
            waiting_pid = second_backend_pid.get(timeout=10)
            assert _wait_until_backend_is_locked(database_url, waiting_pid)

        with pytest.raises(psycopg.Error):
            future.result()

    with psycopg.connect(database_url, autocommit=True) as connection:
        result = connection.execute(
            """
            select
                sum(amount),
                count(*) filter (where type = 'ADMIN_WITHDRAWAL')
            from public.capital_movements
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()
    assert result == (Decimal("20.00000000"), 1)


def _wait_until_backend_is_locked(database_url: str, backend_pid: int) -> bool:
    """Poll local server state until a backend is waiting for the row lock."""
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


def test_only_one_initial_capital_is_allowed(database_url: str) -> None:
    """A simulation cannot have a duplicate INITIAL_CAPITAL ledger entry."""
    creator_id, simulation_id = _seed_simulation(database_url)

    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            _add_initial_capital(
                connection,
                simulation_id=simulation_id,
                creator_id=creator_id,
            )

        count = connection.execute(
            """
            select count(*)
            from public.capital_movements
            where simulation_id = %s
              and type = 'INITIAL_CAPITAL'
            """,
            (simulation_id,),
        ).fetchone()
    assert count == (1,)


def test_initial_capital_must_match_simulation(database_url: str) -> None:
    """The first ledger amount must equal simulation_runs.initial_capital."""
    creator_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        simulation_id = _add_simulation(connection, creator_id=creator_id)

        with pytest.raises(psycopg.Error):
            _add_initial_capital(
                connection,
                simulation_id=simulation_id,
                creator_id=creator_id,
                amount=Decimal("99"),
            )


def test_movement_before_initial_capital_is_rejected(database_url: str) -> None:
    """Every simulation ledger must begin with its INITIAL_CAPITAL row."""
    creator_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        simulation_id = _add_simulation(connection, creator_id=creator_id)

        with pytest.raises(psycopg.Error):
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
                (simulation_id, Decimal("1"), "Premature deposit", creator_id),
            )


def test_movement_for_missing_simulation_is_rejected_by_trigger(database_url: str) -> None:
    """The balance trigger reports a missing simulation before FK validation."""
    creator_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        with pytest.raises(
            psycopg.errors.ForeignKeyViolation,
            match="references a simulation that does not exist",
        ):
            connection.execute(
                """
                insert into public.capital_movements (
                    simulation_id,
                    type,
                    amount,
                    reason,
                    created_by
                )
                values (%s, 'INITIAL_CAPITAL', %s, %s, %s)
                """,
                (
                    uuid4(),
                    Decimal("100"),
                    "Missing simulation test",
                    creator_id,
                ),
            )


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_capital_movement_ledger_is_immutable(database_url: str, operation: str) -> None:
    """Direct owner connections cannot rewrite or remove cash history."""
    _creator_id, simulation_id = _seed_simulation(database_url)

    if operation == "update":
        statement = """
            update public.capital_movements
            set reason = 'Rewritten history'
            where simulation_id = %s
        """
    else:
        statement = """
            delete from public.capital_movements
            where simulation_id = %s
        """

    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(statement, (simulation_id,))

        rows = connection.execute(
            """
            select reason
            from public.capital_movements
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchall()
    assert rows == [("Initial paper capital",)]


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_audit_log_is_immutable(database_url: str, operation: str) -> None:
    """Direct owner connections cannot rewrite or remove audit history."""
    audit_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into public.audit_logs (id, action, entity_type)
            values (%s, 'CREATED', 'SIMULATION')
            """,
            (audit_id,),
        )

        if operation == "update":
            statement = """
                update public.audit_logs
                set action = 'REWRITTEN'
                where id = %s
            """
        else:
            statement = "delete from public.audit_logs where id = %s"

        with pytest.raises(psycopg.Error):
            connection.execute(statement, (audit_id,))

        rows = connection.execute(
            "select action, entity_type from public.audit_logs where id = %s",
            (audit_id,),
        ).fetchall()
    assert rows == [("CREATED", "SIMULATION")]


def test_simulation_cannot_be_deleted_by_direct_connection(database_url: str) -> None:
    """Historical simulations remain protected even when RLS is bypassed."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        creator_id = uuid4()
        _add_auth_user(connection, creator_id)
        simulation_id = _add_simulation(connection, creator_id=creator_id)

        with pytest.raises(psycopg.Error):
            connection.execute(
                "delete from public.simulation_runs where id = %s",
                (simulation_id,),
            )

        exists = connection.execute(
            "select exists(select 1 from public.simulation_runs where id = %s)",
            (simulation_id,),
        ).fetchone()
    assert exists == (True,)


def test_simulation_initial_capital_cannot_be_changed(database_url: str) -> None:
    """Financial identity fields are immutable through direct connections."""
    _, simulation_id = _seed_simulation(database_url)

    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.simulation_runs
                set initial_capital = %s
                where id = %s
                """,
                (Decimal("200"), simulation_id),
            )

        capital = connection.execute(
            "select initial_capital from public.simulation_runs where id = %s",
            (simulation_id,),
        ).fetchone()
    assert capital == (Decimal("100.00000000"),)


def test_active_simulation_rejects_ended_at(database_url: str) -> None:
    """ACTIVE and ended_at are mutually exclusive."""
    creator_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                insert into public.simulation_runs (
                    name,
                    status,
                    initial_capital,
                    started_at,
                    ended_at,
                    created_by
                )
                values (
                    'Invalid active simulation',
                    'ACTIVE',
                    %s,
                    '2026-01-01T00:00:00Z',
                    '2026-01-02T00:00:00Z',
                    %s
                )
                """,
                (Decimal("100"), creator_id),
            )


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
def test_finished_simulation_requires_ended_at(database_url: str, status: str) -> None:
    """Both terminal statuses require an explicit end timestamp."""
    creator_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        with pytest.raises(psycopg.Error):
            _add_simulation(connection, creator_id=creator_id, status=status)


def test_ended_at_cannot_precede_started_at(database_url: str) -> None:
    """Simulation chronology cannot run backwards."""
    creator_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                insert into public.simulation_runs (
                    name,
                    status,
                    initial_capital,
                    started_at,
                    ended_at,
                    created_by
                )
                values (
                    'Invalid chronology',
                    'COMPLETED',
                    %s,
                    '2026-01-02T00:00:00Z',
                    '2026-01-01T00:00:00Z',
                    %s
                )
                """,
                (Decimal("100"), creator_id),
            )


@pytest.mark.parametrize(
    "special_value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_simulation_rejects_special_financial_values(
    database_url: str,
    special_value: Decimal,
) -> None:
    """Simulation capital accepts finite numeric values only."""
    creator_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        with pytest.raises(psycopg.Error):
            _add_simulation(
                connection,
                creator_id=creator_id,
                initial_capital=special_value,
            )


@pytest.mark.parametrize(
    "special_value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_capital_movement_rejects_special_financial_values(
    database_url: str,
    special_value: Decimal,
) -> None:
    """Ledger amounts accept finite numeric values only."""
    creator_id, simulation_id = _seed_simulation(database_url)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                insert into public.capital_movements (
                    simulation_id,
                    type,
                    amount,
                    reason,
                    created_by
                )
                values (%s, 'ADJUSTMENT', %s, %s, %s)
                """,
                (simulation_id, special_value, "Invalid adjustment", creator_id),
            )


@pytest.mark.parametrize(
    ("table_name", "column_name", "valid_values"),
    [
        (
            "simulation_runs",
            "name",
            {
                "status": "ACTIVE",
                "initial_capital": Decimal("100"),
            },
        ),
        (
            "capital_movements",
            "reason",
            {
                "type": "INITIAL_CAPITAL",
                "amount": Decimal("100"),
            },
        ),
        (
            "system_settings",
            "key",
            {
                "value": "{}",
                "description": "Description",
            },
        ),
        (
            "system_settings",
            "description",
            {
                "key": "blank_description_test",
                "value": "{}",
            },
        ),
        (
            "audit_logs",
            "action",
            {
                "entity_type": "SIMULATION",
            },
        ),
        (
            "audit_logs",
            "entity_type",
            {
                "action": "CREATED",
            },
        ),
    ],
)
def test_required_text_fields_reject_whitespace(
    database_url: str,
    table_name: str,
    column_name: str,
    valid_values: dict[str, object],
) -> None:
    """Required descriptive fields cannot contain whitespace alone."""
    creator_id = uuid4()
    values = {**valid_values, column_name: "   "}

    with psycopg.connect(database_url, autocommit=True) as connection:
        _add_auth_user(connection, creator_id)
        if table_name == "simulation_runs":
            values["created_by"] = creator_id
        elif table_name == "capital_movements":
            simulation_id = _add_simulation(connection, creator_id=creator_id)
            values["simulation_id"] = simulation_id
            values["created_by"] = creator_id

        columns = sql.SQL(", ").join(map(sql.Identifier, values))
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in values)
        statement = sql.SQL("insert into public.{} ({}) values ({})").format(
            sql.Identifier(table_name),
            columns,
            placeholders,
        )
        with pytest.raises(psycopg.Error):
            connection.execute(statement, tuple(values.values()))


def _set_authenticated_role(connection: Connection[Any], user_id: UUID) -> None:
    """Model the database role and JWT subject set by the Supabase Data API."""
    connection.execute("set role authenticated")
    connection.execute(
        "select set_config('request.jwt.claim.sub', %s, false)",
        (str(user_id),),
    )


@pytest.mark.parametrize("api_role", ["authenticated", "service_role"])
@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_data_api_roles_cannot_modify_admins(
    database_url: str,
    api_role: str,
    operation: str,
) -> None:
    """Data API roles cannot mutate app_admins, including RLS-bypass roles."""
    administrator_id = uuid4()
    other_admin_id = uuid4()
    candidate_id = uuid4()

    with psycopg.connect(database_url, autocommit=True) as connection:
        for user_id in (administrator_id, other_admin_id, candidate_id):
            _add_auth_user(connection, user_id)
        connection.execute(
            """
            insert into public.app_admins (user_id, created_by)
            values (%s, %s), (%s, %s)
            """,
            (
                administrator_id,
                administrator_id,
                other_admin_id,
                administrator_id,
            ),
        )

    with psycopg.connect(database_url, autocommit=True) as connection:
        if api_role == "authenticated":
            _set_authenticated_role(connection, administrator_id)
            visible_admins = connection.execute(
                "select user_id from public.app_admins order by user_id"
            ).fetchall()
            assert len(visible_admins) == 2
        else:
            connection.execute("set role service_role")

        parameters: tuple[object, ...]
        if operation == "insert":
            statement = """
                insert into public.app_admins (user_id, created_by)
                values (%s, %s)
            """
            parameters = (candidate_id, administrator_id)
        elif operation == "update":
            statement = """
                update public.app_admins
                set created_by = %s
                where user_id = %s
            """
            parameters = (candidate_id, other_admin_id)
        else:
            statement = "delete from public.app_admins where user_id = %s"
            parameters = (other_admin_id,)
        with pytest.raises(psycopg.Error):
            connection.execute(statement, parameters)

    with psycopg.connect(database_url, autocommit=True) as connection:
        rows = connection.execute(
            """
            select user_id, created_by
            from public.app_admins
            order by user_id
            """
        ).fetchall()
    assert set(rows) == {
        (administrator_id, administrator_id),
        (other_admin_id, administrator_id),
    }


def test_public_summary_keeps_balance_and_profit_loss_correct(database_url: str) -> None:
    """The public view reports all cash and only trading P/L in its P/L field."""
    creator_id, simulation_id = _seed_simulation(database_url)
    movements = [
        ("TRADE_PROFIT", Decimal("10"), "Winning paper trade"),
        ("FEE", Decimal("-3"), "Paper trading fee"),
        ("ADMIN_DEPOSIT", Decimal("20"), "Administrative paper deposit"),
    ]

    with psycopg.connect(database_url, autocommit=True) as connection:
        for movement_type, amount, reason in movements:
            connection.execute(
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
                (simulation_id, movement_type, amount, reason, creator_id),
            )

        connection.execute("set role anon")
        row = connection.execute(
            """
            select
                initial_capital,
                current_balance,
                total_profit_loss,
                status
            from public.active_simulation_summary
            where simulation_id = %s
            """,
            (simulation_id,),
        ).fetchone()

    assert row == (
        Decimal("100.00000000"),
        Decimal("127.00000000"),
        Decimal("7.00000000"),
        "ACTIVE",
    )
