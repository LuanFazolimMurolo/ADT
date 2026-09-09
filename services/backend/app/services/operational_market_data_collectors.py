"""Administrative control plane for operational market-data collectors."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

import app.operational_market_data_collectors as collectors
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
)


class OperationalMarketDataCollectorService:
    """Persist administrator intent without executing physical collection work.

    The service owns only application-level command routing and replay ordering.
    PostgreSQL remains the durable operational authority; physical convergence,
    local collector locking, scheduling and exchange I/O belong to Gate 2E.
    """

    def __init__(
        self,
        *,
        repository: PostgresOperationalMarketDataCollectorRepository,
    ) -> None:
        self._repository = repository

    async def start(
        self,
        specification: collectors.OperationalMarketDataCollectorSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Request one epoch for an exact immutable effective specification."""

        if not isinstance(
            specification,
            collectors.OperationalMarketDataCollectorSpecification,
        ):
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()

        specification = replace(specification)

        specification_checksum = (
            collectors.operational_market_data_collector_specification_checksum(specification)
        )

        intent = collectors.OperationalMarketDataCollectorStartIntent(
            specification_checksum=specification_checksum,
        )

        # Historical replay has precedence over the existence of another
        # nonterminal epoch. This also permits exact replay after the original
        # epoch has already reached a terminal state.
        replay = await self._repository.resolve_start_replay(
            intent,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

        if replay is not None:
            return replay

        current = await self._repository.get_current_for_scope(
            specification.scope,
        )

        if current is not None:
            # Close the race in which an identical concurrent START committed
            # after the initial replay miss but before this current-epoch read.
            replay = await self._repository.resolve_start_replay(
                intent,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            )

            if replay is not None:
                return replay

            raise collectors.OperationalMarketDataCollectorCurrentEpochConflictError()

        # Repository.start independently closes the remaining database race and
        # performs replay-after-rollback for concurrent identical START.
        return await self._repository.start(
            specification,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=requested_at,
        )

    async def pause(
        self,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        """Persist PAUSED desired state without waiting for convergence."""

        self._require_command(
            intent,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        )

        return await self._repository.request_command(
            intent,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=requested_at,
        )

    async def resume(
        self,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        """Persist RUNNING desired state without requiring observed PAUSED."""

        self._require_command(
            intent,
            collectors.OperationalMarketDataCollectorCommandType.RESUME,
        )

        return await self._repository.request_command(
            intent,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=requested_at,
        )

    async def stop(
        self,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        """Persist STOPPED desired state without executing collector work."""

        self._require_command(
            intent,
            collectors.OperationalMarketDataCollectorCommandType.STOP,
        )

        return await self._repository.request_command(
            intent,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=requested_at,
        )

    async def get(
        self,
        epoch_id: UUID,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        """Return one exact persisted operational collector epoch."""

        epoch = await self._repository.get(epoch_id)

        if epoch is None:
            raise collectors.OperationalMarketDataCollectorNotFoundError()

        return epoch

    async def list_commands(
        self,
        epoch_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[collectors.OperationalMarketDataCollectorCommand]:
        """Return bounded immutable command history for an existing epoch."""

        await self.get(epoch_id)

        return await self._repository.list_commands(
            epoch_id,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _require_command(
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        command_type: collectors.OperationalMarketDataCollectorCommandType,
    ) -> None:
        if not isinstance(
            intent,
            collectors.OperationalMarketDataCollectorCommandIntent,
        ):
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()

        if intent.command_type is not command_type:
            raise collectors.OperationalMarketDataCollectorCommandConflictError()
