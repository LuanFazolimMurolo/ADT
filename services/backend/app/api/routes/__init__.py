"""HTTP route modules."""

from app.api.routes import (
    admin,
    admin_settings,
    admin_simulations,
    health,
    public,
    system,
)

__all__ = [
    "admin",
    "admin_settings",
    "admin_simulations",
    "health",
    "public",
    "system",
]
