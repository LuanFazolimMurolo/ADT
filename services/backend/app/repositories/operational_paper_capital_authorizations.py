"""PostgreSQL persistence for Phase 7-08 operational paper-capital authorizations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import NoReturn
from uuid import UUID, uuid4

from psycopg import Error
from psycopg.errors import UniqueViolation

from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import (
    DomainError,
    PersistenceError,
    SimulationNotFoundError,
    SimulationTerminalError,
)
from app.operational_paper_capital_authorizations import (
    InvalidOperationalPaperCapitalAuthorizationSpecificationError,
    OperationalPaperCapitalAuthorization,
    OperationalPaperCapitalAuthorizationActiveProfileConflictError,
    OperationalPaperCapitalAuthorizationBoundsExceededError,
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationCurrencyMismatchError,
    OperationalPaperCapitalAuthorizationIdempotencyConflictError,
    OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalAuthorizationProfileStateConflictError,
    OperationalPaperCapitalAuthorizationState,
    OperationalPaperCapitalAuthorizationStateTransitionConflictError,
    build_operational_paper_capital_authorization_specification,
    operational_paper_capital_authorization_create_intent_fingerprint,
    operational_paper_capital_authorization_specification_checksum,
    validate_operational_paper_capital_authorization_idempotency_key,
)

_IDEMPOTENCY_CONSTRAINT = "op_pc_auth_actor_idempotency_key"
_ACTIVE_PROFILE_CONSTRAINT = "op_pc_auth_one_active_per_profile_uidx"

_PROFILE_STATE_MESSAGES = frozenset(
    {
        "operational_paper_capital_authorization_profile_missing",
        "operational_paper_capital_authorization_profile_not_approved",
        "operational_paper_capital_authorization_profile_binding_mismatch",
        "operational_paper_capital_authorization_profile_revision_missing",
    }
)
_CURRENCY_MESSAGES = frozenset(
    {
        "operational_paper_capital_authorization_quote_asset_mismatch",
        "operational_paper_capital_authorization_currency_mismatch",
    }
)

_POSTGRESQL_BIGINT_MAX = (1 << 63) - 1


_AUTHORIZATION_COLUMNS = """
    authorization_id,
    schema_version,
    state,
    record_version,
    profile_id,
    profile_approved_revision,
    profile_specification_checksum,
    simulation_id,
    quote_asset,
    authorized_capital,
    authorization_checksum,
    created_by,
    created_at,
    revoked_by,
    revoked_at,
    create_idempotency_key,
    create_intent_fingerprint
