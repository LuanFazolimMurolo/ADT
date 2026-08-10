"""Atomic content-addressed paper portfolio timeline artifact tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.backtesting.domain import OrderSide
from app.backtesting.serialization import canonical_json_bytes
from app.paper_trading.domain import paper_session_id
from app.paper_trading.errors import (
    PaperPortfolioTimelineNotFoundError,
    PaperSessionCorruptError,
)
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


def _reference_path(tmp_path: Path, session_id: str, state_checksum: str) -> Path:
    return (
        tmp_path
        / "market"
        / "paper-trading"
        / session_id
        / "portfolio-timeline-refs"
        / f"{state_checksum}.json"
    )


def _manifest_path(tmp_path: Path, session_id: str, timeline_id: str) -> Path:
    return (
        tmp_path
        / "market"
        / "paper-trading"
        / session_id
        / "portfolio-timelines"
        / timeline_id
        / "manifest.json"
    )


def _resign_document(path: Path, payload_key: str) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document["checksum"] = hashlib.sha256(canonical_json_bytes(document[payload_key])).hexdigest()
    path.write_bytes(canonical_json_bytes(document))


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


def test_state_binding_cross_checks_reference_and_manifest_without_parquet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = _timeline(tmp_path)
    store = PaperPortfolioTimelineArtifactStore(tmp_path)
    store.publish(timeline)

    def fail_parquet(*_args: object, **_kwargs: object) -> None:
        pytest.fail("metadata-only binding must not read observations.parquet")

    monkeypatch.setattr(
        "app.paper_trading.portfolio_timeline_artifacts.pq.ParquetFile",
        fail_parquet,
    )
    monkeypatch.setattr(
        "app.paper_trading.portfolio_timeline_artifacts.file_checksum",
        fail_parquet,
    )
    binding = store.load_state_binding(
        timeline.session_id,
        timeline.state_id,
        timeline.state_checksum,
    )

    assert binding.session_id == timeline.session_id
    assert binding.config_checksum == timeline.config_checksum
    assert binding.state_id == timeline.state_id
    assert binding.state_checksum == timeline.state_checksum
    assert binding.dataset_version == timeline.dataset_version
    assert binding.source_checksum == timeline.source_checksum
    assert binding.timeline_id == timeline.timeline_id
    assert binding.timeline_content_checksum == timeline.content_checksum


def test_state_binding_rejects_resigned_reference_divergence(tmp_path: Path) -> None:
    timeline = _timeline(tmp_path)
    store = PaperPortfolioTimelineArtifactStore(tmp_path)
    store.publish(timeline)
    reference = _reference_path(tmp_path, timeline.session_id, timeline.state_checksum)
    document = json.loads(reference.read_text(encoding="utf-8"))
    document["reference"]["dataset_version"] = "0" * 64
    reference.write_bytes(canonical_json_bytes(document))
    _resign_document(reference, "reference")

    with pytest.raises(PaperSessionCorruptError):
        store.load_state_binding(
            timeline.session_id,
            timeline.state_id,
            timeline.state_checksum,
        )


def test_state_binding_rejects_resigned_manifest_divergence(tmp_path: Path) -> None:
    timeline = _timeline(tmp_path)
    store = PaperPortfolioTimelineArtifactStore(tmp_path)
    store.publish(timeline)
    manifest = _manifest_path(tmp_path, timeline.session_id, timeline.timeline_id)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["manifest"]["source_checksum"] = "0" * 64
    manifest.write_bytes(canonical_json_bytes(document))
    _resign_document(manifest, "manifest")

    with pytest.raises(PaperSessionCorruptError):
        store.load_state_binding(
            timeline.session_id,
            timeline.state_id,
            timeline.state_checksum,
        )


def test_state_binding_rejects_wrong_reference_target_and_missing_reference(
    tmp_path: Path,
) -> None:
    timeline = _timeline(tmp_path)
    store = PaperPortfolioTimelineArtifactStore(tmp_path)
    store.publish(timeline)
    reference = _reference_path(tmp_path, timeline.session_id, timeline.state_checksum)
    original = reference.read_bytes()
    document = json.loads(original)
    document["reference"]["timeline_id"] = "0" * 64
    reference.write_bytes(canonical_json_bytes(document))
    _resign_document(reference, "reference")

    with pytest.raises(PaperSessionCorruptError):
        store.load_state_binding(
            timeline.session_id,
            timeline.state_id,
            timeline.state_checksum,
        )

    reference.write_bytes(original)
    reference.unlink()
    with pytest.raises(PaperPortfolioTimelineNotFoundError):
        store.load_state_binding(
            timeline.session_id,
            timeline.state_id,
            timeline.state_checksum,
        )


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
    document["checksum"] = hashlib.sha256(canonical_json_bytes(document["manifest"])).hexdigest()
    manifest.write_bytes(canonical_json_bytes(document))

    with pytest.raises(PaperSessionCorruptError):
        store.load(timeline.session_id, timeline.timeline_id)
