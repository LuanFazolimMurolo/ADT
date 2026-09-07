"""Application-service tests for operational paper-session activations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.backtesting.domain import StrategyDescriptor, StrategyParameterValue
from app.domain.errors import DomainError, PersistenceError
from app.operational_paper_session_activations import (
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationCreateIntent,
    OperationalPaperSessionActivationIdempotencyConflictError,
    OperationalPaperSessionActivationNotFoundError,
    OperationalPaperSessionActivationSpecification,
    OperationalPaperSessionActivationState,
    authorize_operational_paper_session_activation,
    build_operational_paper_session_activation_specification,
    operational_paper_session_activation_create_intent_fingerprint,
    revoke_operational_paper_session_activation,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationNotFoundError,
    OperationalPaperSessionMaterializationProfileBinding,
    OperationalPaperSessionMaterializationSpecification,
    OperationalPaperSessionMaterializationState,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
    operational_paper_session_materialization_specification_checksum,
)
from app.operational_paper_session_profiles import (
    OperationalPaperSessionProfileRevision,
    OperationalPaperSessionProfileStrategySnapshot,
    build_operational_paper_session_profile_strategy_snapshot,
)
from app.paper_trading.domain import PaperSessionConfig, paper_config_checksum, paper_session_id
from app.paper_trading.errors import PaperSessionCorruptError, PaperSessionNotFoundError
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
from app.services.operational_paper_session_activations import (
    OperationalPaperSessionActivationService,
)
from app.strategies.errors import (
    StrategyDefinitionCompatibilityError,
    StrategyPluginNotFoundError,
)
from app.strategies.registry import StrategyPluginRegistry
from tests.test_operational_paper_session_materializations_domain import (
    ACTOR_ID,
    MATERIALIZATION_ID,
    NOW,
    _plan,
)
from tests.test_operational_paper_session_materializations_domain import (
    _profile_revision as _domain_profile_revision,
)

ACTIVATION_ID = UUID("90000000-0000-4000-8000-000000000009")
OTHER_MATERIALIZATION_ID = UUID("a0000000-0000-4000-8000-00000000000a")
AUTHORIZED_AT = datetime(2026, 9, 4, 15, tzinfo=UTC)
IDEMPOTENCY_KEY = "activation:service:1"


class _Clock:
    def __init__(self, value: datetime, events: list[str]) -> None:
        self.value = value
        self.events = events
        self.calls = 0

    def __call__(self) -> datetime:
        self.events.append("clock")
        self.calls += 1
        return self.value


class _ActivationRepositoryDouble:
    def __init__(
        self,
        events: list[str],
        *,
        existing: OperationalPaperSessionActivation | None = None,
        get_result: OperationalPaperSessionActivation | None = None,
        list_result: tuple[list[OperationalPaperSessionActivation], int] = ([], 0),
        create_result: OperationalPaperSessionActivation | None = None,
        revoke_result: OperationalPaperSessionActivation | None = None,
    ) -> None:
        self.events = events
        self.existing = existing
        self.get_result = get_result
        self.list_result = list_result
        self.create_result = create_result
        self.revoke_result = revoke_result
        self.actor_lookups: list[tuple[UUID, str]] = []
        self.create_calls: list[
            tuple[OperationalPaperSessionActivationSpecification, UUID, str, datetime]
        ] = []
        self.revoke_calls: list[tuple[UUID, int, UUID, datetime]] = []
        self.get_calls: list[UUID] = []
        self.list_calls: list[
            tuple[int, int, OperationalPaperSessionActivationState | None, UUID | None]
        ] = []

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionActivationState | None = None,
        materialization_id: UUID | None = None,
    ) -> tuple[list[OperationalPaperSessionActivation], int]:
        self.events.append("activation_list")
        self.list_calls.append((limit, offset, state, materialization_id))
        return self.list_result

    async def get(
        self,
        activation_id: UUID,
    ) -> OperationalPaperSessionActivation | None:
        self.events.append("activation_get")
        self.get_calls.append(activation_id)
        return self.get_result

    async def get_by_actor_idempotency(
        self,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperSessionActivation | None:
        self.events.append("actor_key_lookup")
        self.actor_lookups.append((actor_id, idempotency_key))
        return self.existing

    async def create(
        self,
        specification: OperationalPaperSessionActivationSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> OperationalPaperSessionActivation:
        self.events.append("activation_create")
        self.create_calls.append((specification, actor_id, idempotency_key, now))
        assert self.create_result is not None
        return self.create_result

    async def revoke(
        self,
        activation_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionActivation:
        self.events.append("activation_revoke")
        self.revoke_calls.append((activation_id, expected_record_version, actor_id, now))
        assert self.revoke_result is not None
        return self.revoke_result


class _MaterializationRepositoryDouble:
    def __init__(
        self,
        events: list[str],
        result: OperationalPaperSessionMaterialization | None,
    ) -> None:
        self.events = events
        self.result = result
        self.calls: list[UUID] = []

    async def get(
        self,
        materialization_id: UUID,
    ) -> OperationalPaperSessionMaterialization | None:
        self.events.append("materialization_get")
        self.calls.append(materialization_id)
        return self.result


class _PaperRepositoryDouble:
    def __init__(
        self,
        events: list[str],
        config: PaperSessionConfig,
        *,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.config = config
        self.error = error
        self.calls: list[str] = []

    def load_config(self, session_id: str) -> PaperSessionConfig:
        self.events.append("paper_load")
        self.calls.append(session_id)
        if self.error is not None:
            raise self.error
        return self.config


class _ProfileRepositoryDouble:
    def __init__(
        self,
        events: list[str],
        revision: OperationalPaperSessionProfileRevision | None,
    ) -> None:
        self.events = events
        self.revision = revision
        self.calls: list[tuple[UUID, int]] = []

    async def get_revision(
        self,
        profile_id: UUID,
        revision: int,
    ) -> OperationalPaperSessionProfileRevision | None:
        self.events.append("profile_revision_get")
        self.calls.append((profile_id, revision))
        return self.revision


class _RegistryDouble:
    def __init__(
        self,
        events: list[str],
        descriptor: object,
        *,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.descriptor = descriptor
        self.error = error
        self.resolve_calls: list[tuple[str, str]] = []
        self.build_calls = 0

    def resolve(self, name: str, version: str) -> object:
        self.events.append("registry_resolve")
        self.resolve_calls.append((name, version))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(descriptor=self.descriptor)

    def build(self, *_args: object, **_kwargs: object) -> object:
        self.build_calls += 1
        raise AssertionError("activation eligibility must not build a strategy")


def _snapshot(
    **changes: object,
) -> OperationalPaperSessionProfileStrategySnapshot:
    base = _domain_profile_revision().specification.strategy_snapshot
    values: dict[str, object] = {
        "strategy_definition_id": base.strategy_definition_id,
        "source_revision": base.source_revision,
        "plugin_name": base.plugin_name,
        "plugin_version": base.plugin_version,
        "plugin_schema_version": base.plugin_schema_version,
        "strategy_lifecycle_version": base.strategy_lifecycle_version,
        "parameters": base.parameters,
        "parameters_checksum": base.parameters_checksum,
        "snapshot_schema_version": base.snapshot_schema_version,
    }
    values.update(changes)
    return build_operational_paper_session_profile_strategy_snapshot(**values)  # type: ignore[arg-type]


def _materialization(
    *,
    state: OperationalPaperSessionMaterializationState = (
        OperationalPaperSessionMaterializationState.MATERIALIZED
    ),
    profile_revision: OperationalPaperSessionProfileRevision | None = None,
    session_id: str | None = None,
    config_checksum: str | None = None,
) -> OperationalPaperSessionMaterialization:
    plan = _plan()
    base = plan.specification
    profile_binding = base.profile_binding
    if profile_revision is not None:
        profile_binding = OperationalPaperSessionMaterializationProfileBinding(
            profile_id=profile_revision.profile_id,
            approved_revision=profile_revision.revision,
            specification_checksum=profile_revision.specification_checksum,
        )
    specification = OperationalPaperSessionMaterializationSpecification(
        schema_version=base.schema_version,
        materialization_contract_version=base.materialization_contract_version,
        authorization_binding=base.authorization_binding,
        profile_binding=profile_binding,
        mandate_binding=base.mandate_binding,
        simulation_id=base.simulation_id,
        session_id=base.session_id if session_id is None else session_id,
        config_checksum=(base.config_checksum if config_checksum is None else config_checksum),
    )
    materialized = state is OperationalPaperSessionMaterializationState.MATERIALIZED
    return OperationalPaperSessionMaterialization(
        materialization_id=MATERIALIZATION_ID,
        schema_version=specification.schema_version,
        materialization_contract_version=specification.materialization_contract_version,
        state=state,
        record_version=2 if materialized else 1,
        authorization_binding=specification.authorization_binding,
        profile_binding=specification.profile_binding,
        mandate_binding=specification.mandate_binding,
        simulation_id=specification.simulation_id,
        config_checksum=specification.config_checksum,
        session_id=specification.session_id,
        materialization_checksum=(
            operational_paper_session_materialization_specification_checksum(specification)
        ),
        prepared_by=ACTOR_ID,
        prepared_at=NOW,
        materialized_by=ACTOR_ID if materialized else None,
        materialized_at=NOW + timedelta(seconds=1) if materialized else None,
    )


def _activation(
    materialization: OperationalPaperSessionMaterialization,
    *,
    state: OperationalPaperSessionActivationState = (
        OperationalPaperSessionActivationState.AUTHORIZED
    ),
) -> OperationalPaperSessionActivation:
    specification = build_operational_paper_session_activation_specification(materialization)
    intent = OperationalPaperSessionActivationCreateIntent(
        materialization_id=materialization.materialization_id,
        materialization_checksum=materialization.materialization_checksum,
    )
    activation = authorize_operational_paper_session_activation(
        activation_id=ACTIVATION_ID,
        specification=specification,
        authorized_by=ACTOR_ID,
        authorized_at=AUTHORIZED_AT,
        create_idempotency_key=IDEMPOTENCY_KEY,
        create_intent_fingerprint=(
            operational_paper_session_activation_create_intent_fingerprint(intent)
        ),
    )
    if state is OperationalPaperSessionActivationState.REVOKED:
        return revoke_operational_paper_session_activation(
            activation,
            revoked_by=ACTOR_ID,
            revoked_at=AUTHORIZED_AT + timedelta(minutes=1),
        )
    return activation


def _descriptor(
    revision: OperationalPaperSessionProfileRevision,
    **changes: object,
) -> object:
    snapshot = revision.specification.strategy_snapshot
    values: dict[str, object] = {
        "name": snapshot.plugin_name,
        "version": snapshot.plugin_version,
        "schema_version": snapshot.plugin_schema_version,
        "lifecycle_version": snapshot.strategy_lifecycle_version,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _service(
    repository: _ActivationRepositoryDouble,
    materialization_repository: _MaterializationRepositoryDouble,
    paper_repository: _PaperRepositoryDouble,
    profile_repository: _ProfileRepositoryDouble,
    registry: _RegistryDouble,
    clock: _Clock,
) -> OperationalPaperSessionActivationService:
    return OperationalPaperSessionActivationService(
        repository=cast(PostgresOperationalPaperSessionActivationRepository, repository),
        materialization_repository=cast(
            PostgresOperationalPaperSessionMaterializationRepository,
            materialization_repository,
        ),
        profile_repository=cast(
            PostgresOperationalPaperSessionProfileRepository,
            profile_repository,
        ),
        paper_repository=cast(PaperTradingRepository, paper_repository),
        registry=cast(StrategyPluginRegistry, registry),
        clock=clock,
    )


def _dependencies(
    *,
    existing: OperationalPaperSessionActivation | None = None,
    materialization: OperationalPaperSessionMaterialization | None = None,
    config: PaperSessionConfig | None = None,
    profile_revision: OperationalPaperSessionProfileRevision | None = None,
    registry_error: Exception | None = None,
    descriptor: object | None = None,
    create_result: OperationalPaperSessionActivation | None = None,
) -> tuple[
    OperationalPaperSessionActivationService,
    list[str],
    _ActivationRepositoryDouble,
    _MaterializationRepositoryDouble,
    _PaperRepositoryDouble,
    _ProfileRepositoryDouble,
    _RegistryDouble,
    _Clock,
]:
    events: list[str] = []
    materialization = _materialization() if materialization is None else materialization
    config = _plan().config if config is None else config
    profile_revision = _domain_profile_revision() if profile_revision is None else profile_revision
    activation = _activation(materialization)
    repository = _ActivationRepositoryDouble(
        events,
        existing=existing,
        create_result=activation if create_result is None else create_result,
        revoke_result=activation,
    )
    materializations = _MaterializationRepositoryDouble(events, materialization)
    paper = _PaperRepositoryDouble(events, config)
    profiles = _ProfileRepositoryDouble(events, profile_revision)
    registry = _RegistryDouble(
        events,
        _descriptor(profile_revision) if descriptor is None else descriptor,
        error=registry_error,
    )
    clock = _Clock(AUTHORIZED_AT, events)
    return (
        _service(repository, materializations, paper, profiles, registry, clock),
        events,
        repository,
        materializations,
        paper,
        profiles,
        registry,
        clock,
    )


@pytest.mark.asyncio
async def test_list_delegates_all_filters_without_touching_dependencies() -> None:
    materialization = _materialization()
    activation = _activation(materialization)
    service, events, repository, *_ = _dependencies()
    expected = ([activation], 1)
    repository.list_result = expected

    result = await service.list(
        limit=7,
        offset=3,
        state=OperationalPaperSessionActivationState.REVOKED,
        materialization_id=materialization.materialization_id,
    )

    assert result is expected
    assert repository.list_calls == [
        (7, 3, OperationalPaperSessionActivationState.REVOKED, materialization.materialization_id)
    ]
    assert events == ["activation_list"]


@pytest.mark.asyncio
async def test_get_returns_exact_activation_without_touching_dependencies() -> None:
    activation = _activation(_materialization())
    service, events, repository, *_ = _dependencies()
    repository.get_result = activation

    assert await service.get(activation.activation_id) is activation
    assert repository.get_calls == [activation.activation_id]
    assert events == ["activation_get"]


@pytest.mark.asyncio
async def test_get_missing_raises_stable_not_found_without_touching_dependencies() -> None:
    service, events, repository, materializations, paper, profiles, registry, clock = (
        _dependencies()
    )

    with pytest.raises(OperationalPaperSessionActivationNotFoundError):
        await service.get(uuid4())

    assert len(repository.get_calls) == 1
    assert events == ["activation_get"]
    assert clock.calls == 0
    assert repository.create_calls == []
    assert registry.build_calls == 0
    assert registry.resolve_calls == []
    assert profiles.calls == []
    assert paper.calls == []
    assert materializations.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", list(OperationalPaperSessionActivationState))
async def test_historical_replay_precedes_all_mutable_dependencies(
    state: OperationalPaperSessionActivationState,
) -> None:
    existing = _activation(_materialization(), state=state)
    service, events, repository, materializations, paper, profiles, registry, clock = _dependencies(
        existing=existing
    )

    result = await service.authorize(
        existing.materialization_id,
        actor_id=ACTOR_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert result is existing
    assert events == ["actor_key_lookup"]
    assert repository.create_calls == []
    assert registry.resolve_calls == []
    assert registry.build_calls == 0
    assert clock.calls == 0
    assert profiles.calls == []
    assert paper.calls == []
    assert materializations.calls == []


@pytest.mark.asyncio
async def test_same_key_with_different_intent_conflicts_before_mutable_dependencies() -> None:
    existing = _activation(_materialization())
    service, events, repository, materializations, paper, profiles, registry, clock = _dependencies(
        existing=existing
    )

    with pytest.raises(OperationalPaperSessionActivationIdempotencyConflictError):
        await service.authorize(
            OTHER_MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == ["actor_key_lookup"]
    assert repository.create_calls == []
    assert clock.calls == 0
    assert registry.build_calls == 0
    assert registry.resolve_calls == []
    assert profiles.calls == []
    assert paper.calls == []
    assert materializations.calls == []


@pytest.mark.asyncio
async def test_corrupt_historical_fingerprint_fails_closed_before_mutable_dependencies() -> None:
    existing = replace(
        _activation(_materialization()),
        create_intent_fingerprint="0" * 64,
    )
    service, events, repository, materializations, paper, profiles, registry, clock = _dependencies(
        existing=existing
    )

    with pytest.raises(PersistenceError):
        await service.authorize(
            existing.materialization_id,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == ["actor_key_lookup"]
    assert repository.create_calls == []
    assert clock.calls == 0
    assert registry.build_calls == 0
    assert registry.resolve_calls == []
    assert profiles.calls == []
    assert paper.calls == []
    assert materializations.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("materialization_id", "actor_id", "key"),
    (
        (UUID(int=0), ACTOR_ID, IDEMPOTENCY_KEY),
        (MATERIALIZATION_ID, UUID(int=0), IDEMPOTENCY_KEY),
        (MATERIALIZATION_ID, ACTOR_ID, " invalid"),
    ),
)
async def test_authorize_validates_inputs_before_first_repository_lookup(
    materialization_id: UUID,
    actor_id: UUID,
    key: str,
) -> None:
    service, events, repository, materializations, paper, profiles, registry, clock = (
        _dependencies()
    )

    with pytest.raises(DomainError):
        await service.authorize(
            materialization_id,
            actor_id=actor_id,
            idempotency_key=key,
        )

    assert events == []
    assert repository.actor_lookups == []
    assert clock.calls == 0
    assert repository.create_calls == []
    assert registry.build_calls == 0
    assert registry.resolve_calls == []
    assert profiles.calls == []
    assert paper.calls == []
    assert materializations.calls == []


@pytest.mark.asyncio
async def test_normal_authorize_uses_exact_verification_order() -> None:
    materialization = _materialization()
    revision = _domain_profile_revision()
    expected = _activation(materialization)
    service, events, repository, materializations, paper, profiles, registry, clock = _dependencies(
        materialization=materialization,
        profile_revision=revision,
        create_result=expected,
    )

    result = await service.authorize(
        materialization.materialization_id,
        actor_id=ACTOR_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert result is expected
    assert events == [
        "actor_key_lookup",
        "materialization_get",
        "paper_load",
        "profile_revision_get",
        "registry_resolve",
        "clock",
        "activation_create",
    ]
    assert materializations.calls == [materialization.materialization_id]
    assert paper.calls == [materialization.session_id]
    assert profiles.calls == [
        (
            materialization.profile_binding.profile_id,
            materialization.profile_binding.approved_revision,
        )
    ]
    snapshot = revision.specification.strategy_snapshot
    assert registry.resolve_calls == [(snapshot.plugin_name, snapshot.plugin_version)]
    assert registry.build_calls == 0
    assert clock.calls == 1
    assert repository.create_calls == [
        (
            build_operational_paper_session_activation_specification(materialization),
            ACTOR_ID,
            IDEMPOTENCY_KEY,
            AUTHORIZED_AT,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("materialization", "expected_error"),
    (
        (None, OperationalPaperSessionMaterializationNotFoundError),
        (
            _materialization(state=OperationalPaperSessionMaterializationState.PREPARED),
            OperationalPaperSessionMaterializationStateTransitionConflictError,
        ),
    ),
)
async def test_materialization_failures_stop_before_filesystem(
    materialization: OperationalPaperSessionMaterialization | None,
    expected_error: type[DomainError],
) -> None:
    service, events, repository, materializations, paper, profiles, registry, clock = (
        _dependencies()
    )
    materializations.result = materialization

    with pytest.raises(expected_error):
        await service.authorize(
            MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == ["actor_key_lookup", "materialization_get"]
    assert repository.create_calls == []
    assert clock.calls == 0
    assert registry.build_calls == 0
    assert registry.resolve_calls == []
    assert profiles.calls == []
    assert paper.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_error"),
    (
        (
            PaperSessionNotFoundError(),
            OperationalPaperSessionMaterializationConfigIdentityConflictError,
        ),
        (PaperSessionCorruptError(), PaperSessionCorruptError),
    ),
)
async def test_filesystem_errors_preserve_required_boundary(
    error: Exception,
    expected_error: type[Exception],
) -> None:
    service, events, repository, materializations, paper, profiles, registry, clock = (
        _dependencies()
    )
    paper.error = error

    with pytest.raises(expected_error):
        await service.authorize(
            MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == ["actor_key_lookup", "materialization_get", "paper_load"]
    assert repository.create_calls == []
    assert clock.calls == 0
    assert registry.build_calls == 0
    assert registry.resolve_calls == []
    assert profiles.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ("session_id", "config_checksum"))
async def test_filesystem_identity_mismatch_fails_before_create(identity: str) -> None:
    changes = {identity: "f" * 64}
    materialization = _materialization(**changes)
    service, events, repository, materializations, paper, profiles, registry, clock = _dependencies(
        materialization=materialization
    )

    with pytest.raises(OperationalPaperSessionMaterializationConfigIdentityConflictError):
        await service.authorize(
            materialization.materialization_id,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == ["actor_key_lookup", "materialization_get", "paper_load"]
    assert repository.create_calls == []
    assert clock.calls == 0
    assert registry.build_calls == 0
    assert registry.resolve_calls == []
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_missing_exact_profile_revision_is_persistence_error() -> None:
    service, events, repository, materializations, paper, profiles, registry, clock = (
        _dependencies()
    )
    profiles.revision = None

    with pytest.raises(PersistenceError):
        await service.authorize(
            MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == [
        "actor_key_lookup",
        "materialization_get",
        "paper_load",
        "profile_revision_get",
    ]
    assert repository.create_calls == []
    assert clock.calls == 0
    assert registry.build_calls == 0
    assert registry.resolve_calls == []


@pytest.mark.asyncio
async def test_internally_inconsistent_profile_binding_is_persistence_error() -> None:
    revision = replace(_domain_profile_revision(), profile_id=uuid4())
    service, events, repository, materializations, paper, profiles, registry, clock = (
        _dependencies()
    )
    profiles.revision = revision

    with pytest.raises(PersistenceError):
        await service.authorize(
            MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == [
        "actor_key_lookup",
        "materialization_get",
        "paper_load",
        "profile_revision_get",
    ]
    assert repository.create_calls == []
    assert clock.calls == 0
    assert registry.build_calls == 0
    assert registry.resolve_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ("name", "version", "parameters", "lifecycle"))
async def test_snapshot_config_mismatch_fails_before_plugin_resolution(mismatch: str) -> None:
    snapshot_changes: dict[str, object] = {}
    profile_changes: dict[str, object] = {}
    if mismatch == "name":
        snapshot_changes["plugin_name"] = "other-plugin"
    elif mismatch == "version":
        snapshot_changes["plugin_version"] = "other-version"
    elif mismatch == "parameters":
        snapshot_changes["parameters"] = (
            ("fast", 13),
            ("ratio", _plan().config.strategy.parameters[1][1]),
        )
    else:
        snapshot_changes["strategy_lifecycle_version"] = 1
        profile_changes["warmup_candles"] = 0
    revision = _domain_profile_revision(
        strategy_snapshot=_snapshot(**snapshot_changes),
        **profile_changes,
    )
    materialization = _materialization(profile_revision=revision)
    service, events, repository, materializations, paper, profiles, registry, clock = _dependencies(
        materialization=materialization,
        profile_revision=revision,
    )

    with pytest.raises(OperationalPaperSessionMaterializationConfigIdentityConflictError):
        await service.authorize(
            MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == [
        "actor_key_lookup",
        "materialization_get",
        "paper_load",
        "profile_revision_get",
    ]
    assert registry.resolve_calls == []
    assert registry.build_calls == 0
    assert repository.create_calls == []
    assert clock.calls == 0


def _typed_parameter_evidence(
    snapshot_value: StrategyParameterValue,
    config_value: StrategyParameterValue,
) -> tuple[
    OperationalPaperSessionProfileRevision,
    PaperSessionConfig,
    OperationalPaperSessionMaterialization,
]:
    revision = _domain_profile_revision(
        strategy_snapshot=_snapshot(parameters=(("fast", snapshot_value),)),
    )
    base = _plan().config
    config = replace(
        base,
        strategy=StrategyDescriptor(
            base.strategy.name,
            base.strategy.version,
            (("fast", config_value),),
        ),
    )
    materialization = _materialization(
        profile_revision=revision,
        session_id=paper_session_id(config),
        config_checksum=paper_config_checksum(config),
    )
    return revision, config, materialization


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot_value", "config_value"),
    (
        pytest.param(Decimal("12"), 12, id="decimal-vs-integer"),
        pytest.param(True, 1, id="boolean-vs-integer"),
        pytest.param(Decimal("12"), "12", id="decimal-vs-string"),
    ),
)
async def test_typed_parameter_collision_fails_at_snapshot_config_comparison(
    snapshot_value: StrategyParameterValue,
    config_value: StrategyParameterValue,
) -> None:
    revision, config, materialization = _typed_parameter_evidence(snapshot_value, config_value)
    service, events, repository, _, paper, profiles, registry, clock = _dependencies(
        materialization=materialization,
        config=config,
        profile_revision=revision,
    )

    with pytest.raises(OperationalPaperSessionMaterializationConfigIdentityConflictError):
        await service.authorize(
            MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == [
        "actor_key_lookup",
        "materialization_get",
        "paper_load",
        "profile_revision_get",
    ]
    assert paper.calls == [materialization.session_id]
    assert profiles.calls == [(revision.profile_id, revision.revision)]
    assert registry.resolve_calls == []
    assert registry.build_calls == 0
    assert clock.calls == 0
    assert repository.create_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", (12, Decimal("12"), True, "12", None))
async def test_matching_typed_parameters_allow_normal_authorize(
    value: StrategyParameterValue,
) -> None:
    revision, config, materialization = _typed_parameter_evidence(value, value)
    service, events, repository, *_, registry, clock = _dependencies(
        materialization=materialization,
        config=config,
        profile_revision=revision,
    )

    result = await service.authorize(
        MATERIALIZATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert result is repository.create_result
    assert events == [
        "actor_key_lookup",
        "materialization_get",
        "paper_load",
        "profile_revision_get",
        "registry_resolve",
        "clock",
        "activation_create",
    ]
    assert len(repository.create_calls) == 1
    snapshot = revision.specification.strategy_snapshot
    assert registry.resolve_calls == [(snapshot.plugin_name, snapshot.plugin_version)]
    assert registry.build_calls == 0
    assert clock.calls == 1


@pytest.mark.asyncio
async def test_missing_frozen_plugin_is_compatibility_error_without_build() -> None:
    service, events, repository, materializations, paper, profiles, registry, clock = _dependencies(
        registry_error=StrategyPluginNotFoundError(),
    )

    with pytest.raises(StrategyDefinitionCompatibilityError):
        await service.authorize(
            MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == [
        "actor_key_lookup",
        "materialization_get",
        "paper_load",
        "profile_revision_get",
        "registry_resolve",
    ]
    assert registry.build_calls == 0
    assert repository.create_calls == []
    assert clock.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("name", "other-plugin"),
        ("version", "other-version"),
        ("schema_version", 2),
        ("lifecycle_version", 1),
    ),
)
async def test_resolved_descriptor_mismatch_is_compatibility_error(
    field: str,
    value: object,
) -> None:
    revision = _domain_profile_revision()
    service, events, repository, materializations, paper, profiles, registry, clock = _dependencies(
        descriptor=_descriptor(revision, **{field: value}),
    )

    with pytest.raises(StrategyDefinitionCompatibilityError):
        await service.authorize(
            MATERIALIZATION_ID,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert events == [
        "actor_key_lookup",
        "materialization_get",
        "paper_load",
        "profile_revision_get",
        "registry_resolve",
    ]
    assert registry.build_calls == 0
    assert repository.create_calls == []
    assert clock.calls == 0


@pytest.mark.asyncio
async def test_repository_create_race_result_is_returned_without_third_lookup() -> None:
    materialization = _materialization()
    race_result = _activation(
        materialization,
        state=OperationalPaperSessionActivationState.REVOKED,
    )
    service, events, repository, *_, registry, clock = _dependencies(
        materialization=materialization,
        create_result=race_result,
    )

    result = await service.authorize(
        MATERIALIZATION_ID,
        actor_id=ACTOR_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert result is race_result
    assert repository.actor_lookups == [(ACTOR_ID, IDEMPOTENCY_KEY)]
    assert len(repository.create_calls) == 1
    assert clock.calls == 1
    assert registry.build_calls == 0


@pytest.mark.asyncio
async def test_revoke_delegates_exact_args_without_eligibility_checks() -> None:
    authorized = _activation(_materialization())
    revoked = revoke_operational_paper_session_activation(
        authorized,
        revoked_by=ACTOR_ID,
        revoked_at=AUTHORIZED_AT + timedelta(minutes=1),
    )
    service, events, repository, *_, registry, clock = _dependencies()
    repository.revoke_result = revoked

    result = await service.revoke(
        authorized.activation_id,
        expected_record_version=authorized.record_version,
        actor_id=ACTOR_ID,
    )

    assert result is revoked
    assert events == ["clock", "activation_revoke"]
    assert repository.revoke_calls == [
        (authorized.activation_id, authorized.record_version, ACTOR_ID, AUTHORIZED_AT)
    ]
    assert clock.calls == 1
    assert registry.build_calls == 0