"""


def _value(row: Mapping[str, object], key: str) -> object:
    try:
        return row[key]
    except KeyError:
        raise TypeError("persisted row is incomplete") from None


def _uuid(row: Mapping[str, object], key: str) -> UUID:
    value = _value(row, key)
    if not isinstance(value, UUID):
        raise TypeError("persisted value must be a UUID")
    return value


def _optional_uuid(row: Mapping[str, object], key: str) -> UUID | None:
    value = _value(row, key)
    if value is not None and not isinstance(value, UUID):
        raise TypeError("persisted value must be an optional UUID")
    return value


def _integer(row: Mapping[str, object], key: str) -> int:
    value = _value(row, key)
    if type(value) is not int:
        raise TypeError("persisted value must be an exact integer")
    return value


def _text(row: Mapping[str, object], key: str) -> str:
    value = _value(row, key)
    if not isinstance(value, str):
        raise TypeError("persisted value must be text")
    return value


def _decimal(row: Mapping[str, object], key: str) -> Decimal:
    value = _value(row, key)
    if type(value) is not Decimal:
        raise TypeError("persisted value must be Decimal")
    return value


def _timestamp(row: Mapping[str, object], key: str) -> datetime:
    value = _value(row, key)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("persisted timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _optional_timestamp(row: Mapping[str, object], key: str) -> datetime | None:
    value = _value(row, key)
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("persisted timestamp must be optional and timezone-aware")
    return value.astimezone(UTC)


def operational_paper_capital_authorization_from_row(
    row: Mapping[str, object],
) -> OperationalPaperCapitalAuthorization:
    """Strictly reconstruct one persisted operational paper-capital authorization."""

    try:
        return OperationalPaperCapitalAuthorization(
            authorization_id=_uuid(row, "authorization_id"),
            schema_version=_integer(row, "schema_version"),
            state=OperationalPaperCapitalAuthorizationState(_text(row, "state")),
            record_version=_integer(row, "record_version"),
            profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
                profile_id=_uuid(row, "profile_id"),
                approved_revision=_integer(row, "profile_approved_revision"),
                specification_checksum=_text(row, "profile_specification_checksum"),
            ),
            simulation_id=_uuid(row, "simulation_id"),
            quote_asset=_text(row, "quote_asset"),
            authorized_capital=_decimal(row, "authorized_capital"),
            authorization_checksum=_text(row, "authorization_checksum"),
            created_by=_uuid(row, "created_by"),
            created_at=_timestamp(row, "created_at"),
            revoked_by=_optional_uuid(row, "revoked_by"),
            revoked_at=_optional_timestamp(row, "revoked_at"),
            create_idempotency_key=_text(row, "create_idempotency_key"),
            create_intent_fingerprint=_text(row, "create_intent_fingerprint"),
        )
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    return value


def _require_pagination(limit: object, offset: object) -> tuple[int, int]:
    if type(limit) is not int or type(offset) is not int:
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    if not 1 <= limit <= 100 or not 0 <= offset <= _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperCapitalAuthorizationBoundsExceededError()
    return limit, offset


def _require_state(
    value: object,
) -> OperationalPaperCapitalAuthorizationState | None:
    if value is not None and not isinstance(
        value,
        OperationalPaperCapitalAuthorizationState,
    ):
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    return value


def _total_from_row(row: Mapping[str, object] | None) -> int:
    if row is None:
        raise PersistenceError()
    try:
        total = _integer(row, "total")
    except TypeError as error:
        raise PersistenceError() from error
    if total < 0:
        raise PersistenceError()
    return total


def _page_rows_and_total(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[Mapping[str, object]], int]:
    if not rows:
        raise PersistenceError()

    total = _total_from_row(rows[0])
    page_rows: list[Mapping[str, object]] = []
    for row in rows:
        if _total_from_row(row) != total:
            raise PersistenceError()
        try:
            authorization_id = _value(row, "authorization_id")
        except TypeError as error:
            raise PersistenceError() from error
        if authorization_id is None:
            if len(rows) != 1:
                raise PersistenceError()
            continue
        page_rows.append(row)
    return page_rows, total


def _canonical_intent(
    value: object,
) -> OperationalPaperCapitalAuthorizationCreateIntent:
    if not isinstance(value, OperationalPaperCapitalAuthorizationCreateIntent):
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    return OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=value.profile_binding,
        simulation_id=value.simulation_id,
        quote_asset=value.quote_asset,
        authorized_capital=value.authorized_capital,
    )


def _expected_record_version(value: object) -> int:
    from app.operational_paper_capital_authorizations.errors import (
        OperationalPaperCapitalAuthorizationRecordVersionConflictError,
    )

    if type(value) is not int or value < 1:
        raise OperationalPaperCapitalAuthorizationRecordVersionConflictError()
    return value


def _now(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    return value.astimezone(UTC)


def _raise_authorization_database_error(error: Error) -> NoReturn:
    message = error.diag.message_primary or ""
    constraint = error.diag.constraint_name or ""

    if constraint == _IDEMPOTENCY_CONSTRAINT:
        raise OperationalPaperCapitalAuthorizationIdempotencyConflictError() from error
    if constraint == _ACTIVE_PROFILE_CONSTRAINT:
        raise OperationalPaperCapitalAuthorizationActiveProfileConflictError() from error
    if message == "operational_paper_capital_authorization_initial_state_invalid":
        raise OperationalPaperCapitalAuthorizationStateTransitionConflictError() from error
    if message == "operational_paper_capital_authorization_simulation_missing":
        raise SimulationNotFoundError() from error
    if message == "operational_paper_capital_authorization_simulation_not_active":
        raise SimulationTerminalError() from error
    if message in _PROFILE_STATE_MESSAGES:
        raise OperationalPaperCapitalAuthorizationProfileStateConflictError() from error
    if message in _CURRENCY_MESSAGES:
        raise OperationalPaperCapitalAuthorizationCurrencyMismatchError() from error
    if message == "operational_paper_capital_authorization_insufficient_available_capital":
        raise OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError() from error
    if message == "operational_paper_capital_authorization_record_version_conflict":
        from app.operational_paper_capital_authorizations.errors import (
            OperationalPaperCapitalAuthorizationRecordVersionConflictError,
        )

        raise OperationalPaperCapitalAuthorizationRecordVersionConflictError() from error
    if message in {
        "operational_paper_capital_authorization_terminal",
        "operational_paper_capital_authorization_transition_forbidden",
        "operational_paper_capital_authorization_revocation_metadata_required",
    }:
        raise OperationalPaperCapitalAuthorizationStateTransitionConflictError() from error

    raise_domain_error(error)


async def _authorization_row(
    connection: DatabaseConnection,
    authorization_id: UUID,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_AUTHORIZATION_COLUMNS}
        from public.operational_paper_capital_authorizations
        where authorization_id = %s
        """,
        (authorization_id,),
    )
    return await cursor.fetchone()


