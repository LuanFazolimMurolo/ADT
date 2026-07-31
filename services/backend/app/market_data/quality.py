"""Deterministic market-data quality checks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from app.market_data.domain import (
    Candle,
    DataQualityIssue,
    DataQualityReport,
    DataRange,
    QualitySeverity,
    Timeframe,
)


class MarketDataQualityValidator:
    """Validate a bounded canonical candle sequence without filling gaps."""

    def __init__(
        self,
        *,
        max_decimal_places: int = 18,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_decimal_places < 0:
            raise ValueError("max_decimal_places must not be negative")
        self._max_decimal_places = max_decimal_places
        self._clock = clock or (lambda: datetime.now(UTC))

    def validate(
        self,
        candles: tuple[Candle, ...],
        *,
        timeframe: Timeframe,
        expected_range: DataRange | None = None,
        now: datetime | None = None,
    ) -> DataQualityReport:
        """Return blocking errors, warnings and informational findings."""
        current_time = now or self._clock()
        issues: list[DataQualityIssue] = []
        seen: set[datetime] = set()
        previous: Candle | None = None

        for candle in candles:
            if candle.open_time in seen:
                issues.append(
                    DataQualityIssue(
                        "duplicate",
                        QualitySeverity.ERROR,
                        "Candle duplicado pela chave canônica.",
                        candle.open_time,
                    )
                )
            seen.add(candle.open_time)

            if not timeframe.validate_open_time(candle.open_time):
                issues.append(
                    DataQualityIssue(
                        "misaligned_timestamp",
                        QualitySeverity.ERROR,
                        "open_time desalinhado ao timeframe.",
                        candle.open_time,
                    )
                )
            if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(
                candle.open, candle.close, candle.high
            ):
                issues.append(
                    DataQualityIssue(
                        "invalid_ohlc",
                        QualitySeverity.ERROR,
                        "Relação OHLC inválida.",
                        candle.open_time,
                    )
                )
            if candle.volume < 0 or (candle.quote_volume is not None and candle.quote_volume < 0):
                issues.append(
                    DataQualityIssue(
                        "negative_volume",
                        QualitySeverity.ERROR,
                        "Volume negativo.",
                        candle.open_time,
                    )
                )
            if candle.is_closed and candle.close_time > current_time:
                issues.append(
                    DataQualityIssue(
                        "future_candle",
                        QualitySeverity.ERROR,
                        "Candle fechado termina no futuro.",
                        candle.open_time,
                    )
                )
            if not candle.is_closed:
                issues.append(
                    DataQualityIssue(
                        "open_candle",
                        QualitySeverity.WARNING,
                        "Candle ainda aberto.",
                        candle.open_time,
                    )
                )
            if any(
                _decimal_places(value) > self._max_decimal_places
                for value in (
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.quote_volume,
                )
                if value is not None
            ):
                issues.append(
                    DataQualityIssue(
                        "unexpected_precision",
                        QualitySeverity.ERROR,
                        "Precisão decimal excede o schema configurado.",
                        candle.open_time,
                    )
                )

            if previous is not None:
                expected_open = timeframe.next_open_time(previous.open_time)
                if candle.open_time < previous.open_time:
                    issues.append(
                        DataQualityIssue(
                            "out_of_order",
                            QualitySeverity.ERROR,
                            "Candles fora de ordem.",
                            candle.open_time,
                        )
                    )
                if candle.open_time > expected_open:
                    missing_count = int((candle.open_time - expected_open) / timeframe.duration)
                    issues.append(
                        DataQualityIssue(
                            "gap",
                            QualitySeverity.ERROR,
                            f"Gap detectado com {missing_count} intervalo(s) ausente(s).",
                            expected_open,
                        )
                    )
                    issues.append(
                        DataQualityIssue(
                            "missing_interval",
                            QualitySeverity.INFO,
                            "Intervalos ausentes não foram preenchidos artificialmente.",
                            expected_open,
                        )
                    )
                if previous.close_time >= candle.open_time:
                    issues.append(
                        DataQualityIssue(
                            "overlapping_interval",
                            QualitySeverity.ERROR,
                            "Intervalos de candles sobrepostos.",
                            candle.open_time,
                        )
                    )
            previous = candle

        expected_count: int | None = None
        if expected_range is not None:
            expected_count = int((expected_range.end - expected_range.start) / timeframe.duration)
            if candles:
                observed = {candle.open_time for candle in candles}
                cursor = expected_range.start
                missing_boundaries = 0
                while cursor < expected_range.end:
                    if cursor not in observed:
                        missing_boundaries += 1
                    cursor += timeframe.duration
                if missing_boundaries:
                    issues.append(
                        DataQualityIssue(
                            "incomplete_range",
                            QualitySeverity.WARNING,
                            (
                                "O intervalo solicitado possui "
                                f"{missing_boundaries} candle(s) ausente(s)."
                            ),
                        )
                    )
            elif expected_count:
                issues.append(
                    DataQualityIssue(
                        "incomplete_range",
                        QualitySeverity.WARNING,
                        "O intervalo solicitado não retornou candles.",
                    )
                )

        return DataQualityReport(
            issues=tuple(issues),
            checked_count=len(candles),
            expected_count=expected_count,
        )


def _decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0
