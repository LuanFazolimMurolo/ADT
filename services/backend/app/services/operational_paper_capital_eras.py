"""Application service for official operational paper-capital era designation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.operational_paper_capital_eras import (
    OperationalPaperCapitalEra,
    OperationalPaperCapitalEraDesignationIntent,
)
from app.repositories.operational_paper_capital_eras import (
    PostgresOperationalPaperCapitalEraRepository,
)


class OperationalPaperCapitalEraService:
    """Bounded administrator application boundary for official-era designation."""

    def __init__(
        self,
        *,
        repository: PostgresOperationalPaperCapitalEraRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def designate(
        self,
        intent: OperationalPaperCapitalEraDesignationIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperCapitalEra:
        """Designate or replay one official paper-capital era."""

        return await self._repository.designate(
            intent,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )
