from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import Response

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
from app.market_data.domain import TradingPair
from app.market_data.timeframes import get_timeframe
from app.paper_trading.chart_annotations import PaperChartAnnotationReadService
from app.paper_trading.domain import PaperSessionConfig, paper_session_id
from app.paper_trading.repository import PaperTradingRepository


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
    service = PaperChartAnnotationReadService(repository)
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
