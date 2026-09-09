"""Static migration contract tests for Phase 7-11 runner control."""

from __future__ import annotations

import re
from dataclasses import fields
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.rows import dict_row

import app.operational_paper_session_runs as runs
from app.database import Database
from app.operational_paper_session_activations import OperationalPaperSessionActivation
from tests.test_operational_paper_session_activations_migration import (
    AUTHORIZED_AT,
    _valid_activation,
)
from tests.test_operational_paper_session_activations_migration import (
    _insert as _insert_activation,
)
from tests.test_operational_paper_session_activations_migration import (
    _row as _activation_row,
)

MIGRATION_PATH = (
    Path(__file__).parents[3] / "supabase/migrations/"
    "20260907000000_phase_7_11_operational_paper_session_run_epochs.sql"
)


def _migration() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _quoted(value: str) -> set[str]:
    return set(re.findall(r"'([A-Z][A-Z0-9_]*)'", value))


def _table_columns(text: str, table: str) -> tuple[str, ...]:
    start_token = f"create table public.{table} ("
    start = text.index(start_token)
    body = text[start:].splitlines()[1:]

    columns: list[str] = []

    for line in body:
        if line == ");":
            break

        if not line.startswith("    ") or line.startswith("        "):
            continue

        stripped = line.strip()

        if not stripped or stripped.startswith("constraint "):
            continue

        columns.append(stripped.split()[0])

    return tuple(columns)


def _function_body(text: str, name: str) -> str:
    start = text.index(f"create function public.{name}(")
    end = text.index("$function$;", start)
    return text[start:end]


def _check_body(text: str, constraint: str) -> str:
    start = text.index(f"constraint {constraint}")
    tail = text[start:]
    next_constraint = tail.find("\n    constraint ", 1)
    table_end = tail.find("\n);")

    candidates = [value for value in (next_constraint, table_end) if value >= 0]

    end = min(candidates)
    return tail[:end]


def test_schema_markers_and_relation_count_are_exact() -> None:
    text = _migration()

    markers = (
        "-- B1A-END",
        "-- B1B1-END",
        "-- B1B2A-END",
        "-- B1B2B-END",
    )

    positions = [text.index(marker) for marker in markers]

    assert positions == sorted(positions)
    assert all(text.count(marker) == 1 for marker in markers)

    tables = re.findall(
        r"(?m)^create table public\.([a-z0-9_]+) \(",
        text,
    )

    assert tables == [
        "operational_paper_session_run_epochs",
        "operational_paper_session_run_commands",
    ]


def test_epoch_column_contract_is_exact() -> None:
    assert _table_columns(
        _migration(),
        "operational_paper_session_run_epochs",
    ) == (
        "epoch_id",
        "schema_version",
        "run_contract_version",
        "desired_state",
        "observed_state",
        "record_version",
        "fencing_token",
        "activation_id",
        "activation_checksum",
        "materialization_id",
        "materialization_checksum",
        "authorization_id",
        "authorization_checksum",
        "profile_id",
        "profile_approved_revision",
        "profile_specification_checksum",
        "mandate_id",
        "mandate_approved_revision",
        "mandate_specification_checksum",
        "simulation_id",
        "session_id",
        "config_checksum",
        "epoch_checksum",
        "start_requested_by",
        "start_requested_at",
        "start_idempotency_key",
        "start_intent_fingerprint",
        "worker_id",
        "worker_claimed_at",
        "worker_heartbeat_at",
        "worker_lease_expires_at",
        "failure_code",
        "failure_at",
        "terminal_at",
    )


def test_command_column_contract_is_exact() -> None:
    assert _table_columns(
        _migration(),
        "operational_paper_session_run_commands",
    ) == (
        "command_id",
        "command_contract_version",
        "epoch_id",
        "epoch_checksum",
        "command_type",
        "desired_state",
        "expected_record_version",
        "resulting_record_version",
        "actor_id",
        "requested_at",
        "idempotency_key",
        "intent_fingerprint",
    )


def test_foreign_keys_and_unique_contracts_are_present() -> None:
    text = _migration()

    required = {
        "op_ps_run_epoch_activation_fkey",
        "op_ps_run_epoch_materialization_fkey",
        "op_ps_run_epoch_authorization_fkey",
        "op_ps_run_epoch_profile_revision_fkey",
        "op_ps_run_epoch_mandate_revision_fkey",
        "op_ps_run_epoch_simulation_fkey",
        "op_ps_run_epoch_start_requested_by_fkey",
        "op_ps_run_epoch_identity_key",
        "op_ps_run_epoch_actor_start_idempotency_key",
        "op_ps_run_command_epoch_identity_fkey",
        "op_ps_run_command_actor_fkey",
        "op_ps_run_command_actor_idempotency_key",
        "op_ps_run_command_epoch_result_version_key",
    }

    names = set(
        re.findall(
            r"(?m)^    constraint ([a-z0-9_]+)",
            text,
        )
    )

    assert required <= names

    assert "foreign key (epoch_id, epoch_checksum)" in text
    assert "unique (start_requested_by, start_idempotency_key)" in text
    assert "unique (actor_id, idempotency_key)" in text
    assert "unique (epoch_id, resulting_record_version)" in text


def test_index_contract_is_exact() -> None:
    text = _migration()

    indexes = set(
        re.findall(
            r"(?m)^create (?:unique )?index ([a-z0-9_]+)",
            text,
        )
    )

    assert indexes == {
        "op_ps_run_epoch_one_current_per_session_uidx",
        "op_ps_run_epoch_session_history_idx",
        "op_ps_run_epoch_activation_history_idx",
        "op_ps_run_epoch_worker_lease_idx",
        "op_ps_run_epoch_list_idx",
        "op_ps_run_command_epoch_history_idx",
        "op_ps_run_command_list_idx",
    }

    assert "where observed_state not in ('STOPPED', 'FAILED');" in text


def test_backend_only_rls_contract_is_closed() -> None:
    text = _migration()
    lower = text.lower()

    assert lower.count("enable row level security;") == 2
    assert "create policy" not in lower
    assert "grant " not in lower

    assert "from public, anon, authenticated, service_role;" in lower


