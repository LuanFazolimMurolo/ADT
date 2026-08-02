"""Bounded deterministic visualization contracts for verified backtest runs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.backtesting.domain import BacktestRunId, EquityPoint
from app.backtesting.errors import BacktestResultCorruptError
from app.market_data.domain import require_utc

MIN_VISUALIZATION_POINTS = 2
MAX_VISUALIZATION_POINTS = 2_000
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class BacktestVisualizationPoint:
    """One bounded chart point projected from the immutable equity curve."""

    candle_index: int
    event_time: datetime
    close_price: Decimal
    equity: Decimal
    drawdown_pct: Decimal

    def __post_init__(self) -> None:
        if self.candle_index < 0:
            raise ValueError("visualization candle index must be nonnegative")
        object.__setattr__(
            self,
            "event_time",
            require_utc(self.event_time, field_name="event_time"),
        )
        for value in (self.close_price, self.equity, self.drawdown_pct):
            if not value.is_finite():
                raise ValueError("visualization values must be finite")
        if self.close_price <= 0 or self.equity < 0 or self.drawdown_pct < 0:
            raise ValueError("visualization values are outside the supported range")


@dataclass(frozen=True, slots=True)
class BacktestVisualization:
    """Versioned, visualization-safe projection of one verified backtest."""

    contract_version: int
    run_id: str
    snapshot_id: str
    dataset_key: str
    dataset_version: str
    data_start: datetime
    data_end: datetime
    strategy_name: str
    strategy_version: str
    logical_result_checksum: str
    final_equity: Decimal
    total_return: Decimal
    maximum_drawdown_pct: Decimal
    source_point_count: int
    point_count: int
    max_points: int
    downsampled: bool
    points: tuple[BacktestVisualizationPoint, ...]

    def __post_init__(self) -> None:
        if self.contract_version != 1:
            raise ValueError("unsupported visualization contract version")
        BacktestRunId(self.run_id)
        if (
            _SHA256.fullmatch(self.snapshot_id) is None
            or _SHA256.fullmatch(self.dataset_version) is None
            or _SHA256.fullmatch(self.logical_result_checksum) is None
        ):
            raise ValueError("visualization identity is invalid")
        object.__setattr__(
            self,
            "data_start",
            require_utc(self.data_start, field_name="data_start"),
        )
        object.__setattr__(
            self,
            "data_end",
            require_utc(self.data_end, field_name="data_end"),
        )
        if self.data_end <= self.data_start:
            raise ValueError("visualization data range must be positive")
        if not MIN_VISUALIZATION_POINTS <= self.max_points <= MAX_VISUALIZATION_POINTS:
            raise ValueError("visualization max_points is outside the bounded contract")
        if self.source_point_count < 1:
            raise ValueError("visualization source curve must not be empty")
        if self.point_count != len(self.points):
            raise ValueError("visualization point_count is inconsistent")
        if not 1 <= self.point_count <= self.max_points:
            raise ValueError("visualization point count is outside the bounded contract")
        if self.downsampled is not (self.point_count < self.source_point_count):
            raise ValueError("visualization downsample flag is inconsistent")
        for value in (self.final_equity, self.total_return, self.maximum_drawdown_pct):
            if not value.is_finite():
                raise ValueError("visualization summary metrics must be finite")
        if tuple(point.candle_index for point in self.points) != tuple(
            sorted(point.candle_index for point in self.points)
        ):
            raise ValueError("visualization points must be ordered")
        if len({point.candle_index for point in self.points}) != self.point_count:
            raise ValueError("visualization points must be unique")


def build_backtest_visualization(
    summary: Mapping[str, object],
    equity_curve: Sequence[EquityPoint],
    *,
    max_points: int,
) -> BacktestVisualization:
    """Build one bounded visualization only from a verified summary and curve."""
    if not MIN_VISUALIZATION_POINTS <= max_points <= MAX_VISUALIZATION_POINTS:
        raise ValueError("visualization max_points must be between 2 and 2000")
    if not equity_curve:
        raise BacktestResultCorruptError("A curva de equity verificada está vazia.")

    sampled = _uniform_sample(equity_curve, max_points=max_points)
    try:
        data_range = _mapping(summary.get("evaluation_range", summary.get("data_range")))
        strategy = _mapping(summary.get("strategy"))
        metrics = _mapping(summary.get("metrics"))
        return BacktestVisualization(
            contract_version=1,
            run_id=_string(summary.get("run_id")),
            snapshot_id=_string(summary.get("snapshot_id")),
            dataset_key=_string(summary.get("dataset_key")),
            dataset_version=_string(summary.get("dataset_version")),
            data_start=datetime.fromisoformat(_string(data_range.get("start"))),
            data_end=datetime.fromisoformat(_string(data_range.get("end"))),
            strategy_name=_string(strategy.get("name")),
            strategy_version=_string(strategy.get("version")),
            logical_result_checksum=_string(summary.get("logical_result_checksum")),
            final_equity=_decimal(metrics.get("final_equity")),
            total_return=_decimal(metrics.get("total_return")),
            maximum_drawdown_pct=_decimal(metrics.get("maximum_drawdown_pct")),
            source_point_count=len(equity_curve),
            point_count=len(sampled),
            max_points=max_points,
            downsampled=len(sampled) < len(equity_curve),
            points=tuple(
                BacktestVisualizationPoint(
                    candle_index=point.candle_index,
                    event_time=point.event_time,
                    close_price=point.close_price,
                    equity=point.equity,
                    drawdown_pct=point.drawdown_pct,
                )
                for point in sampled
            ),
        )
    except (InvalidOperation, TypeError, ValueError):
        raise BacktestResultCorruptError(
            "O resumo verificado não respeita o contrato de visualização."
        ) from None


def _uniform_sample(
    values: Sequence[EquityPoint],
    *,
    max_points: int,
) -> tuple[EquityPoint, ...]:
    if len(values) <= max_points:
        return tuple(values)
    last_index = len(values) - 1
    denominator = max_points - 1
    indices = tuple((position * last_index) // denominator for position in range(max_points))
    return tuple(values[index] for index in indices)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError
    return value


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise TypeError
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError
    return result
