"""Phase 7-07 operational paper-session profile persistence invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from tests.postgres_support import add_auth_user

BASE_TIME = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
MANDATE_CHECKSUM_A = "a" * 64
MANDATE_CHECKSUM_B = "b" * 64
PROFILE_CHECKSUM_A = "c" * 64
PROFILE_CHECKSUM_B = "d" * 64
PARAMETERS_CHECKSUM = "e" * 64
SNAPSHOT_CHECKSUM = "f" * 64
FINGERPRINT = "9" * 64
POSTGRESQL_INTEGER_MAX = (1 << 31) - 1
POSTGRESQL_BIGINT_MAX = (1 << 63) - 1
CANONICAL_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w")

PROFILE_TABLE = "public.operational_paper_session_profiles"
REVISION_TABLE = "public.operational_paper_session_profile_revisions"
PROFILE_TABLES = (PROFILE_TABLE, REVISION_TABLE)
DATA_API_ROLES = ("anon", "authenticated", "service_role")
TRIGGER_FUNCTIONS = (
    "public.validate_operational_paper_session_profile_revision_insert()",
    "public.validate_operational_paper_session_profile_insert()",
    "public.ensure_operational_paper_session_profile_revision_published()",
    "public.protect_operational_paper_session_profile()",
    "public.reject_operational_paper_session_profile_revision_change()",
)


def _seed_strategy(
    connection: Connection[Any],
    actor_id: UUID,
    *,
    strategy_id: UUID | None = None,
) -> UUID:
    value = strategy_id or uuid4()
    connection.execute(
        """
        insert into public.strategy_definitions (
            id,
            display_name,
            plugin_name,
            plugin_version,
            plugin_schema_version,
            lifecycle_version,
            parameters,
            parameters_checksum,
            state,
            revision,
            created_by,
            updated_by,
            created_at,
            updated_at
        )
        values (
            %s, %s, 'ema-cross-example', '2', 1, 2,
            '{}'::jsonb, %s, 'ACTIVE', 1, %s, %s, %s, %s
        )
        """,
        (
            value,
            f"Strategy {value}",
            PARAMETERS_CHECKSUM,
            actor_id,
            actor_id,
            BASE_TIME,
            BASE_TIME,
        ),
    )
    return value


def _seed_approved_mandate(
    connection: Connection[Any],
    actor_id: UUID,
    *,
    mandate_id: UUID | None = None,
    two_revisions: bool = False,
) -> tuple[UUID, int, str]:
    value = mandate_id or uuid4()
    connection.execute(
        """
        insert into public.operational_mandate_revisions (
            mandate_id, revision, schema_version, specification_checksum,
            name, description, created_by, created_at
        )
        values (%s, 1, 1, %s, 'Mandate', '', %s, %s)
        """,
        (value, MANDATE_CHECKSUM_A, actor_id, BASE_TIME),
    )
    connection.execute(
        """
        insert into public.operational_mandate_revision_instruments (
            mandate_id, revision, exchange, market_type, base_asset, quote_asset
        )
        values (%s, 1, 'binance', 'spot', 'BTC', 'USDT')
        """,
        (value,),
    )
    connection.execute(
        """
        insert into public.operational_mandates (
            mandate_id, state, current_revision, record_version,
            created_by, created_at, create_idempotency_key,
            create_request_fingerprint
        )
        values (%s, 'DRAFT', 1, 1, %s, %s, %s, %s)
        """,
        (value, actor_id, BASE_TIME, f"mandate:{value}", FINGERPRINT),
    )

    revision = 1
    checksum = MANDATE_CHECKSUM_A
    if two_revisions:
        connection.execute(
            """
            insert into public.operational_mandate_revisions (
                mandate_id, revision, schema_version, specification_checksum,
                name, description, created_by, created_at
            )
            values (%s, 2, 1, %s, 'Mandate revised', '', %s, %s)
            """,
            (value, MANDATE_CHECKSUM_B, actor_id, BASE_TIME + timedelta(seconds=1)),
        )
        connection.execute(
            """
            insert into public.operational_mandate_revision_instruments (
                mandate_id, revision, exchange, market_type, base_asset, quote_asset
            )
            values (%s, 2, 'binance', 'spot', 'ETH', 'USDT')
            """,
            (value,),
        )
        connection.execute(
            """
            update public.operational_mandates
            set current_revision = 2, record_version = 2
            where mandate_id = %s
            """,
            (value,),
        )
        revision = 2
        checksum = MANDATE_CHECKSUM_B

    connection.execute(
        """
        update public.operational_mandates
        set state = 'APPROVED',
            record_version = record_version + 1,
            approved_revision = current_revision,
            approved_checksum = %s,
            approved_by = %s,
            approved_at = %s
        where mandate_id = %s
        """,
        (checksum, actor_id, BASE_TIME + timedelta(seconds=2), value),
    )
    return value, revision, checksum


def _revision_values(
    *,
    profile_id: UUID,
    actor_id: UUID,
    mandate_id: UUID,
    mandate_revision: int,
    mandate_checksum: str,
    strategy_id: UUID,
    revision: int = 1,
    checksum: str = PROFILE_CHECKSUM_A,
    **changes: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "profile_id": profile_id,
        "revision": revision,
        "schema_version": 1,
        "specification_checksum": checksum,
        "name": "Primary paper profile",
        "description": "Deterministic policy snapshot",
        "mandate_id": mandate_id,
        "mandate_approved_revision": mandate_revision,
        "mandate_specification_checksum": mandate_checksum,
        "exchange": "binance",
        "market_type": "spot",
        "base_asset": "BTC" if mandate_revision == 1 else "ETH",
        "quote_asset": "USDT",
        "timeframe": "1h",
        "start_at": BASE_TIME,
        "warmup_candles": 20,
        "strategy_definition_id": strategy_id,
        "strategy_source_revision": 1,
        "strategy_plugin_name": "ema-cross-example",
        "strategy_plugin_version": "2",
        "strategy_plugin_schema_version": 1,
        "strategy_lifecycle_version": 2,
        "strategy_parameters": Jsonb([{"name": "fast", "type": "integer", "value": 12}]),
        "strategy_parameters_checksum": PARAMETERS_CHECKSUM,
        "strategy_snapshot_checksum": SNAPSHOT_CHECKSUM,
        "strategy_snapshot_schema_version": 1,
        "execution": Jsonb({"force_close_at_end": False}),
        "instrument_constraints": Jsonb({"minimum_quantity": "0.001"}),
        "risk_limits": Jsonb({"max_open_orders": 4}),
        "history_window": 512,
        "max_candles": 10_000,
        "max_orders": 1_000,
        "max_events": 10_000,
        "engine_version": "paper-engine-v1",
        "market_regime_policy": Jsonb({"schema_version": 1}),
        "created_by": actor_id,
        "created_at": BASE_TIME,
    }
    values.update(changes)
    return values


def _insert_revision(connection: Connection[Any], values: dict[str, object]) -> None:
    connection.execute(
        """
        insert into public.operational_paper_session_profile_revisions (
            profile_id, revision, schema_version, specification_checksum,
            name, description,
            mandate_id, mandate_approved_revision, mandate_specification_checksum,
            exchange, market_type, base_asset, quote_asset,
            timeframe, start_at, warmup_candles,
            strategy_definition_id, strategy_source_revision,
            strategy_plugin_name, strategy_plugin_version,
            strategy_plugin_schema_version, strategy_lifecycle_version,
            strategy_parameters, strategy_parameters_checksum,
            strategy_snapshot_checksum, strategy_snapshot_schema_version,
            execution, instrument_constraints, risk_limits,
            history_window, max_candles, max_orders, max_events,
            engine_version, market_regime_policy, created_by, created_at
        )
        values (
            %(profile_id)s, %(revision)s, %(schema_version)s,
            %(specification_checksum)s, %(name)s, %(description)s,
            %(mandate_id)s, %(mandate_approved_revision)s,
            %(mandate_specification_checksum)s,
            %(exchange)s, %(market_type)s, %(base_asset)s, %(quote_asset)s,
            %(timeframe)s, %(start_at)s, %(warmup_candles)s,
            %(strategy_definition_id)s, %(strategy_source_revision)s,
            %(strategy_plugin_name)s, %(strategy_plugin_version)s,
            %(strategy_plugin_schema_version)s, %(strategy_lifecycle_version)s,
            %(strategy_parameters)s, %(strategy_parameters_checksum)s,
            %(strategy_snapshot_checksum)s, %(strategy_snapshot_schema_version)s,
            %(execution)s, %(instrument_constraints)s, %(risk_limits)s,
            %(history_window)s, %(max_candles)s, %(max_orders)s, %(max_events)s,
            %(engine_version)s, %(market_regime_policy)s,
            %(created_by)s, %(created_at)s
        )
        """,
        values,
    )


def _insert_aggregate(
    connection: Connection[Any],
    *,
    profile_id: UUID,
    actor_id: UUID,
    idempotency_key: str | None = None,
    fingerprint: str = FINGERPRINT,
    state: str = "DRAFT",
    current_revision: int = 1,
    record_version: int = 1,
    approved_revision: int | None = None,
    approved_checksum: str | None = None,
    approved_by: UUID | None = None,
    approved_at: datetime | None = None,
    archived_by: UUID | None = None,
    archived_at: datetime | None = None,
) -> None:
    connection.execute(
        """
        insert into public.operational_paper_session_profiles (
            profile_id, state, current_revision, record_version,
            approved_revision, approved_checksum, created_by, created_at,
            approved_by, approved_at, archived_by, archived_at,
            create_idempotency_key, create_intent_fingerprint
        )
        values (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            profile_id,
            state,
            current_revision,
            record_version,
            approved_revision,
            approved_checksum,
            actor_id,
            BASE_TIME,
            approved_by,
            approved_at,
            archived_by,
            archived_at,
            idempotency_key or f"profile:{profile_id}",
            fingerprint,
        ),
    )


