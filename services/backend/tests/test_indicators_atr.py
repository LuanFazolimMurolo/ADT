from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from app.indicators import (
    AverageTrueRange,
    CandleSeries,
    InvalidIndicatorInputError,
    TrueRange,
    calculate_candles_as_of,
)
from app.market_data.domain import Candle, Exchange, MarketType, Timeframe

_TIMEFRAME = Timeframe("1m", timedelta(minutes=1))
_START = datetime(2026, 1, 1, tzinfo=UTC)


def _candle(
    index: int,
    *,
    open_price: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    open_time = _START + timedelta(minutes=index)
    return Candle(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTC/USDT",
        timeframe=_TIMEFRAME,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
        quote_volume=None,
        trade_count=None,
        is_closed=True,
        source="test",
    )


def _series() -> CandleSeries:
    return CandleSeries(
        (
            _candle(0, open_price="10", high="11", low="9", close="10"),
            _candle(1, open_price="10", high="14", low="10", close="12"),
            _candle(2, open_price="12", high="18", low="12", close="15"),
            _candle(3, open_price="15", high="23", low="15", close="20"),
        )
    )


def _values(
    indicator: AverageTrueRange | TrueRange,
    source: CandleSeries,
) -> tuple[Decimal | None, ...]:
    return tuple(point.value for point in indicator.calculate(source).points)


def test_true_range_handles_intrabar_and_gap_movements() -> None:
    source = CandleSeries(
        (
            _candle(0, open_price="9", high="10", low="8", close="9"),
            _candle(1, open_price="10", high="11", low="10", close="10"),
            _candle(2, open_price="8", high="9", low="7", close="8"),
        )
    )

    assert _values(TrueRange(), source) == (
        Decimal("2"),
        Decimal("2"),
        Decimal("3"),
    )


def test_atr_uses_wilder_seed_and_smoothing() -> None:
    indicator = AverageTrueRange(2)
    result = indicator.calculate(_series())

    assert indicator.warmup_points == 1
    assert indicator.descriptor.parameters == (("period", 2),)
    assert tuple(point.value for point in result.points) == (
        None,
        Decimal("3"),
        Decimal("4.5"),
        Decimal("6.25"),
    )
    assert tuple(point.event_time for point in result.points) == tuple(
        candle.close_time for candle in _series().candles
    )


def test_atr_period_one_equals_true_range() -> None:
    source = _series()

    assert _values(AverageTrueRange(1), source) == _values(TrueRange(), source)


def test_atr_short_source_contains_only_warmup_values() -> None:
    source = CandleSeries(_series().candles[:2])

    assert _values(AverageTrueRange(4), source) == (None, None)


@pytest.mark.parametrize("period", [0, -1, True])
def test_atr_rejects_invalid_period(period: object) -> None:
    with pytest.raises(InvalidIndicatorInputError):
        AverageTrueRange(period)  # type: ignore[arg-type]


def test_atr_is_independent_from_ambient_decimal_precision() -> None:
    source = _series()

    with localcontext() as context:
        context.prec = 6
        low_precision = _values(AverageTrueRange(3), source)
    with localcontext() as context:
        context.prec = 40
        high_precision = _values(AverageTrueRange(3), source)

    assert low_precision == high_precision


def test_atr_prefix_matches_the_same_prefix_of_full_calculation() -> None:
    source = _series()
    indicator = AverageTrueRange(2)
    full = indicator.calculate(source)
    bounded = calculate_candles_as_of(indicator, source, as_of_index=2)

    assert bounded.points == full.points[:3]
