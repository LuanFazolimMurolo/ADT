"""Deterministic ML target extraction from authenticated future candles."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.market_data.domain import Candle, DataRange
from app.market_data.timeframes import get_timeframe
from app.ml.domain import MLCandleDependency, MLObservation, MLTargetSpecification


def compute_target(
    target_spec: MLTargetSpecification,
    candles: Sequence[Candle],
    observation: MLObservation,
) -> tuple[Decimal, MLCandleDependency]:
    """Return one recomputed target value and exact future dependency range."""
    if target_spec.algorithm.name not in {"forward_close_return_v1", "forward_close_return"}:
        raise ValueError("unsupported target recipe")
    if target_spec.algorithm.version != "1":
        raise ValueError("unsupported target algorithm version")
    duration = get_timeframe(observation.dataset.feature_set.timeframe).duration
    future_start = observation.observation_time
    future_end = observation.observation_time + (duration * target_spec.horizon_candles)
    future_range = DataRange(future_start, future_end)
    future = _candles_for_range(candles, future_range)
    if len(future) < target_spec.horizon_candles:
        raise ValueError("insufficient authenticated future coverage")
    baseline_close = _baseline_close(candles, observation)
    future_close = future[-1].close
    if baseline_close <= 0:
        raise ValueError("invalid baseline close for target computation")
    value = (future_close / baseline_close) - Decimal("1")
    dependency = MLCandleDependency(
        source=observation.dataset.provenance.source,
        data_range=future_range,
    )
    return value, dependency


def _baseline_close(candles: Sequence[Candle], observation: MLObservation) -> Decimal:
    for candle in candles:
        if candle.open_time == observation.candle_open_time:
            return candle.close
    raise ValueError("missing observation anchor candle")


def _candles_for_range(candles: Sequence[Candle], data_range: DataRange) -> tuple[Candle, ...]:
    return tuple(
        candle for candle in candles if data_range.start <= candle.open_time < data_range.end
    )