def _create_profile(
    database_url: str,
    actor_id: UUID,
    *,
    profile_id: UUID | None = None,
    revision_changes: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> tuple[UUID, UUID, UUID]:
    profile = profile_id or uuid4()
    with psycopg.connect(database_url) as connection:
        strategy_id = _seed_strategy(connection, actor_id)
        mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
            connection, actor_id
        )
        values = _revision_values(
            profile_id=profile,
            actor_id=actor_id,
            mandate_id=mandate_id,
            mandate_revision=mandate_revision,
            mandate_checksum=mandate_checksum,
            strategy_id=strategy_id,
            **(revision_changes or {}),
        )
        _insert_revision(connection, values)
        _insert_aggregate(
            connection,
            profile_id=profile,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
    return profile, mandate_id, strategy_id


def _approve_profile(
    connection: Connection[Any],
    *,
    profile_id: UUID,
    actor_id: UUID,
    checksum: str = PROFILE_CHECKSUM_A,
    approved_at: datetime = BASE_TIME + timedelta(seconds=3),
) -> None:
    connection.execute(
        """
        update public.operational_paper_session_profiles
        set state = 'APPROVED',
            record_version = record_version + 1,
            approved_revision = current_revision,
            approved_checksum = %s,
            approved_by = %s,
            approved_at = %s
        where profile_id = %s
        """,
        (checksum, actor_id, approved_at, profile_id),
    )


def test_schema_has_exact_tables_columns_keys_indexes_and_triggers(
    database_url: str,
) -> None:
    expected_columns = {
        "operational_paper_session_profiles": {
            "profile_id",
            "state",
            "current_revision",
            "record_version",
            "approved_revision",
            "approved_checksum",
            "created_by",
            "created_at",
            "approved_by",
            "approved_at",
            "archived_by",
            "archived_at",
            "create_idempotency_key",
            "create_intent_fingerprint",
        },
        "operational_paper_session_profile_revisions": {
            "profile_id",
            "revision",
            "schema_version",
            "specification_checksum",
            "name",
            "description",
            "mandate_id",
            "mandate_approved_revision",
            "mandate_specification_checksum",
            "exchange",
            "market_type",
            "base_asset",
            "quote_asset",
            "timeframe",
            "start_at",
            "warmup_candles",
            "strategy_definition_id",
            "strategy_source_revision",
            "strategy_plugin_name",
            "strategy_plugin_version",
            "strategy_plugin_schema_version",
            "strategy_lifecycle_version",
            "strategy_parameters",
            "strategy_parameters_checksum",
            "strategy_snapshot_checksum",
            "strategy_snapshot_schema_version",
            "execution",
            "instrument_constraints",
            "risk_limits",
            "history_window",
            "max_candles",
            "max_orders",
            "max_events",
            "engine_version",
            "market_regime_policy",
            "created_by",
            "created_at",
        },
    }
    with psycopg.connect(database_url, autocommit=True) as connection:
        table_names = connection.execute(
            """
            select tablename
            from pg_catalog.pg_tables
            where schemaname = 'public'
              and tablename like 'operational_paper_session_profile%'
            order by tablename
            """
        ).fetchall()
        assert table_names == [
            ("operational_paper_session_profile_revisions",),
            ("operational_paper_session_profiles",),
        ]

        rows = connection.execute(
            """
            select table_name, column_name
            from information_schema.columns
            where table_schema = 'public'
              and table_name like 'operational_paper_session_profile%'
            """
        ).fetchall()
        actual: dict[str, set[str]] = {}
        for table_name, column_name in rows:
            actual.setdefault(table_name, set()).add(column_name)
        assert actual == expected_columns

        identity_constraints = connection.execute(
            """
            select constraint_name, constraint_type
            from information_schema.table_constraints
            where constraint_schema = 'public'
              and constraint_name in (
                  'operational_paper_session_profiles_pkey',
                  'operational_paper_session_profiles_actor_idempotency_key',
                  'operational_paper_session_profile_revisions_pkey',
                  'operational_paper_session_profile_revisions_checksum_key'
              )
            order by constraint_name
            """
        ).fetchall()
        assert identity_constraints == [
            ("operational_paper_session_profile_revisions_checksum_key", "UNIQUE"),
            ("operational_paper_session_profile_revisions_pkey", "PRIMARY KEY"),
            ("operational_paper_session_profiles_actor_idempotency_key", "UNIQUE"),
            ("operational_paper_session_profiles_pkey", "PRIMARY KEY"),
        ]

        foreign_keys = connection.execute(
            """
            select constraint_name, is_deferrable, initially_deferred
            from information_schema.table_constraints
            where constraint_schema = 'public'
              and constraint_name in (
                  'operational_paper_session_profile_revisions_profile_id_fkey',
                  'operational_paper_session_profile_revisions_mandate_fkey',
                  'operational_paper_session_profile_revisions_instrument_fkey',
                  'operational_paper_session_profile_revisions_strategy_fkey',
                  'operational_paper_session_profiles_current_revision_fkey',
                  'operational_paper_session_profiles_approved_revision_fkey'
              )
            order by constraint_name
            """
        ).fetchall()
        assert foreign_keys == [
            ("operational_paper_session_profile_revisions_instrument_fkey", "NO", "NO"),
            ("operational_paper_session_profile_revisions_mandate_fkey", "NO", "NO"),
            ("operational_paper_session_profile_revisions_profile_id_fkey", "YES", "YES"),
            ("operational_paper_session_profile_revisions_strategy_fkey", "NO", "NO"),
            ("operational_paper_session_profiles_approved_revision_fkey", "NO", "NO"),
            ("operational_paper_session_profiles_current_revision_fkey", "NO", "NO"),
        ]

        indexes = connection.execute(
            """
            select indexname
            from pg_catalog.pg_indexes
            where schemaname = 'public'
              and tablename = 'operational_paper_session_profiles'
            order by indexname
            """
        ).fetchall()
        assert indexes == [
            ("operational_paper_session_profiles_actor_idempotency_key",),
            ("operational_paper_session_profiles_created_idx",),
            ("operational_paper_session_profiles_pkey",),
            ("operational_paper_session_profiles_state_created_idx",),
        ]

        triggers = connection.execute(
            """
            select trigger.tgname
            from pg_catalog.pg_trigger as trigger
            join pg_catalog.pg_class as class on class.oid = trigger.tgrelid
            join pg_catalog.pg_namespace as namespace on namespace.oid = class.relnamespace
            where namespace.nspname = 'public'
              and class.relname like 'operational_paper_session_profile%'
              and not trigger.tgisinternal
            order by trigger.tgname
            """
        ).fetchall()
        assert triggers == [
            ("op_ps_profile_revisions_reject_update_delete",),
            ("operational_paper_session_profile_revision_publication_check",),
            ("operational_paper_session_profile_revisions_validate_insert",),
            ("operational_paper_session_profiles_protect_update_delete",),
            ("operational_paper_session_profiles_validate_insert",),
        ]
        assert connection.execute(
            """
            select tgdeferrable, tginitdeferred
            from pg_catalog.pg_trigger
            where tgname = 'operational_paper_session_profile_revision_publication_check'
            """
        ).fetchone() == (True, True)

        check_constraints = {
            name
            for (name,) in connection.execute(
                """
                select constraint_name
                from information_schema.table_constraints
                where constraint_schema = 'public'
                  and table_name like 'operational_paper_session_profile%'
                  and constraint_type = 'CHECK'
                """
            ).fetchall()
        }
        assert {
            "operational_paper_session_profiles_state_shape_check",
            "operational_paper_session_profiles_chronology_check",
            "operational_paper_session_profile_revisions_timeframe_check",
            "operational_paper_session_profile_revisions_capability_check",
            "op_ps_profile_revisions_strategy_parameters_check",
            "op_ps_profile_revisions_window_relationship_check",
        } <= check_constraints


def test_schema_has_rls_no_policies_and_closed_acl(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            """
            select class.relname, class.relrowsecurity
            from pg_catalog.pg_class as class
            join pg_catalog.pg_namespace as namespace on namespace.oid = class.relnamespace
            where namespace.nspname = 'public'
              and class.relname in (
                  'operational_paper_session_profiles',
                  'operational_paper_session_profile_revisions'
              )
            order by class.relname
            """
        ).fetchall() == [
            ("operational_paper_session_profile_revisions", True),
            ("operational_paper_session_profiles", True),
        ]
        assert (
            connection.execute(
                """
            select tablename, policyname
            from pg_catalog.pg_policies
            where schemaname = 'public'
              and tablename like 'operational_paper_session_profile%'
            """
            ).fetchall()
            == []
        )

        for role in ("public", *DATA_API_ROLES):
            for table in PROFILE_TABLES:
                assert connection.execute(
                    """
                    select
                        has_table_privilege(%s, %s, 'SELECT'),
                        has_table_privilege(%s, %s, 'INSERT'),
                        has_table_privilege(%s, %s, 'UPDATE'),
                        has_table_privilege(%s, %s, 'DELETE')
                    """,
                    (role, table, role, table, role, table, role, table),
                ).fetchone() == (False, False, False, False)
            for function_name in TRIGGER_FUNCTIONS:
                assert connection.execute(
                    "select has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, function_name),
                ).fetchone() == (False,)


def test_initial_draft_is_atomic_and_uses_same_actor(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    profile_id, _, _ = _create_profile(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            """
            select state, current_revision, record_version, created_by
            from public.operational_paper_session_profiles
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone() == ("DRAFT", 1, 1, admin_user_id)
        assert connection.execute(
            """
            select revision, created_by
            from public.operational_paper_session_profile_revisions
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone() == (1, admin_user_id)

    other_actor = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, other_actor)
        with connection.transaction(), pytest.raises(psycopg.errors.CheckViolation):
            strategy_id = _seed_strategy(connection, admin_user_id)
            mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
                connection, admin_user_id
            )
            mismatched_profile = uuid4()
            _insert_revision(
                connection,
                _revision_values(
                    profile_id=mismatched_profile,
                    actor_id=other_actor,
                    mandate_id=mandate_id,
                    mandate_revision=mandate_revision,
                    mandate_checksum=mandate_checksum,
                    strategy_id=strategy_id,
                ),
            )
            _insert_aggregate(
                connection,
                profile_id=mismatched_profile,
                actor_id=admin_user_id,
            )

    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _insert_aggregate(
                connection,
                profile_id=uuid4(),
                actor_id=admin_user_id,
            )


@pytest.mark.parametrize(
    "mutation",
    ["missing_mandate", "missing_revision", "wrong_checksum", "missing_instrument"],
)
def test_exact_mandate_revision_and_instrument_binding_rejects_mismatch(
    database_url: str,
    admin_user_id: UUID,
    mutation: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.transaction():
            strategy_id = _seed_strategy(connection, admin_user_id)
            mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
                connection, admin_user_id
            )
        values = _revision_values(
            profile_id=uuid4(),
            actor_id=admin_user_id,
            mandate_id=mandate_id,
            mandate_revision=mandate_revision,
            mandate_checksum=mandate_checksum,
            strategy_id=strategy_id,
        )
        if mutation == "missing_mandate":
            values["mandate_id"] = uuid4()
        elif mutation == "missing_revision":
            values["mandate_approved_revision"] = 99
        elif mutation == "wrong_checksum":
            values["mandate_specification_checksum"] = "0" * 64
        else:
            values["base_asset"] = "ETH"

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                _insert_revision(connection, values)


def test_instrument_from_different_mandate_revision_is_rejected(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.transaction():
            strategy_id = _seed_strategy(connection, admin_user_id)
            mandate_id, _, _ = _seed_approved_mandate(connection, admin_user_id, two_revisions=True)
        values = _revision_values(
            profile_id=uuid4(),
            actor_id=admin_user_id,
            mandate_id=mandate_id,
            mandate_revision=1,
            mandate_checksum=MANDATE_CHECKSUM_A,
            strategy_id=strategy_id,
            base_asset="ETH",
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                _insert_revision(connection, values)


def test_mandate_archive_preserves_profile_history(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    profile_id, mandate_id, _ = _create_profile(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            update public.operational_mandates
            set state = 'ARCHIVED',
                record_version = record_version + 1,
                archived_by = %s,
                archived_at = %s
            where mandate_id = %s
            """,
            (admin_user_id, BASE_TIME + timedelta(seconds=4), mandate_id),
        )
        assert connection.execute(
            """
            select count(*)
            from public.operational_paper_session_profile_revisions
            where profile_id = %s and mandate_id = %s
            """,
            (profile_id, mandate_id),
        ).fetchone() == (1,)


def test_strategy_fk_is_stable_id_only_and_history_survives_source_changes(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    profile_id, _, strategy_id = _create_profile(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
                    connection, admin_user_id
                )
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=uuid4(),
                        actor_id=admin_user_id,
                        mandate_id=mandate_id,
                        mandate_revision=mandate_revision,
                        mandate_checksum=mandate_checksum,
                        strategy_id=uuid4(),
                    ),
                )

        connection.execute(
            """
            update public.strategy_definitions
            set display_name = 'Revised strategy',
                parameters = '{"changed": true}'::jsonb,
                parameters_checksum = repeat('1', 64),
                revision = revision + 1,
                updated_by = %s
            where id = %s
            """,
            (admin_user_id, strategy_id),
        )
        connection.execute(
            """
            update public.strategy_definitions
            set state = 'ARCHIVED',
                revision = revision + 1,
                archived_at = now(),
                updated_by = %s
            where id = %s
            """,
            (admin_user_id, strategy_id),
        )
        assert connection.execute(
            """
            select strategy_source_revision, strategy_parameters_checksum
            from public.operational_paper_session_profile_revisions
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone() == (1, PARAMETERS_CHECKSUM)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("specification_checksum", "A" * 64),
        ("strategy_parameters_checksum", "bad"),
        ("strategy_snapshot_checksum", "0" * 63),
    ],
)
def test_malformed_snapshot_checksums_are_rejected(
    database_url: str,
    admin_user_id: UUID,
    field: str,
    value: object,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.transaction():
            strategy_id = _seed_strategy(connection, admin_user_id)
            mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
                connection, admin_user_id
            )
        values = _revision_values(
            profile_id=uuid4(),
            actor_id=admin_user_id,
            mandate_id=mandate_id,
            mandate_revision=mandate_revision,
            mandate_checksum=mandate_checksum,
            strategy_id=strategy_id,
            **{field: value},
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                _insert_revision(connection, values)


def test_well_formed_semantically_wrong_checksums_are_structurally_storable(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    profile_id, _, _ = _create_profile(
        database_url,
        admin_user_id,
        revision_changes={
            "specification_checksum": "0" * 64,
            "strategy_parameters_checksum": "1" * 64,
            "strategy_snapshot_checksum": "2" * 64,
        },
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            """
            select specification_checksum
            from public.operational_paper_session_profile_revisions
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone() == ("0" * 64,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy_parameters", Jsonb({"not": "an array"})),
        ("execution", Jsonb([])),
        ("instrument_constraints", Jsonb([])),
        ("risk_limits", Jsonb([])),
        ("market_regime_policy", Jsonb([])),
    ],
)
def test_policy_snapshot_json_container_shapes_are_enforced(
    database_url: str,
    admin_user_id: UUID,
    field: str,
    value: object,
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _create_profile(
            database_url,
            admin_user_id,
            revision_changes={field: value},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exchange", "kraken"),
        ("market_type", "futures"),
        ("timeframe", "2h"),
    ],
)
def test_capability_and_timeframe_registry_are_closed(
    database_url: str,
    admin_user_id: UUID,
    field: str,
    value: str,
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _create_profile(
            database_url,
            admin_user_id,
            revision_changes={field: value},
        )


def test_every_canonical_timeframe_is_accepted(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.transaction():
            strategy_id = _seed_strategy(connection, admin_user_id)
            mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
                connection, admin_user_id
            )
        for timeframe in CANONICAL_TIMEFRAMES:
            profile_id = uuid4()
            with connection.transaction():
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=profile_id,
                        actor_id=admin_user_id,
                        mandate_id=mandate_id,
                        mandate_revision=mandate_revision,
                        mandate_checksum=mandate_checksum,
                        strategy_id=strategy_id,
                        timeframe=timeframe,
                    ),
                )
                _insert_aggregate(
                    connection,
                    profile_id=profile_id,
                    actor_id=admin_user_id,
                )


@pytest.mark.parametrize(
    ("valid_changes", "invalid_changes"),
    [
        ({"name": "x" * 120}, {"name": "x" * 121}),
        ({"description": "x" * 1000}, {"description": "x" * 1001}),
        (
            {"warmup_candles": 100_000, "history_window": 100_000, "max_candles": 100_000},
            {"warmup_candles": 100_001, "history_window": 100_000},
        ),
        (
            {"history_window": 100_000, "max_candles": 100_000},
            {"history_window": 100_001, "max_candles": 200_000},
        ),
        ({"max_candles": 2_000_000}, {"max_candles": 2_000_001}),
        ({"max_orders": 1_000_000}, {"max_orders": 1_000_001}),
        ({"max_events": 20_000_000}, {"max_events": 20_000_001}),
    ],
)
def test_profile_bounds_accept_exact_limit_and_reject_limit_plus_one(
    database_url: str,
    admin_user_id: UUID,
    valid_changes: dict[str, object],
    invalid_changes: dict[str, object],
) -> None:
    _create_profile(database_url, admin_user_id, revision_changes=valid_changes)
    with pytest.raises(psycopg.errors.CheckViolation):
        _create_profile(database_url, admin_user_id, revision_changes=invalid_changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"warmup_candles": -1},
        {"history_window": 0},
        {"max_candles": 0},
        {"max_orders": 0},
        {"max_events": 0},
        {"warmup_candles": 513, "history_window": 512},
        {"history_window": 513, "max_candles": 512},
        {"warmup_candles": 1, "strategy_lifecycle_version": 1},
    ],
)
def test_profile_bounds_and_relationships_reject_invalid_values(
    database_url: str,
    admin_user_id: UUID,
    changes: dict[str, object],
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _create_profile(database_url, admin_user_id, revision_changes=changes)


def test_strategy_source_and_plugin_schema_accept_postgresql_maxima(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    profile_id, _, _ = _create_profile(
        database_url,
        admin_user_id,
        revision_changes={
            "strategy_source_revision": POSTGRESQL_BIGINT_MAX,
            "strategy_plugin_schema_version": POSTGRESQL_INTEGER_MAX,
        },
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            """
            select strategy_source_revision, strategy_plugin_schema_version
            from public.operational_paper_session_profile_revisions
            where profile_id = %s
            """,
            (profile_id,),
        ).fetchone() == (POSTGRESQL_BIGINT_MAX, POSTGRESQL_INTEGER_MAX)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", POSTGRESQL_BIGINT_MAX + 1),
        ("mandate_approved_revision", POSTGRESQL_BIGINT_MAX + 1),
        ("strategy_source_revision", POSTGRESQL_BIGINT_MAX + 1),
        ("strategy_plugin_schema_version", POSTGRESQL_INTEGER_MAX + 1),
        ("strategy_lifecycle_version", POSTGRESQL_INTEGER_MAX + 1),
    ],
)
def test_revision_integer_columns_reject_values_outside_postgresql_width(
    database_url: str,
    admin_user_id: UUID,
    field: str,
    value: int,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.transaction():
            strategy_id = _seed_strategy(connection, admin_user_id)
            mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
                connection, admin_user_id
            )
        values = _revision_values(
            profile_id=uuid4(),
            actor_id=admin_user_id,
            mandate_id=mandate_id,
            mandate_revision=mandate_revision,
            mandate_checksum=mandate_checksum,
            strategy_id=strategy_id,
            **{field: value},
        )
        with pytest.raises(psycopg.errors.NumericValueOutOfRange) as exc_info:
            with connection.transaction():
                _insert_revision(connection, values)
        assert exc_info.value.sqlstate == "22003"


@pytest.mark.parametrize(
    "field",
    ["current_revision", "record_version", "approved_revision"],
)
def test_aggregate_bigint_columns_reject_values_outside_postgresql_width(
    database_url: str,
    admin_user_id: UUID,
    field: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        strategy_id = _seed_strategy(connection, admin_user_id)
        mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
            connection, admin_user_id
        )
        profile_id = uuid4()
        _insert_revision(
            connection,
            _revision_values(
                profile_id=profile_id,
                actor_id=admin_user_id,
                mandate_id=mandate_id,
                mandate_revision=mandate_revision,
                mandate_checksum=mandate_checksum,
                strategy_id=strategy_id,
            ),
        )
        with pytest.raises(psycopg.errors.NumericValueOutOfRange) as exc_info:
            _insert_aggregate(
                connection,
                profile_id=profile_id,
                actor_id=admin_user_id,
                **{field: POSTGRESQL_BIGINT_MAX + 1},
            )
        assert exc_info.value.sqlstate == "22003"


def test_revision_and_aggregate_integer_column_types_are_exact(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            """
            select table_name, column_name, data_type
            from information_schema.columns
            where table_schema = 'public'
              and (
                  (
                      table_name = 'operational_paper_session_profile_revisions'
                      and column_name in (
                          'revision',
                          'mandate_approved_revision',
                          'strategy_source_revision',
                          'strategy_plugin_schema_version',
                          'strategy_lifecycle_version'
                      )
                  )
                  or (
                      table_name = 'operational_paper_session_profiles'
                      and column_name in (
                          'current_revision',
                          'record_version',
                          'approved_revision'
                      )
                  )
              )
            order by table_name, column_name
            """
        ).fetchall() == [
            (
                "operational_paper_session_profile_revisions",
                "mandate_approved_revision",
                "bigint",
            ),
            ("operational_paper_session_profile_revisions", "revision", "bigint"),
            (
                "operational_paper_session_profile_revisions",
                "strategy_lifecycle_version",
                "integer",
            ),
            (
                "operational_paper_session_profile_revisions",
                "strategy_plugin_schema_version",
                "integer",
            ),
            (
                "operational_paper_session_profile_revisions",
                "strategy_source_revision",
                "bigint",
            ),
            ("operational_paper_session_profiles", "approved_revision", "bigint"),
            ("operational_paper_session_profiles", "current_revision", "bigint"),
            ("operational_paper_session_profiles", "record_version", "bigint"),
        ]


def test_revision_snapshots_are_immutable_and_delete_is_forbidden(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    profile_id, _, _ = _create_profile(database_url, admin_user_id)
    assignments = (
        "execution = '{}'::jsonb",
        "instrument_constraints = '{}'::jsonb",
        "risk_limits = '{}'::jsonb",
        "market_regime_policy = '{}'::jsonb",
        "strategy_parameters = '[]'::jsonb",
        "strategy_source_revision = 2",
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        for assignment in assignments:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                connection.execute(
                    f"""
                    update public.operational_paper_session_profile_revisions
                    set {assignment}
                    where profile_id = %s
                    """,
                    (profile_id,),
                )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                delete from public.operational_paper_session_profile_revisions
                where profile_id = %s
                """,
                (profile_id,),
            )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "delete from public.operational_paper_session_profiles where profile_id = %s",
                (profile_id,),
            )


def test_revision_sequence_publication_and_hidden_history_are_enforced(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    profile_id, mandate_id, strategy_id = _create_profile(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            with connection.transaction():
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=uuid4(),
                        actor_id=admin_user_id,
                        mandate_id=mandate_id,
                        mandate_revision=1,
                        mandate_checksum=MANDATE_CHECKSUM_A,
                        strategy_id=strategy_id,
                    ),
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=uuid4(),
                        actor_id=admin_user_id,
                        mandate_id=mandate_id,
                        mandate_revision=1,
                        mandate_checksum=MANDATE_CHECKSUM_A,
                        strategy_id=strategy_id,
                        revision=2,
                    ),
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=profile_id,
                        actor_id=admin_user_id,
                        mandate_id=mandate_id,
                        mandate_revision=1,
                        mandate_checksum=MANDATE_CHECKSUM_A,
                        strategy_id=strategy_id,
                        revision=3,
                    ),
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=profile_id,
                        actor_id=admin_user_id,
                        mandate_id=mandate_id,
                        mandate_revision=1,
                        mandate_checksum=MANDATE_CHECKSUM_A,
                        strategy_id=strategy_id,
                    ),
                )

        with connection.transaction():
            _insert_revision(
                connection,
                _revision_values(
                    profile_id=profile_id,
                    actor_id=admin_user_id,
                    mandate_id=mandate_id,
                    mandate_revision=1,
                    mandate_checksum=MANDATE_CHECKSUM_A,
                    strategy_id=strategy_id,
                    revision=2,
                    checksum=PROFILE_CHECKSUM_B,
                    name="Revised profile",
                ),
            )
            connection.execute(
                """
                update public.operational_paper_session_profiles
                set current_revision = 2, record_version = 2
                where profile_id = %s
                """,
                (profile_id,),
            )

        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=profile_id,
                        actor_id=admin_user_id,
                        mandate_id=mandate_id,
                        mandate_revision=1,
                        mandate_checksum=MANDATE_CHECKSUM_A,
                        strategy_id=strategy_id,
                        revision=3,
                        checksum="1" * 64,
                    ),
                )


