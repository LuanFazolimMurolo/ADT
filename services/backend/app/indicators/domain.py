"""Immutable contracts for deterministic technical indicators."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypeAlias

from app.indicators.errors import (
    FutureDataAccessError,
    InvalidIndicatorInputError,
    UnsupportedIndicatorSchemaError,
)
from app.market_data.domain import Candle

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SUPPORTED_INDICATOR_SCHEMA_VERSIONS = frozenset({1})

IndicatorParameterValue: TypeAlias = None | bool | int | str | Decimal
IndicatorParameters: TypeAlias = tuple[tuple[str, IndicatorParameterValue], ...]


@dataclass(frozen=True, slots=True)
class IndicatorDescriptor:
    """Stable indicator identity with canonical immutable parameters."""

    name: str
    version: str
    parameters: IndicatorParameters = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        name = self.name.strip()
        version = self.version.strip()
        if _SAFE_TOKEN.fullmatch(name) is None or _SAFE_TOKEN.fullmatch(version) is None:
            raise InvalidIndicatorInputError("indicator name and version must be safe identifiers")
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version not in SUPPORTED_INDICATOR_SCHEMA_VERSIONS
        ):
            raise UnsupportedIndicatorSchemaError(
                f"unsupported indicator schema version: {self.schema_version}"
            )

        normalized: list[tuple[str, IndicatorParameterValue]] = []
        seen: set[str] = set()
        for raw_key, value in self.parameters:
            key = raw_key.strip()
            if _SAFE_TOKEN.fullmatch(key) is None or key in seen:
                raise InvalidIndicatorInputError(
                    "indicator parameter keys must be unique safe identifiers"
                )
            if isinstance(value, float):
                raise InvalidIndicatorInputError(
                    "indicator parameters must not contain float values"
                )
            if isinstance(value, Decimal) and not value.is_finite():
                raise InvalidIndicatorInputError("indicator Decimal parameters must be finite")
            if not isinstance(value, (type(None), bool, int, str, Decimal)):
                raise InvalidIndicatorInputError("unsupported indicator parameter value")
            normalized.append((key, value))
            seen.add(key)

        normalized.sort(key=lambda item: item[0])
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "parameters", tuple(normalized))

    @property
    def canonical_key(self) -> tuple[int, str, str, IndicatorParameters]:
        """Return the complete deterministic compatibility key."""

        return self.schema_version, self.name, self.version, self.parameters


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One finite Decimal value known at an exact UTC event time."""

    event_time: datetime
    value: Decimal

    def __post_init__(self) -> None:
        event_time = _require_utc(self.event_time)
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise InvalidIndicatorInputError("series values must be finite Decimal values")
        object.__setattr__(self, "event_time", event_time)


@dataclass(frozen=True, slots=True)
class DecimalSeries:
    """Chronological immutable Decimal source series."""

    points: tuple[SeriesPoint, ...]

    def __post_init__(self) -> None:
        if any(
            left.event_time >= right.event_time for left, right in zip(self.points, self.points[1:])
        ):
            raise InvalidIndicatorInputError("series event times must be strictly chronological")

    def __len__(self) -> int:
        return len(self.points)

    def at(self, index: int) -> SeriesPoint:
        """Return one point without accepting negative indexing."""

        _require_existing_index(index, len(self.points))
        return self.points[index]

    def prefix(self, as_of_index: int) -> DecimalSeries:
        """Materialize only data available through ``as_of_index``."""

        _require_existing_index(as_of_index, len(self.points))
        return DecimalSeries(self.points[: as_of_index + 1])

    def through(self, as_of_index: int) -> DecimalSeriesView:
        """Create a bounded read view that rejects future access."""

        _require_existing_index(as_of_index, len(self.points))
        return DecimalSeriesView(self, as_of_index)

    @classmethod
    def from_candles(cls, candles: tuple[Candle, ...], *, field: str = "close") -> DecimalSeries:
        """Build a series using candle close times, when OHLCV is fully known."""

        if field not in {"open", "high", "low", "close", "volume"}:
            raise InvalidIndicatorInputError("unsupported candle series field")
        validated = CandleSeries(candles)
        return cls(
            tuple(
                SeriesPoint(candle.close_time, _candle_value(candle, field))
                for candle in validated.candles
            )
        )


