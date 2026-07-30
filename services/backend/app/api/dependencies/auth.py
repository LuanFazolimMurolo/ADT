"""Bearer authentication and database-backed administrator authorization."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies.resources import get_admin_service, get_jwt_verifier
from app.auth import InvalidTokenError, SupabaseJWTVerifier
from app.services import AdminService

_BEARER = HTTPBearer(auto_error=False)
_MAX_BEARER_TOKEN_LENGTH = 8192


async def get_authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    verifier: Annotated[SupabaseJWTVerifier, Depends(get_jwt_verifier)],
) -> UUID:
    """Validate the Supabase Bearer token and return its UUID subject."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if len(credentials.credentials) > _MAX_BEARER_TOKEN_LENGTH:
        raise InvalidTokenError
    return await verifier.verify(credentials.credentials)


async def require_administrator(
    user_id: Annotated[UUID, Depends(get_authenticated_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
) -> UUID:
    """Require the verified UUID to exist in the closed administrator table."""
    if not await admin_service.is_admin(user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user_id
