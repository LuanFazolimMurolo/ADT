"""PostgreSQL operational catalog for Phase 2D market-data work."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NoReturn, cast
from uuid import UUID

from psycopg import Error
from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow

from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError
from app.market_data.domain import (
    DataRange,
    Exchange,
    MarketType,
    TradingPair,
    require_utc,
)
from app.market_data.errors import (
    InvalidMarketOperationRequestError,
    InvalidOperationLeaseError,
    InvalidOperationTransitionError,
    InvalidPersistedOperationError,
    MarketOperationNotFoundError,
    MarketOperationTerminalError,
    OperationIdempotencyConflictError,
    OperationProgressRegressionError,
    OperationVersionConflictError,
)
from app.market_data.operations import (
    MarketDatasetSelector,
    MarketOperationFailureCode,
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    OperationProgress,
    OperationResult,
    SanitizedOperationFailure,
    WorkerLease,
    decode_dataset_id,
    encode_dataset_id,
    operation_request_fingerprint,
    renew_lease,
    request_lease_recovery,
    require_progress_not_regressed,
    require_transition,
    validate_operation_update,
)
from app.market_data.timeframes import get_timeframe

_OPERATION_COLUMNS = """
    operation.id,
    operation.operation_type,
    operation.exchange,
    operation.market,
    operation.symbol,
    operation.timeframe,
    operation.dataset_id,
    operation.range_start,
    operation.range_end,
    operation.plan_checksum,
    operation.request_fingerprint,
    operation.idempotency_key,
    operation.requested_by,
    operation.contract_version,
    operation.status,
    operation.local_job_id,
    operation.chunks_planned,
    operation.chunks_completed,
    operation.chunks_failed,
    operation.candles_estimated,
    operation.candles_received,
    operation.candles_persisted,
    operation.requests_estimated,
    operation.requests_completed,
    operation.progress_updated_at,
    operation.lease_owner,
    operation.lease_claimed_at,
    operation.lease_heartbeat_at,
    operation.lease_expires_at,
    operation.result_dataset_version,
    operation.result_dataset_checksum,
    operation.failure_code,
    operation.failure_message,
    operation.version,
    operation.plan_created_at,
    operation.created_at,
    operation.updated_at,
    operation.started_at,
    operation.finished_at
