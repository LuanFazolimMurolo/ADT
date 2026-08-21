"""Phase 7-06 operational-mandate PostgreSQL persistence invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection

from tests.postgres_support import add_auth_user

BASE_TIME = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
CHECKSUM_A = "a" * 64
CHECKSUM_B = "b" * 64
FINGERPRINT_A = "c" * 64

MANDATE_TABLE = "public.operational_mandates"
REVISION_TABLE = "public.operational_mandate_revisions"
INSTRUMENT_TABLE = "public.operational_mandate_revision_instruments"
MANDATE_TABLES = (MANDATE_TABLE, REVISION_TABLE, INSTRUMENT_TABLE)
DATA_API_ROLES = ("anon", "authenticated", "service_role")
TRIGGER_FUNCTIONS = (
    "public.validate_operational_mandate_revision_insert()",
    "public.validate_operational_mandate_instrument_insert()",
    "public.validate_operational_mandate_insert()",
    "public.ensure_operational_mandate_revision_published()",
    "public.protect_operational_mandate()",
    "public.reject_operational_mandate_revision_change()",
    "public.reject_operational_mandate_instrument_change()",
)


def _insert_revision(
    connection: Connection[Any],
    *,
    mandate_id: UUID,
    revision: int,
    actor_id: UUID,
    schema_version: int = 1,
    checksum: str = CHECKSUM_A,
    name: str = "Primary mandate",
    description: str = "Bounded Binance Spot authority",
    created_at: datetime = BASE_TIME,
) -> None:
    connection.execute(
        """
        insert into public.operational_mandate_revisions (
            mandate_id,
            revision,
            schema_version,
            specification_checksum,
            name,
            description,
            created_by,
            created_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            mandate_id,
            revision,
            schema_version,
            checksum,
            name,
            description,
            actor_id,
            created_at,
        ),
    )


def _insert_instrument(
    connection: Connection[Any],
    *,
    mandate_id: UUID,
    revision: int,
    base_asset: str = "BTC",
    quote_asset: str = "USDT",
    exchange: str = "binance",
    market_type: str = "spot",
) -> None:
    connection.execute(
        """
        insert into public.operational_mandate_revision_instruments (
            mandate_id,
            revision,
            exchange,
            market_type,
            base_asset,
            quote_asset
        )
        values (%s, %s, %s, %s, %s, %s)
        """,
        (
            mandate_id,
            revision,
            exchange,
            market_type,
            base_asset,
            quote_asset,
        ),
    )


