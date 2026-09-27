"""Service/repository semantics for human ML recommendation approval."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from app.ml.recommendations import MLStrategyRecommendation
from app.paper_recommendation_approval_memory import (
    InMemoryPaperRecommendationApprovalRepository,
)
from app.paper_recommendation_approvals import (
    PaperRecommendationApprovalState,
)
from app.services.paper_recommendation_approvals import (
    PaperRecommendationApprovalIdempotencyConflictError,
    PaperRecommendationApprovalNotFoundError,
    PaperRecommendationApprovalRecordVersionConflictError,
    PaperRecommendationApprovalService,
)

RECOMMENDATION_ID = "a" * 64
OTHER_RECOMMENDATION_ID = "1" * 64
APPROVAL_ID = UUID("10000000-0000-4000-8000-000000000001")
SECOND_APPROVAL_ID = UUID("10000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("20000000-0000-4000-8000-000000000002")
OTHER_ACTOR_ID = UUID("30000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 9, 27, 7, tzinfo=UTC)
KEY = "ml-paper-approval:service:1"


class _RecommendationSource:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, recommendation_id: str) -> MLStrategyRecommendation:
        self.calls.append(recommendation_id)

        if recommendation_id == RECOMMENDATION_ID:
            return _recommendation(
                identity=RECOMMENDATION_ID,
                checksum="b" * 64,
                candidate_identity="c" * 64,
                candidate_checksum="d" * 64,
                configuration_checksum="e" * 64,
                parameters_checksum="f" * 64,
            )

        if recommendation_id == OTHER_RECOMMENDATION_ID:
            return _recommendation(
                identity=OTHER_RECOMMENDATION_ID,
                checksum="2" * 64,
                candidate_identity="3" * 64,
                candidate_checksum="4" * 64,
                configuration_checksum="5" * 64,
                parameters_checksum="6" * 64,
            )

        raise LookupError(recommendation_id)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.value


class _UuidFactory:
    def __init__(self, *values: UUID) -> None:
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> UUID:
        self.calls += 1
        return self._values.pop(0)


def _recommendation(
    *,
    identity: str,
    checksum: str,
    candidate_identity: str,
    candidate_checksum: str,
    configuration_checksum: str,
    parameters_checksum: str,
) -> MLStrategyRecommendation:
    value = SimpleNamespace(
        identity=identity,
        checksum=checksum,
        candidate=SimpleNamespace(
            identity=candidate_identity,
            checksum=candidate_checksum,
            configuration_checksum=configuration_checksum,
            parameters_checksum=parameters_checksum,
        ),
    )
    return cast(MLStrategyRecommendation, value)


def _service(
    *,
    source: _RecommendationSource | None = None,
    repository: InMemoryPaperRecommendationApprovalRepository | None = None,
    clock: _Clock | None = None,
    uuid_factory: _UuidFactory | None = None,
) -> tuple[
    PaperRecommendationApprovalService,
    _RecommendationSource,
    InMemoryPaperRecommendationApprovalRepository,
    _Clock,
    _UuidFactory,
]:
    source = source or _RecommendationSource()
    repository = repository or InMemoryPaperRecommendationApprovalRepository()
    clock = clock or _Clock(NOW)
    uuid_factory = uuid_factory or _UuidFactory(
        APPROVAL_ID,
        SECOND_APPROVAL_ID,
    )

    return (
        PaperRecommendationApprovalService(
            recommendation_source=source,
            repository=repository,
            clock=clock,
            uuid_factory=uuid_factory,
        ),
        source,
        repository,
        clock,
        uuid_factory,
    )


@pytest.mark.asyncio
async def test_service_freezes_binding_from_server_owned_recommendation() -> None:
    service, source, _repository, clock, uuid_factory = _service()

    approval = await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )

    assert source.calls == [RECOMMENDATION_ID]
    assert clock.calls == 1
    assert uuid_factory.calls == 1

    assert approval.approval_id == APPROVAL_ID
    assert approval.recommendation_identity == RECOMMENDATION_ID
    assert approval.recommendation_checksum == "b" * 64
    assert approval.candidate_identity == "c" * 64
    assert approval.candidate_checksum == "d" * 64
    assert approval.configuration_checksum == "e" * 64
    assert approval.parameters_checksum == "f" * 64
    assert approval.approved_by == ACTOR_ID
    assert approval.state is PaperRecommendationApprovalState.APPROVED
    assert approval.record_version == 1


@pytest.mark.asyncio
async def test_same_actor_and_idempotency_key_replays_same_approval() -> None:
    service, _source, _repository, _clock, uuid_factory = _service()

    first = await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )
    second = await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )

    assert second is first
    assert second.approval_id == APPROVAL_ID

    # Service may allocate an unused candidate UUID before repository replay.
    assert uuid_factory.calls == 2


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_intent_conflicts() -> None:
    service, _source, _repository, _clock, _uuid_factory = _service()

    await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )

    with pytest.raises(PaperRecommendationApprovalIdempotencyConflictError):
        await service.approve(
            OTHER_RECOMMENDATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=KEY,
        )


@pytest.mark.asyncio
async def test_idempotency_scope_is_per_actor() -> None:
    service, _source, _repository, _clock, _uuid_factory = _service()

    first = await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )
    second = await service.approve(
        RECOMMENDATION_ID,
        actor_id=OTHER_ACTOR_ID,
        idempotency_key=KEY,
    )

    assert first.approval_id == APPROVAL_ID
    assert second.approval_id == SECOND_APPROVAL_ID
    assert first.approved_by == ACTOR_ID
    assert second.approved_by == OTHER_ACTOR_ID


@pytest.mark.asyncio
async def test_get_returns_exact_stored_approval() -> None:
    service, _source, _repository, _clock, _uuid_factory = _service()

    created = await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )

    assert await service.get(created.approval_id) is created


@pytest.mark.asyncio
async def test_get_missing_approval_raises_not_found() -> None:
    service, _source, _repository, _clock, _uuid_factory = _service()

    with pytest.raises(PaperRecommendationApprovalNotFoundError):
        await service.get(APPROVAL_ID)


@pytest.mark.asyncio
async def test_revoke_requires_exact_record_version() -> None:
    service, _source, _repository, clock, _uuid_factory = _service()

    created = await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )

    clock.value = NOW + timedelta(minutes=1)

    with pytest.raises(PaperRecommendationApprovalRecordVersionConflictError):
        await service.revoke(
            created.approval_id,
            expected_record_version=2,
            actor_id=OTHER_ACTOR_ID,
        )

    current = await service.get(created.approval_id)
    assert current.state is PaperRecommendationApprovalState.APPROVED
    assert current.record_version == 1


@pytest.mark.asyncio
async def test_revoke_advances_version_and_preserves_binding() -> None:
    service, _source, _repository, clock, _uuid_factory = _service()

    created = await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )

    clock.value = NOW + timedelta(minutes=1)

    revoked = await service.revoke(
        created.approval_id,
        expected_record_version=1,
        actor_id=OTHER_ACTOR_ID,
    )

    assert revoked.state is PaperRecommendationApprovalState.REVOKED
    assert revoked.record_version == 2
    assert revoked.revoked_by == OTHER_ACTOR_ID
    assert revoked.revoked_at == NOW + timedelta(minutes=1)

    assert revoked.recommendation_identity == created.recommendation_identity
    assert revoked.recommendation_checksum == created.recommendation_checksum
    assert revoked.candidate_identity == created.candidate_identity
    assert revoked.candidate_checksum == created.candidate_checksum
    assert revoked.configuration_checksum == created.configuration_checksum
    assert revoked.parameters_checksum == created.parameters_checksum
    assert revoked.approval_checksum == created.approval_checksum


@pytest.mark.asyncio
async def test_revoke_missing_approval_raises_not_found() -> None:
    service, _source, _repository, _clock, _uuid_factory = _service()

    with pytest.raises(PaperRecommendationApprovalNotFoundError):
        await service.revoke(
            APPROVAL_ID,
            expected_record_version=1,
            actor_id=ACTOR_ID,
        )


@pytest.mark.asyncio
async def test_revoke_rejects_non_integer_expected_version() -> None:
    service, _source, _repository, _clock, _uuid_factory = _service()

    created = await service.approve(
        RECOMMENDATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=KEY,
    )

    with pytest.raises(PaperRecommendationApprovalRecordVersionConflictError):
        await service.revoke(
            created.approval_id,
            expected_record_version=True,
            actor_id=OTHER_ACTOR_ID,
        )