def test_function_and_trigger_surface_is_exact() -> None:
    text = _migration()

    functions = set(
        re.findall(
            r"(?m)^create function public\.([a-z0-9_]+)\(",
            text,
        )
    )

    assert functions == {
        "op_ps_run_transition_allowed",
        "validate_op_ps_run_epoch_insert",
        "protect_op_ps_run_epoch",
        "op_ps_run_command_allowed",
        "validate_op_ps_run_command_insert",
        "protect_op_ps_run_command",
        "assert_op_ps_run_epoch_start_command",
        "assert_op_ps_run_command_applied",
    }

    regular = set(
        re.findall(
            r"(?m)^create trigger ([a-z0-9_]+)",
            text,
        )
    )
    deferred = set(
        re.findall(
            r"(?m)^create constraint trigger ([a-z0-9_]+)",
            text,
        )
    )

    assert regular == {
        "op_ps_run_epoch_validate_insert",
        "op_ps_run_epoch_protect",
        "op_ps_run_command_validate_insert",
        "op_ps_run_command_protect",
    }

    assert deferred == {
        "op_ps_run_epoch_start_command_required",
        "op_ps_run_command_applied",
    }

    assert text.count("deferrable initially deferred") == 2


def test_persisted_state_and_failure_vocabularies_are_closed() -> None:
    text = _migration()

    desired = _quoted(
        _check_body(
            text,
            "op_ps_run_epoch_desired_state_check",
        )
    )
    observed = _quoted(
        _check_body(
            text,
            "op_ps_run_epoch_observed_state_check",
        )
    )
    failures = _quoted(
        _check_body(
            text,
            "op_ps_run_epoch_failure_code_check",
        )
    )

    assert desired == {"RUNNING", "PAUSED", "STOPPED"}

    assert observed == {
        "PENDING",
        "STARTING",
        "RUNNING",
        "PAUSED",
        "RECOVERING",
        "STOPPING",
        "STOPPED",
        "FAILED",
    }

    assert failures == {
        "AUTHORITY_LOST",
        "ACTIVATION_REVOKED",
        "CONFIG_UNAVAILABLE",
        "CONFIG_IDENTITY_CONFLICT",
        "PLUGIN_UNAVAILABLE",
        "RAW_NOT_READY",
        "LOCAL_RUNNER_BUSY",
        "LOCAL_STATE_INVALID",
        "LEASE_LOST",
        "INTERNAL_ERROR",
    }


def test_observed_transition_graph_matches_domain_contract() -> None:
    body = _function_body(
        _migration(),
        "op_ps_run_transition_allowed",
    )

    expected_fragments = (
        "when 'PENDING' then",
        "new_state in ('STARTING', 'PAUSED', 'STOPPED', 'FAILED')",
        "when 'STARTING' then",
        "'RUNNING',",
        "'PAUSED',",
        "'RECOVERING',",
        "'STOPPING',",
        "'FAILED'",
        "when 'RUNNING' then",
        "when 'PAUSED' then",
        "new_state in ('STARTING', 'STOPPED', 'FAILED')",
        "when 'RECOVERING' then",
        "when 'STOPPING' then",
        "new_state in ('RECOVERING', 'STOPPED', 'FAILED')",
        "when 'STOPPED' then false",
        "when 'FAILED' then false",
    )

    for fragment in expected_fragments:
        assert fragment in body


def test_command_matrix_and_targets_match_domain_contract() -> None:
    text = _migration()
    body = _function_body(text, "op_ps_run_command_allowed")

    assert "when 'START' then false" in body
    assert "when 'RESUME' then" in body
    assert "observed_state = 'PAUSED'" in body

    for state in (
        "PENDING",
        "STARTING",
        "RUNNING",
        "RECOVERING",
    ):
        assert f"'{state}'" in body

    command_check = _quoted(
        _check_body(
            text,
            "op_ps_run_command_type_check",
        )
    )
    assert command_check == {"START", "PAUSE", "RESUME", "STOP"}

    target = _check_body(
        text,
        "op_ps_run_command_target_check",
    )

    assert "command_type in ('START', 'RESUME')" in target
    assert "desired_state = 'RUNNING'" in target
    assert "command_type = 'PAUSE'" in target
    assert "desired_state = 'PAUSED'" in target
    assert "command_type = 'STOP'" in target
    assert "desired_state = 'STOPPED'" in target


def test_worker_fencing_and_lease_guards_are_persisted() -> None:
    text = _migration()
    protect = _function_body(text, "protect_op_ps_run_epoch")

    required = (
        "new.fencing_token < old.fencing_token",
        "new.fencing_token > old.fencing_token + 1",
        "new.fencing_token = old.fencing_token + 1",
        "operational_paper_session_run_epoch_new_claim_invalid",
        "operational_paper_session_run_epoch_recovery_invalid",
        "operational_paper_session_run_epoch_worker_requires_new_fence",
        "operational_paper_session_run_epoch_worker_identity_changed",
        "operational_paper_session_run_epoch_lease_renewal_invalid",
        "operational_paper_session_run_epoch_worker_release_invalid",
    )

    for fragment in required:
        assert fragment in protect

    assert "worker_heartbeat_at < worker_lease_expires_at" in text
    assert "fencing_token >= 0" in text


def test_command_history_is_append_only_and_transactionally_applied() -> None:
    text = _migration()

    protect = _function_body(
        text,
        "protect_op_ps_run_command",
    )

    assert "command_delete_forbidden" in protect
    assert "command_update_forbidden" in protect

    assert "create constraint trigger op_ps_run_epoch_start_command_required" in text
    assert "create constraint trigger op_ps_run_command_applied" in text

    assert "operational_paper_session_run_epoch_start_command_required" in text
    assert "operational_paper_session_run_command_not_applied" in text


def test_start_insert_revalidates_upstream_authority_in_lock_order() -> None:
    body = _function_body(
        _migration(),
        "validate_op_ps_run_epoch_insert",
    )

    anchors = (
        "from public.simulation_runs as simulation",
        "from public.operational_paper_capital_authorizations as capital_authorization",
        "from public.operational_paper_session_profiles as profile",
        "from public.operational_mandates as mandate",
        "from public.operational_paper_session_materializations as materialization",
        "from public.operational_paper_session_activations as activation",
    )

    positions = [body.index(anchor) for anchor in anchors]

    assert positions == sorted(positions)
    assert body.count("for update;") >= 6

    required = (
        "simulation_not_active",
        "authorization_not_authorized",
        "profile_not_approved",
        "mandate_not_approved",
        "materialization_not_materialized",
        "activation_not_authorized",
        "authorization_binding_mismatch",
        "profile_binding_mismatch",
        "mandate_binding_mismatch",
        "materialization_binding_mismatch",
        "activation_binding_mismatch",
        "quote_asset_binding_mismatch",
    )

    for fragment in required:
        assert fragment in body


def test_migration_excludes_forbidden_runtime_and_secret_surfaces() -> None:
    lower = _migration().lower()

    forbidden = (
        "create policy",
        "grant ",
        "double precision",
        "api_key",
        "password",
        "secret",
        "credential",
        "adt_data_dir",
        "filesystem_path",
        "hostname",
        "process_id",
        "binance order",
    )

    for fragment in forbidden:
        assert fragment not in lower


