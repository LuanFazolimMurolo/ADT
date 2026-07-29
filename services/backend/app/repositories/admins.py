"""Administrator allow-list persistence."""

from uuid import UUID

from psycopg import Error

from app.database.errors import raise_domain_error
from app.database.pool import Database


class AdminRepository:
    """Read the closed administrator allow-list."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def is_admin(self, user_id: UUID) -> bool:
        """Return whether a Supabase Auth UUID is an ADT administrator."""
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    """
                    select exists (
                        select 1
                        from public.app_admins
                        where user_id = %s
                    ) as is_admin
                    """,
                    (user_id,),
                )
                row = await cursor.fetchone()
                return row is not None and bool(row["is_admin"])
        except Error as error:
            raise_domain_error(error)
