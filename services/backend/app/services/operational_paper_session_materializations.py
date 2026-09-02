"""Application service for durable paper-session config materialization."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.domain.errors import PersistenceError
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationNotFoundError,
    OperationalPaperCapitalAuthorizationSpecification,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationNotFoundError,
    OperationalPaperSessionMaterializationPlan,
    OperationalPaperSessionMaterializationState,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
    build_operational_paper_session_materialization_plan,
    operational_paper_session_materialization_specification_checksum,
)
from app.paper_trading.domain import (
    PaperSessionConfig,
    paper_config_checksum,
    paper_session_id,
)
from app.paper_trading.errors import PaperSessionConflictError, PaperSessionNotFoundError
from app.paper_trading.repository import PaperTradingRepository
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)

OperationalPaperSessionMaterializationClock = Callable[[], datetime]


def _verify_admin_identity(
    materialization: OperationalPaperSessionMaterialization,
    plan: OperationalPaperSessionMaterializationPlan,
) -> None:
    specification = plan.specification
    if (
        materialization.authorization_binding != specification.authorization_binding
        or materialization.profile_binding != specification.profile_binding
        or materialization.mandate_binding != specification.mandate_binding
        or materialization.simulation_id != specification.simulation_id
        or materialization.config_checksum != specification.config_checksum
        or materialization.session_id != specification.session_id
        or materialization.materialization_checksum
        != operational_paper_session_materialization_specification_checksum(specification)
    ):
        raise OperationalPaperSessionMaterializationConfigIdentityConflictError()


def _verify_executable_identity(
    persisted_config: PaperSessionConfig,
    plan: OperationalPaperSessionMaterializationPlan,
    materialization: OperationalPaperSessionMaterialization,
) -> None:
    session_id = paper_session_id(persisted_config)
    config_checksum = paper_config_checksum(persisted_config)
    specification = plan.specification
    if (
        persisted_config != plan.config
        or session_id != materialization.session_id
        or session_id != specification.session_id
        or config_checksum != materialization.config_checksum
        or config_checksum != specification.config_checksum
    ):
        raise OperationalPaperSessionMaterializationConfigIdentityConflictError()


def _verify_materialized_transition(
    prepared: OperationalPaperSessionMaterialization,
    materialized: OperationalPaperSessionMaterialization,
    plan: OperationalPaperSessionMaterializationPlan,
    actor_id: UUID,
) -> None:
    _verify_admin_identity(materialized, plan)
    if (
        materialized.state is not OperationalPaperSessionMaterializationState.MATERIALIZED
        or materialized.record_version != prepared.record_version + 1
        or materialized.materialization_id != prepared.materialization_id
        or materialized.prepared_by != prepared.prepared_by
        or materialized.prepared_at != prepared.prepared_at
        or materialized.materialized_by != actor_id
        or materialized.materialized_at is None
    ):
        raise OperationalPaperSessionMaterializationStateTransitionConflictError()


class OperationalPaperSessionMaterializationService:
    """Reconcile durable administrative provenance with one canonical local config."""

    def __init__(
        self,
        *,
        repository: PostgresOperationalPaperSessionMaterializationRepository,
        authorization_repository: PostgresOperationalPaperCapitalAuthorizationRepository,
        profile_repository: PostgresOperationalPaperSessionProfileRepository,
        paper_repository: PaperTradingRepository,
        clock: OperationalPaperSessionMaterializationClock,
    ) -> None:
        self._repository = repository
        self._authorization_repository = authorization_repository
        self._profile_repository = profile_repository
        self._paper_repository = paper_repository
        self._clock = clock

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionMaterializationState | None = None,
    ) -> tuple[list[OperationalPaperSessionMaterialization], int]:
        return await self._repository.list(
            limit=limit,
            offset=offset,
            state=state,
        )

    async def get(
        self,
        materialization_id: UUID,
    ) -> OperationalPaperSessionMaterialization:
        materialization = await self._repository.get(materialization_id)
        if materialization is None:
            raise OperationalPaperSessionMaterializationNotFoundError()
        return materialization

    async def materialize(
        self,
        plan: OperationalPaperSessionMaterializationPlan,
        *,
        actor_id: UUID,
    ) -> OperationalPaperSessionMaterialization:
        """Prepare provenance, publish its exact config, and mark it materialized."""

        prepared_request_at = self._clock()
        prepared = await self._repository.prepare(
            plan,
            actor_id=actor_id,
            now=prepared_request_at,
        )
        _verify_admin_identity(prepared, plan)

        if prepared.state is OperationalPaperSessionMaterializationState.MATERIALIZED:
            if prepared.record_version != 2:
                raise OperationalPaperSessionMaterializationStateTransitionConflictError()
            try:
                persisted_config = self._paper_repository.load_config(prepared.session_id)
            except PaperSessionNotFoundError as error:
                raise OperationalPaperSessionMaterializationConfigIdentityConflictError() from error
            _verify_executable_identity(persisted_config, plan, prepared)
            return prepared

        if (
            prepared.state is not OperationalPaperSessionMaterializationState.PREPARED
            or prepared.record_version != 1
        ):
            raise OperationalPaperSessionMaterializationStateTransitionConflictError()

        try:
            persisted_config = self._paper_repository.create(plan.config)
        except PaperSessionConflictError as error:
            raise OperationalPaperSessionMaterializationConfigIdentityConflictError() from error
        _verify_executable_identity(persisted_config, plan, prepared)

        materialized_at = self._clock()
        materialized = await self._repository.mark_materialized(
            prepared.materialization_id,
            expected_record_version=prepared.record_version,
            actor_id=actor_id,
            now=materialized_at,
        )
        _verify_materialized_transition(prepared, materialized, plan, actor_id)
        return materialized

    async def materialize_authorization(
        self,
        authorization_id: UUID,
        *,
        actor_id: UUID,
    ) -> OperationalPaperSessionMaterialization:
        """Resolve frozen authoritative evidence and materialize its paper session."""

        authorization = await self._authorization_repository.get(authorization_id)
        if authorization is None:
            raise OperationalPaperCapitalAuthorizationNotFoundError()

        authorization_specification = OperationalPaperCapitalAuthorizationSpecification(
            schema_version=authorization.schema_version,
            profile_binding=authorization.profile_binding,
            simulation_id=authorization.simulation_id,
            quote_asset=authorization.quote_asset,
            authorized_capital=authorization.authorized_capital,
        )
        binding = authorization.profile_binding
        profile_revision = await self._profile_repository.get_revision(
            binding.profile_id,
            binding.approved_revision,
        )
        if profile_revision is None:
            raise PersistenceError()

        plan = build_operational_paper_session_materialization_plan(
            authorization_id=authorization.authorization_id,
            authorization_specification=authorization_specification,
            authorization_checksum=authorization.authorization_checksum,
            profile_revision=profile_revision,
        )
        return await self.materialize(plan, actor_id=actor_id)