@pytest.mark.parametrize("target_state", ["APPROVED", "ARCHIVED"])
def test_revision_append_is_forbidden_after_draft(
    database_url: str,
    admin_user_id: UUID,
    target_state: str,
) -> None:
    profile_id, mandate_id, strategy_id = _create_profile(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        if target_state == "APPROVED":
            _approve_profile(connection, profile_id=profile_id, actor_id=admin_user_id)
        else:
            connection.execute(
                """
                update public.operational_paper_session_profiles
                set state = 'ARCHIVED', record_version = 2,
                    archived_by = %s, archived_at = %s
                where profile_id = %s
                """,
                (admin_user_id, BASE_TIME + timedelta(seconds=3), profile_id),
            )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            with connection.transaction():
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=profile_id,
                        actor_id=admin_user_id,
                        mandate_id=mandate_id,
                        mandate_revision=1,
                        mandate_checksum=MANDATE_CHECKSUM_A,
                        strategy_id=strategy_id,
                        revision=2,
                        checksum=PROFILE_CHECKSUM_B,
                    ),
                )


def test_valid_lifecycle_paths_preserve_approval_history(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    approved_id, _, _ = _create_profile(
        database_url, admin_user_id, idempotency_key="approve-archive"
    )
    draft_id, _, _ = _create_profile(database_url, admin_user_id, idempotency_key="draft-archive")
    with psycopg.connect(database_url, autocommit=True) as connection:
        _approve_profile(connection, profile_id=approved_id, actor_id=admin_user_id)
        connection.execute(
            """
            update public.operational_paper_session_profiles
            set state = 'ARCHIVED', record_version = 3,
                archived_by = %s, archived_at = %s
            where profile_id = %s
            """,
            (admin_user_id, BASE_TIME + timedelta(seconds=4), approved_id),
        )
        connection.execute(
            """
            update public.operational_paper_session_profiles
            set state = 'ARCHIVED', record_version = 2,
                archived_by = %s, archived_at = %s
            where profile_id = %s
            """,
            (admin_user_id, BASE_TIME + timedelta(seconds=3), draft_id),
        )
        assert connection.execute(
            """
            select state, approved_revision, approved_checksum, record_version
            from public.operational_paper_session_profiles
            where profile_id = %s
            """,
            (approved_id,),
        ).fetchone() == ("ARCHIVED", 1, PROFILE_CHECKSUM_A, 3)
        assert connection.execute(
            """
            select state, approved_revision, record_version
            from public.operational_paper_session_profiles
            where profile_id = %s
            """,
            (draft_id,),
        ).fetchone() == ("ARCHIVED", None, 2)


@pytest.mark.parametrize(
    "update_sql",
    [
        "set record_version = record_version",
        "set state = 'ARCHIVED', record_version = record_version",
        "set state = 'ARCHIVED', record_version = record_version + 2",
        "set current_revision = current_revision + 2, record_version = record_version + 1",
    ],
)
def test_record_version_and_current_revision_changes_must_be_exact(
    database_url: str,
    admin_user_id: UUID,
    update_sql: str,
) -> None:
    profile_id, _, _ = _create_profile(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(
                f"""
                update public.operational_paper_session_profiles
                {update_sql}
                where profile_id = %s
                """,
                (profile_id,),
            )


def test_invalid_lifecycle_and_metadata_mutations_are_rejected(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    profile_id, _, _ = _create_profile(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update public.operational_paper_session_profiles
                set state = 'APPROVED', record_version = 2,
                    approved_revision = 1, approved_checksum = %s,
                    approved_by = %s, approved_at = %s
                where profile_id = %s
                """,
                (PROFILE_CHECKSUM_A, admin_user_id, BASE_TIME - timedelta(seconds=1), profile_id),
            )
        _approve_profile(connection, profile_id=profile_id, actor_id=admin_user_id)
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.operational_paper_session_profiles
                set state = 'DRAFT', record_version = 3,
                    approved_revision = null, approved_checksum = null,
                    approved_by = null, approved_at = null
                where profile_id = %s
                """,
                (profile_id,),
            )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.operational_paper_session_profiles
                set state = 'ARCHIVED', record_version = 3,
                    approved_checksum = repeat('1', 64),
                    archived_by = %s, archived_at = %s
                where profile_id = %s
                """,
                (admin_user_id, BASE_TIME + timedelta(seconds=4), profile_id),
            )


def test_archive_chronology_and_terminal_metadata_are_enforced(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    approved_id, _, _ = _create_profile(
        database_url, admin_user_id, idempotency_key="archive-chronology"
    )
    terminal_id, _, _ = _create_profile(
        database_url, admin_user_id, idempotency_key="archive-terminal"
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        _approve_profile(
            connection,
            profile_id=approved_id,
            actor_id=admin_user_id,
            approved_at=BASE_TIME + timedelta(seconds=4),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                update public.operational_paper_session_profiles
                set state = 'ARCHIVED', record_version = 3,
                    archived_by = %s, archived_at = %s
                where profile_id = %s
                """,
                (admin_user_id, BASE_TIME + timedelta(seconds=3), approved_id),
            )

        connection.execute(
            """
            update public.operational_paper_session_profiles
            set state = 'ARCHIVED', record_version = 2,
                archived_by = %s, archived_at = %s
            where profile_id = %s
            """,
            (admin_user_id, BASE_TIME + timedelta(seconds=3), terminal_id),
        )
        for assignment in (
            "state = 'DRAFT'",
            "state = 'APPROVED'",
            "archived_at = archived_at + interval '1 second'",
        ):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                connection.execute(
                    f"""
                    update public.operational_paper_session_profiles
                    set {assignment}, record_version = 3
                    where profile_id = %s
                    """,
                    (terminal_id,),
                )


def test_actor_scoped_idempotency_and_create_identity_are_protected(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    other_actor = uuid4()
    profile_id, _, _ = _create_profile(database_url, admin_user_id, idempotency_key="same-key")
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, other_actor)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _create_profile(database_url, admin_user_id, idempotency_key="same-key")
    _create_profile(database_url, other_actor, idempotency_key="same-key")

    with psycopg.connect(database_url, autocommit=True) as connection:
        for assignment in (
            "created_by = gen_random_uuid()",
            "created_at = created_at + interval '1 second'",
            "create_idempotency_key = 'changed'",
            "create_intent_fingerprint = repeat('1', 64)",
        ):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                connection.execute(
                    f"""
                    update public.operational_paper_session_profiles
                    set {assignment}, record_version = record_version + 1
                    where profile_id = %s
                    """,
                    (profile_id,),
                )


@pytest.mark.parametrize(
    ("idempotency_key", "fingerprint"),
    [
        ("bad key", FINGERPRINT),
        ("x" * 129, FINGERPRINT),
        ("valid", "A" * 64),
        ("valid", "a" * 63),
    ],
)
def test_create_tokens_are_strictly_validated(
    database_url: str,
    admin_user_id: UUID,
    idempotency_key: str,
    fingerprint: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        strategy_id = _seed_strategy(connection, admin_user_id)
        mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
            connection, admin_user_id
        )
        profile_id = uuid4()
        _insert_revision(
            connection,
            _revision_values(
                profile_id=profile_id,
                actor_id=admin_user_id,
                mandate_id=mandate_id,
                mandate_revision=mandate_revision,
                mandate_checksum=mandate_checksum,
                strategy_id=strategy_id,
            ),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_aggregate(
                connection,
                profile_id=profile_id,
                actor_id=admin_user_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )


def test_actor_foreign_keys_collective_metadata_and_chronology(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    missing_actor = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.transaction():
            strategy_id = _seed_strategy(connection, admin_user_id)
            mandate_id, mandate_revision, mandate_checksum = _seed_approved_mandate(
                connection, admin_user_id
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                _insert_revision(
                    connection,
                    _revision_values(
                        profile_id=uuid4(),
                        actor_id=missing_actor,
                        mandate_id=mandate_id,
                        mandate_revision=mandate_revision,
                        mandate_checksum=mandate_checksum,
                        strategy_id=strategy_id,
                    ),
                )

    profile_id, _, _ = _create_profile(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                update public.operational_paper_session_profiles
                set state = 'APPROVED', record_version = 2,
                    approved_revision = 1, approved_checksum = %s,
                    approved_by = %s, approved_at = %s
                where profile_id = %s
                """,
                (
                    PROFILE_CHECKSUM_A,
                    missing_actor,
                    BASE_TIME + timedelta(seconds=3),
                    profile_id,
                ),
            )
        for statement, params in (
            (
                """
                update public.operational_paper_session_profiles
                set state = 'APPROVED', record_version = 2,
                    approved_revision = 1, approved_checksum = %s,
                    approved_at = %s
                where profile_id = %s
                """,
                (PROFILE_CHECKSUM_A, BASE_TIME + timedelta(seconds=3), profile_id),
            ),
            (
                """
                update public.operational_paper_session_profiles
                set state = 'ARCHIVED', record_version = 2,
                    archived_by = %s
                where profile_id = %s
                """,
                (admin_user_id, profile_id),
            ),
            (
                """
                update public.operational_paper_session_profiles
                set state = 'ARCHIVED', record_version = 2,
                    archived_by = %s, archived_at = %s
                where profile_id = %s
                """,
                (admin_user_id, BASE_TIME - timedelta(seconds=1), profile_id),
            ),
        ):
            with pytest.raises(psycopg.Error):
                connection.execute(statement, params)


def test_schema_contains_no_capital_session_or_runtime_authority(database_url: str) -> None:
    forbidden = {
        "capital",
        "initial_capital",
        "allocation",
        "portfolio",
        "balance",
        "session_id",
        "paper_session_id",
        "materialization",
        "runner",
        "collector",
        "worker",
    }
    with psycopg.connect(database_url, autocommit=True) as connection:
        columns = {
            column_name
            for (column_name,) in connection.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public'
                  and table_name like 'operational_paper_session_profile%'
                """
            ).fetchall()
        }
        assert forbidden.isdisjoint(columns)
