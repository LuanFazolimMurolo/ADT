"""Immutable versioned contracts for deterministic strategy plugins."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias

from app.backtesting.domain import (
    SUPPORTED_STRATEGY_LIFECYCLE_VERSIONS as _SUPPORTED_STRATEGY_LIFECYCLE_VERSIONS,
)
from app.backtesting.domain import (
    StrategyDescriptor,
    StrategyParameters,
    StrategyParameterValue,
)
from app.indicators.domain import IndicatorDescriptor
from app.strategies.errors import (
    InvalidStrategyPluginError,
    StrategyIndicatorCompatibilityError,
    StrategyParameterValidationError,
    UnsupportedStrategyLifecycleError,
    UnsupportedStrategyPluginSchemaError,
)

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SUPPORTED_STRATEGY_PLUGIN_SCHEMA_VERSIONS = frozenset({1})
SUPPORTED_STRATEGY_LIFECYCLE_VERSIONS = _SUPPORTED_STRATEGY_LIFECYCLE_VERSIONS

RawStrategyParameters: TypeAlias = Mapping[str, object]


class StrategyParameterKind(StrEnum):
    """Supported scalar types for canonical strategy parameters."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    STRING = "string"


@dataclass(frozen=True, slots=True)
class StrategyParameterSpec:
    """One deterministic scalar parameter declaration."""

    name: str
    kind: StrategyParameterKind
    required: bool = True
    default: StrategyParameterValue = None
    minimum: int | Decimal | None = None
    maximum: int | Decimal | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        if _SAFE_TOKEN.fullmatch(name) is None:
            raise InvalidStrategyPluginError("strategy parameter name must be a safe identifier")
        if not isinstance(self.kind, StrategyParameterKind):
            raise InvalidStrategyPluginError("strategy parameter kind is invalid")
        if not isinstance(self.required, bool):
            raise InvalidStrategyPluginError("strategy parameter required flag must be boolean")
        if self.required and self.default is not None:
            raise InvalidStrategyPluginError(
                "required strategy parameters must not define defaults"
            )
        if not self.required and self.default is None:
            raise InvalidStrategyPluginError("optional strategy parameters must define defaults")
        object.__setattr__(self, "name", name)
        self._validate_bounds()
        if self.default is not None:
            object.__setattr__(self, "default", self.normalize(self.default))

    def normalize(self, value: object) -> StrategyParameterValue:
        """Validate one supplied value without coercion."""

        if isinstance(value, float):
            raise StrategyParameterValidationError("strategy parameters must not contain float")
        if self.kind is StrategyParameterKind.BOOLEAN:
            if not isinstance(value, bool):
                raise StrategyParameterValidationError(f"parameter {self.name} must be boolean")
            normalized: StrategyParameterValue = value
        elif self.kind is StrategyParameterKind.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise StrategyParameterValidationError(f"parameter {self.name} must be integer")
            normalized = value
        elif self.kind is StrategyParameterKind.DECIMAL:
            if not isinstance(value, Decimal) or not value.is_finite():
                raise StrategyParameterValidationError(
                    f"parameter {self.name} must be a finite Decimal"
                )
            normalized = value
        else:
            if not isinstance(value, str):
                raise StrategyParameterValidationError(f"parameter {self.name} must be string")
            normalized = value.strip()
            if not normalized:
                raise StrategyParameterValidationError(f"parameter {self.name} must not be empty")

        self._validate_range(normalized)
        return normalized

    def _validate_bounds(self) -> None:
        if self.kind not in {StrategyParameterKind.INTEGER, StrategyParameterKind.DECIMAL}:
            if self.minimum is not None or self.maximum is not None:
                raise InvalidStrategyPluginError(
                    "only integer and Decimal parameters may declare bounds"
                )
            return
        expected_type = int if self.kind is StrategyParameterKind.INTEGER else Decimal
        for label, value in (("minimum", self.minimum), ("maximum", self.maximum)):
            if value is None:
                continue
            if expected_type is int:
                valid = isinstance(value, int) and not isinstance(value, bool)
            else:
                valid = isinstance(value, Decimal) and value.is_finite()
            if not valid:
                raise InvalidStrategyPluginError(
                    f"strategy parameter {label} has an incompatible type"
                )
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise InvalidStrategyPluginError("strategy parameter minimum exceeds maximum")

    def _validate_range(self, value: StrategyParameterValue) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            return
        if self.minimum is not None and value < self.minimum:
            raise StrategyParameterValidationError(f"parameter {self.name} is below minimum")
        if self.maximum is not None and value > self.maximum:
            raise StrategyParameterValidationError(f"parameter {self.name} exceeds maximum")


