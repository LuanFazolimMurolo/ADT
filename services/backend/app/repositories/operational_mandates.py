"""PostgreSQL persistence for Phase 7-06 operational mandates."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from uuid import UUID, uuid4

from psycopg import Error
from psycopg.errors import UniqueViolation

from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.operational_mandates.domain import (
    OperationalMandate,
    OperationalMandateInstrument,
    OperationalMandateRevision,
    OperationalMandateSpecification,
    OperationalMandateState,
    operational_mandate_create_request_fingerprint,
    operational_mandate_specification_checksum,
    operational_mandate_specifications_equal,
    validate_operational_mandate_idempotency_key,
)
from app.operational_mandates.errors import (
    InvalidOperationalMandateSpecificationError,
    OperationalMandateBoundsExceededError,
    OperationalMandateChecksumMismatchError,
    OperationalMandateIdempotencyConflictError,
    OperationalMandateNotFoundError,
    OperationalMandateRecordVersionConflictError,
    OperationalMandateRevisionConflictError,
    OperationalMandateStateTransitionConflictError,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_AGGREGATE_COLUMNS = """
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
"""

_REVISION_COLUMNS = """
    mandate_id,
    revision,
    schema_version,
    specification_checksum,
    name,
    description,
    created_by,
    created_at
"""

_IDEMPOTENCY_CONSTRAINT = "operational_mandates_actor_idempotency_key"

_REVISION_CONFLICT_MESSAGES = frozenset(
    {
        "operational_mandate_initial_revision_invalid",
        "operational_mandate_revision_sequence_invalid",
        "operational_mandate_initial_instrument_revision_invalid",
        "operational_mandate_revision_not_published",
        "operational_mandate_revision_publication_invalid",
        "operational_mandate_revision_missing",
    }
)

_STATE_CONFLICT_MESSAGES = frozenset(
    {
        "operational_mandate_revision_append_forbidden",
        "operational_mandate_instrument_append_forbidden",
        "operational_mandate_terminal",
        "operational_mandate_approval_invalid",
        "operational_mandate_draft_archive_invalid",
        "operational_mandate_approved_archive_invalid",
        "operational_mandate_transition_invalid",
    }
)

_CHECKSUM_CONSTRAINTS = frozenset(
    {
        "operational_mandates_approved_checksum_check",
        "operational_mandates_approved_revision_fkey",
        "operational_mandate_revisions_checksum_check",
    }
)


def _row_value(row: Mapping[str, object], key: str) -> object:
    try:
        return row[key]
    except KeyError:
        raise TypeError("persisted row is incomplete") from None


def _row_str(row: Mapping[str, object], key: str) -> str:
    value = _row_value(row, key)
    if not isinstance(value, str):
        raise TypeError("persisted value must be text")
    return value


def _row_optional_str(row: Mapping[str, object], key: str) -> str | None:
    value = _row_value(row, key)
    if value is not None and not isinstance(value, str):
        raise TypeError("persisted value must be optional text")
    return value


def _row_int(row: Mapping[str, object], key: str) -> int:
    value = _row_value(row, key)
    if type(value) is not int:
        raise TypeError("persisted value must be an exact integer")
    return value


def _row_optional_int(row: Mapping[str, object], key: str) -> int | None:
    value = _row_value(row, key)
    if value is not None and type(value) is not int:
        raise TypeError("persisted value must be an optional exact integer")
    return value


def _row_uuid(row: Mapping[str, object], key: str) -> UUID:
    value = _row_value(row, key)
    if not isinstance(value, UUID):
        raise TypeError("persisted value must be a UUID")
    return value


def _row_optional_uuid(row: Mapping[str, object], key: str) -> UUID | None:
    value = _row_value(row, key)
    if value is not None and not isinstance(value, UUID):
        raise TypeError("persisted value must be an optional UUID")
    return value


def _row_datetime(row: Mapping[str, object], key: str) -> datetime:
    value = _row_value(row, key)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("persisted timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _row_optional_datetime(
    row: Mapping[str, object],
    key: str,
) -> datetime | None:
    value = _row_value(row, key)
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("persisted timestamp must be optional and timezone-aware")
    return value.astimezone(UTC)


def operational_mandate_from_row(
    row: Mapping[str, object],
) -> OperationalMandate:
    """Strictly reconstruct one complete persisted aggregate."""

    try:
        return OperationalMandate(
            mandate_id=_row_uuid(row, "mandate_id"),
            state=OperationalMandateState(_row_str(row, "state")),
            current_revision=_row_int(row, "current_revision"),
            record_version=_row_int(row, "record_version"),
            approved_revision=_row_optional_int(row, "approved_revision"),
            approved_checksum=_row_optional_str(row, "approved_checksum"),
            created_by=_row_uuid(row, "created_by"),
            created_at=_row_datetime(row, "created_at"),
            approved_by=_row_optional_uuid(row, "approved_by"),
            approved_at=_row_optional_datetime(row, "approved_at"),
            archived_by=_row_optional_uuid(row, "archived_by"),
            archived_at=_row_optional_datetime(row, "archived_at"),
            create_idempotency_key=_row_str(row, "create_idempotency_key"),
            create_request_fingerprint=_row_str(
                row,
                "create_request_fingerprint",
            ),
        )
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def operational_mandate_revision_from_rows(
    row: Mapping[str, object],
    instrument_rows: Sequence[Mapping[str, object]],
) -> OperationalMandateRevision:
    """Strictly reconstruct one persisted revision and its instruments."""

    try:
        instruments = tuple(
            OperationalMandateInstrument(
                exchange=Exchange(_row_str(instrument, "exchange")),
                market_type=MarketType(_row_str(instrument, "market_type")),
                pair=TradingPair(
                    _row_str(instrument, "base_asset"),
                    _row_str(instrument, "quote_asset"),
                ),
            )
            for instrument in instrument_rows
        )
        specification = OperationalMandateSpecification(
            schema_version=_row_int(row, "schema_version"),
            name=_row_str(row, "name"),
            description=_row_str(row, "description"),
            instruments=instruments,
        )
        return OperationalMandateRevision(
            mandate_id=_row_uuid(row, "mandate_id"),
            revision=_row_int(row, "revision"),
            specification=specification,
            specification_checksum=_row_str(row, "specification_checksum"),
            created_by=_row_uuid(row, "created_by"),
            created_at=_row_datetime(row, "created_at"),
        )
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def _canonical_specification(
    value: OperationalMandateSpecification,
) -> OperationalMandateSpecification:
    if not isinstance(value, OperationalMandateSpecification):
        raise InvalidOperationalMandateSpecificationError()
    return OperationalMandateSpecification(
        schema_version=value.schema_version,
        name=value.name,
        description=value.description,
        instruments=value.instruments,
    )


def _require_mandate_id(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise InvalidOperationalMandateSpecificationError()
    return value


def _require_pagination(limit: object, offset: object) -> tuple[int, int]:
    if type(limit) is not int or type(offset) is not int:
        raise InvalidOperationalMandateSpecificationError()
    if not 1 <= limit <= 100 or offset < 0:
        raise OperationalMandateBoundsExceededError()
    return limit, offset


def _require_state(value: object) -> OperationalMandateState | None:
    if value is not None and not isinstance(value, OperationalMandateState):
        raise InvalidOperationalMandateSpecificationError()
    return value


def _require_actor_id(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidOperationalMandateSpecificationError()
    return value


def _require_timestamp(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise InvalidOperationalMandateSpecificationError()
    return value.astimezone(UTC)


def _require_expected_revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise OperationalMandateRevisionConflictError()
    return value


def _require_expected_record_version(value: object) -> int:
    if type(value) is not int or value < 1:
        raise OperationalMandateRecordVersionConflictError()
    return value


def _require_expected_checksum(value: object) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise OperationalMandateChecksumMismatchError()
    return value


def _raise_mandate_database_error(error: Error) -> NoReturn:
    """Translate only known Gate 2B conflicts and safely delegate the rest."""

    message = error.diag.message_primary or ""
    constraint = error.diag.constraint_name or ""

    if constraint == _IDEMPOTENCY_CONSTRAINT:
        raise OperationalMandateIdempotencyConflictError() from error
    if message == "operational_mandate_record_version_conflict":
        raise OperationalMandateRecordVersionConflictError() from error
    if message in _REVISION_CONFLICT_MESSAGES:
        raise OperationalMandateRevisionConflictError() from error
    if message in _STATE_CONFLICT_MESSAGES:
        raise OperationalMandateStateTransitionConflictError() from error
    if constraint in _CHECKSUM_CONSTRAINTS:
        raise OperationalMandateChecksumMismatchError() from error

    raise_domain_error(error)


async def _get_aggregate_row(
    connection: DatabaseConnection,
    mandate_id: UUID,
    *,
    lock: str | None = None,
) -> Mapping[str, object] | None:
    lock_clause = "" if lock is None else f" for {lock}"
    cursor = await connection.execute(
        f"""
        select {_AGGREGATE_COLUMNS}
        from public.operational_mandates
        where mandate_id = %s
        {lock_clause}
        """,
        (mandate_id,),
    )
    return await cursor.fetchone()


async def _get_idempotent_aggregate_row(
    connection: DatabaseConnection,
    *,
    actor_id: UUID,
    idempotency_key: str,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_AGGREGATE_COLUMNS}
        from public.operational_mandates
        where created_by = %s
          and create_idempotency_key = %s
        """,
        (actor_id, idempotency_key),
    )
    return await cursor.fetchone()


