"""Transactional PostgreSQL authority for paper run epochs and worker capabilities."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn
from uuid import UUID, uuid4

from psycopg import Error, sql

import app.operational_paper_session_runs as runs
from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationProfileBinding,
)

_MAX_BIGINT = (1 << 63) - 1
_EPOCHS = "operational_paper_session_run_epochs"
_COMMANDS = "operational_paper_session_run_commands"


def _uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise runs.InvalidOperationalPaperSessionRunSpecificationError()
    return value


def _int(value: object) -> int:
    if type(value) is not int:
        raise runs.InvalidOperationalPaperSessionRunSpecificationError()
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise runs.InvalidOperationalPaperSessionRunSpecificationError()
    return value


def _now(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise runs.InvalidOperationalPaperSessionRunSpecificationError()
    return value.astimezone(UTC)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise runs.InvalidOperationalPaperSessionRunSpecificationError()
    return value.astimezone(UTC)


def _version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_BIGINT:
        raise runs.OperationalPaperSessionRunRecordVersionConflictError()
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise runs.InvalidOperationalPaperSessionRunSpecificationError()
    return value


def operational_paper_session_run_epoch_from_row(
    row: Mapping[str, object],
) -> runs.OperationalPaperSessionRunEpoch:
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
            worker = runs.OperationalPaperSessionRunWorkerClaim(
                epoch_id=_uuid(row["epoch_id"]),
                worker_id=_uuid(row["worker_id"]),
                fencing_token=_int(row["fencing_token"]),
                claimed_at=_timestamp(row["worker_claimed_at"]),
                heartbeat_at=_timestamp(row["worker_heartbeat_at"]),
                lease_expires_at=_timestamp(row["worker_lease_expires_at"]),
            )
        failure = None
        if row["failure_code"] is not None or row["failure_at"] is not None:
            failure = runs.OperationalPaperSessionRunFailure(
                code=runs.OperationalPaperSessionRunFailureCode(_text(row["failure_code"])),
                failed_at=_timestamp(row["failure_at"]),
            )
        return runs.OperationalPaperSessionRunEpoch(
            epoch_id=_uuid(row["epoch_id"]),
            schema_version=_int(row["schema_version"]),
            run_contract_version=_int(row["run_contract_version"]),
            desired_state=runs.OperationalPaperSessionRunDesiredState(_text(row["desired_state"])),
            observed_state=runs.OperationalPaperSessionRunObservedState(
                _text(row["observed_state"])
            ),
            record_version=_int(row["record_version"]),
            fencing_token=_int(row["fencing_token"]),
            activation_id=_uuid(row["activation_id"]),
            activation_checksum=_text(row["activation_checksum"]),
            materialization_id=_uuid(row["materialization_id"]),
            materialization_checksum=_text(row["materialization_checksum"]),
            authorization_binding=OperationalPaperSessionMaterializationAuthorizationBinding(
                authorization_id=_uuid(row["authorization_id"]),
                authorization_checksum=_text(row["authorization_checksum"]),
            ),
            profile_binding=OperationalPaperSessionMaterializationProfileBinding(
                profile_id=_uuid(row["profile_id"]),
                approved_revision=_int(row["profile_approved_revision"]),
                specification_checksum=_text(row["profile_specification_checksum"]),
            ),
            mandate_binding=OperationalPaperSessionMaterializationMandateBinding(
                mandate_id=_uuid(row["mandate_id"]),
                approved_revision=_int(row["mandate_approved_revision"]),
                specification_checksum=_text(row["mandate_specification_checksum"]),
            ),
            simulation_id=_uuid(row["simulation_id"]),
            session_id=_text(row["session_id"]),
            config_checksum=_text(row["config_checksum"]),
            epoch_checksum=_text(row["epoch_checksum"]),
            start_requested_by=_uuid(row["start_requested_by"]),
            start_requested_at=_timestamp(row["start_requested_at"]),
            start_idempotency_key=_text(row["start_idempotency_key"]),
            start_intent_fingerprint=_text(row["start_intent_fingerprint"]),
            worker_claim=worker,
            failure=failure,
            terminal_at=None if row["terminal_at"] is None else _timestamp(row["terminal_at"]),
        )
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def operational_paper_session_run_command_from_row(
    row: Mapping[str, object],
) -> runs.OperationalPaperSessionRunEpochCommand:
    """Decode the SQL START predecessor sentinel only after checking it is exactly zero."""
    try:
        kind = runs.OperationalPaperSessionRunCommandType(_text(row["command_type"]))
        expected = _int(row["expected_record_version"])
        if kind is runs.OperationalPaperSessionRunCommandType.START and expected != 0:
            raise ValueError
        return runs.OperationalPaperSessionRunEpochCommand(
            command_id=_uuid(row["command_id"]),
            command_contract_version=_int(row["command_contract_version"]),
            epoch_id=_uuid(row["epoch_id"]),
            epoch_checksum=_text(row["epoch_checksum"]),
            command_type=kind,
            desired_state=runs.OperationalPaperSessionRunDesiredState(_text(row["desired_state"])),
            expected_record_version=None
            if kind is runs.OperationalPaperSessionRunCommandType.START
            else expected,
            resulting_record_version=_int(row["resulting_record_version"]),
            actor_id=_uuid(row["actor_id"]),
            requested_at=_timestamp(row["requested_at"]),
            idempotency_key=_text(row["idempotency_key"]),
            intent_fingerprint=_text(row["intent_fingerprint"]),
        )
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def _epoch_values(epoch: runs.OperationalPaperSessionRunEpoch) -> dict[str, object]:
    excluded = {
        "authorization_binding",
        "profile_binding",
        "mandate_binding",
        "worker_claim",
        "failure",
    }
    values = {
        field.name: getattr(epoch, field.name)
        for field in fields(epoch)
        if field.name not in excluded
    }
    values.update(
        desired_state=epoch.desired_state.value,
        observed_state=epoch.observed_state.value,
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
    values.update(
        worker_id=claim.worker_id if claim else None,
        worker_claimed_at=claim.claimed_at if claim else None,
        worker_heartbeat_at=claim.heartbeat_at if claim else None,
        worker_lease_expires_at=claim.lease_expires_at if claim else None,
        failure_code=epoch.failure.code.value if epoch.failure else None,
        failure_at=epoch.failure.failed_at if epoch.failure else None,
    )
    return values


def _command_values(command: runs.OperationalPaperSessionRunEpochCommand) -> dict[str, object]:
    values = {field.name: getattr(command, field.name) for field in fields(command)}
    values.update(
        command_type=command.command_type.value,
        desired_state=command.desired_state.value,
        expected_record_version=command.expected_record_version or 0,
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
) -> runs.OperationalPaperSessionRunEpoch | None:
    cursor = await connection.execute(
        "select * from public.operational_paper_session_run_epochs where epoch_id = %s"
        + (" for update" if lock else ""),
        (epoch_id,),
    )
    row = await cursor.fetchone()
    return None if row is None else operational_paper_session_run_epoch_from_row(row)


async def _command_replay(
    connection: DatabaseConnection,
    actor_id: UUID,
    key: str,
) -> runs.OperationalPaperSessionRunEpochCommand | None:
    cursor = await connection.execute(
        "select * from public.operational_paper_session_run_commands "
        "where actor_id = %s and idempotency_key = %s",
        (actor_id, key),
    )
    row = await cursor.fetchone()
    return None if row is None else operational_paper_session_run_command_from_row(row)


def _raise_database_error(error: Error) -> NoReturn:
    """Translate closed trigger/constraint failures without exposing SQL diagnostics."""
    message = error.diag.message_primary or ""
    constraint = error.diag.constraint_name
    if error.sqlstate in {"40001", "40P01"} or (
        constraint == "op_ps_run_command_epoch_result_version_key"
    ):
        raise runs.OperationalPaperSessionRunRecordVersionConflictError() from error
    if constraint in {
        "op_ps_run_epoch_actor_start_idempotency_key",
        "op_ps_run_command_actor_idempotency_key",
    }:
        raise runs.OperationalPaperSessionRunIdempotencyConflictError() from error
    if constraint == "op_ps_run_epoch_one_current_per_session_uidx":
        raise runs.OperationalPaperSessionRunCurrentEpochConflictError() from error
    if message.startswith("operational_paper_session_run_"):
        if "record_version_conflict" in message or error.sqlstate == "40001":
            raise runs.OperationalPaperSessionRunRecordVersionConflictError() from error
        if "checksum_mismatch" in message or "binding_mismatch" in message:
            raise runs.OperationalPaperSessionRunChecksumMismatchError() from error
        if message in {
            "operational_paper_session_run_command_epoch_missing",
            "operational_paper_session_run_command_epoch_missing_at_commit",
        }:
            raise runs.OperationalPaperSessionRunNotFoundError() from error
        if any(term in message for term in ("lease_", "worker_", "fencing_", "recovery_")):
            raise runs.OperationalPaperSessionRunLeaseError() from error
        if "command_" in message:
            raise runs.OperationalPaperSessionRunCommandConflictError() from error
        raise runs.OperationalPaperSessionRunStateTransitionConflictError() from error
    raise_domain_error(error)


class PostgresOperationalPaperSessionRunRepository:
    """Bounded database-only adapter; operation times are injected authoritative UTC times.

    START replay returns the current form of the original historical epoch. Command
    replay returns the original immutable command (including resulting record version),
    never a fabricated historical snapshot of the mutable epoch.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, epoch_id: UUID) -> runs.OperationalPaperSessionRunEpoch | None:
        epoch_id = _uuid(epoch_id)
        try:
            async with self._database.transaction() as connection:
                return await _epoch(connection, epoch_id)
        except Error as error:
            _raise_database_error(error)

    async def get_current_for_session(
        self,
        session_id: str,
    ) -> runs.OperationalPaperSessionRunEpoch | None:
        session_id = _sha(session_id)
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    "select * from public.operational_paper_session_run_epochs "
                    "where session_id = %s and observed_state not in ('STOPPED', 'FAILED')",
                    (session_id,),
                )
                row = await cursor.fetchone()
                return None if row is None else operational_paper_session_run_epoch_from_row(row)
        except Error as error:
            _raise_database_error(error)

    async def list_commands(
        self,
        epoch_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[runs.OperationalPaperSessionRunEpochCommand]:
        epoch_id = _uuid(epoch_id)
        if type(limit) is not int or type(offset) is not int:
            raise runs.InvalidOperationalPaperSessionRunSpecificationError()
        if not 1 <= limit <= 100 or not 0 <= offset <= _MAX_BIGINT:
            raise runs.OperationalPaperSessionRunBoundsExceededError()
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    "select * from public.operational_paper_session_run_commands "
                    "where epoch_id = %s "
                    "order by resulting_record_version, requested_at, command_id "
                    "limit %s offset %s",
                    (epoch_id, limit, offset),
                )
                return [
                    operational_paper_session_run_command_from_row(row)
                    for row in await cursor.fetchall()
                ]
        except Error as error:
            _raise_database_error(error)

    async def list_nonterminal(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[runs.OperationalPaperSessionRunEpoch]:
        """Return a bounded deterministic page of persisted nonterminal epochs."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset < 1 << 63
        ):
            raise runs.OperationalPaperSessionRunBoundsExceededError()

        states = (
            runs.OperationalPaperSessionRunObservedState.PENDING,
            runs.OperationalPaperSessionRunObservedState.STARTING,
            runs.OperationalPaperSessionRunObservedState.RUNNING,
            runs.OperationalPaperSessionRunObservedState.PAUSED,
            runs.OperationalPaperSessionRunObservedState.RECOVERING,
            runs.OperationalPaperSessionRunObservedState.STOPPING,
        )

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    "select * "
                    "from public.operational_paper_session_run_epochs "
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
                return [operational_paper_session_run_epoch_from_row(row) for row in rows]
        except Error as error:
            _raise_database_error(error)

    async def resolve_start_replay(
        self,
        intent: runs.OperationalPaperSessionRunEpochStartIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> runs.OperationalPaperSessionRunEpoch | None:
        """Read historical START reuse without requiring current execution eligibility.

        Return the current persisted form of the original epoch, including terminal
        history. A miss grants no creation authority: START must still perform its
        own transactional replay check after the caller validates fresh eligibility.
        """
        fingerprint = runs.operational_paper_session_run_epoch_start_intent_fingerprint(intent)
        actor_id = _uuid(actor_id)
        key = runs.validate_operational_paper_session_run_idempotency_key(idempotency_key)
        try:
            async with self._database.transaction() as connection:
                return await self._start_replay(connection, actor_id, key, fingerprint)
        except Error as error:
            _raise_database_error(error)

    async def start(
        self,
        specification: runs.OperationalPaperSessionRunEpochSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> runs.OperationalPaperSessionRunEpoch:
        if not isinstance(specification, runs.OperationalPaperSessionRunEpochSpecification):
            raise runs.InvalidOperationalPaperSessionRunSpecificationError()
        specification = replace(specification)
        actor_id, now = _uuid(actor_id), _now(now)
        key = runs.validate_operational_paper_session_run_idempotency_key(idempotency_key)
        intent = runs.OperationalPaperSessionRunEpochStartIntent(
            activation_id=specification.activation_id,
            activation_checksum=specification.activation_checksum,
        )
        fingerprint = runs.operational_paper_session_run_epoch_start_intent_fingerprint(intent)
        checksum = runs.operational_paper_session_run_epoch_specification_checksum(specification)
        try:
            async with self._database.transaction() as connection:
                existing = await self._start_replay(
                    connection, actor_id, key, fingerprint, checksum
                )
                if existing is not None:
                    return existing
                epoch, command = runs.start_operational_paper_session_run_epoch(
                    epoch_id=uuid4(),
                    command_id=uuid4(),
                    specification=specification,
                    start_intent=intent,
                    requested_by=actor_id,
                    requested_at=now,
                    idempotency_key=key,
                )
                stored = operational_paper_session_run_epoch_from_row(
                    await _insert(connection, _EPOCHS, _epoch_values(epoch)),
                )
                saved_command = operational_paper_session_run_command_from_row(
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
    ) -> runs.OperationalPaperSessionRunEpoch | None:
        command = await _command_replay(connection, actor_id, key)
        if command is None:
            return None
        if (
            command.command_type is not runs.OperationalPaperSessionRunCommandType.START
            or command.intent_fingerprint != fingerprint
            or (checksum is not None and command.epoch_checksum != checksum)
        ):
            raise runs.OperationalPaperSessionRunIdempotencyConflictError()
        epoch = await _epoch(connection, command.epoch_id)
        if epoch is None:
            raise PersistenceError()
        if (
            epoch.start_requested_by != command.actor_id
            or epoch.start_idempotency_key != key
            or epoch.start_intent_fingerprint != command.intent_fingerprint
            or epoch.start_requested_at != command.requested_at
            or epoch.epoch_checksum != command.epoch_checksum
        ):
            raise PersistenceError()
        return epoch

    async def request_command(
        self,
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> runs.OperationalPaperSessionRunEpochCommand:
        """Commit command + desired mutation, or return its original immutable replay result."""
        if not isinstance(intent, runs.OperationalPaperSessionRunEpochCommandIntent):
            raise runs.InvalidOperationalPaperSessionRunSpecificationError()
        intent = replace(intent)
        actor_id, now = _uuid(actor_id), _now(now)
        key = runs.validate_operational_paper_session_run_idempotency_key(idempotency_key)
        fingerprint = runs.operational_paper_session_run_epoch_command_intent_fingerprint(intent)
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
                    raise runs.OperationalPaperSessionRunNotFoundError()
                if current.epoch_checksum != intent.epoch_checksum:
                    raise runs.OperationalPaperSessionRunChecksumMismatchError()
                if current.record_version != intent.expected_record_version:
                    raise runs.OperationalPaperSessionRunRecordVersionConflictError()
                target, command = runs.request_operational_paper_session_run_epoch_command(
                    current,
                    command_id=uuid4(),
                    intent=intent,
                    actor_id=actor_id,
                    requested_at=now,
                    idempotency_key=key,
                )
                stored = operational_paper_session_run_command_from_row(
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
        command: runs.OperationalPaperSessionRunEpochCommand,
        fingerprint: str,
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
    ) -> runs.OperationalPaperSessionRunEpochCommand:
        if (
            command.intent_fingerprint != fingerprint
            or command.epoch_id != intent.epoch_id
            or command.epoch_checksum != intent.epoch_checksum
            or command.command_type != intent.command_type
            or command.expected_record_version != intent.expected_record_version
        ):
            raise runs.OperationalPaperSessionRunIdempotencyConflictError()
        return command

    async def _mutate(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        now: datetime,
        transform: Callable[
            [runs.OperationalPaperSessionRunEpoch], runs.OperationalPaperSessionRunEpoch
        ],
        mode: Literal["claim", "recover", "worker", "renew", "unclaimed"],
        worker_id: UUID | None = None,
        fencing_token: int | None = None,
    ) -> runs.OperationalPaperSessionRunEpoch:
        epoch_id, now = _uuid(epoch_id), _now(now)
        expected_record_version = _version(expected_record_version)
        if worker_id is not None:
            worker_id = _uuid(worker_id)
        if mode in ("worker", "renew"):
            if type(fencing_token) is not int or not 1 <= fencing_token <= _MAX_BIGINT:
                raise runs.OperationalPaperSessionRunLeaseError()
        try:
            async with self._database.transaction() as connection:
                current = await _epoch(connection, epoch_id, lock=True)
                if current is None:
                    raise runs.OperationalPaperSessionRunNotFoundError()
                if mode in ("worker", "renew"):
                    claim = current.worker_claim
                    if (
                        claim is None
                        or claim.worker_id != worker_id
                        or claim.fencing_token != fencing_token
                        or not claim.is_active(now)
                    ):
                        raise runs.OperationalPaperSessionRunLeaseError()
                if current.record_version != expected_record_version:
                    raise runs.OperationalPaperSessionRunRecordVersionConflictError()
                target = transform(current)
                if target == current:
                    return current
                return await self._update(connection, current, target, mode=mode, now=now)
        except Error as error:
            _raise_database_error(error)

    @staticmethod
    async def _update(
        connection: DatabaseConnection,
        current: runs.OperationalPaperSessionRunEpoch,
        target: runs.OperationalPaperSessionRunEpoch,
        *,
        mode: Literal["admin", "claim", "recover", "worker", "renew", "unclaimed"],
        now: datetime,
    ) -> runs.OperationalPaperSessionRunEpoch:
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
        if mode in ("worker", "renew", "recover"):
            claim = current.worker_claim
            if claim is None:
                raise runs.OperationalPaperSessionRunLeaseError()
            conditions.append("worker_id = %s")
            parameters.append(claim.worker_id)
            if mode == "recover":
                conditions.append("worker_lease_expires_at <= %s")
                parameters.append(now)
            else:
                conditions.extend(["worker_claimed_at <= %s", "worker_lease_expires_at > %s"])
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
                raise runs.OperationalPaperSessionRunNotFoundError()
            if mode in ("worker", "renew", "recover"):
                if (
                    persisted.worker_claim is None
                    or current.worker_claim is None
                    or persisted.worker_claim.worker_id != current.worker_claim.worker_id
                    or persisted.fencing_token != current.fencing_token
                    or (mode in ("worker", "renew") and not persisted.worker_claim.is_active(now))
                ):
                    raise runs.OperationalPaperSessionRunLeaseError()
            if persisted.record_version != current.record_version:
                raise runs.OperationalPaperSessionRunRecordVersionConflictError()
            if mode in ("worker", "renew", "recover", "claim"):
                raise runs.OperationalPaperSessionRunLeaseError()
            raise runs.OperationalPaperSessionRunStateTransitionConflictError()
        result = operational_paper_session_run_epoch_from_row(row)
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain claim transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="claim",
            transform=lambda epoch: runs.claim_operational_paper_session_run_epoch(
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain recover transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="recover",
            transform=lambda epoch: runs.recover_operational_paper_session_run_epoch(
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain renew transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="renew",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: runs.renew_operational_paper_session_run_worker_claim(
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain mark_starting transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: runs.mark_operational_paper_session_run_epoch_starting(
                epoch,
                observed_at=now,
                worker_id=worker_id,
                fencing_token=fencing_token,
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain mark_running transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: runs.mark_operational_paper_session_run_epoch_running(
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain mark_stopping transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: runs.mark_operational_paper_session_run_epoch_stopping(
                epoch,
                observed_at=now,
                worker_id=worker_id,
                fencing_token=fencing_token,
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain settle_paused transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: runs.settle_operational_paper_session_run_epoch_paused(
                epoch,
                observed_at=now,
                worker_id=worker_id,
                fencing_token=fencing_token,
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain settle_stopped transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: runs.settle_operational_paper_session_run_epoch_stopped(
                epoch,
                observed_at=now,
                worker_id=worker_id,
                fencing_token=fencing_token,
            ),
        )

    async def settle_unclaimed(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        now: datetime,
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain settle_unclaimed transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="unclaimed",
            transform=lambda epoch: runs.settle_unclaimed_operational_paper_session_run_epoch(
                epoch,
                observed_at=now,
            ),
        )

    async def fail_claimed(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        worker_id: UUID,
        fencing_token: int,
        code: runs.OperationalPaperSessionRunFailureCode,
        now: datetime,
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain fail_claimed transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="worker",
            worker_id=worker_id,
            fencing_token=fencing_token,
            transform=lambda epoch: runs.fail_claimed_operational_paper_session_run_epoch(
                epoch,
                failed_at=now,
                worker_id=worker_id,
                fencing_token=fencing_token,
                code=code,
            ),
        )

    async def fail_unclaimed(
        self,
        epoch_id: UUID,
        *,
        expected_record_version: int,
        code: runs.OperationalPaperSessionRunFailureCode,
        now: datetime,
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Persist the domain fail_unclaimed transition under exact version/capability guards."""
        return await self._mutate(
            epoch_id,
            expected_record_version=expected_record_version,
            now=now,
            mode="unclaimed",
            transform=lambda epoch: runs.fail_unclaimed_operational_paper_session_run_epoch(
                epoch,
                failed_at=now,
                code=code,
            ),
        )
