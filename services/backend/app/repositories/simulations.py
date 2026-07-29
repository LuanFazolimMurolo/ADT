"""Simulation persistence with explicit transactional boundaries."""

from decimal import Decimal
from uuid import UUID

from psycopg import Error

from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import PersistenceError, SimulationNotFoundError, SimulationTerminalError
from app.domain.models import SimulationDetails, SimulationStatus
from app.repositories._rows import simulation_details_from_row

_SIMULATION_DETAILS_SELECT = """
    select
        simulation.id,
        simulation.name,
        simulation.status,
        simulation.currency,
        simulation.initial_capital,
        simulation.started_at,
        simulation.ended_at,
        simulation.created_by,
        simulation.created_at,
        simulation.updated_at,
        coalesce(sum(movement.amount), 0::numeric) as current_balance,
        coalesce(
            sum(movement.amount) filter (
                where movement.type in ('TRADE_PROFIT', 'TRADE_LOSS', 'FEE')
            ),
            0::numeric
        ) as total_profit_loss
    from public.simulation_runs as simulation
    left join public.capital_movements as movement
        on movement.simulation_id = simulation.id
"""

_SIMULATION_DETAILS_GROUP_BY = """
    group by
        simulation.id,
        simulation.name,
        simulation.status,
        simulation.currency,
        simulation.initial_capital,
        simulation.started_at,
        simulation.ended_at,
        simulation.created_by,
        simulation.created_at,
        simulation.updated_at
"""


async def _get_details(
    connection: DatabaseConnection,
    simulation_id: UUID,
) -> SimulationDetails | None:
    query = (
        _SIMULATION_DETAILS_SELECT
        + """
        where simulation.id = %s
        """
        + _SIMULATION_DETAILS_GROUP_BY
    )
    cursor = await connection.execute(query, (simulation_id,))
    row = await cursor.fetchone()
    return None if row is None else simulation_details_from_row(row)


class SimulationRepository:
    """Persist simulations while leaving financial invariants to PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[SimulationDetails], int]:
        """List simulations newest first and return the unpaginated total."""
        query = (
            _SIMULATION_DETAILS_SELECT
            + _SIMULATION_DETAILS_GROUP_BY
            + """
            order by simulation.created_at desc, simulation.id desc
            limit %s offset %s
            """
        )
        try:
            async with self._database.transaction() as connection:
                count_cursor = await connection.execute(
                    "select count(*) as total from public.simulation_runs"
                )
                count_row = await count_cursor.fetchone()
                total = 0 if count_row is None else int(count_row["total"])

                cursor = await connection.execute(query, (limit, offset))
                rows = await cursor.fetchall()
        except Error as error:
            raise_domain_error(error)

        return [simulation_details_from_row(row) for row in rows], total

    async def get(self, simulation_id: UUID) -> SimulationDetails:
        """Return one simulation with its calculated balance and P/L."""
        try:
            async with self._database.transaction() as connection:
                details = await _get_details(connection, simulation_id)
        except Error as error:
            raise_domain_error(error)

        if details is None:
            raise SimulationNotFoundError()
        return details

    async def create_with_initial_capital(
        self,
        *,
        name: str,
        initial_capital: Decimal,
        currency: str,
        created_by: UUID,
    ) -> SimulationDetails:
        """Create an ACTIVE simulation and its opening ledger row atomically."""
        try:
            async with self._database.transaction() as connection:
                simulation_cursor = await connection.execute(
                    """
                    insert into public.simulation_runs (
                        name,
                        status,
                        currency,
                        initial_capital,
                        created_by
                    )
                    values (%s, 'ACTIVE', %s, %s, %s)
                    returning id
                    """,
                    (name, currency, initial_capital, created_by),
                )
                simulation_row = await simulation_cursor.fetchone()
                if simulation_row is None:
                    raise PersistenceError()
                simulation_id = simulation_row["id"]

                await connection.execute(
                    """
                    insert into public.capital_movements (
                        simulation_id,
                        type,
                        amount,
                        reason,
                        created_by
                    )
                    values (%s, 'INITIAL_CAPITAL', %s, %s, %s)
                    """,
                    (
                        simulation_id,
                        initial_capital,
                        "Capital inicial da simulação.",
                        created_by,
                    ),
                )

                details = await _get_details(connection, simulation_id)
                if details is None:
                    raise PersistenceError()
        except Error as error:
            raise_domain_error(error)

        return details

    async def transition(
        self,
        simulation_id: UUID,
        *,
        target_status: SimulationStatus,
    ) -> SimulationDetails:
        """Atomically end an ACTIVE simulation with a terminal status."""
        if target_status not in {
            SimulationStatus.COMPLETED,
            SimulationStatus.CANCELLED,
        }:
            raise ValueError("target_status must be a terminal simulation status")

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    """
                    update public.simulation_runs
                    set status = %s, ended_at = now()
                    where id = %s and status = 'ACTIVE'
                    returning id
                    """,
                    (target_status.value, simulation_id),
                )
                updated_row = await cursor.fetchone()
                if updated_row is None:
                    state_cursor = await connection.execute(
                        """
                        select status
                        from public.simulation_runs
                        where id = %s
                        """,
                        (simulation_id,),
                    )
                    state_row = await state_cursor.fetchone()
                    if state_row is None:
                        raise SimulationNotFoundError()
                    raise SimulationTerminalError()

                details = await _get_details(connection, simulation_id)
                if details is None:
                    raise PersistenceError()
        except Error as error:
            raise_domain_error(error)

        return details
