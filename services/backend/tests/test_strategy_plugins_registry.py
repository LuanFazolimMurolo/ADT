"""Explicit strategy-plugin registry tests."""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.backtesting.domain import Fill, OrderIntent, StrategyDescriptor, StrategyParameters
from app.backtesting.strategy import NoOpStrategy, StrategyContext
from app.market_data.domain import Candle
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

    assert registry.identities == (
        ("ema-cross-example", "1"),
        ("ema-cross-example", "2"),
        ("no-op", "1"),
        ("no-op", "2"),
    )
    assert {
        identity: registry.resolve(*identity).descriptor.lifecycle_version
        for identity in registry.identities
    } == {
        ("ema-cross-example", "1"): 1,
        ("ema-cross-example", "2"): 2,
        ("no-op", "1"): 1,
        ("no-op", "2"): 2,
    }


def test_registry_rejects_arbitrary_module_and_unknown_versions() -> None:
    registry = StrategyPluginRegistry.builtins()

    for name in ("some.module:Strategy", "../../strategy", "os.system"):
        with pytest.raises(StrategyPluginNotFoundError):
            registry.resolve(name, "1")
    with pytest.raises(StrategyPluginNotFoundError):
        registry.resolve("no-op", "3")


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


@pytest.mark.parametrize("name", ["no-op", "ema-cross-example"])
def test_builtin_versions_build_with_exact_runtime_identity(name: str) -> None:
    registry = StrategyPluginRegistry.builtins()
    parameters = {} if name == "no-op" else {"quantity": Decimal("0.1")}
    indicators = () if name == "no-op" else builtin_indicator_capabilities()

    version_one = registry.build(
        name,
        "1",
        parameters,
        available_indicators=indicators,
    )
    version_two = registry.build(
        name,
        "2",
        parameters,
        available_indicators=indicators,
    )

    assert version_one.descriptor.name == version_two.descriptor.name == name
    assert version_one.descriptor.version == "1"
    assert version_two.descriptor.version == "2"
    assert callable(version_two.on_warmup_candle)


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


@dataclass(slots=True)
class LegacyLifecycleStrategy:
    descriptor: StrategyDescriptor

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        del context
        return ()

    def on_candle(self, context: StrategyContext, candle: Candle) -> tuple[OrderIntent, ...]:
        del context, candle
        return ()

    def on_fill(self, context: StrategyContext, fill: Fill) -> tuple[OrderIntent, ...]:
        del context, fill
        return ()

    def on_end(self, context: StrategyContext) -> None:
        del context


@dataclass(frozen=True, slots=True)
class LifecyclePlugin:
    lifecycle_version: int

    @property
    def descriptor(self) -> StrategyPluginDescriptor:
        return StrategyPluginDescriptor(
            "lifecycle-test",
            "1",
            "Lifecycle compatibility fixture.",
            lifecycle_version=self.lifecycle_version,
        )

    def build(self, parameters: StrategyParameters) -> LegacyLifecycleStrategy:
        assert parameters == ()
        return LegacyLifecycleStrategy(StrategyDescriptor("lifecycle-test", "1"))


def test_registry_preserves_legacy_lifecycle_without_warmup_callback() -> None:
    registry = StrategyPluginRegistry((LifecyclePlugin(1),))

    strategy = registry.build("lifecycle-test", "1", {}, available_indicators=())

    assert not hasattr(strategy, "on_warmup_candle")


def test_registry_rejects_lifecycle_two_factory_without_callable_warmup() -> None:
    registry = StrategyPluginRegistry((LifecyclePlugin(2),))

    with pytest.raises(InvalidStrategyPluginError, match="warmup-aware"):
        registry.build("lifecycle-test", "1", {}, available_indicators=())
