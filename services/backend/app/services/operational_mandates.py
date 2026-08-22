"""Transport-independent operational-mandate application service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.operational_mandates import (
    OperationalMandate,
    OperationalMandateRevision,
    OperationalMandateSpecification,
    OperationalMandateState,
)
from app.operational_mandates.errors import OperationalMandateNotFoundError
from app.repositories.operational_mandates import PostgresOperationalMandateRepository

OperationalMandateClock = Callable[[], datetime]
OperationalMandateCurrent = tuple[OperationalMandate, OperationalMandateRevision]
OperationalMandatePage = tuple[list[OperationalMandateCurrent], int]
OperationalMandateRevisionPage = tuple[list[OperationalMandateRevision], int]


class OperationalMandateService:
    """Coordinate bounded mandate use cases without transport concerns."""

    def __init__(
        self,
        *,
        repository: PostgresOperationalMandateRepository,
        clock: OperationalMandateClock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalMandateState | None = None,
    ) -> OperationalMandatePage:
        """Return the repository's bounded current-mandate catalog."""

        return await self._repository.list_current(
            limit=limit,
            offset=offset,
            state=state,
        )

    async def get(self, mandate_id: UUID) -> OperationalMandateCurrent:
        """Return one mandate with its exact current immutable revision."""

        current = await self._repository.get_current(mandate_id)
        if current is None:
            raise OperationalMandateNotFoundError()
        return current

    async def list_revisions(
        self,
        mandate_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> OperationalMandateRevisionPage:
        """Return one mandate's bounded immutable revision history."""

        return await self._repository.list_revisions(
            mandate_id,
            limit=limit,
            offset=offset,
        )

    async def get_revision(
        self,
        mandate_id: UUID,
        revision: int,
    ) -> OperationalMandateRevision:
        """Return one exact immutable revision or raise stable not-found."""

        result = await self._repository.get_revision(mandate_id, revision)
        if result is None:
            raise OperationalMandateNotFoundError()
        return result

    async def create(
        self,
        specification: OperationalMandateSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalMandateCurrent:
        """Create or replay one draft intent at one authoritative instant."""

        now = self._clock()
        return await self._repository.create(
            specification,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def replace_draft(
        self,
        mandate_id: UUID,
        specification: OperationalMandateSpecification,
        *,
        expected_revision: int,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalMandateCurrent:
        """Replace one draft while preserving explicit concurrency tokens."""

        now = self._clock()
        return await self._repository.replace_draft(
            mandate_id,
            specification,
            expected_revision=expected_revision,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
        )

    async def approve(
        self,
        mandate_id: UUID,
        *,
        expected_revision: int,
        expected_checksum: str,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalMandate:
        """Approve one exact draft revision without external validation."""

        now = self._clock()
        return await self._repository.approve(
            mandate_id,
            expected_revision=expected_revision,
            expected_checksum=expected_checksum,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
        )

    async def archive(
        self,
        mandate_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalMandate:
        """Archive one mandate without reinterpreting lifecycle semantics."""

        now = self._clock()
        return await self._repository.archive(
            mandate_id,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
        )
