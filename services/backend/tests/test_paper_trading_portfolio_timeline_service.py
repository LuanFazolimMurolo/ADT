"""Paper service integration regressions for deterministic portfolio timelines."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.backtesting.domain import OrderSide
from app.paper_trading.domain import PaperRunAction, paper_session_id
from app.paper_trading.errors import PaperSessionVerificationError
from app.paper_trading.portfolio_timeline import build_paper_portfolio_timeline
from app.paper_trading.portfolio_timeline_artifacts import (
    PaperPortfolioTimelineArtifactStore,
)
from tests.test_paper_trading import FakeSource, _candle
from tests.test_paper_trading_journal import (
    _intent,
    _journal_config,
    _service,
)


def _running_session(tmp_path: Path):
    source = FakeSource(
        tuple(_candle(index, close) for index, close in enumerate(("100", "110", "120", "130")))
    )
    schedule = (
        (0, (_intent(OrderSide.BUY, "1", "entry"),)),
        (2, (_intent(OrderSide.SELL, "0.5", "partial"),)),
    )
    service = _service(tmp_path, source, schedule)
    config = _journal_config()
    session_id = paper_session_id(config)
    service.create(config)
    return source, service, config, session_id


def test_run_once_publishes_timeline_before_returning_updated_state(
    tmp_path: Path,
) -> None:
    source, service, config, session_id = _running_session(tmp_path)

    result = service.run_once(session_id)
    batch = source.load(config, end=result.state.data_range.end)
    expected = build_paper_portfolio_timeline(config, batch, result.state)
    persisted = PaperPortfolioTimelineArtifactStore(tmp_path).load(
        session_id,
        expected.timeline_id,
    )

    assert result.action is PaperRunAction.UPDATED
    assert persisted == expected
    assert persisted.observations[-1].portfolio == result.state.portfolio


def test_noop_cycle_backfills_missing_timeline_without_changing_state(
    tmp_path: Path,
) -> None:
    source, service, config, session_id = _running_session(tmp_path)
    first = service.run_once(session_id)
    batch = source.load(config, end=first.state.data_range.end)
    expected = build_paper_portfolio_timeline(config, batch, first.state)
    artifact_dir = (
        tmp_path
        / "market"
        / "paper-trading"
        / session_id
        / "portfolio-timelines"
        / expected.timeline_id
    )
    shutil.rmtree(artifact_dir)
    assert not artifact_dir.exists()

    second = service.run_once(session_id)

    assert second.action is PaperRunAction.NOOP
    assert second.state == first.state
    assert (
        PaperPortfolioTimelineArtifactStore(tmp_path).load(
            session_id,
            expected.timeline_id,
        )
        == expected
    )


def test_verify_rebuilds_timeline_and_rejects_persisted_tampering(
    tmp_path: Path,
) -> None:
    source, service, config, session_id = _running_session(tmp_path)
    state = service.run_once(session_id).state
    batch = source.load(config, end=state.data_range.end)
    expected = build_paper_portfolio_timeline(config, batch, state)
    parquet = (
        tmp_path
        / "market"
        / "paper-trading"
        / session_id
        / "portfolio-timelines"
        / expected.timeline_id
        / "observations.parquet"
    )
    with parquet.open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(PaperSessionVerificationError):
        service.verify(session_id)
