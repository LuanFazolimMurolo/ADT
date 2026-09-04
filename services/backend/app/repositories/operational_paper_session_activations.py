"""PostgreSQL persistence for Phase 7-10 paper-session activation grants."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from uuid import UUID, uuid4

from psycopg import Error

from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import (
    DomainError,
    PersistenceError,
    SimulationNotFoundError,
    SimulationTerminalError,
)
from app.operational_mandates.errors import (
    OperationalMandateNotFoundError,
    OperationalMandateRevisionConflictError,
    OperationalMandateStateTransitionConflictError,
)
from app.operational_paper_capital_authorizations.errors import (
    OperationalPaperCapitalAuthorizationChecksumMismatchError,
    OperationalPaperCapitalAuthorizationCurrencyMismatchError,
    OperationalPaperCapitalAuthorizationNotFoundError,
    OperationalPaperCapitalAuthorizationStateTransitionConflictError,
)
from app.operational_paper_session_activations import (
    InvalidOperationalPaperSessionActivationSpecificationError,
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationBoundsExceededError,
    OperationalPaperSessionActivationCreateIntent,
    OperationalPaperSessionActivationCurrentGrantConflictError,
    OperationalPaperSessionActivationIdempotencyConflictError,
    OperationalPaperSessionActivationNotFoundError,
    OperationalPaperSessionActivationRecordVersionConflictError,
    OperationalPaperSessionActivationSpecification,
    OperationalPaperSessionActivationState,
    OperationalPaperSessionActivationStateTransitionConflictError,
    authorize_operational_paper_session_activation,
    operational_paper_session_activation_create_intent_fingerprint,
    revoke_operational_paper_session_activation,
    validate_operational_paper_session_activation_idempotency_key,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationChecksumMismatchError,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationNotFoundError,
    OperationalPaperSessionMaterializationProfileBinding,
    OperationalPaperSessionMaterializationProfileBindingConflictError,
    OperationalPaperSessionMaterializationQuoteAssetConflictError,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
)
from app.operational_paper_session_profiles.errors import (
    OperationalPaperSessionProfileNotFoundError,
    OperationalPaperSessionProfileRevisionConflictError,
    OperationalPaperSessionProfileStateTransitionConflictError,
)

_POSTGRESQL_BIGINT_MAX = (1 << 63) - 1

_ACTIVATION_COLUMNS = """
    activation_id,
    schema_version,
    activation_contract_version,
    state,
    record_version,
    materialization_id,
    materialization_checksum,
    authorization_id,
    authorization_checksum,
    profile_id,
    profile_approved_revision,
    profile_specification_checksum,
    mandate_id,
    mandate_approved_revision,
    mandate_specification_checksum,
    simulation_id,
    session_id,
    config_checksum,
    activation_checksum,
    authorized_by,
    authorized_at,
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
    if not isinstance(value, UUID) or value.int == 0:
        raise TypeError("persisted value must be a nonzero UUID")
    return value


def _optional_uuid(row: Mapping[str, object], key: str) -> UUID | None:
    value = _value(row, key)
    if value is None:
        return None
    if not isinstance(value, UUID) or value.int == 0:
        raise TypeError("persisted value must be an optional nonzero UUID")
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


def _optional_timestamp(
    row: Mapping[str, object],
    key: str,
) -> datetime | None:
    value = _value(row, key)
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("persisted timestamp must be optional and timezone-aware")
    return value.astimezone(UTC)


