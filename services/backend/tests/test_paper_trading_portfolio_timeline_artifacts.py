"""Atomic content-addressed paper portfolio timeline artifact tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtesting.domain import OrderSide
from app.paper_trading.domain import paper_session_id
from app.paper_trading.errors import PaperSessionCorruptError
from app.paper_trading.portfolio_timeline import build_paper_portfolio_timeline
from app.paper_trading.portfolio_timeline_artifacts import (
    PaperPortfolioTimelineArtifactStore,
)
from tests.test_paper_trading import FakeSource, _candle
from tests.test_paper_trading_journal import _intent, _journal_config, _service


def _timeline(tmp_path: Path):
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
    state = service.run_once(session_id).state
    batch = source.load(config, end=state.data_range.end)
    return build_paper_portfolio_timeline(config, batch, state)


def test_store_publishes_content_addressed_directory_and_round_trips(
    tmp_path: Path,
) -> None:
    timeline = _timeline(tmp_path)
    store = PaperPortfolioTimelineArtifactStore(tmp_path)

    first = store.publish(timeline)
    second = store.publish(timeline)

    assert first == timeline
    assert second == timeline
    assert store.load(timeline.session_id, timeline.timeline_id) == timeline

    root = (
        tmp_path
        / "market"
        / "paper-trading"
        / timeline.session_id
        / "portfolio-timelines"
        / timeline.timeline_id
    )
    assert root.is_dir()
    assert (root / "manifest.json").is_file()
    assert (root / "observations.parquet").is_file()
    assert not tuple(root.parent.glob(f".{timeline.timeline_id}.tmp-*"))


def test_store_rejects_parquet_tampering(tmp_path: Path) -> None:
    timeline = _timeline(tmp_path)
    store = PaperPortfolioTimelineArtifactStore(tmp_path)
    store.publish(timeline)
    parquet = (
        tmp_path
        / "market"
        / "paper-trading"
        / timeline.session_id
        / "portfolio-timelines"
        / timeline.timeline_id
        / "observations.parquet"
    )
    with parquet.open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(PaperSessionCorruptError):
        store.load(timeline.session_id, timeline.timeline_id)


def test_store_rejects_resigned_manifest_that_points_to_wrong_content(
    tmp_path: Path,
) -> None:
    timeline = _timeline(tmp_path)
    store = PaperPortfolioTimelineArtifactStore(tmp_path)
    store.publish(timeline)
    manifest = (
        tmp_path
        / "market"
        / "paper-trading"
        / timeline.session_id
        / "portfolio-timelines"
        / timeline.timeline_id
        / "manifest.json"
    )
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["manifest"]["state_checksum"] = "0" * 64

    # Deliberately update the outer envelope checksum: semantic validation must
    # still reject the forged state binding.
    import hashlib

    from app.backtesting.serialization import canonical_json_bytes

    document["checksum"] = hashlib.sha256(canonical_json_bytes(document["manifest"])).hexdigest()
    manifest.write_bytes(canonical_json_bytes(document))

    with pytest.raises(PaperSessionCorruptError):
        store.load(timeline.session_id, timeline.timeline_id)