# PostgreSQL helpers: serialization belongs to these tests, not a Gate 2C repository.
EPOCHS = "operational_paper_session_run_epochs"
COMMANDS = "operational_paper_session_run_commands"
START_AT = AUTHORIZED_AT + timedelta(seconds=1)


def _epoch_row(epoch: runs.OperationalPaperSessionRunEpoch) -> dict[str, object]:
    nested = {
        "authorization_binding",
        "profile_binding",
        "mandate_binding",
        "worker_claim",
        "failure",
    }
    row = {
        field.name: getattr(epoch, field.name)
        for field in fields(epoch)
        if field.name not in nested
    }
    row["desired_state"] = epoch.desired_state.value
    row["observed_state"] = epoch.observed_state.value
    row.update(
        authorization_id=epoch.authorization_binding.authorization_id,
        authorization_checksum=epoch.authorization_binding.authorization_checksum,
        profile_id=epoch.profile_binding.profile_id,
        profile_approved_revision=epoch.profile_binding.approved_revision,
        profile_specification_checksum=epoch.profile_binding.specification_checksum,
        mandate_id=epoch.mandate_binding.mandate_id,
        mandate_approved_revision=epoch.mandate_binding.approved_revision,
        mandate_specification_checksum=epoch.mandate_binding.specification_checksum,
    )
    claim = epoch.worker_claim
    row.update(
        worker_id=claim.worker_id if claim else None,
        worker_claimed_at=claim.claimed_at if claim else None,
        worker_heartbeat_at=claim.heartbeat_at if claim else None,
        worker_lease_expires_at=claim.lease_expires_at if claim else None,
        failure_code=epoch.failure.code.value if epoch.failure else None,
        failure_at=epoch.failure.failed_at if epoch.failure else None,
    )
    return row


def _command_row(command: runs.OperationalPaperSessionRunEpochCommand) -> dict[str, object]:
    row = {field.name: getattr(command, field.name) for field in fields(command)}
    row["command_type"] = command.command_type.value
    row["desired_state"] = command.desired_state.value
    # The domain represents START's absent predecessor as None; SQL uses zero.
    row["expected_record_version"] = (
        0 if command.expected_record_version is None else command.expected_record_version
    )
    return row


def _insert_run_row(
    connection: psycopg.Connection[object],
    table: str,
    row: dict[str, object],
) -> None:
    assert table in (EPOCHS, COMMANDS)
    connection.execute(
        sql.SQL("insert into public.{} ({}) values ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, row)),
            sql.SQL(", ").join(sql.Placeholder() for _ in row),
        ),
        tuple(row.values()),
    )


def _update_epoch_row(
    connection: psycopg.Connection[object],
    epoch_id: UUID,
    changes: dict[str, object],
) -> None:
    result = connection.execute(
        sql.SQL("update public.{} set {} where epoch_id = %s").format(
            sql.Identifier(EPOCHS),
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(key)) for key in changes),
        ),
        (*changes.values(), epoch_id),
    )
    assert result.rowcount == 1


def _persist_change(
    database_url: str,
    epoch: runs.OperationalPaperSessionRunEpoch,
    command: runs.OperationalPaperSessionRunEpochCommand | None = None,
) -> None:
    with psycopg.connect(database_url) as connection:
        if command is not None:
            _insert_run_row(connection, COMMANDS, _command_row(command))
        _update_epoch_row(connection, epoch.epoch_id, _epoch_row(epoch))
    _assert_stored_epoch(database_url, epoch)


def _assert_stored_epoch(database_url: str, epoch: runs.OperationalPaperSessionRunEpoch) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        assert connection.execute(
            "select * from public.operational_paper_session_run_epochs where epoch_id = %s",
            (epoch.epoch_id,),
        ).fetchone() == _epoch_row(epoch)


def _start(
    activation: OperationalPaperSessionActivation,
    *,
    key: str = "run:start",
) -> tuple[runs.OperationalPaperSessionRunEpoch, runs.OperationalPaperSessionRunEpochCommand]:
    return runs.start_operational_paper_session_run_epoch(
        epoch_id=uuid4(),
        command_id=uuid4(),
        specification=runs.build_operational_paper_session_run_epoch_specification(activation),
        start_intent=runs.OperationalPaperSessionRunEpochStartIntent(
            activation_id=activation.activation_id,
            activation_checksum=activation.activation_checksum,
        ),
        requested_by=activation.authorized_by,
        requested_at=START_AT,
        idempotency_key=key,
    )


def _persist_start(
    database_url: str,
    epoch: runs.OperationalPaperSessionRunEpoch,
    command: runs.OperationalPaperSessionRunEpochCommand,
) -> None:
    with psycopg.connect(database_url) as connection:
        _insert_run_row(connection, EPOCHS, _epoch_row(epoch))
        _insert_run_row(connection, COMMANDS, _command_row(command))