def operational_paper_session_activation_from_row(
    row: Mapping[str, object],
) -> OperationalPaperSessionActivation:
    """Strictly reconstruct one persisted paper-session activation grant."""

    try:
        state = OperationalPaperSessionActivationState(_text(row, "state"))
        record_version = _integer(row, "record_version")

        if (state is OperationalPaperSessionActivationState.AUTHORIZED and record_version != 1) or (
            state is OperationalPaperSessionActivationState.REVOKED and record_version != 2
        ):
            raise TypeError("persisted state/version shape is invalid")

        return OperationalPaperSessionActivation(
            activation_id=_uuid(row, "activation_id"),
            schema_version=_integer(row, "schema_version"),
            activation_contract_version=_integer(
                row,
                "activation_contract_version",
            ),
            state=state,
            record_version=record_version,
            materialization_id=_uuid(row, "materialization_id"),
            materialization_checksum=_text(row, "materialization_checksum"),
            authorization_binding=(
                OperationalPaperSessionMaterializationAuthorizationBinding(
                    authorization_id=_uuid(row, "authorization_id"),
                    authorization_checksum=_text(
                        row,
                        "authorization_checksum",
                    ),
                )
            ),
            profile_binding=OperationalPaperSessionMaterializationProfileBinding(
                profile_id=_uuid(row, "profile_id"),
                approved_revision=_integer(
                    row,
                    "profile_approved_revision",
                ),
                specification_checksum=_text(
                    row,
                    "profile_specification_checksum",
                ),
            ),
            mandate_binding=OperationalPaperSessionMaterializationMandateBinding(
                mandate_id=_uuid(row, "mandate_id"),
                approved_revision=_integer(
                    row,
                    "mandate_approved_revision",
                ),
                specification_checksum=_text(
                    row,
                    "mandate_specification_checksum",
                ),
            ),
            simulation_id=_uuid(row, "simulation_id"),
            session_id=_text(row, "session_id"),
            config_checksum=_text(row, "config_checksum"),
            activation_checksum=_text(row, "activation_checksum"),
            authorized_by=_uuid(row, "authorized_by"),
            authorized_at=_timestamp(row, "authorized_at"),
            revoked_by=_optional_uuid(row, "revoked_by"),
            revoked_at=_optional_timestamp(row, "revoked_at"),
            create_idempotency_key=_text(
                row,
                "create_idempotency_key",
            ),
            create_intent_fingerprint=_text(
                row,
                "create_intent_fingerprint",
            ),
        )
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return value


def _require_pagination(limit: object, offset: object) -> tuple[int, int]:
    if type(limit) is not int or type(offset) is not int:
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    if not 1 <= limit <= 100 or not 0 <= offset <= _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionActivationBoundsExceededError()
    return limit, offset


def _require_state(
    value: object,
) -> OperationalPaperSessionActivationState | None:
    if value is not None and not isinstance(value, OperationalPaperSessionActivationState):
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return value


def _expected_record_version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionActivationRecordVersionConflictError()
    return value


def _now(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return value.astimezone(UTC)


def _canonical_specification(
    value: object,
) -> OperationalPaperSessionActivationSpecification:
    if not isinstance(value, OperationalPaperSessionActivationSpecification):
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return OperationalPaperSessionActivationSpecification(
        schema_version=value.schema_version,
        activation_contract_version=value.activation_contract_version,
        materialization_id=value.materialization_id,
        materialization_checksum=value.materialization_checksum,
        authorization_binding=value.authorization_binding,
        profile_binding=value.profile_binding,
        mandate_binding=value.mandate_binding,
        simulation_id=value.simulation_id,
        session_id=value.session_id,
        config_checksum=value.config_checksum,
    )


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
            activation_id = _value(row, "activation_id")
        except TypeError as error:
            raise PersistenceError() from error
        if activation_id is None:
            if len(rows) != 1:
                raise PersistenceError()
            continue
        page_rows.append(row)
    return page_rows, total


async def _activation_row(
    connection: DatabaseConnection,
    activation_id: UUID,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_ACTIVATION_COLUMNS}
        from public.operational_paper_session_activations
        where activation_id = %s
        """,
        (activation_id,),
    )
    return await cursor.fetchone()


async def _locked_activation_row(
    connection: DatabaseConnection,
    activation_id: UUID,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_ACTIVATION_COLUMNS}
        from public.operational_paper_session_activations
        where activation_id = %s
        for update
        """,
        (activation_id,),
    )
    return await cursor.fetchone()


async def _actor_idempotency_row(
    connection: DatabaseConnection,
    *,
    actor_id: UUID,
    idempotency_key: str,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_ACTIVATION_COLUMNS}
        from public.operational_paper_session_activations
        where authorized_by = %s
          and create_idempotency_key = %s
        """,
        (actor_id, idempotency_key),
    )
    return await cursor.fetchone()


async def _current_materialization_activation_row(
    connection: DatabaseConnection,
    materialization_id: UUID,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_ACTIVATION_COLUMNS}
        from public.operational_paper_session_activations
        where materialization_id = %s
          and state = 'AUTHORIZED'
        """,
        (materialization_id,),
    )
    return await cursor.fetchone()


