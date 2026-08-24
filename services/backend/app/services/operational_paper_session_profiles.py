"""Transport-independent operational paper-session profile service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.backtesting.domain import StrategyParameters
from app.operational_paper_session_profiles import (
    OperationalPaperSessionProfile,
    OperationalPaperSessionProfileCreateIntent,
    OperationalPaperSessionProfileRevision,
    OperationalPaperSessionProfileState,
)
from app.operational_paper_session_profiles.errors import (
    OperationalPaperSessionProfileNotFoundError,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.strategies.definitions import StrategyDefinition, decode_strategy_parameters
from app.strategies.errors import StrategyDefinitionCompatibilityError
from app.strategies.registry import StrategyPluginRegistry

OperationalPaperSessionProfileClock = Callable[[], datetime]
OperationalPaperSessionProfileCurrent = tuple[
    OperationalPaperSessionProfile,
    OperationalPaperSessionProfileRevision,
]
OperationalPaperSessionProfilePage = tuple[
    list[OperationalPaperSessionProfileCurrent],
    int,
]
OperationalPaperSessionProfileRevisionPage = tuple[
    list[OperationalPaperSessionProfileRevision],
    int,
]
OperationalPaperSessionProfileStrategyResolver = Callable[
    [StrategyDefinition],
    StrategyParameters,
]


class OperationalPaperSessionProfileService:
    """Coordinate profile use cases without transport or runtime concerns."""

    def __init__(
        self,
        *,
        repository: PostgresOperationalPaperSessionProfileRepository,
        registry: StrategyPluginRegistry,
        clock: OperationalPaperSessionProfileClock,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._clock = clock

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionProfileState | None = None,
    ) -> OperationalPaperSessionProfilePage:
        """Return the repository's bounded current-profile catalog."""

        return await self._repository.list_current(
            limit=limit,
            offset=offset,
            state=state,
        )

    async def get(self, profile_id: UUID) -> OperationalPaperSessionProfileCurrent:
        """Return one profile with its exact current immutable revision."""

        current = await self._repository.get_current(profile_id)
        if current is None:
            raise OperationalPaperSessionProfileNotFoundError()
        return current

    async def list_revisions(
        self,
        profile_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> OperationalPaperSessionProfileRevisionPage:
        """Return one profile's bounded immutable revision history."""

        return await self._repository.list_revisions(
            profile_id,
            limit=limit,
            offset=offset,
        )

    async def get_revision(
        self,
        profile_id: UUID,
        revision: int,
    ) -> OperationalPaperSessionProfileRevision:
        """Return one exact immutable revision or raise stable not-found."""

        result = await self._repository.get_revision(profile_id, revision)
        if result is None:
            raise OperationalPaperSessionProfileNotFoundError()
        return result

    async def create(
        self,
        intent: OperationalPaperSessionProfileCreateIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperSessionProfileCurrent:
        """Create or replay one draft without eagerly resolving its strategy."""

        now = self._clock()
        return await self._repository.create(
            intent,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=now,
            strategy_resolver=self._resolve_strategy_parameters,
        )

    async def replace_draft(
        self,
        profile_id: UUID,
        intent: OperationalPaperSessionProfileCreateIntent,
        *,
        expected_revision: int,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperSessionProfileCurrent:
        """Replace one draft while preserving repository ordering and tokens."""

        now = self._clock()
        return await self._repository.replace_draft(
            profile_id,
            intent,
            expected_revision=expected_revision,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
            strategy_resolver=self._resolve_strategy_parameters,
        )

    async def approve(
        self,
        profile_id: UUID,
        *,
        expected_revision: int,
        expected_checksum: str,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperSessionProfile:
        """Approve one exact revision without eagerly resolving its strategy."""

        now = self._clock()
        return await self._repository.approve(
            profile_id,
            expected_revision=expected_revision,
            expected_checksum=expected_checksum,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
            strategy_resolver=self._resolve_strategy_parameters,
        )

    async def archive(
        self,
        profile_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperSessionProfile:
        """Archive one profile without consulting strategy authority."""

        now = self._clock()
        return await self._repository.archive(
            profile_id,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
        )

    def _resolve_strategy_parameters(
        self,
        definition: StrategyDefinition,
    ) -> StrategyParameters:
        try:
            plugin = self._registry.resolve(
                definition.spec.plugin_name,
                definition.spec.plugin_version,
            )
        except ValueError as error:
            raise StrategyDefinitionCompatibilityError() from error

        descriptor = plugin.descriptor
        if (
            descriptor.schema_version != definition.spec.plugin_schema_version
            or descriptor.lifecycle_version != definition.spec.lifecycle_version
        ):
            raise StrategyDefinitionCompatibilityError()
        return decode_strategy_parameters(descriptor, definition.spec.parameters)