@pytest_asyncio.fixture
async def run_activation(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> OperationalPaperSessionActivation:
    activation = await _valid_activation(database_url, database, auth_user_id)
    with psycopg.connect(database_url) as connection:
        _insert_activation(connection, _activation_row(activation))
    return activation


@pytest.fixture
def pending_epoch(
    database_url: str,
    run_activation: OperationalPaperSessionActivation,
) -> runs.OperationalPaperSessionRunEpoch:
    epoch, command = _start(run_activation)
    _persist_start(database_url, epoch, command)
    return epoch


def _request(
    epoch: runs.OperationalPaperSessionRunEpoch,
    command_type: str,
    *,
    key: str | None = None,
) -> tuple[runs.OperationalPaperSessionRunEpoch, runs.OperationalPaperSessionRunEpochCommand]:
    return runs.request_operational_paper_session_run_epoch_command(
        epoch,
        command_id=uuid4(),
        intent=runs.OperationalPaperSessionRunEpochCommandIntent(
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=runs.OperationalPaperSessionRunCommandType(command_type),
            expected_record_version=epoch.record_version,
        ),
        actor_id=epoch.start_requested_by,
        requested_at=START_AT + timedelta(seconds=epoch.record_version),
        idempotency_key=key or f"run:{command_type}:{epoch.record_version}",
    )


def _reject_update(
    database_url: str,
    epoch: runs.OperationalPaperSessionRunEpoch,
    changes: dict[str, object],
    message: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.Error, match=message):
            with connection.transaction():
                _update_epoch_row(
                    connection,
                    epoch.epoch_id,
                    {"record_version": epoch.record_version + 1} | changes,
                )
    _assert_stored_epoch(database_url, epoch)


# Block A — actual catalog contract, independent of migration text parsing.
def test_postgres_exact_columns_and_types(database_url: str) -> None:
    epoch_types = {
        "epoch_id": "uuid",
        "schema_version": "integer",
        "run_contract_version": "integer",
        "desired_state": "text",
        "observed_state": "text",
        "record_version": "bigint",
        "fencing_token": "bigint",
        "activation_id": "uuid",
        "activation_checksum": "text",
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
        "epoch_checksum": "text",
        "start_requested_by": "uuid",
        "start_requested_at": "timestamp with time zone",
        "start_idempotency_key": "text",
        "start_intent_fingerprint": "text",
        "worker_id": "uuid",
        "worker_claimed_at": "timestamp with time zone",
        "worker_heartbeat_at": "timestamp with time zone",
        "worker_lease_expires_at": "timestamp with time zone",
        "failure_code": "text",
        "failure_at": "timestamp with time zone",
        "terminal_at": "timestamp with time zone",
    }
    command_types = {
        "command_id": "uuid",
        "command_contract_version": "integer",
        "epoch_id": "uuid",
        "epoch_checksum": "text",
        "command_type": "text",
        "desired_state": "text",
        "expected_record_version": "bigint",
        "resulting_record_version": "bigint",
        "actor_id": "uuid",
        "requested_at": "timestamp with time zone",
        "idempotency_key": "text",
        "intent_fingerprint": "text",
    }
    with psycopg.connect(database_url) as connection:
        for table, expected in ((EPOCHS, epoch_types), (COMMANDS, command_types)):
            actual = connection.execute(
                "select column_name, data_type from information_schema.columns "
                "where table_schema = 'public' and table_name = %s order by ordinal_position",
                (table,),
            ).fetchall()
            assert actual == list(expected.items())


def test_postgres_foreign_keys_and_unique_indexes(database_url: str) -> None:
    expected_fks = {
        "op_ps_run_epoch_activation_fkey": (
            "FOREIGN KEY (activation_id) REFERENCES "
            "operational_paper_session_activations(activation_id) ON DELETE RESTRICT"
        ),
        "op_ps_run_epoch_materialization_fkey": (
            "FOREIGN KEY (materialization_id) REFERENCES "
            "operational_paper_session_materializations(materialization_id) ON DELETE "
            "RESTRICT"
        ),
        "op_ps_run_epoch_authorization_fkey": (
            "FOREIGN KEY (authorization_id) REFERENCES "
            "operational_paper_capital_authorizations(authorization_id) ON DELETE "
            "RESTRICT"
        ),
        "op_ps_run_epoch_profile_revision_fkey": (
            "FOREIGN KEY (profile_id, profile_approved_revision, "
            "profile_specification_checksum) REFERENCES "
            "operational_paper_session_profile_revisions(profile_id, revision, "
            "specification_checksum) ON DELETE RESTRICT"
        ),
        "op_ps_run_epoch_mandate_revision_fkey": (
            "FOREIGN KEY (mandate_id, mandate_approved_revision, "
            "mandate_specification_checksum) REFERENCES "
            "operational_mandate_revisions(mandate_id, revision, specification_checksum) "
            "ON DELETE RESTRICT"
        ),
        "op_ps_run_epoch_simulation_fkey": (
            "FOREIGN KEY (simulation_id) REFERENCES simulation_runs(id) ON DELETE RESTRICT"
        ),
        "op_ps_run_epoch_start_requested_by_fkey": (
            "FOREIGN KEY (start_requested_by) REFERENCES auth.users(id) ON DELETE RESTRICT"
        ),
        "op_ps_run_command_epoch_identity_fkey": (
            "FOREIGN KEY (epoch_id, epoch_checksum) REFERENCES "
            "operational_paper_session_run_epochs(epoch_id, epoch_checksum) ON DELETE "
            "RESTRICT"
        ),
        "op_ps_run_command_actor_fkey": (
            "FOREIGN KEY (actor_id) REFERENCES auth.users(id) ON DELETE RESTRICT"
        ),
    }
    with psycopg.connect(database_url) as connection:
        assert (
            dict(
                connection.execute(
                    "select conname, pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid in (%s::regclass, %s::regclass) and contype = 'f'",
                    (EPOCHS, COMMANDS),
                ).fetchall()
            )
            == expected_fks
        )
        indexes: dict[str, str] = dict(
            connection.execute(
                "select indexrelid::regclass::text, pg_get_indexdef(indexrelid) "
                "from pg_index where indrelid in (%s::regclass, %s::regclass) and indisunique",
                (EPOCHS, COMMANDS),
            ).fetchall()
        )
        expected_keys = {
            "operational_paper_session_run_epochs_pkey": "(epoch_id)",
            "op_ps_run_epoch_identity_key": "(epoch_id, epoch_checksum)",
            "op_ps_run_epoch_actor_start_idempotency_key": (
                "(start_requested_by, start_idempotency_key)"
            ),
            "op_ps_run_epoch_one_current_per_session_uidx": "(session_id)",
            "operational_paper_session_run_commands_pkey": "(command_id)",
            "op_ps_run_command_actor_idempotency_key": "(actor_id, idempotency_key)",
            "op_ps_run_command_epoch_result_version_key": "(epoch_id, resulting_record_version)",
        }
        assert indexes.keys() == expected_keys.keys()
        for name, columns in expected_keys.items():
            assert f"USING btree {columns}" in indexes[name]
        assert indexes["op_ps_run_epoch_one_current_per_session_uidx"].endswith(
            "WHERE (observed_state <> ALL (ARRAY['STOPPED'::text, 'FAILED'::text]))"
        )


def test_postgres_rls_and_data_api_privileges(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        for table in (EPOCHS, COMMANDS):
            assert connection.execute(
                "select relrowsecurity from pg_class where oid = %s::regclass",
                (table,),
            ).fetchone() == (True,)
            assert connection.execute(
                "select count(*) from pg_policy where polrelid = %s::regclass",
                (table,),
            ).fetchone() == (0,)
            assert connection.execute(
                "select count(*) from pg_class c, lateral aclexplode("
                "coalesce(c.relacl, acldefault('r', c.relowner))) a "
                "where c.oid = %s::regclass and a.grantee = 0",
                (table,),
            ).fetchone() == (0,)
            for role in ("anon", "authenticated", "service_role"):
                assert connection.execute(
                    "select has_table_privilege(%s, %s, "
                    "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')",
                    (role, table),
                ).fetchone() == (False,)


# Block B — exact domain-produced START, committed and read on a new connection.
def test_postgres_valid_start_transaction(
    database_url: str,
    run_activation: OperationalPaperSessionActivation,
) -> None:
    epoch, command = _start(run_activation)
    _persist_start(database_url, epoch, command)
    _assert_stored_epoch(database_url, epoch)
    assert (
        epoch.observed_state.value,
        epoch.desired_state.value,
        epoch.record_version,
        epoch.fencing_token,
    ) == ("PENDING", "RUNNING", 1, 0)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        stored = connection.execute(
            "select * from public.operational_paper_session_run_commands where command_id = %s",
            (command.command_id,),
        ).fetchone()
    assert stored == _command_row(command)
    assert stored["expected_record_version"] == 0
    assert stored["resulting_record_version"] == 1


# Block C — deferred checks must fail at COMMIT, not be hidden by rollback.
def test_postgres_epoch_without_start_fails_at_commit(
    database_url: str,
    run_activation: OperationalPaperSessionActivation,
) -> None:
    epoch, _ = _start(run_activation)
    with psycopg.connect(database_url) as connection:
        _insert_run_row(connection, EPOCHS, _epoch_row(epoch))
        with pytest.raises(psycopg.Error, match="run_epoch_start_command_required"):
            connection.commit()
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "select count(*) from public.operational_paper_session_run_epochs"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"expected_record_version": 1}, "run_start_command_mismatch"),
        ({"resulting_record_version": 2}, "run_start_command_mismatch"),
        ({"idempotency_key": "mismatch"}, "run_start_command_mismatch"),
        ({"intent_fingerprint": "0" * 64}, "run_start_command_mismatch"),
        ({"requested_at": START_AT + timedelta(seconds=1)}, "run_start_command_mismatch"),
        ({"epoch_checksum": "0" * 64}, "run_command_epoch_checksum_mismatch"),
    ],
)
def test_postgres_mismatched_start_rejected(
    database_url: str,
    run_activation: OperationalPaperSessionActivation,
    changes: dict[str, object],
    message: str,
) -> None:
    epoch, command = _start(run_activation)
    with pytest.raises(psycopg.Error, match=message):
        with psycopg.connect(database_url) as connection:
            _insert_run_row(connection, EPOCHS, _epoch_row(epoch))
            _insert_run_row(connection, COMMANDS, _command_row(command) | changes)