"""

_FAILURE_MESSAGES: dict[MarketOperationFailureCode, str] = {
    MarketOperationFailureCode.INVALID_REQUEST: "A solicitação operacional é inválida.",
    MarketOperationFailureCode.PLAN_CONFLICT: "O plano local diverge da solicitação.",
    MarketOperationFailureCode.DATASET_BUSY: "O dataset está ocupado por outra operação.",
    MarketOperationFailureCode.LEASE_LOST: "A operação perdeu a lease do worker.",
    MarketOperationFailureCode.WORKER_UNAVAILABLE: "O worker não está disponível.",
    MarketOperationFailureCode.LOCAL_STATE_INVALID: "O estado local não pôde ser validado.",
    MarketOperationFailureCode.NETWORK_FAILURE: "A fonte pública não pôde ser acessada.",
    MarketOperationFailureCode.RATE_LIMITED: "A fonte pública limitou as requisições.",
    MarketOperationFailureCode.CANCELLED_BY_ADMIN: "A operação foi cancelada por administrador.",
    MarketOperationFailureCode.INTERNAL_ERROR: "A operação falhou de forma segura.",
}

_CLEAR_LEASE_STATES = frozenset(
    {
        MarketOperationState.PENDING,
        MarketOperationState.PAUSED,
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
        MarketOperationState.RECOVERING,
    }
)


def failure_message_for(code: MarketOperationFailureCode) -> str:
    """Return one fixed, persistence-safe message for a closed failure code."""
    try:
        return _FAILURE_MESSAGES[code]
    except (KeyError, TypeError):
        raise InvalidMarketOperationRequestError() from None


def market_operation_from_row(row: DictRow) -> MarketOperationSnapshot:
    """Strictly convert one complete PostgreSQL row into immutable domain state."""
    try:
        operation_id = _uuid(row, "id")
        identity = MarketDatasetSelector(
            exchange=Exchange(_str(row, "exchange")),
            market_type=MarketType(_str(row, "market")),
            pair=TradingPair.parse(_str(row, "symbol")),
            timeframe=get_timeframe(_str(row, "timeframe")),
        )
        dataset_id = _str(row, "dataset_id")
        if decode_dataset_id(dataset_id) != identity or encode_dataset_id(identity) != dataset_id:
            raise InvalidPersistedOperationError()

        request = MarketOperationRequest(
            operation_type=MarketOperationType(_str(row, "operation_type")),
            dataset=identity,
            data_range=DataRange(
                start=_datetime(row, "range_start"),
                end=_datetime(row, "range_end"),
            ),
            plan_checksum=_str(row, "plan_checksum"),
            idempotency_key=_str(row, "idempotency_key"),
            requested_by=_uuid(row, "requested_by"),
            contract_version=_int(row, "contract_version"),
        )
        if operation_request_fingerprint(request) != _str(row, "request_fingerprint"):
            raise InvalidPersistedOperationError()

        plan = OperationPlanSummary(
            checksum=_str(row, "plan_checksum"),
            chunks_planned=_int(row, "chunks_planned"),
            estimated_candles=_int(row, "candles_estimated"),
            estimated_requests=_int(row, "requests_estimated"),
            created_at=_datetime(row, "plan_created_at"),
        )
        progress = OperationProgress(
            chunks_planned=_int(row, "chunks_planned"),
            chunks_completed=_int(row, "chunks_completed"),
            chunks_failed=_int(row, "chunks_failed"),
            candles_estimated=_int(row, "candles_estimated"),
            candles_received=_int(row, "candles_received"),
            candles_persisted=_int(row, "candles_persisted"),
            requests_completed=_int(row, "requests_completed"),
            updated_at=_datetime(row, "progress_updated_at"),
        )
        lease = _lease_from_row(row, operation_id)
        result = _result_from_row(row)
        failure = _failure_from_row(row)
        snapshot = MarketOperationSnapshot(
            operation_id=operation_id,
            request=request,
            plan=plan,
            state=MarketOperationState(_str(row, "status")),
            progress=progress,
            created_at=_datetime(row, "created_at"),
            updated_at=_datetime(row, "updated_at"),
            record_version=_int(row, "version"),
            local_job_id=_optional_str(row, "local_job_id"),
            lease=lease,
            result=result,
            failure=failure,
            finished_at=_optional_datetime(row, "finished_at"),
            started_at=_optional_datetime(row, "started_at"),
        )
        _validate_started_at(row, snapshot)
        return snapshot
    except InvalidPersistedOperationError:
        raise
    except (DomainError, KeyError, TypeError, ValueError):
        raise InvalidPersistedOperationError() from None


def _lease_from_row(row: DictRow, operation_id: UUID) -> WorkerLease | None:
    owner = _optional_uuid(row, "lease_owner")
    temporal_values = (
        _optional_datetime(row, "lease_claimed_at"),
        _optional_datetime(row, "lease_heartbeat_at"),
        _optional_datetime(row, "lease_expires_at"),
    )
    if owner is None:
        if any(value is not None for value in temporal_values):
            raise InvalidPersistedOperationError()
        return None
    if any(value is None for value in temporal_values):
        raise InvalidPersistedOperationError()
    claimed_at, heartbeat_at, lease_expires_at = cast(
        tuple[datetime, datetime, datetime],
        temporal_values,
    )
    return WorkerLease(
        operation_id=operation_id,
        owner_id=owner,
        claimed_at=claimed_at,
        heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
    )


def _result_from_row(row: DictRow) -> OperationResult | None:
    version = _optional_str(row, "result_dataset_version")
    checksum = _optional_str(row, "result_dataset_checksum")
    finished_at = _optional_datetime(row, "finished_at")
    if version is None and checksum is None:
        return None
    if version is None or checksum is None or finished_at is None:
        raise InvalidPersistedOperationError()
    return OperationResult(
        dataset_version=version,
        dataset_checksum=checksum,
        completed_at=finished_at,
    )


def _failure_from_row(row: DictRow) -> SanitizedOperationFailure | None:
    raw_code = _optional_str(row, "failure_code")
    message = _optional_str(row, "failure_message")
    finished_at = _optional_datetime(row, "finished_at")
    if raw_code is None and message is None:
        return None
    if raw_code is None or message is None or finished_at is None:
        raise InvalidPersistedOperationError()
    code = MarketOperationFailureCode(raw_code)
    if message != failure_message_for(code):
        raise InvalidPersistedOperationError()
    return SanitizedOperationFailure(code=code, failed_at=finished_at)


def _validate_started_at(row: DictRow, snapshot: MarketOperationSnapshot) -> None:
    started_at = _optional_datetime(row, "started_at")
    if started_at != snapshot.started_at:
        raise InvalidPersistedOperationError()
    if started_at is not None and not snapshot.created_at <= started_at <= snapshot.updated_at:
        raise InvalidPersistedOperationError()
    if snapshot.state in {MarketOperationState.CLAIMED, MarketOperationState.RUNNING}:
        if started_at is None or snapshot.lease is None:
            raise InvalidPersistedOperationError()
        if started_at > snapshot.lease.claimed_at:
            raise InvalidPersistedOperationError()


def _str(row: DictRow, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise InvalidPersistedOperationError()
    return value


def _optional_str(row: DictRow, key: str) -> str | None:
    value = row[key]
    if value is not None and not isinstance(value, str):
        raise InvalidPersistedOperationError()
    return value


def _int(row: DictRow, key: str) -> int:
    value = row[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidPersistedOperationError()
    return value


def _uuid(row: DictRow, key: str) -> UUID:
    value = row[key]
    if not isinstance(value, UUID):
        raise InvalidPersistedOperationError()
    return value


def _optional_uuid(row: DictRow, key: str) -> UUID | None:
    value = row[key]
    if value is not None and not isinstance(value, UUID):
        raise InvalidPersistedOperationError()
    return value


def _datetime(row: DictRow, key: str) -> datetime:
    value = row[key]
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidPersistedOperationError()
    return value.astimezone(UTC)


def _optional_datetime(row: DictRow, key: str) -> datetime | None:
    value = row[key]
    if value is not None and (
        not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
    ):
        raise InvalidPersistedOperationError()
    return None if value is None else value.astimezone(UTC)


async def _get_row(
    connection: DatabaseConnection,
    operation_id: UUID,
    *,
    for_update: bool = False,
) -> DictRow | None:
    lock_clause = " for update" if for_update else ""
    cursor = await connection.execute(
        f"""
        select {_OPERATION_COLUMNS}
        from public.market_data_operations as operation
        where operation.id = %s
        {lock_clause}
        """,
        (operation_id,),
    )
    return await cursor.fetchone()


def _require_expected_version(
    operation: MarketOperationSnapshot,
    expected_version: int,
) -> None:
    if (
        not isinstance(expected_version, int)
        or isinstance(expected_version, bool)
        or operation.record_version != expected_version
    ):
        raise OperationVersionConflictError()


def _require_pagination(limit: int, offset: int) -> None:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or not 1 <= limit <= 500
        or offset < 0
    ):
        raise InvalidMarketOperationRequestError()


def _require_operation_timestamp(value: datetime) -> datetime:
    try:
        return require_utc(value, field_name="operation_timestamp")
    except DomainError:
        raise InvalidMarketOperationRequestError() from None


def _require_worker_owner_id(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidOperationLeaseError()
    return value


def _require_lease_timestamp(value: datetime) -> datetime:
    try:
        return require_utc(value, field_name="lease_validation_timestamp")
    except DomainError:
        raise InvalidOperationLeaseError() from None


def _require_owned_active_lease(
    operation: MarketOperationSnapshot,
    *,
    owner_id: UUID,
    now: datetime,
) -> WorkerLease:
    owner = _require_worker_owner_id(owner_id)
    current = _require_lease_timestamp(now)
    lease = operation.lease
    if (
        current < operation.updated_at
        or lease is None
        or not lease.belongs_to(owner)
        or not lease.is_active(current)
    ):
        raise InvalidOperationLeaseError()
    return lease


def _raise_operation_persistence_error(error: Error) -> NoReturn:
    message = error.diag.message_primary or ""
    constraint = error.diag.constraint_name or ""
    if message == "market_data_operation_terminal":
        raise MarketOperationTerminalError() from None
    if message == "market_data_operation_version_conflict":
        raise OperationVersionConflictError() from None
    if message == "market_data_operation_progress_regression":
        raise OperationProgressRegressionError() from None
    if message == "market_data_operation_lease_invalid":
        raise InvalidOperationLeaseError() from None
    if message == "market_data_operation_transition_invalid":
        raise InvalidOperationTransitionError() from None
    if constraint in {
        "market_data_operations_one_active_dataset_uidx",
        "market_data_operations_one_active_owner_uidx",
    }:
        raise InvalidOperationLeaseError() from None
    if constraint == "market_data_operations_admin_idempotency_key_key":
        raise OperationIdempotencyConflictError() from None
    if message == "market_data_operation_requester_not_admin":
        raise InvalidMarketOperationRequestError() from None
    if constraint.startswith("market_data_operations_"):
        raise InvalidMarketOperationRequestError() from None
    raise PersistenceError() from None


class PostgresMarketOperationRepository:
    """Short-transaction PostgreSQL implementation of the Phase 2D port."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_idempotently(
        self,
        *,
        operation_id: UUID,
        request: MarketOperationRequest,
        plan: OperationPlanSummary,
        now: datetime,
    ) -> MarketOperationSnapshot:
        """Insert once per administrator/key or resolve a same-payload retry."""
        now = _require_operation_timestamp(now)
        if plan.checksum != request.plan_checksum or plan.created_at > now:
            raise InvalidMarketOperationRequestError()
        dataset_id = encode_dataset_id(request.dataset)
        fingerprint = operation_request_fingerprint(request)
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    insert into public.market_data_operations as operation (
                        id,
                        operation_type,
                        exchange,
                        market,
                        symbol,
                        timeframe,
                        dataset_id,
                        range_start,
                        range_end,
                        plan_checksum,
                        request_fingerprint,
                        idempotency_key,
                        requested_by,
                        contract_version,
                        status,
                        chunks_planned,
                        candles_estimated,
                        requests_estimated,
                        progress_updated_at,
                        version,
                        plan_created_at,
                        created_at,
                        updated_at
                    )
                    values (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, 'PENDING', %s, %s, %s, %s, 1, %s, %s, %s
                    )
                    on conflict (requested_by, idempotency_key) do nothing
                    returning {_OPERATION_COLUMNS}
                    """,
                    (
                        operation_id,
                        request.operation_type.value,
                        request.dataset.exchange.value,
                        request.dataset.market_type.value,
                        request.dataset.pair.symbol,
                        request.dataset.timeframe.code,
                        dataset_id,
                        request.data_range.start,
                        request.data_range.end,
                        request.plan_checksum,
                        fingerprint,
                        request.idempotency_key,
                        request.requested_by,
                        request.contract_version,
                        plan.chunks_planned,
                        plan.estimated_candles,
                        plan.estimated_requests,
                        now,
                        plan.created_at,
                        now,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    existing_cursor = await connection.execute(
                        f"""
                        select {_OPERATION_COLUMNS}
                        from public.market_data_operations as operation
                        where operation.requested_by = %s
                          and operation.idempotency_key = %s
                        """,
                        (request.requested_by, request.idempotency_key),
                    )
                    row = await existing_cursor.fetchone()
                    if row is None:
                        raise PersistenceError()
                    if _str(row, "request_fingerprint") != fingerprint:
                        raise OperationIdempotencyConflictError()
        except Error as error:
            _raise_operation_persistence_error(error)
        return market_operation_from_row(row)

    async def get(self, operation_id: UUID) -> MarketOperationSnapshot | None:
        """Return one operation or ``None`` without exposing persistence details."""
        try:
            async with self._database.transaction() as connection:
                row = await _get_row(connection, operation_id)
        except Error as error:
            _raise_operation_persistence_error(error)
        return None if row is None else market_operation_from_row(row)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: MarketOperationState | None = None,
        requested_by: UUID | None = None,
        dataset_id: str | None = None,
    ) -> tuple[MarketOperationSnapshot, ...]:
        """List newest-first with optional closed, parameterized filters."""
        _require_pagination(limit, offset)
        if state is not None and not isinstance(state, MarketOperationState):
            raise InvalidMarketOperationRequestError()
        if requested_by is not None and not isinstance(requested_by, UUID):
            raise InvalidMarketOperationRequestError()
        if dataset_id is not None:
            encode_dataset_id(decode_dataset_id(dataset_id))

        predicates: list[str] = []
        parameters: list[object] = []
        if state is not None:
            predicates.append("operation.status = %s")
            parameters.append(state.value)
        if requested_by is not None:
            predicates.append("operation.requested_by = %s")
            parameters.append(requested_by)
        if dataset_id is not None:
            predicates.append("operation.dataset_id = %s")
            parameters.append(dataset_id)
        where_clause = f"where {' and '.join(predicates)}" if predicates else ""
        parameters.extend((limit, offset))
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    select {_OPERATION_COLUMNS}
                    from public.market_data_operations as operation
                    {where_clause}
                    order by operation.created_at desc, operation.id desc
                    limit %s offset %s
                    """,
                    tuple(parameters),
                )
                rows = await cursor.fetchall()
        except Error as error:
            _raise_operation_persistence_error(error)
        return tuple(market_operation_from_row(row) for row in rows)

    async def request_state(
        self,
        *,
        operation_id: UUID,
        target: MarketOperationState,
        expected_version: int,
        now: datetime,
        owner_id: UUID | None = None,
    ) -> MarketOperationSnapshot:
        """Apply one explicit normal or expired-lease recovery transition."""
        if not isinstance(target, MarketOperationState):
            raise InvalidOperationTransitionError()
        now = _require_operation_timestamp(now)
        if owner_id is not None:
            owner_id = _require_worker_owner_id(owner_id)
        try:
            async with self._database.transaction() as connection:
                current = await self._locked_operation(
                    connection,
                    operation_id,
                    expected_version,
                )
                if current.state is target:
                    return current
                if target is MarketOperationState.RECOVERING:
                    request_lease_recovery(current, now=now)
                else:
                    require_transition(current.state, target)
                worker_owned = (
                    target
                    in {
                        MarketOperationState.RUNNING,
                        MarketOperationState.PAUSED,
                        MarketOperationState.CANCELLED,
                    }
                    and current.lease is not None
                )
                if worker_owned:
                    if owner_id is None:
                        raise InvalidOperationLeaseError()
                    _require_owned_active_lease(
                        current,
                        owner_id=owner_id,
                        now=now,
                    )
                if target in {MarketOperationState.COMPLETED, MarketOperationState.FAILED}:
                    raise InvalidOperationTransitionError()

                clear_lease = target in _CLEAR_LEASE_STATES
                cancelled = target is MarketOperationState.CANCELLED
                cursor = await connection.execute(
                    f"""
                    update public.market_data_operations as operation
                    set
                        status = %s,
                        lease_owner = case when %s then null else lease_owner end,
                        lease_claimed_at = case when %s then null else lease_claimed_at end,
                        lease_heartbeat_at = case when %s then null else lease_heartbeat_at end,
                        lease_expires_at = case when %s then null else lease_expires_at end,
                        failure_code = case when %s then %s else null end,
                        failure_message = case when %s then %s else null end,
                        finished_at = case when %s then %s else null end,
                        updated_at = %s,
                        version = version + 1
                    where operation.id = %s
                      and operation.version = %s
                      and (
                          not %s
                          or (
                              operation.lease_owner = %s
                              and operation.lease_expires_at > %s
                          )
                      )
                    returning {_OPERATION_COLUMNS}
                    """,
                    (
                        target.value,
                        clear_lease,
                        clear_lease,
                        clear_lease,
                        clear_lease,
                        cancelled,
                        MarketOperationFailureCode.CANCELLED_BY_ADMIN.value,
                        cancelled,
                        failure_message_for(MarketOperationFailureCode.CANCELLED_BY_ADMIN),
                        cancelled,
                        now,
                        now,
                        operation_id,
                        expected_version,
                        worker_owned,
                        owner_id,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    if worker_owned:
                        raise InvalidOperationLeaseError()
                    raise OperationVersionConflictError()
        except Error as error:
            _raise_operation_persistence_error(error)
        return market_operation_from_row(row)

    async def claim_next(
        self,
        *,
        owner_id: UUID,
        now: datetime,
        lease_expires_at: datetime,
    ) -> MarketOperationSnapshot | None:
        """Atomically claim the oldest eligible operation and end the transaction."""
        owner_id = _require_worker_owner_id(owner_id)
        WorkerLease(
            operation_id=UUID(int=0),
            owner_id=owner_id,
            claimed_at=now,
            heartbeat_at=now,
            lease_expires_at=lease_expires_at,
        )
        for attempt in range(2):
            try:
                async with self._database.transaction() as connection:
                    cursor = await connection.execute(
                        f"""
                        with candidate as (
                            select operation.id
                            from public.market_data_operations as operation
                            where operation.status = 'PENDING'
                              and not exists (
                                  select 1
                                  from public.market_data_operations as owned
                                  where owned.lease_owner = %s
                              )
                              and not exists (
                                  select 1
                                  from public.market_data_operations as active
                                  where active.dataset_id = operation.dataset_id
                                    and (
                                        active.status in (
                                            'CLAIMED', 'RUNNING', 'RECOVERING'
                                        )
                                        or (
                                            active.status in (
                                                'PAUSE_REQUESTED',
                                                'CANCEL_REQUESTED'
                                            )
                                            and active.lease_owner is not null
                                        )
                                    )
                              )
                            order by operation.created_at, operation.id
                            for update skip locked
                            limit 1
                        )
                        update public.market_data_operations as operation
                        set
                            status = 'CLAIMED',
                            lease_owner = %s,
                            lease_claimed_at = %s,
                            lease_heartbeat_at = %s,
                            lease_expires_at = %s,
                            started_at = coalesce(operation.started_at, %s),
                            updated_at = %s,
                            version = operation.version + 1
                        from candidate
                        where operation.id = candidate.id
                        returning {_OPERATION_COLUMNS}
                        """,
                        (
                            owner_id,
                            owner_id,
                            now,
                            now,
                            lease_expires_at,
                            now,
                            now,
                        ),
                    )
                    row = await cursor.fetchone()
                return None if row is None else market_operation_from_row(row)
            except UniqueViolation as error:
                if (
                    error.diag.constraint_name == "market_data_operations_one_active_dataset_uidx"
                    or error.diag.constraint_name == "market_data_operations_one_active_owner_uidx"
                ) and attempt == 0:
                    continue
                _raise_operation_persistence_error(error)
            except Error as error:
                _raise_operation_persistence_error(error)
        return None

    async def renew_lease(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        lease: WorkerLease,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        """Persist a strictly forward owner-checked heartbeat and expiry."""
        owner_id = _require_worker_owner_id(owner_id)
        now = _require_lease_timestamp(now)
        if lease.operation_id != operation_id:
            raise InvalidOperationLeaseError()
        try:
            async with self._database.transaction() as connection:
                current = await self._locked_operation(
                    connection,
                    operation_id,
                    expected_version,
                )
                current_lease = _require_owned_active_lease(
                    current,
                    owner_id=owner_id,
                    now=now,
                )
                expected = renew_lease(
                    current_lease,
                    owner_id=owner_id,
                    now=now,
                    lease_expires_at=lease.lease_expires_at,
                )
                if expected != lease or lease.heartbeat_at < current.updated_at:
                    raise InvalidOperationLeaseError()
                cursor = await connection.execute(
                    f"""
                    update public.market_data_operations as operation
                    set
                        lease_heartbeat_at = %s,
                        lease_expires_at = %s,
                        updated_at = %s,
                        version = version + 1
                    where operation.id = %s
                      and operation.version = %s
                      and operation.lease_owner = %s
                      and operation.lease_expires_at > %s
                    returning {_OPERATION_COLUMNS}
                    """,
                    (
                        now,
                        lease.lease_expires_at,
                        now,
                        operation_id,
                        expected_version,
                        owner_id,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise InvalidOperationLeaseError()
        except Error as error:
            _raise_operation_persistence_error(error)
        return market_operation_from_row(row)

    async def update_progress(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        progress: OperationProgress,
        local_job_id: str | None,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        """Persist monotonic worker progress while its lease remains active."""
        owner_id = _require_worker_owner_id(owner_id)
        now = _require_lease_timestamp(now)
        try:
            async with self._database.transaction() as connection:
                current = await self._locked_operation(
                    connection,
                    operation_id,
                    expected_version,
                )
                require_progress_not_regressed(current.progress, progress)
                if progress.updated_at < current.updated_at or progress.updated_at > now:
                    raise OperationProgressRegressionError()
                _require_owned_active_lease(
                    current,
                    owner_id=owner_id,
                    now=now,
                )
                if current.local_job_id is not None and local_job_id != current.local_job_id:
                    raise InvalidMarketOperationRequestError()
                cursor = await connection.execute(
                    f"""
                    update public.market_data_operations as operation
                    set
                        chunks_completed = %s,
                        chunks_failed = %s,
                        candles_received = %s,
                        candles_persisted = %s,
                        requests_completed = %s,
                        progress_updated_at = %s,
                        local_job_id = coalesce(operation.local_job_id, %s),
                        updated_at = %s,
                        version = version + 1
                    where operation.id = %s
                      and operation.version = %s
                      and operation.lease_owner = %s
                      and operation.lease_expires_at > %s
                    returning {_OPERATION_COLUMNS}
                    """,
                    (
                        progress.chunks_completed,
                        progress.chunks_failed,
                        progress.candles_received,
                        progress.candles_persisted,
                        progress.requests_completed,
                        progress.updated_at,
                        local_job_id,
                        now,
                        operation_id,
                        expected_version,
                        owner_id,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise OperationVersionConflictError()
        except Error as error:
            _raise_operation_persistence_error(error)
        return market_operation_from_row(row)

    async def complete(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        result: OperationResult,
        progress: OperationProgress,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        """Complete only while the persisted worker lease is still active."""
        owner_id = _require_worker_owner_id(owner_id)
        now = _require_lease_timestamp(now)
        try:
            async with self._database.transaction() as connection:
                current = await self._locked_operation(
                    connection,
                    operation_id,
                    expected_version,
                )
                require_transition(current.state, MarketOperationState.COMPLETED)
                require_progress_not_regressed(current.progress, progress)
                _require_owned_active_lease(
                    current,
                    owner_id=owner_id,
                    now=now,
                )
                if progress.updated_at > result.completed_at or result.completed_at > now:
                    raise OperationProgressRegressionError()
                cursor = await connection.execute(
                    f"""
                    update public.market_data_operations as operation
                    set
                        status = 'COMPLETED',
                        chunks_completed = %s,
                        chunks_failed = %s,
                        candles_received = %s,
                        candles_persisted = %s,
                        requests_completed = %s,
                        progress_updated_at = %s,
                        lease_owner = null,
                        lease_claimed_at = null,
                        lease_heartbeat_at = null,
                        lease_expires_at = null,
                        result_dataset_version = %s,
                        result_dataset_checksum = %s,
                        finished_at = %s,
                        updated_at = %s,
                        version = version + 1
                    where operation.id = %s
                      and operation.version = %s
                      and operation.lease_owner = %s
                      and operation.lease_expires_at > %s
                    returning {_OPERATION_COLUMNS}
                    """,
                    (
                        progress.chunks_completed,
                        progress.chunks_failed,
                        progress.candles_received,
                        progress.candles_persisted,
                        progress.requests_completed,
                        progress.updated_at,
                        result.dataset_version,
                        result.dataset_checksum,
                        result.completed_at,
                        now,
                        operation_id,
                        expected_version,
                        owner_id,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise OperationVersionConflictError()
        except Error as error:
            _raise_operation_persistence_error(error)
        return market_operation_from_row(row)

    async def fail(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        failure: SanitizedOperationFailure,
        progress: OperationProgress,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        """Persist only a closed failure code and its fixed safe message."""
        owner_id = _require_worker_owner_id(owner_id)
        now = _require_lease_timestamp(now)
        if failure.code is MarketOperationFailureCode.CANCELLED_BY_ADMIN:
            raise InvalidMarketOperationRequestError()
        try:
            async with self._database.transaction() as connection:
                current = await self._locked_operation(
                    connection,
                    operation_id,
                    expected_version,
                )
                require_transition(current.state, MarketOperationState.FAILED)
                require_progress_not_regressed(current.progress, progress)
                if progress.updated_at > failure.failed_at or failure.failed_at > now:
                    raise OperationProgressRegressionError()
                _require_owned_active_lease(
                    current,
                    owner_id=owner_id,
                    now=now,
                )
                cursor = await connection.execute(
                    f"""
                    update public.market_data_operations as operation
                    set
                        status = 'FAILED',
                        chunks_completed = %s,
                        chunks_failed = %s,
                        candles_received = %s,
                        candles_persisted = %s,
                        requests_completed = %s,
                        progress_updated_at = %s,
                        lease_owner = null,
                        lease_claimed_at = null,
                        lease_heartbeat_at = null,
                        lease_expires_at = null,
                        failure_code = %s,
                        failure_message = %s,
                        finished_at = %s,
                        updated_at = %s,
                        version = version + 1
                    where operation.id = %s
                      and operation.version = %s
                      and operation.lease_owner = %s
                      and operation.lease_expires_at > %s
                    returning {_OPERATION_COLUMNS}
                    """,
                    (
                        progress.chunks_completed,
                        progress.chunks_failed,
                        progress.candles_received,
                        progress.candles_persisted,
                        progress.requests_completed,
                        progress.updated_at,
                        failure.code.value,
                        failure_message_for(failure.code),
                        failure.failed_at,
                        now,
                        operation_id,
                        expected_version,
                        owner_id,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise OperationVersionConflictError()
        except Error as error:
            _raise_operation_persistence_error(error)
        return market_operation_from_row(row)

    async def reconcile(
        self,
        *,
        operation: MarketOperationSnapshot,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        """Persist one fully validated recovery-only snapshot."""
        try:
            async with self._database.transaction() as connection:
                current = await self._locked_operation(
                    connection,
                    operation.operation_id,
                    expected_version,
                )
                validate_operation_update(current, operation, reconciliation=True)
                result_version = (
                    operation.result.dataset_version if operation.result is not None else None
                )
                result_checksum = (
                    operation.result.dataset_checksum if operation.result is not None else None
                )
                failure_code = (
                    operation.failure.code.value if operation.failure is not None else None
                )
                failure_message = (
                    failure_message_for(operation.failure.code)
                    if operation.failure is not None
                    else None
                )
                cursor = await connection.execute(
                    f"""
                    update public.market_data_operations as operation
                    set
                        status = %s,
                        local_job_id = %s,
                        chunks_completed = %s,
                        chunks_failed = %s,
                        candles_received = %s,
                        candles_persisted = %s,
                        requests_completed = %s,
                        progress_updated_at = %s,
                        lease_owner = %s,
                        lease_claimed_at = %s,
                        lease_heartbeat_at = %s,
                        lease_expires_at = %s,
                        result_dataset_version = %s,
                        result_dataset_checksum = %s,
                        failure_code = %s,
                        failure_message = %s,
                        finished_at = %s,
                        updated_at = %s,
                        version = %s
                    where operation.id = %s
                      and operation.version = %s
                    returning {_OPERATION_COLUMNS}
                    """,
                    (
                        operation.state.value,
                        operation.local_job_id,
                        operation.progress.chunks_completed,
                        operation.progress.chunks_failed,
                        operation.progress.candles_received,
                        operation.progress.candles_persisted,
                        operation.progress.requests_completed,
                        operation.progress.updated_at,
                        operation.lease.owner_id if operation.lease is not None else None,
                        operation.lease.claimed_at if operation.lease is not None else None,
                        operation.lease.heartbeat_at if operation.lease is not None else None,
                        (operation.lease.lease_expires_at if operation.lease is not None else None),
                        result_version,
                        result_checksum,
                        failure_code,
                        failure_message,
                        operation.finished_at,
                        operation.updated_at,
                        operation.record_version,
                        operation.operation_id,
                        expected_version,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise OperationVersionConflictError()
        except Error as error:
            _raise_operation_persistence_error(error)
        return market_operation_from_row(row)

    async def request_pause(
        self,
        *,
        operation_id: UUID,
        expected_version: int,
        now: datetime,
    ) -> MarketOperationSnapshot:
        return await self.request_state(
            operation_id=operation_id,
            target=MarketOperationState.PAUSE_REQUESTED,
            expected_version=expected_version,
            now=now,
        )

    async def request_cancel(
        self,
        *,
        operation_id: UUID,
        expected_version: int,
        now: datetime,
    ) -> MarketOperationSnapshot:
        return await self.request_state(
            operation_id=operation_id,
            target=MarketOperationState.CANCEL_REQUESTED,
            expected_version=expected_version,
            now=now,
        )

    async def resume(
        self,
        *,
        operation_id: UUID,
        expected_version: int,
        now: datetime,
    ) -> MarketOperationSnapshot:
        return await self.request_state(
            operation_id=operation_id,
            target=MarketOperationState.PENDING,
            expected_version=expected_version,
            now=now,
        )

    async def recover_expired(
        self,
        *,
        operation_id: UUID,
        expected_version: int,
        now: datetime,
    ) -> MarketOperationSnapshot:
        return await self.request_state(
            operation_id=operation_id,
            target=MarketOperationState.RECOVERING,
            expected_version=expected_version,
            now=now,
        )

    async def _locked_operation(
        self,
        connection: DatabaseConnection,
        operation_id: UUID,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        row = await _get_row(connection, operation_id, for_update=True)
        if row is None:
            raise MarketOperationNotFoundError()
        operation = market_operation_from_row(row)
        _require_expected_version(operation, expected_version)
        return operation