def _insert_aggregate(
    connection: Connection[Any],
    *,
    mandate_id: UUID,
    actor_id: UUID,
    state: str = "DRAFT",
    current_revision: int = 1,
    record_version: int = 1,
    idempotency_key: str = "create-1",
    fingerprint: str = FINGERPRINT_A,
    approved_revision: int | None = None,
    approved_checksum: str | None = None,
    approved_by: UUID | None = None,
    approved_at: datetime | None = None,
    archived_by: UUID | None = None,
    archived_at: datetime | None = None,
) -> None:
    connection.execute(
        """
        insert into public.operational_mandates (
            mandate_id,
            state,
            current_revision,
            record_version,
            approved_revision,
            approved_checksum,
            created_by,
            created_at,
            approved_by,
            approved_at,
            archived_by,
            archived_at,
            create_idempotency_key,
            create_request_fingerprint
        )
        values (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            mandate_id,
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
            idempotency_key,
            fingerprint,
        ),
    )


def _create_draft(
    database_url: str,
    actor_id: UUID,
    *,
    mandate_id: UUID | None = None,
    idempotency_key: str = "create-1",
    checksum: str = CHECKSUM_A,
) -> UUID:
    value = mandate_id or uuid4()
    with psycopg.connect(database_url) as connection:
        _insert_revision(
            connection,
            mandate_id=value,
            revision=1,
            actor_id=actor_id,
            checksum=checksum,
        )
        _insert_instrument(connection, mandate_id=value, revision=1)
        _insert_aggregate(
            connection,
            mandate_id=value,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
    return value


def _approve(
    connection: Connection[Any],
    *,
    mandate_id: UUID,
    actor_id: UUID,
    checksum: str = CHECKSUM_A,
    approved_at: datetime = BASE_TIME + timedelta(seconds=1),
) -> None:
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
        (checksum, actor_id, approved_at, mandate_id),
    )


def test_schema_has_exact_tables_columns_and_revision_bindings(
    database_url: str,
) -> None:
    expected_columns = {
        "operational_mandates": {
            "mandate_id",
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
            "create_request_fingerprint",
        },
        "operational_mandate_revisions": {
            "mandate_id",
            "revision",
            "schema_version",
            "specification_checksum",
            "name",
            "description",
            "created_by",
            "created_at",
        },
        "operational_mandate_revision_instruments": {
            "mandate_id",
            "revision",
            "exchange",
            "market_type",
            "base_asset",
            "quote_asset",
        },
    }

    with psycopg.connect(database_url, autocommit=True) as connection:
        rows = connection.execute(
            """
            select table_name, column_name
            from information_schema.columns
            where table_schema = 'public'
              and table_name in (
                  'operational_mandates',
                  'operational_mandate_revisions',
                  'operational_mandate_revision_instruments'
              )
            """
        ).fetchall()

        actual: dict[str, set[str]] = {}
        for table_name, column_name in rows:
            actual.setdefault(table_name, set()).add(column_name)
        assert actual == expected_columns

        foreign_keys = connection.execute(
            """
            select constraint_name, is_deferrable, initially_deferred
            from information_schema.table_constraints
            where constraint_schema = 'public'
              and constraint_name in (
                  'operational_mandate_revisions_mandate_id_fkey',
                  'operational_mandates_current_revision_fkey',
                  'operational_mandates_approved_revision_fkey'
              )
            order by constraint_name
            """
        ).fetchall()

        assert foreign_keys == [
            ("operational_mandate_revisions_mandate_id_fkey", "YES", "YES"),
            ("operational_mandates_approved_revision_fkey", "NO", "NO"),
            ("operational_mandates_current_revision_fkey", "NO", "NO"),
        ]


def test_schema_has_rls_no_policies_and_closed_data_api_acl(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            """
            select class.relname, class.relrowsecurity
            from pg_catalog.pg_class as class
            join pg_catalog.pg_namespace as namespace
              on namespace.oid = class.relnamespace
            where namespace.nspname = 'public'
              and class.relname in (
                  'operational_mandates',
                  'operational_mandate_revisions',
                  'operational_mandate_revision_instruments'
              )
            order by class.relname
            """
        ).fetchall() == [
            ("operational_mandate_revision_instruments", True),
            ("operational_mandate_revisions", True),
            ("operational_mandates", True),
        ]

        assert (
            connection.execute(
                """
            select tablename, policyname
            from pg_catalog.pg_policies
            where schemaname = 'public'
              and tablename like 'operational_mandate%'
            """
            ).fetchall()
            == []
        )

        assert (
            connection.execute(
                """
            select table_name, privilege_type
            from information_schema.role_table_grants
            where table_schema = 'public'
              and table_name like 'operational_mandate%'
              and grantee = 'PUBLIC'
            """
            ).fetchall()
            == []
        )

        for role in DATA_API_ROLES:
            for table in MANDATE_TABLES:
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

        for function_name in TRIGGER_FUNCTIONS:
            assert connection.execute(
                "select has_function_privilege('public', %s, 'EXECUTE')",
                (function_name,),
            ).fetchone() == (False,)


def test_create_draft_publishes_revision_and_instruments_atomically(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            """
            select state, current_revision, record_version
            from public.operational_mandates
            where mandate_id = %s
            """,
            (mandate_id,),
        ).fetchone() == ("DRAFT", 1, 1)
        assert connection.execute(
            """
            select count(*)
            from public.operational_mandate_revision_instruments
            where mandate_id = %s and revision = 1
            """,
            (mandate_id,),
        ).fetchone() == (1,)


def test_persistence_does_not_require_current_admin_membership(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, auth_user_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            "select count(*) from public.app_admins where user_id = %s",
            (auth_user_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "select created_by from public.operational_mandates where mandate_id = %s",
            (mandate_id,),
        ).fetchone() == (auth_user_id,)


@pytest.mark.parametrize("state", ["APPROVED", "ARCHIVED"])
def test_direct_initial_terminal_state_is_rejected(
    database_url: str,
    admin_user_id: UUID,
    state: str,
) -> None:
    mandate_id = uuid4()
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url) as connection:
            _insert_revision(
                connection,
                mandate_id=mandate_id,
                revision=1,
                actor_id=admin_user_id,
            )
            _insert_instrument(connection, mandate_id=mandate_id, revision=1)
            _insert_aggregate(
                connection,
                mandate_id=mandate_id,
                actor_id=admin_user_id,
                state=state,
            )


def test_initial_revision_requires_same_actor_and_at_least_one_instrument(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    other_actor = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, other_actor)

    for revision_actor, add_instrument in (
        (other_actor, True),
        (admin_user_id, False),
    ):
        mandate_id = uuid4()
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(database_url) as connection:
                _insert_revision(
                    connection,
                    mandate_id=mandate_id,
                    revision=1,
                    actor_id=revision_actor,
                )
                if add_instrument:
                    _insert_instrument(connection, mandate_id=mandate_id, revision=1)
                _insert_aggregate(
                    connection,
                    mandate_id=mandate_id,
                    actor_id=admin_user_id,
                    idempotency_key=f"actor-{revision_actor}",
                )


def test_revision_rejects_more_than_one_hundred_instruments(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = uuid4()
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url) as connection:
            _insert_revision(
                connection,
                mandate_id=mandate_id,
                revision=1,
                actor_id=admin_user_id,
            )
            for index in range(101):
                _insert_instrument(
                    connection,
                    mandate_id=mandate_id,
                    revision=1,
                    base_asset=f"A{index}",
                )


@pytest.mark.parametrize(
    ("exchange", "market_type", "base_asset", "quote_asset"),
    [
        ("kraken", "spot", "BTC", "USDT"),
        ("binance", "futures", "BTC", "USDT"),
        ("binance", "spot", "btc", "USDT"),
        ("binance", "spot", "BTC/ETH", "USDT"),
        ("binance", "spot", "BTC", "BTC"),
    ],
)
def test_instrument_capability_and_asset_shape_are_closed(
    database_url: str,
    admin_user_id: UUID,
    exchange: str,
    market_type: str,
    base_asset: str,
    quote_asset: str,
) -> None:
    mandate_id = uuid4()
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url) as connection:
            _insert_revision(
                connection,
                mandate_id=mandate_id,
                revision=1,
                actor_id=admin_user_id,
            )
            _insert_instrument(
                connection,
                mandate_id=mandate_id,
                revision=1,
                exchange=exchange,
                market_type=market_type,
                base_asset=base_asset,
                quote_asset=quote_asset,
            )


@pytest.mark.parametrize(
    ("schema_version", "checksum", "name", "description"),
    [
        (2, CHECKSUM_A, "Valid", ""),
        (1, "A" * 64, "Valid", ""),
        (1, "a" * 63, "Valid", ""),
        (1, CHECKSUM_A, "", ""),
        (1, CHECKSUM_A, " Leading", ""),
        (1, CHECKSUM_A, "Trailing ", ""),
        (1, CHECKSUM_A, "Line\rbreak", ""),
        (1, CHECKSUM_A, "Valid", "x" * 1001),
        (1, CHECKSUM_A, "Valid", "Line\rbreak"),
    ],
)
def test_revision_contract_fields_are_strictly_validated(
    database_url: str,
    admin_user_id: UUID,
    schema_version: int,
    checksum: str,
    name: str,
    description: str,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_revision(
                connection,
                mandate_id=uuid4(),
                revision=1,
                actor_id=admin_user_id,
                schema_version=schema_version,
                checksum=checksum,
                name=name,
                description=description,
            )


def test_actor_scoped_idempotency_is_unique_and_cross_actor_safe(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    other_actor = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, other_actor)

    _create_draft(database_url, admin_user_id, idempotency_key="same-key")

    with pytest.raises(psycopg.errors.UniqueViolation):
        _create_draft(database_url, admin_user_id, idempotency_key="same-key")

    _create_draft(database_url, other_actor, idempotency_key="same-key")


@pytest.mark.parametrize(
    ("idempotency_key", "fingerprint"),
    [
        (" bad", FINGERPRINT_A),
        ("bad ", FINGERPRINT_A),
        ("bad key", FINGERPRINT_A),
        ("x" * 129, FINGERPRINT_A),
        ("valid-key", "A" * 64),
        ("valid-key", "a" * 63),
    ],
)
def test_create_tokens_are_strictly_validated(
    database_url: str,
    admin_user_id: UUID,
    idempotency_key: str,
    fingerprint: str,
) -> None:
    mandate_id = uuid4()
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url) as connection:
            _insert_revision(
                connection,
                mandate_id=mandate_id,
                revision=1,
                actor_id=admin_user_id,
            )
            _insert_instrument(connection, mandate_id=mandate_id, revision=1)
            _insert_aggregate(
                connection,
                mandate_id=mandate_id,
                actor_id=admin_user_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )


def test_draft_revision_append_publishes_exactly_next_revision(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)

    with psycopg.connect(database_url) as connection:
        _insert_revision(
            connection,
            mandate_id=mandate_id,
            revision=2,
            actor_id=admin_user_id,
            checksum=CHECKSUM_B,
            name="Revised mandate",
        )
        _insert_instrument(
            connection,
            mandate_id=mandate_id,
            revision=2,
            base_asset="ETH",
        )
        connection.execute(
            """
            update public.operational_mandates
            set current_revision = 2,
                record_version = 2
            where mandate_id = %s
            """,
            (mandate_id,),
        )

    with psycopg.connect(database_url, autocommit=True) as connection:
        assert connection.execute(
            """
            select state, current_revision, record_version
            from public.operational_mandates
            where mandate_id = %s
            """,
            (mandate_id,),
        ).fetchone() == ("DRAFT", 2, 2)


def test_revision_gap_and_unpublished_revision_are_rejected(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)

    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url) as connection:
            _insert_revision(
                connection,
                mandate_id=mandate_id,
                revision=3,
                actor_id=admin_user_id,
            )

    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url) as connection:
            _insert_revision(
                connection,
                mandate_id=mandate_id,
                revision=2,
                actor_id=admin_user_id,
                checksum=CHECKSUM_B,
            )
            _insert_instrument(connection, mandate_id=mandate_id, revision=2)


def test_committed_orphan_revision_is_impossible(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    with pytest.raises(psycopg.Error):
        with psycopg.connect(database_url) as connection:
            _insert_revision(
                connection,
                mandate_id=uuid4(),
                revision=1,
                actor_id=admin_user_id,
            )


def test_published_revision_rejects_late_instrument_insert(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            _insert_instrument(
                connection,
                mandate_id=mandate_id,
                revision=1,
                base_asset="ETH",
            )


@pytest.mark.parametrize("target_state", ["APPROVED", "ARCHIVED"])
def test_revision_append_is_forbidden_after_draft(
    database_url: str,
    admin_user_id: UUID,
    target_state: str,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        if target_state == "APPROVED":
            _approve(connection, mandate_id=mandate_id, actor_id=admin_user_id)
        else:
            connection.execute(
                """
                update public.operational_mandates
                set state = 'ARCHIVED',
                    record_version = 2,
                    archived_by = %s,
                    archived_at = %s
                where mandate_id = %s
                """,
                (admin_user_id, BASE_TIME + timedelta(seconds=1), mandate_id),
            )

        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            _insert_revision(
                connection,
                mandate_id=mandate_id,
                revision=2,
                actor_id=admin_user_id,
            )


@pytest.mark.parametrize(
    "statement",
    [
        "update public.operational_mandate_revisions set name = 'Changed'",
        "delete from public.operational_mandate_revisions",
        "update public.operational_mandate_revision_instruments set base_asset = 'ETH'",
        "delete from public.operational_mandate_revision_instruments",
        "delete from public.operational_mandates",
    ],
)
def test_historical_rows_and_aggregate_delete_are_forbidden(
    database_url: str,
    admin_user_id: UUID,
    statement: str,
) -> None:
    _create_draft(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(statement)


@pytest.mark.parametrize(
    "assignment",
    [
        "mandate_id = gen_random_uuid()",
        "created_by = gen_random_uuid()",
        "created_at = created_at + interval '1 second'",
        "create_idempotency_key = 'changed-key'",
        "create_request_fingerprint = repeat('d', 64)",
    ],
)
def test_aggregate_creation_identity_is_immutable(
    database_url: str,
    admin_user_id: UUID,
    assignment: str,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                f"""
                update public.operational_mandates
                set {assignment}, record_version = 2
                where mandate_id = %s
                """,
                (mandate_id,),
            )


@pytest.mark.parametrize(
    "update_sql",
    [
        "set record_version = record_version",
        "set state = 'ARCHIVED', record_version = record_version",
        "set state = 'ARCHIVED', record_version = record_version + 2",
        "set current_revision = current_revision + 2, record_version = record_version + 1",
    ],
)
def test_version_and_revision_changes_must_be_exact(
    database_url: str,
    admin_user_id: UUID,
    update_sql: str,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(
                f"""
                update public.operational_mandates
                {update_sql}
                where mandate_id = %s
                """,
                (mandate_id,),
            )


def test_approval_seals_exact_current_revision_and_checksum(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)
    approved_at = BASE_TIME + timedelta(seconds=1)
    with psycopg.connect(database_url, autocommit=True) as connection:
        _approve(
            connection,
            mandate_id=mandate_id,
            actor_id=admin_user_id,
            approved_at=approved_at,
        )
        assert connection.execute(
            """
            select
                state,
                current_revision,
                record_version,
                approved_revision,
                approved_checksum,
                approved_by,
                approved_at
            from public.operational_mandates
            where mandate_id = %s
            """,
            (mandate_id,),
        ).fetchone() == (
            "APPROVED",
            1,
            2,
            1,
            CHECKSUM_A,
            admin_user_id,
            approved_at,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "approved_checksum = null",
        "approved_revision = 2",
        "approved_checksum = repeat('b', 64)",
        "current_revision = 2, approved_revision = 2",
    ],
)
def test_invalid_approval_shapes_are_rejected(
    database_url: str,
    admin_user_id: UUID,
    mutation: str,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute(
                f"""
                update public.operational_mandates
                set state = 'APPROVED',
                    record_version = 2,
                    approved_revision = 1,
                    approved_checksum = repeat('a', 64),
                    approved_by = %s,
                    approved_at = %s,
                    {mutation}
                where mandate_id = %s
                """,
                (
                    admin_user_id,
                    BASE_TIME + timedelta(seconds=1),
                    mandate_id,
                ),
            )


def test_approved_mandate_cannot_return_to_draft(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        _approve(connection, mandate_id=mandate_id, actor_id=admin_user_id)
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.operational_mandates
                set state = 'DRAFT',
                    record_version = 3,
                    approved_revision = null,
                    approved_checksum = null,
                    approved_by = null,
                    approved_at = null
                where mandate_id = %s
                """,
                (mandate_id,),
            )


