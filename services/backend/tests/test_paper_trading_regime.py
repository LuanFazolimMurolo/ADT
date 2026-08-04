"""Deterministic market-regime integration for paper trading."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import StrategyDescriptor
from app.backtesting.serialization import canonical_value
from app.domain.errors import InvalidDomainInputError
from app.indicators.regime import (
    MarketRegimeKind,
    MarketRegimePolicy,
)
from app.paper_trading.commands import (
    _market_regime_policy,
    configure_paper_trading_parser,
)
from app.paper_trading.documents import (
    decode_paper_config,
    decode_paper_state,
    encode_paper_config,
    encode_paper_state,
)
from app.paper_trading.domain import (
    PaperSessionConfig,
    paper_session_id,
    validate_paper_session_state,
)
from app.paper_trading.errors import (
    InvalidPaperSessionError,
    PaperSessionCorruptError,
)
from app.paper_trading.service import PaperTradingService
from app.strategies.registry import StrategyPluginRegistry
from tests.test_paper_trading import FakeSource, _candle, _config, _service


def _policy() -> MarketRegimePolicy:
    return MarketRegimePolicy(
        fast_ema_period=2,
        slow_ema_period=3,
        atr_period=2,
        volatile_atr_ratio=Decimal("0.5"),
        trend_strength_threshold=Decimal("0.1"),
    )


def _regime_config() -> PaperSessionConfig:
    return replace(
        _config(),
        market_regime_policy=_policy(),
        schema_version=2,
    )


def test_regime_config_round_trip_and_identity_are_policy_bound() -> None:
    legacy = _config()
    configured = _regime_config()

    assert configured != legacy
    assert paper_session_id(configured) != paper_session_id(legacy)
    assert decode_paper_config(encode_paper_config(configured)) == configured

    legacy_payload = json.loads(encode_paper_config(legacy))["config"]
    configured_payload = json.loads(encode_paper_config(configured))["config"]
    assert "market_regime_policy" not in legacy_payload
    assert configured_payload["market_regime_policy"] == canonical_value(_policy())


def test_config_schema_requires_policy_only_in_version_two() -> None:
    with pytest.raises(InvalidPaperSessionError):
        replace(_config(), market_regime_policy=_policy())
    with pytest.raises(InvalidPaperSessionError):
        replace(_config(), schema_version=2)


def test_regime_paper_replay_persists_latest_verified_closed_candle(
    tmp_path: Path,
) -> None:
    candles = tuple(
        _candle(index, close) for index, close in enumerate(("100", "105", "110", "120"))
    )
    source = FakeSource(candles)
    service = _service(tmp_path, source)
    config = _regime_config()
    session_id = paper_session_id(config)
    service.create(config)

    state = service.run_once(session_id).state

    assert state.schema_version == 2
    assert state.latest_market_regime is not None
    assert state.latest_market_regime.event_time == candles[-1].close_time
    assert state.latest_market_regime.regime is MarketRegimeKind.TREND
    assert decode_paper_state(encode_paper_state(state)) == state
    assert service.verify(session_id) == state

    state_payload = json.loads(encode_paper_state(state))["state"]
    assert state_payload["latest_market_regime"]["event_time"] == (
        candles[-1].close_time.isoformat()
    )


def test_regime_warmup_accounts_for_paper_context_candles(tmp_path: Path) -> None:
    candles = tuple(_candle(index, close) for index, close in enumerate(("100", "105", "110")))
    source = FakeSource(candles)
    service = PaperTradingService(
        tmp_path,
        source=source,
        registry=StrategyPluginRegistry.builtins(),
        clock=lambda: datetime(2026, 8, 2, tzinfo=UTC),
    )
    config = replace(
        _config(warmup=2, lifecycle=2),
        strategy=StrategyDescriptor("no-op", "2"),
        market_regime_policy=_policy(),
        schema_version=2,
    )
    session_id = paper_session_id(config)
    service.create(config)

    state = service.run_once(session_id).state

    assert state.candles_processed == 1
    assert state.latest_market_regime is not None
    assert state.latest_market_regime.regime is not MarketRegimeKind.WARMUP
    assert service.verify(session_id) == state


def test_legacy_paper_state_keeps_original_schema_and_document_shape(
    tmp_path: Path,
) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)

    state = service.run_once(session_id).state
    payload = json.loads(encode_paper_state(state))["state"]

    assert state.schema_version == 1
    assert state.latest_market_regime is None
    assert "latest_market_regime" not in payload


def test_state_validation_rejects_mutated_latest_regime(tmp_path: Path) -> None:
    source = FakeSource(tuple(_candle(index) for index in range(4)))
    service = _service(tmp_path, source)
    config = _regime_config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    assert state.latest_market_regime is not None

    object.__setattr__(
        state.latest_market_regime,
        "event_time",
        state.latest_market_regime.event_time + timedelta(minutes=1),
    )

    with pytest.raises(PaperSessionCorruptError):
        validate_paper_session_state(state)


def test_cli_policy_is_opt_in_and_rejects_detached_overrides() -> None:
    parser = argparse.ArgumentParser()
    configure_paper_trading_parser(parser)

    detached = parser.parse_args(
        [
            "create",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1m",
            "--start",
            "2026-08-01T00:00:00Z",
            "--strategy",
            "noop",
            "--strategy-version",
            "1",
            "--initial-capital",
            "1000",
            "--regime-fast-ema-period",
            "5",
        ]
    )
    with pytest.raises(InvalidDomainInputError, match="--market-regime"):
        _market_regime_policy(detached)

    enabled = parser.parse_args(
        [
            "create",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1m",
            "--start",
            "2026-08-01T00:00:00Z",
            "--strategy",
            "noop",
            "--strategy-version",
            "1",
            "--initial-capital",
            "1000",
            "--market-regime",
            "--regime-fast-ema-period",
            "5",
            "--regime-slow-ema-period",
            "10",
        ]
    )
    policy = _market_regime_policy(enabled)
    assert policy is not None
    assert policy.fast_ema_period == 5
    assert policy.slow_ema_period == 10
