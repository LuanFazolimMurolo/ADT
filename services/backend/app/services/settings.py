"""System setting service."""

from uuid import UUID

from app.domain.models import JsonValue, SystemSetting
from app.repositories.settings import SettingsRepository
from app.services._validation import require_nonblank


class SettingsService:
    """List and mutate only the value of existing non-secret settings."""

    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def list(self) -> list[SystemSetting]:
        """List every persisted system setting."""
        return await self._repository.list()

    async def update_value(
        self,
        key: str,
        *,
        value: JsonValue,
        updated_by: UUID,
    ) -> SystemSetting:
        """Update an existing setting value and record the administrator."""
        return await self._repository.update_value(
            require_nonblank(key, field_name="key"),
            value=value,
            updated_by=updated_by,
        )
