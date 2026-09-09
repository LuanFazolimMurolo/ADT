"""Phase 7-12 Gate 2B/B1A collector-control migration tests."""

from __future__ import annotations

import re
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import app.operational_market_data_collectors as collectors

MIGRATION_PATH = (
    Path(__file__).parents[3] / "supabase/migrations/"
    "20260909000000_phase_7_12_operational_market_data_collector_epochs.sql"
)

EPOCHS = "operational_market_data_collector_epochs"
COMMANDS = "operational_market_data_collector_commands"

START_AT = datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


def _migration() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


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


def _quoted_constraint_values(text: str, constraint: str) -> set[str]:
    start = text.index(f"constraint {constraint}")
    tail = text[start:]
    next_constraint = tail.find("\n    constraint ", 1)
    table_end = tail.find("\n);")

    candidates = [value for value in (next_constraint, table_end) if value >= 0]
    end = min(candidates)

    return set(re.findall(r"'([A-Z][A-Z0-9_]*)'", tail[:end]))


def _specification() -> collectors.OperationalMarketDataCollectorSpecification:
    return collectors.OperationalMarketDataCollectorSpecification(
        schema_version=(collectors.OPERATIONAL_MARKET_DATA_COLLECTOR_SCHEMA_VERSION),
        collector_contract_version=(collectors.OPERATIONAL_MARKET_DATA_COLLECTOR_CONTRACT_VERSION),
        scope=(collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW),
        targets=(
            collectors.OperationalMarketDataCollectorTarget(
                symbol="BTC/USDT",
                timeframe="1m",
                bootstrap_candles=500,
            ),
            collectors.OperationalMarketDataCollectorTarget(
                symbol="ETH/USDT",
                timeframe="5m",
                bootstrap_candles=300,
            ),
        ),
        interval_seconds=60,
        overlap_candles=2,
    )


def _start(
    actor_id: UUID,
    *,
    key: str = "collector:start",
) -> tuple[
    collectors.OperationalMarketDataCollectorEpoch,
    collectors.OperationalMarketDataCollectorCommand,
]:
    specification = _specification()
    checksum = collectors.operational_market_data_collector_specification_checksum(specification)

    return collectors.start_operational_market_data_collector_epoch(
        epoch_id=uuid4(),
        command_id=uuid4(),
        specification=specification,
        start_intent=collectors.OperationalMarketDataCollectorStartIntent(
            specification_checksum=checksum,
        ),
        requested_by=actor_id,
        requested_at=START_AT,
        idempotency_key=key,
    )


def _target_payload(
    target: collectors.OperationalMarketDataCollectorTarget,
) -> dict[str, object]:
    return {
        "symbol": target.symbol,
        "timeframe": target.timeframe,
        "bootstrap_candles": target.bootstrap_candles,
    }


def _epoch_row(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
) -> dict[str, object]:
    specification = epoch.specification
    claim = epoch.worker_claim

    return {
        "epoch_id": epoch.epoch_id,
        "schema_version": epoch.schema_version,
        "collector_contract_version": epoch.collector_contract_version,
        "scope": epoch.scope.value,
        "targets": [_target_payload(target) for target in specification.targets],
        "interval_seconds": specification.interval_seconds,
        "overlap_candles": specification.overlap_candles,
        "specification_checksum": epoch.specification_checksum,
        "desired_state": epoch.desired_state.value,
        "observed_state": epoch.observed_state.value,
        "record_version": epoch.record_version,
        "fencing_token": epoch.fencing_token,
        "epoch_checksum": epoch.epoch_checksum,
        "start_requested_by": epoch.start_requested_by,
        "start_requested_at": epoch.start_requested_at,
        "start_idempotency_key": epoch.start_idempotency_key,
        "start_intent_fingerprint": epoch.start_intent_fingerprint,
        "worker_id": claim.worker_id if claim else None,
        "worker_claimed_at": claim.claimed_at if claim else None,
        "worker_heartbeat_at": claim.heartbeat_at if claim else None,
        "worker_lease_expires_at": (claim.lease_expires_at if claim else None),
        "failure_code": (epoch.failure.code.value if epoch.failure else None),
        "failure_at": (epoch.failure.failed_at if epoch.failure else None),
        "terminal_at": epoch.terminal_at,
    }


def _command_row(
    command: collectors.OperationalMarketDataCollectorCommand,
) -> dict[str, object]:
    row = {field.name: getattr(command, field.name) for field in fields(command)}

    row["command_type"] = command.command_type.value
    row["desired_state"] = command.desired_state.value
    row["expected_record_version"] = (
        0 if command.expected_record_version is None else command.expected_record_version
    )

    return row


