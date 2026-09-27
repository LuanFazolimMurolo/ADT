"""PostgreSQL persistence for human PAPER recommendation approval evidence."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import NoReturn
from uuid import UUID

from psycopg import Error
from psycopg.errors import UniqueViolation

from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import PersistenceError
from app.paper_recommendation_approvals import (
    InvalidPaperRecommendationApprovalError,
    PaperRecommendationApproval,
    PaperRecommendationApprovalIntent,
    PaperRecommendationApprovalState,
    PaperRecommendationApprovalStateTransitionError,
    approve_paper_recommendation,
    paper_recommendation_approval_intent_fingerprint,
    revoke_paper_recommendation_approval,
)
from app.services.paper_recommendation_approvals import (
    PaperRecommendationApprovalIdempotencyConflictError,
    PaperRecommendationApprovalNotFoundError,
    PaperRecommendationApprovalRecordVersionConflictError,
)

_TABLE = "public.paper_recommendation_approvals"
_IDEMPOTENCY_CONSTRAINT = "paper_rec_approval_actor_idempotency_key"


def paper_recommendation_approval_from_row(
    row: Mapping[str, object],
) -> PaperRecommendationApproval:
    """Reconstruct and revalidate persisted approval evidence."""

    try:
        return PaperRecommendationApproval(
            approval_id=_uuid(row["approval_id"]),
            schema_version=_integer(row["schema_version"]),
            state=PaperRecommendationApprovalState(_text(row["state"])),
            record_version=_integer(row["record_version"]),
            recommendation_identity=_text(row["recommendation_identity"]),
            recommendation_checksum=_text(row["recommendation_checksum"]),
            candidate_identity=_text(row["candidate_identity"]),
            candidate_checksum=_text(row["candidate_checksum"]),
            configuration_checksum=_text(row["configuration_checksum"]),
            parameters_checksum=_text(row["parameters_checksum"]),
            approval_checksum=_text(row["approval_checksum"]),
            approved_by=_uuid(row["approved_by"]),
            approved_at=_timestamp(row["approved_at"]),
            create_idempotency_key=_text(row["create_idempotency_key"]),
            create_intent_fingerprint=_text(row["create_intent_fingerprint"]),
            revoked_by=_optional_uuid(row["revoked_by"]),
            revoked_at=_optional_timestamp(row["revoked_at"]),
        )
    except (
        InvalidPaperRecommendationApprovalError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise PersistenceError() from error


async def _replay(
    connection: DatabaseConnection,
    *,
    actor_id: UUID,
    idempotency_key: str,
    fingerprint: str,
) -> PaperRecommendationApproval | None:
    cursor = await connection.execute(
        f"select * from {_TABLE} where approved_by = %s and create_idempotency_key = %s",
        (actor_id, idempotency_key),
    )
    row = await cursor.fetchone()

    if row is None:
        return None

    approval = paper_recommendation_approval_from_row(row)

    if approval.create_intent_fingerprint != fingerprint:
        raise PaperRecommendationApprovalIdempotencyConflictError()

    return approval


def _raise_database_error(error: Error) -> NoReturn:
    if error.diag.constraint_name == _IDEMPOTENCY_CONSTRAINT:
        raise PaperRecommendationApprovalIdempotencyConflictError() from error

    raise_domain_error(error)


class PostgresPaperRecommendationApprovalRepository:
    """Persist approval evidence without granting operational PAPER authority."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(
        self,
        approval_id: UUID,
    ) -> PaperRecommendationApproval | None:
        if type(approval_id) is not UUID:
            raise PaperRecommendationApprovalNotFoundError()

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"select * from {_TABLE} where approval_id = %s",
                    (approval_id,),
                )
                row = await cursor.fetchone()
        except Error as error:
            _raise_database_error(error)

        if row is None:
            return None

        return paper_recommendation_approval_from_row(row)

    async def create(
        self,
        intent: PaperRecommendationApprovalIntent,
        *,
        approval_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> PaperRecommendationApproval:
        proposed = approve_paper_recommendation(
            intent,
            approval_id=approval_id,
            actor_id=actor_id,
            approved_at=now,
            idempotency_key=idempotency_key,
        )

        fingerprint = paper_recommendation_approval_intent_fingerprint(intent)

        try:
            async with self._database.transaction() as connection:
                replay = await _replay(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return replay

                cursor = await connection.execute(
                    f"""
                    insert into {_TABLE} (
                        approval_id,
                        schema_version,
                        state,
                        record_version,
                        recommendation_identity,
                        recommendation_checksum,
                        candidate_identity,
                        candidate_checksum,
                        configuration_checksum,
                        parameters_checksum,
                        approval_checksum,
                        approved_by,
                        approved_at,
                        create_idempotency_key,
                        create_intent_fingerprint,
                        revoked_by,
                        revoked_at
                    )
                    values (
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s,
                        %s, %s,
                        %s, %s,
                        %s, %s
                    )
                    returning *
                    """,
                    (
                        proposed.approval_id,
                        proposed.schema_version,
                        proposed.state.value,
                        proposed.record_version,
                        proposed.recommendation_identity,
                        proposed.recommendation_checksum,
                        proposed.candidate_identity,
                        proposed.candidate_checksum,
                        proposed.configuration_checksum,
                        proposed.parameters_checksum,
                        proposed.approval_checksum,
                        proposed.approved_by,
                        proposed.approved_at,
                        proposed.create_idempotency_key,
                        proposed.create_intent_fingerprint,
                        proposed.revoked_by,
                        proposed.revoked_at,
                    ),
                )
                row = await cursor.fetchone()

        except UniqueViolation as error:
            if error.diag.constraint_name != _IDEMPOTENCY_CONSTRAINT:
                _raise_database_error(error)

            try:
                async with self._database.transaction() as connection:
                    replay = await _replay(
                        connection,
                        actor_id=actor_id,
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                    )
            except Error as replay_error:
                _raise_database_error(replay_error)

            if replay is None:
                raise PersistenceError() from error

            return replay

        except Error as error:
            _raise_database_error(error)

        if row is None:
            raise PersistenceError()

        return paper_recommendation_approval_from_row(row)

    async def revoke(
        self,
        approval_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> PaperRecommendationApproval:
        if type(approval_id) is not UUID:
            raise PaperRecommendationApprovalNotFoundError()

        if type(expected_record_version) is not int or expected_record_version < 1:
            raise PaperRecommendationApprovalRecordVersionConflictError()

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"select * from {_TABLE} where approval_id = %s for update",
                    (approval_id,),
                )
                row = await cursor.fetchone()

                if row is None:
                    raise PaperRecommendationApprovalNotFoundError()

                current = paper_recommendation_approval_from_row(row)

                if current.record_version != expected_record_version:
                    raise PaperRecommendationApprovalRecordVersionConflictError()

                revoked = revoke_paper_recommendation_approval(
                    current,
                    actor_id=actor_id,
                    revoked_at=now,
                )

                cursor = await connection.execute(
                    f"""
                    update {_TABLE}
                    set
                        state = %s,
                        record_version = %s,
                        revoked_by = %s,
                        revoked_at = %s
                    where approval_id = %s
                      and record_version = %s
                      and state = 'APPROVED'
                    returning *
                    """,
                    (
                        revoked.state.value,
                        revoked.record_version,
                        revoked.revoked_by,
                        revoked.revoked_at,
                        approval_id,
                        expected_record_version,
                    ),
                )
                updated = await cursor.fetchone()

        except (
            PaperRecommendationApprovalNotFoundError,
            PaperRecommendationApprovalRecordVersionConflictError,
            PaperRecommendationApprovalStateTransitionError,
        ):
            raise
        except Error as error:
            _raise_database_error(error)

        if updated is None:
            raise PaperRecommendationApprovalRecordVersionConflictError()

        return paper_recommendation_approval_from_row(updated)


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError
    return value


def _uuid(value: object) -> UUID:
    if type(value) is UUID:
        return value

    if type(value) is str:
        return UUID(value)

    raise ValueError


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    return _uuid(value)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError
    return value


def _optional_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value)
