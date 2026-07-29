"""Administrative authorization service."""

from uuid import UUID

from app.repositories.admins import AdminRepository


class AdminService:
    """Resolve PostgreSQL-backed administrator membership."""

    def __init__(self, repository: AdminRepository) -> None:
        self._repository = repository

    async def is_admin(self, user_id: UUID) -> bool:
        """Return whether an authenticated UUID is in the admin allow-list."""
        return await self._repository.is_admin(user_id)
