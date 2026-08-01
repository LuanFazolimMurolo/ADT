"""Closed-trade and deterministic metric tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pytest

from app.backtesting.domain import (
    EquityPoint,
    Fill,
    FillLiquidity,
    FillReason,
    OrderSide,
    PortfolioSnapshot,
    SimulatedOrder,
)
from app.backtesting.metrics import calculate_metrics, derive_closed_trades, metrics_for_schema
from tests.market_data_helpers import utc


def _fill(
    sequence: int,
    side: OrderSide,
    quantity: str,
    price: str,
    *,
    fee: str = "0",
    candle_index: int | None = None,
) -> Fill:
    quantity_value = Decimal(quantity)
    price_value = Decimal(price)
    return Fill(
        fill_id=f"fill-{sequence:08d}",
        order_id=f"order-{sequence:08d}",
        reason=FillReason.MARKET_OPEN,
        liquidity=FillLiquidity.TAKER,
        side=side,
        quantity=quantity_value,
        base_price=price_value,
        execution_price=price_value,
        notional=quantity_value * price_value,
        fee=Decimal(fee),
        slippage_cost=Decimal("0"),
        event_time=utc(2026, 1, 1, sequence),
        candle_index=sequence if candle_index is None else candle_index,
    )


def test_closed_trades_use_average_cost_and_allocate_entry_fees() -> None:
    fills = (
        _fill(1, OrderSide.BUY, "1", "100", fee="1"),
        _fill(2, OrderSide.BUY, "1", "200", fee="2"),
        _fill(3, OrderSide.SELL, "1", "180", fee="1.8"),
        _fill(4, OrderSide.SELL, "1", "210", fee="2.1"),
    )

    trades = derive_closed_trades(fills)

    assert len(trades) == 2
    assert trades[0].average_entry == Decimal("150")
    assert trades[0].gross_pnl == Decimal("30")
    assert trades[0].fees == Decimal("3.3")
    assert trades[0].net_pnl == Decimal("26.7")
    assert trades[1].gross_pnl == Decimal("60")
    assert trades[1].fees == Decimal("3.6")
    assert trades[1].net_pnl == Decimal("56.4")


def test_partial_sale_keeps_remaining_average_cost() -> None:
    fills = (
        _fill(1, OrderSide.BUY, "2", "100"),
        _fill(2, OrderSide.SELL, "0.5", "120"),
        _fill(3, OrderSide.SELL, "1.5", "90"),
    )

    trades = derive_closed_trades(fills)

    assert [trade.average_entry for trade in trades] == [Decimal("100"), Decimal("100")]
    assert [trade.quantity for trade in trades] == [Decimal("0.5"), Decimal("1.5")]


def test_sell_without_position_is_rejected() -> None:
    try:
        derive_closed_trades((_fill(1, OrderSide.SELL, "1", "100"),))
    except ValueError as error:
        assert "exceed" in str(error)
    else:  # pragma: no cover
        raise AssertionError("sell without position must fail")


@dataclass(frozen=True)
class _Execution:
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[Fill, ...]
    equity_curve: tuple[EquityPoint, ...]
    final_portfolio: PortfolioSnapshot


def test_metrics_handle_zero_trades_without_infinity() -> None:
    point = EquityPoint(
        candle_index=0,
        event_time=utc(2026, 1, 1, 1),
        close_price=Decimal("100"),
        quote_cash=Decimal("1000"),
        base_quantity=Decimal("0"),
        equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        drawdown=Decimal("0"),
        drawdown_pct=Decimal("0"),
    )
    portfolio = PortfolioSnapshot(
        quote_cash=Decimal("1000"),
        base_quantity=Decimal("0"),
        average_entry_price=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        drawdown=Decimal("0"),
        cost_basis=Decimal("0"),
        drawdown_pct=Decimal("0"),
    )

    metrics = calculate_metrics(
        _Execution((), (), (point,), portfolio),
        initial_equity=Decimal("1000"),
    )

    assert metrics.total_return == 0
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.expectancy is None
    assert metrics.exposure_pct == 0


def test_metrics_calculate_drawdown_exposure_turnover_and_benchmark() -> None:
    fills = (
        _fill(1, OrderSide.BUY, "1", "100"),
        _fill(2, OrderSide.SELL, "1", "120"),
    )
    points = (
        EquityPoint(
            0,
            utc(2026, 1, 1, 1),
            Decimal("100"),
            Decimal("900"),
            Decimal("1"),
            Decimal("1000"),
            Decimal("1000"),
            Decimal("0"),
            Decimal("0"),
        ),
        EquityPoint(
            1,
            utc(2026, 1, 1, 2),
            Decimal("80"),
            Decimal("900"),
            Decimal("1"),
            Decimal("980"),
            Decimal("1000"),
            Decimal("20"),
            Decimal("2"),
        ),
        EquityPoint(
            2,
            utc(2026, 1, 1, 3),
            Decimal("120"),
            Decimal("1020"),
            Decimal("0"),
            Decimal("1020"),
            Decimal("1020"),
            Decimal("0"),
            Decimal("0"),
        ),
    )
    portfolio = PortfolioSnapshot(
        quote_cash=Decimal("1020"),
        base_quantity=Decimal("0"),
        average_entry_price=Decimal("0"),
        realized_pnl=Decimal("20"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=Decimal("1020"),
        peak_equity=Decimal("1020"),
        drawdown=Decimal("0"),
        cost_basis=Decimal("0"),
        drawdown_pct=Decimal("0"),
    )

    metrics = calculate_metrics(
        _Execution((), fills, points, portfolio), initial_equity=Decimal("1000")
    )

    assert metrics.total_return == Decimal("2")
    assert metrics.maximum_drawdown == Decimal("20")
    assert metrics.maximum_drawdown_pct == Decimal("2")
    assert metrics.exposure_pct == Decimal(2) / Decimal(3) * Decimal("100")
    assert metrics.turnover == Decimal("22")
    assert metrics.buy_and_hold_return == Decimal("20")
    assert metrics.strategy_vs_buy_and_hold == Decimal("-18")


def _equity_point(
    index: int,
    event_time: datetime,
    equity: str,
    *,
    peak: str | None = None,
) -> EquityPoint:
    equity_value = Decimal(equity)
    peak_value = equity_value if peak is None else Decimal(peak)
    drawdown = peak_value - equity_value
    drawdown_pct = Decimal("0") if peak_value == 0 else drawdown / peak_value * Decimal("100")
    return EquityPoint(
        candle_index=index,
        event_time=event_time,
        close_price=Decimal("100"),
        quote_cash=equity_value,
        base_quantity=Decimal("0"),
        equity=equity_value,
        peak_equity=peak_value,
        drawdown=drawdown,
        drawdown_pct=drawdown_pct,
    )


def _final_portfolio(equity: str) -> PortfolioSnapshot:
    value = Decimal(equity)
    return PortfolioSnapshot(
        quote_cash=value,
        base_quantity=Decimal("0"),
        average_entry_price=Decimal("0"),
        realized_pnl=value - Decimal("1000"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=value,
        peak_equity=value,
        drawdown=Decimal("0"),
        cost_basis=Decimal("0"),
        drawdown_pct=Decimal("0"),
    )


def test_one_year_period_normalization_and_cagr_are_exact() -> None:
    point = _equity_point(0, utc(2027, 1, 1), "2000")
    metrics = calculate_metrics(
        _Execution((), (), (point,), _final_portfolio("2000")),
        initial_equity=Decimal("1000"),
        period_start=utc(2026, 1, 1),
    )

    assert metrics.return_periods == 1
    assert metrics.elapsed_seconds == Decimal("31536000")
    assert metrics.periods_per_year == Decimal("1")
    assert metrics.cagr == Decimal("100")
    assert metrics.annualized_volatility == Decimal("0")
    assert metrics.annualized_downside_deviation == Decimal("0")
    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None


def test_sharpe_sortino_and_dispersion_use_zero_rate_and_population_variance() -> None:
    points = (
        _equity_point(0, utc(2026, 1, 2), "1100"),
        _equity_point(1, utc(2026, 1, 3), "990", peak="1100"),
        _equity_point(2, utc(2026, 1, 4), "1089", peak="1100"),
    )
    metrics = calculate_metrics(
        _Execution((), (), points, _final_portfolio("1089")),
        initial_equity=Decimal("1000"),
        period_start=utc(2026, 1, 1),
    )

    assert metrics.return_periods == 3
    assert metrics.periods_per_year == Decimal("365")
    assert metrics.sharpe_ratio is not None
    assert metrics.sortino_ratio is not None
    assert metrics.annualized_volatility is not None
    assert metrics.annualized_downside_deviation is not None
    assert metrics.sharpe_ratio.quantize(Decimal("0.000001")) == Decimal("6.754628")
    assert metrics.sortino_ratio.quantize(Decimal("0.000001")) == Decimal("11.030261")
    assert metrics.annualized_volatility.quantize(Decimal("0.000001")) == Decimal("180.123414")
    assert metrics.annualized_downside_deviation.quantize(Decimal("0.000001")) == Decimal(
        "110.302614"
    )


def test_irregular_observations_are_normalized_by_actual_elapsed_time() -> None:
    points = (
        _equity_point(0, utc(2026, 1, 1, 12), "1000"),
        _equity_point(1, utc(2026, 1, 3), "1000"),
    )
    metrics = calculate_metrics(
        _Execution((), (), points, _final_portfolio("1000")),
        initial_equity=Decimal("1000"),
        period_start=utc(2026, 1, 1),
    )

    assert metrics.return_periods == 2
    assert metrics.elapsed_seconds == Decimal("172800")
    assert metrics.periods_per_year == Decimal("365")


def test_zero_final_equity_has_minus_one_hundred_percent_cagr() -> None:
    point = _equity_point(0, utc(2027, 1, 1), "0", peak="1000")
    metrics = calculate_metrics(
        _Execution((), (), (point,), _final_portfolio("0")),
        initial_equity=Decimal("1000"),
        period_start=utc(2026, 1, 1),
    )

    assert metrics.cagr == Decimal("-100")
    assert metrics.annualized_downside_deviation == Decimal("100")
    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio == Decimal("-1")


def test_period_start_and_equity_observations_must_be_strictly_ordered() -> None:
    point = _equity_point(0, utc(2026, 1, 1), "1000")

    with pytest.raises(ValueError, match="period_start"):
        calculate_metrics(
            _Execution((), (), (point,), _final_portfolio("1000")),
            initial_equity=Decimal("1000"),
            period_start=point.event_time,
        )


def test_schema_one_projection_preserves_phase_3a_metric_artifacts() -> None:
    point = _equity_point(0, utc(2027, 1, 1), "1100")
    metrics = calculate_metrics(
        _Execution((), (), (point,), _final_portfolio("1100")),
        initial_equity=Decimal("1000"),
        period_start=utc(2026, 1, 1),
    )

    legacy = metrics_for_schema(metrics, 1)
    current = metrics_for_schema(metrics, 2)

    assert "sharpe_ratio" not in legacy
    assert "cagr" not in legacy
    assert current["sharpe_ratio"] is None
    assert current["cagr"] == "10"


def test_metric_projection_rejects_unimplemented_schema_version() -> None:
    point = _equity_point(0, utc(2026, 1, 2), "1000")
    metrics = calculate_metrics(
        _Execution((), (), (point,), _final_portfolio("1000")),
        initial_equity=Decimal("1000"),
        period_start=utc(2026, 1, 1),
    )

    with pytest.raises(ValueError, match="schema_version is not supported"):
        metrics_for_schema(metrics, 3)