async def _idempotent_row(
    connection: DatabaseConnection,
    *,
    actor_id: UUID,
    idempotency_key: str,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_AUTHORIZATION_COLUMNS}
        from public.operational_paper_capital_authorizations
        where created_by = %s and create_idempotency_key = %s
        """,
        (actor_id, idempotency_key),
    )
    return await cursor.fetchone()


class PostgresOperationalPaperCapitalAuthorizationRepository:
    """Transactional PostgreSQL adapter for paper-capital authorization persistence."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(
        self,
        authorization_id: UUID,
    ) -> OperationalPaperCapitalAuthorization | None:
        """Return one authorization by immutable identifier."""

        authorization_id = _require_uuid(authorization_id)
        try:
            async with self._database.transaction() as connection:
                row = await _authorization_row(connection, authorization_id)
        except Error as error:
            _raise_authorization_database_error(error)

        return None if row is None else operational_paper_capital_authorization_from_row(row)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperCapitalAuthorizationState | None = None,
    ) -> tuple[list[OperationalPaperCapitalAuthorization], int]:
        """Return one bounded newest-first authorization page and its matching total."""

        limit, offset = _require_pagination(limit, offset)
        state = _require_state(state)
        where_clause = "" if state is None else "where state = %s"
        filter_parameters: tuple[object, ...] = () if state is None else (state.value,)
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    with filtered as (
                        select {_AUTHORIZATION_COLUMNS}
                        from public.operational_paper_capital_authorizations
                        {where_clause}
                    ),
                    page as (
                        select *
                        from filtered
                        order by created_at desc, authorization_id desc
                        limit %s offset %s
                    ),
                    total as (
                        select count(*) as total
                        from filtered
                    )
                    select total.total, page.*
                    from total
                    left join page on true
                    order by page.created_at desc, page.authorization_id desc
                    """,  # noqa: S608 - where_clause is a closed internal fragment.
                    (*filter_parameters, limit, offset),
                )
                rows, total = _page_rows_and_total(await cursor.fetchall())
        except Error as error:
            _raise_authorization_database_error(error)

        return [operational_paper_capital_authorization_from_row(row) for row in rows], total

    async def create(
        self,
        intent: OperationalPaperCapitalAuthorizationCreateIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> OperationalPaperCapitalAuthorization:
        """Create one reservation or replay the actor-scoped committed request."""

        intent = _canonical_intent(intent)
        actor_id = _require_uuid(actor_id)
        idempotency_key = validate_operational_paper_capital_authorization_idempotency_key(
            idempotency_key
        )
        now = _now(now)
        fingerprint = operational_paper_capital_authorization_create_intent_fingerprint(intent)
        specification = build_operational_paper_capital_authorization_specification(intent)
        checksum = operational_paper_capital_authorization_specification_checksum(specification)

        try:
            async with self._database.transaction() as connection:
                existing = await _idempotent_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    return self._replay_row(existing, fingerprint)

                authorization_id = uuid4()
                cursor = await connection.execute(
                    f"""
                    insert into public.operational_paper_capital_authorizations (
                        authorization_id,
                        schema_version,
                        state,
                        record_version,
                        profile_id,
                        profile_approved_revision,
                        profile_specification_checksum,
                        simulation_id,
                        quote_asset,
                        authorized_capital,
                        authorization_checksum,
                        created_by,
                        created_at,
                        create_idempotency_key,
                        create_intent_fingerprint
                    )
                    values (
                        %s, %s, 'AUTHORIZED', 1,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    returning {_AUTHORIZATION_COLUMNS}
                    """,
                    (
                        authorization_id,
                        specification.schema_version,
                        specification.profile_binding.profile_id,
                        specification.profile_binding.approved_revision,
                        specification.profile_binding.specification_checksum,
                        specification.simulation_id,
                        specification.quote_asset,
                        specification.authorized_capital,
                        checksum,
                        actor_id,
                        now,
                        idempotency_key,
                        fingerprint,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise PersistenceError()
                return operational_paper_capital_authorization_from_row(row)
        except UniqueViolation as error:
            if error.diag.constraint_name != _IDEMPOTENCY_CONSTRAINT:
                _raise_authorization_database_error(error)
            return await self._resolve_replay(
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
        except Error as error:
            _raise_authorization_database_error(error)

    async def _resolve_replay(
        self,
        *,
        actor_id: UUID,
        idempotency_key: str,
        fingerprint: str,
    ) -> OperationalPaperCapitalAuthorization:
        try:
            async with self._database.transaction() as connection:
                row = await _idempotent_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
                if row is None:
                    raise PersistenceError()
                return self._replay_row(row, fingerprint)
        except Error as error:
            _raise_authorization_database_error(error)

    @staticmethod
    def _replay_row(
        row: Mapping[str, object],
        fingerprint: str,
    ) -> OperationalPaperCapitalAuthorization:
        authorization = operational_paper_capital_authorization_from_row(row)
        if authorization.create_intent_fingerprint != fingerprint:
            raise OperationalPaperCapitalAuthorizationIdempotencyConflictError()
        return authorization

    async def revoke(
        self,
        authorization_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperCapitalAuthorization:
        """Revoke one active reservation using the simulation as financial mutex."""

        from app.operational_paper_capital_authorizations.domain import (
            OperationalPaperCapitalAuthorizationState,
        )
        from app.operational_paper_capital_authorizations.errors import (
            OperationalPaperCapitalAuthorizationNotFoundError,
            OperationalPaperCapitalAuthorizationRecordVersionConflictError,
            OperationalPaperCapitalAuthorizationStateTransitionConflictError,
        )

        authorization_id = _require_uuid(authorization_id)
        expected_record_version = _expected_record_version(expected_record_version)
        actor_id = _require_uuid(actor_id)
        now = _now(now)

        try:
            async with self._database.transaction() as connection:
                preliminary_cursor = await connection.execute(
                    """
                    select simulation_id
                    from public.operational_paper_capital_authorizations
                    where authorization_id = %s
                    """,
                    (authorization_id,),
                )
                preliminary = await preliminary_cursor.fetchone()
                if preliminary is None:
                    raise OperationalPaperCapitalAuthorizationNotFoundError()

                simulation_id = preliminary["simulation_id"]
                if not isinstance(simulation_id, UUID):
                    raise PersistenceError()

                simulation_cursor = await connection.execute(
                    """
                    select id
                    from public.simulation_runs
                    where id = %s
                    for update
                    """,
                    (simulation_id,),
                )
                if await simulation_cursor.fetchone() is None:
                    raise PersistenceError()

                authorization_cursor = await connection.execute(
                    f"""
                    select {_AUTHORIZATION_COLUMNS}
                    from public.operational_paper_capital_authorizations
                    where authorization_id = %s
                    for update
                    """,
                    (authorization_id,),
                )
                row = await authorization_cursor.fetchone()
                if row is None:
                    raise OperationalPaperCapitalAuthorizationNotFoundError()

                current = operational_paper_capital_authorization_from_row(row)
                if current.simulation_id != simulation_id:
                    raise PersistenceError()

                if current.state is OperationalPaperCapitalAuthorizationState.REVOKED:
                    if (
                        current.revoked_by == actor_id
                        and current.record_version == expected_record_version + 1
                    ):
                        return current
                    raise OperationalPaperCapitalAuthorizationStateTransitionConflictError()

                if current.record_version != expected_record_version:
                    raise OperationalPaperCapitalAuthorizationRecordVersionConflictError()

                if current.state is not OperationalPaperCapitalAuthorizationState.AUTHORIZED:
                    raise OperationalPaperCapitalAuthorizationStateTransitionConflictError()

                if now < current.created_at:
                    raise OperationalPaperCapitalAuthorizationStateTransitionConflictError()

                update_cursor = await connection.execute(
                    f"""
                    update public.operational_paper_capital_authorizations
                    set state = 'REVOKED',
                        record_version = record_version + 1,
                        revoked_by = %s,
                        revoked_at = %s
                    where authorization_id = %s
                      and simulation_id = %s
                      and state = 'AUTHORIZED'
                      and record_version = %s
                    returning {_AUTHORIZATION_COLUMNS}
                    """,
                    (
                        actor_id,
                        now,
                        authorization_id,
                        simulation_id,
                        expected_record_version,
                    ),
                )
                updated = await update_cursor.fetchone()
                if updated is None:
                    raise PersistenceError()
                return operational_paper_capital_authorization_from_row(updated)
        except Error as error:
            _raise_authorization_database_error(error)
