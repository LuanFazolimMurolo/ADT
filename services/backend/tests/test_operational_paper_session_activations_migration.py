"""PostgreSQL contract tests for Phase 7-10 activation authority."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.database import Database
from app.operational_paper_session_activations import (
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationCreateIntent,
    authorize_operational_paper_session_activation,
    build_operational_paper_session_activation_specification,
    operational_paper_session_activation_create_intent_fingerprint,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from tests.conftest import add_auth_user
from tests.test_operational_paper_session_materializations_repository import (
    PREPARED_AT,
    _plan_context,
)

MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "supabase/migrations/20260903000000_phase_7_10_operational_paper_session_activations.sql"
)
AUTHORIZED_AT = PREPARED_AT + timedelta(minutes=2)


async def _valid_activation(
    database_url: str,
    database: Database,
    actor_id: UUID,
    *,
    key: str = "activation:valid",
) -> OperationalPaperSessionActivation:
    plan = await _plan_context(database_url, database, actor_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(plan, actor_id=actor_id, now=PREPARED_AT)
    materialized = await repository.mark_materialized(
        prepared.materialization_id,
        expected_record_version=1,
        actor_id=actor_id,
        now=AUTHORIZED_AT - timedelta(minutes=1),
    )
    specification = build_operational_paper_session_activation_specification(materialized)
    intent = OperationalPaperSessionActivationCreateIntent(
        materialization_id=specification.materialization_id,
        materialization_checksum=specification.materialization_checksum,
    )
    return authorize_operational_paper_session_activation(
        activation_id=uuid4(),
        specification=specification,
        authorized_by=actor_id,
        authorized_at=AUTHORIZED_AT,
        create_idempotency_key=key,
        create_intent_fingerprint=(
            operational_paper_session_activation_create_intent_fingerprint(intent)
        ),
    )


def _row(activation: OperationalPaperSessionActivation) -> dict[str, object]:
    return {
        "activation_id": activation.activation_id,
        "schema_version": activation.schema_version,
        "activation_contract_version": activation.activation_contract_version,
        "state": activation.state.value,
        "record_version": activation.record_version,
        "materialization_id": activation.materialization_id,
        "materialization_checksum": activation.materialization_checksum,
        "authorization_id": activation.authorization_binding.authorization_id,
        "authorization_checksum": activation.authorization_binding.authorization_checksum,
        "profile_id": activation.profile_binding.profile_id,
        "profile_approved_revision": activation.profile_binding.approved_revision,
        "profile_specification_checksum": (activation.profile_binding.specification_checksum),
        "mandate_id": activation.mandate_binding.mandate_id,
        "mandate_approved_revision": activation.mandate_binding.approved_revision,
        "mandate_specification_checksum": (activation.mandate_binding.specification_checksum),
        "simulation_id": activation.simulation_id,
        "session_id": activation.session_id,
        "config_checksum": activation.config_checksum,
        "activation_checksum": activation.activation_checksum,
        "authorized_by": activation.authorized_by,
        "authorized_at": activation.authorized_at,
        "revoked_by": activation.revoked_by,
        "revoked_at": activation.revoked_at,
        "create_idempotency_key": activation.create_idempotency_key,
        "create_intent_fingerprint": activation.create_intent_fingerprint,
    }


def _insert(connection: psycopg.Connection[object], row: dict[str, object]) -> UUID:
    columns = tuple(row)
    placeholders = ", ".join(["%s"] * len(columns))
    names = ", ".join(columns)
    result = connection.execute(
        f"""
        insert into public.operational_paper_session_activations ({names})
        values ({placeholders})
        returning activation_id
        """,  # noqa: S608 - column names are a closed test-owned mapping.
        tuple(row[column] for column in columns),
    ).fetchone()
    assert result is not None
    return result[0]  # type: ignore[no-any-return]


def _assert_insert_error(
    database_url: str,
    row: dict[str, object],
    message: str | None = None,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error) as caught:
            _insert(connection, row)
    if message is not None:
        assert caught.value.diag.message_primary == message


def _force_update(
    database_url: str,
    table: str,
    identifier: str,
    identifier_value: object,
    assignments: dict[str, object],
) -> None:
    clauses = ", ".join(f"{column} = %s" for column in assignments)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("set session_replication_role = replica")
        connection.execute(
            f"update public.{table} set {clauses} where {identifier} = %s",  # noqa: S608
            (*assignments.values(), identifier_value),
        )
        connection.execute("set session_replication_role = origin")


def test_schema_columns_foreign_keys_indexes_and_rls(database_url: str) -> None:
    expected_columns = {
        "activation_id": "uuid",
        "schema_version": "integer",
        "activation_contract_version": "integer",
        "state": "text",
        "record_version": "bigint",
        "materialization_id": "uuid",
        "materialization_checksum": "text",
        "authorization_id": "uuid",
        "authorization_checksum": "text",
        "profile_id": "uuid",
        "profile_approved_revision": "bigint",
        "profile_specification_checksum": "text",
        "mandate_id": "uuid",
        "mandate_approved_revision": "bigint",
        "mandate_specification_checksum": "text",
        "simulation_id": "uuid",
        "session_id": "text",
        "config_checksum": "text",
        "activation_checksum": "text",
        "authorized_by": "uuid",
        "authorized_at": "timestamp with time zone",
        "revoked_by": "uuid",
        "revoked_at": "timestamp with time zone",
        "create_idempotency_key": "text",
        "create_intent_fingerprint": "text",
    }
    expected_fks = {
        "op_ps_activation_materialization_fkey": (
            "FOREIGN KEY (materialization_id) REFERENCES "
            "operational_paper_session_materializations(materialization_id) "
            "ON DELETE RESTRICT"
        ),
        "op_ps_activation_authorization_fkey": (
            "FOREIGN KEY (authorization_id) REFERENCES "
            "operational_paper_capital_authorizations(authorization_id) "
            "ON DELETE RESTRICT"
        ),
        "op_ps_activation_profile_revision_fkey": (
            "FOREIGN KEY (profile_id, profile_approved_revision, "
            "profile_specification_checksum) REFERENCES "
            "operational_paper_session_profile_revisions(profile_id, revision, "
            "specification_checksum) ON DELETE RESTRICT"
        ),
        "op_ps_activation_mandate_revision_fkey": (
            "FOREIGN KEY (mandate_id, mandate_approved_revision, "
            "mandate_specification_checksum) REFERENCES "
            "operational_mandate_revisions(mandate_id, revision, "
            "specification_checksum) ON DELETE RESTRICT"
        ),
        "op_ps_activation_simulation_fkey": (
            "FOREIGN KEY (simulation_id) REFERENCES simulation_runs(id) ON DELETE RESTRICT"
        ),
        "op_ps_activation_authorized_by_fkey": (
            "FOREIGN KEY (authorized_by) REFERENCES auth.users(id) ON DELETE RESTRICT"
        ),
        "op_ps_activation_revoked_by_fkey": (
            "FOREIGN KEY (revoked_by) REFERENCES auth.users(id) ON DELETE RESTRICT"
        ),
    }
    with psycopg.connect(database_url) as connection:
        columns = dict(
            connection.execute(
                """
                select column_name, data_type
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'operational_paper_session_activations'
                """
            ).fetchall()
        )
        constraints = dict(
            connection.execute(
                """
                select conname, pg_get_constraintdef(oid)
                from pg_constraint
                where conrelid = 'public.operational_paper_session_activations'::regclass
                  and contype = 'f'
                """
            ).fetchall()
        )
        indexes = dict(
            connection.execute(
                """
                select indexname, indexdef
                from pg_indexes
                where schemaname = 'public'
                  and tablename = 'operational_paper_session_activations'
                """
            ).fetchall()
        )
        rls = connection.execute(
            """
            select relrowsecurity
            from pg_class
            where oid = 'public.operational_paper_session_activations'::regclass
            """
        ).fetchone()
        policies = connection.execute(
            """
            select count(*) from pg_policy
            where polrelid = 'public.operational_paper_session_activations'::regclass
            """
        ).fetchone()

    assert columns == expected_columns
    assert constraints == expected_fks
    actor_unique = indexes["op_ps_activation_actor_idempotency_key"]
    assert "UNIQUE" in actor_unique
    assert "authorized_by, create_idempotency_key" in actor_unique
    current_unique = indexes["op_ps_activation_one_authorized_per_materialization_uidx"]
    assert "UNIQUE" in current_unique
    assert "(materialization_id)" in current_unique
    assert "WHERE (state = 'AUTHORIZED'::text)" in current_unique
    assert rls == (True,)
    assert policies == (0,)


def test_static_lock_order_and_python_checksum_authority() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    validator = sql.split(
        "create function public.validate_operational_paper_session_activation_insert()",
        1,
    )[1].split("create trigger operational_paper_session_activations_validate_insert", 1)[0]
    protection = sql.split(
        "create function public.protect_operational_paper_session_activation()", 1
    )[1].split("create trigger operational_paper_session_activations_protect", 1)[0]
    locked_tables = re.findall(r"from public\.([a-z_]+).*?for update;", validator, re.DOTALL)

    assert locked_tables == [
        "simulation_runs",
        "operational_paper_capital_authorizations",
        "operational_paper_session_profiles",
        "operational_mandates",
        "operational_paper_session_materializations",
    ]
    assert "from public.operational_paper_session_activations" not in validator
    assert "for update" not in protection.lower()
    lowered = sql.lower()
    assert all(
        token not in lowered for token in ("digest(", "json_build", "jsonb_build", "encode(")
    )


@pytest.mark.asyncio
async def test_valid_insert_current_uniqueness_and_historical_reauthorization(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    first = await _valid_activation(database_url, database, auth_user_id)
    first_row = _row(first)
    with psycopg.connect(database_url, autocommit=True) as connection:
        _insert(connection, first_row)

    simultaneous = first_row | {
        "activation_id": uuid4(),
        "create_idempotency_key": "activation:simultaneous",
    }
    _assert_insert_error(database_url, simultaneous)

    second_actor = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, second_actor)
        connection.execute(
            """
            update public.operational_paper_session_activations
            set state = 'REVOKED', record_version = 2,
                revoked_by = %s, revoked_at = %s
            where activation_id = %s
            """,
            (auth_user_id, AUTHORIZED_AT + timedelta(seconds=1), first.activation_id),
        )
        second = first_row | {
            "activation_id": uuid4(),
            "authorized_by": second_actor,
            "authorized_at": AUTHORIZED_AT + timedelta(seconds=2),
            "create_idempotency_key": first.create_idempotency_key,
        }
        _insert(connection, second)
        states = connection.execute(
            """
            select state, record_version from public.operational_paper_session_activations
            where materialization_id = %s order by authorized_at
            """,
            (first.materialization_id,),
        ).fetchall()

    assert states == [("REVOKED", 2), ("AUTHORIZED", 1)]


@pytest.mark.asyncio
async def test_checks_initial_shape_and_nonzero_uuids(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    activation = await _valid_activation(database_url, database, auth_user_id)
    valid = _row(activation)
    bad_values: list[tuple[str, object]] = [
        ("schema_version", 2),
        ("activation_contract_version", 2),
        ("state", "DRAFT"),
        ("record_version", 2),
        ("materialization_checksum", "A" * 64),
        ("authorization_checksum", "x" * 64),
        ("profile_specification_checksum", "short"),
        ("mandate_specification_checksum", "0" * 63),
        ("session_id", "not-sha"),
        ("config_checksum", "f" * 65),
        ("activation_checksum", "G" * 64),
        ("create_intent_fingerprint", "bad"),
        ("profile_approved_revision", 0),
        ("mandate_approved_revision", 0),
        ("create_idempotency_key", " bad"),
        ("create_idempotency_key", "a" * 129),
        ("authorized_at", "infinity"),
    ]
    for field, value in bad_values:
        _assert_insert_error(database_url, valid | {field: value, "activation_id": uuid4()})

    for field in (
        "activation_id",
        "materialization_id",
        "authorization_id",
        "profile_id",
        "mandate_id",
        "simulation_id",
        "authorized_by",
    ):
        _assert_insert_error(database_url, valid | {field: UUID(int=0)})

    for changes in (
        {"state": "REVOKED", "record_version": 2},
        {"record_version": 3},
        {"revoked_by": auth_user_id, "revoked_at": AUTHORIZED_AT},
    ):
        _assert_insert_error(
            database_url,
            valid | changes | {"activation_id": uuid4()},
            "operational_paper_session_activation_initial_state_invalid",
        )


@pytest.mark.asyncio
async def test_insert_validator_rejects_all_upstream_binding_and_chronology_failures(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    activation = await _valid_activation(database_url, database, auth_user_id)
    valid = _row(activation)

    cases = [
        ({"simulation_id": uuid4()}, "simulation_missing"),
        ({"authorization_id": uuid4()}, "authorization_missing"),
        ({"authorization_checksum": "1" * 64}, "authorization_checksum_mismatch"),
        ({"profile_id": uuid4()}, "authorization_profile_binding_mismatch"),
        ({"materialization_id": uuid4()}, "materialization_missing"),
        ({"materialization_checksum": "2" * 64}, "materialization_checksum_mismatch"),
        ({"session_id": "3" * 64}, "materialization_config_binding_mismatch"),
        ({"config_checksum": "4" * 64}, "materialization_config_binding_mismatch"),
        ({"authorized_at": PREPARED_AT}, "authorized_at_invalid"),
    ]
    for changes, suffix in cases:
        _assert_insert_error(
            database_url,
            valid | changes | {"activation_id": uuid4(), "create_idempotency_key": uuid4().hex},
            f"operational_paper_session_activation_{suffix}",
        )

    authorization_id = activation.authorization_binding.authorization_id
    other_simulation_id = uuid4()
    authorization_binding_cases = [
        (
            {"simulation_id": other_simulation_id},
            {},
            {"simulation_id": valid["simulation_id"]},
            "authorization_simulation_mismatch",
        ),
        (
            {"profile_id": uuid4()},
            None,
            {"profile_id": valid["profile_id"]},
            "profile_missing",
        ),
        (
            {"profile_approved_revision": valid["profile_approved_revision"] + 1},  # type: ignore[operator]
            None,
            {"profile_approved_revision": valid["profile_approved_revision"]},
            "profile_binding_mismatch",
        ),
        (
            {"profile_specification_checksum": "9" * 64},
            None,
            {"profile_specification_checksum": valid["profile_specification_checksum"]},
            "profile_binding_mismatch",
        ),
    ]
    for stored_changes, candidate_changes, restore, suffix in authorization_binding_cases:
        if candidate_changes is None:
            candidate_changes = stored_changes
        _force_update(
            database_url,
            "operational_paper_capital_authorizations",
            "authorization_id",
            authorization_id,
            stored_changes,
        )
        _assert_insert_error(
            database_url,
            valid
            | candidate_changes
            | {"activation_id": uuid4(), "create_idempotency_key": uuid4().hex},
            f"operational_paper_session_activation_{suffix}",
        )
        _force_update(
            database_url,
            "operational_paper_capital_authorizations",
            "authorization_id",
            authorization_id,
            restore,
        )

    for candidate_changes, suffix in (
        ({"mandate_id": uuid4()}, "mandate_missing"),
        (
            {"mandate_approved_revision": valid["mandate_approved_revision"] + 1},  # type: ignore[operator]
            "mandate_binding_mismatch",
        ),
        (
            {"mandate_specification_checksum": "8" * 64},
            "mandate_binding_mismatch",
        ),
    ):
        _assert_insert_error(
            database_url,
            valid
            | candidate_changes
            | {"activation_id": uuid4(), "create_idempotency_key": uuid4().hex},
            f"operational_paper_session_activation_{suffix}",
        )

    mutations = [
        (
            "simulation_runs",
            "id",
            activation.simulation_id,
            {"status": "COMPLETED", "ended_at": AUTHORIZED_AT},
            "simulation_not_active",
        ),
        (
            "operational_paper_capital_authorizations",
            "authorization_id",
            activation.authorization_binding.authorization_id,
            {
                "state": "REVOKED",
                "record_version": 2,
                "revoked_by": auth_user_id,
                "revoked_at": AUTHORIZED_AT,
            },
            "authorization_not_authorized",
        ),
        (
            "operational_paper_session_profiles",
            "profile_id",
            activation.profile_binding.profile_id,
            {
                "state": "ARCHIVED",
                "record_version": 3,
                "archived_by": auth_user_id,
                "archived_at": AUTHORIZED_AT,
            },
            "profile_not_approved",
        ),
        (
            "operational_mandates",
            "mandate_id",
            activation.mandate_binding.mandate_id,
            {
                "state": "ARCHIVED",
                "record_version": 3,
                "archived_by": auth_user_id,
                "archived_at": AUTHORIZED_AT,
            },
            "mandate_not_approved",
        ),
        (
            "operational_paper_session_materializations",
            "materialization_id",
            activation.materialization_id,
            {
                "state": "PREPARED",
                "record_version": 1,
                "materialized_by": None,
                "materialized_at": None,
            },
            "materialization_not_materialized",
        ),
    ]
    for table, identifier, value, assignment, suffix in mutations:
        original: dict[str, object]
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            original = dict(
                connection.execute(
                    f"select {', '.join(assignment)} from public.{table} where {identifier} = %s",  # noqa: S608
                    (value,),
                ).fetchone()
                or {}
            )
        _force_update(database_url, table, identifier, value, assignment)
        _assert_insert_error(
            database_url,
            valid | {"activation_id": uuid4(), "create_idempotency_key": uuid4().hex},
            f"operational_paper_session_activation_{suffix}",
        )
        _force_update(database_url, table, identifier, value, original)

    chronology_sources = [
        (
            "operational_paper_capital_authorizations",
            "authorization_id",
            activation.authorization_binding.authorization_id,
            "created_at",
        ),
        (
            "operational_paper_session_profiles",
            "profile_id",
            activation.profile_binding.profile_id,
            "approved_at",
        ),
        (
            "operational_mandates",
            "mandate_id",
            activation.mandate_binding.mandate_id,
            "approved_at",
        ),
        (
            "operational_paper_session_materializations",
            "materialization_id",
            activation.materialization_id,
            "materialized_at",
        ),
    ]
    for table, identifier, value, timestamp_column in chronology_sources:
        with psycopg.connect(database_url) as connection:
            stored = connection.execute(
                f"select {timestamp_column} from public.{table} where {identifier} = %s",  # noqa: S608
                (value,),
            ).fetchone()
        assert stored is not None
        _force_update(
            database_url,
            table,
            identifier,
            value,
            {timestamp_column: AUTHORIZED_AT + timedelta(seconds=1)},
        )
        _assert_insert_error(
            database_url,
            valid | {"activation_id": uuid4(), "create_idempotency_key": uuid4().hex},
            "operational_paper_session_activation_authorized_at_invalid",
        )
        _force_update(
            database_url,
            table,
            identifier,
            value,
            {timestamp_column: stored[0]},
        )

    stored_mismatch_cases = [
        ("authorization_id", uuid4(), "materialization_authorization_binding_mismatch"),
        ("profile_id", uuid4(), "materialization_profile_binding_mismatch"),
        ("mandate_id", uuid4(), "materialization_mandate_binding_mismatch"),
        ("simulation_id", uuid4(), "materialization_simulation_mismatch"),
    ]
    for field, value, suffix in stored_mismatch_cases:
        _force_update(
            database_url,
            "operational_paper_session_materializations",
            "materialization_id",
            activation.materialization_id,
            {field: value},
        )
        _assert_insert_error(
            database_url,
            valid | {"activation_id": uuid4(), "create_idempotency_key": uuid4().hex},
            f"operational_paper_session_activation_{suffix}",
        )
        _force_update(
            database_url,
            "operational_paper_session_materializations",
            "materialization_id",
            activation.materialization_id,
            {field: valid[field]},
        )


@pytest.mark.asyncio
async def test_profile_mandate_and_currency_coherence_fail_closed(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    activation = await _valid_activation(database_url, database, auth_user_id)
    valid = _row(activation)
    revision_key = activation.profile_binding.specification_checksum
    mutations = [
        (
            "operational_paper_session_profile_revisions",
            "specification_checksum",
            revision_key,
            {"mandate_id": uuid4()},
            "profile_mandate_binding_mismatch",
        ),
        (
            "operational_paper_session_profile_revisions",
            "specification_checksum",
            revision_key,
            {"quote_asset": "EUR"},
            "authorization_quote_asset_mismatch",
        ),
        (
            "simulation_runs",
            "id",
            activation.simulation_id,
            {"currency": "EUR"},
            "currency_mismatch",
        ),
    ]
    for table, identifier, value, assignment, suffix in mutations:
        column = next(iter(assignment))
        with psycopg.connect(database_url) as connection:
            original = connection.execute(
                f"select {column} from public.{table} where {identifier} = %s",  # noqa: S608
                (value,),
            ).fetchone()
        assert original is not None
        _force_update(database_url, table, identifier, value, assignment)
        _assert_insert_error(
            database_url,
            valid | {"activation_id": uuid4(), "create_idempotency_key": uuid4().hex},
            f"operational_paper_session_activation_{suffix}",
        )
        _force_update(database_url, table, identifier, value, {column: original[0]})


@pytest.mark.asyncio
async def test_update_delete_and_revocation_shape_are_protected(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    activation = await _valid_activation(database_url, database, auth_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        _insert(connection, _row(activation))
        with pytest.raises(psycopg.Error):
            connection.execute(
                "delete from public.operational_paper_session_activations where activation_id = %s",
                (activation.activation_id,),
            )
        for assignment in (
            "activation_checksum = repeat('0', 64)",
            "create_idempotency_key = 'changed'",
            "materialization_id = gen_random_uuid()",
            "authorized_by = gen_random_uuid()",
        ):
            with pytest.raises(psycopg.Error):
                connection.execute(
                    "update public.operational_paper_session_activations "
                    f"set {assignment}, record_version = 2 "  # noqa: S608
                    "where activation_id = %s",
                    (activation.activation_id,),
                )
        for assignment in (
            "state = 'REVOKED', record_version = 1, "
            "revoked_by = gen_random_uuid(), revoked_at = now()",
            "state = 'REVOKED', record_version = 3, "
            "revoked_by = gen_random_uuid(), revoked_at = now()",
            "state = 'REVOKED', record_version = 2",
            "state = 'REVOKED', record_version = 2, revoked_by = gen_random_uuid(), "
            "revoked_at = authorized_at - interval '1 second'",
            "state = 'REVOKED', record_version = 2, "
            "revoked_by = '00000000-0000-0000-0000-000000000000', revoked_at = now()",
            "state = 'REVOKED', record_version = 2, "
            "revoked_by = gen_random_uuid(), revoked_at = 'infinity'",
        ):
            with pytest.raises(psycopg.Error):
                connection.execute(
                    "update public.operational_paper_session_activations "
                    f"set {assignment} where activation_id = %s",  # noqa: S608
                    (activation.activation_id,),
                )
        connection.execute(
            """
            update public.operational_paper_session_activations
            set state = 'REVOKED', record_version = 2,
                revoked_by = %s, revoked_at = %s
            where activation_id = %s
            """,
            (auth_user_id, AUTHORIZED_AT + timedelta(seconds=1), activation.activation_id),
        )
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_session_activations
                set record_version = 3 where activation_id = %s
                """,
                (activation.activation_id,),
            )


