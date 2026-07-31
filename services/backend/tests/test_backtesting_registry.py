"""Safe explicit strategy-registry tests."""

from decimal import Decimal

import pytest

from app.backtesting.errors import UnsupportedStrategyError
from app.backtesting.registry import StrategyRegistry
from app.backtesting.strategy import BuyAndHoldExample, NoOpStrategy


def test_registry_exposes_only_approved_cli_strategies() -> None:
    registry = StrategyRegistry()

    assert registry.names == ("buy-and-hold-example", "no-op")
    assert isinstance(registry.build("no-op"), NoOpStrategy)
    assert isinstance(
        registry.build("buy-and-hold-example", quantity=Decimal("0.5")),
        BuyAndHoldExample,
    )


def test_registry_rejects_arbitrary_module_and_invalid_parameters() -> None:
    registry = StrategyRegistry()

    with pytest.raises(UnsupportedStrategyError):
        registry.build("some.module:Strategy")
    with pytest.raises(UnsupportedStrategyError, match="--quantity"):
        registry.build("buy-and-hold-example")
    with pytest.raises(UnsupportedStrategyError, match="não aceita"):
        registry.build("no-op", quantity=Decimal("1"))
