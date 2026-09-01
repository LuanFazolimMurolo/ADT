"""PostgreSQL persistence for Phase 7-09 paper-session materializations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from uuid import UUID, uuid4

from psycopg import Error
from psycopg.errors import UniqueViolation

from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError
from app.operational_paper_session_materializations import (
    InvalidOperationalPaperSessionMaterializationSpecificationError,
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationBoundsExceededError,
    OperationalPaperSessionMaterializationChecksumMismatchError,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationPlan,
    OperationalPaperSessionMaterializationProfileBinding,
    OperationalPaperSessionMaterializationSpecification,
    OperationalPaperSessionMaterializationState,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
    materialize_operational_paper_session_materialization,
    operational_paper_session_materialization_specification_checksum,
    operational_paper_session_materialization_specifications_equal,
    prepare_operational_paper_session_materialization,
)

_AUTHORIZATION_CONSTRAINT = "op_ps_mat_authorization_key"
_POSTGRESQL_BIGINT_MAX = (1 << 63) - 1

_MATERIALIZATION_COLUMNS = """
    materialization_id,
    schema_version,
    materialization_contract_version,
    state,
    record_version,
    authorization_id,
    authorization_checksum,
    profile_id,
    profile_approved_revision,
    profile_specification_checksum,
    mandate_id,
    mandate_approved_revision,
    mandate_specification_checksum,
    simulation_id,
    config_checksum,
    session_id,
    materialization_checksum,
    prepared_by,
    prepared_at,
    materialized_by,
    materialized_at
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


def operational_paper_session_materialization_from_row(
    row: Mapping[str, object],
) -> OperationalPaperSessionMaterialization:
    """Strictly reconstruct one persisted paper-session materialization."""

    try:
        state = OperationalPaperSessionMaterializationState(_text(row, "state"))
        record_version = _integer(row, "record_version")
        if (
            state is OperationalPaperSessionMaterializationState.PREPARED and record_version != 1
        ) or (
            state is OperationalPaperSessionMaterializationState.MATERIALIZED
            and record_version != 2
        ):
            raise TypeError("persisted state/version shape is invalid")

        return OperationalPaperSessionMaterialization(
            materialization_id=_uuid(row, "materialization_id"),
            schema_version=_integer(row, "schema_version"),
            materialization_contract_version=_integer(row, "materialization_contract_version"),
            state=state,
            record_version=record_version,
            authorization_binding=OperationalPaperSessionMaterializationAuthorizationBinding(
                authorization_id=_uuid(row, "authorization_id"),
                authorization_checksum=_text(row, "authorization_checksum"),
            ),
            profile_binding=OperationalPaperSessionMaterializationProfileBinding(
                profile_id=_uuid(row, "profile_id"),
                approved_revision=_integer(row, "profile_approved_revision"),
                specification_checksum=_text(row, "profile_specification_checksum"),
            ),
            mandate_binding=OperationalPaperSessionMaterializationMandateBinding(
                mandate_id=_uuid(row, "mandate_id"),
                approved_revision=_integer(row, "mandate_approved_revision"),
                specification_checksum=_text(row, "mandate_specification_checksum"),
            ),
            simulation_id=_uuid(row, "simulation_id"),
            config_checksum=_text(row, "config_checksum"),
            session_id=_text(row, "session_id"),
            materialization_checksum=_text(row, "materialization_checksum"),
            prepared_by=_uuid(row, "prepared_by"),
            prepared_at=_timestamp(row, "prepared_at"),
            materialized_by=_optional_uuid(row, "materialized_by"),
            materialized_at=_optional_timestamp(row, "materialized_at"),
        )
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidOperationalPaperSessionMaterializationSpecificationError()
    return value


def _expected_record_version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _POSTGRESQL_BIGINT_MAX:
        raise InvalidOperationalPaperSessionMaterializationSpecificationError()
    return value


def _canonical_plan(value: object) -> OperationalPaperSessionMaterializationPlan:
    if not isinstance(value, OperationalPaperSessionMaterializationPlan):
        raise InvalidOperationalPaperSessionMaterializationSpecificationError()
    return OperationalPaperSessionMaterializationPlan(
        specification=value.specification,
        config=value.config,
    )


