"""HTTP route modules."""

from app.api.routes import (
    admin,
    admin_settings,
    admin_simulations,
    admin_strategies,
    assets,
    health,
    paper_trading,
    public,
    system,
)

__all__ = [
    "admin",
    "admin_settings",
    "admin_simulations",
    "admin_strategies",
    "assets",
    "health",
    "paper_trading",
    "public",
    "system",
]
