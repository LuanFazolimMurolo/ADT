"""Incremental market-regime parity and stream-safety tests."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import cast

import pytest

from app.indicators.domain import CandleSeries
from app.indicators.errors import InvalidIndicatorInputError
from app.indicators.regime import DeterministicMarketRegimeDetector, MarketRegimePolicy
from app.indicators.regime_incremental import MarketRegimeAccumulator
from app.market_data.domain import Candle
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import candle, utc


def _source() -> CandleSeries:
    start = utc(2026, 1, 1)
    rows = (
        ("100", "102", "98", "101"),
        ("101", "104", "100", "103"),
        ("103", "109", "102", "108"),
        ("108", "112", "106", "111"),
        ("111", "113", "109", "110"),
    )
    return CandleSeries(
        tuple(
            candle(
                start + timedelta(hours=index),
                timeframe=get_timeframe("1h"),
                open_price=open_price,
                high=high,
                low=low,
                close=close,
            )
            for index, (open_price, high, low, close) in enumerate(rows)
        )
    )


def _policy() -> MarketRegimePolicy:
    return MarketRegimePolicy(
        fast_ema_period=2,
        slow_ema_period=3,
        atr_period=2,
        volatile_atr_ratio=Decimal("0.20"),
        trend_strength_threshold=Decimal("0.10"),
    )


def test_incremental_accumulator_matches_batch_series_exactly() -> None:
    source = _source()
    batch = DeterministicMarketRegimeDetector(_policy()).calculate(source)
    accumulator = MarketRegimeAccumulator(_policy())

    incremental = tuple(accumulator.update(item) for item in source.candles)

    assert incremental == batch.points
    assert accumulator.points_seen == len(source)


def test_incremental_accumulator_rejects_repeated_candle() -> None:
    source = _source()
    accumulator = MarketRegimeAccumulator(_policy())
    accumulator.update(source.candles[0])

    with pytest.raises(InvalidIndicatorInputError, match="chronological"):
        accumulator.update(source.candles[0])


def test_incremental_accumulator_rejects_hostile_policy_mutation() -> None:
    policy = _policy()
    object.__setattr__(policy, "slow_ema_period", 1)

    with pytest.raises(InvalidIndicatorInputError, match="canonical"):
        MarketRegimeAccumulator(policy)


def test_incremental_accumulator_rejects_non_candle_input() -> None:
    accumulator = MarketRegimeAccumulator(_policy())

    with pytest.raises(InvalidIndicatorInputError, match="must be a candle"):
        accumulator.update(cast(Candle, object()))
