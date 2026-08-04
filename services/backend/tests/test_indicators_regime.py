from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from app.indicators import (
    CandleSeries,
    DeterministicMarketRegimeDetector,
    InvalidIndicatorInputError,
    MarketRegimeKind,
    MarketRegimePoint,
    MarketRegimePolicy,
    TrendDirection,
    calculate_market_regimes_as_of,
)
from app.market_data.domain import Candle, Exchange, MarketType, Timeframe

_TIMEFRAME = Timeframe("1m", timedelta(minutes=1))
_START = datetime(2026, 1, 1, tzinfo=UTC)


def _candle(index: int, close: str, *, spread: str = "0.2") -> Candle:
    close_value = Decimal(close)
    half_spread = Decimal(spread) / Decimal("2")
    open_time = _START + timedelta(minutes=index)
    return Candle(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTC/USDT",
        timeframe=_TIMEFRAME,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=close_value,
        high=close_value + half_spread,
        low=close_value - half_spread,
        close=close_value,
        volume=Decimal("1"),
        quote_volume=None,
        trade_count=None,
        is_closed=True,
        source="test",
    )


def _series(*closes: str, spread: str = "0.2") -> CandleSeries:
    return CandleSeries(
        tuple(_candle(index, close, spread=spread) for index, close in enumerate(closes))
    )


def _policy(**overrides: object) -> MarketRegimePolicy:
    values: dict[str, object] = {
        "fast_ema_period": 2,
        "slow_ema_period": 3,
        "atr_period": 2,
        "volatile_atr_ratio": Decimal("0.5"),
        "trend_strength_threshold": Decimal("0.2"),
    }
    values.update(overrides)
    return MarketRegimePolicy(**values)  # type: ignore[arg-type]


def test_policy_exposes_canonical_versioned_identity() -> None:
    policy = _policy()

    assert policy.warmup_points == 2
    assert policy.descriptor.name == "market_regime"
    assert policy.descriptor.version == "1"
    assert policy.descriptor.parameters == (
        ("atr_period", 2),
        ("fast_ema_period", 2),
        ("slow_ema_period", 3),
        ("trend_strength_threshold", Decimal("0.2")),
        ("volatile_atr_ratio", Decimal("0.5")),
    )
    assert policy.canonical_key == (1, policy.descriptor.canonical_key)


@pytest.mark.parametrize(
    "overrides",
    [
        {"fast_ema_period": 0},
        {"fast_ema_period": True},
        {"fast_ema_period": Decimal("2")},
        {"fast_ema_period": 3, "slow_ema_period": 3},
        {"atr_period": -1},
        {"volatile_atr_ratio": Decimal("0")},
        {"trend_strength_threshold": Decimal("NaN")},
        {"schema_version": 1.0},
        {"schema_version": 2},
    ],
)
def test_policy_rejects_invalid_or_noncanonical_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(InvalidIndicatorInputError):
        _policy(**overrides)


def test_detector_emits_explicit_warmup_then_range() -> None:
    detector = DeterministicMarketRegimeDetector(_policy())
    result = detector.calculate(_series("100", "100", "100", "100", "100"))

    assert tuple(point.regime for point in result.points) == (
        MarketRegimeKind.WARMUP,
        MarketRegimeKind.WARMUP,
        MarketRegimeKind.RANGE,
        MarketRegimeKind.RANGE,
        MarketRegimeKind.RANGE,
    )
    assert result.points[0].atr is None
    assert result.points[-1].trend_direction is TrendDirection.NONE
    assert result.points[-1].trend_strength == Decimal("0")


def test_detector_identifies_up_and_down_trends() -> None:
    detector = DeterministicMarketRegimeDetector(_policy())

    up = detector.calculate(_series("100", "101", "102", "103", "104"))
    down = detector.calculate(_series("104", "103", "102", "101", "100"))

    assert up.points[-1].regime is MarketRegimeKind.TREND
    assert up.points[-1].trend_direction is TrendDirection.UP
    assert down.points[-1].regime is MarketRegimeKind.TREND
    assert down.points[-1].trend_direction is TrendDirection.DOWN


def test_volatile_regime_has_priority_over_directional_trend() -> None:
    detector = DeterministicMarketRegimeDetector(_policy(volatile_atr_ratio=Decimal("0.1")))
    source = _series("100", "110", "120", "130", "140", spread="40")

    latest = detector.calculate(source).points[-1]

    assert latest.regime is MarketRegimeKind.VOLATILE
    assert latest.trend_direction is TrendDirection.NONE
    assert latest.atr_ratio is not None
    assert latest.atr_ratio >= Decimal("0.1")


def test_as_of_prefix_matches_full_series_without_future_access() -> None:
    detector = DeterministicMarketRegimeDetector(_policy())
    source = _series("100", "101", "102", "101", "100", "99")
    full = detector.calculate(source)

    bounded = calculate_market_regimes_as_of(detector, source, as_of_index=3)

    assert bounded.points == full.points[:4]


def test_detector_is_independent_from_ambient_decimal_precision() -> None:
    detector = DeterministicMarketRegimeDetector(_policy())
    source = _series("100", "101.25", "102.75", "104.125", "105.875")

    with localcontext() as context:
        context.prec = 6
        low_precision = detector.calculate(source)
    with localcontext() as context:
        context.prec = 40
        high_precision = detector.calculate(source)

    assert low_precision == high_precision


def test_detector_rejects_nonpositive_close_anywhere_in_source() -> None:
    detector = DeterministicMarketRegimeDetector(_policy())

    with pytest.raises(InvalidIndicatorInputError):
        detector.calculate(_series("1", "1", "0", "1"))


def test_regime_point_rejects_inconsistent_direction_and_metrics() -> None:
    with pytest.raises(InvalidIndicatorInputError):
        MarketRegimePoint(
            event_time=_START,
            regime=MarketRegimeKind.TREND,
            trend_direction=TrendDirection.NONE,
            fast_ema=Decimal("2"),
            slow_ema=Decimal("1"),
            atr=Decimal("1"),
            atr_ratio=Decimal("0.01"),
            trend_strength=Decimal("1"),
        )


def test_regime_point_rejects_raw_string_enums() -> None:
    with pytest.raises(InvalidIndicatorInputError, match="canonical enums"):
        MarketRegimePoint(
            event_time=_START,
            regime="range",  # type: ignore[arg-type]
            trend_direction=TrendDirection.NONE,
            fast_ema=Decimal("2"),
            slow_ema=Decimal("1"),
            atr=Decimal("1"),
            atr_ratio=Decimal("0.01"),
            trend_strength=Decimal("1"),
        )
