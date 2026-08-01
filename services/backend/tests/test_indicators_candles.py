from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.indicators import (
    CandleSeries,
    DecimalSeries,
    FutureDataAccessError,
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
    open_price: str = "10",
    high: str = "12",
    low: str = "9",
    close: str = "11",
    symbol: str = "BTC/USDT",
    is_closed: bool = True,
) -> Candle:
    open_time = _START + timedelta(minutes=index)
    return Candle(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol=symbol,
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
        is_closed=is_closed,
        source="test",
    )


def test_candle_series_requires_closed_homogeneous_chronological_candles() -> None:
    valid = CandleSeries((_candle(0), _candle(1)))
    assert len(valid) == 2
    assert valid.at(1).close == Decimal("11")

    with pytest.raises(InvalidIndicatorInputError):
        CandleSeries((_candle(0), _candle(1, is_closed=False)))
    with pytest.raises(InvalidIndicatorInputError):
        CandleSeries((_candle(0), _candle(1, symbol="ETH/USDT")))
    with pytest.raises(InvalidIndicatorInputError):
        CandleSeries((_candle(1), _candle(0)))


def test_decimal_series_from_candles_reuses_closed_homogeneous_validation() -> None:
    series = DecimalSeries.from_candles((_candle(0), _candle(1)), field="close")

    assert tuple(point.value for point in series.points) == (
        Decimal("11"),
        Decimal("11"),
    )
    with pytest.raises(InvalidIndicatorInputError):
        DecimalSeries.from_candles((_candle(0), _candle(1, is_closed=False)))
    with pytest.raises(InvalidIndicatorInputError):
        DecimalSeries.from_candles((_candle(0), _candle(1, symbol="ETH/USDT")))


def test_candle_series_is_immutable_and_rejects_negative_indexes() -> None:
    series = CandleSeries((_candle(0), _candle(1)))

    with pytest.raises(AttributeError):
        series.candles = ()  # type: ignore[misc]
    with pytest.raises(InvalidIndicatorInputError):
        series.at(-1)


def test_bounded_candle_view_rejects_future_data() -> None:
    series = CandleSeries((_candle(0), _candle(1), _candle(2)))
    view = series.through(1)

    assert len(view) == 2
    assert view.latest.open_time == _START + timedelta(minutes=1)
    assert view.materialize() == series.prefix(1)
    with pytest.raises(FutureDataAccessError):
        view.at(2)


def test_calculate_candles_as_of_matches_full_prefix() -> None:
    series = CandleSeries(
        (
            _candle(0, high="10", low="8", close="9"),
            _candle(1, high="11", low="10", close="10"),
            _candle(2, open_price="8", high="9", low="7", close="8"),
        )
    )
    full = TrueRange().calculate(series)
    bounded = calculate_candles_as_of(TrueRange(), series, as_of_index=1)

    assert bounded.points == full.points[:2]
