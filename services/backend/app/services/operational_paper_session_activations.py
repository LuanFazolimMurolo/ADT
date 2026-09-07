"""Application policy for operational paper-session activation grants."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.domain.errors import PersistenceError
from app.operational_paper_session_activations import (
    InvalidOperationalPaperSessionActivationSpecificationError,
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationCreateIntent,
    OperationalPaperSessionActivationIdempotencyConflictError,
    OperationalPaperSessionActivationNotFoundError,
    OperationalPaperSessionActivationState,
    build_operational_paper_session_activation_specification,
    operational_paper_session_activation_create_intent_fingerprint,
    validate_operational_paper_session_activation_idempotency_key,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationNotFoundError,
    OperationalPaperSessionMaterializationState,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
)
from app.operational_paper_session_profiles import (
    OperationalPaperSessionProfileRevision,
    operational_paper_session_profile_strategy_snapshot_payload,
)
from app.paper_trading.domain import (
    PaperSessionConfig,
    paper_config_checksum,
    paper_config_payload,
    paper_session_id,
)
from app.paper_trading.errors import PaperSessionNotFoundError
from app.paper_trading.repository import PaperTradingRepository
from app.repositories.operational_paper_session_activations import (
    PostgresOperationalPaperSessionActivationRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.strategies.errors import StrategyDefinitionCompatibilityError
from app.strategies.registry import StrategyPluginRegistry

OperationalPaperSessionActivationClock = Callable[[], datetime]
OperationalPaperSessionActivationPage = tuple[
    list[OperationalPaperSessionActivation],
    int,
]


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidOperationalPaperSessionActivationSpecificationError()
    return value


def _verify_historical_fingerprint(
    activation: OperationalPaperSessionActivation,
) -> None:
    historical_intent = OperationalPaperSessionActivationCreateIntent(
        materialization_id=activation.materialization_id,
        materialization_checksum=activation.materialization_checksum,
    )
    expected = operational_paper_session_activation_create_intent_fingerprint(historical_intent)
    if activation.create_intent_fingerprint != expected:
        raise PersistenceError()


def _verify_materialization_config(
    materialization: OperationalPaperSessionMaterialization,
    config: PaperSessionConfig,
) -> None:
    if (
        paper_session_id(config) != materialization.session_id
        or paper_config_checksum(config) != materialization.config_checksum
    ):
        raise OperationalPaperSessionMaterializationConfigIdentityConflictError()


def _verify_profile_revision(
    materialization: OperationalPaperSessionMaterialization,
    revision: object,
) -> OperationalPaperSessionProfileRevision:
    binding = materialization.profile_binding
    if not isinstance(revision, OperationalPaperSessionProfileRevision):
        raise PersistenceError()
    if (
        revision.profile_id != binding.profile_id
        or revision.revision != binding.approved_revision
        or revision.specification_checksum != binding.specification_checksum
    ):
        raise PersistenceError()
    return revision


class OperationalPaperSessionActivationService:
    """Verify frozen executable evidence and delegate activation persistence."""

    def __init__(
        self,
        *,
        repository: PostgresOperationalPaperSessionActivationRepository,
        materialization_repository: PostgresOperationalPaperSessionMaterializationRepository,
        profile_repository: PostgresOperationalPaperSessionProfileRepository,
        paper_repository: PaperTradingRepository,
        registry: StrategyPluginRegistry,
        clock: OperationalPaperSessionActivationClock,
    ) -> None:
        self._repository = repository
        self._materialization_repository = materialization_repository
        self._profile_repository = profile_repository
        self._paper_repository = paper_repository
        self._registry = registry
        self._clock = clock

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionActivationState | None = None,
        materialization_id: UUID | None = None,
    ) -> OperationalPaperSessionActivationPage:
        return await self._repository.list(
            limit=limit,
            offset=offset,
            state=state,
            materialization_id=materialization_id,
        )

    async def get(
        self,
        activation_id: UUID,
    ) -> OperationalPaperSessionActivation:
        activation = await self._repository.get(activation_id)
        if activation is None:
            raise OperationalPaperSessionActivationNotFoundError()
        return activation

    async def authorize(
        self,
        materialization_id: UUID,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperSessionActivation:
        materialization_id = _require_uuid(materialization_id)
        actor_id = _require_uuid(actor_id)
        idempotency_key = validate_operational_paper_session_activation_idempotency_key(
            idempotency_key
        )

        existing = await self._repository.get_by_actor_idempotency(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            _verify_historical_fingerprint(existing)
            if existing.materialization_id != materialization_id:
                raise OperationalPaperSessionActivationIdempotencyConflictError()
            return existing

        materialization = await self._materialization_repository.get(materialization_id)
        if materialization is None:
            raise OperationalPaperSessionMaterializationNotFoundError()
        if materialization.state is not OperationalPaperSessionMaterializationState.MATERIALIZED:
            raise OperationalPaperSessionMaterializationStateTransitionConflictError()
        specification = build_operational_paper_session_activation_specification(materialization)

        try:
            config = self._paper_repository.load_config(materialization.session_id)
        except PaperSessionNotFoundError as error:
            raise OperationalPaperSessionMaterializationConfigIdentityConflictError() from error
        _verify_materialization_config(materialization, config)

        binding = materialization.profile_binding
        revision = await self._profile_repository.get_revision(
            binding.profile_id,
            binding.approved_revision,
        )
        revision = _verify_profile_revision(materialization, revision)
        snapshot = revision.specification.strategy_snapshot
        snapshot_payload = operational_paper_session_profile_strategy_snapshot_payload(snapshot)
        config_payload = paper_config_payload(config)
        expected_strategy_payload = {
            "name": snapshot.plugin_name,
            "version": snapshot.plugin_version,
            "parameters": snapshot_payload["parameters"],
        }
        if (
            config_payload["strategy"] != expected_strategy_payload
            or config.strategy_lifecycle_version != snapshot.strategy_lifecycle_version
        ):
            raise OperationalPaperSessionMaterializationConfigIdentityConflictError()

        try:
            plugin = self._registry.resolve(
                snapshot.plugin_name,
                snapshot.plugin_version,
            )
        except ValueError as error:
            raise StrategyDefinitionCompatibilityError() from error
        descriptor = plugin.descriptor
        if (
            descriptor.name != snapshot.plugin_name
            or descriptor.version != snapshot.plugin_version
            or descriptor.schema_version != snapshot.plugin_schema_version
            or descriptor.lifecycle_version != snapshot.strategy_lifecycle_version
        ):
            raise StrategyDefinitionCompatibilityError()

        now = self._clock()
        return await self._repository.create(
            specification,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def revoke(
        self,
        activation_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperSessionActivation:
        now = self._clock()
        return await self._repository.revoke(
            activation_id,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
        )
