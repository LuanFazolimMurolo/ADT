"""Application-service tests for operational paper-session profiles."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from app.backtesting.domain import StrategyParameters
from app.backtesting.strategy import BacktestStrategy
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
from app.services.operational_paper_session_profiles import (
    OperationalPaperSessionProfileCurrent,
    OperationalPaperSessionProfilePage,
    OperationalPaperSessionProfileRevisionPage,
    OperationalPaperSessionProfileService,
    OperationalPaperSessionProfileStrategyResolver,
)
from app.strategies.definitions import (
    StoredStrategyParameter,
    StrategyDefinition,
    StrategyDefinitionSpec,
    StrategyDefinitionState,
    StrategyParameterDocument,
    strategy_parameter_checksum,
)
from app.strategies.domain import (
    IndicatorCapability,
    RawStrategyParameters,
    StrategyParameterKind,
    StrategyParameterSpec,
    StrategyPluginDescriptor,
)
from app.strategies.errors import StrategyDefinitionCompatibilityError
from app.strategies.protocols import StrategyPlugin
from app.strategies.registry import StrategyPluginRegistry

PROFILE_ID = UUID("10000000-0000-4000-8000-000000000001")
STRATEGY_DEFINITION_ID = UUID("20000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("30000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
IDEMPOTENCY_KEY = "gate-2e-profile-create"
CHECKSUM = "a" * 64

PROFILE = cast(OperationalPaperSessionProfile, object())
REVISION = cast(OperationalPaperSessionProfileRevision, object())
INTENT = cast(OperationalPaperSessionProfileCreateIntent, object())
CURRENT: OperationalPaperSessionProfileCurrent = (PROFILE, REVISION)
CURRENT_PAGE: OperationalPaperSessionProfilePage = ([CURRENT], 1)
REVISION_PAGE: OperationalPaperSessionProfileRevisionPage = ([REVISION], 1)

DESCRIPTOR = StrategyPluginDescriptor(
    name="gate-2e-test",
    version="1.0.0",
    description="Gate 2E resolver test plugin",
    parameters=(
        StrategyParameterSpec(
            "threshold",
            StrategyParameterKind.DECIMAL,
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        ),
        StrategyParameterSpec("period", StrategyParameterKind.INTEGER, minimum=1, maximum=100),
        StrategyParameterSpec("enabled", StrategyParameterKind.BOOLEAN),
        StrategyParameterSpec("label", StrategyParameterKind.STRING),
    ),
)
EXPECTED_PARAMETERS: StrategyParameters = (
    ("enabled", True),
    ("label", "paper"),
    ("period", 5),
    ("threshold", Decimal("0.250")),
)


def _document(
    *,
    threshold_kind: StrategyParameterKind = StrategyParameterKind.DECIMAL,
    threshold_value: str = "0.250",
) -> StrategyParameterDocument:
    return (
        StoredStrategyParameter("threshold", threshold_kind, threshold_value),
        StoredStrategyParameter("period", StrategyParameterKind.INTEGER, 5),
        StoredStrategyParameter("enabled", StrategyParameterKind.BOOLEAN, True),
        StoredStrategyParameter("label", StrategyParameterKind.STRING, "paper"),
    )


def _definition(
    *,
    plugin_name: str = DESCRIPTOR.name,
    plugin_schema_version: int = DESCRIPTOR.schema_version,
    lifecycle_version: int = DESCRIPTOR.lifecycle_version,
    parameters: StrategyParameterDocument | None = None,
) -> StrategyDefinition:
    document = _document() if parameters is None else parameters
    return StrategyDefinition(
        id=STRATEGY_DEFINITION_ID,
        spec=StrategyDefinitionSpec(
            display_name="Gate 2E strategy",
            plugin_name=plugin_name,
            plugin_version=DESCRIPTOR.version,
            plugin_schema_version=plugin_schema_version,
            lifecycle_version=lifecycle_version,
            parameters=document,
            parameters_checksum=strategy_parameter_checksum(document),
        ),
        state=StrategyDefinitionState.ACTIVE,
        revision=3,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=NOW,
        updated_at=NOW,
    )


class RecordingClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.value


class NoBuildPlugin:
    def __init__(self, descriptor: StrategyPluginDescriptor = DESCRIPTOR) -> None:
        self.descriptor = descriptor
        self.build_calls = 0

    def build(self, parameters: StrategyParameters) -> BacktestStrategy:
        self.build_calls += 1
        raise AssertionError(f"plugin.build must not run: {parameters!r}")


class RecordingRegistry(StrategyPluginRegistry):
    def __init__(
        self,
        plugin: NoBuildPlugin,
        *,
        explode_on_resolve: bool = False,
    ) -> None:
        super().__init__((plugin,))
        self.resolve_calls: list[tuple[str, str]] = []
        self.build_calls = 0
        self.explode_on_resolve = explode_on_resolve

    def resolve(self, name: str, version: str) -> StrategyPlugin:
        self.resolve_calls.append((name, version))
        if self.explode_on_resolve:
            raise AssertionError("registry.resolve must not run")
        return super().resolve(name, version)

    def build(
        self,
        name: str,
        version: str,
        parameters: RawStrategyParameters,
        *,
        available_indicators: tuple[IndicatorCapability, ...],
    ) -> BacktestStrategy:
        self.build_calls += 1
        raise AssertionError(
            f"registry.build must not run: {name}@{version} {parameters!r} {available_indicators!r}"
        )


class RecordingRepository(PostgresOperationalPaperSessionProfileRepository):
    def __init__(self, definition: StrategyDefinition | None = None) -> None:
        self.definition = definition or _definition()
        self.resolve_on: set[str] = set()
        self.resolved_parameters: dict[str, StrategyParameters] = {}

        self.current_result: OperationalPaperSessionProfileCurrent | None = CURRENT
        self.revision_result: OperationalPaperSessionProfileRevision | None = REVISION
        self.current_page_result = CURRENT_PAGE
        self.revision_page_result = REVISION_PAGE
        self.create_result = CURRENT
        self.replace_result = CURRENT
        self.approve_result = PROFILE
        self.archive_result = PROFILE

        self.list_current_calls: list[
            tuple[int, int, OperationalPaperSessionProfileState | None]
        ] = []
        self.get_current_calls: list[UUID] = []
        self.list_revision_calls: list[tuple[UUID, int, int]] = []
        self.get_revision_calls: list[tuple[UUID, int]] = []
        self.create_calls: list[
            tuple[
                OperationalPaperSessionProfileCreateIntent,
                UUID,
                str,
                datetime,
                OperationalPaperSessionProfileStrategyResolver,
            ]
        ] = []
        self.replace_calls: list[
            tuple[
                UUID,
                OperationalPaperSessionProfileCreateIntent,
                int,
                int,
                UUID,
                datetime,
                OperationalPaperSessionProfileStrategyResolver,
            ]
        ] = []
        self.approve_calls: list[
            tuple[
                UUID,
                int,
                str,
                int,
                UUID,
                datetime,
                OperationalPaperSessionProfileStrategyResolver,
            ]
        ] = []
        self.archive_calls: list[tuple[UUID, int, UUID, datetime]] = []

    def _maybe_resolve(
        self,
        method: str,
        resolver: OperationalPaperSessionProfileStrategyResolver,
    ) -> None:
        if method in self.resolve_on:
            self.resolved_parameters[method] = resolver(self.definition)

    async def list_current(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionProfileState | None = None,
    ) -> OperationalPaperSessionProfilePage:
        self.list_current_calls.append((limit, offset, state))
        return self.current_page_result

    async def get_current(
        self,
        profile_id: UUID,
    ) -> OperationalPaperSessionProfileCurrent | None:
        self.get_current_calls.append(profile_id)
        return self.current_result

    async def list_revisions(
        self,
        profile_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> OperationalPaperSessionProfileRevisionPage:
        self.list_revision_calls.append((profile_id, limit, offset))
        return self.revision_page_result

    async def get_revision(
        self,
        profile_id: UUID,
        revision: int,
    ) -> OperationalPaperSessionProfileRevision | None:
        self.get_revision_calls.append((profile_id, revision))
        return self.revision_result

    async def create(
        self,
        intent: OperationalPaperSessionProfileCreateIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
        strategy_resolver: OperationalPaperSessionProfileStrategyResolver,
    ) -> OperationalPaperSessionProfileCurrent:
        self.create_calls.append((intent, actor_id, idempotency_key, now, strategy_resolver))
        self._maybe_resolve("create", strategy_resolver)
        return self.create_result

    async def replace_draft(
        self,
        profile_id: UUID,
        intent: OperationalPaperSessionProfileCreateIntent,
        *,
        expected_revision: int,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
        strategy_resolver: OperationalPaperSessionProfileStrategyResolver,
    ) -> OperationalPaperSessionProfileCurrent:
        self.replace_calls.append(
            (
                profile_id,
                intent,
                expected_revision,
                expected_record_version,
                actor_id,
                now,
                strategy_resolver,
            )
        )
        self._maybe_resolve("replace_draft", strategy_resolver)
        return self.replace_result

    async def approve(
        self,
        profile_id: UUID,
        *,
        expected_revision: int,
        expected_checksum: str,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
        strategy_resolver: OperationalPaperSessionProfileStrategyResolver,
    ) -> OperationalPaperSessionProfile:
        self.approve_calls.append(
            (
                profile_id,
                expected_revision,
                expected_checksum,
                expected_record_version,
                actor_id,
                now,
                strategy_resolver,
            )
        )
        self._maybe_resolve("approve", strategy_resolver)
        return self.approve_result

    async def archive(
        self,
        profile_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionProfile:
        self.archive_calls.append((profile_id, expected_record_version, actor_id, now))
        return self.archive_result


def _service(
    *,
    definition: StrategyDefinition | None = None,
    explode_on_resolve: bool = False,
) -> tuple[
    OperationalPaperSessionProfileService,
    RecordingRepository,
    RecordingRegistry,
    NoBuildPlugin,
    RecordingClock,
]:
    plugin = NoBuildPlugin()
    registry = RecordingRegistry(plugin, explode_on_resolve=explode_on_resolve)
    repository = RecordingRepository(definition)
    clock = RecordingClock()
    return (
        OperationalPaperSessionProfileService(
            repository=repository,
            registry=registry,
            clock=clock,
        ),
        repository,
        registry,
        plugin,
        clock,
    )


@pytest.mark.parametrize(
    "state",
    [None, OperationalPaperSessionProfileState.DRAFT],
)
async def test_list_forwards_limit_offset_and_typed_state_without_authority_reads(
    state: OperationalPaperSessionProfileState | None,
) -> None:
    service, repository, registry, _plugin, clock = _service(explode_on_resolve=True)

    result = await service.list(limit=17, offset=4, state=state)

    assert result is CURRENT_PAGE
    assert repository.list_current_calls == [(17, 4, state)]
    assert registry.resolve_calls == []
    assert clock.calls == 0


async def test_get_forwards_identity_and_maps_none_to_stable_not_found() -> None:
    service, repository, registry, _plugin, clock = _service(explode_on_resolve=True)

    assert await service.get(PROFILE_ID) is CURRENT
    repository.current_result = None
    with pytest.raises(OperationalPaperSessionProfileNotFoundError):
        await service.get(PROFILE_ID)

    assert repository.get_current_calls == [PROFILE_ID, PROFILE_ID]
    assert registry.resolve_calls == []
    assert clock.calls == 0


async def test_list_revisions_forwards_exact_bounded_repository_contract() -> None:
    service, repository, registry, _plugin, clock = _service(explode_on_resolve=True)

    result = await service.list_revisions(PROFILE_ID, limit=9, offset=2)

    assert result is REVISION_PAGE
    assert repository.list_revision_calls == [(PROFILE_ID, 9, 2)]
    assert registry.resolve_calls == []
    assert clock.calls == 0


async def test_get_revision_forwards_identity_and_maps_none_to_stable_not_found() -> None:
    service, repository, registry, _plugin, clock = _service(explode_on_resolve=True)

    assert await service.get_revision(PROFILE_ID, 3) is REVISION
    repository.revision_result = None
    with pytest.raises(OperationalPaperSessionProfileNotFoundError):
        await service.get_revision(PROFILE_ID, 99)

    assert repository.get_revision_calls == [(PROFILE_ID, 3), (PROFILE_ID, 99)]
    assert registry.resolve_calls == []
    assert clock.calls == 0


async def test_create_replay_forwards_callback_without_eager_resolution() -> None:
    service, repository, registry, plugin, clock = _service(explode_on_resolve=True)

    result = await service.create(
        INTENT,
        actor_id=ACTOR_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert result is CURRENT
    assert len(repository.create_calls) == 1
    intent, actor_id, key, now, resolver = repository.create_calls[0]
    assert intent is INTENT
    assert actor_id == ACTOR_ID
    assert key == IDEMPOTENCY_KEY
    assert now == NOW
    assert callable(resolver)
    assert not inspect.iscoroutinefunction(resolver)
    assert registry.resolve_calls == []
    assert registry.build_calls == plugin.build_calls == 0
    assert clock.calls == 1


async def test_create_new_intent_resolves_and_decodes_without_building() -> None:
    service, repository, registry, plugin, clock = _service()
    repository.resolve_on.add("create")

    result = await service.create(
        INTENT,
        actor_id=ACTOR_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert result is CURRENT
    assert repository.resolved_parameters == {"create": EXPECTED_PARAMETERS}
    threshold = dict(repository.resolved_parameters["create"])["threshold"]
    assert isinstance(threshold, Decimal)
    assert threshold.as_tuple() == Decimal("0.250").as_tuple()
    assert registry.resolve_calls == [(DESCRIPTOR.name, DESCRIPTOR.version)]
    assert registry.build_calls == plugin.build_calls == 0
    assert clock.calls == 1


async def test_replace_noop_forwards_tokens_without_eager_resolution() -> None:
    service, repository, registry, plugin, clock = _service(explode_on_resolve=True)

    result = await service.replace_draft(
        PROFILE_ID,
        INTENT,
        expected_revision=3,
        expected_record_version=7,
        actor_id=ACTOR_ID,
    )

    assert result is CURRENT
    assert len(repository.replace_calls) == 1
    profile_id, intent, revision, record_version, actor_id, now, resolver = (
        repository.replace_calls[0]
    )
    assert (profile_id, intent, revision, record_version, actor_id, now) == (
        PROFILE_ID,
        INTENT,
        3,
        7,
        ACTOR_ID,
        NOW,
    )
    assert callable(resolver)
    assert not inspect.iscoroutinefunction(resolver)
    assert registry.resolve_calls == []
    assert registry.build_calls == plugin.build_calls == 0
    assert clock.calls == 1


async def test_replace_changed_path_resolves_without_building() -> None:
    service, repository, registry, plugin, clock = _service()
    repository.resolve_on.add("replace_draft")

    result = await service.replace_draft(
        PROFILE_ID,
        INTENT,
        expected_revision=3,
        expected_record_version=7,
        actor_id=ACTOR_ID,
    )

    assert result is CURRENT
    assert repository.resolved_parameters == {"replace_draft": EXPECTED_PARAMETERS}
    assert registry.resolve_calls == [(DESCRIPTOR.name, DESCRIPTOR.version)]
    assert registry.build_calls == plugin.build_calls == 0
    assert clock.calls == 1


async def test_approved_retry_forwards_callback_without_eager_resolution() -> None:
    service, repository, registry, plugin, clock = _service(explode_on_resolve=True)

    result = await service.approve(
        PROFILE_ID,
        expected_revision=3,
        expected_checksum=CHECKSUM,
        expected_record_version=7,
        actor_id=ACTOR_ID,
    )

    assert result is PROFILE
    assert len(repository.approve_calls) == 1
    profile_id, revision, checksum, record_version, actor_id, now, resolver = (
        repository.approve_calls[0]
    )
    assert (profile_id, revision, checksum, record_version, actor_id, now) == (
        PROFILE_ID,
        3,
        CHECKSUM,
        7,
        ACTOR_ID,
        NOW,
    )
    assert callable(resolver)
    assert not inspect.iscoroutinefunction(resolver)
    assert registry.resolve_calls == []
    assert registry.build_calls == plugin.build_calls == 0
    assert clock.calls == 1


async def test_normal_approval_resolves_without_building() -> None:
    service, repository, registry, plugin, clock = _service()
    repository.resolve_on.add("approve")

    result = await service.approve(
        PROFILE_ID,
        expected_revision=3,
        expected_checksum=CHECKSUM,
        expected_record_version=7,
        actor_id=ACTOR_ID,
    )

    assert result is PROFILE
    assert repository.resolved_parameters == {"approve": EXPECTED_PARAMETERS}
    assert registry.resolve_calls == [(DESCRIPTOR.name, DESCRIPTOR.version)]
    assert registry.build_calls == plugin.build_calls == 0
    assert clock.calls == 1


async def test_archive_forwards_exact_tokens_and_never_consults_registry() -> None:
    service, repository, registry, plugin, clock = _service(explode_on_resolve=True)

    result = await service.archive(
        PROFILE_ID,
        expected_record_version=7,
        actor_id=ACTOR_ID,
    )

    assert result is PROFILE
    assert repository.archive_calls == [(PROFILE_ID, 7, ACTOR_ID, NOW)]
    assert registry.resolve_calls == []
    assert registry.build_calls == plugin.build_calls == 0
    assert clock.calls == 1


@pytest.mark.parametrize(
    "definition",
    [
        _definition(plugin_name="missing-plugin"),
        _definition(plugin_schema_version=2),
        _definition(lifecycle_version=2),
        _definition(
            parameters=_document(
                threshold_kind=StrategyParameterKind.STRING,
                threshold_value="0.250",
            )
        ),
        _definition(parameters=_document(threshold_value="not-decimal")),
        _definition(parameters=_document(threshold_value="2")),
    ],
    ids=[
        "missing-plugin",
        "schema-mismatch",
        "lifecycle-mismatch",
        "parameter-kind",
        "malformed-decimal",
        "normalized-range",
    ],
)
async def test_resolver_failures_map_to_stable_compatibility_error(
    definition: StrategyDefinition,
) -> None:
    service, repository, registry, plugin, clock = _service(definition=definition)
    repository.resolve_on.add("create")

    with pytest.raises(StrategyDefinitionCompatibilityError):
        await service.create(
            INTENT,
            actor_id=ACTOR_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert registry.build_calls == plugin.build_calls == 0
    assert clock.calls == 1


async def test_service_forwards_repository_native_values_without_coercion() -> None:
    service, repository, _registry, _plugin, _clock = _service()
    raw_limit = "17"
    raw_offset = "4"
    raw_state = "DRAFT"
    raw_revision = "3"
    raw_record_version = "7"

    await service.list(
        limit=cast(int, raw_limit),
        offset=cast(int, raw_offset),
        state=cast(OperationalPaperSessionProfileState, raw_state),
    )
    await service.replace_draft(
        PROFILE_ID,
        INTENT,
        expected_revision=cast(int, raw_revision),
        expected_record_version=cast(int, raw_record_version),
        actor_id=ACTOR_ID,
    )

    assert cast(tuple[object, ...], repository.list_current_calls[-1]) == (
        raw_limit,
        raw_offset,
        raw_state,
    )
    assert cast(tuple[object, ...], repository.replace_calls[-1][2:4]) == (
        raw_revision,
        raw_record_version,
    )