@dataclass(frozen=True, slots=True)
class DecimalSeriesView:
    """Read-only source view bounded to an already-observed index."""

    _source: DecimalSeries
    as_of_index: int

    def __post_init__(self) -> None:
        _require_existing_index(self.as_of_index, len(self._source))

    def __len__(self) -> int:
        return self.as_of_index + 1

    @property
    def points(self) -> tuple[SeriesPoint, ...]:
        return self._source.points[: self.as_of_index + 1]

    @property
    def latest(self) -> SeriesPoint:
        return self._source.points[self.as_of_index]

    def at(self, index: int) -> SeriesPoint:
        _require_visible_index(index, self.as_of_index, len(self._source))
        return self._source.points[index]

    def materialize(self) -> DecimalSeries:
        return DecimalSeries(self.points)


@dataclass(frozen=True, slots=True)
class CandleSeries:
    """Chronological immutable series of fully closed candles."""

    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        if any(not candle.is_closed for candle in self.candles):
            raise InvalidIndicatorInputError(
                "indicator candle series must contain only closed candles"
            )
        if self.candles:
            expected_identity = _candle_identity(self.candles[0])
            if any(_candle_identity(candle) != expected_identity for candle in self.candles[1:]):
                raise InvalidIndicatorInputError(
                    "indicator candle series must use one instrument and timeframe"
                )
        if any(
            left.open_time >= right.open_time or left.close_time >= right.close_time
            for left, right in zip(self.candles, self.candles[1:])
        ):
            raise InvalidIndicatorInputError("indicator candles must be strictly chronological")

    def __len__(self) -> int:
        return len(self.candles)

    def at(self, index: int) -> Candle:
        """Return one candle without accepting negative indexing."""

        _require_existing_index(index, len(self.candles))
        return self.candles[index]

    def prefix(self, as_of_index: int) -> CandleSeries:
        """Materialize only candles known through ``as_of_index``."""

        _require_existing_index(as_of_index, len(self.candles))
        return CandleSeries(self.candles[: as_of_index + 1])

    def through(self, as_of_index: int) -> CandleSeriesView:
        """Create a bounded read view that rejects future candle access."""

        _require_existing_index(as_of_index, len(self.candles))
        return CandleSeriesView(self, as_of_index)


@dataclass(frozen=True, slots=True)
class CandleSeriesView:
    """Read-only candle view bounded to an already-observed index."""

    _source: CandleSeries
    as_of_index: int

    def __post_init__(self) -> None:
        _require_existing_index(self.as_of_index, len(self._source))

    def __len__(self) -> int:
        return self.as_of_index + 1

    @property
    def candles(self) -> tuple[Candle, ...]:
        return self._source.candles[: self.as_of_index + 1]

    @property
    def latest(self) -> Candle:
        return self._source.candles[self.as_of_index]

    def at(self, index: int) -> Candle:
        _require_visible_index(index, self.as_of_index, len(self._source))
        return self._source.candles[index]

    def materialize(self) -> CandleSeries:
        return CandleSeries(self.candles)


@dataclass(frozen=True, slots=True)
class IndicatorPoint:
    """One aligned indicator value; ``None`` marks explicit warmup."""

    event_time: datetime
    value: Decimal | None

    def __post_init__(self) -> None:
        event_time = _require_utc(self.event_time)
        if self.value is not None and (
            not isinstance(self.value, Decimal) or not self.value.is_finite()
        ):
            raise InvalidIndicatorInputError(
                "indicator values must be finite Decimal values or None"
            )
        object.__setattr__(self, "event_time", event_time)


@dataclass(frozen=True, slots=True)
class IndicatorSeries:
    """Chronological immutable output aligned to one source series."""

    descriptor: IndicatorDescriptor
    warmup_points: int
    points: tuple[IndicatorPoint, ...]

    def __post_init__(self) -> None:
        if isinstance(self.warmup_points, bool) or self.warmup_points < 0:
            raise InvalidIndicatorInputError("warmup_points must be nonnegative")
        if any(
            left.event_time >= right.event_time for left, right in zip(self.points, self.points[1:])
        ):
            raise InvalidIndicatorInputError("indicator event times must be strictly chronological")
        unavailable_count = min(self.warmup_points, len(self.points))
        if any(point.value is not None for point in self.points[:unavailable_count]):
            raise InvalidIndicatorInputError(
                "indicator values must remain unavailable during warmup"
            )
        if any(point.value is None for point in self.points[unavailable_count:]):
            raise InvalidIndicatorInputError("indicator values must be available after warmup")

    def __len__(self) -> int:
        return len(self.points)

    def at(self, index: int) -> IndicatorPoint:
        _require_existing_index(index, len(self.points))
        return self.points[index]

    def through(self, as_of_index: int) -> IndicatorSeriesView:
        _require_existing_index(as_of_index, len(self.points))
        return IndicatorSeriesView(self, as_of_index)


