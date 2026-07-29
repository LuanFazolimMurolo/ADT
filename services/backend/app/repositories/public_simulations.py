"""Read-only access to the secured public simulation view."""

from psycopg import Error

from app.database.errors import raise_domain_error
from app.database.pool import Database
from app.domain.models import PublicSimulationSummary
from app.repositories._rows import public_summary_from_row


class PublicSimulationRepository:
    """Read the narrow UUID-free public simulation projection."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_active(self) -> PublicSimulationSummary | None:
        """Return the active simulation summary without selecting its UUID."""
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    """
                    select
                        simulation_name,
                        currency,
                        initial_capital,
                        current_balance,
                        total_profit_loss,
                        started_at,
                        status
                    from public.active_simulation_summary
                    limit 1
                    """
                )
                row = await cursor.fetchone()
        except Error as error:
            raise_domain_error(error)

        return None if row is None else public_summary_from_row(row)