def test_draft_and_approved_archive_paths_preserve_history(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    draft_id = _create_draft(
        database_url,
        admin_user_id,
        idempotency_key="archive-draft",
    )
    approved_id = _create_draft(
        database_url,
        admin_user_id,
        idempotency_key="archive-approved",
    )
    approved_at = BASE_TIME + timedelta(seconds=1)
    archived_at = BASE_TIME + timedelta(seconds=2)

    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            update public.operational_mandates
            set state = 'ARCHIVED',
                record_version = 2,
                archived_by = %s,
                archived_at = %s
            where mandate_id = %s
            """,
            (admin_user_id, archived_at, draft_id),
        )
        _approve(
            connection,
            mandate_id=approved_id,
            actor_id=admin_user_id,
            approved_at=approved_at,
        )
        connection.execute(
            """
            update public.operational_mandates
            set state = 'ARCHIVED',
                record_version = 3,
                archived_by = %s,
                archived_at = %s
            where mandate_id = %s
            """,
            (admin_user_id, archived_at, approved_id),
        )

        assert connection.execute(
            """
            select state, approved_revision, approved_checksum, record_version
            from public.operational_mandates
            where mandate_id = %s
            """,
            (draft_id,),
        ).fetchone() == ("ARCHIVED", None, None, 2)
        assert connection.execute(
            """
            select state, approved_revision, approved_checksum, record_version
            from public.operational_mandates
            where mandate_id = %s
            """,
            (approved_id,),
        ).fetchone() == ("ARCHIVED", 1, CHECKSUM_A, 3)


def test_archive_shape_chronology_and_terminal_state_are_enforced(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    for key, archive_values in (
        ("partial", (admin_user_id, None)),
        (
            "chronology",
            (admin_user_id, BASE_TIME - timedelta(seconds=1)),
        ),
    ):
        mandate_id = _create_draft(
            database_url,
            admin_user_id,
            idempotency_key=key,
        )
        with psycopg.connect(database_url, autocommit=True) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(
                    """
                    update public.operational_mandates
                    set state = 'ARCHIVED',
                        record_version = 2,
                        archived_by = %s,
                        archived_at = %s
                    where mandate_id = %s
                    """,
                    (*archive_values, mandate_id),
                )

    terminal_id = _create_draft(
        database_url,
        admin_user_id,
        idempotency_key="terminal",
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            update public.operational_mandates
            set state = 'ARCHIVED',
                record_version = 2,
                archived_by = %s,
                archived_at = %s
            where mandate_id = %s
            """,
            (admin_user_id, BASE_TIME + timedelta(seconds=1), terminal_id),
        )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.operational_mandates
                set record_version = 3
                where mandate_id = %s
                """,
                (terminal_id,),
            )


def test_archiving_approved_mandate_cannot_rewrite_approval_metadata(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)
    other_actor = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, other_actor)
        _approve(connection, mandate_id=mandate_id, actor_id=admin_user_id)
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.operational_mandates
                set state = 'ARCHIVED',
                    record_version = 3,
                    approved_by = %s,
                    archived_by = %s,
                    archived_at = %s
                where mandate_id = %s
                """,
                (
                    other_actor,
                    admin_user_id,
                    BASE_TIME + timedelta(seconds=2),
                    mandate_id,
                ),
            )


def test_archive_cannot_predate_approval(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    mandate_id = _create_draft(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        _approve(
            connection,
            mandate_id=mandate_id,
            actor_id=admin_user_id,
            approved_at=BASE_TIME + timedelta(seconds=2),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                update public.operational_mandates
                set state = 'ARCHIVED',
                    record_version = 3,
                    archived_by = %s,
                    archived_at = %s
                where mandate_id = %s
                """,
                (
                    admin_user_id,
                    BASE_TIME + timedelta(seconds=1),
                    mandate_id,
                ),
            )


def test_actor_foreign_keys_and_delete_restriction_preserve_history(
    database_url: str,
    admin_user_id: UUID,
) -> None:
    missing_actor = uuid4()
    mandate_id = uuid4()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(database_url) as connection:
            _insert_revision(
                connection,
                mandate_id=mandate_id,
                revision=1,
                actor_id=missing_actor,
            )

    persisted_id = _create_draft(database_url, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.RestrictViolation):
            connection.execute(
                "delete from auth.users where id = %s",
                (admin_user_id,),
            )
        assert connection.execute(
            "select count(*) from public.operational_mandates where mandate_id = %s",
            (persisted_id,),
        ).fetchone() == (1,)