def _raise_activation_database_error(error: Error) -> NoReturn:
    message = error.diag.message_primary or ""

    if message == "operational_paper_session_activation_simulation_missing":
        raise SimulationNotFoundError() from error
    if message == "operational_paper_session_activation_simulation_not_active":
        raise SimulationTerminalError() from error

    if message == "operational_paper_session_activation_authorization_missing":
        raise OperationalPaperCapitalAuthorizationNotFoundError() from error
    if message == "operational_paper_session_activation_authorization_not_authorized":
        raise OperationalPaperCapitalAuthorizationStateTransitionConflictError() from error
    if message == "operational_paper_session_activation_authorization_checksum_mismatch":
        raise OperationalPaperCapitalAuthorizationChecksumMismatchError() from error
    if message == "operational_paper_session_activation_authorization_profile_binding_mismatch":
        raise OperationalPaperSessionMaterializationProfileBindingConflictError() from error

    if message == "operational_paper_session_activation_profile_missing":
        raise OperationalPaperSessionProfileNotFoundError() from error
    if message == "operational_paper_session_activation_profile_not_approved":
        raise OperationalPaperSessionProfileStateTransitionConflictError() from error
    if message in {
        "operational_paper_session_activation_profile_binding_mismatch",
        "operational_paper_session_activation_profile_revision_missing",
    }:
        raise OperationalPaperSessionProfileRevisionConflictError() from error

    if message == "operational_paper_session_activation_mandate_missing":
        raise OperationalMandateNotFoundError() from error
    if message == "operational_paper_session_activation_mandate_not_approved":
        raise OperationalMandateStateTransitionConflictError() from error
    if message == "operational_paper_session_activation_mandate_binding_mismatch":
        raise OperationalMandateRevisionConflictError() from error

    if message == "operational_paper_session_activation_materialization_missing":
        raise OperationalPaperSessionMaterializationNotFoundError() from error
    if message == "operational_paper_session_activation_materialization_not_materialized":
        raise OperationalPaperSessionMaterializationStateTransitionConflictError() from error
    if message == "operational_paper_session_activation_materialization_checksum_mismatch":
        raise OperationalPaperSessionMaterializationChecksumMismatchError() from error
    if message == "operational_paper_session_activation_authorization_profile_binding_mismatch":
        raise OperationalPaperSessionMaterializationProfileBindingConflictError() from error
    if message == "operational_paper_session_activation_authorization_quote_asset_mismatch":
        raise OperationalPaperSessionMaterializationQuoteAssetConflictError() from error
    if message == "operational_paper_session_activation_currency_mismatch":
        raise OperationalPaperCapitalAuthorizationCurrencyMismatchError() from error
    if message == "operational_paper_session_activation_materialization_config_binding_mismatch":
        raise OperationalPaperSessionMaterializationConfigIdentityConflictError() from error

    if message == "operational_paper_session_activation_record_version_conflict":
        raise OperationalPaperSessionActivationRecordVersionConflictError() from error
    if message in {
        "operational_paper_session_activation_initial_state_invalid",
        "operational_paper_session_activation_terminal",
        "operational_paper_session_activation_transition_forbidden",
        "operational_paper_session_activation_revocation_metadata_required",
        "operational_paper_session_activation_revoked_at_invalid",
    }:
        raise OperationalPaperSessionActivationStateTransitionConflictError() from error

    raise_domain_error(error)