def _now(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise InvalidOperationalPaperSessionMaterializationSpecificationError()
    return value.astimezone(UTC)


def _materialization_specification(
    materialization: OperationalPaperSessionMaterialization,
) -> OperationalPaperSessionMaterializationSpecification:
    return OperationalPaperSessionMaterializationSpecification(
        schema_version=materialization.schema_version,
        materialization_contract_version=materialization.materialization_contract_version,
        authorization_binding=materialization.authorization_binding,
        profile_binding=materialization.profile_binding,
        mandate_binding=materialization.mandate_binding,
        simulation_id=materialization.simulation_id,
        config_checksum=materialization.config_checksum,
        session_id=materialization.session_id,
    )


def _replay_materialization(
    row: Mapping[str, object],
    specification: OperationalPaperSessionMaterializationSpecification,
    materialization_checksum: str,
) -> OperationalPaperSessionMaterialization:
    materialization = operational_paper_session_materialization_from_row(row)
    persisted_specification = _materialization_specification(materialization)
    if (
        materialization.materialization_checksum != materialization_checksum
        or not operational_paper_session_materialization_specifications_equal(
            persisted_specification,
            specification,
        )
    ):
        raise OperationalPaperSessionMaterializationChecksumMismatchError()
    return materialization


def _require_pagination(limit: object, offset: object) -> tuple[int, int]:
    if type(limit) is not int or type(offset) is not int:
        raise InvalidOperationalPaperSessionMaterializationSpecificationError()
    if not 1 <= limit <= 100 or not 0 <= offset <= _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionMaterializationBoundsExceededError()
    return limit, offset


def _require_state(
    value: object,
) -> OperationalPaperSessionMaterializationState | None:
    if value is not None and not isinstance(value, OperationalPaperSessionMaterializationState):
        raise InvalidOperationalPaperSessionMaterializationSpecificationError()
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
            materialization_id = _value(row, "materialization_id")
        except TypeError as error:
            raise PersistenceError() from error
        if materialization_id is None:
            if len(rows) != 1:
                raise PersistenceError()
            continue
        page_rows.append(row)
    return page_rows, total


async def _materialization_row(
    connection: DatabaseConnection,
    materialization_id: UUID,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_MATERIALIZATION_COLUMNS}
        from public.operational_paper_session_materializations
        where materialization_id = %s
        """,
        (materialization_id,),
    )
    return await cursor.fetchone()


async def _locked_materialization_row(
    connection: DatabaseConnection,
    materialization_id: UUID,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_MATERIALIZATION_COLUMNS}
        from public.operational_paper_session_materializations
        where materialization_id = %s
        for update
        """,
        (materialization_id,),
    )
    return await cursor.fetchone()


