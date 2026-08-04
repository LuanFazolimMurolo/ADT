"""Persistence, verification and CLI access for market-regime observations."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

import app.backtesting.commands as commands_module
from app.backtesting.artifacts import BacktestArtifactStore, build_backtest_result
from app.backtesting.commands import configure_backtest_parser, prepare_backtest
from app.backtesting.domain import RegimeAwareBacktestConfig
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.query import BacktestRunReader
from app.backtesting.serialization import (
    canonical_json_bytes,
    canonical_value,
    decimal_text,
    file_checksum,
    read_json_envelope,
    write_json_envelope,
)
from app.backtesting.verifier import BacktestResultVerifier
from app.core.config import MarketDataSettings
from app.domain.errors import InvalidDomainInputError
from app.indicators._math import contextual, indicator_decimal_context
from app.indicators.regime import (
    MarketRegimeKind,
    MarketRegimePoint,
    TrendDirection,
)
from tests.test_backtesting_regime import (
    _base_config,
    _candles,
    _Reader,
    _RecordingStrategy,
    _snapshot,
    _tracked_config,
    _tracked_evaluation_config,
)


def _settings(tmp_path: Path) -> MarketDataSettings:
    return MarketDataSettings(
        data_dir=tmp_path,
        market_http_retries=0,
        backtest_default_maker_fee_bps=Decimal("0"),
        backtest_default_taker_fee_bps=Decimal("0"),
        backtest_default_slippage_bps=Decimal("0"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    configure_backtest_parser(parser)
    return parser


def _plan_arguments(*extra: str) -> list[str]:
    return [
        "plan",
        "--snapshot-id",
        "a" * 64,
        "--strategy",
        "no-op",
        "--initial-capital",
        "1000",
        *extra,
    ]


def _publish_regime_run(tmp_path: Path) -> tuple[str, Path, _Reader]:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()
    config = _tracked_config(_base_config(snapshot, strategy.descriptor))
    source = _Reader(snapshot, candles)
    execution = DeterministicBacktestEngine(source).run(config, strategy)
    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=lambda _path: _Reader(snapshot, candles),
    ).publish(config, execution)
    root = tmp_path / "market" / "backtests" / result.run_id.value
    return result.run_id.value, root, source


def _refresh_artifact_checksum(root: Path, artifact_name: str) -> None:
    manifest_path = root / "manifest.json"
    manifest = read_json_envelope(manifest_path, "manifest")
    artifacts = manifest.get("artifacts")
    assert isinstance(artifacts, list)
    for item in artifacts:
        assert isinstance(item, dict)
        if item.get("relative_path") == artifact_name:
            artifact_path = root / artifact_name
            item["checksum"] = file_checksum(artifact_path)
            item["size_bytes"] = artifact_path.stat().st_size
    write_json_envelope(manifest_path, "manifest", manifest)


def _coherent_hostile_point(
    point: MarketRegimePoint,
    *,
    close_price: Decimal,
) -> MarketRegimePoint:
    with indicator_decimal_context():
        fast_ema = Decimal("2")
        slow_ema = Decimal("1")
        atr = Decimal("1")
        return replace(
            point,
            regime=MarketRegimeKind.TREND,
            trend_direction=TrendDirection.UP,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            atr=atr,
            atr_ratio=contextual(atr / close_price),
            trend_strength=contextual(abs(fast_ema - slow_ema) / atr),
        )


def test_regime_run_publishes_verifies_inspects_and_pages(tmp_path: Path) -> None:
    run_id, root, source = _publish_regime_run(tmp_path)
    snapshot = source.snapshot
    snapshot_factory = lambda _path: _Reader(snapshot, source.candles)  # noqa: E731

    verification = BacktestResultVerifier(
        tmp_path,
        snapshot_factory=snapshot_factory,
    ).verify(run_id)
    reader = BacktestRunReader(tmp_path, snapshot_factory=snapshot_factory)
    summary = reader.inspect(run_id)
    page = reader.regimes(run_id, offset=1, limit=2)

    assert (root / "regimes.jsonl").is_file()
    assert verification.artifact_count == 8
    assert verification.market_regime_count == len(source.candles)
    assert summary["market_regime_count"] == len(source.candles)
    assert summary["market_regime_policy"] == canonical_value(
        _tracked_config(
            _base_config(snapshot, _RecordingStrategy().descriptor)
        ).market_regime_policy
    )
    assert page["offset"] == 1
    assert page["limit"] == 2
    assert page["total"] == len(source.candles)
    assert len(page["items"]) == 2
    assert page["truncated"] is True


def test_evaluation_regime_run_publishes_context_aware_slice(tmp_path: Path) -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()
    config = _tracked_evaluation_config(
        _base_config(snapshot, strategy.descriptor),
        evaluation_start=2,
    )
    source = _Reader(snapshot, candles)
    execution = DeterministicBacktestEngine(source).run(config, strategy)

    result = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=lambda _path: _Reader(snapshot, candles),
    ).publish(config, execution)
    verification = BacktestResultVerifier(
        tmp_path,
        snapshot_factory=lambda _path: _Reader(snapshot, candles),
    ).verify(result.run_id.value)

    assert execution.market_regimes[0].regime is not MarketRegimeKind.WARMUP
    assert verification.market_regime_count == execution.candles_processed == 2


def test_legacy_run_keeps_original_artifact_contract(tmp_path: Path) -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()
    config = _base_config(snapshot, strategy.descriptor)
    source = _Reader(snapshot, candles)
    execution = DeterministicBacktestEngine(source).run(config, strategy)
    store = BacktestArtifactStore(
        tmp_path,
        snapshot_factory=lambda _path: _Reader(snapshot, candles),
    )

    result = store.publish(config, execution)
    root = store.root / result.run_id.value
    verifier = BacktestResultVerifier(
        tmp_path,
        snapshot_factory=lambda _path: _Reader(snapshot, candles),
    )
    verification = verifier.verify(result.run_id.value)
    page = BacktestRunReader(
        tmp_path,
        snapshot_factory=lambda _path: _Reader(snapshot, candles),
    ).regimes(result.run_id.value, offset=0, limit=20)

    assert not (root / "regimes.jsonl").exists()
    assert verification.artifact_count == 7
    assert verification.market_regime_count == 0
    assert page == {
        "offset": 0,
        "limit": 20,
        "total": 0,
        "items": [],
        "truncated": False,
    }


def test_result_rejects_regimes_for_legacy_config() -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()
    base = _base_config(snapshot, strategy.descriptor)
    tracked = _tracked_config(base)
    execution = DeterministicBacktestEngine(_Reader(snapshot, candles)).run(
        tracked,
        strategy,
    )

    with pytest.raises(ValueError, match="legacy"):
        build_backtest_result(base, execution)


def test_store_rejects_semantically_wrong_regime_output(tmp_path: Path) -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()
    config = _tracked_config(_base_config(snapshot, strategy.descriptor))
    execution = DeterministicBacktestEngine(_Reader(snapshot, candles)).run(
        config,
        strategy,
    )
    point = execution.market_regimes[2]
    hostile_point = _coherent_hostile_point(
        point,
        close_price=candles[2].close,
    )
    hostile_execution = replace(
        execution,
        market_regimes=(
            *execution.market_regimes[:2],
            hostile_point,
            *execution.market_regimes[3:],
        ),
    )

    with pytest.raises(ValueError, match="immutable snapshot candles"):
        BacktestArtifactStore(
            tmp_path,
            snapshot_factory=lambda _path: _Reader(snapshot, candles),
        ).publish(config, hostile_execution)


def test_verifier_rejects_rechecksummed_semantically_wrong_regime(
    tmp_path: Path,
) -> None:
    run_id, root, source = _publish_regime_run(tmp_path)
    path = root / "regimes.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    with indicator_decimal_context():
        fast_ema = Decimal("2")
        slow_ema = Decimal("1")
        atr = Decimal("1")
        rows[2].update(
            {
                "regime": "trend",
                "trend_direction": "up",
                "fast_ema": "2",
                "slow_ema": "1",
                "atr": "1",
                "atr_ratio": decimal_text(contextual(atr / source.candles[2].close)),
                "trend_strength": decimal_text(contextual(abs(fast_ema - slow_ema) / atr)),
            }
        )
    path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))
    _refresh_artifact_checksum(root, "regimes.jsonl")

    with pytest.raises(BacktestResultCorruptError, match="candles do snapshot"):
        BacktestResultVerifier(
            tmp_path,
            snapshot_factory=lambda _path: _Reader(source.snapshot, source.candles),
        ).verify(run_id)


def test_cli_builds_identity_bearing_regime_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    monkeypatch.setattr(
        commands_module,
        "MarketDatasetReader",
        lambda _path: _Reader(snapshot, candles),
    )
    args = _parser().parse_args(
        _plan_arguments(
            "--market-regime",
            "--regime-fast-ema-period",
            "3",
            "--regime-slow-ema-period",
            "8",
            "--regime-atr-period",
            "5",
            "--regime-volatile-atr-ratio",
            "0.04",
            "--regime-trend-strength-threshold",
            "1.5",
        )
    )

    prepared = prepare_backtest(args, settings=_settings(tmp_path))

    assert isinstance(prepared.config, RegimeAwareBacktestConfig)
    assert prepared.config.market_regime_policy.fast_ema_period == 3
    assert prepared.config.market_regime_policy.slow_ema_period == 8
    assert prepared.config.market_regime_policy.atr_period == 5
    assert prepared.config.market_regime_policy.volatile_atr_ratio == Decimal("0.04")
    assert prepared.config.market_regime_policy.trend_strength_threshold == Decimal("1.5")


def test_cli_rejects_regime_override_without_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    monkeypatch.setattr(
        commands_module,
        "MarketDatasetReader",
        lambda _path: _Reader(snapshot, candles),
    )
    args = _parser().parse_args(_plan_arguments("--regime-fast-ema-period", "3"))

    with pytest.raises(InvalidDomainInputError, match="--market-regime"):
        prepare_backtest(args, settings=_settings(tmp_path))