def test_postgres_start_first_rejected_by_immediate_contract(
    database_url: str,
    run_activation: OperationalPaperSessionActivation,
) -> None:
    epoch, command = _start(run_activation)
    with psycopg.connect(database_url) as connection:
        connection.execute("set constraints all deferred")
        with pytest.raises(psycopg.Error, match="run_command_epoch_missing"):
            _insert_run_row(connection, COMMANDS, _command_row(command))
        connection.rollback()
    # Deferred constraint triggers do not defer the BEFORE trigger or immediate FK.
    _persist_start(database_url, epoch, command)
    _assert_stored_epoch(database_url, epoch)


def test_postgres_start_requires_matching_epoch_state_at_commit(
    database_url: str,
    run_activation: OperationalPaperSessionActivation,
) -> None:
    epoch, command = _start(run_activation)
    claimed = runs.claim_operational_paper_session_run_epoch(
        epoch,
        worker_id=uuid4(),
        claimed_at=START_AT + timedelta(seconds=1),
        lease_expires_at=START_AT + timedelta(seconds=61),
    )
    with psycopg.connect(database_url) as connection:
        _insert_run_row(connection, EPOCHS, _epoch_row(epoch))
        _insert_run_row(connection, COMMANDS, _command_row(command))
        _update_epoch_row(connection, epoch.epoch_id, _epoch_row(claimed))
        with pytest.raises(psycopg.Error, match="run_command_not_applied"):
            connection.commit()


# Block D — current-epoch exclusion and actor-scoped idempotency.
def _terminalize_pending(
    database_url: str,
    epoch: runs.OperationalPaperSessionRunEpoch,
) -> runs.OperationalPaperSessionRunEpoch:
    stopping, command = _request(epoch, "STOP")
    _persist_change(database_url, stopping, command)
    stopped = runs.settle_unclaimed_operational_paper_session_run_epoch(
        stopping,
        observed_at=START_AT + timedelta(seconds=10),
    )
    _persist_change(database_url, stopped)
    return stopped


def test_postgres_current_epoch_exclusion_and_new_start_after_terminal(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    run_activation: OperationalPaperSessionActivation,
) -> None:
    later, command = _start(run_activation, key="run:later")
    with pytest.raises(psycopg.errors.UniqueViolation) as caught:
        _persist_start(database_url, later, command)
    assert caught.value.diag.constraint_name == "op_ps_run_epoch_one_current_per_session_uidx"
    _assert_stored_epoch(database_url, pending_epoch)
    stopped = _terminalize_pending(database_url, pending_epoch)
    # This is a genuine later START, not reuse of the first epoch or command.
    later, command = runs.start_operational_paper_session_run_epoch(
        epoch_id=uuid4(),
        command_id=uuid4(),
        specification=runs.build_operational_paper_session_run_epoch_specification(run_activation),
        start_intent=runs.OperationalPaperSessionRunEpochStartIntent(
            activation_id=run_activation.activation_id,
            activation_checksum=run_activation.activation_checksum,
        ),
        requested_by=run_activation.authorized_by,
        requested_at=START_AT + timedelta(seconds=11),
        idempotency_key="run:later",
    )
    _persist_start(database_url, later, command)
    assert later.epoch_id != stopped.epoch_id
    assert later.session_id == stopped.session_id
    _assert_stored_epoch(database_url, stopped)
    _assert_stored_epoch(database_url, later)


def test_postgres_duplicate_start_actor_key_rejected_after_terminal(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    run_activation: OperationalPaperSessionActivation,
) -> None:
    _terminalize_pending(database_url, pending_epoch)
    duplicate, command = _start(run_activation, key=pending_epoch.start_idempotency_key)
    with pytest.raises(psycopg.errors.UniqueViolation) as caught:
        _persist_start(database_url, duplicate, command)
    assert caught.value.diag.constraint_name == "op_ps_run_epoch_actor_start_idempotency_key"


@pytest.mark.parametrize("same_actor", [True, False], ids=["duplicate-actor", "distinct-actor"])
def test_postgres_command_idempotency_is_actor_scoped(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    same_actor: bool,
) -> None:
    from tests.postgres_support import add_auth_user

    paused, pause = _request(pending_epoch, "PAUSE", key="shared-command-key")
    _persist_change(database_url, paused, pause)
    actor_id = pending_epoch.start_requested_by if same_actor else uuid4()
    if not same_actor:
        with psycopg.connect(database_url) as connection:
            add_auth_user(connection, actor_id)
    stopped, stop = runs.request_operational_paper_session_run_epoch_command(
        paused,
        command_id=uuid4(),
        intent=runs.OperationalPaperSessionRunEpochCommandIntent(
            epoch_id=paused.epoch_id,
            epoch_checksum=paused.epoch_checksum,
            command_type=runs.OperationalPaperSessionRunCommandType.STOP,
            expected_record_version=paused.record_version,
        ),
        actor_id=actor_id,
        requested_at=START_AT + timedelta(seconds=3),
        idempotency_key=pause.idempotency_key,
    )
    if same_actor:
        with pytest.raises(psycopg.errors.UniqueViolation) as caught:
            _persist_change(database_url, stopped, stop)
        assert caught.value.diag.constraint_name == "op_ps_run_command_actor_idempotency_key"
        _assert_stored_epoch(database_url, paused)
    else:
        _persist_change(database_url, stopped, stop)
        with psycopg.connect(database_url) as connection:
            assert connection.execute(
                "select actor_id from public.operational_paper_session_run_commands "
                "where idempotency_key = %s order by requested_at",
                (pause.idempotency_key,),
            ).fetchall() == [(pause.actor_id,), (actor_id,)]


