"""PostgreSQL persistence for market-data worker runtime observability."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from psycopg import Error
from psycopg.rows import DictRow

from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError
from app.market_data.domain import require_utc
from app.market_data.errors import (
    InvalidPersistedWorkerRuntimeError,
    InvalidWorkerRuntimeObservabilityError,
    WorkerRuntimeNotFoundError,
    WorkerRuntimeTerminalError,
)
from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    SETTLED_OPERATION_STATES,
    WorkerRuntimeActivityState,
    WorkerRuntimeEvent,
    WorkerRuntimeEventType,
    WorkerRuntimeFailureCode,
    WorkerRuntimeLifecycleState,
    WorkerRuntimeSnapshot,
)

_MAX_READ_LIMIT = 100

_RUNTIME_COLUMNS = """
    id,
    lifecycle_state,
    activity_state,
    started_at,
    heartbeat_at,
    stopped_at,
    failure_code
"""

_EVENT_COLUMNS = """
    id,
    runtime_id,
    operation_id,
    event_type,
    operation_state,
    occurred_at
"""


def worker_runtime_from_row(row: DictRow) -> WorkerRuntimeSnapshot:
    """Strictly convert one PostgreSQL runtime row into immutable domain state."""

    try:
        raw_failure_code = _optional_str(row, "failure_code")
        return WorkerRuntimeSnapshot(
            runtime_id=_uuid(row, "id"),
            lifecycle_state=WorkerRuntimeLifecycleState(_str(row, "lifecycle_state")),
            activity_state=WorkerRuntimeActivityState(_str(row, "activity_state")),
            started_at=_datetime(row, "started_at"),
            heartbeat_at=_datetime(row, "heartbeat_at"),
            stopped_at=_optional_datetime(row, "stopped_at"),
            failure_code=(
                None if raw_failure_code is None else WorkerRuntimeFailureCode(raw_failure_code)
            ),
        )
    except InvalidPersistedWorkerRuntimeError:
        raise
    except (
        InvalidWorkerRuntimeObservabilityError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise InvalidPersistedWorkerRuntimeError() from None


def worker_runtime_event_from_row(row: DictRow) -> WorkerRuntimeEvent:
    """Strictly convert one append-only event row into domain state."""

    try:
        raw_operation_state = _optional_str(row, "operation_state")
        return WorkerRuntimeEvent(
            event_id=_int(row, "id"),
            runtime_id=_uuid(row, "runtime_id"),
            operation_id=_optional_uuid(row, "operation_id"),
            event_type=WorkerRuntimeEventType(_str(row, "event_type")),
            operation_state=(
                None if raw_operation_state is None else MarketOperationState(raw_operation_state)
            ),
            occurred_at=_datetime(row, "occurred_at"),
        )
    except InvalidPersistedWorkerRuntimeError:
        raise
    except (
        InvalidWorkerRuntimeObservabilityError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise InvalidPersistedWorkerRuntimeError() from None


def _str(row: DictRow, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise InvalidPersistedWorkerRuntimeError()
    return value


def _optional_str(row: DictRow, key: str) -> str | None:
    value = row[key]
    if value is not None and not isinstance(value, str):
        raise InvalidPersistedWorkerRuntimeError()
    return value


def _int(row: DictRow, key: str) -> int:
    value = row[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidPersistedWorkerRuntimeError()
    return value


def _uuid(row: DictRow, key: str) -> UUID:
    value = row[key]
    if not isinstance(value, UUID):
        raise InvalidPersistedWorkerRuntimeError()
    return value


def _optional_uuid(row: DictRow, key: str) -> UUID | None:
    value = row[key]
    if value is not None and not isinstance(value, UUID):
        raise InvalidPersistedWorkerRuntimeError()
    return value


def _datetime(row: DictRow, key: str) -> datetime:
    value = row[key]
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidPersistedWorkerRuntimeError()
    return value.astimezone(UTC)


def _optional_datetime(row: DictRow, key: str) -> datetime | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidPersistedWorkerRuntimeError()
    return value.astimezone(UTC)


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidWorkerRuntimeObservabilityError()
    return value


def _require_timestamp(value: datetime) -> datetime:
    try:
        return require_utc(
            value,
            field_name="worker_runtime_observability_timestamp",
        )
    except DomainError:
        raise InvalidWorkerRuntimeObservabilityError() from None


def _require_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_READ_LIMIT:
        raise InvalidWorkerRuntimeObservabilityError()
    return limit


def _require_running(runtime: WorkerRuntimeSnapshot) -> None:
    if runtime.lifecycle_state is not WorkerRuntimeLifecycleState.RUNNING:
        raise WorkerRuntimeTerminalError()


def _raise_worker_persistence_error(error: Error) -> NoReturn:
    message = error.diag.message_primary or ""
    constraint = error.diag.constraint_name or ""

    if message == "market_data_worker_runtime_terminal":
        raise WorkerRuntimeTerminalError() from error

    if message in {
        "market_data_worker_runtime_invalid_initial_state",
        "market_data_worker_runtime_identity_immutable",
        "market_data_worker_runtime_heartbeat_regression",
        "market_data_worker_runtime_delete_forbidden",
        "market_data_worker_events_append_only",
    }:
        raise InvalidWorkerRuntimeObservabilityError() from error

    if constraint.startswith("market_data_worker_runtimes_"):
        raise InvalidWorkerRuntimeObservabilityError() from error

    if constraint.startswith("market_data_worker_events_"):
        raise InvalidWorkerRuntimeObservabilityError() from error

    raise_domain_error(error)


async def _get_runtime_row(
    connection: DatabaseConnection,
    runtime_id: UUID,
    *,
    for_update: bool = False,
) -> DictRow | None:
    lock_clause = " for update" if for_update else ""

    cursor = await connection.execute(
        f"""
        select {_RUNTIME_COLUMNS}
        from public.market_data_worker_runtimes
        where id = %s
        {lock_clause}
        """,
        (runtime_id,),
    )
    return await cursor.fetchone()


async def _insert_event(
    connection: DatabaseConnection,
    *,
    runtime_id: UUID,
    event_type: WorkerRuntimeEventType,
    occurred_at: datetime,
    operation_id: UUID | None = None,
    operation_state: MarketOperationState | None = None,
) -> WorkerRuntimeEvent:
    cursor = await connection.execute(
        f"""
        insert into public.market_data_worker_events (
            runtime_id,
            operation_id,
            event_type,
            operation_state,
            occurred_at
        )
        values (%s, %s, %s, %s, %s)
        returning {_EVENT_COLUMNS}
        """,
        (
            runtime_id,
            operation_id,
            event_type.value,
            None if operation_state is None else operation_state.value,
            occurred_at,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise PersistenceError()
    return worker_runtime_event_from_row(row)


async def _require_single_start_event(
    connection: DatabaseConnection,
    runtime: WorkerRuntimeSnapshot,
) -> None:
    cursor = await connection.execute(
        """
        select occurred_at
        from public.market_data_worker_events
        where runtime_id = %s
          and event_type = 'RUNTIME_STARTED'
        order by id
        limit 2
        """,
        (runtime.runtime_id,),
    )
    rows = await cursor.fetchall()

    if len(rows) != 1:
        raise InvalidPersistedWorkerRuntimeError()

    if _datetime(rows[0], "occurred_at") != runtime.started_at:
        raise InvalidPersistedWorkerRuntimeError()


class PostgresWorkerRuntimeObservabilityRepository:
    """Short-transaction PostgreSQL implementation of worker observability."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def start_idempotently(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
    ) -> WorkerRuntimeSnapshot:
        """Create one runtime epoch and STARTED event atomically."""

        runtime_id = _require_uuid(runtime_id)
        now = _require_timestamp(now)

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    insert into public.market_data_worker_runtimes (
                        id,
                        lifecycle_state,
                        activity_state,
                        started_at,
                        heartbeat_at
                    )
                    values (%s, 'RUNNING', 'IDLE', %s, %s)
                    on conflict (id) do nothing
                    returning {_RUNTIME_COLUMNS}
                    """,
                    (runtime_id, now, now),
                )
                row = await cursor.fetchone()

                if row is None:
                    row = await _get_runtime_row(
                        connection,
                        runtime_id,
                        for_update=True,
                    )
                    if row is None:
                        raise PersistenceError()

                    runtime = worker_runtime_from_row(row)

                    if runtime.started_at != now:
                        raise InvalidWorkerRuntimeObservabilityError()

                    _require_running(runtime)
                    await _require_single_start_event(connection, runtime)
                    return runtime

                runtime = worker_runtime_from_row(row)

                await _insert_event(
                    connection,
                    runtime_id=runtime_id,
                    event_type=WorkerRuntimeEventType.RUNTIME_STARTED,
                    occurred_at=now,
                )

                return runtime
        except Error as error:
            _raise_worker_persistence_error(error)

    async def get(
        self,
        runtime_id: UUID,
    ) -> WorkerRuntimeSnapshot | None:
        """Return one internal runtime epoch or ``None``."""

        runtime_id = _require_uuid(runtime_id)

        try:
            async with self._database.transaction() as connection:
                row = await _get_runtime_row(connection, runtime_id)
        except Error as error:
            _raise_worker_persistence_error(error)

        return None if row is None else worker_runtime_from_row(row)

    async def heartbeat(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        activity_state: WorkerRuntimeActivityState,
    ) -> WorkerRuntimeSnapshot:
        """Advance heartbeat and coarse activity monotonically."""

        runtime_id = _require_uuid(runtime_id)
        now = _require_timestamp(now)

        if not isinstance(activity_state, WorkerRuntimeActivityState):
            raise InvalidWorkerRuntimeObservabilityError()

        try:
            async with self._database.transaction() as connection:
                row = await _get_runtime_row(
                    connection,
                    runtime_id,
                    for_update=True,
                )
                if row is None:
                    raise WorkerRuntimeNotFoundError()

                current = worker_runtime_from_row(row)
                _require_running(current)

                if now < current.heartbeat_at:
                    raise InvalidWorkerRuntimeObservabilityError()

                cursor = await connection.execute(
                    f"""
                    update public.market_data_worker_runtimes
                    set heartbeat_at = %s,
                        activity_state = %s
                    where id = %s
                    returning {_RUNTIME_COLUMNS}
                    """,
                    (
                        now,
                        activity_state.value,
                        runtime_id,
                    ),
                )
                updated_row = await cursor.fetchone()

                if updated_row is None:
                    raise PersistenceError()

                return worker_runtime_from_row(updated_row)
        except Error as error:
            _raise_worker_persistence_error(error)

    async def stop(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
    ) -> WorkerRuntimeSnapshot:
        """Terminalize one runtime as STOPPED with one event."""

        return await self._terminalize(
            runtime_id=runtime_id,
            now=now,
            lifecycle_state=WorkerRuntimeLifecycleState.STOPPED,
            event_type=WorkerRuntimeEventType.RUNTIME_STOPPED,
            failure_code=None,
        )

    async def fail(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        failure_code: WorkerRuntimeFailureCode,
    ) -> WorkerRuntimeSnapshot:
        """Terminalize one runtime as FAILED with sanitized failure code."""

        if not isinstance(failure_code, WorkerRuntimeFailureCode):
            raise InvalidWorkerRuntimeObservabilityError()

        return await self._terminalize(
            runtime_id=runtime_id,
            now=now,
            lifecycle_state=WorkerRuntimeLifecycleState.FAILED,
            event_type=WorkerRuntimeEventType.RUNTIME_FAILED,
            failure_code=failure_code,
        )

    async def _terminalize(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        lifecycle_state: WorkerRuntimeLifecycleState,
        event_type: WorkerRuntimeEventType,
        failure_code: WorkerRuntimeFailureCode | None,
    ) -> WorkerRuntimeSnapshot:
        runtime_id = _require_uuid(runtime_id)
        now = _require_timestamp(now)

        try:
            async with self._database.transaction() as connection:
                row = await _get_runtime_row(
                    connection,
                    runtime_id,
                    for_update=True,
                )
                if row is None:
                    raise WorkerRuntimeNotFoundError()

                current = worker_runtime_from_row(row)
                _require_running(current)

                if now < current.heartbeat_at:
                    raise InvalidWorkerRuntimeObservabilityError()

                cursor = await connection.execute(
                    f"""
                    update public.market_data_worker_runtimes
                    set lifecycle_state = %s,
                        activity_state = 'IDLE',
                        heartbeat_at = %s,
                        stopped_at = %s,
                        failure_code = %s
                    where id = %s
                    returning {_RUNTIME_COLUMNS}
                    """,
                    (
                        lifecycle_state.value,
                        now,
                        now,
                        None if failure_code is None else failure_code.value,
                        runtime_id,
                    ),
                )
                updated_row = await cursor.fetchone()

                if updated_row is None:
                    raise PersistenceError()

                runtime = worker_runtime_from_row(updated_row)

                await _insert_event(
                    connection,
                    runtime_id=runtime_id,
                    event_type=event_type,
                    occurred_at=now,
                )

                return runtime
        except Error as error:
            _raise_worker_persistence_error(error)

    async def record_operation_settled(
        self,
        *,
        runtime_id: UUID,
        operation_id: UUID,
        operation_state: MarketOperationState,
        now: datetime,
    ) -> WorkerRuntimeEvent:
        """Append a sanitized event for one actually settled operation."""

        runtime_id = _require_uuid(runtime_id)
        operation_id = _require_uuid(operation_id)
        now = _require_timestamp(now)

        if (
            not isinstance(operation_state, MarketOperationState)
            or operation_state not in SETTLED_OPERATION_STATES
        ):
            raise InvalidWorkerRuntimeObservabilityError()

        try:
            async with self._database.transaction() as connection:
                runtime_row = await _get_runtime_row(
                    connection,
                    runtime_id,
                    for_update=True,
                )
                if runtime_row is None:
                    raise WorkerRuntimeNotFoundError()

                runtime = worker_runtime_from_row(runtime_row)
                _require_running(runtime)

                if now < runtime.started_at:
                    raise InvalidWorkerRuntimeObservabilityError()

                operation_cursor = await connection.execute(
                    """
                    select status
                    from public.market_data_operations
                    where id = %s
                    for share
                    """,
                    (operation_id,),
                )
                operation_row = await operation_cursor.fetchone()

                if operation_row is None or operation_row["status"] != operation_state.value:
                    raise InvalidWorkerRuntimeObservabilityError()

                return await _insert_event(
                    connection,
                    runtime_id=runtime_id,
                    operation_id=operation_id,
                    event_type=WorkerRuntimeEventType.OPERATION_SETTLED,
                    operation_state=operation_state,
                    occurred_at=now,
                )
        except Error as error:
            _raise_worker_persistence_error(error)

    async def list_recent_runtimes(
        self,
        *,
        limit: int,
    ) -> tuple[WorkerRuntimeSnapshot, ...]:
        """Return newest runtime observations with a defensive hard bound."""

        limit = _require_limit(limit)

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    select {_RUNTIME_COLUMNS}
                    from public.market_data_worker_runtimes
                    order by heartbeat_at desc, started_at desc, id desc
                    limit %s
                    """,
                    (limit,),
                )
                rows = await cursor.fetchall()
        except Error as error:
            _raise_worker_persistence_error(error)

        return tuple(worker_runtime_from_row(row) for row in rows)

    async def list_recent_events(
        self,
        *,
        limit: int,
    ) -> tuple[WorkerRuntimeEvent, ...]:
        """Return newest sanitized events with a defensive hard bound."""

        limit = _require_limit(limit)

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    select {_EVENT_COLUMNS}
                    from public.market_data_worker_events
                    order by occurred_at desc, id desc
                    limit %s
                    """,
                    (limit,),
                )
                rows = await cursor.fetchall()
        except Error as error:
            _raise_worker_persistence_error(error)

        return tuple(worker_runtime_event_from_row(row) for row in rows)
