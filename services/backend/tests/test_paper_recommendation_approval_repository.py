"""Remote-free PostgreSQL repository contract tests for ML PAPER approvals."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from app.database.pool import Database
from app.domain.errors import PersistenceError
from app.paper_recommendation_approvals import (
    PaperRecommendationApprovalIntent,
    PaperRecommendationApprovalState,
    approve_paper_recommendation,
    revoke_paper_recommendation_approval,
)
from app.repositories.paper_recommendation_approvals import (
    PostgresPaperRecommendationApprovalRepository,
    paper_recommendation_approval_from_row,
)
from app.services.paper_recommendation_approvals import (
    PaperRecommendationApprovalNotFoundError,
    PaperRecommendationApprovalRecordVersionConflictError,
)

APPROVAL_ID = UUID("10000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("20000000-0000-4000-8000-000000000002")
OTHER_ACTOR_ID = UUID("30000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 9, 27, 8, tzinfo=UTC)
KEY = "ml-paper-approval:repository:1"


def _intent() -> PaperRecommendationApprovalIntent:
    return PaperRecommendationApprovalIntent(
        recommendation_identity="a" * 64,
        recommendation_checksum="b" * 64,
        candidate_identity="c" * 64,
        candidate_checksum="d" * 64,
        configuration_checksum="e" * 64,
        parameters_checksum="f" * 64,
    )


def _approved():
    return approve_paper_recommendation(
        _intent(),
        approval_id=APPROVAL_ID,
        actor_id=ACTOR_ID,
        approved_at=NOW,
        idempotency_key=KEY,
    )


def _row(value=None) -> dict[str, object]:
    approval = value or _approved()

    return {
        "approval_id": approval.approval_id,
        "schema_version": approval.schema_version,
        "state": approval.state.value,
        "record_version": approval.record_version,
        "recommendation_identity": approval.recommendation_identity,
        "recommendation_checksum": approval.recommendation_checksum,
        "candidate_identity": approval.candidate_identity,
        "candidate_checksum": approval.candidate_checksum,
        "configuration_checksum": approval.configuration_checksum,
        "parameters_checksum": approval.parameters_checksum,
        "approval_checksum": approval.approval_checksum,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at,
        "create_idempotency_key": approval.create_idempotency_key,
        "create_intent_fingerprint": approval.create_intent_fingerprint,
        "revoked_by": approval.revoked_by,
        "revoked_at": approval.revoked_at,
    }


class _Cursor:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row


class _Connection:
    def __init__(
        self,
        script: list[tuple[str, dict[str, object] | None]],
    ) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, object]] = []

    async def execute(
        self,
        query: object,
        params: object = None,
    ) -> _Cursor:
        normalized = " ".join(str(query).split()).lower()
        self.calls.append((normalized, params))

        if not self.script:
            raise AssertionError(f"unexpected query: {normalized}")

        expected, row = self.script.pop(0)
        assert expected in normalized

        return _Cursor(row)


class _Database:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.transactions = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_Connection]:
        self.transactions += 1
        yield self.connection


def _repository(
    script: list[tuple[str, dict[str, object] | None]],
) -> tuple[
    PostgresPaperRecommendationApprovalRepository,
    _Connection,
]:
    connection = _Connection(script)
    database = cast(Database, _Database(connection))
    return PostgresPaperRecommendationApprovalRepository(database), connection


def test_from_row_reconstructs_valid_approval() -> None:
    reconstructed = paper_recommendation_approval_from_row(_row())

    assert reconstructed == _approved()


def test_from_row_rejects_corrupt_persisted_evidence() -> None:
    row = _row()
    row["candidate_checksum"] = "0" * 64

    with pytest.raises(PersistenceError):
        paper_recommendation_approval_from_row(row)


@pytest.mark.asyncio
async def test_get_returns_none_when_missing() -> None:
    repository, connection = _repository(
        [
            ("where approval_id = %s", None),
        ]
    )

    assert await repository.get(APPROVAL_ID) is None
    assert len(connection.calls) == 1


@pytest.mark.asyncio
async def test_get_reconstructs_stored_approval() -> None:
    repository, _connection = _repository(
        [
            ("where approval_id = %s", _row()),
        ]
    )

    assert await repository.get(APPROVAL_ID) == _approved()


@pytest.mark.asyncio
async def test_create_inserts_exact_domain_evidence() -> None:
    repository, connection = _repository(
        [
            (
                "where approved_by = %s and create_idempotency_key = %s",
                None,
            ),
            ("insert into public.paper_recommendation_approvals", _row()),
        ]
    )

    created = await repository.create(
        _intent(),
        approval_id=APPROVAL_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
        now=NOW,
    )

    assert created == _approved()
    assert len(connection.calls) == 2

    insert_params = connection.calls[1][1]
    assert isinstance(insert_params, tuple)
    assert insert_params[0] == APPROVAL_ID
    assert insert_params[2] == "APPROVED"
    assert insert_params[3] == 1
    assert insert_params[11] == ACTOR_ID
    assert insert_params[13] == KEY


@pytest.mark.asyncio
async def test_create_replays_same_actor_key_and_fingerprint() -> None:
    repository, connection = _repository(
        [
            (
                "where approved_by = %s and create_idempotency_key = %s",
                _row(),
            ),
        ]
    )

    replay = await repository.create(
        _intent(),
        approval_id=UUID("40000000-0000-4000-8000-000000000004"),
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
        now=NOW + timedelta(minutes=1),
    )

    assert replay == _approved()
    assert len(connection.calls) == 1


@pytest.mark.asyncio
async def test_create_rejects_same_key_with_different_fingerprint() -> None:
    existing = _row()
    existing["create_intent_fingerprint"] = "0" * 64

    repository, _connection = _repository(
        [
            (
                "where approved_by = %s and create_idempotency_key = %s",
                existing,
            ),
        ]
    )

    with pytest.raises(PersistenceError):
        await repository.create(
            _intent(),
            approval_id=APPROVAL_ID,
            actor_id=ACTOR_ID,
            idempotency_key=KEY,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_revoke_requires_existing_approval() -> None:
    repository, _connection = _repository(
        [
            ("where approval_id = %s for update", None),
        ]
    )

    with pytest.raises(PaperRecommendationApprovalNotFoundError):
        await repository.revoke(
            APPROVAL_ID,
            expected_record_version=1,
            actor_id=OTHER_ACTOR_ID,
            now=NOW + timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_revoke_rejects_stale_record_version() -> None:
    repository, connection = _repository(
        [
            ("where approval_id = %s for update", _row()),
        ]
    )

    with pytest.raises(PaperRecommendationApprovalRecordVersionConflictError):
        await repository.revoke(
            APPROVAL_ID,
            expected_record_version=2,
            actor_id=OTHER_ACTOR_ID,
            now=NOW + timedelta(minutes=1),
        )

    assert len(connection.calls) == 1


@pytest.mark.asyncio
async def test_revoke_updates_only_lifecycle_columns() -> None:
    approved = _approved()
    revoked = revoke_paper_recommendation_approval(
        approved,
        actor_id=OTHER_ACTOR_ID,
        revoked_at=NOW + timedelta(minutes=1),
    )

    repository, connection = _repository(
        [
            ("where approval_id = %s for update", _row(approved)),
            (
                "update public.paper_recommendation_approvals set",
                _row(revoked),
            ),
        ]
    )

    result = await repository.revoke(
        APPROVAL_ID,
        expected_record_version=1,
        actor_id=OTHER_ACTOR_ID,
        now=NOW + timedelta(minutes=1),
    )

    assert result.state is PaperRecommendationApprovalState.REVOKED
    assert result.record_version == 2
    assert result.revoked_by == OTHER_ACTOR_ID

    update_query, update_params = connection.calls[1]
    assert "recommendation_identity =" not in update_query
    assert "candidate_identity =" not in update_query
    assert "approval_checksum =" not in update_query

    assert isinstance(update_params, tuple)
    assert update_params[0] == "REVOKED"
    assert update_params[1] == 2
    assert update_params[4] == APPROVAL_ID
    assert update_params[5] == 1


@pytest.mark.asyncio
async def test_revoke_rejects_non_integer_expected_version_before_db() -> None:
    repository, connection = _repository([])

    with pytest.raises(PaperRecommendationApprovalRecordVersionConflictError):
        await repository.revoke(
            APPROVAL_ID,
            expected_record_version=True,
            actor_id=OTHER_ACTOR_ID,
            now=NOW,
        )

    assert connection.calls == []