# Block E — commands and desired-state changes linearize in one transaction.
def test_postgres_pause_resume_stop_command_sequence(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    paused, pause = _request(pending_epoch, "PAUSE")
    _persist_change(database_url, paused, pause)
    assert (paused.desired_state.value, paused.observed_state.value, paused.record_version) == (
        "PAUSED",
        "PENDING",
        2,
    )
    settled = runs.settle_unclaimed_operational_paper_session_run_epoch(
        paused,
        observed_at=START_AT + timedelta(seconds=2),
    )
    _persist_change(database_url, settled)
    resumed, resume = _request(settled, "RESUME")
    _persist_change(database_url, resumed, resume)
    assert (
        resumed.desired_state.value,
        resumed.observed_state.value,
        resume.expected_record_version,
        resumed.record_version,
    ) == ("RUNNING", "PAUSED", 3, 4)
    stopping, stop = _request(resumed, "STOP")
    _persist_change(database_url, stopping, stop)
    assert (
        stopping.desired_state.value,
        stopping.observed_state.value,
        stopping.record_version,
        stopping.terminal_at,
    ) == ("STOPPED", "PAUSED", 5, None)


def test_postgres_unapplied_pause_fails_at_deferred_commit(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    _, command = _request(pending_epoch, "PAUSE")
    with psycopg.connect(database_url) as connection:
        _insert_run_row(connection, COMMANDS, _command_row(command))
        with pytest.raises(psycopg.Error, match="run_command_not_applied"):
            connection.commit()
    _assert_stored_epoch(database_url, pending_epoch)
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "select count(*) from public.operational_paper_session_run_commands "
            "where command_id = %s",
            (command.command_id,),
        ).fetchone() == (0,)


def test_postgres_desired_mutation_requires_command(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    _reject_update(
        database_url, pending_epoch, {"desired_state": "PAUSED"}, "run_epoch_command_required"
    )


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"expected_record_version": 2}, "run_command_record_version_conflict"),
        ({"resulting_record_version": 3}, "run_command_record_version_conflict"),
        ({"desired_state": "STOPPED"}, "run_command_desired_transition_invalid"),
        ({"command_type": "RESUME", "desired_state": "RUNNING"}, "run_command_not_allowed"),
    ],
)
def test_postgres_invalid_administrative_command_rejected(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    changes: dict[str, object],
    message: str,
) -> None:
    _, command = _request(pending_epoch, "PAUSE")
    with pytest.raises(psycopg.Error, match=message):
        with psycopg.connect(database_url) as connection:
            _insert_run_row(connection, COMMANDS, _command_row(command) | changes)
    _assert_stored_epoch(database_url, pending_epoch)


# Block F — command history and immutable epoch provenance.
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_postgres_commands_are_append_only(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    operation: str,
) -> None:
    query = (
        "update public.operational_paper_session_run_commands set idempotency_key = 'changed' "
        "where epoch_id = %s"
        if operation == "update"
        else "delete from public.operational_paper_session_run_commands where epoch_id = %s"
    )
    with pytest.raises(psycopg.Error, match=f"run_command_{operation}_forbidden"):
        with psycopg.connect(database_url) as connection:
            connection.execute(query, (pending_epoch.epoch_id,))
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "select command_type, idempotency_key "
            "from public.operational_paper_session_run_commands "
            "where epoch_id = %s",
            (pending_epoch.epoch_id,),
        ).fetchall() == [("START", pending_epoch.start_idempotency_key)]


def test_postgres_epoch_delete_forbidden(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    with pytest.raises(psycopg.Error, match="run_epoch_delete_forbidden"):
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "delete from public.operational_paper_session_run_epochs where epoch_id = %s",
                (pending_epoch.epoch_id,),
            )
    _assert_stored_epoch(database_url, pending_epoch)


@pytest.mark.parametrize(
    "field, value",
    [
        ("epoch_checksum", "0" * 64),
        ("session_id", "0" * 64),
        ("config_checksum", "0" * 64),
        ("activation_id", UUID(int=123)),
        ("activation_checksum", "0" * 64),
        ("materialization_id", UUID(int=123)),
        ("materialization_checksum", "0" * 64),
        ("authorization_id", UUID(int=123)),
        ("authorization_checksum", "0" * 64),
        ("profile_id", UUID(int=123)),
        ("profile_approved_revision", 99),
        ("profile_specification_checksum", "0" * 64),
        ("mandate_id", UUID(int=123)),
        ("mandate_approved_revision", 99),
        ("mandate_specification_checksum", "0" * 64),
        ("simulation_id", UUID(int=123)),
        ("start_requested_by", UUID(int=123)),
        ("start_requested_at", START_AT + timedelta(seconds=1)),
        ("start_idempotency_key", "changed"),
        ("start_intent_fingerprint", "0" * 64),
    ],
)
def test_postgres_epoch_provenance_immutable(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    field: str,
    value: object,
) -> None:
    _reject_update(
        database_url, pending_epoch, {field: value}, "run_epoch_immutable_fields_changed"
    )


@pytest.mark.parametrize(
    "version, message",
    [
        (1, "run_epoch_record_version_conflict"),
        (3, "run_epoch_record_version_conflict"),
        (2, "run_epoch_noop_mutation"),
    ],
)
def test_postgres_record_version_guards(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    version: int,
    message: str,
) -> None:
    _reject_update(database_url, pending_epoch, {"record_version": version}, message)


@pytest.mark.parametrize("terminal", ["STOPPED", "FAILED"])
@pytest.mark.parametrize("reopen", [False, True], ids=["mutate", "reopen"])
def test_postgres_terminal_epochs_never_mutate_or_reopen(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    terminal: str,
    reopen: bool,
) -> None:
    if terminal == "STOPPED":
        epoch = _terminalize_pending(database_url, pending_epoch)
    else:
        epoch = runs.fail_unclaimed_operational_paper_session_run_epoch(
            pending_epoch,
            code=runs.OperationalPaperSessionRunFailureCode.RAW_NOT_READY,
            failed_at=START_AT + timedelta(seconds=1),
        )
        _persist_change(database_url, epoch)
    changes: dict[str, object] = {}
    if reopen:
        changes.update(
            observed_state="PENDING",
            desired_state="RUNNING",
            terminal_at=None,
            failure_code=None,
            failure_at=None,
        )
    _reject_update(database_url, epoch, changes, "run_epoch_terminal")