def test_table_and_function_privileges_are_locked_down(database_url: str) -> None:
    roles = ("anon", "authenticated", "service_role")
    functions = (
        "public.validate_operational_paper_session_activation_insert()",
        "public.protect_operational_paper_session_activation()",
    )
    with psycopg.connect(database_url) as connection:
        public_table = connection.execute(
            """
            select exists (
                select 1 from information_schema.role_table_grants
                where table_schema = 'public'
                  and table_name = 'operational_paper_session_activations'
                  and grantee = 'PUBLIC'
            )
            """
        ).fetchone()
        assert public_table == (False,)
        for role in roles:
            assert connection.execute(
                """
                select has_table_privilege(
                    %s, 'public.operational_paper_session_activations',
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                )
                """,
                (role,),
            ).fetchone() == (False,)
        for function in functions:
            assert connection.execute(
                """
                select exists (
                    select 1 from pg_proc as procedure
                    cross join lateral aclexplode(
                        coalesce(procedure.proacl, acldefault('f', procedure.proowner))
                    ) as privilege
                    where procedure.oid = %s::regprocedure
                      and privilege.grantee = 0
                      and privilege.privilege_type = 'EXECUTE'
                )
                """,
                (function,),
            ).fetchone() == (False,)
            for role in roles:
                assert connection.execute(
                    "select has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, function),
                ).fetchone() == (False,)
