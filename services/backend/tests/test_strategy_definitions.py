"""Tests for versioned strategy-definition documents and CRUD orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from app.strategies.builtins import EmaCrossExamplePlugin, NoOpStrategyPlugin
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.definitions import (
    StoredStrategyParameter,
    StrategyDefinition,
    StrategyDefinitionRepository,
    StrategyDefinitionService,
    StrategyDefinitionSpec,
    StrategyDefinitionState,
    decode_strategy_parameters,
    encode_strategy_parameters,
    strategy_parameter_checksum,
    strategy_parameter_document_from_json,
    strategy_parameter_document_to_json,
)
from app.strategies.domain import StrategyParameterKind
from app.strategies.errors import (
    InvalidStrategyDefinitionError,
    StrategyDefinitionArchivedError,
    StrategyDefinitionCompatibilityError,
    StrategyDefinitionNotFoundError,
)
from app.strategies.registry import StrategyPluginRegistry

DEFINITION_ID = UUID("10000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("20000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)


class FakeRepository(StrategyDefinitionRepository):
    def __init__(self) -> None:
        self.definition: StrategyDefinition | None = None
        self.replace_revision: int | None = None
        self.archive_revision: int | None = None

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        include_archived: bool,
    ) -> tuple[list[StrategyDefinition], int]:
        del limit, offset
        if self.definition is None:
            return [], 0
        if self.definition.state is StrategyDefinitionState.ARCHIVED and not include_archived:
            return [], 0
        return [self.definition], 1

    async def get(self, definition_id: UUID) -> StrategyDefinition | None:
        assert definition_id == DEFINITION_ID
        return self.definition

    async def create(
        self,
        spec: StrategyDefinitionSpec,
        *,
        actor_id: UUID,
    ) -> StrategyDefinition:
        self.definition = StrategyDefinition(
            id=DEFINITION_ID,
            spec=spec,
            state=StrategyDefinitionState.ACTIVE,
            revision=1,
            created_by=actor_id,
            updated_by=actor_id,
            created_at=NOW,
            updated_at=NOW,
        )
        return self.definition

    async def replace(
        self,
        definition_id: UUID,
        spec: StrategyDefinitionSpec,
        *,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition:
        assert definition_id == DEFINITION_ID
        assert self.definition is not None
        self.replace_revision = expected_revision
        self.definition = replace(
            self.definition,
            spec=spec,
            revision=self.definition.revision + 1,
            updated_by=actor_id,
        )
        return self.definition

    async def archive(
        self,
        definition_id: UUID,
        *,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition:
        assert definition_id == DEFINITION_ID
        assert self.definition is not None
        self.archive_revision = expected_revision
        self.definition = replace(
            self.definition,
            state=StrategyDefinitionState.ARCHIVED,
            revision=self.definition.revision + 1,
            updated_by=actor_id,
            archived_at=NOW,
        )
        return self.definition


def _service(repository: FakeRepository) -> StrategyDefinitionService:
    return StrategyDefinitionService(
        repository,
        registry=StrategyPluginRegistry((NoOpStrategyPlugin(), EmaCrossExamplePlugin())),
        available_indicators=builtin_indicator_capabilities(),
    )


def test_lossless_document_distinguishes_decimal_from_string() -> None:
    plugin = EmaCrossExamplePlugin().descriptor
    normalized = plugin.normalize_parameters(
        {"fast_period": 3, "slow_period": 5, "quantity": Decimal("1.2500")}
    )

    document = encode_strategy_parameters(plugin, normalized)

    assert document[-1] == StoredStrategyParameter("slow_period", StrategyParameterKind.INTEGER, 5)
    quantity = next(item for item in document if item.name == "quantity")
    assert quantity.kind is StrategyParameterKind.DECIMAL
    assert quantity.value == "1.25"
    assert decode_strategy_parameters(plugin, document) == (
        ("fast_period", 3),
        ("quantity", Decimal("1.25")),
        ("slow_period", 5),
    )


def test_json_round_trip_is_strict_and_checksum_is_stable() -> None:
    document = (
        StoredStrategyParameter("enabled", StrategyParameterKind.BOOLEAN, True),
        StoredStrategyParameter("label", StrategyParameterKind.STRING, "demo"),
    )
    payload = strategy_parameter_document_to_json(document)

    assert strategy_parameter_document_from_json(payload) == document
    assert strategy_parameter_checksum(document) == strategy_parameter_checksum(
        tuple(reversed(document))
    )
    with pytest.raises(InvalidStrategyDefinitionError):
        strategy_parameter_document_from_json(
            {"enabled": {"kind": "boolean", "value": True, "extra": "forbidden"}}
        )


def test_decimal_document_is_independent_from_ambient_context() -> None:
    plugin = EmaCrossExamplePlugin().descriptor
    value = Decimal("1.23456789012345678901234567890123456789")

    with localcontext() as context:
        context.prec = 8
        low_precision = encode_strategy_parameters(
            plugin,
            plugin.normalize_parameters({"quantity": value}),
        )
    with localcontext() as context:
        context.prec = 50
        high_precision = encode_strategy_parameters(
            plugin,
            plugin.normalize_parameters({"quantity": value}),
        )

    assert low_precision == high_precision
    quantity = next(item for item in low_precision if item.name == "quantity")
    assert quantity.value == "1.23456789012345678901234567890123456789"


def test_spec_rejects_checksum_mismatch() -> None:
    with pytest.raises(InvalidStrategyDefinitionError):
        StrategyDefinitionSpec(
            display_name="Demo",
            plugin_name="no-op",
            plugin_version="1",
            plugin_schema_version=1,
            lifecycle_version=1,
            parameters=(),
            parameters_checksum="0" * 64,
        )


@pytest.mark.asyncio
async def test_create_normalizes_defaults_and_builds_fresh_state() -> None:
    repository = FakeRepository()
    service = _service(repository)

    created = await service.create(
        display_name="  EMA demo  ",
        plugin_name="ema-cross-example",
        plugin_version="1",
        parameters={"quantity": Decimal("0.5")},
        actor_id=ACTOR_ID,
    )
    first = await service.build(created.id)
    second = await service.build(created.id)

    assert created.spec.display_name == "EMA demo"
    assert created.spec.parameters_checksum == strategy_parameter_checksum(created.spec.parameters)
    assert first.descriptor.name == "ema-cross-example"
    assert first is not second
    assert first.descriptor.parameters == (
        ("fast_period", 3),
        ("quantity", Decimal("0.5")),
        ("slow_period", 5),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters",
    [
        {"quantity": Decimal("0")},
        {
            "fast_period": 5,
            "slow_period": 3,
            "quantity": Decimal("1"),
        },
    ],
)
async def test_create_rejects_factory_level_parameter_invariants(
    parameters: dict[str, object],
) -> None:
    repository = FakeRepository()

    with pytest.raises(InvalidStrategyDefinitionError):
        await _service(repository).create(
            display_name="Invalid EMA demo",
            plugin_name="ema-cross-example",
            plugin_version="1",
            parameters=parameters,
            actor_id=ACTOR_ID,
        )

    assert repository.definition is None


@pytest.mark.asyncio
async def test_persisted_factory_invalid_parameters_are_incompatible() -> None:
    repository = FakeRepository()
    service = _service(repository)
    created = await service.create(
        display_name="EMA demo",
        plugin_name="ema-cross-example",
        plugin_version="1",
        parameters={"quantity": Decimal("1")},
        actor_id=ACTOR_ID,
    )
    plugin = EmaCrossExamplePlugin().descriptor
    invalid_document = encode_strategy_parameters(
        plugin,
        plugin.normalize_parameters({"quantity": Decimal("0")}),
    )
    repository.definition = replace(
        created,
        spec=replace(
            created.spec,
            parameters=invalid_document,
            parameters_checksum=strategy_parameter_checksum(invalid_document),
        ),
    )

    with pytest.raises(StrategyDefinitionCompatibilityError):
        await service.get(created.id)


@pytest.mark.asyncio
async def test_get_missing_definition_raises_safe_not_found() -> None:
    with pytest.raises(StrategyDefinitionNotFoundError):
        await _service(FakeRepository()).get(DEFINITION_ID)


@pytest.mark.asyncio
async def test_replace_passes_expected_revision_and_increments_record() -> None:
    repository = FakeRepository()
    service = _service(repository)
    created = await service.create(
        display_name="Demo",
        plugin_name="no-op",
        plugin_version="1",
        parameters={},
        actor_id=ACTOR_ID,
    )

    updated = await service.replace(
        created.id,
        display_name="Demo 2",
        plugin_name="ema-cross-example",
        plugin_version="1",
        parameters={"quantity": Decimal("1")},
        expected_revision=1,
        actor_id=ACTOR_ID,
    )

    assert repository.replace_revision == 1
    assert updated.revision == 2
    assert updated.spec.display_name == "Demo 2"


@pytest.mark.asyncio
async def test_archive_is_one_way_and_blocks_build_or_replace() -> None:
    repository = FakeRepository()
    service = _service(repository)
    created = await service.create(
        display_name="Demo",
        plugin_name="no-op",
        plugin_version="1",
        parameters={},
        actor_id=ACTOR_ID,
    )

    archived = await service.archive(
        created.id,
        expected_revision=1,
        actor_id=ACTOR_ID,
    )

    assert archived.state is StrategyDefinitionState.ARCHIVED
    assert repository.archive_revision == 1
    with pytest.raises(StrategyDefinitionArchivedError):
        await service.build(created.id)
    with pytest.raises(StrategyDefinitionArchivedError):
        await service.replace(
            created.id,
            display_name="x",
            plugin_name="no-op",
            plugin_version="1",
            parameters={},
            expected_revision=2,
            actor_id=ACTOR_ID,
        )


@pytest.mark.asyncio
async def test_persisted_future_schema_is_rejected_before_runtime() -> None:
    repository = FakeRepository()
    service = _service(repository)
    created = await service.create(
        display_name="Demo",
        plugin_name="no-op",
        plugin_version="1",
        parameters={},
        actor_id=ACTOR_ID,
    )
    repository.definition = replace(
        created,
        spec=replace(created.spec, plugin_schema_version=2),
    )

    with pytest.raises(StrategyDefinitionCompatibilityError):
        await service.get(created.id)


@pytest.mark.asyncio
async def test_list_is_bounded_and_validates_every_record() -> None:
    repository = FakeRepository()
    service = _service(repository)
    await service.create(
        display_name="Demo",
        plugin_name="no-op",
        plugin_version="1",
        parameters={},
        actor_id=ACTOR_ID,
    )

    items, total = await service.list(limit=100, offset=0)

    assert len(items) == total == 1
    with pytest.raises(InvalidStrategyDefinitionError):
        await service.list(limit=101, offset=0)