async def _load_revision(
    connection: DatabaseConnection,
    mandate_id: UUID,
    revision: int,
) -> OperationalMandateRevision | None:
    cursor = await connection.execute(
        f"""
        select {_REVISION_COLUMNS}
        from public.operational_mandate_revisions
        where mandate_id = %s
          and revision = %s
        """,
        (mandate_id, revision),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    instrument_cursor = await connection.execute(
        """
        select
            exchange,
            market_type,
            base_asset,
            quote_asset
        from public.operational_mandate_revision_instruments
        where mandate_id = %s
          and revision = %s
        order by exchange, market_type, base_asset, quote_asset
        """,
        (mandate_id, revision),
    )
    instrument_rows = await instrument_cursor.fetchall()
    return operational_mandate_revision_from_rows(row, instrument_rows)


async def _load_current_revision(
    connection: DatabaseConnection,
    mandate: OperationalMandate,
) -> OperationalMandateRevision:
    revision = await _load_revision(
        connection,
        mandate.mandate_id,
        mandate.current_revision,
    )
    if revision is None or revision.revision != mandate.current_revision:
        raise PersistenceError()
    return revision


def _total_from_row(row: Mapping[str, object] | None) -> int:
    if row is None:
        raise PersistenceError()
    try:
        total = _row_int(row, "total")
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
            mandate_id = _row_value(row, "mandate_id")
        except TypeError as error:
            raise PersistenceError() from error
        if mandate_id is None:
            if len(rows) != 1:
                raise PersistenceError()
            continue
        page_rows.append(row)
    return page_rows, total


def _revision_identity(row: Mapping[str, object]) -> tuple[UUID, int]:
    try:
        return _row_uuid(row, "mandate_id"), _row_int(row, "revision")
    except TypeError as error:
        raise PersistenceError() from error


async def _revisions_by_identity(
    connection: DatabaseConnection,
    revision_rows: Sequence[Mapping[str, object]],
) -> dict[tuple[UUID, int], OperationalMandateRevision]:
    if not revision_rows:
        return {}

    identities = [_revision_identity(row) for row in revision_rows]
    if len(set(identities)) != len(identities):
        raise PersistenceError()

    mandate_ids = [mandate_id for mandate_id, _ in identities]
    revisions = [revision for _, revision in identities]
    instrument_cursor = await connection.execute(
        """
        select
            instrument.mandate_id,
            instrument.revision,
            instrument.exchange,
            instrument.market_type,
            instrument.base_asset,
            instrument.quote_asset
        from public.operational_mandate_revision_instruments as instrument
        join unnest(%s::uuid[], %s::bigint[]) as requested(mandate_id, revision)
          on requested.mandate_id = instrument.mandate_id
         and requested.revision = instrument.revision
        order by
            instrument.mandate_id,
            instrument.revision,
            instrument.exchange,
            instrument.market_type,
            instrument.base_asset,
            instrument.quote_asset
        """,
        (mandate_ids, revisions),
    )
    instrument_rows = await instrument_cursor.fetchall()
    grouped_instruments: dict[
        tuple[UUID, int],
        list[Mapping[str, object]],
    ] = {identity: [] for identity in identities}
    for instrument_row in instrument_rows:
        identity = _revision_identity(instrument_row)
        if identity not in grouped_instruments:
            raise PersistenceError()
        grouped_instruments[identity].append(instrument_row)

    return {
        identity: operational_mandate_revision_from_rows(
            row,
            grouped_instruments[identity],
        )
        for identity, row in zip(identities, revision_rows, strict=True)
    }


async def _load_revisions_batch(
    connection: DatabaseConnection,
    identities: Sequence[tuple[UUID, int]],
) -> dict[tuple[UUID, int], OperationalMandateRevision]:
    if not identities:
        return {}

    mandate_ids = [mandate_id for mandate_id, _ in identities]
    revisions = [revision for _, revision in identities]
    cursor = await connection.execute(
        """
        select
            revision_record.mandate_id,
            revision_record.revision,
            revision_record.schema_version,
            revision_record.specification_checksum,
            revision_record.name,
            revision_record.description,
            revision_record.created_by,
            revision_record.created_at
        from public.operational_mandate_revisions as revision_record
        join unnest(%s::uuid[], %s::bigint[]) as requested(mandate_id, revision)
          on requested.mandate_id = revision_record.mandate_id
         and requested.revision = revision_record.revision
        """,
        (mandate_ids, revisions),
    )
    rows = await cursor.fetchall()
    result = await _revisions_by_identity(connection, rows)
    if set(result) != set(identities):
        raise PersistenceError()
    return result


async def _insert_revision(
    connection: DatabaseConnection,
    *,
    mandate_id: UUID,
    revision: int,
    specification: OperationalMandateSpecification,
    checksum: str,
    actor_id: UUID,
    now: datetime,
) -> None:
    await connection.execute(
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
            specification.schema_version,
            checksum,
            specification.name,
            specification.description,
            actor_id,
            now,
        ),
    )
    for instrument in specification.instruments:
        await connection.execute(
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
                instrument.exchange.value,
                instrument.market_type.value,
                instrument.pair.base,
                instrument.pair.quote,
            ),
        )


