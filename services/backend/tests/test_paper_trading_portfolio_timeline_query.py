"""Read-side regressions for persisted paper portfolio timelines."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.backtesting.serialization import canonical_json_bytes
from app.paper_trading.domain import PaperRunAction, PaperSessionState
from app.paper_trading.errors import (
    InvalidPaperSessionError,
    PaperPortfolioTimelineNotFoundError,
    PaperSessionCorruptError,
)
from app.paper_trading.portfolio_timeline_artifacts import (
    PaperPortfolioTimelineArtifactStore,
)
from app.paper_trading.portfolio_timeline_query import (
    PaperPortfolioTimelinePageQuery,
    PaperPortfolioTimelineReadService,
)
from app.paper_trading.repository import PaperTradingRepository
from app.paper_trading.service import PaperTradingService
from tests.test_paper_trading_portfolio_timeline_service import _running_session


def _populated_query_service(
    tmp_path: Path,
) -> tuple[
    PaperPortfolioTimelineReadService,
    PaperTradingService,
    PaperSessionState,
    str,
]:
    source, paper_service, config, session_id = _running_session(tmp_path)
    del source, config
    state = paper_service.run_once(session_id).state
    read_service = PaperPortfolioTimelineReadService(
        PaperTradingRepository(tmp_path),
        PaperPortfolioTimelineArtifactStore(tmp_path),
    )
    return read_service, paper_service, state, session_id


def _reference_path(tmp_path: Path, session_id: str, state_checksum: str) -> Path:
    return (
        tmp_path
        / "market"
        / "paper-trading"
        / session_id
        / "portfolio-timeline-refs"
        / f"{state_checksum}.json"
    )


def test_read_page_uses_stable_backward_pagination_from_persisted_artifact(
    tmp_path: Path,
) -> None:
    read_service, _, state, session_id = _populated_query_service(tmp_path)

    latest = read_service.read_page(PaperPortfolioTimelinePageQuery(session_id=session_id, limit=2))

    assert latest.state_id == state.state_id
    assert latest.state_checksum == state.checksum
    assert latest.total_observations == 4
    assert [item.candle_index for item in latest.observations] == [2, 3]
    assert latest.has_more_before is True
    assert latest.next_before == latest.page_range.start
    assert latest.timeline_id
    assert latest.timeline_content_checksum
    assert latest.content_checksum

    assert latest.next_before is not None
    older = read_service.read_page(
        PaperPortfolioTimelinePageQuery(
            session_id=session_id,
            before=latest.next_before,
            limit=2,
        )
    )

    assert [item.candle_index for item in older.observations] == [0, 1]
    assert older.has_more_before is False
    assert older.next_before is None
    assert older.timeline_id == latest.timeline_id
    assert older.timeline_content_checksum == latest.timeline_content_checksum
    assert older.content_checksum != latest.content_checksum


def test_read_page_rejects_unaligned_or_out_of_range_cursor(
    tmp_path: Path,
) -> None:
    read_service, _, _, session_id = _populated_query_service(tmp_path)
    latest = read_service.read_page(PaperPortfolioTimelinePageQuery(session_id=session_id))

    with pytest.raises(InvalidPaperSessionError):
        read_service.read_page(
            PaperPortfolioTimelinePageQuery(
                session_id=session_id,
                before=latest.available_range.end - timedelta(seconds=1),
            )
        )

    with pytest.raises(InvalidPaperSessionError):
        read_service.read_page(
            PaperPortfolioTimelinePageQuery(
                session_id=session_id,
                before=latest.available_range.end + latest.timeframe.duration,
            )
        )


def test_missing_state_reference_fails_closed_and_noop_backfills_it(
    tmp_path: Path,
) -> None:
    read_service, paper_service, state, session_id = _populated_query_service(tmp_path)
    reference = _reference_path(tmp_path, session_id, state.checksum)
    assert reference.is_file()
    reference.unlink()

    with pytest.raises(PaperPortfolioTimelineNotFoundError):
        read_service.read_page(PaperPortfolioTimelinePageQuery(session_id))

    result = paper_service.run_once(session_id)

    assert result.action is PaperRunAction.NOOP
    assert result.state == state
    assert reference.is_file()
    assert (
        read_service.read_page(PaperPortfolioTimelinePageQuery(session_id)).state_checksum
        == state.checksum
    )


def test_resigned_state_reference_tampering_is_rejected(
    tmp_path: Path,
) -> None:
    read_service, _, state, session_id = _populated_query_service(tmp_path)
    reference = _reference_path(tmp_path, session_id, state.checksum)
    document: dict[str, Any] = json.loads(reference.read_text(encoding="utf-8"))
    reference_payload = document["reference"]
    assert isinstance(reference_payload, dict)
    reference_payload["timeline_id"] = "0" * 64
    document["checksum"] = hashlib.sha256(canonical_json_bytes(reference_payload)).hexdigest()
    reference.write_bytes(canonical_json_bytes(document))

    with pytest.raises(PaperSessionCorruptError):
        read_service.read_page(PaperPortfolioTimelinePageQuery(session_id))
