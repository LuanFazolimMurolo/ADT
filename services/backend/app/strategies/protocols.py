"""Typed factory boundary for deterministic strategy plugins."""

from typing import Protocol

from app.backtesting.domain import StrategyParameters
from app.backtesting.strategy import BacktestStrategy
from app.strategies.domain import StrategyPluginDescriptor


class StrategyPlugin(Protocol):
    """One explicitly registered factory that returns fresh strategy state."""

    @property
    def descriptor(self) -> StrategyPluginDescriptor: ...

    def build(self, parameters: StrategyParameters) -> BacktestStrategy: ...
