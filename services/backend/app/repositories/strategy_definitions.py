"""PostgreSQL persistence for revisioned strategy definitions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NoReturn, cast
from uuid import UUID

from psycopg import Error
from psycopg.types.json import Jsonb

from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import PersistenceError
from app.strategies.definitions import (
    StrategyDefinition,
    StrategyDefinitionSpec,
    StrategyDefinitionState,
    strategy_parameter_document_from_json,
    strategy_parameter_document_to_json,
)
from app.strategies.errors import (
    InvalidStrategyDefinitionError,
    StrategyDefinitionArchivedError,
    StrategyDefinitionCompatibilityError,
    StrategyDefinitionNameConflictError,
    StrategyDefinitionNotFoundError,
    StrategyDefinitionRevisionConflictError,
)

_DEFINITION_COLUMNS = """
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
    updated_at,
    archived_at
"""
_NAME_CONSTRAINTS = frozenset(
    {
        "strategy_definitions_display_name_key_key",
        "strategy_definitions_display_name_key_uidx",
    }
)


def _as_utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _as_optional_utc_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _as_utc_datetime(value)


def _definition_from_row(row: Mapping[str, object]) -> StrategyDefinition:
    try:
        raw_parameters = row["parameters"]
        if not isinstance(raw_parameters, Mapping):
            raise TypeError("parameters must be a mapping")
        spec = StrategyDefinitionSpec(
            display_name=cast(str, row["display_name"]),
            plugin_name=cast(str, row["plugin_name"]),
            plugin_version=cast(str, row["plugin_version"]),
            plugin_schema_version=int(cast(int, row["plugin_schema_version"])),
            lifecycle_version=int(cast(int, row["lifecycle_version"])),
            parameters=strategy_parameter_document_from_json(
                cast(Mapping[str, object], raw_parameters)
            ),
            parameters_checksum=cast(str, row["parameters_checksum"]),
        )
        return StrategyDefinition(
            id=cast(UUID, row["id"]),
            spec=spec,
            state=StrategyDefinitionState(cast(str, row["state"])),
            revision=int(cast(int, row["revision"])),
            created_by=cast(UUID, row["created_by"]),
            updated_by=cast(UUID, row["updated_by"]),
            created_at=_as_utc_datetime(row["created_at"]),
            updated_at=_as_utc_datetime(row["updated_at"]),
            archived_at=_as_optional_utc_datetime(row["archived_at"]),
        )
    except (KeyError, TypeError, ValueError, InvalidStrategyDefinitionError) as error:
        raise StrategyDefinitionCompatibilityError() from error


def _raise_strategy_database_error(error: Error) -> NoReturn:
    constraint_name = error.diag.constraint_name
    if constraint_name in _NAME_CONSTRAINTS:
        raise StrategyDefinitionNameConflictError() from error
    raise_domain_error(error)


async def _raise_missing_or_conflict(
    connection: DatabaseConnection,
    definition_id: UUID,
    *,
    expected_revision: int,
) -> NoReturn:
    cursor = await connection.execute(
        """
        select state, revision
        from public.strategy_definitions
        where id = %s
        """,
        (definition_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise StrategyDefinitionNotFoundError()
    if row["state"] == StrategyDefinitionState.ARCHIVED.value:
        raise StrategyDefinitionArchivedError()
    if int(row["revision"]) != expected_revision:
        raise StrategyDefinitionRevisionConflictError()
    raise PersistenceError()


class PostgresStrategyDefinitionRepository:
    """Persist definitions with database-backed optimistic concurrency."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        include_archived: bool,
    ) -> tuple[list[StrategyDefinition], int]:
        """Return a stable bounded page and the matching unpaginated total."""

        state_filter = "" if include_archived else "where state = 'ACTIVE'"
        try:
            async with self._database.transaction() as connection:
                count_cursor = await connection.execute(
                    f"""
                    select count(*) as total
                    from public.strategy_definitions
                    {state_filter}
                    """  # noqa: S608 - state_filter is an internal constant fragment.
                )
                count_row = await count_cursor.fetchone()
                total = 0 if count_row is None else int(count_row["total"])

                cursor = await connection.execute(
                    f"""
                    select {_DEFINITION_COLUMNS}
                    from public.strategy_definitions
                    {state_filter}
                    order by created_at desc, id desc
                    limit %s offset %s
                    """,  # noqa: S608 - state_filter is an internal constant fragment.
                    (limit, offset),
                )
                rows = await cursor.fetchall()
        except Error as error:
            _raise_strategy_database_error(error)

        return [_definition_from_row(row) for row in rows], total

    async def get(self, definition_id: UUID) -> StrategyDefinition | None:
        """Return one definition or ``None`` when it does not exist."""

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    select {_DEFINITION_COLUMNS}
                    from public.strategy_definitions
                    where id = %s
                    """,
                    (definition_id,),
                )
                row = await cursor.fetchone()
        except Error as error:
            _raise_strategy_database_error(error)
        return None if row is None else _definition_from_row(row)

    async def create(
        self,
        spec: StrategyDefinitionSpec,
        *,
        actor_id: UUID,
    ) -> StrategyDefinition:
        """Insert one active definition with revision one."""

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    insert into public.strategy_definitions (
                        display_name,
                        plugin_name,
                        plugin_version,
                        plugin_schema_version,
                        lifecycle_version,
                        parameters,
                        parameters_checksum,
                        created_by,
                        updated_by
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning {_DEFINITION_COLUMNS}
                    """,
                    (
                        spec.display_name,
                        spec.plugin_name,
                        spec.plugin_version,
                        spec.plugin_schema_version,
                        spec.lifecycle_version,
                        Jsonb(strategy_parameter_document_to_json(spec.parameters)),
                        spec.parameters_checksum,
                        actor_id,
                        actor_id,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise PersistenceError()
        except Error as error:
            _raise_strategy_database_error(error)
        return _definition_from_row(row)

    async def replace(
        self,
        definition_id: UUID,
        spec: StrategyDefinitionSpec,
        *,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition:
        """Replace mutable fields only when the active revision still matches."""

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    update public.strategy_definitions
                    set
                        display_name = %s,
                        plugin_name = %s,
                        plugin_version = %s,
                        plugin_schema_version = %s,
                        lifecycle_version = %s,
                        parameters = %s,
                        parameters_checksum = %s,
                        revision = revision + 1,
                        updated_by = %s
                    where id = %s
                      and revision = %s
                      and state = 'ACTIVE'
                    returning {_DEFINITION_COLUMNS}
                    """,
                    (
                        spec.display_name,
                        spec.plugin_name,
                        spec.plugin_version,
                        spec.plugin_schema_version,
                        spec.lifecycle_version,
                        Jsonb(strategy_parameter_document_to_json(spec.parameters)),
                        spec.parameters_checksum,
                        actor_id,
                        definition_id,
                        expected_revision,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    await _raise_missing_or_conflict(
                        connection,
                        definition_id,
                        expected_revision=expected_revision,
                    )
        except Error as error:
            _raise_strategy_database_error(error)
        if row is None:
            raise PersistenceError()
        return _definition_from_row(row)

    async def archive(
        self,
        definition_id: UUID,
        *,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition:
        """Perform the one-way ACTIVE-to-ARCHIVED transition."""

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    update public.strategy_definitions
                    set
                        state = 'ARCHIVED',
                        revision = revision + 1,
                        updated_by = %s,
                        archived_at = now()
                    where id = %s
                      and revision = %s
                      and state = 'ACTIVE'
                    returning {_DEFINITION_COLUMNS}
                    """,
                    (actor_id, definition_id, expected_revision),
                )
                row = await cursor.fetchone()
                if row is None:
                    await _raise_missing_or_conflict(
                        connection,
                        definition_id,
                        expected_revision=expected_revision,
                    )
        except Error as error:
            _raise_strategy_database_error(error)
        if row is None:
            raise PersistenceError()
        return _definition_from_row(row)