class PostgresOperationalPaperSessionActivationRepository:
    """Transactional PostgreSQL adapter for paper-session activation grants."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(
        self,
        activation_id: UUID,
    ) -> OperationalPaperSessionActivation | None:
        """Return one historical activation by immutable identifier."""

        activation_id = _require_uuid(activation_id)
        try:
            async with self._database.transaction() as connection:
                row = await _activation_row(connection, activation_id)
        except Error as error:
            _raise_activation_database_error(error)
        return None if row is None else operational_paper_session_activation_from_row(row)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionActivationState | None = None,
        materialization_id: UUID | None = None,
    ) -> tuple[list[OperationalPaperSessionActivation], int]:
        """Return a bounded newest-first activation page and matching total."""

        limit, offset = _require_pagination(limit, offset)
        state = _require_state(state)
        if materialization_id is not None:
            materialization_id = _require_uuid(materialization_id)

        filters: list[str] = []
        filter_parameters: list[object] = []
        if state is not None:
            filters.append("state = %s")
            filter_parameters.append(state.value)
        if materialization_id is not None:
            filters.append("materialization_id = %s")
            filter_parameters.append(materialization_id)
        where_clause = "" if not filters else f"where {' and '.join(filters)}"

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"""
                    with filtered as (
                        select {_ACTIVATION_COLUMNS}
                        from public.operational_paper_session_activations
                        {where_clause}
                    ),
                    page as (
                        select *
                        from filtered
                        order by authorized_at desc, activation_id desc
                        limit %s offset %s
                    ),
                    total as (
                        select count(*) as total
                        from filtered
                    )
                    select total.total, page.*
                    from total
                    left join page on true
                    order by page.authorized_at desc, page.activation_id desc
                    """,  # noqa: S608 - where_clause contains only closed internal fragments.
                    (*filter_parameters, limit, offset),
                )
                rows, total = _page_rows_and_total(await cursor.fetchall())
        except Error as error:
            _raise_activation_database_error(error)

        return [operational_paper_session_activation_from_row(row) for row in rows], total

    async def get_current_for_materialization(
        self,
        materialization_id: UUID,
    ) -> OperationalPaperSessionActivation | None:
        """Return the unique currently authorized grant for a materialization."""

        materialization_id = _require_uuid(materialization_id)
        try:
            async with self._database.transaction() as connection:
                row = await _current_materialization_activation_row(
                    connection,
                    materialization_id,
                )
        except Error as error:
            _raise_activation_database_error(error)
        return None if row is None else operational_paper_session_activation_from_row(row)

    async def get_by_actor_idempotency(
        self,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperSessionActivation | None:
        """Return the historical row bound to one actor-scoped request key."""

        actor_id = _require_uuid(actor_id)
        idempotency_key = validate_operational_paper_session_activation_idempotency_key(
            idempotency_key
        )
        try:
            async with self._database.transaction() as connection:
                row = await _actor_idempotency_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
        except Error as error:
            _raise_activation_database_error(error)
        return None if row is None else operational_paper_session_activation_from_row(row)

    async def create(
        self,
        specification: OperationalPaperSessionActivationSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> OperationalPaperSessionActivation:
        """Create a grant or replay the actor-scoped historical request."""

        specification = _canonical_specification(specification)
        actor_id = _require_uuid(actor_id)
        idempotency_key = validate_operational_paper_session_activation_idempotency_key(
            idempotency_key
        )
        now = _now(now)
        intent = OperationalPaperSessionActivationCreateIntent(
            materialization_id=specification.materialization_id,
            materialization_checksum=specification.materialization_checksum,
        )
        fingerprint = operational_paper_session_activation_create_intent_fingerprint(intent)
        insert_attempted = False

        try:
            async with self._database.transaction() as connection:
                existing = await _actor_idempotency_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    return self._replay_row(existing, fingerprint)

                candidate = authorize_operational_paper_session_activation(
                    activation_id=uuid4(),
                    specification=specification,
                    authorized_by=actor_id,
                    authorized_at=now,
                    create_idempotency_key=idempotency_key,
                    create_intent_fingerprint=fingerprint,
                )
                insert_attempted = True
                cursor = await connection.execute(
                    f"""
                    insert into public.operational_paper_session_activations (
                        activation_id,
                        schema_version,
                        activation_contract_version,
                        state,
                        record_version,
                        materialization_id,
                        materialization_checksum,
                        authorization_id,
                        authorization_checksum,
                        profile_id,
                        profile_approved_revision,
                        profile_specification_checksum,
                        mandate_id,
                        mandate_approved_revision,
                        mandate_specification_checksum,
                        simulation_id,
                        session_id,
                        config_checksum,
                        activation_checksum,
                        authorized_by,
                        authorized_at,
                        revoked_by,
                        revoked_at,
                        create_idempotency_key,
                        create_intent_fingerprint
                    )
                    values (
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    )
                    returning {_ACTIVATION_COLUMNS}
                    """,
                    (
                        candidate.activation_id,
                        candidate.schema_version,
                        candidate.activation_contract_version,
                        candidate.state.value,
                        candidate.record_version,
                        candidate.materialization_id,
                        candidate.materialization_checksum,
                        candidate.authorization_binding.authorization_id,
                        candidate.authorization_binding.authorization_checksum,
                        candidate.profile_binding.profile_id,
                        candidate.profile_binding.approved_revision,
                        candidate.profile_binding.specification_checksum,
                        candidate.mandate_binding.mandate_id,
                        candidate.mandate_binding.approved_revision,
                        candidate.mandate_binding.specification_checksum,
                        candidate.simulation_id,
                        candidate.session_id,
                        candidate.config_checksum,
                        candidate.activation_checksum,
                        candidate.authorized_by,
                        candidate.authorized_at,
                        candidate.revoked_by,
                        candidate.revoked_at,
                        candidate.create_idempotency_key,
                        candidate.create_intent_fingerprint,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise PersistenceError()
                return operational_paper_session_activation_from_row(row)
        except Error as error:
            if not insert_attempted:
                _raise_activation_database_error(error)
            return await self._recover_create_error(
                original_error=error,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                materialization_id=specification.materialization_id,
            )

    async def _recover_create_error(
        self,
        *,
        original_error: Error,
        actor_id: UUID,
        idempotency_key: str,
        fingerprint: str,
        materialization_id: UUID,
    ) -> OperationalPaperSessionActivation:
        """Resolve a post-rollback race with idempotency-first precedence."""

        try:
            async with self._database.transaction() as connection:
                idempotent = await _actor_idempotency_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
                if idempotent is not None:
                    return self._replay_row(idempotent, fingerprint)

                current = await _current_materialization_activation_row(
                    connection,
                    materialization_id,
                )
                if current is not None:
                    operational_paper_session_activation_from_row(current)
                    raise OperationalPaperSessionActivationCurrentGrantConflictError()
        except Error as recovery_error:
            _raise_activation_database_error(recovery_error)

        _raise_activation_database_error(original_error)

    @staticmethod
    def _replay_row(
        row: Mapping[str, object],
        fingerprint: str,
    ) -> OperationalPaperSessionActivation:
        activation = operational_paper_session_activation_from_row(row)
        if activation.create_intent_fingerprint != fingerprint:
            raise OperationalPaperSessionActivationIdempotencyConflictError()
        return activation

    async def revoke(
        self,
        activation_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionActivation:
        """Revoke one grant while locking only its activation row."""

        activation_id = _require_uuid(activation_id)
        expected_record_version = _expected_record_version(expected_record_version)
        actor_id = _require_uuid(actor_id)
        now = _now(now)

        try:
            async with self._database.transaction() as connection:
                row = await _locked_activation_row(connection, activation_id)
                if row is None:
                    raise OperationalPaperSessionActivationNotFoundError()
                current = operational_paper_session_activation_from_row(row)

                if current.state is OperationalPaperSessionActivationState.REVOKED:
                    if (
                        current.revoked_by == actor_id
                        and current.record_version == expected_record_version + 1
                    ):
                        return current
                    raise OperationalPaperSessionActivationStateTransitionConflictError()

                if current.record_version != expected_record_version:
                    raise OperationalPaperSessionActivationRecordVersionConflictError()
                if current.state is not OperationalPaperSessionActivationState.AUTHORIZED:
                    raise OperationalPaperSessionActivationStateTransitionConflictError()
                if now < current.authorized_at:
                    raise OperationalPaperSessionActivationStateTransitionConflictError()

                revoked = revoke_operational_paper_session_activation(
                    current,
                    revoked_by=actor_id,
                    revoked_at=now,
                )
                cursor = await connection.execute(
                    f"""
                    update public.operational_paper_session_activations
                    set state = %s,
                        record_version = %s,
                        revoked_by = %s,
                        revoked_at = %s
                    where activation_id = %s
                      and state = 'AUTHORIZED'
                      and record_version = %s
                    returning {_ACTIVATION_COLUMNS}
                    """,
                    (
                        revoked.state.value,
                        revoked.record_version,
                        revoked.revoked_by,
                        revoked.revoked_at,
                        current.activation_id,
                        expected_record_version,
                    ),
                )
                updated = await cursor.fetchone()
                if updated is None:
                    raise PersistenceError()
                return operational_paper_session_activation_from_row(updated)
        except Error as error:
            _raise_activation_database_error(error)
