"""Typed indicator protocol and bounded evaluation helpers."""

from __future__ import annotations

from typing import Protocol

from app.indicators.domain import (
    CandleSeries,
    DecimalSeries,
    IndicatorBundle,
    IndicatorDescriptor,
    IndicatorSeries,
)


class CandleTechnicalIndicator(Protocol):
    """Deterministic indicator evaluated from fully closed candles."""

    @property
    def descriptor(self) -> IndicatorDescriptor: ...

    @property
    def warmup_points(self) -> int: ...

    def calculate(self, source: CandleSeries) -> IndicatorSeries: ...


class CompositeTechnicalIndicator(Protocol):
    """Deterministic indicator that exposes multiple aligned output series."""

    @property
    def descriptor(self) -> IndicatorDescriptor: ...

    @property
    def warmup_points(self) -> int: ...

    def calculate(self, source: DecimalSeries) -> IndicatorBundle: ...


class TechnicalIndicator(Protocol):
    """Deterministic indicator boundary independent from strategy state."""

    @property
    def descriptor(self) -> IndicatorDescriptor: ...

    @property
    def warmup_points(self) -> int: ...

    def calculate(self, source: DecimalSeries) -> IndicatorSeries: ...


def calculate_as_of(
    indicator: TechnicalIndicator,
    source: DecimalSeries,
    *,
    as_of_index: int,
) -> IndicatorSeries:
    """Evaluate against a materialized prefix so future input cannot be observed."""

    return indicator.calculate(source.prefix(as_of_index))


def calculate_candles_as_of(
    indicator: CandleTechnicalIndicator,
    source: CandleSeries,
    *,
    as_of_index: int,
) -> IndicatorSeries:
    """Evaluate from a candle prefix so future OHLCV cannot be observed."""

    return indicator.calculate(source.prefix(as_of_index))


def calculate_composite_as_of(
    indicator: CompositeTechnicalIndicator,
    source: DecimalSeries,
    *,
    as_of_index: int,
) -> IndicatorBundle:
    """Evaluate a composite indicator from an already-observed source prefix."""

    return indicator.calculate(source.prefix(as_of_index))
