"""PostgreSQL integration tests for revisioned strategy definitions."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest

from app.database import Database
from app.repositories import PostgresStrategyDefinitionRepository
from app.strategies import StrategyDefinitionService, StrategyDefinitionState
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.errors import (
    StrategyDefinitionArchivedError,
    StrategyDefinitionNameConflictError,
    StrategyDefinitionRevisionConflictError,
)


def _service(database: Database) -> StrategyDefinitionService:
    return StrategyDefinitionService(
        PostgresStrategyDefinitionRepository(database),
        available_indicators=builtin_indicator_capabilities(),
    )


@pytest.mark.asyncio
async def test_repository_round_trip_preserves_decimal_and_revision(
    database: Database,
    auth_user_id: UUID,
) -> None:
    service = _service(database)

    created = await service.create(
        display_name="EMA persistida",
        plugin_name="ema-cross-example",
        plugin_version="1",
        parameters={"quantity": Decimal("1.2500")},
        actor_id=auth_user_id,
    )
    loaded = await service.get(created.id)

    assert loaded == created
    assert loaded.revision == 1

    stored_parameters = {parameter.name: parameter for parameter in loaded.spec.parameters}
    assert stored_parameters["quantity"].value == "1.25"


@pytest.mark.asyncio
async def test_normalized_display_name_is_unique(
    database: Database,
    auth_user_id: UUID,
) -> None:
    service = _service(database)
    await service.create(
        display_name="Demo",
        plugin_name="no-op",
        plugin_version="1",
        parameters={},
        actor_id=auth_user_id,
    )

    with pytest.raises(StrategyDefinitionNameConflictError):
        await service.create(
            display_name="demo",
            plugin_name="no-op",
            plugin_version="1",
            parameters={},
            actor_id=auth_user_id,
        )


@pytest.mark.asyncio
async def test_replace_rejects_stale_revision(
    database: Database,
    auth_user_id: UUID,
) -> None:
    service = _service(database)
    created = await service.create(
        display_name="Demo",
        plugin_name="no-op",
        plugin_version="1",
        parameters={},
        actor_id=auth_user_id,
    )
    updated = await service.replace(
        created.id,
        display_name="Demo atualizada",
        plugin_name="no-op",
        plugin_version="1",
        parameters={},
        expected_revision=1,
        actor_id=auth_user_id,
    )

    assert updated.revision == 2
    with pytest.raises(StrategyDefinitionRevisionConflictError):
        await service.replace(
            created.id,
            display_name="Concorrente",
            plugin_name="no-op",
            plugin_version="1",
            parameters={},
            expected_revision=1,
            actor_id=auth_user_id,
        )


@pytest.mark.asyncio
async def test_archive_is_filtered_and_cannot_be_changed(
    database: Database,
    auth_user_id: UUID,
) -> None:
    service = _service(database)
    created = await service.create(
        display_name="Demo",
        plugin_name="no-op",
        plugin_version="1",
        parameters={},
        actor_id=auth_user_id,
    )
    archived = await service.archive(
        created.id,
        expected_revision=1,
        actor_id=auth_user_id,
    )

    active, active_total = await service.list(limit=100, offset=0)
    history, history_total = await service.list(
        limit=100,
        offset=0,
        include_archived=True,
    )

    assert archived.state is StrategyDefinitionState.ARCHIVED
    assert archived.revision == 2
    assert active == [] and active_total == 0
    assert history == [archived] and history_total == 1
    with pytest.raises(StrategyDefinitionArchivedError):
        await service.archive(
            created.id,
            expected_revision=2,
            actor_id=auth_user_id,
        )


def test_database_rejects_delete_and_data_api_privileges(
    database_url: str,
    auth_user_id: UUID,
) -> None:
    definition_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into public.strategy_definitions (
                id,
                display_name,
                plugin_name,
                plugin_version,
                plugin_schema_version,
                lifecycle_version,
                parameters,
                parameters_checksum,
                created_by,
                updated_by
            )
            values (%s, 'History', 'no-op', '1', 1, 1, '{}'::jsonb, %s, %s, %s)
            """,
            (
                definition_id,
                "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
                auth_user_id,
                auth_user_id,
            ),
        )
        with pytest.raises(psycopg.Error, match="historical"):
            connection.execute(
                "delete from public.strategy_definitions where id = %s",
                (definition_id,),
            )
        privilege = connection.execute(
            """
            select
                has_table_privilege('anon', 'public.strategy_definitions', 'select') as anon_select,
                has_table_privilege(
                    'authenticated',
                    'public.strategy_definitions',
                    'insert'
                ) as authenticated_insert,
                has_table_privilege(
                    'service_role',
                    'public.strategy_definitions',
                    'update'
                ) as service_update
            """
        ).fetchone()

    assert privilege == (False, False, False)