def _insert_row(
    connection: psycopg.Connection[object],
    table: str,
    row: dict[str, object],
) -> None:
    assert table in (EPOCHS, COMMANDS)

    values = dict(row)

    if table == EPOCHS:
        values["targets"] = Jsonb(values["targets"])

    connection.execute(
        sql.SQL("insert into public.{} ({}) values ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ),
        tuple(values.values()),
    )


def test_b1a_relation_and_marker_contract_is_exact() -> None:
    text = _migration()

    assert text.count("-- B1A-END") == 1

    tables = re.findall(
        r"(?m)^create table public\.([a-z0-9_]+) \(",
        text,
    )

    assert tables == [
        EPOCHS,
        COMMANDS,
    ]


def test_epoch_column_contract_is_exact() -> None:
    assert _table_columns(
        _migration(),
        EPOCHS,
    ) == (
        "epoch_id",
        "schema_version",
        "collector_contract_version",
        "scope",
        "targets",
        "interval_seconds",
        "overlap_candles",
        "specification_checksum",
        "desired_state",
        "observed_state",
        "record_version",
        "fencing_token",
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
        COMMANDS,
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


def test_closed_vocabularies_match_domain() -> None:
    text = _migration()

    desired = _quoted_constraint_values(
        text,
        "op_md_collector_epoch_desired_state_check",
    )
    observed = _quoted_constraint_values(
        text,
        "op_md_collector_epoch_observed_state_check",
    )
    failures = _quoted_constraint_values(
        text,
        "op_md_collector_epoch_failure_code_check",
    )

    assert desired == {item.value for item in collectors.OperationalMarketDataCollectorDesiredState}

    assert observed == {
        item.value for item in collectors.OperationalMarketDataCollectorObservedState
    }

    assert failures == {item.value for item in collectors.OperationalMarketDataCollectorFailureCode}


def test_static_backend_only_authority_contract() -> None:
    lower = _migration().lower()

    assert lower.count("enable row level security;") == 2
    assert "create policy" not in lower
    assert "grant " not in lower

    assert "from public, anon, authenticated, service_role;" in lower

    forbidden = (
        "api_key",
        "password",
        "secret",
        "credential",
        "adt_data_dir",
        "filesystem_path",
        "hostname",
        "process_id",
        "binance order",
        "double precision",
    )

    for fragment in forbidden:
        assert fragment not in lower


def test_postgres_exact_columns_and_types(
    database_url: str,
) -> None:
    epoch_types = {
        "epoch_id": "uuid",
        "schema_version": "integer",
        "collector_contract_version": "integer",
        "scope": "text",
        "targets": "jsonb",
        "interval_seconds": "integer",
        "overlap_candles": "integer",
        "specification_checksum": "text",
        "desired_state": "text",
        "observed_state": "text",
        "record_version": "bigint",
        "fencing_token": "bigint",
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
        for table, expected in (
            (EPOCHS, epoch_types),
            (COMMANDS, command_types),
        ):
            actual = connection.execute(
                "select column_name, data_type "
                "from information_schema.columns "
                "where table_schema = 'public' "
                "and table_name = %s "
                "order by ordinal_position",
                (table,),
            ).fetchall()

            assert actual == list(expected.items())


def test_postgres_unique_and_foreign_key_contract(
    database_url: str,
) -> None:
    expected_fks = {
        "op_md_collector_epoch_start_actor_fkey": (
            "FOREIGN KEY (start_requested_by) REFERENCES auth.users(id) ON DELETE RESTRICT"
        ),
        "op_md_collector_command_epoch_identity_fkey": (
            "FOREIGN KEY (epoch_id, epoch_checksum) "
            "REFERENCES operational_market_data_collector_epochs"
            "(epoch_id, epoch_checksum) ON DELETE RESTRICT"
        ),
        "op_md_collector_command_actor_fkey": (
            "FOREIGN KEY (actor_id) REFERENCES auth.users(id) ON DELETE RESTRICT"
        ),
    }

    with psycopg.connect(database_url) as connection:
        actual_fks = dict(
            connection.execute(
                "select conname, pg_get_constraintdef(oid) "
                "from pg_constraint "
                "where conrelid in "
                "(%s::regclass, %s::regclass) "
                "and contype = 'f'",
                (EPOCHS, COMMANDS),
            ).fetchall()
        )

        assert actual_fks == expected_fks

        indexes: dict[str, str] = dict(
            connection.execute(
                "select indexrelid::regclass::text, "
                "pg_get_indexdef(indexrelid) "
                "from pg_index "
                "where indrelid in "
                "(%s::regclass, %s::regclass) "
                "and indisunique",
                (EPOCHS, COMMANDS),
            ).fetchall()
        )

    expected = {
        "operational_market_data_collector_epochs_pkey": "(epoch_id)",
        "op_md_collector_epoch_identity_key": "(epoch_id, epoch_checksum)",
        "op_md_collector_epoch_actor_start_idempotency_key": (
            "(start_requested_by, start_idempotency_key)"
        ),
        "op_md_collector_epoch_one_current_per_scope_uidx": "(scope)",
        "operational_market_data_collector_commands_pkey": "(command_id)",
        "op_md_collector_command_actor_idempotency_key": "(actor_id, idempotency_key)",
        "op_md_collector_command_epoch_result_version_key": "(epoch_id, resulting_record_version)",
    }

    assert indexes.keys() == expected.keys()

    for name, columns in expected.items():
        assert f"USING btree {columns}" in indexes[name]

    assert indexes["op_md_collector_epoch_one_current_per_scope_uidx"].endswith(
        "WHERE (observed_state <> ALL (ARRAY['STOPPED'::text, 'FAILED'::text]))"
    )


def test_postgres_rls_and_data_api_privileges(
    database_url: str,
) -> None:
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

            for role in (
                "anon",
                "authenticated",
                "service_role",
            ):
                assert connection.execute(
                    "select has_table_privilege("
                    "%s, %s, "
                    "'SELECT,INSERT,UPDATE,DELETE,"
                    "TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'"
                    ")",
                    (role, table),
                ).fetchone() == (False,)


def test_postgres_domain_start_rows_are_persistable(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)

    with psycopg.connect(database_url) as connection:
        _insert_row(
            connection,
            EPOCHS,
            _epoch_row(epoch),
        )
        _insert_row(
            connection,
            COMMANDS,
            _command_row(command),
        )

    with psycopg.connect(
        database_url,
        row_factory=dict_row,
    ) as connection:
        stored_epoch = connection.execute(
            "select * from public.operational_market_data_collector_epochs where epoch_id = %s",
            (epoch.epoch_id,),
        ).fetchone()

        stored_command = connection.execute(
            "select * from public.operational_market_data_collector_commands where command_id = %s",
            (command.command_id,),
        ).fetchone()

    assert stored_epoch == _epoch_row(epoch)
    assert stored_command == _command_row(command)


def test_postgres_targets_top_level_shape_is_closed(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, _command = _start(auth_user_id)
    row = _epoch_row(epoch)
    row["targets"] = {}

    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation) as caught:
            _insert_row(
                connection,
                EPOCHS,
                row,
            )

    assert caught.value.diag.constraint_name == "op_md_collector_epoch_targets_shape_check"


def test_postgres_only_one_nonterminal_epoch_per_scope(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    first, first_command = _start(
        auth_user_id,
        key="collector:start:first",
    )
    second, _second_command = _start(
        auth_user_id,
        key="collector:start:second",
    )

    with psycopg.connect(database_url) as connection:
        _insert_row(
            connection,
            EPOCHS,
            _epoch_row(first),
        )
        _insert_row(
            connection,
            COMMANDS,
            _command_row(first_command),
        )

    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation) as caught:
            _insert_row(
                connection,
                EPOCHS,
                _epoch_row(second),
            )

    assert caught.value.diag.constraint_name == "op_md_collector_epoch_one_current_per_scope_uidx"


# GATE_2B_B1B1_TESTS


def _reject_epoch_insert(
    database_url: str,
    row: dict[str, object],
    message: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match=message,
        ):
            _insert_row(
                connection,
                EPOCHS,
                row,
            )


def test_b1b1_function_and_trigger_surface_is_present() -> None:
    text = _migration()

    functions = set(
        re.findall(
            r"(?m)^create function public\.([a-z0-9_]+)\(",
            text,
        )
    )

    assert {
        "op_md_collector_transition_allowed",
        "validate_op_md_collector_epoch_insert",
    } <= functions

    triggers = set(
        re.findall(
            r"(?m)^create trigger ([a-z0-9_]+)",
            text,
        )
    )

    assert "op_md_collector_epoch_validate_insert" in triggers
    assert text.count("-- B1B1-END") == 1


def test_b1b1_transition_graph_matches_domain_contract() -> None:
    text = _migration()

    start = text.index("create function public.op_md_collector_transition_allowed(")
    end = text.index("$function$;", start)
    body = text[start:end]

    required = (
        "when 'PENDING' then",
        "new_state in ('STARTING', 'PAUSED', 'STOPPED', 'FAILED')",
        "when 'STARTING' then",
        "when 'RUNNING' then",
        "when 'PAUSED' then",
        "new_state in ('STARTING', 'STOPPED', 'FAILED')",
        "when 'RECOVERING' then",
        "when 'STOPPING' then",
        "new_state in ('RECOVERING', 'STOPPED', 'FAILED')",
        "when 'STOPPED' then false",
        "when 'FAILED' then false",
    )

    for fragment in required:
        assert fragment in body


def test_postgres_rejects_noncanonical_initial_epoch_state(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, _command = _start(auth_user_id)
    row = _epoch_row(epoch)
    row["desired_state"] = "PAUSED"

    _reject_epoch_insert(
        database_url,
        row,
        "collector_epoch_initial_state_invalid",
    )


def test_postgres_rejects_target_with_extra_field(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, _command = _start(auth_user_id)
    row = _epoch_row(epoch)

    targets = list(row["targets"])  # type: ignore[arg-type]
    first = dict(targets[0])  # type: ignore[arg-type]
    first["exchange"] = "BINANCE"
    targets[0] = first
    row["targets"] = targets

    _reject_epoch_insert(
        database_url,
        row,
        "collector_target_shape_invalid",
    )


@pytest.mark.parametrize(
    "symbol",
    [
        "btc/usdt",
        "BTC/BTC",
        "BTCUSDT",
        "BTC /USDT",
    ],
)
def test_postgres_rejects_noncanonical_target_symbol(
    database_url: str,
    auth_user_id: UUID,
    symbol: str,
) -> None:
    epoch, _command = _start(auth_user_id)
    row = _epoch_row(epoch)

    targets = list(row["targets"])  # type: ignore[arg-type]
    first = dict(targets[0])  # type: ignore[arg-type]
    first["symbol"] = symbol
    targets[0] = first
    row["targets"] = targets

    _reject_epoch_insert(
        database_url,
        row,
        "collector_target_symbol_invalid",
    )


def test_postgres_rejects_unsupported_target_timeframe(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, _command = _start(auth_user_id)
    row = _epoch_row(epoch)

    targets = list(row["targets"])  # type: ignore[arg-type]
    first = dict(targets[0])  # type: ignore[arg-type]
    first["timeframe"] = "2m"
    targets[0] = first
    row["targets"] = targets

    _reject_epoch_insert(
        database_url,
        row,
        "collector_target_timeframe_invalid",
    )


@pytest.mark.parametrize(
    "bootstrap",
    [
        0,
        1_000_001,
        1.5,
        "500",
        True,
    ],
)
def test_postgres_rejects_invalid_target_bootstrap(
    database_url: str,
    auth_user_id: UUID,
    bootstrap: object,
) -> None:
    epoch, _command = _start(auth_user_id)
    row = _epoch_row(epoch)

    targets = list(row["targets"])  # type: ignore[arg-type]
    first = dict(targets[0])  # type: ignore[arg-type]
    first["bootstrap_candles"] = bootstrap
    targets[0] = first
    row["targets"] = targets

    expected = (
        "collector_target_type_invalid"
        if isinstance(bootstrap, (str, bool))
        else "collector_target_bootstrap_invalid"
    )

    _reject_epoch_insert(
        database_url,
        row,
        expected,
    )


def test_postgres_rejects_unsorted_targets(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, _command = _start(auth_user_id)
    row = _epoch_row(epoch)

    targets = list(row["targets"])  # type: ignore[arg-type]
    row["targets"] = list(reversed(targets))

    _reject_epoch_insert(
        database_url,
        row,
        "collector_targets_order_invalid",
    )


def test_postgres_rejects_duplicate_targets(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, _command = _start(auth_user_id)
    row = _epoch_row(epoch)

    targets = list(row["targets"])  # type: ignore[arg-type]
    row["targets"] = [
        targets[0],
        targets[0],
    ]

    _reject_epoch_insert(
        database_url,
        row,
        "collector_targets_order_invalid",
    )


def test_postgres_valid_domain_spec_still_persists_after_b1b1(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(
        auth_user_id,
        key="collector:b1b1:valid",
    )

    with psycopg.connect(database_url) as connection:
        _insert_row(
            connection,
            EPOCHS,
            _epoch_row(epoch),
        )
        _insert_row(
            connection,
            COMMANDS,
            _command_row(command),
        )

    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "select scope, desired_state, observed_state, "
            "record_version, fencing_token "
            "from public.operational_market_data_collector_epochs "
            "where epoch_id = %s",
            (epoch.epoch_id,),
        ).fetchone() == (
            "BINANCE_SPOT_RAW",
            "RUNNING",
            "PENDING",
            1,
            0,
        )


# GATE_2B_B1B2A_TESTS


def _persist_collector_start(
    database_url: str,
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    command: collectors.OperationalMarketDataCollectorCommand,
) -> None:
    with psycopg.connect(database_url) as connection:
        _insert_row(connection, EPOCHS, _epoch_row(epoch))
        _insert_row(connection, COMMANDS, _command_row(command))


def _mutable_epoch_row(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
) -> dict[str, object]:
    claim = epoch.worker_claim

    return {
        "desired_state": epoch.desired_state.value,
        "observed_state": epoch.observed_state.value,
        "record_version": epoch.record_version,
        "fencing_token": epoch.fencing_token,
        "worker_id": claim.worker_id if claim else None,
        "worker_claimed_at": claim.claimed_at if claim else None,
        "worker_heartbeat_at": claim.heartbeat_at if claim else None,
        "worker_lease_expires_at": (claim.lease_expires_at if claim else None),
        "failure_code": (epoch.failure.code.value if epoch.failure else None),
        "failure_at": (epoch.failure.failed_at if epoch.failure else None),
        "terminal_at": epoch.terminal_at,
    }


def _update_collector_epoch(
    connection: psycopg.Connection[object],
    epoch_id: UUID,
    changes: dict[str, object],
) -> None:
    result = connection.execute(
        sql.SQL("update public.{} set {} where epoch_id = %s").format(
            sql.Identifier(EPOCHS),
            sql.SQL(", ").join(
                sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes
            ),
        ),
        (*changes.values(), epoch_id),
    )

    assert result.rowcount == 1


def _persist_domain_epoch_change(
    database_url: str,
    epoch: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    with psycopg.connect(database_url) as connection:
        _update_collector_epoch(
            connection,
            epoch.epoch_id,
            _mutable_epoch_row(epoch),
        )


def _stored_mutable_epoch(
    database_url: str,
    epoch_id: UUID,
) -> dict[str, object]:
    with psycopg.connect(
        database_url,
        row_factory=dict_row,
    ) as connection:
        row = connection.execute(
            "select "
            "desired_state, observed_state, record_version, "
            "fencing_token, worker_id, worker_claimed_at, "
            "worker_heartbeat_at, worker_lease_expires_at, "
            "failure_code, failure_at, terminal_at "
            "from public.operational_market_data_collector_epochs "
            "where epoch_id = %s",
            (epoch_id,),
        ).fetchone()

    assert row is not None
    return dict(row)


def _persisted_running_epoch(
    database_url: str,
    auth_user_id: UUID,
) -> collectors.OperationalMarketDataCollectorEpoch:
    epoch, command = _start(
        auth_user_id,
        key=f"collector:b1b2a:{uuid4().hex}",
    )
    _persist_collector_start(
        database_url,
        epoch,
        command,
    )

    worker_id = uuid4()

    claimed = collectors.claim_operational_market_data_collector_epoch(
        epoch,
        worker_id=worker_id,
        claimed_at=START_AT + timedelta(seconds=1),
        lease_expires_at=START_AT + timedelta(seconds=31),
    )
    _persist_domain_epoch_change(database_url, claimed)

    running = collectors.mark_operational_market_data_collector_epoch_running(
        claimed,
        worker_id=worker_id,
        fencing_token=claimed.fencing_token,
        observed_at=START_AT + timedelta(seconds=2),
    )
    _persist_domain_epoch_change(database_url, running)

    return running


def _reject_epoch_update(
    database_url: str,
    epoch_id: UUID,
    changes: dict[str, object],
    message: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.Error, match=message):
            with connection.transaction():
                _update_collector_epoch(
                    connection,
                    epoch_id,
                    changes,
                )


def test_b1b2a_protect_function_and_trigger_are_exact() -> None:
    text = _migration()

    assert text.count("create function public.protect_op_md_collector_epoch()") == 1
    assert text.count("create trigger op_md_collector_epoch_protect") == 1
    assert text.count("-- B1B2A-END") == 1

    required = (
        "collector_epoch_delete_forbidden",
        "collector_epoch_immutable_fields_changed",
        "collector_epoch_record_version_conflict",
        "collector_epoch_terminal",
        "collector_epoch_fencing_token_invalid",
        "collector_epoch_command_mixed_mutation",
        "collector_epoch_command_required",
        "collector_epoch_unexpected_command_version",
        "collector_epoch_observed_transition_forbidden",
        "collector_epoch_noop_mutation",
        "collector_epoch_new_claim_invalid",
        "collector_epoch_claim_transition_invalid",
        "collector_epoch_recovery_invalid",
        "collector_epoch_worker_requires_new_fence",
        "collector_epoch_worker_identity_changed",
        "collector_epoch_lease_renewal_invalid",
        "collector_epoch_worker_release_invalid",
        "collector_epoch_failure_at_invalid",
        "collector_epoch_terminal_at_invalid",
    )

    for fragment in required:
        assert fragment in text


def test_postgres_domain_claim_and_running_are_persistable(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    stored = _stored_mutable_epoch(
        database_url,
        running.epoch_id,
    )

    assert stored == _mutable_epoch_row(running)
    assert running.fencing_token == 1
    assert running.record_version == 3
    assert running.observed_state is collectors.OperationalMarketDataCollectorObservedState.RUNNING


def test_postgres_domain_lease_renewal_is_persistable(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )
    claim = running.worker_claim
    assert claim is not None

    renewed = collectors.renew_operational_market_data_collector_worker_claim(
        running,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        heartbeat_at=START_AT + timedelta(seconds=10),
        lease_expires_at=START_AT + timedelta(seconds=41),
    )

    _persist_domain_epoch_change(database_url, renewed)

    assert _stored_mutable_epoch(
        database_url,
        renewed.epoch_id,
    ) == _mutable_epoch_row(renewed)

    assert renewed.record_version == running.record_version + 1
    assert renewed.fencing_token == running.fencing_token


def test_postgres_domain_recovery_advances_fence(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    recovered = collectors.recover_operational_market_data_collector_epoch(
        running,
        worker_id=uuid4(),
        recovered_at=START_AT + timedelta(seconds=31),
        lease_expires_at=START_AT + timedelta(seconds=61),
    )

    _persist_domain_epoch_change(database_url, recovered)

    assert _stored_mutable_epoch(
        database_url,
        recovered.epoch_id,
    ) == _mutable_epoch_row(recovered)

    assert recovered.fencing_token == running.fencing_token + 1
    assert (
        recovered.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.RECOVERING
    )


def test_postgres_domain_claimed_failure_releases_worker(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )
    claim = running.worker_claim
    assert claim is not None

    failed = collectors.fail_claimed_operational_market_data_collector_epoch(
        running,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
        failed_at=START_AT + timedelta(seconds=5),
    )

    _persist_domain_epoch_change(database_url, failed)

    assert _stored_mutable_epoch(
        database_url,
        failed.epoch_id,
    ) == _mutable_epoch_row(failed)

    assert failed.worker_claim is None
    assert failed.observed_state is collectors.OperationalMarketDataCollectorObservedState.FAILED


def test_postgres_epoch_delete_is_forbidden(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)
    _persist_collector_start(database_url, epoch, command)

    with psycopg.connect(database_url) as connection:
        with pytest.raises(
            psycopg.Error,
            match="collector_epoch_delete_forbidden",
        ):
            connection.execute(
                "delete from public.operational_market_data_collector_epochs where epoch_id = %s",
                (epoch.epoch_id,),
            )


def test_postgres_epoch_specification_is_immutable(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)
    _persist_collector_start(database_url, epoch, command)

    _reject_epoch_update(
        database_url,
        epoch.epoch_id,
        {
            "record_version": epoch.record_version + 1,
            "interval_seconds": 61,
        },
        "collector_epoch_immutable_fields_changed",
    )


def test_postgres_epoch_record_version_must_advance_exactly_one(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)
    _persist_collector_start(database_url, epoch, command)

    _reject_epoch_update(
        database_url,
        epoch.epoch_id,
        {
            "record_version": epoch.record_version + 2,
            "observed_state": "PAUSED",
        },
        "collector_epoch_record_version_conflict",
    )


def test_postgres_pure_record_version_bump_is_forbidden(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)
    _persist_collector_start(database_url, epoch, command)

    _reject_epoch_update(
        database_url,
        epoch.epoch_id,
        {
            "record_version": epoch.record_version + 1,
        },
        "collector_epoch_noop_mutation",
    )


def test_postgres_observed_transition_cannot_bypass_graph(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)
    _persist_collector_start(database_url, epoch, command)

    _reject_epoch_update(
        database_url,
        epoch.epoch_id,
        {
            "record_version": epoch.record_version + 1,
            "observed_state": "RUNNING",
        },
        "collector_epoch_observed_transition_forbidden",
    )


def test_postgres_desired_state_requires_command_record(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)
    _persist_collector_start(database_url, epoch, command)

    _reject_epoch_update(
        database_url,
        epoch.epoch_id,
        {
            "record_version": epoch.record_version + 1,
            "desired_state": "PAUSED",
        },
        "collector_epoch_command_required",
    )


def test_postgres_fence_cannot_jump(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)
    _persist_collector_start(database_url, epoch, command)

    _reject_epoch_update(
        database_url,
        epoch.epoch_id,
        {
            "record_version": epoch.record_version + 1,
            "fencing_token": epoch.fencing_token + 2,
        },
        "collector_epoch_fencing_token_invalid",
    )


def test_postgres_worker_cannot_appear_without_new_fence(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, command = _start(auth_user_id)
    _persist_collector_start(database_url, epoch, command)

    claimed_at = START_AT + timedelta(seconds=1)

    _reject_epoch_update(
        database_url,
        epoch.epoch_id,
        {
            "record_version": epoch.record_version + 1,
            "observed_state": "STARTING",
            "worker_id": uuid4(),
            "worker_claimed_at": claimed_at,
            "worker_heartbeat_at": claimed_at,
            "worker_lease_expires_at": (START_AT + timedelta(seconds=31)),
        },
        "collector_epoch_worker_requires_new_fence",
    )


def test_postgres_recovery_before_expired_lease_is_forbidden(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    recovered_at = START_AT + timedelta(seconds=20)

    _reject_epoch_update(
        database_url,
        running.epoch_id,
        {
            "record_version": running.record_version + 1,
            "observed_state": "RECOVERING",
            "fencing_token": running.fencing_token + 1,
            "worker_id": uuid4(),
            "worker_claimed_at": recovered_at,
            "worker_heartbeat_at": recovered_at,
            "worker_lease_expires_at": (START_AT + timedelta(seconds=50)),
        },
        "collector_epoch_recovery_invalid",
    )


def test_postgres_worker_identity_cannot_change_under_same_fence(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    _reject_epoch_update(
        database_url,
        running.epoch_id,
        {
            "record_version": running.record_version + 1,
            "worker_id": uuid4(),
        },
        "collector_epoch_worker_identity_changed",
    )


def test_postgres_lease_renewal_must_extend_lease(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )
    claim = running.worker_claim
    assert claim is not None

    _reject_epoch_update(
        database_url,
        running.epoch_id,
        {
            "record_version": running.record_version + 1,
            "worker_heartbeat_at": (START_AT + timedelta(seconds=10)),
            "worker_lease_expires_at": claim.lease_expires_at,
        },
        "collector_epoch_lease_renewal_invalid",
    )


def test_postgres_worker_release_requires_boundary_or_failure(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    _reject_epoch_update(
        database_url,
        running.epoch_id,
        {
            "record_version": running.record_version + 1,
            "worker_id": None,
            "worker_claimed_at": None,
            "worker_heartbeat_at": None,
            "worker_lease_expires_at": None,
        },
        "collector_epoch_worker_release_invalid",
    )


def test_postgres_terminal_epoch_cannot_reopen(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )
    claim = running.worker_claim
    assert claim is not None

    failed = collectors.fail_claimed_operational_market_data_collector_epoch(
        running,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        failure_code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
        failed_at=START_AT + timedelta(seconds=5),
    )
    _persist_domain_epoch_change(database_url, failed)

    _reject_epoch_update(
        database_url,
        failed.epoch_id,
        {
            "record_version": failed.record_version + 1,
            "observed_state": "STARTING",
        },
        "collector_epoch_terminal",
    )


# GATE_2B_B1B2B_TESTS


def _request_collector_command(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    command_type: collectors.OperationalMarketDataCollectorCommandType,
    *,
    requested_at: datetime,
    key: str,
) -> tuple[
    collectors.OperationalMarketDataCollectorEpoch,
    collectors.OperationalMarketDataCollectorCommand,
]:
    return collectors.request_operational_market_data_collector_command(
        epoch,
        command_id=uuid4(),
        intent=collectors.OperationalMarketDataCollectorCommandIntent(
            epoch_id=epoch.epoch_id,
            epoch_checksum=epoch.epoch_checksum,
            command_type=command_type,
            expected_record_version=epoch.record_version,
        ),
        actor_id=epoch.start_requested_by,
        requested_at=requested_at,
        idempotency_key=key,
    )


def _persist_collector_command(
    database_url: str,
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    command: collectors.OperationalMarketDataCollectorCommand,
) -> None:
    with psycopg.connect(database_url) as connection:
        _insert_row(
            connection,
            COMMANDS,
            _command_row(command),
        )

        _update_collector_epoch(
            connection,
            epoch.epoch_id,
            _mutable_epoch_row(epoch),
        )


def _persisted_paused_epoch(
    database_url: str,
    auth_user_id: UUID,
) -> collectors.OperationalMarketDataCollectorEpoch:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    claim = running.worker_claim
    assert claim is not None

    requested, command = _request_collector_command(
        running,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        requested_at=START_AT + timedelta(seconds=3),
        key=f"pause:{uuid4().hex}",
    )

    _persist_collector_command(
        database_url,
        requested,
        command,
    )

    paused = collectors.settle_operational_market_data_collector_epoch_paused(
        requested,
        worker_id=claim.worker_id,
        fencing_token=requested.fencing_token,
        observed_at=START_AT + timedelta(seconds=4),
    )

    _persist_domain_epoch_change(
        database_url,
        paused,
    )

    return paused


def test_b1b2b_function_and_trigger_surface_is_exact() -> None:
    text = _migration()

    functions = set(
        re.findall(
            r"(?m)^create function public\.([a-z0-9_]+)\(",
            text,
        )
    )

    assert functions == {
        "op_md_collector_transition_allowed",
        "validate_op_md_collector_epoch_insert",
        "protect_op_md_collector_epoch",
        "op_md_collector_command_allowed",
        "validate_op_md_collector_command_insert",
        "protect_op_md_collector_command",
        "assert_op_md_collector_epoch_start_command",
        "assert_op_md_collector_command_applied",
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
        "op_md_collector_epoch_validate_insert",
        "op_md_collector_epoch_protect",
        "op_md_collector_command_validate_insert",
        "op_md_collector_command_protect",
    }

    assert deferred == {
        "op_md_collector_epoch_start_command_required",
        "op_md_collector_command_applied",
    }

    assert text.count("deferrable initially deferred") == 2
    assert text.count("-- B1B2B-END") == 1


def test_b1b2b_command_matrix_is_desired_state_based() -> None:
    text = _migration()

    start = text.index("create function public.op_md_collector_command_allowed(")
    end = text.index("$function$;", start)
    body = text[start:end]

    required = (
        "desired_state text",
        "observed_state text",
        "command_type text",
        "observed_state not in ('STOPPED', 'FAILED')",
        "when 'START' then false",
        "when 'PAUSE' then",
        "desired_state = 'RUNNING'",
        "when 'RESUME' then",
        "desired_state = 'PAUSED'",
        "when 'STOP' then",
        "desired_state in ('RUNNING', 'PAUSED')",
    )

    for fragment in required:
        assert fragment in body


def test_postgres_epoch_without_start_command_fails_at_commit(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, _command = _start(
        auth_user_id,
        key="missing:start:command",
    )

    with psycopg.connect(database_url) as connection:
        _insert_row(
            connection,
            EPOCHS,
            _epoch_row(epoch),
        )

        with pytest.raises(
            psycopg.Error,
            match="collector_epoch_start_command_required",
        ):
            connection.commit()

    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "select count(*) "
            "from public.operational_market_data_collector_epochs "
            "where epoch_id = %s",
            (epoch.epoch_id,),
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("expected_record_version", 1),
        ("resulting_record_version", 2),
        ("idempotency_key", "different:start:key"),
        ("intent_fingerprint", "0" * 64),
    ],
)
def test_postgres_mismatched_start_command_is_rejected(
    database_url: str,
    auth_user_id: UUID,
    field_name: str,
    replacement: object,
) -> None:
    epoch, command = _start(
        auth_user_id,
        key=f"mismatch:{field_name}",
    )

    row = _command_row(command)
    row[field_name] = replacement

    with psycopg.connect(database_url) as connection:
        with pytest.raises(
            psycopg.Error,
            match="collector_start_command_mismatch",
        ):
            with connection.transaction():
                _insert_row(
                    connection,
                    EPOCHS,
                    _epoch_row(epoch),
                )
                _insert_row(
                    connection,
                    COMMANDS,
                    row,
                )


def test_postgres_pause_before_physical_convergence_can_be_resumed(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    pause_requested, pause_command = _request_collector_command(
        running,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        requested_at=START_AT + timedelta(seconds=3),
        key="collector:pause:cancelled",
    )

    _persist_collector_command(
        database_url,
        pause_requested,
        pause_command,
    )

    assert pause_requested.desired_state.value == "PAUSED"
    assert pause_requested.observed_state.value == "RUNNING"

    resumed, resume_command = _request_collector_command(
        pause_requested,
        collectors.OperationalMarketDataCollectorCommandType.RESUME,
        requested_at=START_AT + timedelta(seconds=4),
        key="collector:resume:before-convergence",
    )

    _persist_collector_command(
        database_url,
        resumed,
        resume_command,
    )

    assert resumed.desired_state.value == "RUNNING"
    assert resumed.observed_state.value == "RUNNING"

    assert _stored_mutable_epoch(
        database_url,
        resumed.epoch_id,
    ) == _mutable_epoch_row(resumed)


def test_postgres_resume_intent_can_be_paused_again_before_restart(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    paused = _persisted_paused_epoch(
        database_url,
        auth_user_id,
    )

    resumed, resume_command = _request_collector_command(
        paused,
        collectors.OperationalMarketDataCollectorCommandType.RESUME,
        requested_at=START_AT + timedelta(seconds=5),
        key="collector:resume:then-pause",
    )

    _persist_collector_command(
        database_url,
        resumed,
        resume_command,
    )

    assert resumed.desired_state.value == "RUNNING"
    assert resumed.observed_state.value == "PAUSED"

    paused_again, pause_command = _request_collector_command(
        resumed,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        requested_at=START_AT + timedelta(seconds=6),
        key="collector:pause:before-restart",
    )

    _persist_collector_command(
        database_url,
        paused_again,
        pause_command,
    )

    assert paused_again.desired_state.value == "PAUSED"
    assert paused_again.observed_state.value == "PAUSED"


def test_postgres_command_without_epoch_mutation_fails_at_commit(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    _requested, command = _request_collector_command(
        running,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        requested_at=START_AT + timedelta(seconds=3),
        key="collector:pause:not-applied",
    )

    with pytest.raises(
        psycopg.Error,
        match="collector_command_not_applied",
    ):
        with psycopg.connect(database_url) as connection:
            _insert_row(
                connection,
                COMMANDS,
                _command_row(command),
            )


def test_postgres_stale_command_record_version_is_rejected(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    _requested, command = _request_collector_command(
        running,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        requested_at=START_AT + timedelta(seconds=3),
        key="collector:pause:stale",
    )

    row = _command_row(command)
    row["expected_record_version"] = running.record_version - 1
    row["resulting_record_version"] = running.record_version

    with psycopg.connect(database_url) as connection:
        with pytest.raises(
            psycopg.Error,
            match="collector_command_record_version_conflict",
        ):
            _insert_row(
                connection,
                COMMANDS,
                row,
            )


def test_postgres_command_checksum_mismatch_is_rejected(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    running = _persisted_running_epoch(
        database_url,
        auth_user_id,
    )

    _requested, command = _request_collector_command(
        running,
        collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        requested_at=START_AT + timedelta(seconds=3),
        key="collector:pause:checksum",
    )

    row = _command_row(command)
    row["epoch_checksum"] = "0" * 64

    with psycopg.connect(database_url) as connection:
        with pytest.raises(
            psycopg.Error,
            match="collector_command_epoch_checksum_mismatch",
        ):
            _insert_row(
                connection,
                COMMANDS,
                row,
            )


def test_postgres_command_chronology_cannot_move_backwards(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    paused = _persisted_paused_epoch(
        database_url,
        auth_user_id,
    )

    resumed, command = _request_collector_command(
        paused,
        collectors.OperationalMarketDataCollectorCommandType.RESUME,
        requested_at=START_AT + timedelta(seconds=2),
        key="collector:resume:backdated",
    )

    assert resumed.desired_state.value == "RUNNING"

    with psycopg.connect(database_url) as connection:
        with pytest.raises(
            psycopg.Error,
            match="collector_command_chronology_invalid",
        ):
            _insert_row(
                connection,
                COMMANDS,
                _command_row(command),
            )


def test_postgres_stop_from_pending_and_settlement_are_valid(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    epoch, start_command = _start(
        auth_user_id,
        key="collector:stop:start",
    )

    _persist_collector_start(
        database_url,
        epoch,
        start_command,
    )

    stop_requested, stop_command = _request_collector_command(
        epoch,
        collectors.OperationalMarketDataCollectorCommandType.STOP,
        requested_at=START_AT + timedelta(seconds=1),
        key="collector:stop:pending",
    )

    _persist_collector_command(
        database_url,
        stop_requested,
        stop_command,
    )

    stopped = collectors.settle_unclaimed_operational_market_data_collector_epoch(
        stop_requested,
        observed_at=START_AT + timedelta(seconds=2),
    )

    _persist_domain_epoch_change(
        database_url,
        stopped,
    )

    assert stopped.desired_state.value == "STOPPED"
    assert stopped.observed_state.value == "STOPPED"
    assert stopped.terminal_at == START_AT + timedelta(seconds=2)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("UPDATE", "collector_command_update_forbidden"),
        ("DELETE", "collector_command_delete_forbidden"),
    ],
)
def test_postgres_command_history_is_append_only(
    database_url: str,
    auth_user_id: UUID,
    operation: str,
    message: str,
) -> None:
    epoch, command = _start(
        auth_user_id,
        key=f"append-only:{operation.lower()}",
    )

    _persist_collector_start(
        database_url,
        epoch,
        command,
    )

    with psycopg.connect(database_url) as connection:
        with pytest.raises(
            psycopg.Error,
            match=message,
        ):
            if operation == "UPDATE":
                connection.execute(
                    "update "
                    "public.operational_market_data_collector_commands "
                    "set idempotency_key = 'changed' "
                    "where command_id = %s",
                    (command.command_id,),
                )
            else:
                connection.execute(
                    "delete from "
                    "public.operational_market_data_collector_commands "
                    "where command_id = %s",
                    (command.command_id,),
                )


def test_postgres_command_functions_are_not_data_api_authority(
    database_url: str,
) -> None:
    functions = (
        "op_md_collector_command_allowed(text,text,text)",
        "validate_op_md_collector_command_insert()",
        "protect_op_md_collector_command()",
        "assert_op_md_collector_epoch_start_command()",
        "assert_op_md_collector_command_applied()",
    )

    with psycopg.connect(database_url) as connection:
        for function_name in functions:
            for role in (
                "anon",
                "authenticated",
                "service_role",
            ):
                assert connection.execute(
                    "select has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, function_name),
                ).fetchone() == (False,)
