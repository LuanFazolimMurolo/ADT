"""Deterministic Decimal-only candle resampling."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from app.market_data.calendars import ContinuousUtcCalendar, MarketCalendar
from app.market_data.datasets import GapPolicy, ResamplingPlan, ResamplingResult
from app.market_data.domain import Candle, DataRange, Timeframe
from app.market_data.errors import MarketDataInconsistencyError
from app.market_data.storage import canonical_candle_bytes, validate_candle_serialization

SUPPORTED_RESAMPLING = frozenset(
    {
        ("1m", "5m"),
        ("1m", "15m"),
        ("1m", "30m"),
        ("1m", "1h"),
        ("5m", "15m"),
        ("5m", "30m"),
        ("5m", "1h"),
        ("15m", "30m"),
        ("15m", "1h"),
        ("30m", "1h"),
        ("1h", "4h"),
        ("1h", "1d"),
        ("4h", "1d"),
    }
)


class DeterministicCandleResampler:
    def __init__(self, calendar: MarketCalendar | None = None) -> None:
        self._calendar = calendar or ContinuousUtcCalendar()

    def validate_timeframes(self, source: Timeframe, target: Timeframe) -> int:
        if (source.code, target.code) not in SUPPORTED_RESAMPLING:
            raise MarketDataInconsistencyError("A combinação de timeframes não é suportada.")
        return self._calendar.validate_pair(source, target)

    def resample(
        self,
        candles: Iterable[Candle],
        plan: ResamplingPlan,
        *,
        source_timeframe: Timeframe,
        target_timeframe: Timeframe,
    ) -> ResamplingResult:
        group_size = self.validate_timeframes(source_timeframe, target_timeframe)
        if group_size != plan.group_size:
            raise MarketDataInconsistencyError("O tamanho do grupo diverge do plano.")
        groups: dict[datetime, list[Candle]] = {}
        previous: Candle | None = None
        source_count = 0
        for candle in candles:
            source_count += 1
            validate_candle_serialization(candle, require_closed=False)
            identity = (
                candle.exchange,
                candle.market_type,
                candle.symbol,
                candle.timeframe.code,
            )
            expected_identity = (
                plan.source.exchange,
                plan.source.market_type,
                plan.source.symbol,
                source_timeframe.code,
            )
            if identity != expected_identity:
                raise MarketDataInconsistencyError(
                    "O candle possui identidade de origem divergente."
                )
            if not plan.data_range.start <= candle.open_time < plan.data_range.end:
                raise MarketDataInconsistencyError("O candle está fora do intervalo planejado.")
            if previous is not None and candle.open_time <= previous.open_time:
                raise MarketDataInconsistencyError(
                    "Os candles de origem não estão em ordem estrita."
                )
            start = self._calendar.group_start(candle.open_time, target_timeframe)
            groups.setdefault(start, []).append(candle)
            previous = candle

        output: list[Candle] = []
        skipped: list[DataRange] = []
        start = plan.data_range.start
        while start < plan.data_range.end:
            components = groups.get(start, [])
            complete = self._complete_group(
                components,
                start,
                source_timeframe,
                target_timeframe,
                group_size,
            )
            if not complete:
                group_range = DataRange(start, start + target_timeframe.duration)
                if plan.gap_policy is GapPolicy.STRICT:
                    raise MarketDataInconsistencyError(
                        "Um grupo de resampling está incompleto ou inválido."
                    )
                skipped.append(group_range)
            else:
                output.append(
                    self._aggregate(components, start, target_timeframe, plan.target.source)
                )
            start += target_timeframe.duration

        digest = hashlib.sha256()
        for candle in output:
            digest.update(canonical_candle_bytes(candle))
        return ResamplingResult(
            plan=plan,
            candles=tuple(output),
            skipped_ranges=tuple(skipped),
            source_count=source_count,
            materialized_count=len(output),
            checksum=digest.hexdigest(),
        )

    @staticmethod
    def _complete_group(
        candles: list[Candle],
        start: datetime,
        source: Timeframe,
        target: Timeframe,
        group_size: int,
    ) -> bool:
        if len(candles) != group_size or any(not candle.is_closed for candle in candles):
            return False
        expected = start
        for candle in candles:
            if (
                candle.open_time != expected
                or candle.close_time != expected + source.duration - timedelta(milliseconds=1)
            ):
                return False
            expected += source.duration
        return bool(expected == start + target.duration)

    @staticmethod
    def _aggregate(
        candles: list[Candle],
        start: datetime,
        timeframe: Timeframe,
        source: str,
    ) -> Candle:
        first = candles[0]
        quote_volume = (
            sum(
                (item.quote_volume for item in candles if item.quote_volume is not None),
                start=Decimal(0),
            )
            if all(item.quote_volume is not None for item in candles)
            else None
        )
        trade_count = (
            sum(item.trade_count for item in candles if item.trade_count is not None)
            if all(item.trade_count is not None for item in candles)
            else None
        )
        return replace(
            first,
            timeframe=timeframe,
            open_time=start,
            close_time=start + timeframe.duration - timedelta(milliseconds=1),
            open=first.open,
            high=max(item.high for item in candles),
            low=min(item.low for item in candles),
            close=candles[-1].close,
            volume=sum((item.volume for item in candles), start=Decimal(0)),
            quote_volume=quote_volume,
            trade_count=trade_count,
            is_closed=all(item.is_closed for item in candles),
            source=source,
        )
