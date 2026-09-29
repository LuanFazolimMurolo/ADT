"""Dependency composition for the public official PAPER projection."""

from typing import Annotated

from fastapi import Depends

from app.api.dependencies.resources import get_database
from app.database import Database
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.services.public_official_paper import PublicOfficialPaperService


def get_public_official_paper_service(
    database: Annotated[Database, Depends(get_database)],
) -> PublicOfficialPaperService:
    """Build the bounded read-only official PAPER projection service."""

    return PublicOfficialPaperService(PostgresOperationalPaperSessionRunRepository(database))