@dataclass(frozen=True, slots=True)
class IndicatorBundle:
    """Aligned named outputs produced by one composite indicator."""

    descriptor: IndicatorDescriptor
    components: tuple[tuple[str, IndicatorSeries], ...]

    def __post_init__(self) -> None:
        if not self.components:
            raise InvalidIndicatorInputError(
                "indicator bundles must contain at least one component"
            )

        seen: set[str] = set()
        reference_times: tuple[datetime, ...] | None = None
        for raw_name, series in self.components:
            name = raw_name.strip()
            if _SAFE_TOKEN.fullmatch(name) is None or name in seen:
                raise InvalidIndicatorInputError(
                    "indicator component names must be unique safe identifiers"
                )
            event_times = tuple(point.event_time for point in series.points)
            if reference_times is None:
                reference_times = event_times
            elif event_times != reference_times:
                raise InvalidIndicatorInputError(
                    "indicator bundle components must share identical event times"
                )
            seen.add(name)

        object.__setattr__(
            self,
            "components",
            tuple((name.strip(), series) for name, series in self.components),
        )

    def __len__(self) -> int:
        return len(self.components[0][1])

    @property
    def warmup_points(self) -> int:
        """Return the point where every component is simultaneously available."""

        return max(series.warmup_points for _, series in self.components)

    def component(self, name: str) -> IndicatorSeries:
        """Return one named component without exposing positional assumptions."""

        for component_name, series in self.components:
            if component_name == name:
                return series
        raise InvalidIndicatorInputError(f"unknown indicator component: {name}")


@dataclass(frozen=True, slots=True)
class IndicatorSeriesView:
    """Read-only indicator view bounded to already-observed output."""

    _source: IndicatorSeries
    as_of_index: int

    def __post_init__(self) -> None:
        _require_existing_index(self.as_of_index, len(self._source))

    def __len__(self) -> int:
        return self.as_of_index + 1

    @property
    def descriptor(self) -> IndicatorDescriptor:
        return self._source.descriptor

    @property
    def warmup_points(self) -> int:
        return self._source.warmup_points

    @property
    def points(self) -> tuple[IndicatorPoint, ...]:
        return self._source.points[: self.as_of_index + 1]

    @property
    def latest(self) -> IndicatorPoint:
        return self._source.points[self.as_of_index]

    def at(self, index: int) -> IndicatorPoint:
        _require_visible_index(index, self.as_of_index, len(self._source))
        return self._source.points[index]


def _candle_identity(candle: Candle) -> tuple[object, ...]:
    return (
        candle.exchange,
        candle.market_type,
        candle.symbol,
        candle.timeframe.code,
        candle.timeframe.duration,
        candle.timeframe.alignment,
    )


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidIndicatorInputError("event_time must include UTC timezone")
    if value.utcoffset() != timedelta(0):
        raise InvalidIndicatorInputError("event_time must be UTC")
    return value.astimezone(UTC)


def _candle_value(candle: Candle, field: str) -> Decimal:
    if field == "open":
        return candle.open
    if field == "high":
        return candle.high
    if field == "low":
        return candle.low
    if field == "close":
        return candle.close
    return candle.volume


def _require_existing_index(index: int, length: int) -> None:
    if isinstance(index, bool) or index < 0 or index >= length:
        raise InvalidIndicatorInputError("series index is outside the available range")


def _require_visible_index(index: int, as_of_index: int, source_length: int) -> None:
    if isinstance(index, bool) or index < 0 or index >= source_length:
        raise InvalidIndicatorInputError("series index is outside the available range")
    if index > as_of_index:
        raise FutureDataAccessError("future series data is not visible")
