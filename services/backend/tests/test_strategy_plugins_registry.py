"""Explicit strategy-plugin registry tests."""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.backtesting.domain import StrategyDescriptor, StrategyParameters
from app.backtesting.strategy import NoOpStrategy
from app.strategies.builtins import NoOpStrategyPlugin
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.domain import StrategyPluginDescriptor
from app.strategies.errors import (
    DuplicateStrategyPluginError,
    InvalidStrategyPluginError,
    StrategyIndicatorCompatibilityError,
    StrategyParameterValidationError,
    StrategyPluginNotFoundError,
)
from app.strategies.registry import StrategyPluginRegistry


def test_builtin_registry_exposes_only_explicit_identities() -> None:
    registry = StrategyPluginRegistry.builtins()

    assert registry.identities == (("ema-cross-example", "1"), ("no-op", "1"))
    assert registry.resolve("no-op", "1").descriptor.name == "no-op"


def test_registry_rejects_arbitrary_module_and_unknown_versions() -> None:
    registry = StrategyPluginRegistry.builtins()

    for name in ("some.module:Strategy", "../../strategy", "os.system"):
        with pytest.raises(StrategyPluginNotFoundError):
            registry.resolve(name, "1")
    with pytest.raises(StrategyPluginNotFoundError):
        registry.resolve("no-op", "2")


def test_registry_rejects_duplicate_canonical_identity() -> None:
    with pytest.raises(DuplicateStrategyPluginError, match="no-op@1"):
        StrategyPluginRegistry((NoOpStrategyPlugin(), NoOpStrategyPlugin()))


def test_noop_build_returns_fresh_state_with_exact_runtime_descriptor() -> None:
    registry = StrategyPluginRegistry.builtins()

    first = registry.build(
        "no-op",
        "1",
        {},
        available_indicators=(),
    )
    second = registry.build(
        "no-op",
        "1",
        {},
        available_indicators=(),
    )

    assert isinstance(first, NoOpStrategy)
    assert isinstance(second, NoOpStrategy)
    assert first is not second
    assert first.descriptor == StrategyDescriptor("no-op", "1")


def test_ema_example_requires_registered_indicator_capability() -> None:
    registry = StrategyPluginRegistry.builtins()
    parameters = {"quantity": Decimal("0.1")}

    with pytest.raises(StrategyIndicatorCompatibilityError):
        registry.build(
            "ema-cross-example",
            "1",
            parameters,
            available_indicators=(),
        )

    strategy = registry.build(
        "ema-cross-example",
        "1",
        parameters,
        available_indicators=builtin_indicator_capabilities(),
    )
    assert strategy.descriptor == StrategyDescriptor(
        "ema-cross-example",
        "1",
        (("fast_period", 3), ("quantity", Decimal("0.1")), ("slow_period", 5)),
    )


def test_ema_example_rejects_cross_field_and_unknown_parameters() -> None:
    registry = StrategyPluginRegistry.builtins()
    capabilities = builtin_indicator_capabilities()

    with pytest.raises(StrategyParameterValidationError, match="fast < slow"):
        registry.build(
            "ema-cross-example",
            "1",
            {"fast_period": 5, "slow_period": 5, "quantity": Decimal("1")},
            available_indicators=capabilities,
        )
    with pytest.raises(StrategyParameterValidationError, match="positive"):
        registry.build(
            "ema-cross-example",
            "1",
            {"quantity": Decimal("0")},
            available_indicators=capabilities,
        )
    with pytest.raises(StrategyParameterValidationError, match="unknown"):
        registry.build(
            "ema-cross-example",
            "1",
            {"quantity": Decimal("1"), "module": "evil"},
            available_indicators=capabilities,
        )


@dataclass(frozen=True, slots=True)
class DivergentPlugin:
    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        "divergent",
        "1",
        "Factory that violates its descriptor contract.",
    )

    def build(self, parameters: StrategyParameters) -> NoOpStrategy:
        assert parameters == ()
        return NoOpStrategy()


def test_registry_rejects_factory_descriptor_divergence() -> None:
    registry = StrategyPluginRegistry((DivergentPlugin(),))

    with pytest.raises(InvalidStrategyPluginError, match="divergent"):
        registry.build("divergent", "1", {}, available_indicators=())