@dataclass(frozen=True, slots=True)
class IndicatorCapability:
    """Parameter-independent indicator implementation identity."""

    name: str
    version: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        name = self.name.strip()
        version = self.version.strip()
        if _SAFE_TOKEN.fullmatch(name) is None or _SAFE_TOKEN.fullmatch(version) is None:
            raise InvalidStrategyPluginError("indicator capability must use safe identifiers")
        if isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise InvalidStrategyPluginError("indicator capability schema version is invalid")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)

    @classmethod
    def from_descriptor(cls, descriptor: IndicatorDescriptor) -> IndicatorCapability:
        return cls(descriptor.name, descriptor.version, descriptor.schema_version)

    @property
    def canonical_key(self) -> tuple[int, str, str]:
        return self.schema_version, self.name, self.version


@dataclass(frozen=True, slots=True)
class StrategyIndicatorRequirement:
    """One exact indicator capability required by a strategy plugin."""

    alias: str
    capability: IndicatorCapability

    def __post_init__(self) -> None:
        alias = self.alias.strip()
        if _SAFE_TOKEN.fullmatch(alias) is None:
            raise InvalidStrategyPluginError("indicator requirement alias must be safe")
        object.__setattr__(self, "alias", alias)


@dataclass(frozen=True, slots=True)
class StrategyPluginDescriptor:
    """Stable plugin identity, lifecycle and parameter/indicator schemas."""

    name: str
    version: str
    description: str
    parameters: tuple[StrategyParameterSpec, ...] = ()
    indicators: tuple[StrategyIndicatorRequirement, ...] = ()
    schema_version: int = 1
    lifecycle_version: int = 1

    def __post_init__(self) -> None:
        name = self.name.strip()
        version = self.version.strip()
        description = self.description.strip()
        if _SAFE_TOKEN.fullmatch(name) is None or _SAFE_TOKEN.fullmatch(version) is None:
            raise InvalidStrategyPluginError("strategy plugin identity must use safe identifiers")
        if not description:
            raise InvalidStrategyPluginError("strategy plugin description must not be empty")
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version not in SUPPORTED_STRATEGY_PLUGIN_SCHEMA_VERSIONS
        ):
            raise UnsupportedStrategyPluginSchemaError(
                f"unsupported strategy plugin schema version: {self.schema_version}"
            )
        if (
            isinstance(self.lifecycle_version, bool)
            or self.lifecycle_version not in SUPPORTED_STRATEGY_LIFECYCLE_VERSIONS
        ):
            raise UnsupportedStrategyLifecycleError(
                f"unsupported strategy lifecycle version: {self.lifecycle_version}"
            )
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise InvalidStrategyPluginError("strategy parameter names must be unique")
        indicator_aliases = [item.alias for item in self.indicators]
        if len(indicator_aliases) != len(set(indicator_aliases)):
            raise InvalidStrategyPluginError("strategy indicator aliases must be unique")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "description", description)
        object.__setattr__(
            self,
            "parameters",
            tuple(sorted(self.parameters, key=lambda item: item.name)),
        )
        object.__setattr__(
            self,
            "indicators",
            tuple(sorted(self.indicators, key=lambda item: item.alias)),
        )

    @property
    def canonical_key(self) -> tuple[int, int, str, str]:
        return self.schema_version, self.lifecycle_version, self.name, self.version

    def normalize_parameters(self, raw: RawStrategyParameters) -> StrategyParameters:
        """Validate and canonicalize one parameter mapping."""

        declared = {item.name: item for item in self.parameters}
        unknown = sorted(set(raw) - set(declared))
        if unknown:
            raise StrategyParameterValidationError(
                f"unknown strategy parameters: {', '.join(unknown)}"
            )
        normalized: list[tuple[str, StrategyParameterValue]] = []
        for spec in self.parameters:
            if spec.name in raw:
                value = spec.normalize(raw[spec.name])
            elif spec.required:
                raise StrategyParameterValidationError(
                    f"required strategy parameter is missing: {spec.name}"
                )
            else:
                value = spec.default
            normalized.append((spec.name, value))
        return tuple(normalized)

    def runtime_descriptor(self, raw: RawStrategyParameters) -> StrategyDescriptor:
        """Create the exact descriptor consumed by the backtest engine."""

        return StrategyDescriptor(self.name, self.version, self.normalize_parameters(raw))

    def ensure_indicator_compatibility(
        self,
        available: tuple[IndicatorCapability, ...],
    ) -> None:
        """Require every declared indicator capability by exact identity."""

        keys = {item.canonical_key for item in available}
        missing = [
            requirement
            for requirement in self.indicators
            if requirement.capability.canonical_key not in keys
        ]
        if missing:
            labels = ", ".join(
                f"{item.alias}={item.capability.name}@{item.capability.version}" for item in missing
            )
            raise StrategyIndicatorCompatibilityError(
                f"required indicator capabilities are unavailable: {labels}"
            )
