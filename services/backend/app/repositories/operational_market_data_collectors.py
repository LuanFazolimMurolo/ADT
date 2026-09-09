"""Transactional PostgreSQL authority for collector epochs and worker capabilities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn
from uuid import UUID, uuid4

from psycopg import Error, sql
from psycopg.types.json import Jsonb

import app.operational_market_data_collectors as collectors
from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError

_MAX_BIGINT = (1 << 63) - 1
_EPOCHS = "operational_market_data_collector_epochs"
_COMMANDS = "operational_market_data_collector_commands"


def _uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
    return value


def _int(value: object) -> int:
    if type(value) is not int:
        raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
    return value


def _now(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
    return value.astimezone(UTC)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
    return value.astimezone(UTC)


def _version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_BIGINT:
        raise collectors.OperationalMarketDataCollectorRecordVersionConflictError()
    return value


def _specification_from_row(
    row: Mapping[str, object],
) -> collectors.OperationalMarketDataCollectorSpecification:
    payload = row["targets"]
    if not isinstance(payload, list):
        raise ValueError
    if not 1 <= len(payload) <= collectors.MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_TARGETS:
        raise ValueError
    targets = []
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"symbol", "timeframe", "bootstrap_candles"}:
            raise ValueError
        target = collectors.OperationalMarketDataCollectorTarget(
            symbol=_text(item["symbol"]),
            timeframe=_text(item["timeframe"]),
            bootstrap_candles=_int(item["bootstrap_candles"]),
        )
        if target.symbol != item["symbol"] or target.timeframe != item["timeframe"]:
            raise ValueError
        targets.append(target)
    return collectors.OperationalMarketDataCollectorSpecification(
        schema_version=_int(row["schema_version"]),
        collector_contract_version=_int(row["collector_contract_version"]),
        scope=collectors.OperationalMarketDataCollectorScope(_text(row["scope"])),
        targets=tuple(targets),
        interval_seconds=_int(row["interval_seconds"]),
        overlap_candles=_int(row["overlap_candles"]),
    )


def operational_market_data_collector_epoch_from_row(
    row: Mapping[str, object],
) -> collectors.OperationalMarketDataCollectorEpoch:
    """Hydrate and revalidate persisted provenance, canonical checksum and runtime shape."""
    try:
        worker_columns = (
            "worker_id",
            "worker_claimed_at",
            "worker_heartbeat_at",
            "worker_lease_expires_at",
        )
        worker = None
        if any(row[key] is not None for key in worker_columns):
            worker = collectors.OperationalMarketDataCollectorWorkerClaim(
                epoch_id=_uuid(row["epoch_id"]),
                worker_id=_uuid(row["worker_id"]),
                fencing_token=_int(row["fencing_token"]),
                claimed_at=_timestamp(row["worker_claimed_at"]),
                heartbeat_at=_timestamp(row["worker_heartbeat_at"]),
                lease_expires_at=_timestamp(row["worker_lease_expires_at"]),
            )
        failure = None
        if row["failure_code"] is not None or row["failure_at"] is not None:
            failure = collectors.OperationalMarketDataCollectorFailure(
                code=collectors.OperationalMarketDataCollectorFailureCode(
                    _text(row["failure_code"])
                ),
                failed_at=_timestamp(row["failure_at"]),
            )
        return collectors.OperationalMarketDataCollectorEpoch(
            epoch_id=_uuid(row["epoch_id"]),
            schema_version=_int(row["schema_version"]),
            collector_contract_version=_int(row["collector_contract_version"]),
            scope=collectors.OperationalMarketDataCollectorScope(_text(row["scope"])),
            specification=_specification_from_row(row),
            specification_checksum=_text(row["specification_checksum"]),
            desired_state=collectors.OperationalMarketDataCollectorDesiredState(
                _text(row["desired_state"])
            ),
            observed_state=collectors.OperationalMarketDataCollectorObservedState(
                _text(row["observed_state"])
            ),
            record_version=_int(row["record_version"]),
            fencing_token=_int(row["fencing_token"]),
            epoch_checksum=_text(row["epoch_checksum"]),
            start_requested_by=_uuid(row["start_requested_by"]),
            start_requested_at=_timestamp(row["start_requested_at"]),
            start_idempotency_key=_text(row["start_idempotency_key"]),
            start_intent_fingerprint=_text(row["start_intent_fingerprint"]),
            worker_claim=worker,
            failure=failure,
            terminal_at=None if row["terminal_at"] is None else _timestamp(row["terminal_at"]),
        )
    except (DomainError, KeyError, TypeError, ValueError, OverflowError) as error:
        raise PersistenceError() from error


def operational_market_data_collector_command_from_row(
    row: Mapping[str, object],
) -> collectors.OperationalMarketDataCollectorCommand:
    """Decode the SQL START predecessor sentinel only after checking it is exactly zero."""
    try:
        kind = collectors.OperationalMarketDataCollectorCommandType(_text(row["command_type"]))
        expected = _int(row["expected_record_version"])
        if kind is collectors.OperationalMarketDataCollectorCommandType.START and expected != 0:
            raise ValueError
        return collectors.OperationalMarketDataCollectorCommand(
            command_id=_uuid(row["command_id"]),
            command_contract_version=_int(row["command_contract_version"]),
            epoch_id=_uuid(row["epoch_id"]),
            epoch_checksum=_text(row["epoch_checksum"]),
            command_type=kind,
            desired_state=collectors.OperationalMarketDataCollectorDesiredState(
                _text(row["desired_state"])
            ),
            expected_record_version=None
            if kind is collectors.OperationalMarketDataCollectorCommandType.START
            else expected,
            resulting_record_version=_int(row["resulting_record_version"]),
            actor_id=_uuid(row["actor_id"]),
            requested_at=_timestamp(row["requested_at"]),
            idempotency_key=_text(row["idempotency_key"]),
            intent_fingerprint=_text(row["intent_fingerprint"]),
        )
    except (DomainError, KeyError, TypeError, ValueError, OverflowError) as error:
        raise PersistenceError() from error


def _epoch_values(epoch: collectors.OperationalMarketDataCollectorEpoch) -> dict[str, object]:
    values = {
        field.name: getattr(epoch, field.name)
        for field in fields(epoch)
        if field.name not in {"specification", "worker_claim", "failure"}
    }
    specification = epoch.specification
    values.update(
        scope=epoch.scope.value,
        desired_state=epoch.desired_state.value,
        observed_state=epoch.observed_state.value,
        targets=Jsonb(
            [
                {
                    "symbol": target.symbol,
                    "timeframe": target.timeframe,
                    "bootstrap_candles": target.bootstrap_candles,
                }
                for target in specification.targets
            ]
        ),
        interval_seconds=specification.interval_seconds,
        overlap_candles=specification.overlap_candles,
    )
    claim = epoch.worker_claim
    values.update(
        worker_id=claim.worker_id if claim else None,
        worker_claimed_at=claim.claimed_at if claim else None,
        worker_heartbeat_at=claim.heartbeat_at if claim else None,
        worker_lease_expires_at=claim.lease_expires_at if claim else None,
        failure_code=epoch.failure.code.value if epoch.failure else None,
        failure_at=epoch.failure.failed_at if epoch.failure else None,
    )
    return values


def _command_values(command: collectors.OperationalMarketDataCollectorCommand) -> dict[str, object]:
    values = {field.name: getattr(command, field.name) for field in fields(command)}
    values.update(
        command_type=command.command_type.value,
        desired_state=command.desired_state.value,
        expected_record_version=(
            0 if command.expected_record_version is None else command.expected_record_version
        ),
    )
    return values


async def _insert(
    connection: DatabaseConnection,
    table: str,
    values: Mapping[str, object],
) -> Mapping[str, object]:
    cursor = await connection.execute(
        sql.SQL("insert into public.{} ({}) values ({}) returning *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ),
        tuple(values.values()),
    )
    row = await cursor.fetchone()
    if row is None:
        raise PersistenceError()
    return row


async def _epoch(
    connection: DatabaseConnection,
    epoch_id: UUID,
    *,
    lock: bool = False,
) -> collectors.OperationalMarketDataCollectorEpoch | None:
    cursor = await connection.execute(
        "select * from public.operational_market_data_collector_epochs where epoch_id = %s"
        + (" for update" if lock else ""),
        (epoch_id,),
    )
    row = await cursor.fetchone()
    return None if row is None else operational_market_data_collector_epoch_from_row(row)


async def _command_replay(
    connection: DatabaseConnection,
    actor_id: UUID,
    key: str,
) -> collectors.OperationalMarketDataCollectorCommand | None:
    cursor = await connection.execute(
        "select * from public.operational_market_data_collector_commands "
        "where actor_id = %s and idempotency_key = %s",
        (actor_id, key),
    )
    row = await cursor.fetchone()
    return None if row is None else operational_market_data_collector_command_from_row(row)


_DATABASE_MESSAGES: dict[str, type[DomainError]] = {
    "operational_market_data_collector_command_chronology_invalid": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_command_delete_forbidden": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_command_desired_transition_invalid": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_command_epoch_checksum_mismatch": (
        collectors.OperationalMarketDataCollectorChecksumMismatchError
    ),
    "operational_market_data_collector_command_epoch_missing": (
        collectors.OperationalMarketDataCollectorNotFoundError
    ),
    "operational_market_data_collector_command_not_allowed": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_command_not_applied": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_command_record_version_conflict": (
        collectors.OperationalMarketDataCollectorRecordVersionConflictError
    ),
    "operational_market_data_collector_command_requested_at_invalid": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_command_update_forbidden": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_epoch_claim_transition_invalid": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_epoch_command_mixed_mutation": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_epoch_command_required": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_epoch_command_type_mismatch": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_epoch_delete_forbidden": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_desired_transition_forbidden": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_failure_at_invalid": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_fence_mutation_invalid": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_epoch_fencing_token_invalid": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_epoch_immutable_fields_changed": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_initial_state_invalid": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_lease_renewal_invalid": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_epoch_new_claim_invalid": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_epoch_noop_mutation": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_observed_transition_forbidden": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_record_version_conflict": (
        collectors.OperationalMarketDataCollectorRecordVersionConflictError
    ),
    "operational_market_data_collector_epoch_recovery_invalid": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_epoch_start_command_required": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_epoch_terminal": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_terminal_at_invalid": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_unclaimed_transition_invalid": (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
    ),
    "operational_market_data_collector_epoch_unexpected_command_version": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_epoch_worker_identity_changed": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_epoch_worker_release_invalid": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_epoch_worker_requires_new_fence": (
        collectors.OperationalMarketDataCollectorLeaseError
    ),
    "operational_market_data_collector_start_command_mismatch": (
        collectors.OperationalMarketDataCollectorCommandConflictError
    ),
    "operational_market_data_collector_target_bootstrap_invalid": (
        collectors.InvalidOperationalMarketDataCollectorSpecificationError
    ),
    "operational_market_data_collector_target_shape_invalid": (
        collectors.InvalidOperationalMarketDataCollectorSpecificationError
    ),
    "operational_market_data_collector_target_symbol_invalid": (
        collectors.InvalidOperationalMarketDataCollectorSpecificationError
    ),
    "operational_market_data_collector_target_timeframe_invalid": (
        collectors.InvalidOperationalMarketDataCollectorSpecificationError
    ),
    "operational_market_data_collector_target_type_invalid": (
        collectors.InvalidOperationalMarketDataCollectorSpecificationError
    ),
    "operational_market_data_collector_targets_order_invalid": (
        collectors.InvalidOperationalMarketDataCollectorSpecificationError
    ),
    "operational_market_data_collector_targets_shape_invalid": (
        collectors.InvalidOperationalMarketDataCollectorSpecificationError
    ),
}


def _raise_database_error(error: Error) -> NoReturn:
    """Translate closed trigger/constraint failures without exposing SQL diagnostics."""
    message = error.diag.message_primary or ""
    constraint = error.diag.constraint_name
    if error.sqlstate in {"40001", "40P01"} or (
        constraint == "op_md_collector_command_epoch_result_version_key"
    ):
        raise collectors.OperationalMarketDataCollectorRecordVersionConflictError() from error
    if constraint in {
        "op_md_collector_epoch_actor_start_idempotency_key",
        "op_md_collector_command_actor_idempotency_key",
    }:
        raise collectors.OperationalMarketDataCollectorIdempotencyConflictError() from error
    if constraint == "op_md_collector_epoch_one_current_per_scope_uidx":
        raise collectors.OperationalMarketDataCollectorCurrentEpochConflictError() from error
    mapped = _DATABASE_MESSAGES.get(message)
    if mapped is not None:
        raise mapped() from error
    raise_domain_error(error)


class PostgresOperationalMarketDataCollectorRepository:
    """Bounded database-only adapter; operation times are injected authoritative UTC times.

    START replay returns the current form of the original historical epoch. Command
    replay returns the original immutable command (including resulting record version),
    never a fabricated historical snapshot of the mutable epoch.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, epoch_id: UUID) -> collectors.OperationalMarketDataCollectorEpoch | None:
        epoch_id = _uuid(epoch_id)
        try:
            async with self._database.transaction() as connection:
                return await _epoch(connection, epoch_id)
        except Error as error:
            _raise_database_error(error)

    async def get_current_for_scope(
        self,
        scope: collectors.OperationalMarketDataCollectorScope = (
            collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW
        ),
    ) -> collectors.OperationalMarketDataCollectorEpoch | None:
        if scope is not collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW:
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    "select * from public.operational_market_data_collector_epochs "
                    "where scope = %s and observed_state not in ('STOPPED', 'FAILED')",
                    (scope.value,),
                )
                row = await cursor.fetchone()
                return (
                    None if row is None else operational_market_data_collector_epoch_from_row(row)
                )
        except Error as error:
            _raise_database_error(error)

    async def list_commands(
        self,
        epoch_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[collectors.OperationalMarketDataCollectorCommand]:
        epoch_id = _uuid(epoch_id)
        if type(limit) is not int or type(offset) is not int:
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
        if not 1 <= limit <= 100 or not 0 <= offset <= _MAX_BIGINT:
            raise collectors.OperationalMarketDataCollectorBoundsExceededError()
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    "select * from public.operational_market_data_collector_commands "
                    "where epoch_id = %s "
                    "order by resulting_record_version, requested_at, command_id "
                    "limit %s offset %s",
                    (epoch_id, limit, offset),
                )
                return [
                    operational_market_data_collector_command_from_row(row)
                    for row in await cursor.fetchall()
                ]
        except Error as error:
            _raise_database_error(error)

    async def list_nonterminal(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[collectors.OperationalMarketDataCollectorEpoch]:
        """Return a bounded deterministic page of persisted nonterminal epochs."""
        if type(limit) is not int or type(offset) is not int:
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
        if not 1 <= limit <= 100 or not 0 <= offset <= _MAX_BIGINT:
            raise collectors.OperationalMarketDataCollectorBoundsExceededError()

        states = (
            collectors.OperationalMarketDataCollectorObservedState.PENDING,
            collectors.OperationalMarketDataCollectorObservedState.STARTING,
            collectors.OperationalMarketDataCollectorObservedState.RUNNING,
            collectors.OperationalMarketDataCollectorObservedState.PAUSED,
            collectors.OperationalMarketDataCollectorObservedState.RECOVERING,
            collectors.OperationalMarketDataCollectorObservedState.STOPPING,
        )

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    "select * "
                    "from public.operational_market_data_collector_epochs "
                    "where observed_state in (%s, %s, %s, %s, %s, %s) "
                    "order by start_requested_at asc, epoch_id asc "
                    "limit %s offset %s",
                    (
                        *(state.value for state in states),
                        limit,
                        offset,
                    ),
                )
                rows = await cursor.fetchall()
                return [operational_market_data_collector_epoch_from_row(row) for row in rows]
        except Error as error:
            _raise_database_error(error)

    async def resolve_start_replay(
        self,
        intent: collectors.OperationalMarketDataCollectorStartIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> collectors.OperationalMarketDataCollectorEpoch | None:
        """Read historical START reuse without requiring current execution eligibility.

        Return the current persisted form of the original epoch, including terminal
        history. A miss grants no creation authority: START must still perform its
        own transactional replay check after the caller validates fresh eligibility.
        """
        fingerprint = collectors.operational_market_data_collector_start_intent_fingerprint(intent)
        actor_id = _uuid(actor_id)
        key = collectors.validate_operational_market_data_collector_idempotency_key(idempotency_key)
        try:
            async with self._database.transaction() as connection:
                return await self._start_replay(connection, actor_id, key, fingerprint)
        except Error as error:
            _raise_database_error(error)

    async def start(
        self,
        specification: collectors.OperationalMarketDataCollectorSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        if not isinstance(specification, collectors.OperationalMarketDataCollectorSpecification):
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
        specification = replace(specification)
        actor_id, now = _uuid(actor_id), _now(now)
        key = collectors.validate_operational_market_data_collector_idempotency_key(idempotency_key)
        intent = collectors.OperationalMarketDataCollectorStartIntent(
            specification_checksum=collectors.operational_market_data_collector_specification_checksum(
                specification
            ),
        )
        fingerprint = collectors.operational_market_data_collector_start_intent_fingerprint(intent)
        checksum = collectors.operational_market_data_collector_specification_checksum(
            specification
        )
        try:
            async with self._database.transaction() as connection:
                existing = await self._start_replay(
                    connection, actor_id, key, fingerprint, checksum
                )
                if existing is not None:
                    return existing
                epoch, command = collectors.start_operational_market_data_collector_epoch(
                    epoch_id=uuid4(),
                    command_id=uuid4(),
                    specification=specification,
                    start_intent=intent,
                    requested_by=actor_id,
                    requested_at=now,
                    idempotency_key=key,
                )
                stored = operational_market_data_collector_epoch_from_row(
                    await _insert(connection, _EPOCHS, _epoch_values(epoch)),
                )
                saved_command = operational_market_data_collector_command_from_row(
                    await _insert(connection, _COMMANDS, _command_values(command)),
                )
                if stored != epoch or saved_command != command:
                    raise PersistenceError()
                return stored
        except Error as error:
            # The original transaction has rolled back, including deferred failures.
            # Replay precedes current-epoch conflict even when concurrent START won.
            try:
                async with self._database.transaction() as connection:
                    replay = await self._start_replay(
                        connection,
                        actor_id,
                        key,
                        fingerprint,
                        checksum,
                    )
                    if replay is not None:
                        return replay
            except Error as recovery_error:
                _raise_database_error(recovery_error)
            _raise_database_error(error)

    @staticmethod
    async def _start_replay(
        connection: DatabaseConnection,
        actor_id: UUID,
        key: str,
        fingerprint: str,
        checksum: str | None = None,
    ) -> collectors.OperationalMarketDataCollectorEpoch | None:
        command = await _command_replay(connection, actor_id, key)
        if command is None:
            return None
        if (
            command.command_type is not collectors.OperationalMarketDataCollectorCommandType.START
            or command.intent_fingerprint != fingerprint
        ):
            raise collectors.OperationalMarketDataCollectorIdempotencyConflictError()
        epoch = await _epoch(connection, command.epoch_id)
        if epoch is None:
            raise PersistenceError()
        if (
            (checksum is not None and epoch.specification_checksum != checksum)
            or epoch.start_requested_by != command.actor_id
            or epoch.start_idempotency_key != key
            or epoch.start_intent_fingerprint != command.intent_fingerprint
            or epoch.start_requested_at != command.requested_at
            or epoch.epoch_checksum != command.epoch_checksum
        ):
            raise PersistenceError()
        return epoch

    async def request_command(
        self,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        """Commit command + desired mutation, or return its original immutable replay result."""
        if not isinstance(intent, collectors.OperationalMarketDataCollectorCommandIntent):
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()
        intent = replace(intent)
        actor_id, now = _uuid(actor_id), _now(now)
        key = collectors.validate_operational_market_data_collector_idempotency_key(idempotency_key)
        fingerprint = collectors.operational_market_data_collector_command_intent_fingerprint(
            intent
        )
        try:
            async with self._database.transaction() as connection:
                replay = await _command_replay(connection, actor_id, key)
                if replay is not None:
                    return self._check_command_replay(replay, fingerprint, intent)
                current = await _epoch(connection, intent.epoch_id, lock=True)
                # Recheck after waiting for an in-flight command on this epoch.
                replay = await _command_replay(connection, actor_id, key)
                if replay is not None:
                    return self._check_command_replay(replay, fingerprint, intent)
                if current is None:
                    raise collectors.OperationalMarketDataCollectorNotFoundError()
                if current.epoch_checksum != intent.epoch_checksum:
                    raise collectors.OperationalMarketDataCollectorChecksumMismatchError()
                if current.record_version != intent.expected_record_version:
                    raise collectors.OperationalMarketDataCollectorRecordVersionConflictError()
                target, command = collectors.request_operational_market_data_collector_command(
                    current,
                    command_id=uuid4(),
                    intent=intent,
                    actor_id=actor_id,
                    requested_at=now,
                    idempotency_key=key,
                )
                stored = operational_market_data_collector_command_from_row(
                    await _insert(connection, _COMMANDS, _command_values(command)),
                )
                await self._update(connection, current, target, mode="admin", now=now)
                if stored != command:
                    raise PersistenceError()
                return stored
        except Error as error:
            try:
                async with self._database.transaction() as connection:
                    replay = await _command_replay(connection, actor_id, key)
                    if replay is not None:
                        return self._check_command_replay(replay, fingerprint, intent)
            except Error as recovery_error:
                _raise_database_error(recovery_error)
            _raise_database_error(error)

    @staticmethod
    def _check_command_replay(
        command: collectors.OperationalMarketDataCollectorCommand,
        fingerprint: str,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        if (
            command.intent_fingerprint != fingerprint
            or command.epoch_id != intent.epoch_id
            or command.epoch_checksum != intent.epoch_checksum
            or command.command_type != intent.command_type
            or command.expected_record_version != intent.expected_record_version
        ):
            raise collectors.OperationalMarketDataCollectorIdempotencyConflictError()
        return command

    async def _mutate(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        now: datetime,
        transform: Callable[
            [collectors.OperationalMarketDataCollectorEpoch],
            collectors.OperationalMarketDataCollectorEpoch,
        ],
        mode: Literal["claim", "recover", "worker", "renew", "unclaimed"],
        worker_id: UUID | None = None,
        fencing_token: int | None = None,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        epoch_id, now = _uuid(epoch_id), _now(now)
        expected_record_version = _version(expected_record_version)
        if worker_id is not None:
            worker_id = _uuid(worker_id)
        if mode in ("worker", "renew"):
            if type(fencing_token) is not int or not 1 <= fencing_token <= _MAX_BIGINT:
                raise collectors.OperationalMarketDataCollectorLeaseError()
        try:
            async with self._database.transaction() as connection:
                current = await _epoch(connection, epoch_id, lock=True)
                if current is None:
                    raise collectors.OperationalMarketDataCollectorNotFoundError()
                if mode in ("worker", "renew"):
                    claim = current.worker_claim
                    if (
                        claim is None
                        or claim.worker_id != worker_id
                        or claim.fencing_token != fencing_token
                        or not claim.is_active(now)
                    ):
                        raise collectors.OperationalMarketDataCollectorLeaseError()
                if current.record_version != expected_record_version:
                    raise collectors.OperationalMarketDataCollectorRecordVersionConflictError()
                if mode == "recover" and current.worker_claim is not None:
                    if current.worker_claim.worker_id == worker_id:
                        raise collectors.OperationalMarketDataCollectorLeaseError()
                target = transform(current)
                if target == current:
                    return current
                return await self._update(connection, current, target, mode=mode, now=now)
        except Error as error:
            _raise_database_error(error)

    @staticmethod
    async def _update(
        connection: DatabaseConnection,
        current: collectors.OperationalMarketDataCollectorEpoch,
        target: collectors.OperationalMarketDataCollectorEpoch,
        *,
        mode: Literal["admin", "claim", "recover", "worker", "renew", "unclaimed"],
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        values = _epoch_values(target)
        mutable = (
            "desired_state",
            "observed_state",
            "record_version",
            "fencing_token",
            "worker_id",
            "worker_claimed_at",
            "worker_heartbeat_at",
            "worker_lease_expires_at",
            "failure_code",
            "failure_at",
            "terminal_at",
        )
        conditions = [
            "epoch_id = %s",
            "record_version = %s",
            "observed_state = %s",
            "desired_state = %s",
            "fencing_token = %s",
        ]
        parameters: list[object] = [
            current.epoch_id,
            current.record_version,
            current.observed_state.value,
            current.desired_state.value,
            current.fencing_token,
        ]
        current_values = _epoch_values(current)
        for column in (
            "worker_id",
            "worker_claimed_at",
            "worker_heartbeat_at",
            "worker_lease_expires_at",
        ):
            conditions.append(f"{column} is not distinct from %s")
            parameters.append(current_values[column])
        if mode in ("worker", "renew", "recover"):
            claim = current.worker_claim
            if claim is None:
                raise collectors.OperationalMarketDataCollectorLeaseError()
            conditions.append("worker_id = %s")
            parameters.append(claim.worker_id)
            if mode == "recover":
                conditions.append("worker_lease_expires_at <= %s")
                parameters.append(now)
            else:
                conditions.extend(["worker_heartbeat_at <= %s", "worker_lease_expires_at > %s"])
                parameters.extend([now, now])
            if mode == "renew":
                assert target.worker_claim is not None
                conditions.extend(
                    ["worker_heartbeat_at < %s", "worker_lease_expires_at < %s", "%s > %s"]
                )
                parameters.extend(
                    [
                        now,
                        target.worker_claim.lease_expires_at,
                        target.worker_claim.lease_expires_at,
                        now,
                    ]
                )
        elif mode in ("claim", "unclaimed"):
            conditions.append("worker_id is null")
        cursor = await connection.execute(
            sql.SQL("update public.{} set {} where {} returning *").format(
                sql.Identifier(_EPOCHS),
                sql.SQL(", ").join(
                    sql.SQL("{} = %s").format(sql.Identifier(key)) for key in mutable
                ),
                sql.SQL(" and ").join(map(sql.SQL, conditions)),
            ),
            (*[values[key] for key in mutable], *parameters),
        )
        row = await cursor.fetchone()
        if row is None:
            # Locking normally precludes this. Diagnose a rejected conditional mutation
            # explicitly rather than confusing a stale capability with missing identity.
            persisted = await _epoch(connection, current.epoch_id, lock=True)
            if persisted is None:
                raise collectors.OperationalMarketDataCollectorNotFoundError()
            if mode in ("worker", "renew", "recover"):
                if (
                    persisted.worker_claim is None
                    or current.worker_claim is None
                    or persisted.worker_claim.worker_id != current.worker_claim.worker_id
                    or persisted.fencing_token != current.fencing_token
                    or (mode in ("worker", "renew") and not persisted.worker_claim.is_active(now))
                ):
                    raise collectors.OperationalMarketDataCollectorLeaseError()
            if persisted.record_version != current.record_version:
                raise collectors.OperationalMarketDataCollectorRecordVersionConflictError()
            if (
                persisted.worker_claim != current.worker_claim
                or persisted.fencing_token != current.fencing_token
                or (
                    mode == "recover"
                    and persisted.worker_claim is not None
                    and not persisted.worker_claim.is_expired(now)
                )
            ):
                raise collectors.OperationalMarketDataCollectorLeaseError()
            raise collectors.OperationalMarketDataCollectorStateTransitionConflictError()
        result = operational_market_data_collector_epoch_from_row(row)
        if result != target:
            raise PersistenceError()
        return result

    async def claim(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        lease_expires_at: datetime,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain claim transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="claim",
            worker_id=worker_id,
            transform=lambda epoch: collectors.claim_operational_market_data_collector_epoch(
                epoch,
                claimed_at=now,
                worker_id=worker_id,
                lease_expires_at=_now(lease_expires_at),
            ),
        )

    async def recover(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        lease_expires_at: datetime,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain recover transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="recover",
            worker_id=worker_id,
            transform=lambda epoch: collectors.recover_operational_market_data_collector_epoch(
                epoch,
                recovered_at=now,
                worker_id=worker_id,
                lease_expires_at=_now(lease_expires_at),
            ),
        )

    async def renew(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        fencing_token: int,
        lease_expires_at: datetime,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain renew transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="renew",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: collectors.renew_operational_market_data_collector_worker_claim(
                epoch,
                heartbeat_at=now,
                worker_id=worker_id,
                fencing_token=fencing_token,
                lease_expires_at=_now(lease_expires_at),
            ),
        )

    async def mark_starting(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        fencing_token: int,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain mark_starting transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: (
                collectors.mark_operational_market_data_collector_epoch_starting(
                    epoch,
                    observed_at=now,
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                )
            ),
        )

    async def mark_running(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        fencing_token: int,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain mark_running transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: collectors.mark_operational_market_data_collector_epoch_running(
                epoch,
                observed_at=now,
                worker_id=worker_id,
                fencing_token=fencing_token,
            ),
        )

    async def mark_stopping(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        fencing_token: int,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain mark_stopping transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: (
                collectors.mark_operational_market_data_collector_epoch_stopping(
                    epoch,
                    observed_at=now,
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                )
            ),
        )

    async def settle_paused(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        fencing_token: int,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain settle_paused transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: (
                collectors.settle_operational_market_data_collector_epoch_paused(
                    epoch,
                    observed_at=now,
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                )
            ),
        )

    async def settle_stopped(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        fencing_token: int,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain settle_stopped transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: (
                collectors.settle_operational_market_data_collector_epoch_stopped(
                    epoch,
                    observed_at=now,
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                )
            ),
        )

    async def settle_unclaimed(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain settle_unclaimed transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="unclaimed",
            transform=lambda epoch: (
                collectors.settle_unclaimed_operational_market_data_collector_epoch(
                    epoch,
                    observed_at=now,
                )
            ),
        )

    async def fail_claimed(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        fencing_token: int,
        failure_code: collectors.OperationalMarketDataCollectorFailureCode,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain fail_claimed transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: collectors.fail_claimed_operational_market_data_collector_epoch(
                epoch,
                failed_at=now,
                worker_id=worker_id,
                fencing_token=fencing_token,
                failure_code=failure_code,
            ),
        )

    async def fail_unclaimed(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        failure_code: collectors.OperationalMarketDataCollectorFailureCode,
        now: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Persist the domain fail_unclaimed transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="unclaimed",
            transform=lambda epoch: (
                collectors.fail_unclaimed_operational_market_data_collector_epoch(
                    epoch,
                    failed_at=now,
                    failure_code=failure_code,
                )
            ),
        )
