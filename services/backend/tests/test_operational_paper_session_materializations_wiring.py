"""Dependency wiring tests for operational paper-session materialization."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from fastapi import FastAPI, Request

import app.services as services_contract
from app.api.dependencies.resources import (
    get_operational_paper_session_materialization_service,
    get_paper_trading_repository,
)
from app.database import Database
from app.paper_trading.repository import PaperTradingRepository
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.services.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationService,
)


def test_services_contract_exports_materialization_service() -> None:
    assert (
        services_contract.OperationalPaperSessionMaterializationService
        is OperationalPaperSessionMaterializationService
    )


def test_paper_repository_dependency_returns_application_owned_repository(
    tmp_path: Path,
) -> None:
    application = FastAPI()
    repository = PaperTradingRepository(tmp_path)
    application.state.paper_trading_repository = repository
    request = Request({"type": "http", "app": application})

    assert get_paper_trading_repository(request) is repository


def test_materialization_dependency_composes_authoritative_repositories(
    tmp_path: Path,
) -> None:
    database = Database("postgresql://adt_test@127.0.0.1:1/adt_test")
    paper_repository = PaperTradingRepository(tmp_path)

    service = get_operational_paper_session_materialization_service(
        database=database,
        paper_repository=paper_repository,
    )

    assert isinstance(service, OperationalPaperSessionMaterializationService)
    assert isinstance(
        service._repository,
        PostgresOperationalPaperSessionMaterializationRepository,
    )
    assert service._repository._database is database
    assert isinstance(
        service._authorization_repository,
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )
    assert service._authorization_repository._database is database
    assert isinstance(
        service._profile_repository,
        PostgresOperationalPaperSessionProfileRepository,
    )
    assert service._profile_repository._database is database
    assert service._paper_repository is paper_repository
    observed_at = service._clock()
    assert observed_at.tzinfo is UTC


def test_main_exposes_lifespan_owned_paper_repository() -> None:
    source = Path("app/main.py").read_text()
    creation = "paper_repository = PaperTradingRepository("
    exposure = "application.state.paper_trading_repository = paper_repository"

    assert source.count(creation) == 1
    assert source.count(exposure) == 1
    assert source.index(creation) < source.index(exposure)
