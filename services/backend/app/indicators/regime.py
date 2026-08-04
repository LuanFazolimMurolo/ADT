"""Deterministic explainable market-regime classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.indicators._math import contextual, indicator_decimal_context
from app.indicators.atr import AverageTrueRange
from app.indicators.domain import CandleSeries, DecimalSeries, IndicatorDescriptor
from app.indicators.ema import ExponentialMovingAverage
from app.indicators.errors import InvalidIndicatorInputError

_ZERO = Decimal("0")
SUPPORTED_MARKET_REGIME_POLICY_SCHEMA_VERSIONS = frozenset({1})


class MarketRegimeKind(StrEnum):
    """Mutually exclusive deterministic regime classes."""

    WARMUP = "warmup"
    TREND = "trend"
    RANGE = "range"
    VOLATILE = "volatile"


class TrendDirection(StrEnum):
    """Directional evidence emitted only for the trend regime."""

    NONE = "none"
    UP = "up"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class MarketRegimePolicy:
    """Versioned thresholds and periods for the deterministic heuristic."""

    fast_ema_period: int = 12
    slow_ema_period: int = 26
    atr_period: int = 14
    volatile_atr_ratio: Decimal = Decimal("0.03")
    trend_strength_threshold: Decimal = Decimal("1")
    schema_version: int = 1
    descriptor: IndicatorDescriptor = field(init=False)

    def __post_init__(self) -> None:
        periods = (self.fast_ema_period, self.slow_ema_period, self.atr_period)
        if any(type(period) is not int or period < 1 for period in periods):
            raise InvalidIndicatorInputError("market-regime periods must be positive integers")
        if self.fast_ema_period >= self.slow_ema_period:
            raise InvalidIndicatorInputError("fast_ema_period must be smaller than slow_ema_period")
        thresholds = (self.volatile_atr_ratio, self.trend_strength_threshold)
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= _ZERO
            for value in thresholds
        ):
            raise InvalidIndicatorInputError(
                "market-regime thresholds must be positive finite Decimal values"
            )
        if (
            type(self.schema_version) is not int
            or self.schema_version not in SUPPORTED_MARKET_REGIME_POLICY_SCHEMA_VERSIONS
        ):
            raise InvalidIndicatorInputError(
                f"unsupported market-regime policy schema version: {self.schema_version}"
            )

        object.__setattr__(
            self,
            "descriptor",
            IndicatorDescriptor(
                "market_regime",
                "1",
                (
                    ("atr_period", self.atr_period),
                    ("fast_ema_period", self.fast_ema_period),
                    ("slow_ema_period", self.slow_ema_period),
                    ("trend_strength_threshold", self.trend_strength_threshold),
                    ("volatile_atr_ratio", self.volatile_atr_ratio),
                ),
            ),
        )

    @property
    def warmup_points(self) -> int:
        """Return the prefix where at least one required input is unavailable."""

        return max(self.slow_ema_period - 1, self.atr_period - 1)

    @property
    def canonical_key(self) -> tuple[object, ...]:
        """Return the complete compatibility and identity key."""

        return self.schema_version, self.descriptor.canonical_key


@dataclass(frozen=True, slots=True)
class MarketRegimePoint:
    """One closed-candle classification with finite explainability metrics."""

    event_time: datetime
    regime: MarketRegimeKind
    trend_direction: TrendDirection
    fast_ema: Decimal | None
    slow_ema: Decimal | None
    atr: Decimal | None
    atr_ratio: Decimal | None
    trend_strength: Decimal | None

    def __post_init__(self) -> None:
        event_time = _require_utc(self.event_time)
        object.__setattr__(self, "event_time", event_time)
        if (
            type(self.regime) is not MarketRegimeKind
            or type(self.trend_direction) is not TrendDirection
        ):
            raise InvalidIndicatorInputError(
                "market-regime kind and direction must use canonical enums"
            )

        metrics = (
            self.fast_ema,
            self.slow_ema,
            self.atr,
            self.atr_ratio,
            self.trend_strength,
        )
        if self.regime is MarketRegimeKind.WARMUP:
            if self.trend_direction is not TrendDirection.NONE or any(
                value is not None for value in metrics
            ):
                raise InvalidIndicatorInputError(
                    "warmup regime points must not expose classification metrics"
                )
            return

        if any(
            value is None or not isinstance(value, Decimal) or not value.is_finite()
            for value in metrics
        ):
            raise InvalidIndicatorInputError(
                "classified regime points require finite Decimal metrics"
            )
        if _required(self.atr) < _ZERO:
            raise InvalidIndicatorInputError("ATR must be nonnegative")
        if _required(self.atr_ratio) < _ZERO or _required(self.trend_strength) < _ZERO:
            raise InvalidIndicatorInputError(
                "market-regime ratios and strengths must be nonnegative"
            )
        if self.regime is MarketRegimeKind.TREND:
            if self.trend_direction not in {TrendDirection.UP, TrendDirection.DOWN}:
                raise InvalidIndicatorInputError(
                    "trend regime points require an up or down direction"
                )
        elif self.trend_direction is not TrendDirection.NONE:
            raise InvalidIndicatorInputError(
                "range and volatile regime points must use direction none"
            )


@dataclass(frozen=True, slots=True)
class MarketRegimeSeries:
    """Chronological regime output aligned to one closed-candle source."""

    policy: MarketRegimePolicy
    points: tuple[MarketRegimePoint, ...]

    def __post_init__(self) -> None:
        if any(
            left.event_time >= right.event_time for left, right in zip(self.points, self.points[1:])
        ):
            raise InvalidIndicatorInputError(
                "market-regime event times must be strictly chronological"
            )
        unavailable = min(self.policy.warmup_points, len(self.points))
        if any(point.regime is not MarketRegimeKind.WARMUP for point in self.points[:unavailable]):
            raise InvalidIndicatorInputError(
                "market-regime values must remain unavailable during warmup"
            )
        if any(point.regime is MarketRegimeKind.WARMUP for point in self.points[unavailable:]):
            raise InvalidIndicatorInputError("market-regime values must be available after warmup")

    def __len__(self) -> int:
        return len(self.points)

    def at(self, index: int) -> MarketRegimePoint:
        """Return one point without accepting negative indexing."""

        if isinstance(index, bool) or index < 0 or index >= len(self.points):
            raise InvalidIndicatorInputError("market-regime index is outside the available range")
        return self.points[index]


@dataclass(frozen=True, slots=True)
class DeterministicMarketRegimeDetector:
    """Classify trend, range or volatility from closed-candle prefixes only."""

    policy: MarketRegimePolicy = MarketRegimePolicy()

    @property
    def descriptor(self) -> IndicatorDescriptor:
        return self.policy.descriptor

    @property
    def warmup_points(self) -> int:
        return self.policy.warmup_points

    def calculate(self, source: CandleSeries) -> MarketRegimeSeries:
        if any(candle.close <= _ZERO for candle in source.candles):
            raise InvalidIndicatorInputError(
                "market-regime classification requires positive close prices"
            )

        closes = DecimalSeries.from_candles(source.candles, field="close")
        fast = ExponentialMovingAverage(self.policy.fast_ema_period).calculate(closes)
        slow = ExponentialMovingAverage(self.policy.slow_ema_period).calculate(closes)
        atr = AverageTrueRange(self.policy.atr_period).calculate(source)

        points: list[MarketRegimePoint] = []
        with indicator_decimal_context():
            for candle, fast_point, slow_point, atr_point in zip(
                source.candles,
                fast.points,
                slow.points,
                atr.points,
                strict=True,
            ):
                if fast_point.value is None or slow_point.value is None or atr_point.value is None:
                    points.append(
                        MarketRegimePoint(
                            event_time=candle.close_time,
                            regime=MarketRegimeKind.WARMUP,
                            trend_direction=TrendDirection.NONE,
                            fast_ema=None,
                            slow_ema=None,
                            atr=None,
                            atr_ratio=None,
                            trend_strength=None,
                        )
                    )
                    continue

                fast_value = fast_point.value
                slow_value = slow_point.value
                atr_value = atr_point.value
                atr_ratio = contextual(atr_value / candle.close)
                trend_strength = (
                    _ZERO
                    if atr_value == _ZERO
                    else contextual(abs(fast_value - slow_value) / atr_value)
                )
                regime, direction = _classify(
                    fast_ema=fast_value,
                    slow_ema=slow_value,
                    atr_ratio=atr_ratio,
                    trend_strength=trend_strength,
                    policy=self.policy,
                )
                points.append(
                    MarketRegimePoint(
                        event_time=candle.close_time,
                        regime=regime,
                        trend_direction=direction,
                        fast_ema=fast_value,
                        slow_ema=slow_value,
                        atr=atr_value,
                        atr_ratio=atr_ratio,
                        trend_strength=trend_strength,
                    )
                )

        return MarketRegimeSeries(policy=self.policy, points=tuple(points))


def calculate_market_regimes_as_of(
    detector: DeterministicMarketRegimeDetector,
    source: CandleSeries,
    *,
    as_of_index: int,
) -> MarketRegimeSeries:
    """Classify only an already-observed closed-candle prefix."""

    return detector.calculate(source.prefix(as_of_index))


def _classify(
    *,
    fast_ema: Decimal,
    slow_ema: Decimal,
    atr_ratio: Decimal,
    trend_strength: Decimal,
    policy: MarketRegimePolicy,
) -> tuple[MarketRegimeKind, TrendDirection]:
    if atr_ratio >= policy.volatile_atr_ratio:
        return MarketRegimeKind.VOLATILE, TrendDirection.NONE
    if trend_strength >= policy.trend_strength_threshold and fast_ema != slow_ema:
        direction = TrendDirection.UP if fast_ema > slow_ema else TrendDirection.DOWN
        return MarketRegimeKind.TREND, direction
    return MarketRegimeKind.RANGE, TrendDirection.NONE


def _required(value: Decimal | None) -> Decimal:
    if value is None:
        raise AssertionError("a classified market-regime metric was unexpectedly unavailable")
    return value


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidIndicatorInputError("event_time must include UTC timezone")
    if value.utcoffset() != timedelta(0):
        raise InvalidIndicatorInputError("event_time must be UTC")
    return value.astimezone(UTC)