async def _current_pair_from_row(
    connection: DatabaseConnection,
    row: Mapping[str, object],
) -> tuple[OperationalMandate, OperationalMandateRevision]:
    mandate = operational_mandate_from_row(row)
    revision = await _load_current_revision(connection, mandate)
    return mandate, revision


async def _raise_replace_cas_conflict(
    connection: DatabaseConnection,
    mandate_id: UUID,
    *,
    expected_revision: int,
    expected_record_version: int,
) -> NoReturn:
    row = await _get_aggregate_row(connection, mandate_id)
    if row is None:
        raise OperationalMandateNotFoundError()
    current = operational_mandate_from_row(row)
    if current.current_revision != expected_revision:
        raise OperationalMandateRevisionConflictError()
    if current.record_version != expected_record_version:
        raise OperationalMandateRecordVersionConflictError()
    if current.state is not OperationalMandateState.DRAFT:
        raise OperationalMandateStateTransitionConflictError()
    raise PersistenceError()


class PostgresOperationalMandateRepository:
    """Transactional PostgreSQL implementation of the mandate repository."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, mandate_id: UUID) -> OperationalMandate | None:
        """Return one strictly reconstructed aggregate or ``None``."""

        mandate_id = _require_mandate_id(mandate_id)
        try:
            async with self._database.transaction() as connection:
                row = await _get_aggregate_row(connection, mandate_id)
        except Error as error:
            _raise_mandate_database_error(error)
        return None if row is None else operational_mandate_from_row(row)

    async def get_revision(
        self,
        mandate_id: UUID,
        revision: int,
    ) -> OperationalMandateRevision | None:
        """Return one immutable revision with canonically ordered instruments."""

        mandate_id = _require_mandate_id(mandate_id)
        try:
            async with self._database.transaction() as connection:
                result = await _load_revision(connection, mandate_id, revision)
        except Error as error:
            _raise_mandate_database_error(error)
        return result

    async def get_current(
        self,
        mandate_id: UUID,
    ) -> tuple[OperationalMandate, OperationalMandateRevision] | None:
        """Return an aggregate and its exact current immutable revision."""

        mandate_id = _require_mandate_id(mandate_id)
        try:
            async with self._database.transaction() as connection:
                row = await _get_aggregate_row(
                    connection,
                    mandate_id,
                    lock="share",
                )
                if row is None:
                    return None
                return await _current_pair_from_row(connection, row)
        except Error as error:
            _raise_mandate_database_error(error)

    async def list_current(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalMandateState | None = None,
    ) -> tuple[
        list[tuple[OperationalMandate, OperationalMandateRevision]],
        int,
    ]:
        """Return a bounded stable page with each exact current revision."""

        limit, offset = _require_pagination(limit, offset)
        state = _require_state(state)
        where_clause = "" if state is None else "where state = %s"
        filter_parameters: tuple[object, ...] = () if state is None else (state.value,)
        try:
            async with self._database.transaction() as connection:
                page_cursor = await connection.execute(
                    f"""
                    with filtered as (
                        select {_AGGREGATE_COLUMNS}
                        from public.operational_mandates
                        {where_clause}
                    ),
                    page as (
                        select *
                        from filtered
                        order by created_at desc, mandate_id desc
                        limit %s offset %s
                    ),
                    total as (
                        select count(*) as total
                        from filtered
                    )
                    select
                        total.total,
                        page.mandate_id,
                        page.state,
                        page.current_revision,
                        page.record_version,
                        page.approved_revision,
                        page.approved_checksum,
                        page.created_by,
                        page.created_at,
                        page.approved_by,
                        page.approved_at,
                        page.archived_by,
                        page.archived_at,
                        page.create_idempotency_key,
                        page.create_request_fingerprint
                    from total
                    left join page on true
                    order by created_at desc, mandate_id desc
                    """,  # noqa: S608 - where_clause is a closed internal fragment.
                    (*filter_parameters, limit, offset),
                )
                rows, total = _page_rows_and_total(await page_cursor.fetchall())
                mandates = [operational_mandate_from_row(row) for row in rows]
                identities = [
                    (mandate.mandate_id, mandate.current_revision) for mandate in mandates
                ]
                revisions = await _load_revisions_batch(connection, identities)
        except Error as error:
            _raise_mandate_database_error(error)

        return [
            (mandate, revisions[(mandate.mandate_id, mandate.current_revision)])
            for mandate in mandates
        ], total

    async def list_revisions(
        self,
        mandate_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[OperationalMandateRevision], int]:
        """Return one mandate's bounded immutable history newest-first."""

        mandate_id = _require_mandate_id(mandate_id)
        limit, offset = _require_pagination(limit, offset)
        try:
            async with self._database.transaction() as connection:
                exists_cursor = await connection.execute(
                    """
                    select 1
                    from public.operational_mandates
                    where mandate_id = %s
                    """,
                    (mandate_id,),
                )
                if await exists_cursor.fetchone() is None:
                    raise OperationalMandateNotFoundError()

                page_cursor = await connection.execute(
                    f"""
                    with filtered as (
                        select {_REVISION_COLUMNS}
                        from public.operational_mandate_revisions
                        where mandate_id = %s
                    ),
                    page as (
                        select *
                        from filtered
                        order by revision desc
                        limit %s offset %s
                    ),
                    total as (
                        select count(*) as total
                        from filtered
                    )
                    select
                        total.total,
                        page.mandate_id,
                        page.revision,
                        page.schema_version,
                        page.specification_checksum,
                        page.name,
                        page.description,
                        page.created_by,
                        page.created_at
                    from total
                    left join page on true
                    order by revision desc
                    """,
                    (mandate_id, limit, offset),
                )
                rows, total = _page_rows_and_total(await page_cursor.fetchall())
                revisions = await _revisions_by_identity(connection, rows)
        except Error as error:
            _raise_mandate_database_error(error)

        return [revisions[_revision_identity(row)] for row in rows], total

    async def create(
        self,
        specification: OperationalMandateSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> tuple[OperationalMandate, OperationalMandateRevision]:
        """Create a draft atomically or resolve actor-scoped idempotent replay."""

        canonical = _canonical_specification(specification)
        actor_id = _require_actor_id(actor_id)
        idempotency_key = validate_operational_mandate_idempotency_key(idempotency_key)
        now = _require_timestamp(now)
        checksum = operational_mandate_specification_checksum(canonical)
        fingerprint = operational_mandate_create_request_fingerprint(canonical)

        try:
            async with self._database.transaction() as connection:
                existing = await _get_idempotent_aggregate_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    return await self._resolve_create_replay_row(
                        connection,
                        existing,
                        fingerprint=fingerprint,
                    )

                mandate_id = uuid4()
                await _insert_revision(
                    connection,
                    mandate_id=mandate_id,
                    revision=1,
                    specification=canonical,
                    checksum=checksum,
                    actor_id=actor_id,
                    now=now,
                )
                cursor = await connection.execute(
                    f"""
                    insert into public.operational_mandates (
                        mandate_id,
                        state,
                        current_revision,
                        record_version,
                        created_by,
                        created_at,
                        create_idempotency_key,
                        create_request_fingerprint
                    )
                    values (%s, 'DRAFT', 1, 1, %s, %s, %s, %s)
                    returning {_AGGREGATE_COLUMNS}
                    """,
                    (
                        mandate_id,
                        actor_id,
                        now,
                        idempotency_key,
                        fingerprint,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise PersistenceError()
                return await _current_pair_from_row(connection, row)
        except UniqueViolation as error:
            if error.diag.constraint_name != _IDEMPOTENCY_CONSTRAINT:
                _raise_mandate_database_error(error)
            return await self._resolve_create_replay(
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
        except Error as error:
            _raise_mandate_database_error(error)

    async def replace_draft(
        self,
        mandate_id: UUID,
        specification: OperationalMandateSpecification,
        *,
        expected_revision: int,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> tuple[OperationalMandate, OperationalMandateRevision]:
        """Append one changed draft revision or return an exact semantic no-op."""

        mandate_id = _require_mandate_id(mandate_id)
        canonical = _canonical_specification(specification)
        expected_revision = _require_expected_revision(expected_revision)
        expected_record_version = _require_expected_record_version(expected_record_version)
        actor_id = _require_actor_id(actor_id)
        now = _require_timestamp(now)

        try:
            async with self._database.transaction() as connection:
                row = await _get_aggregate_row(connection, mandate_id, lock="update")
                if row is None:
                    raise OperationalMandateNotFoundError()
                current = operational_mandate_from_row(row)

                if current.current_revision != expected_revision:
                    raise OperationalMandateRevisionConflictError()
                if current.record_version != expected_record_version:
                    raise OperationalMandateRecordVersionConflictError()
                if current.state is not OperationalMandateState.DRAFT:
                    raise OperationalMandateStateTransitionConflictError()

                current_revision = await _load_current_revision(connection, current)
                if operational_mandate_specifications_equal(
                    current_revision.specification,
                    canonical,
                ):
                    return current, current_revision

                new_revision_number = current.current_revision + 1
                checksum = operational_mandate_specification_checksum(canonical)
                await _insert_revision(
                    connection,
                    mandate_id=mandate_id,
                    revision=new_revision_number,
                    specification=canonical,
                    checksum=checksum,
                    actor_id=actor_id,
                    now=now,
                )
                cursor = await connection.execute(
                    f"""
                    update public.operational_mandates
                    set current_revision = %s,
                        record_version = record_version + 1
                    where mandate_id = %s
                      and state = 'DRAFT'
                      and current_revision = %s
                      and record_version = %s
                    returning {_AGGREGATE_COLUMNS}
                    """,
                    (
                        new_revision_number,
                        mandate_id,
                        expected_revision,
                        expected_record_version,
                    ),
                )
                updated_row = await cursor.fetchone()
                if updated_row is None:
                    await _raise_replace_cas_conflict(
                        connection,
                        mandate_id,
                        expected_revision=expected_revision,
                        expected_record_version=expected_record_version,
                    )
                if updated_row is None:
                    raise PersistenceError()
                updated = operational_mandate_from_row(updated_row)
                revision = await _load_current_revision(connection, updated)
                return updated, revision
        except Error as error:
            _raise_mandate_database_error(error)

    async def approve(
        self,
        mandate_id: UUID,
        *,
        expected_revision: int,
        expected_checksum: str,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalMandate:
        """Seal one exact draft revision or resolve an exact approval replay."""

        mandate_id = _require_mandate_id(mandate_id)
        expected_revision = _require_expected_revision(expected_revision)
        expected_checksum = _require_expected_checksum(expected_checksum)
        expected_record_version = _require_expected_record_version(expected_record_version)
        actor_id = _require_actor_id(actor_id)
        now = _require_timestamp(now)

        try:
            async with self._database.transaction() as connection:
                row = await _get_aggregate_row(connection, mandate_id, lock="update")
                if row is None:
                    raise OperationalMandateNotFoundError()
                current = operational_mandate_from_row(row)

                if current.state is OperationalMandateState.APPROVED:
                    if (
                        current.approved_revision == expected_revision
                        and current.approved_checksum == expected_checksum
                        and current.approved_by == actor_id
                        and current.record_version == expected_record_version + 1
                    ):
                        return current
                    raise OperationalMandateStateTransitionConflictError()
                if current.state is not OperationalMandateState.DRAFT:
                    raise OperationalMandateStateTransitionConflictError()
                if current.current_revision != expected_revision:
                    raise OperationalMandateRevisionConflictError()
                if current.record_version != expected_record_version:
                    raise OperationalMandateRecordVersionConflictError()

                revision = await _load_current_revision(connection, current)
                if revision.specification_checksum != expected_checksum:
                    raise OperationalMandateChecksumMismatchError()
                if now < current.created_at:
                    raise OperationalMandateStateTransitionConflictError()

                cursor = await connection.execute(
                    f"""
                    update public.operational_mandates
                    set state = 'APPROVED',
                        record_version = record_version + 1,
                        approved_revision = current_revision,
                        approved_checksum = %s,
                        approved_by = %s,
                        approved_at = %s
                    where mandate_id = %s
                      and state = 'DRAFT'
                      and current_revision = %s
                      and record_version = %s
                    returning {_AGGREGATE_COLUMNS}
                    """,
                    (
                        expected_checksum,
                        actor_id,
                        now,
                        mandate_id,
                        expected_revision,
                        expected_record_version,
                    ),
                )
                updated_row = await cursor.fetchone()
                if updated_row is None:
                    await _raise_replace_cas_conflict(
                        connection,
                        mandate_id,
                        expected_revision=expected_revision,
                        expected_record_version=expected_record_version,
                    )
                if updated_row is None:
                    raise PersistenceError()
                return operational_mandate_from_row(updated_row)
        except Error as error:
            _raise_mandate_database_error(error)

    async def archive(
        self,
        mandate_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalMandate:
        """Archive a draft/approved mandate or resolve one exact replay."""

        mandate_id = _require_mandate_id(mandate_id)
        expected_record_version = _require_expected_record_version(expected_record_version)
        actor_id = _require_actor_id(actor_id)
        now = _require_timestamp(now)

        try:
            async with self._database.transaction() as connection:
                row = await _get_aggregate_row(connection, mandate_id, lock="update")
                if row is None:
                    raise OperationalMandateNotFoundError()
                current = operational_mandate_from_row(row)

                if current.state is OperationalMandateState.ARCHIVED:
                    if (
                        current.archived_by == actor_id
                        and current.record_version == expected_record_version + 1
                    ):
                        return current
                    raise OperationalMandateStateTransitionConflictError()
                if current.record_version != expected_record_version:
                    raise OperationalMandateRecordVersionConflictError()
                if current.state not in {
                    OperationalMandateState.DRAFT,
                    OperationalMandateState.APPROVED,
                }:
                    raise OperationalMandateStateTransitionConflictError()
                if now < current.created_at or (
                    current.approved_at is not None and now < current.approved_at
                ):
                    raise OperationalMandateStateTransitionConflictError()

                cursor = await connection.execute(
                    f"""
                    update public.operational_mandates
                    set state = 'ARCHIVED',
                        record_version = record_version + 1,
                        archived_by = %s,
                        archived_at = %s
                    where mandate_id = %s
                      and state = %s
                      and current_revision = %s
                      and record_version = %s
                    returning {_AGGREGATE_COLUMNS}
                    """,
                    (
                        actor_id,
                        now,
                        mandate_id,
                        current.state.value,
                        current.current_revision,
                        expected_record_version,
                    ),
                )
                updated_row = await cursor.fetchone()
                if updated_row is None:
                    raise PersistenceError()
                return operational_mandate_from_row(updated_row)
        except Error as error:
            _raise_mandate_database_error(error)

    async def _resolve_create_replay(
        self,
        *,
        actor_id: UUID,
        idempotency_key: str,
        fingerprint: str,
    ) -> tuple[OperationalMandate, OperationalMandateRevision]:
        try:
            async with self._database.transaction() as connection:
                row = await _get_idempotent_aggregate_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
                if row is None:
                    raise PersistenceError()
                return await self._resolve_create_replay_row(
                    connection,
                    row,
                    fingerprint=fingerprint,
                )
        except Error as error:
            _raise_mandate_database_error(error)

    async def _resolve_create_replay_row(
        self,
        connection: DatabaseConnection,
        row: Mapping[str, object],
        *,
        fingerprint: str,
    ) -> tuple[OperationalMandate, OperationalMandateRevision]:
        mandate = operational_mandate_from_row(row)
        if mandate.create_request_fingerprint != fingerprint:
            raise OperationalMandateIdempotencyConflictError()
        revision = await _load_current_revision(connection, mandate)
        return mandate, revision
