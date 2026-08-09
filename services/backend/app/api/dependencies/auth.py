"""Bearer authentication and database-backed application authorization."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies.resources import get_admin_service, get_jwt_verifier
from app.auth import InvalidTokenError, SupabaseJWTVerifier
from app.services import AdminService

_BEARER = HTTPBearer(auto_error=False)
_MAX_BEARER_TOKEN_LENGTH = 8192


@dataclass(frozen=True, slots=True)
class AppPaperSessionReadAccess:
    """Backend-authoritative project-owner access to paper-session reads."""

    user_id: UUID
    is_project_owner_reader: bool

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise TypeError("The authenticated user identifier is invalid.")
        if type(self.is_project_owner_reader) is not bool:
            raise TypeError("The project-owner reader decision is invalid.")


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


async def get_app_paper_session_read_access(
    user_id: Annotated[UUID, Depends(get_authenticated_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
) -> AppPaperSessionReadAccess:
    """Resolve project-owner paper-session read access from PostgreSQL."""

    return AppPaperSessionReadAccess(
        user_id=user_id,
        is_project_owner_reader=await admin_service.is_admin(user_id),
    )


async def require_app_paper_session_reader(
    access: Annotated[
        AppPaperSessionReadAccess,
        Depends(get_app_paper_session_read_access),
    ],
) -> UUID:
    """Reject session-scoped reads before any paper artifact is inspected."""

    if not access.is_project_owner_reader:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return access.user_id
