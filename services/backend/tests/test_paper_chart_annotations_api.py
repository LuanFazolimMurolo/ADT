from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import Response, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_paper_chart_annotation_read_service
from app.api.routes.admin_paper_chart_annotations import (
    get_paper_chart_annotations,
)
from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    RiskLimits,
    SlippageModel,
    StrategyDescriptor,
)
from app.main import app
from app.market_data.domain import TradingPair
from app.market_data.timeframes import get_timeframe
from app.paper_trading.chart_annotations import PaperChartAnnotationReadService
from app.paper_trading.domain import PaperSessionConfig, paper_session_id
from app.paper_trading.persisted_state import PaperPersistedStateVerifier
from app.paper_trading.portfolio_timeline_artifacts import (
    PaperPortfolioTimelineArtifactStore,
)
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading_journal_query import (
    _persist_resigned_state,
    _populated_repository,
    _state_verifier,
)


def _config() -> PaperSessionConfig:
    return PaperSessionConfig(
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1m"),
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        warmup_candles=0,
        strategy=StrategyDescriptor("no-op", "1"),
        strategy_lifecycle_version=1,
        initial_capital=Decimal("1000"),
        execution=ExecutionAssumptions(
            fees=FeeModel(Decimal("0"), Decimal("0")),
            slippage=SlippageModel(),
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("0"),
        ),
        risk_limits=RiskLimits(),
        history_window=10,
        max_candles=100,
        max_orders=100,
        max_events=1_000,
        engine_version="1",
        schema_version=1,
    )


def test_route_sets_no_store_and_integrity_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    repository = PaperTradingRepository(tmp_path)
    monkeypatch.setattr(repository, "load_config", lambda _session_id: config)
    monkeypatch.setattr(repository, "load_state", lambda _session_id: None)
    service = PaperChartAnnotationReadService(
        repository,
        PaperPersistedStateVerifier(PaperPortfolioTimelineArtifactStore(tmp_path)),
    )
    response = Response()
    start = config.start_at

    result = get_paper_chart_annotations(
        response=response,
        _administrator_id=UUID(int=1),
        service=service,
        session_id=paper_session_id(config),
        start=start,
        before=start + timedelta(minutes=10),
        limit=100,
    )

    assert result.count == 0
    assert result.state_available is False
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-ADT-Paper-Chart-Rows"] == "0"
    assert response.headers["X-ADT-Paper-Chart-Content-Checksum"] == result.content_checksum
    assert "X-ADT-Paper-State-Checksum" not in response.headers


@pytest.mark.asyncio
async def test_admin_annotations_reject_resigned_state_with_safe_conflict(
    tmp_path: Path,
) -> None:
    repository, session_id = _populated_repository(tmp_path)
    _persist_resigned_state(
        tmp_path,
        repository,
        session_id,
        dataset_version="c" * 64,
    )
    service = PaperChartAnnotationReadService(repository, _state_verifier(tmp_path))
    app.dependency_overrides[require_administrator] = lambda: UUID(int=1)
    app.dependency_overrides[get_paper_chart_annotation_read_service] = lambda: service

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                f"/api/v1/admin/paper-trading/sessions/{session_id}/chart-annotations",
                params={
                    "start": "2026-01-01T00:00:00Z",
                    "before": "2026-01-02T00:00:00Z",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"] == {
        "code": "paper_session_verification_failed",
        "message": "A sessão de paper trading não pôde ser verificada.",
    }
