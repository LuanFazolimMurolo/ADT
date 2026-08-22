"""Application-service tests for operational-mandate orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from app.domain.errors import PersistenceError
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.operational_mandates import (
    OPERATIONAL_MANDATE_SPEC_SCHEMA_VERSION,
    OperationalMandate,
    OperationalMandateInstrument,
    OperationalMandateRevision,
    OperationalMandateSpecification,
    OperationalMandateState,
    operational_mandate_create_request_fingerprint,
    operational_mandate_specification_checksum,
)
from app.operational_mandates.errors import (
    OperationalMandateChecksumMismatchError,
    OperationalMandateIdempotencyConflictError,
    OperationalMandateNotFoundError,
    OperationalMandateRecordVersionConflictError,
    OperationalMandateRevisionConflictError,
    OperationalMandateStateTransitionConflictError,
)
from app.repositories.operational_mandates import PostgresOperationalMandateRepository
from app.services import OperationalMandateService as ExportedOperationalMandateService
from app.services.operational_mandates import OperationalMandateService

MANDATE_ID = UUID("10000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("20000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 8, 22, 18, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=1)
IDEMPOTENCY_KEY = "gate-2e-create"


def _specification() -> OperationalMandateSpecification:
    return OperationalMandateSpecification(
        schema_version=OPERATIONAL_MANDATE_SPEC_SCHEMA_VERSION,
        name="Gate 2E mandate",
        description="Transport-independent orchestration",
        instruments=(
            OperationalMandateInstrument(
                exchange=Exchange.BINANCE,
                market_type=MarketType.SPOT,
                pair=TradingPair("BTC", "USDT"),
            ),
        ),
    )


SPECIFICATION = _specification()
REVISION = OperationalMandateRevision(
    mandate_id=MANDATE_ID,
    revision=1,
    specification=SPECIFICATION,
    specification_checksum=operational_mandate_specification_checksum(SPECIFICATION),
    created_by=ACTOR_ID,
    created_at=NOW,
)
DRAFT = OperationalMandate(
    mandate_id=MANDATE_ID,
    state=OperationalMandateState.DRAFT,
    current_revision=1,
    record_version=1,
    approved_revision=None,
    approved_checksum=None,
    created_by=ACTOR_ID,
    created_at=NOW,
    approved_by=None,
    approved_at=None,
    archived_by=None,
    archived_at=None,
    create_idempotency_key=IDEMPOTENCY_KEY,
    create_request_fingerprint=operational_mandate_create_request_fingerprint(SPECIFICATION),
)
CURRENT = (DRAFT, REVISION)
APPROVED = replace(
    DRAFT,
    state=OperationalMandateState.APPROVED,
    record_version=2,
    approved_revision=1,
    approved_checksum=REVISION.specification_checksum,
    approved_by=ACTOR_ID,
    approved_at=LATER,
)
ARCHIVED = replace(
    APPROVED,
    state=OperationalMandateState.ARCHIVED,
    record_version=3,
    archived_by=ACTOR_ID,
    archived_at=LATER + timedelta(minutes=1),
)


class RecordingClock:
    def __init__(self, value: datetime = LATER) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.value


class RecordingRepository(PostgresOperationalMandateRepository):
    def __init__(self) -> None:
        self.current_result: tuple[OperationalMandate, OperationalMandateRevision] | None = CURRENT
        self.revision_result: OperationalMandateRevision | None = REVISION
        self.current_page_result = ([CURRENT], 1)
        self.revision_page_result = ([REVISION], 1)
        self.create_result = CURRENT
        self.replace_result = CURRENT
        self.approve_result = APPROVED
        self.archive_result = ARCHIVED
        self.failures: dict[str, Exception] = {}

        self.list_current_calls: list[tuple[int, int, OperationalMandateState | None]] = []
        self.get_current_calls: list[UUID] = []
        self.list_revision_calls: list[tuple[UUID, int, int]] = []
        self.get_revision_calls: list[tuple[UUID, int]] = []
        self.create_calls: list[tuple[OperationalMandateSpecification, UUID, str, datetime]] = []
        self.replace_calls: list[
            tuple[UUID, OperationalMandateSpecification, int, int, UUID, datetime]
        ] = []
        self.approve_calls: list[tuple[UUID, int, str, int, UUID, datetime]] = []
        self.archive_calls: list[tuple[UUID, int, UUID, datetime]] = []

    def _raise_failure(self, method: str) -> None:
        failure = self.failures.get(method)
        if failure is not None:
            raise failure

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
        self._raise_failure("list_current")
        self.list_current_calls.append((limit, offset, state))
        return self.current_page_result

    async def get_current(
        self,
        mandate_id: UUID,
    ) -> tuple[OperationalMandate, OperationalMandateRevision] | None:
        self._raise_failure("get_current")
        self.get_current_calls.append(mandate_id)
        return self.current_result

    async def list_revisions(
        self,
        mandate_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[OperationalMandateRevision], int]:
        self._raise_failure("list_revisions")
        self.list_revision_calls.append((mandate_id, limit, offset))
        return self.revision_page_result

    async def get_revision(
        self,
        mandate_id: UUID,
        revision: int,
    ) -> OperationalMandateRevision | None:
        self._raise_failure("get_revision")
        self.get_revision_calls.append((mandate_id, revision))
        return self.revision_result

    async def create(
        self,
        specification: OperationalMandateSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> tuple[OperationalMandate, OperationalMandateRevision]:
        self._raise_failure("create")
        self.create_calls.append((specification, actor_id, idempotency_key, now))
        return self.create_result

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
        self._raise_failure("replace_draft")
        self.replace_calls.append(
            (
                mandate_id,
                specification,
                expected_revision,
                expected_record_version,
                actor_id,
                now,
            )
        )
        return self.replace_result

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
        self._raise_failure("approve")
        self.approve_calls.append(
            (
                mandate_id,
                expected_revision,
                expected_checksum,
                expected_record_version,
                actor_id,
                now,
            )
        )
        return self.approve_result

    async def archive(
        self,
        mandate_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalMandate:
        self._raise_failure("archive")
        self.archive_calls.append((mandate_id, expected_record_version, actor_id, now))
        return self.archive_result


def _service() -> tuple[OperationalMandateService, RecordingRepository, RecordingClock]:
    repository = RecordingRepository()
    clock = RecordingClock()
    return (
        OperationalMandateService(repository=repository, clock=clock),
        repository,
        clock,
    )


def test_package_exports_service() -> None:
    assert ExportedOperationalMandateService is OperationalMandateService


async def test_queries_forward_exact_repository_contract_without_reading_clock() -> None:
    service, repository, clock = _service()

    page = await service.list(
        limit=17,
        offset=4,
        state=OperationalMandateState.DRAFT,
    )
    current = await service.get(MANDATE_ID)
    history = await service.list_revisions(MANDATE_ID, limit=9, offset=2)
    revision = await service.get_revision(MANDATE_ID, 1)

    assert page == repository.current_page_result
    assert current is CURRENT
    assert history == repository.revision_page_result
    assert revision is REVISION
    assert repository.list_current_calls == [(17, 4, OperationalMandateState.DRAFT)]
    assert repository.get_current_calls == [MANDATE_ID]
    assert repository.list_revision_calls == [(MANDATE_ID, 9, 2)]
    assert repository.get_revision_calls == [(MANDATE_ID, 1)]
    assert clock.calls == 0


async def test_missing_current_or_exact_revision_raises_stable_not_found() -> None:
    service, repository, _clock = _service()
    repository.current_result = None
    repository.revision_result = None

    with pytest.raises(OperationalMandateNotFoundError):
        await service.get(MANDATE_ID)
    with pytest.raises(OperationalMandateNotFoundError):
        await service.get_revision(MANDATE_ID, 99)


async def test_create_preserves_actor_key_specification_and_one_clock_value() -> None:
    service, repository, clock = _service()

    result = await service.create(
        SPECIFICATION,
        actor_id=ACTOR_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert result is CURRENT
    assert repository.create_calls == [(SPECIFICATION, ACTOR_ID, IDEMPOTENCY_KEY, LATER)]
    assert clock.calls == 1


async def test_replace_preserves_tokens_and_passes_semantic_noop_through() -> None:
    service, repository, clock = _service()

    result = await service.replace_draft(
        MANDATE_ID,
        SPECIFICATION,
        expected_revision=1,
        expected_record_version=1,
        actor_id=ACTOR_ID,
    )

    assert result is CURRENT
    assert repository.replace_calls == [(MANDATE_ID, SPECIFICATION, 1, 1, ACTOR_ID, LATER)]
    assert clock.calls == 1


async def test_approve_preserves_checksum_tokens_actor_and_one_clock_value() -> None:
    service, repository, clock = _service()

    result = await service.approve(
        MANDATE_ID,
        expected_revision=1,
        expected_checksum=REVISION.specification_checksum,
        expected_record_version=1,
        actor_id=ACTOR_ID,
    )

    assert result is APPROVED
    assert repository.approve_calls == [
        (
            MANDATE_ID,
            1,
            REVISION.specification_checksum,
            1,
            ACTOR_ID,
            LATER,
        )
    ]
    assert clock.calls == 1


async def test_archive_preserves_record_version_actor_and_one_clock_value() -> None:
    service, repository, clock = _service()

    result = await service.archive(
        MANDATE_ID,
        expected_record_version=2,
        actor_id=ACTOR_ID,
    )

    assert result is ARCHIVED
    assert repository.archive_calls == [(MANDATE_ID, 2, ACTOR_ID, LATER)]
    assert clock.calls == 1


async def test_service_does_not_coerce_repository_inputs() -> None:
    service, repository, _clock = _service()
    raw_mandate_id = "10000000-0000-4000-8000-000000000001"
    raw_state = "DRAFT"
    raw_limit = "17"
    raw_offset = "4"
    raw_revision = "1"
    raw_record_version = "2"
    raw_actor_id = "20000000-0000-4000-8000-000000000002"

    await service.list(
        limit=cast(int, raw_limit),
        offset=cast(int, raw_offset),
        state=cast(OperationalMandateState, raw_state),
    )
    await service.get(cast(UUID, raw_mandate_id))
    await service.approve(
        cast(UUID, raw_mandate_id),
        expected_revision=cast(int, raw_revision),
        expected_checksum=REVISION.specification_checksum,
        expected_record_version=cast(int, raw_record_version),
        actor_id=cast(UUID, raw_actor_id),
    )

    assert cast(tuple[object, object, object], repository.list_current_calls[-1]) == (
        raw_limit,
        raw_offset,
        raw_state,
    )
    assert cast(object, repository.get_current_calls[-1]) == raw_mandate_id
    assert cast(tuple[object, ...], repository.approve_calls[-1][0:5]) == (
        raw_mandate_id,
        raw_revision,
        REVISION.specification_checksum,
        raw_record_version,
        raw_actor_id,
    )


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("create", OperationalMandateIdempotencyConflictError()),
        ("replace_draft", OperationalMandateRevisionConflictError()),
        ("replace_draft", OperationalMandateRecordVersionConflictError()),
        ("approve", OperationalMandateChecksumMismatchError()),
        ("archive", OperationalMandateStateTransitionConflictError()),
        ("list_revisions", OperationalMandateNotFoundError()),
        ("list_current", PersistenceError()),
    ],
)
async def test_repository_errors_propagate_unchanged(
    method: str,
    error: Exception,
) -> None:
    service, repository, _clock = _service()
    repository.failures[method] = error

    with pytest.raises(type(error)) as caught:
        if method == "create":
            await service.create(
                SPECIFICATION,
                actor_id=ACTOR_ID,
                idempotency_key=IDEMPOTENCY_KEY,
            )
        elif method == "replace_draft":
            await service.replace_draft(
                MANDATE_ID,
                SPECIFICATION,
                expected_revision=1,
                expected_record_version=1,
                actor_id=ACTOR_ID,
            )
        elif method == "approve":
            await service.approve(
                MANDATE_ID,
                expected_revision=1,
                expected_checksum=REVISION.specification_checksum,
                expected_record_version=1,
                actor_id=ACTOR_ID,
            )
        elif method == "archive":
            await service.archive(
                MANDATE_ID,
                expected_record_version=1,
                actor_id=ACTOR_ID,
            )
        elif method == "list_revisions":
            await service.list_revisions(MANDATE_ID, limit=20, offset=0)
        else:
            await service.list(limit=20, offset=0)

    assert caught.value is error
