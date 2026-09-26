"""Deterministic performance and degradation evidence for Phase 8-10."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.backtesting.reports import ComparisonMetric
from app.indicators.domain import IndicatorDescriptor
from app.market_data.domain import DataRange
from app.ml.domain import (
    MLContract,
    MLImplementationReference,
    MLTargetSpecification,
    MLValueKind,
    _algorithm,
    _hash,
    _nested,
    _require,
    _same,
    _scalar,
    _temporal,
    _utc,
)
from app.ml.predictions import MLRegressionPrediction
from app.strategies.definitions import (
    StoredStrategyParameter,
    strategy_parameter_checksum,
)


@dataclass(frozen=True, slots=True)
class MLPerformanceSubject(MLContract):
    """Canonical strategy configuration whose future performance is predicted."""

    plugin_name: str
    plugin_version: str
    plugin_schema_version: int
    lifecycle_version: int
    parameters: tuple[StoredStrategyParameter, ...]
    parameters_checksum: str

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _require(
            isinstance(self.plugin_name, str)
            and bool(self.plugin_name)
            and self.plugin_name == self.plugin_name.strip()
        )
        _require(
            isinstance(self.plugin_version, str)
            and bool(self.plugin_version)
            and self.plugin_version == self.plugin_version.strip()
        )
        _require(
            isinstance(self.plugin_schema_version, int)
            and not isinstance(self.plugin_schema_version, bool)
            and self.plugin_schema_version >= 1
        )
        _require(
            isinstance(self.lifecycle_version, int)
            and not isinstance(self.lifecycle_version, bool)
            and self.lifecycle_version >= 1
        )
        _require(isinstance(self.parameters, tuple))

        names: list[str] = []
        for parameter in self.parameters:
            _require(type(parameter) is StoredStrategyParameter)
            candidate = StoredStrategyParameter(
                parameter.name,
                parameter.kind,
                parameter.value,
            )
            _require(candidate == parameter)
            names.append(parameter.name)

        _require(names == sorted(names))
        _require(len(names) == len(set(names)))
        _hash(self.parameters_checksum)
        _same(strategy_parameter_checksum(self.parameters) == self.parameters_checksum)


class MLDegradationDirection(StrEnum):
    """Explicit direction in which movement beyond baseline is degradation."""

    BELOW_BASELINE = "BELOW_BASELINE"
    ABOVE_BASELINE = "ABOVE_BASELINE"


_PERFORMANCE_UNITS: dict[ComparisonMetric, str] = {
    ComparisonMetric.TOTAL_RETURN: "ratio",
    ComparisonMetric.CAGR: "ratio",
    ComparisonMetric.SHARPE_RATIO: "ratio",
    ComparisonMetric.SORTINO_RATIO: "ratio",
    ComparisonMetric.MAXIMUM_DRAWDOWN_PCT: "ratio",
    ComparisonMetric.NET_PROFIT: "quote",
    ComparisonMetric.PROFIT_FACTOR: "ratio",
}


def performance_target_descriptor(
    subject: MLPerformanceSubject,
    metric: ComparisonMetric,
) -> IndicatorDescriptor:
    """Return the canonical v1 recipe for one strategy-performance target."""

    _nested(subject, MLPerformanceSubject)
    _metric(metric)

    return IndicatorDescriptor(
        "strategy_performance_metric",
        "1",
        (
            ("lifecycle_version", subject.lifecycle_version),
            ("metric", metric.value),
            ("parameters_checksum", subject.parameters_checksum),
            ("plugin_name", subject.plugin_name),
            ("plugin_schema_version", subject.plugin_schema_version),
            ("plugin_version", subject.plugin_version),
        ),
    )


def build_performance_target(
    subject: MLPerformanceSubject,
    metric: ComparisonMetric,
    *,
    horizon_candles: int,
    implementation: MLImplementationReference,
) -> MLTargetSpecification:
    """Build one canonical decimal target for one strategy configuration."""

    _nested(subject, MLPerformanceSubject)
    _metric(metric)
    _nested(implementation, MLImplementationReference)

    metric_name = metric.value

    return MLTargetSpecification(
        name=f"strategy_{metric_name}_v1",
        algorithm=performance_target_descriptor(subject, metric),
        kind=MLValueKind.DECIMAL,
        unit=_PERFORMANCE_UNITS[metric],
        meaning=f"future strategy {metric_name}",
        horizon_candles=horizon_candles,
        implementation=implementation,
    )


@dataclass(frozen=True, slots=True)
class MLPerformanceMetricSpecification(MLContract):
    """Semantic binding between one strategy configuration, metric, and target."""

    subject: MLPerformanceSubject
    metric: ComparisonMetric
    target: MLTargetSpecification
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.subject, MLPerformanceSubject)
        _metric(self.metric)
        _nested(self.target, MLTargetSpecification)
        _nested(self.implementation, MLImplementationReference)

        _require(self.target.kind is MLValueKind.DECIMAL)
        _algorithm(self.target.algorithm)

        _same(self.target.algorithm == performance_target_descriptor(self.subject, self.metric))
        _same(self.target.name == f"strategy_{self.metric.value}_v1")
        _same(self.target.unit == _PERFORMANCE_UNITS[self.metric])
        _same(self.target.meaning == f"future strategy {self.metric.value}")
        _same(self.target.implementation == self.implementation)


@dataclass(frozen=True, slots=True)
class MLPerformancePredictionEvidence(MLContract):
    """One regression prediction interpreted as a strategy-performance metric."""

    specification: MLPerformanceMetricSpecification
    prediction: MLRegressionPrediction

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.specification, MLPerformanceMetricSpecification)
        _nested(self.prediction, MLRegressionPrediction)

        _same(self.prediction.prediction_specification.target == self.specification.target)
        _same(self.prediction.observation.dataset.target == self.specification.target)
        _same(
            self.prediction.prediction_specification.output_unit == self.specification.target.unit
        )


@dataclass(frozen=True, slots=True)
class MLPerformanceBaseline(MLContract):
    """Verified historical scalar for one exact strategy configuration."""

    subject: MLPerformanceSubject
    metric: ComparisonMetric
    value: Decimal
    data_range: DataRange
    information_time: datetime
    source_checksum: str

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.subject, MLPerformanceSubject)
        _metric(self.metric)
        _scalar(self.value, MLValueKind.DECIMAL)
        _require(self.value.is_finite())

        # DataRange is an external immutable domain value. Reconstruct it to
        # reject hostile mutation before it becomes part of ML evidence.
        candidate = DataRange(self.data_range.start, self.data_range.end)
        _require(candidate == self.data_range)

        _utc(self.information_time)
        _temporal(self.data_range.end <= self.information_time)
        _hash(self.source_checksum)


@dataclass(frozen=True, slots=True)
class MLDegradationSpecification(MLContract):
    """Explicit deterministic rule for interpreting performance deterioration."""

    performance: MLPerformanceMetricSpecification
    direction: MLDegradationDirection
    absolute_threshold: Decimal

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.performance, MLPerformanceMetricSpecification)
        _require(type(self.direction) is MLDegradationDirection)
        _scalar(self.absolute_threshold, MLValueKind.DECIMAL)
        _require(self.absolute_threshold.is_finite())
        _require(self.absolute_threshold >= Decimal("0"))


@dataclass(frozen=True, slots=True)
class MLDegradationEvidence(MLContract):
    """Deterministic comparison of one prediction against one historical baseline."""

    specification: MLDegradationSpecification
    baseline: MLPerformanceBaseline
    prediction: MLPerformancePredictionEvidence
    delta: Decimal = field(init=False)
    degraded: bool = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.specification, MLDegradationSpecification)
        _nested(self.baseline, MLPerformanceBaseline)
        _nested(self.prediction, MLPerformancePredictionEvidence)

        _same(self.prediction.specification == self.specification.performance)
        _same(self.baseline.subject == self.specification.performance.subject)
        _same(self.baseline.metric == self.specification.performance.metric)

        prediction_time = self.prediction.prediction.prediction_time
        _temporal(self.baseline.information_time <= prediction_time)

        delta = self.prediction.prediction.output - self.baseline.value
        _require(delta.is_finite())

        threshold = self.specification.absolute_threshold
        if self.specification.direction is MLDegradationDirection.BELOW_BASELINE:
            degraded = delta < -threshold
        else:
            degraded = delta > threshold

        object.__setattr__(self, "delta", delta)
        object.__setattr__(self, "degraded", degraded)


def _metric(metric: ComparisonMetric) -> None:
    _require(type(metric) is ComparisonMetric)
    _require(metric in _PERFORMANCE_UNITS)
