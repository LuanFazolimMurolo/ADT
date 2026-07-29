"""Append-only capital movement persistence."""

from decimal import Decimal
from uuid import UUID

from psycopg import Error
from psycopg.types.json import Jsonb

from app.database.errors import raise_domain_error
from app.database.pool import Database
from app.domain.errors import PersistenceError, SimulationNotFoundError, SimulationTerminalError
from app.domain.models import CapitalMovement, JsonObject, LedgerMovementType, SimulationStatus
from app.repositories._rows import movement_from_row

_MOVEMENT_SELECT = """
    select
        movement.id,
        movement.simulation_id,
        movement.type,
        movement.amount,
        movement.reason,
        movement.reference_id,
        movement.created_by,
        movement.created_at,
        metadata_record.metadata
    from public.capital_movements as movement
    left join lateral (
        select audit.metadata
        from public.audit_logs as audit
        where audit.entity_type = 'CAPITAL_MOVEMENT'
          and audit.entity_id = movement.id
          and audit.action = 'CAPITAL_MOVEMENT_METADATA_RECORDED'
        order by audit.created_at desc, audit.id desc
        limit 1
    ) as metadata_record on true
"""


class CapitalMovementRepository:
    """Append and list immutable administrative capital movements."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list(
        self,
        simulation_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[CapitalMovement], int]:
        """List movements in stable chronological order and return their total."""
        query = (
            _MOVEMENT_SELECT
            + """
            where movement.simulation_id = %s
            order by movement.created_at asc, movement.id asc
            limit %s offset %s
            """
        )
        try:
            async with self._database.transaction() as connection:
                exists_cursor = await connection.execute(
                    "select 1 from public.simulation_runs where id = %s",
                    (simulation_id,),
                )
                if await exists_cursor.fetchone() is None:
                    raise SimulationNotFoundError()

                count_cursor = await connection.execute(
                    """
                    select count(*) as total
                    from public.capital_movements
                    where simulation_id = %s
                    """,
                    (simulation_id,),
                )
                count_row = await count_cursor.fetchone()
                total = 0 if count_row is None else int(count_row["total"])

                cursor = await connection.execute(query, (simulation_id, limit, offset))
                rows = await cursor.fetchall()
        except Error as error:
            raise_domain_error(error)

        return [movement_from_row(row) for row in rows], total

    async def create(
        self,
        *,
        simulation_id: UUID,
        movement_type: LedgerMovementType,
        amount: Decimal,
        reason: str,
        created_by: UUID,
        metadata: JsonObject | None,
    ) -> CapitalMovement:
        """Append a movement and optional metadata audit record atomically."""
        try:
            async with self._database.transaction() as connection:
                state_cursor = await connection.execute(
                    """
                    select status
                    from public.simulation_runs
                    where id = %s
                    for update
                    """,
                    (simulation_id,),
                )
                state_row = await state_cursor.fetchone()
                if state_row is None:
                    raise SimulationNotFoundError()
                if state_row["status"] != SimulationStatus.ACTIVE.value:
                    raise SimulationTerminalError()

                movement_cursor = await connection.execute(
                    """
                    insert into public.capital_movements (
                        simulation_id,
                        type,
                        amount,
                        reason,
                        created_by
                    )
                    values (%s, %s, %s, %s, %s)
                    returning
                        id,
                        simulation_id,
                        type,
                        amount,
                        reason,
                        reference_id,
                        created_by,
                        created_at
                    """,
                    (
                        simulation_id,
                        movement_type.value,
                        amount,
                        reason,
                        created_by,
                    ),
                )
                row = await movement_cursor.fetchone()
                if row is None:
                    raise PersistenceError()

                if metadata is not None:
                    await connection.execute(
                        """
                        insert into public.audit_logs (
                            actor_user_id,
                            action,
                            entity_type,
                            entity_id,
                            metadata
                        )
                        values (
                            %s,
                            'CAPITAL_MOVEMENT_METADATA_RECORDED',
                            'CAPITAL_MOVEMENT',
                            %s,
                            %s
                        )
                        """,
                        (created_by, row["id"], Jsonb(metadata)),
                    )
        except Error as error:
            raise_domain_error(error)

        row["metadata"] = metadata
        return movement_from_row(row)
