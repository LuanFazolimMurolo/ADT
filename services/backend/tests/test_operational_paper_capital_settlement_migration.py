"""Local PostgreSQL foundation tests for Phase 7-14 era/settlement evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.operational_paper_capital_eras import (
    OperationalPaperCapitalEra,
    OperationalPaperCapitalEraDesignationIntent,
    OperationalPaperCapitalEraSpecification,
    operational_paper_capital_era_designation_intent_fingerprint,
    operational_paper_capital_era_specification_checksum,
)
from app.operational_paper_session_runs import OperationalPaperSessionRunEpoch
from app.operational_paper_session_settlements import (
    OperationalPaperSessionSettlement,
    OperationalPaperSessionSettlementIntent,
    OperationalPaperSessionSettlementSpecification,
    operational_paper_session_settlement_intent_fingerprint,
    operational_paper_session_settlement_specification_checksum,
)
from tests.postgres_support import MIGRATION_PATHS, add_auth_user
from tests.test_operational_paper_capital_authorizations_migration import _seed_simulation
from tests.test_operational_paper_session_runs_migration import _terminalize_pending
from tests.test_operational_paper_session_runs_migration import pending_epoch as pending_epoch
from tests.test_operational_paper_session_runs_migration import run_activation as run_activation

MIGRATION_PATH = (
    Path(__file__).parents[3] / "supabase/migrations/"
    "20260912000000_phase_7_14_official_paper_capital_eras_session_settlements.sql"
)
ERAS = "operational_paper_capital_eras"
SETTLEMENTS = "operational_paper_session_settlements"
TABLES = (ERAS, SETTLEMENTS)
ROLES = ("anon", "authenticated", "service_role")
AMOUNTS = (
    "initial_capital",
    "final_quote_cash",
    "realized_pnl",
    "unrealized_pnl",
    "base_quantity",
    "average_entry_price",
    "cost_basis",
    "total_fees",
    "total_slippage_cost",
    "settlement_delta",
)
SETTLEMENT_HASHES = (
    "era_checksum",
    "epoch_checksum",
    "authorization_checksum",
    "session_id",
    "config_checksum",
    "state_id",
    "state_checksum",
    "dataset_version",
    "source_checksum",
    "timeline_id",
    "timeline_content_checksum",
    "settlement_checksum",
    "settle_intent_fingerprint",
)


def _insert(connection: psycopg.Connection[Any], table: str, row: dict[str, object]) -> None:
    assert table in TABLES
    connection.execute(
        sql.SQL("insert into public.{} ({}) values ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, row)),
            sql.SQL(", ").join(sql.Placeholder() for _ in row),
        ),
        tuple(row.values()),
    )


def _era_row(
    connection: psycopg.Connection[Any],
    simulation_id: UUID,
    actor_id: UUID,
) -> dict[str, object]:
    simulation = connection.execute(
        "select currency, initial_capital, started_at from public.simulation_runs where id = %s",
        (simulation_id,),
    ).fetchone()
    assert simulation is not None
    currency, initial_capital, started_at = simulation
    started_at = started_at.astimezone(UTC)
    spec = OperationalPaperCapitalEraSpecification(
        1, 1, simulation_id, currency, initial_capital, started_at
    )
    era = OperationalPaperCapitalEra(
        era_id=uuid4(),
        schema_version=1,
        designation_contract_version=1,
        simulation_id=simulation_id,
        currency=currency,
        initial_capital=initial_capital,
        simulation_started_at=started_at,
        era_checksum=operational_paper_capital_era_specification_checksum(spec),
        designated_by=actor_id,
        designated_at=started_at,
        designation_idempotency_key=f"era:{uuid4().hex}",
        designation_intent_fingerprint=operational_paper_capital_era_designation_intent_fingerprint(
            OperationalPaperCapitalEraDesignationIntent(simulation_id)
        ),
    )
    return {field.name: getattr(era, field.name) for field in fields(era)}


@pytest.fixture
def era_row(database_url: str, auth_user_id: UUID) -> dict[str, object]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(connection, auth_user_id)
        return _era_row(connection, simulation_id, auth_user_id)


@dataclass(frozen=True)
class SettlementSeed:
    epoch: OperationalPaperSessionRunEpoch
    era: dict[str, object]
    initial_capital: Decimal


@pytest.fixture
def settlement_seed(
    database_url: str,
    pending_epoch: OperationalPaperSessionRunEpoch,
) -> SettlementSeed:
    epoch = _terminalize_pending(database_url, pending_epoch)
    with psycopg.connect(database_url, autocommit=True) as connection:
        # Persist a historical designation snapshot. Designation-time eligibility
        # under the financial mutex is Gate 2C, not this schema fixture's concern.
        era = _era_row(connection, epoch.simulation_id, epoch.start_requested_by)
        _insert(connection, ERAS, era)
        row = connection.execute(
            "select authorized_capital from public.operational_paper_capital_authorizations "
            "where authorization_id = %s",
            (epoch.authorization_binding.authorization_id,),
        ).fetchone()
        assert row is not None
        return SettlementSeed(epoch, era, row[0])


def _settlement_row(
    seed: SettlementSeed,
    *,
    delta: Decimal = Decimal("0"),
    movement_id: UUID | None = None,
) -> dict[str, object]:
    epoch = seed.epoch
    assert epoch.terminal_at is not None
    era_id = seed.era["era_id"]
    era_checksum = seed.era["era_checksum"]
    assert isinstance(era_id, UUID) and isinstance(era_checksum, str)
    spec = OperationalPaperSessionSettlementSpecification(
        schema_version=1,
        settlement_contract_version=1,
        era_id=era_id,
        era_checksum=era_checksum,
        simulation_id=epoch.simulation_id,
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        epoch_terminal_at=epoch.terminal_at,
        authorization_id=epoch.authorization_binding.authorization_id,
        authorization_checksum=epoch.authorization_binding.authorization_checksum,
        session_id=epoch.session_id,
        config_checksum=epoch.config_checksum,
        state_id="a" * 64,
        state_checksum="b" * 64,
        dataset_version="c" * 64,
        source_checksum="d" * 64,
        timeline_id="e" * 64,
        timeline_content_checksum="f" * 64,
        initial_capital=seed.initial_capital,
        final_quote_cash=seed.initial_capital + delta,
        realized_pnl=delta,
        unrealized_pnl=Decimal("0"),
        base_quantity=Decimal("0"),
        average_entry_price=Decimal("0"),
        cost_basis=Decimal("0"),
        total_fees=Decimal("2"),
        total_slippage_cost=Decimal("3"),
        settlement_delta=delta,
        ledger_movement_id=movement_id,
    )
    settlement = OperationalPaperSessionSettlement(
        settlement_id=uuid4(),
        specification=spec,
        settlement_checksum=operational_paper_session_settlement_specification_checksum(spec),
        settled_by=epoch.start_requested_by,
        settled_at=epoch.terminal_at,
        settle_idempotency_key=f"settle:{uuid4().hex}",
        settle_intent_fingerprint=operational_paper_session_settlement_intent_fingerprint(
            OperationalPaperSessionSettlementIntent(epoch.epoch_id, epoch.epoch_checksum)
        ),
    )
    row = {field.name: getattr(spec, field.name) for field in fields(spec)}
    row.update(
        {
            field.name: getattr(settlement, field.name)
            for field in fields(settlement)
            if field.name != "specification"
        }
    )
    return row


def _movement(connection: psycopg.Connection[Any], seed: SettlementSeed, delta: Decimal) -> UUID:
    movement_id = uuid4()
    connection.execute(
        """
        insert into public.capital_movements (id, simulation_id, type, amount, reason, created_by)
        values (%s, %s, %s, %s, 'local settlement schema fixture', %s)
        """,
        (
            movement_id,
            seed.epoch.simulation_id,
            "TRADE_PROFIT" if delta > 0 else "TRADE_LOSS",
            delta,
            seed.epoch.start_requested_by,
        ),
    )
    return movement_id


def _assert_insert_error(
    connection: psycopg.Connection[Any],
    table: str,
    row: dict[str, object],
    error: type[psycopg.Error],
    constraint: str | None = None,
) -> None:
    with pytest.raises(error) as caught:
        with connection.transaction():
            _insert(connection, table, row)
    if constraint is not None:
        assert caught.value.diag.constraint_name == constraint


def test_migration_is_discovered_and_only_adds_two_evidence_tables() -> None:
    assert MIGRATION_PATH in MIGRATION_PATHS
    assert (
        MIGRATION_PATH.name
        > "20260909000000_phase_7_12_operational_market_data_collector_epochs.sql"
    )
    text = MIGRATION_PATH.read_text()
    assert re.findall(r"(?m)^create table public\.([a-z_]+)", text) == list(TABLES)
    assert not re.search(r"(?im)^\s*(insert into|update|delete from|drop|truncate)\b", text)
    assert not re.search(
        r"(?im)^alter table public\."
        r"(?!operational_paper_capital_eras\b|operational_paper_session_settlements\b)",
        text,
    )


@pytest.mark.parametrize("table", TABLES)
def test_exact_domain_columns_types_nullability_and_primary_keys(
    database_url: str, table: str
) -> None:
    if table == ERAS:
        expected = {field.name for field in fields(OperationalPaperCapitalEra)}
        uuid_columns = {"era_id", "simulation_id", "designated_by"}
        timestamps = {"simulation_started_at", "designated_at"}
        integers = {"schema_version", "designation_contract_version"}
        amounts = {"initial_capital"}
        primary_key = "era_id"
    else:
        expected = {field.name for field in fields(OperationalPaperSessionSettlementSpecification)}
        expected |= {field.name for field in fields(OperationalPaperSessionSettlement)} - {
            "specification"
        }
        uuid_columns = {
            "settlement_id",
            "era_id",
            "simulation_id",
            "epoch_id",
            "authorization_id",
            "ledger_movement_id",
            "settled_by",
        }
        timestamps = {"epoch_terminal_at", "settled_at"}
        integers = {"schema_version", "settlement_contract_version"}
        amounts = set(AMOUNTS)
        primary_key = "settlement_id"
    with psycopg.connect(database_url) as connection:
        columns = connection.execute(
            """
            select column_name, data_type, is_nullable,
                   numeric_precision, numeric_scale, column_default
            from information_schema.columns where table_schema = 'public' and table_name = %s
            """,
            (table,),
        ).fetchall()
        assert {row[0] for row in columns} == expected
        for name, dtype, nullable, precision, scale, default in columns:
            assert dtype == (
                "uuid"
                if name in uuid_columns
                else "timestamp with time zone"
                if name in timestamps
                else "integer"
                if name in integers
                else "numeric"
                if name in amounts
                else "text"
            )
            assert nullable == ("YES" if name == "ledger_movement_id" else "NO")
            if name in amounts:
                assert (precision, scale) == (20, 8)
            assert default == ("gen_random_uuid()" if name == primary_key else None)
        pk = connection.execute(
            """
            select a.attname from pg_constraint c
            join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
            where c.conrelid = %s::regclass and c.contype = 'p'
            """,
            (f"public.{table}",),
        ).fetchall()
        assert pk == [(primary_key,)]


@pytest.mark.parametrize("table", TABLES)
def test_foreign_keys_use_exact_authorities_and_restrict_deletion(
    database_url: str, table: str
) -> None:
    expected = (
        {
            "op_pc_era_simulation_fkey": ("simulation_runs", ["simulation_id"], ["id"]),
            "op_pc_era_designated_by_fkey": ("auth.users", ["designated_by"], ["id"]),
        }
        if table == ERAS
        else {
            "op_ps_settlement_era_identity_fkey": (
                ERAS,
                ["era_id", "simulation_id", "era_checksum"],
                ["era_id", "simulation_id", "era_checksum"],
            ),
            "op_ps_settlement_simulation_fkey": ("simulation_runs", ["simulation_id"], ["id"]),
            "op_ps_settlement_epoch_identity_fkey": (
                "operational_paper_session_run_epochs",
                ["epoch_id", "epoch_checksum"],
                ["epoch_id", "epoch_checksum"],
            ),
            "op_ps_settlement_authorization_fkey": (
                "operational_paper_capital_authorizations",
                ["authorization_id"],
                ["authorization_id"],
            ),
            "op_ps_settlement_movement_fkey": ("capital_movements", ["ledger_movement_id"], ["id"]),
            "op_ps_settlement_settled_by_fkey": ("auth.users", ["settled_by"], ["id"]),
        }
    )
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """
            select c.conname, c.confrelid::regclass::text, c.confdeltype,
                array(select a.attname::text from unnest(c.conkey) with ordinality k(num, ord)
                      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.num
                      order by k.ord),
                array(select a.attname::text from unnest(c.confkey) with ordinality k(num, ord)
                      join pg_attribute a on a.attrelid = c.confrelid and a.attnum = k.num
                      order by k.ord)
            from pg_constraint c where c.conrelid = %s::regclass and c.contype = 'f'
            """,
            (f"public.{table}",),
        ).fetchall()
        assert {
            name: (target, local, remote) for name, target, _, local, remote in rows
        } == expected
        assert all(delete == "r" for _, _, delete, _, _ in rows)


@pytest.mark.parametrize("table", TABLES)
def test_unique_constraints_and_supporting_indexes(database_url: str, table: str) -> None:
    expected = (
        {
            ("era_id",),
            ("simulation_id",),
            ("era_id", "simulation_id", "era_checksum"),
            ("designated_by", "designation_idempotency_key"),
        }
        if table == ERAS
        else {
            ("settlement_id",),
            ("session_id",),
            ("ledger_movement_id",),
            ("settled_by", "settle_idempotency_key"),
        }
    )
    indexes = (
        {"op_pc_era_designated_idx"}
        if table == ERAS
        else {
            "op_ps_settlement_settled_idx",
            "op_ps_settlement_era_history_idx",
            "op_ps_settlement_simulation_idx",
            "op_ps_settlement_epoch_idx",
            "op_ps_settlement_authorization_idx",
        }
    )
    with psycopg.connect(database_url) as connection:
        actual = connection.execute(
            """
            select array(select a.attname::text from unnest(c.conkey) with ordinality k(num, ord)
                         join pg_attribute a on a.attrelid=c.conrelid and a.attnum=k.num
                         order by k.ord)
            from pg_constraint c where c.conrelid=%s::regclass and c.contype in ('p', 'u')
            """,
            (f"public.{table}",),
        ).fetchall()
        assert {tuple(row[0]) for row in actual} == expected
        actual_indexes = connection.execute(
            "select indexname from pg_indexes where schemaname='public' and tablename=%s",
            (table,),
        ).fetchall()
        assert indexes <= {row[0] for row in actual_indexes}


@pytest.mark.parametrize("table", TABLES)
def test_rls_and_table_function_privileges_deny_data_api(database_url: str, table: str) -> None:
    function = "protect_op_pc_era" if table == ERAS else "protect_op_ps_settlement"
    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            "select relrowsecurity from pg_class where oid=%s::regclass", (f"public.{table}",)
        ).fetchone() == (True,)
        assert connection.execute(
            "select count(*) from pg_policies where schemaname='public' and tablename=%s", (table,)
        ).fetchone() == (0,)
        for role in ROLES:
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert connection.execute(
                    "select has_table_privilege(%s, %s, %s)", (role, f"public.{table}", privilege)
                ).fetchone() == (False,)
            assert connection.execute(
                "select has_function_privilege(%s, %s, 'EXECUTE')", (role, f"public.{function}()")
            ).fetchone() == (False,)
            with connection.transaction():
                connection.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute(
                            sql.SQL("select * from public.{}").format(sql.Identifier(table))
                        )
        proc = connection.execute(
            "select proconfig, prosecdef from pg_proc where oid=%s::regprocedure",
            (f"public.{function}()",),
        ).fetchone()
        assert proc == (['search_path=""'], False)


def test_valid_era_roundtrips_without_writing_ledger(
    database_url: str, era_row: dict[str, object]
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("set time zone 'UTC'")
        before = connection.execute("select * from public.capital_movements order by id").fetchall()
        _insert(connection, ERAS, era_row)
        with connection.cursor(row_factory=dict_row) as cursor:
            stored = cursor.execute(
                "select * from public.operational_paper_capital_eras"
            ).fetchone()
        assert stored == era_row
        assert stored is not None
        assert OperationalPaperCapitalEra(**stored)
        assert (
            connection.execute("select * from public.capital_movements order by id").fetchall()
            == before
        )


@pytest.mark.parametrize("delta", (Decimal("0"), Decimal("10"), Decimal("-10")))
def test_valid_zero_profit_loss_roundtrip_without_automatic_posting_or_consumption(
    database_url: str,
    settlement_seed: SettlementSeed,
    delta: Decimal,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("set time zone 'UTC'")
        movement = None if delta == 0 else _movement(connection, settlement_seed, delta)
        row = _settlement_row(settlement_seed, delta=delta, movement_id=movement)
        before = connection.execute("select * from public.capital_movements order by id").fetchall()
        _insert(connection, SETTLEMENTS, row)
        with connection.cursor(row_factory=dict_row) as cursor:
            stored = cursor.execute(
                "select * from public.operational_paper_session_settlements"
            ).fetchone()
        assert stored == row
        assert stored is not None
        spec = OperationalPaperSessionSettlementSpecification(
            **{
                field.name: stored[field.name]
                for field in fields(OperationalPaperSessionSettlementSpecification)
            }
        )
        assert (
            operational_paper_session_settlement_specification_checksum(spec)
            == stored["settlement_checksum"]
        )
        assert spec.settlement_delta == delta and spec.total_fees == Decimal("2")
        assert (
            connection.execute("select * from public.capital_movements order by id").fetchall()
            == before
        )
        assert connection.execute(
            "select state from public.operational_paper_capital_authorizations "
            "where authorization_id=%s",
            (settlement_seed.epoch.authorization_binding.authorization_id,),
        ).fetchone() == ("AUTHORIZED",)


def test_duplicate_era_simulation_and_primary_key_rejected(
    database_url: str, era_row: dict[str, object]
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        _insert(connection, ERAS, era_row)
        _assert_insert_error(
            connection,
            ERAS,
            {**era_row, "era_id": uuid4(), "designation_idempotency_key": "different"},
            psycopg.errors.UniqueViolation,
            "op_pc_era_simulation_key",
        )
        _assert_insert_error(
            connection, ERAS, era_row, psycopg.errors.UniqueViolation, f"{ERAS}_pkey"
        )


@pytest.mark.parametrize("same_actor", (True, False))
def test_era_idempotency_is_actor_scoped(
    database_url: str, era_row: dict[str, object], same_actor: bool
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        _insert(connection, ERAS, era_row)
        connection.execute(
            "update public.simulation_runs set status='COMPLETED', ended_at=started_at where id=%s",
            (era_row["simulation_id"],),
        )
        actor = era_row["designated_by"]
        assert isinstance(actor, UUID)
        if not same_actor:
            actor = uuid4()
            add_auth_user(connection, actor)
        simulation = _seed_simulation(connection, actor)
        second = _era_row(connection, simulation, actor)
        second["designation_idempotency_key"] = era_row["designation_idempotency_key"]
        if same_actor:
            _assert_insert_error(
                connection,
                ERAS,
                second,
                psycopg.errors.UniqueViolation,
                "op_pc_era_actor_idempotency_key",
            )
        else:
            _insert(connection, ERAS, second)


def test_settlement_unique_session_and_primary_key(
    database_url: str, settlement_seed: SettlementSeed
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        row = _settlement_row(settlement_seed)
        _insert(connection, SETTLEMENTS, row)
        duplicate = _settlement_row(settlement_seed)
        _assert_insert_error(
            connection,
            SETTLEMENTS,
            duplicate,
            psycopg.errors.UniqueViolation,
            "op_ps_settlement_session_key",
        )
        _assert_insert_error(
            connection, SETTLEMENTS, row, psycopg.errors.UniqueViolation, f"{SETTLEMENTS}_pkey"
        )


@pytest.mark.parametrize("same_actor", (True, False))
def test_settlement_idempotency_is_actor_scoped(
    database_url: str, settlement_seed: SettlementSeed, same_actor: bool
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        row = _settlement_row(settlement_seed)
        _insert(connection, SETTLEMENTS, row)
        # Isolate uniqueness from later Gate 2C session/epoch semantic resolution.
        second = {
            **row,
            "settlement_id": uuid4(),
            "session_id": hashlib.sha256(b"other session").hexdigest(),
        }
        if same_actor:
            _assert_insert_error(
                connection,
                SETTLEMENTS,
                second,
                psycopg.errors.UniqueViolation,
                "op_ps_settlement_actor_idempotency_key",
            )
        else:
            actor = uuid4()
            add_auth_user(connection, actor)
            _insert(connection, SETTLEMENTS, {**second, "settled_by": actor})


def test_optional_movement_is_unique_while_multiple_nulls_are_allowed(
    database_url: str, settlement_seed: SettlementSeed
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        delta = Decimal("10")
        movement = _movement(connection, settlement_seed, delta)
        row = _settlement_row(settlement_seed, delta=delta, movement_id=movement)
        _insert(connection, SETTLEMENTS, row)
        second = {
            **_settlement_row(settlement_seed, delta=delta, movement_id=movement),
            "session_id": "1" * 64,
        }
        _assert_insert_error(
            connection,
            SETTLEMENTS,
            second,
            psycopg.errors.UniqueViolation,
            "op_ps_settlement_movement_key",
        )
        for session in ("2" * 64, "3" * 64):
            _insert(
                connection, SETTLEMENTS, {**_settlement_row(settlement_seed), "session_id": session}
            )


@pytest.mark.parametrize(
    "field", ("base_quantity", "average_entry_price", "cost_basis", "unrealized_pnl")
)
def test_flatness_is_enforced_by_postgresql(
    database_url: str, settlement_seed: SettlementSeed, field: str
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        for value in (Decimal("0.00000001"), Decimal("-0.00000001")):
            _assert_insert_error(
                connection,
                SETTLEMENTS,
                {**_settlement_row(settlement_seed), field: value},
                psycopg.errors.CheckViolation,
                "op_ps_settlement_flat_check",
            )


@pytest.mark.parametrize(
    "changes",
    (
        {"initial_capital": Decimal("41")},
        {"final_quote_cash": Decimal("41")},
        {"realized_pnl": Decimal("1")},
        {"settlement_delta": Decimal("1")},
        {"settlement_delta": Decimal("1"), "realized_pnl": Decimal("1")},
    ),
)
def test_both_arithmetic_equalities_are_enforced(
    database_url: str, settlement_seed: SettlementSeed, changes: dict[str, object]
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        _assert_insert_error(
            connection,
            SETTLEMENTS,
            {**_settlement_row(settlement_seed), **changes},
            psycopg.errors.CheckViolation,
            "op_ps_settlement_arithmetic_check",
        )


def test_zero_and_nonzero_movement_shape_is_enforced(
    database_url: str, settlement_seed: SettlementSeed
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        movement = _movement(connection, settlement_seed, Decimal("1"))
        _assert_insert_error(
            connection,
            SETTLEMENTS,
            {**_settlement_row(settlement_seed), "ledger_movement_id": movement},
            psycopg.errors.CheckViolation,
            "op_ps_settlement_movement_shape_check",
        )
        for delta in (Decimal("1"), Decimal("-1")):
            row = _settlement_row(settlement_seed, delta=delta, movement_id=movement)
            _assert_insert_error(
                connection,
                SETTLEMENTS,
                {**row, "ledger_movement_id": None},
                psycopg.errors.CheckViolation,
                "op_ps_settlement_movement_shape_check",
            )


@pytest.mark.parametrize("field", AMOUNTS)
def test_all_financial_columns_reject_nonfinite_values_and_overflow(
    database_url: str, settlement_seed: SettlementSeed, field: str
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        for value in ("NaN", "Infinity", "-Infinity", "1000000000000", "-1000000000000"):
            _assert_insert_error(
                connection,
                SETTLEMENTS,
                {**_settlement_row(settlement_seed), field: Decimal(value)},
                psycopg.errors.DataError if value != "NaN" else psycopg.errors.CheckViolation,
            )


@pytest.mark.parametrize(
    "field", ("initial_capital", "final_quote_cash", "total_fees", "total_slippage_cost")
)
def test_negative_capital_and_audit_costs_are_rejected(
    database_url: str, settlement_seed: SettlementSeed, field: str
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        _assert_insert_error(
            connection,
            SETTLEMENTS,
            {**_settlement_row(settlement_seed), field: Decimal("-1")},
            psycopg.errors.CheckViolation,
        )
        if field == "initial_capital":
            _assert_insert_error(
                connection,
                SETTLEMENTS,
                {**_settlement_row(settlement_seed), field: Decimal("0")},
                psycopg.errors.CheckViolation,
            )


@pytest.mark.parametrize("field", SETTLEMENT_HASHES)
def test_settlement_hash_columns_require_lowercase_sha256(
    database_url: str, settlement_seed: SettlementSeed, field: str
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        for value in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n"):
            _assert_insert_error(
                connection,
                SETTLEMENTS,
                {**_settlement_row(settlement_seed), field: value},
                psycopg.errors.CheckViolation,
                "op_ps_settlement_hashes_check",
            )


@pytest.mark.parametrize(
    "field",
    (
        "settlement_id",
        "era_id",
        "simulation_id",
        "epoch_id",
        "authorization_id",
        "settled_by",
        "ledger_movement_id",
    ),
)
def test_settlement_uuids_reject_zero(
    database_url: str, settlement_seed: SettlementSeed, field: str
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        row = _settlement_row(settlement_seed)
        if field == "ledger_movement_id":
            row = _settlement_row(settlement_seed, delta=Decimal("1"), movement_id=uuid4())
        _assert_insert_error(
            connection,
            SETTLEMENTS,
            {**row, field: UUID(int=0)},
            psycopg.errors.CheckViolation,
            "op_ps_settlement_nonzero_uuids_check",
        )


@pytest.mark.parametrize(
    "field",
    (
        "era_id",
        "simulation_id",
        "epoch_id",
        "authorization_id",
        "settled_by",
        "ledger_movement_id",
        "era_checksum",
        "epoch_checksum",
    ),
)
def test_settlement_rejects_missing_authorities_or_composite_identity_mismatch(
    database_url: str, settlement_seed: SettlementSeed, field: str
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        row = _settlement_row(settlement_seed)
        if field == "ledger_movement_id":
            row = _settlement_row(settlement_seed, delta=Decimal("1"), movement_id=uuid4())
        row[field] = "0" * 64 if field.endswith("checksum") else uuid4()
        _assert_insert_error(connection, SETTLEMENTS, row, psycopg.errors.ForeignKeyViolation)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("operation", ("update", "delete"))
def test_evidence_tables_reject_owner_update_and_delete(
    database_url: str, settlement_seed: SettlementSeed, table: str, operation: str
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        if table == SETTLEMENTS:
            _insert(connection, table, _settlement_row(settlement_seed))
        query = sql.SQL(
            "update public.{} set schema_version = schema_version"
            if operation == "update"
            else "delete from public.{}"
        ).format(sql.Identifier(table))
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(query)
        assert connection.execute(
            sql.SQL("select count(*) from public.{}").format(sql.Identifier(table))
        ).fetchone() == (1,)


def test_era_constraints_reject_invalid_metadata(
    database_url: str, era_row: dict[str, object]
) -> None:
    invalid: list[dict[str, object]] = [
        {"schema_version": 0},
        {"schema_version": 2},
        {"designation_contract_version": 2},
        {"initial_capital": Decimal("0")},
        {"initial_capital": Decimal("-1")},
        {"initial_capital": Decimal("NaN")},
        {"currency": "usdt"},
        {"currency": "US DT"},
        {"currency": "A" * 33},
        {"era_checksum": "A" * 64},
        {"designation_intent_fingerprint": "a" * 63},
        {"designated_at": "infinity"},
        {"simulation_started_at": "-infinity"},
    ]
    for field in ("era_id", "simulation_id", "designated_by"):
        invalid.append({field: UUID(int=0)})
    for key in ("", "a" * 129, "has space", "a/b", "a\n"):
        invalid.append({"designation_idempotency_key": key})
    started = era_row["simulation_started_at"]
    assert isinstance(started, datetime)
    invalid.append({"designated_at": started - timedelta(microseconds=1)})
    with psycopg.connect(database_url, autocommit=True) as connection:
        for changes in invalid:
            _assert_insert_error(
                connection, ERAS, {**era_row, **changes}, psycopg.errors.CheckViolation
            )
        for field in ("simulation_id", "designated_by"):
            _assert_insert_error(
                connection, ERAS, {**era_row, field: uuid4()}, psycopg.errors.ForeignKeyViolation
            )
        for value in (Decimal("Infinity"), Decimal("-Infinity"), Decimal("1000000000000")):
            _assert_insert_error(
                connection,
                ERAS,
                {**era_row, "initial_capital": value},
                psycopg.errors.NumericValueOutOfRange,
            )


def test_settlement_versions_tokens_and_chronology(
    database_url: str, settlement_seed: SettlementSeed
) -> None:
    row = _settlement_row(settlement_seed)
    terminal = row["epoch_terminal_at"]
    assert isinstance(terminal, datetime)
    invalid: list[dict[str, object]] = [
        {"schema_version": 0},
        {"schema_version": 2},
        {"settlement_contract_version": 0},
        {"settlement_contract_version": 2},
        {"epoch_terminal_at": "infinity"},
        {"settled_at": "-infinity"},
        {"settled_at": terminal - timedelta(microseconds=1)},
    ]
    for key in ("", "a" * 129, "has space", "_bad", "a\n"):
        invalid.append({"settle_idempotency_key": key})
    with psycopg.connect(database_url, autocommit=True) as connection:
        for changes in invalid:
            _assert_insert_error(
                connection, SETTLEMENTS, {**row, **changes}, psycopg.errors.CheckViolation
            )
        _insert(connection, SETTLEMENTS, {**row, "settle_idempotency_key": "a" * 128})
