"""Stateful no-look-ahead market-regime classification for streaming engines."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from decimal import Decimal

from app.indicators._math import contextual, indicator_decimal_context
from app.indicators.domain import CandleSeries
from app.indicators.errors import InvalidIndicatorInputError
from app.indicators.regime import (
    MarketRegimeKind,
    MarketRegimePoint,
    MarketRegimePolicy,
    TrendDirection,
)
from app.market_data.domain import Candle

_ZERO = Decimal("0")


@dataclass(slots=True)
class MarketRegimeAccumulator:
    """Incrementally classify closed candles using fixed-memory indicator state."""

    policy: MarketRegimePolicy = MarketRegimePolicy()
    _count: int = field(init=False, default=0)
    _previous_candle: Candle | None = field(init=False, default=None)
    _fast_seed: Decimal = field(init=False, default=_ZERO)
    _slow_seed: Decimal = field(init=False, default=_ZERO)
    _atr_seed: Decimal = field(init=False, default=_ZERO)
    _fast_ema: Decimal | None = field(init=False, default=None)
    _slow_ema: Decimal | None = field(init=False, default=None)
    _atr: Decimal | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if type(self.policy) is not MarketRegimePolicy:
            raise InvalidIndicatorInputError("market-regime policy must be canonical")
        candidate = copy(self.policy)
        try:
            MarketRegimePolicy.__post_init__(candidate)
        except Exception:
            raise InvalidIndicatorInputError("market-regime policy must be canonical") from None
        if candidate != self.policy:
            raise InvalidIndicatorInputError("market-regime policy must be canonical")

    @property
    def points_seen(self) -> int:
        """Return how many closed candles were consumed."""

        return self._count

    def update(self, candle: Candle) -> MarketRegimePoint:
        """Consume exactly one new closed candle and return its classification."""

        if not isinstance(candle, Candle):
            raise InvalidIndicatorInputError("market-regime input must be a candle")
        source = (candle,) if self._previous_candle is None else (self._previous_candle, candle)
        CandleSeries(source)
        if candle.close <= _ZERO:
            raise InvalidIndicatorInputError(
                "market-regime classification requires positive close prices"
            )

        count = self._count + 1
        with indicator_decimal_context():
            true_range = _true_range(candle, self._previous_candle)
            fast_ema, fast_seed = _next_ema(
                close=candle.close,
                count=count,
                period=self.policy.fast_ema_period,
                seed=self._fast_seed,
                previous=self._fast_ema,
            )
            slow_ema, slow_seed = _next_ema(
                close=candle.close,
                count=count,
                period=self.policy.slow_ema_period,
                seed=self._slow_seed,
                previous=self._slow_ema,
            )
            atr, atr_seed = _next_atr(
                true_range=true_range,
                count=count,
                period=self.policy.atr_period,
                seed=self._atr_seed,
                previous=self._atr,
            )
            point = _point_for(
                candle=candle,
                fast_ema=fast_ema,
                slow_ema=slow_ema,
                atr=atr,
                policy=self.policy,
            )

        self._count = count
        self._previous_candle = candle
        self._fast_seed = fast_seed
        self._slow_seed = slow_seed
        self._atr_seed = atr_seed
        self._fast_ema = fast_ema
        self._slow_ema = slow_ema
        self._atr = atr
        return point


def _point_for(
    *,
    candle: Candle,
    fast_ema: Decimal | None,
    slow_ema: Decimal | None,
    atr: Decimal | None,
    policy: MarketRegimePolicy,
) -> MarketRegimePoint:
    if fast_ema is None or slow_ema is None or atr is None:
        return MarketRegimePoint(
            event_time=candle.close_time,
            regime=MarketRegimeKind.WARMUP,
            trend_direction=TrendDirection.NONE,
            fast_ema=None,
            slow_ema=None,
            atr=None,
            atr_ratio=None,
            trend_strength=None,
        )

    atr_ratio = contextual(atr / candle.close)
    trend_strength = _ZERO if atr == _ZERO else contextual(abs(fast_ema - slow_ema) / atr)
    if atr_ratio >= policy.volatile_atr_ratio:
        regime = MarketRegimeKind.VOLATILE
        direction = TrendDirection.NONE
    elif trend_strength >= policy.trend_strength_threshold and fast_ema != slow_ema:
        regime = MarketRegimeKind.TREND
        direction = TrendDirection.UP if fast_ema > slow_ema else TrendDirection.DOWN
    else:
        regime = MarketRegimeKind.RANGE
        direction = TrendDirection.NONE

    return MarketRegimePoint(
        event_time=candle.close_time,
        regime=regime,
        trend_direction=direction,
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        atr=atr,
        atr_ratio=atr_ratio,
        trend_strength=trend_strength,
    )


def _true_range(candle: Candle, previous: Candle | None) -> Decimal:
    intrabar = candle.high - candle.low
    if previous is None:
        return contextual(intrabar)
    return contextual(
        max(
            intrabar,
            abs(candle.high - previous.close),
            abs(candle.low - previous.close),
        )
    )


def _next_ema(
    *,
    close: Decimal,
    count: int,
    period: int,
    seed: Decimal,
    previous: Decimal | None,
) -> tuple[Decimal | None, Decimal]:
    if count <= period:
        seed = contextual(seed + close)
    if count < period:
        return None, seed
    if count == period:
        return contextual(seed / Decimal(period)), seed
    if previous is None:
        raise AssertionError("EMA state was unavailable after warmup")
    alpha = contextual(Decimal("2") / Decimal(period + 1))
    return contextual(previous + alpha * (close - previous)), seed


def _next_atr(
    *,
    true_range: Decimal,
    count: int,
    period: int,
    seed: Decimal,
    previous: Decimal | None,
) -> tuple[Decimal | None, Decimal]:
    if count <= period:
        seed = contextual(seed + true_range)
    if count < period:
        return None, seed
    period_decimal = Decimal(period)
    if count == period:
        return contextual(seed / period_decimal), seed
    if previous is None:
        raise AssertionError("ATR state was unavailable after warmup")
    return (
        contextual((previous * Decimal(period - 1) + true_range) / period_decimal),
        seed,
    )
