"""Deterministic market-regime classification evidence for Phase 8-09."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.indicators.domain import IndicatorDescriptor
from app.indicators.errors import InvalidIndicatorInputError
from app.indicators.regime import (
    MarketRegimeKind,
    MarketRegimePoint,
    MarketRegimePolicy,
    TrendDirection,
)
from app.ml.domain import (
    MLContract,
    MLImplementationReference,
    MLObservation,
    _algorithm,
    _nested,
    _require,
    _same,
    _temporal,
)
from app.ml.errors import MLContractError


class MLMarketRegimeError(MLContractError):
    code = "ml_market_regime_error"
    default_message = "A classificação de regime ML falhou."


class MLMarketRegimeInputError(MLMarketRegimeError):
    code = "ml_market_regime_input_error"
    default_message = "A entrada de classificação de regime ML é incompatível."


@dataclass(frozen=True, slots=True)
class MLMarketRegimeSpecification(MLContract):
    """Immutable reference to one canonical deterministic regime policy."""

    policy: IndicatorDescriptor
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _algorithm(self.policy)
        _nested(self.implementation, MLImplementationReference)

        canonical_policy = _canonical_policy(self.policy)
        _same(canonical_policy.descriptor == self.policy)

    @property
    def warmup_points(self) -> int:
        return _canonical_policy(self.policy).warmup_points


@dataclass(frozen=True, slots=True)
class MLMarketRegimeClassification(MLContract):
    """Immutable explainable classification aligned to one ML observation."""

    specification: MLMarketRegimeSpecification
    observation: MLObservation
    regime: MarketRegimeKind
    trend_direction: TrendDirection
    fast_ema: Decimal | None
    slow_ema: Decimal | None
    atr: Decimal | None
    atr_ratio: Decimal | None
    trend_strength: Decimal | None

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.specification, MLMarketRegimeSpecification)
        _nested(self.observation, MLObservation)

        _require(type(self.regime) is MarketRegimeKind)
        _require(type(self.trend_direction) is TrendDirection)

        # Reuse the canonical indicator-domain invariants rather than
        # maintaining a second market-regime validation implementation.
        try:
            point = MarketRegimePoint(
                event_time=self.observation.observation_time,
                regime=self.regime,
                trend_direction=self.trend_direction,
                fast_ema=self.fast_ema,
                slow_ema=self.slow_ema,
                atr=self.atr,
                atr_ratio=self.atr_ratio,
                trend_strength=self.trend_strength,
            )
        except InvalidIndicatorInputError as error:
            raise MLMarketRegimeInputError(
                "market-regime classification values are invalid"
            ) from error

        _same(point.regime == self.regime)
        _same(point.trend_direction == self.trend_direction)


def market_regime_specification(
    policy: MarketRegimePolicy,
    *,
    implementation: MLImplementationReference,
) -> MLMarketRegimeSpecification:
    """Project one canonical market-regime policy into the ML contract layer."""

    _validate_policy(policy)
    _nested(implementation, MLImplementationReference)

    return MLMarketRegimeSpecification(
        policy=policy.descriptor,
        implementation=implementation,
    )


def classify_market_regime_evidence(
    specification: MLMarketRegimeSpecification,
    observation: MLObservation,
    point: MarketRegimePoint,
) -> MLMarketRegimeClassification:
    """Project one canonical regime point into immutable ML evidence."""

    _nested(specification, MLMarketRegimeSpecification)
    _nested(observation, MLObservation)
    _validate_point(point)

    _temporal(point.event_time == observation.observation_time)

    return MLMarketRegimeClassification(
        specification=specification,
        observation=observation,
        regime=point.regime,
        trend_direction=point.trend_direction,
        fast_ema=point.fast_ema,
        slow_ema=point.slow_ema,
        atr=point.atr,
        atr_ratio=point.atr_ratio,
        trend_strength=point.trend_strength,
    )


def _canonical_policy(descriptor: IndicatorDescriptor) -> MarketRegimePolicy:
    if (
        descriptor.name != "market_regime"
        or descriptor.version != "1"
        or descriptor.schema_version != 1
    ):
        raise MLMarketRegimeInputError(
            "market-regime specification requires the canonical v1 policy descriptor"
        )

    parameters = dict(descriptor.parameters)

    expected_names = {
        "atr_period",
        "fast_ema_period",
        "slow_ema_period",
        "trend_strength_threshold",
        "volatile_atr_ratio",
    }

    if set(parameters) != expected_names or len(descriptor.parameters) != len(expected_names):
        raise MLMarketRegimeInputError(
            "market-regime policy descriptor parameters are incompatible"
        )

    try:
        policy = MarketRegimePolicy(
            fast_ema_period=_exact_int(parameters["fast_ema_period"]),
            slow_ema_period=_exact_int(parameters["slow_ema_period"]),
            atr_period=_exact_int(parameters["atr_period"]),
            volatile_atr_ratio=_exact_decimal(parameters["volatile_atr_ratio"]),
            trend_strength_threshold=_exact_decimal(parameters["trend_strength_threshold"]),
            schema_version=1,
        )
    except (InvalidIndicatorInputError, TypeError, ValueError, KeyError) as error:
        raise MLMarketRegimeInputError("market-regime policy descriptor is invalid") from error

    if policy.descriptor != descriptor:
        raise MLMarketRegimeInputError("market-regime policy descriptor is not canonical")

    return policy


def _validate_policy(policy: MarketRegimePolicy) -> None:
    _require(type(policy) is MarketRegimePolicy)

    try:
        candidate = MarketRegimePolicy(
            fast_ema_period=policy.fast_ema_period,
            slow_ema_period=policy.slow_ema_period,
            atr_period=policy.atr_period,
            volatile_atr_ratio=policy.volatile_atr_ratio,
            trend_strength_threshold=policy.trend_strength_threshold,
            schema_version=policy.schema_version,
        )
    except (TypeError, ValueError) as error:
        raise MLMarketRegimeInputError("market-regime policy is invalid") from error

    _same(candidate == policy)


def _validate_point(point: MarketRegimePoint) -> None:
    _require(type(point) is MarketRegimePoint)

    try:
        candidate = MarketRegimePoint(
            event_time=point.event_time,
            regime=point.regime,
            trend_direction=point.trend_direction,
            fast_ema=point.fast_ema,
            slow_ema=point.slow_ema,
            atr=point.atr,
            atr_ratio=point.atr_ratio,
            trend_strength=point.trend_strength,
        )
    except (TypeError, ValueError) as error:
        raise MLMarketRegimeInputError("market-regime point is invalid") from error

    _same(candidate == point)


def _exact_int(value: object) -> int:
    if type(value) is not int:
        raise MLMarketRegimeInputError("market-regime period parameters must be exact integers")
    return value


def _exact_decimal(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise MLMarketRegimeInputError(
            "market-regime threshold parameters must be finite Decimal values"
        )
    return value
