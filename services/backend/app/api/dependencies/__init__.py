"""FastAPI dependencies for runtime resources and authorization."""

from app.api.dependencies.auth import get_authenticated_user, require_administrator
from app.api.dependencies.resources import (
    get_admin_service,
    get_capital_movement_service,
    get_database,
    get_jwt_verifier,
    get_public_simulation_service,
    get_settings_service,
    get_simulation_service,
)

__all__ = [
    "get_admin_service",
    "get_authenticated_user",
    "get_capital_movement_service",
    "get_database",
    "get_jwt_verifier",
    "get_public_simulation_service",
    "get_settings_service",
    "get_simulation_service",
    "require_administrator",
]