# Block G — worker claims, heartbeat and fencing are separate from record versions.
@pytest.fixture
def claimed_epoch(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> runs.OperationalPaperSessionRunEpoch:
    claimed = runs.claim_operational_paper_session_run_epoch(
        pending_epoch,
        worker_id=uuid4(),
        claimed_at=START_AT + timedelta(seconds=1),
        lease_expires_at=START_AT + timedelta(seconds=61),
    )
    _persist_change(database_url, claimed)
    return claimed


@pytest.fixture
def running_epoch(
    database_url: str,
    claimed_epoch: runs.OperationalPaperSessionRunEpoch,
) -> runs.OperationalPaperSessionRunEpoch:
    assert claimed_epoch.worker_claim is not None
    running = runs.mark_operational_paper_session_run_epoch_running(
        claimed_epoch,
        worker_id=claimed_epoch.worker_claim.worker_id,
        fencing_token=claimed_epoch.fencing_token,
        observed_at=START_AT + timedelta(seconds=2),
    )
    _persist_change(database_url, running)
    return running


def test_postgres_initial_claim_and_heartbeat(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
    claimed_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    claim = claimed_epoch.worker_claim
    assert claim is not None
    assert claimed_epoch.epoch_id == pending_epoch.epoch_id
    assert (
        claimed_epoch.observed_state.value,
        claimed_epoch.fencing_token,
        claimed_epoch.record_version,
    ) == ("STARTING", 1, 2)
    assert claim.claimed_at == claim.heartbeat_at < claim.lease_expires_at
    renewed = runs.renew_operational_paper_session_run_worker_claim(
        claimed_epoch,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        heartbeat_at=START_AT + timedelta(seconds=10),
        lease_expires_at=START_AT + timedelta(seconds=70),
    )
    _persist_change(database_url, renewed)
    assert renewed.worker_claim is not None
    assert renewed.worker_claim.worker_id == claim.worker_id
    assert renewed.fencing_token == claimed_epoch.fencing_token
    assert renewed.record_version == claimed_epoch.record_version + 1
    assert renewed.worker_claim.heartbeat_at > claim.heartbeat_at
    assert renewed.worker_claim.lease_expires_at > claim.lease_expires_at


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"worker_id": UUID(int=123)}, "run_epoch_worker_identity_changed"),
        ({"fencing_token": 0}, "run_epoch_fencing_token_invalid"),
        ({"fencing_token": 3}, "run_epoch_fencing_token_invalid"),
        ({"worker_heartbeat_at": START_AT}, "run_epoch_lease_renewal_invalid"),
        (
            {"worker_heartbeat_at": START_AT + timedelta(seconds=10)},
            "run_epoch_lease_renewal_invalid",
        ),
        (
            {
                "worker_heartbeat_at": START_AT + timedelta(seconds=10),
                "worker_lease_expires_at": START_AT + timedelta(seconds=10),
            },
            "run_epoch_lease_renewal_invalid",
        ),
    ],
)
def test_postgres_invalid_worker_and_lease_mutations(
    database_url: str,
    claimed_epoch: runs.OperationalPaperSessionRunEpoch,
    changes: dict[str, object],
    message: str,
) -> None:
    _reject_update(database_url, claimed_epoch, changes, message)


def test_postgres_claim_cannot_appear_without_new_fence(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    claimed = runs.claim_operational_paper_session_run_epoch(
        pending_epoch,
        worker_id=uuid4(),
        claimed_at=START_AT + timedelta(seconds=1),
        lease_expires_at=START_AT + timedelta(seconds=61),
    )
    _reject_update(
        database_url,
        pending_epoch,
        _epoch_row(claimed) | {"fencing_token": 0},
        "run_epoch_worker_requires_new_fence",
    )


# Block H — recovery preserves epoch identity and invalidates the old capability.
def test_postgres_recovery_requires_expiry_and_keeps_one_current_epoch(
    database_url: str,
    running_epoch: runs.OperationalPaperSessionRunEpoch,
    run_activation: OperationalPaperSessionActivation,
) -> None:
    claim = running_epoch.worker_claim
    assert claim is not None
    recovered = runs.recover_operational_paper_session_run_epoch(
        running_epoch,
        worker_id=uuid4(),
        recovered_at=claim.lease_expires_at,
        lease_expires_at=claim.lease_expires_at + timedelta(seconds=60),
    )
    early = _epoch_row(recovered) | {
        "worker_claimed_at": claim.lease_expires_at - timedelta(microseconds=1),
        "worker_heartbeat_at": claim.lease_expires_at - timedelta(microseconds=1),
    }
    _reject_update(database_url, running_epoch, early, "run_epoch_recovery_invalid")
    _persist_change(database_url, recovered)
    assert recovered.epoch_id == running_epoch.epoch_id
    assert recovered.record_version == running_epoch.record_version + 1
    assert recovered.fencing_token == running_epoch.fencing_token + 1
    assert recovered.observed_state.value == "RECOVERING"
    assert recovered.worker_claim is not None
    assert recovered.worker_claim.worker_id != claim.worker_id
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "select epoch_id from public.operational_paper_session_run_epochs "
            "where session_id = %s",
            (recovered.session_id,),
        ).fetchall() == [(recovered.epoch_id,)]
    duplicate, command = _start(run_activation, key="run:during-recovery")
    with pytest.raises(psycopg.errors.UniqueViolation) as caught:
        _persist_start(database_url, duplicate, command)
    assert caught.value.diag.constraint_name == "op_ps_run_epoch_one_current_per_session_uidx"
    _assert_stored_epoch(database_url, recovered)


