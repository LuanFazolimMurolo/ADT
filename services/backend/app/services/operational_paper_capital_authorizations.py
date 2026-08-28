"""Application service for operational paper-capital authorizations."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorization,
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationNotFoundError,
    OperationalPaperCapitalAuthorizationState,
)
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)

OperationalPaperCapitalAuthorizationClock = Callable[[], datetime]
OperationalPaperCapitalAuthorizationPage = tuple[
    list[OperationalPaperCapitalAuthorization],
    int,
]


class OperationalPaperCapitalAuthorizationService:
    """Coordinate paper-capital authorization use cases without transport concerns."""

    def __init__(
        self,
        *,
        repository: PostgresOperationalPaperCapitalAuthorizationRepository,
        clock: OperationalPaperCapitalAuthorizationClock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperCapitalAuthorizationState | None = None,
    ) -> OperationalPaperCapitalAuthorizationPage:
        """Return one bounded authorization page without transport concerns."""

        return await self._repository.list(
            limit=limit,
            offset=offset,
            state=state,
        )

    async def get(
        self,
        authorization_id: UUID,
    ) -> OperationalPaperCapitalAuthorization:
        """Return one authorization or raise the stable not-found error."""

        result = await self._repository.get(authorization_id)
        if result is None:
            raise OperationalPaperCapitalAuthorizationNotFoundError()
        return result

    async def create(
        self,
        intent: OperationalPaperCapitalAuthorizationCreateIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperCapitalAuthorization:
        """Create or replay one paper-capital authorization."""

        now = self._clock()
        return await self._repository.create(
            intent,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def revoke(
        self,
        authorization_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperCapitalAuthorization:
        """Revoke one authorization through the repository authority."""

        now = self._clock()
        return await self._repository.revoke(
            authorization_id,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
        )
