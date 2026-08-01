"""Explicit registry for safe local strategy plugins."""

from __future__ import annotations

from collections.abc import Iterable

from app.backtesting.domain import StrategyDescriptor
from app.backtesting.strategy import BacktestStrategy
from app.strategies.builtins import EmaCrossExamplePlugin, NoOpStrategyPlugin
from app.strategies.domain import IndicatorCapability, RawStrategyParameters
from app.strategies.errors import (
    DuplicateStrategyPluginError,
    InvalidStrategyPluginError,
    StrategyPluginNotFoundError,
)
from app.strategies.protocols import StrategyPlugin


class StrategyPluginRegistry:
    """Resolve only pre-registered factories; arbitrary imports are forbidden."""

    def __init__(self, plugins: Iterable[StrategyPlugin]) -> None:
        registrations: dict[tuple[str, str], StrategyPlugin] = {}
        for plugin in plugins:
            key = (plugin.descriptor.name, plugin.descriptor.version)
            if key in registrations:
                raise DuplicateStrategyPluginError(
                    f"duplicate strategy plugin registration: {key[0]}@{key[1]}"
                )
            registrations[key] = plugin
        self._registrations = registrations

    @classmethod
    def builtins(cls) -> StrategyPluginRegistry:
        """Create the fixed registry shipped by this code version."""

        return cls((NoOpStrategyPlugin(), EmaCrossExamplePlugin()))

    @property
    def identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._registrations))

    def resolve(self, name: str, version: str) -> StrategyPlugin:
        """Resolve an exact identity without importing a Python module."""

        plugin = self._registrations.get((name, version))
        if plugin is None:
            raise StrategyPluginNotFoundError(
                f"strategy plugin is not registered: {name}@{version}"
            )
        return plugin

    def build(
        self,
        name: str,
        version: str,
        parameters: RawStrategyParameters,
        *,
        available_indicators: tuple[IndicatorCapability, ...],
    ) -> BacktestStrategy:
        """Validate compatibility and return fresh deterministic strategy state."""

        plugin = self.resolve(name, version)
        plugin.descriptor.ensure_indicator_compatibility(available_indicators)
        normalized = plugin.descriptor.normalize_parameters(parameters)
        strategy = plugin.build(normalized)
        expected = StrategyDescriptor(
            plugin.descriptor.name,
            plugin.descriptor.version,
            normalized,
        )
        if strategy.descriptor != expected:
            raise InvalidStrategyPluginError(
                "strategy factory returned a descriptor divergent from its plugin schema"
            )
        return strategy
