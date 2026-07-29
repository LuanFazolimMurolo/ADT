"""System setting persistence."""

from uuid import UUID

from psycopg import Error
from psycopg.types.json import Jsonb

from app.database.errors import raise_domain_error
from app.database.pool import Database
from app.domain.errors import SettingNotFoundError
from app.domain.models import JsonValue, SystemSetting
from app.repositories._rows import setting_from_row


class SettingsRepository:
    """Read and update non-secret system settings."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list(self) -> list[SystemSetting]:
        """List settings in stable key order."""
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    """
                    select
                        key,
                        value,
                        description,
                        is_public,
                        updated_by,
                        created_at,
                        updated_at
                    from public.system_settings
                    order by key asc
                    """
                )
                rows = await cursor.fetchall()
        except Error as error:
            raise_domain_error(error)

        return [setting_from_row(row) for row in rows]

    async def update_value(
        self,
        key: str,
        *,
        value: JsonValue,
        updated_by: UUID,
    ) -> SystemSetting:
        """Update only a setting value and its actor metadata."""
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    """
                    update public.system_settings
                    set value = %s, updated_by = %s
                    where key = %s
                    returning
                        key,
                        value,
                        description,
                        is_public,
                        updated_by,
                        created_at,
                        updated_at
                    """,
                    (Jsonb(value), updated_by, key),
                )
                row = await cursor.fetchone()
        except Error as error:
            raise_domain_error(error)

        if row is None:
            raise SettingNotFoundError()
        return setting_from_row(row)