@pytest.mark.parametrize("action", ["renew", "transition", "release", "fail"])
def test_postgres_stale_worker_mutations_rejected_after_recovery(
    database_url: str,
    running_epoch: runs.OperationalPaperSessionRunEpoch,
    action: str,
) -> None:
    # Freeze legitimate work produced by worker A before worker B recovers.
    original = running_epoch
    if action == "release":
        original, command = _request(original, "PAUSE")
        _persist_change(database_url, original, command)
    elif action == "transition":
        original, command = _request(original, "STOP")
        _persist_change(database_url, original, command)
    claim = original.worker_claim
    assert claim is not None
    if action == "renew":
        stale = runs.renew_operational_paper_session_run_worker_claim(
            original,
            worker_id=claim.worker_id,
            fencing_token=claim.fencing_token,
            heartbeat_at=START_AT + timedelta(seconds=10),
            lease_expires_at=START_AT + timedelta(seconds=70),
        )
    elif action == "transition":
        stale = runs.mark_operational_paper_session_run_epoch_stopping(
            original,
            worker_id=claim.worker_id,
            fencing_token=claim.fencing_token,
            observed_at=START_AT + timedelta(seconds=10),
        )
    elif action == "release":
        stale = runs.settle_operational_paper_session_run_epoch_paused(
            original,
            worker_id=claim.worker_id,
            fencing_token=claim.fencing_token,
            observed_at=START_AT + timedelta(seconds=10),
        )
    else:
        stale = runs.fail_claimed_operational_paper_session_run_epoch(
            original,
            worker_id=claim.worker_id,
            fencing_token=claim.fencing_token,
            code=runs.OperationalPaperSessionRunFailureCode.INTERNAL_ERROR,
            failed_at=START_AT + timedelta(seconds=10),
        )
    recovered = runs.recover_operational_paper_session_run_epoch(
        original,
        worker_id=uuid4(),
        recovered_at=claim.lease_expires_at,
        lease_expires_at=claim.lease_expires_at + timedelta(seconds=60),
    )
    _persist_change(database_url, recovered)
    _reject_update(database_url, recovered, _epoch_row(stale), "run_epoch_record_version_conflict")
    # Even learning the new record version cannot restore worker A's old fence.
    _reject_update(
        database_url,
        recovered,
        _epoch_row(stale) | {"record_version": recovered.record_version + 1},
        "run_epoch_fencing_token_invalid",
    )


# Block I — safe boundary settlement releases claims and retains historical fences.
def test_postgres_running_pause_settlement_and_resume_reclaim(
    database_url: str,
    running_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    requested, command = _request(running_epoch, "PAUSE")
    _persist_change(database_url, requested, command)
    claim = requested.worker_claim
    assert claim is not None
    paused = runs.settle_operational_paper_session_run_epoch_paused(
        requested,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        observed_at=START_AT + timedelta(seconds=10),
    )
    _persist_change(database_url, paused)
    assert paused.worker_claim is None
    assert paused.fencing_token == claim.fencing_token
    assert paused.observed_state.value == "PAUSED"
    assert paused.terminal_at is None
    assert not runs.operational_paper_session_run_epoch_is_terminal(paused)
    resumed, command = _request(paused, "RESUME")
    _persist_change(database_url, resumed, command)
    reclaimed = runs.claim_operational_paper_session_run_epoch(
        resumed,
        worker_id=uuid4(),
        claimed_at=START_AT + timedelta(seconds=11),
        lease_expires_at=START_AT + timedelta(seconds=71),
    )
    _persist_change(database_url, reclaimed)
    assert reclaimed.fencing_token == paused.fencing_token + 1
    assert reclaimed.epoch_id == paused.epoch_id
    assert reclaimed.observed_state.value == "STARTING"


def test_postgres_active_stop_settles_terminal_and_releases_claim(
    database_url: str,
    running_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    requested, command = _request(running_epoch, "STOP")
    _persist_change(database_url, requested, command)
    claim = requested.worker_claim
    assert claim is not None
    assert requested.observed_state.value == "RUNNING"
    assert requested.desired_state.value == "STOPPED"
    assert requested.terminal_at is None
    stopping = runs.mark_operational_paper_session_run_epoch_stopping(
        requested,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        observed_at=START_AT + timedelta(seconds=10),
    )
    _persist_change(database_url, stopping)
    assert stopping.observed_state.value == "STOPPING"
    assert stopping.worker_claim == claim
    stopped = runs.settle_operational_paper_session_run_epoch_stopped(
        stopping,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        observed_at=START_AT + timedelta(seconds=11),
    )
    _persist_change(database_url, stopped)
    assert stopped.worker_claim is None
    assert stopped.fencing_token == claim.fencing_token
    assert (stopped.observed_state.value, stopped.desired_state.value) == ("STOPPED", "STOPPED")
    assert stopped.terminal_at == START_AT + timedelta(seconds=11)
    assert stopped.failure is None
    _reject_update(database_url, stopped, {"observed_state": "RUNNING"}, "run_epoch_terminal")


@pytest.mark.parametrize("code", list(runs.OperationalPaperSessionRunFailureCode))
def test_postgres_claimed_failure_is_closed_and_terminal(
    database_url: str,
    running_epoch: runs.OperationalPaperSessionRunEpoch,
    code: runs.OperationalPaperSessionRunFailureCode,
) -> None:
    claim = running_epoch.worker_claim
    assert claim is not None
    failed_at = START_AT + timedelta(seconds=10)
    failed = runs.fail_claimed_operational_paper_session_run_epoch(
        running_epoch,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        code=code,
        failed_at=failed_at,
    )
    _persist_change(database_url, failed)
    assert failed.observed_state.value == "FAILED"
    assert failed.worker_claim is None
    assert failed.fencing_token == claim.fencing_token
    assert failed.failure is not None
    assert failed.failure.code is code
    assert failed.failure.failed_at == failed.terminal_at == failed_at
    _reject_update(database_url, failed, {"observed_state": "PENDING"}, "run_epoch_terminal")


def test_postgres_arbitrary_failure_diagnostics_rejected(
    database_url: str,
    pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    failed = runs.fail_unclaimed_operational_paper_session_run_epoch(
        pending_epoch,
        code=runs.OperationalPaperSessionRunFailureCode.INTERNAL_ERROR,
        failed_at=START_AT + timedelta(seconds=1),
    )
    _reject_update(
        database_url,
        pending_epoch,
        _epoch_row(failed) | {"failure_code": "raw exception /tmp/runner pid=123"},
        "op_ps_run_epoch_failure_code_check",
    )
    _persist_change(database_url, failed)


# Lease authority must expire even if no replacement worker has claimed yet.
def test_postgres_expired_worker_cannot_renew_without_recovery(
    database_url: str,
    claimed_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    claim = claimed_epoch.worker_claim
    assert claim is not None
    expired_at = claim.lease_expires_at
    with pytest.raises(runs.OperationalPaperSessionRunLeaseError):
        runs.renew_operational_paper_session_run_worker_claim(
            claimed_epoch,
            worker_id=claim.worker_id,
            fencing_token=claim.fencing_token,
            heartbeat_at=expired_at,
            lease_expires_at=expired_at + timedelta(seconds=60),
        )
    # Bypass only domain validation: the unchanged fence must not resurrect an expired lease.
    _reject_update(
        database_url,
        claimed_epoch,
        {
            "worker_heartbeat_at": expired_at,
            "worker_lease_expires_at": expired_at + timedelta(seconds=60),
        },
        "run_epoch_lease_renewal_invalid",
    )