async def _authorization_materialization_row(
    connection: DatabaseConnection,
    authorization_id: UUID,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_MATERIALIZATION_COLUMNS}
        from public.operational_paper_session_materializations
        where authorization_id = %s
        """,
        (authorization_id,),
    )
    return await cursor.fetchone()


def _raise_materialization_database_error(error: Error) -> NoReturn:
    message = error.diag.message_primary or ""
    if message in {
        "operational_paper_session_materialization_record_version_conflict",
        "operational_paper_session_materialization_terminal",
        "operational_paper_session_materialization_transition_forbidden",
        "operational_paper_session_materialization_materialized_metadata_required",
        "operational_paper_session_materialization_materialized_at_invalid",
    }:
        raise OperationalPaperSessionMaterializationStateTransitionConflictError() from error
    raise_domain_error(error)


class PostgresOperationalPaperSessionMaterializationRepository:
    """PostgreSQL adapter for operational paper-session materialization persistence."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(
        self,
        materialization_id: UUID,
    ) -> OperationalPaperSessionMaterialization | None:
        materialization_id = _require_uuid(materialization_id)
        try:
            async with self._database.transaction() as connection:
                row = await _materialization_row(connection, materialization_id)
        except Error as error:
            raise_domain_error(error)
        return None if row is None else operational_paper_session_materialization_from_row(row)

    async def get_by_authorization(
        self,
        authorization_id: UUID,
    ) -> OperationalPaperSessionMaterialization | None:
        authorization_id = _require_uuid(authorization_id)
        try:
            async with self._database.transaction() as connection:
                row = await _authorization_materialization_row(connection, authorization_id)
        except Error as error:
            raise_domain_error(error)
        return None if row is None else operational_paper_session_materialization_from_row(row)

    async def prepare(
        self,
        plan: OperationalPaperSessionMaterializationPlan,
        *,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionMaterialization:
        """Persist PREPARED provenance or replay the authorization's exact row."""

        plan = _canonical_plan(plan)
        actor_id = _require_uuid(actor_id)
        now = _now(now)
        specification = plan.specification
        materialization_checksum = operational_paper_session_materialization_specification_checksum(
            specification
        )
        prepared = prepare_operational_paper_session_materialization(
            materialization_id=uuid4(),
            plan=plan,
            prepared_by=actor_id,
            prepared_at=now,
        )
        authorization_id = specification.authorization_binding.authorization_id

        try:
            async with self._database.transaction() as connection:
                existing = await _authorization_materialization_row(
                    connection,
                    authorization_id,
                )
                if existing is not None:
                    return _replay_materialization(
                        existing,
                        specification,
                        materialization_checksum,
                    )

                cursor = await connection.execute(
                    f"""
                    insert into public.operational_paper_session_materializations (
                        materialization_id,
                        schema_version,
                        materialization_contract_version,
                        state,
                        record_version,
                        authorization_id,
                        authorization_checksum,
                        profile_id,
                        profile_approved_revision,
                        profile_specification_checksum,
                        mandate_id,
                        mandate_approved_revision,
                        mandate_specification_checksum,
                        simulation_id,
                        config_checksum,
                        session_id,
                        materialization_checksum,
                        prepared_by,
                        prepared_at,
                        materialized_by,
                        materialized_at
                    )
                    values (
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    returning {_MATERIALIZATION_COLUMNS}
                    """,
                    (
                        prepared.materialization_id,
                        prepared.schema_version,
                        prepared.materialization_contract_version,
                        prepared.state.value,
                        prepared.record_version,
                        prepared.authorization_binding.authorization_id,
                        prepared.authorization_binding.authorization_checksum,
                        prepared.profile_binding.profile_id,
                        prepared.profile_binding.approved_revision,
                        prepared.profile_binding.specification_checksum,
                        prepared.mandate_binding.mandate_id,
                        prepared.mandate_binding.approved_revision,
                        prepared.mandate_binding.specification_checksum,
                        prepared.simulation_id,
                        prepared.config_checksum,
                        prepared.session_id,
                        prepared.materialization_checksum,
                        prepared.prepared_by,
                        prepared.prepared_at,
                        prepared.materialized_by,
                        prepared.materialized_at,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise PersistenceError()
                return operational_paper_session_materialization_from_row(row)
        except UniqueViolation as error:
            if error.diag.constraint_name != _AUTHORIZATION_CONSTRAINT:
                raise_domain_error(error)
            return await self._resolve_prepare_replay(
                authorization_id=authorization_id,
                specification=specification,
                materialization_checksum=materialization_checksum,
            )
        except Error as error:
            raise_domain_error(error)

    async def _resolve_prepare_replay(
        self,
        *,
        authorization_id: UUID,
        specification: OperationalPaperSessionMaterializationSpecification,
        materialization_checksum: str,
    ) -> OperationalPaperSessionMaterialization:
        try:
            async with self._database.transaction() as connection:
                row = await _authorization_materialization_row(
                    connection,
                    authorization_id,
                )
                if row is None:
                    raise PersistenceError()
                return _replay_materialization(
                    row,
                    specification,
                    materialization_checksum,
                )
        except Error as error:
            raise_domain_error(error)

    async def mark_materialized(
        self,
        materialization_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionMaterialization:
        """Mark one PREPARED aggregate MATERIALIZED without publishing its config."""

        materialization_id = _require_uuid(materialization_id)
        expected_record_version = _expected_record_version(expected_record_version)
        actor_id = _require_uuid(actor_id)
        now = _now(now)

        try:
            async with self._database.transaction() as connection:
                row = await _locked_materialization_row(connection, materialization_id)
                if row is None:
                    raise PersistenceError()
                current = operational_paper_session_materialization_from_row(row)

                if current.state is OperationalPaperSessionMaterializationState.MATERIALIZED:
                    if (
                        current.record_version == expected_record_version + 1
                        and current.materialized_by == actor_id
                    ):
                        return current
                    raise OperationalPaperSessionMaterializationStateTransitionConflictError()

                if (
                    current.state is not OperationalPaperSessionMaterializationState.PREPARED
                    or current.record_version != expected_record_version
                    or now < current.prepared_at
                ):
                    raise OperationalPaperSessionMaterializationStateTransitionConflictError()

                materialized = materialize_operational_paper_session_materialization(
                    current,
                    materialized_by=actor_id,
                    materialized_at=now,
                )
                cursor = await connection.execute(
                    f"""
                    update public.operational_paper_session_materializations
                    set state = %s,
                        record_version = %s,
                        materialized_by = %s,
                        materialized_at = %s
                    where materialization_id = %s
                      and state = %s
                      and record_version = %s
                    returning {_MATERIALIZATION_COLUMNS}
                    """,
                    (
                        materialized.state.value,
                        materialized.record_version,
                        materialized.materialized_by,
                        materialized.materialized_at,
                        current.materialization_id,
                        current.state.value,
                        expected_record_version,
                    ),
                )
                updated = await cursor.fetchone()
                if updated is None:
                    raise PersistenceError()
                return operational_paper_session_materialization_from_row(updated)
        except Error as error:
            _raise_materialization_database_error(error)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionMaterializationState | None = None,
    ) -> tuple[list[OperationalPaperSessionMaterialization], int]:
        limit, offset = _require_pagination(limit, offset)
        state = _require_state(state)
        where_clause = "" if state is None else "where state = %s"
        filter_parameters: tuple[object, ...] = () if state is None else (state.value,)
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    with filtered as (
                        select {_MATERIALIZATION_COLUMNS}
                        from public.operational_paper_session_materializations
                        {where_clause}
                    ),

                    page as (
                        select *
                        from filtered
                        order by prepared_at desc, materialization_id desc
                        limit %s offset %s
                    ),
                    total as (
                        select count(*) as total
                        from filtered
                    )
                    select total.total, page.*
                    from total
                    left join page on true
                    order by page.prepared_at desc, page.materialization_id desc
                    """,  # noqa: S608 - where_clause is a closed internal fragment.
                    (*filter_parameters, limit, offset),
                )
                rows, total = _page_rows_and_total(await cursor.fetchall())
        except Error as error:
            raise_domain_error(error)

        return [operational_paper_session_materialization_from_row(row) for row in rows], total
