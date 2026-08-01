"""Versioned strategy-plugin domain tests."""

from decimal import Decimal

import pytest

from app.indicators.domain import IndicatorDescriptor
from app.strategies.domain import (
    IndicatorCapability,
    StrategyIndicatorRequirement,
    StrategyParameterKind,
    StrategyParameterSpec,
    StrategyPluginDescriptor,
)
from app.strategies.errors import (
    InvalidStrategyPluginError,
    StrategyIndicatorCompatibilityError,
    StrategyParameterValidationError,
    UnsupportedStrategyLifecycleError,
    UnsupportedStrategyPluginSchemaError,
)


def _descriptor() -> StrategyPluginDescriptor:
    return StrategyPluginDescriptor(
        name="example",
        version="1",
        description="Example plugin.",
        parameters=(
            StrategyParameterSpec(
                "period",
                StrategyParameterKind.INTEGER,
                required=False,
                default=5,
                minimum=1,
                maximum=100,
            ),
            StrategyParameterSpec("quantity", StrategyParameterKind.DECIMAL),
        ),
        indicators=(
            StrategyIndicatorRequirement(
                "ema",
                IndicatorCapability("ema", "1", 1),
            ),
        ),
    )


def test_descriptor_canonicalizes_parameter_and_indicator_order() -> None:
    descriptor = StrategyPluginDescriptor(
        name=" example ",
        version=" 1 ",
        description=" Example plugin. ",
        parameters=(
            StrategyParameterSpec("zeta", StrategyParameterKind.STRING),
            StrategyParameterSpec("alpha", StrategyParameterKind.BOOLEAN),
        ),
        indicators=(
            StrategyIndicatorRequirement("slow", IndicatorCapability("ema", "1")),
            StrategyIndicatorRequirement("fast", IndicatorCapability("ema", "1")),
        ),
    )

    assert descriptor.name == "example"
    assert descriptor.version == "1"
    assert descriptor.description == "Example plugin."
    assert tuple(item.name for item in descriptor.parameters) == ("alpha", "zeta")
    assert tuple(item.alias for item in descriptor.indicators) == ("fast", "slow")
    assert descriptor.canonical_key == (1, 1, "example", "1")


def test_descriptor_rejects_future_schema_and_lifecycle_versions() -> None:
    with pytest.raises(UnsupportedStrategyPluginSchemaError):
        StrategyPluginDescriptor("example", "1", "Example.", schema_version=2)
    with pytest.raises(UnsupportedStrategyLifecycleError):
        StrategyPluginDescriptor("example", "1", "Example.", lifecycle_version=2)
    with pytest.raises(UnsupportedStrategyPluginSchemaError):
        StrategyPluginDescriptor("example", "1", "Example.", schema_version=True)


def test_descriptor_rejects_duplicate_parameter_names_and_indicator_aliases() -> None:
    duplicate_parameter = StrategyParameterSpec("period", StrategyParameterKind.INTEGER)
    with pytest.raises(InvalidStrategyPluginError, match="parameter names"):
        StrategyPluginDescriptor(
            "example",
            "1",
            "Example.",
            parameters=(duplicate_parameter, duplicate_parameter),
        )
    requirement = StrategyIndicatorRequirement("ema", IndicatorCapability("ema", "1"))
    with pytest.raises(InvalidStrategyPluginError, match="aliases"):
        StrategyPluginDescriptor(
            "example",
            "1",
            "Example.",
            indicators=(requirement, requirement),
        )


def test_parameter_schema_rejects_invalid_defaults_and_bounds() -> None:
    with pytest.raises(InvalidStrategyPluginError, match="required"):
        StrategyParameterSpec("period", StrategyParameterKind.INTEGER, default=5)
    with pytest.raises(InvalidStrategyPluginError, match="optional"):
        StrategyParameterSpec(
            "period",
            StrategyParameterKind.INTEGER,
            required=False,
        )
    with pytest.raises(InvalidStrategyPluginError, match="incompatible"):
        StrategyParameterSpec(
            "period",
            StrategyParameterKind.INTEGER,
            required=False,
            default=5,
            minimum=Decimal("1"),
        )
    with pytest.raises(InvalidStrategyPluginError, match="exceeds"):
        StrategyParameterSpec(
            "period",
            StrategyParameterKind.INTEGER,
            required=False,
            default=5,
            minimum=10,
            maximum=1,
        )
    with pytest.raises(InvalidStrategyPluginError, match="only integer"):
        StrategyParameterSpec(
            "name",
            StrategyParameterKind.STRING,
            minimum=1,
        )


def test_parameters_are_normalized_without_coercion() -> None:
    descriptor = _descriptor()

    normalized = descriptor.normalize_parameters({"quantity": Decimal("0.5")})

    assert normalized == (("period", 5), ("quantity", Decimal("0.5")))
    assert descriptor.runtime_descriptor({"quantity": Decimal("0.5")}).parameters == normalized


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "missing"),
        ({"quantity": Decimal("1"), "unknown": 1}, "unknown"),
        ({"quantity": 1.0}, "float"),
        ({"quantity": 1}, "finite Decimal"),
        ({"quantity": Decimal("1"), "period": True}, "integer"),
        ({"quantity": Decimal("1"), "period": 0}, "below minimum"),
        ({"quantity": Decimal("1"), "period": 101}, "exceeds maximum"),
    ],
)
def test_invalid_parameters_are_rejected(raw: dict[str, object], message: str) -> None:
    with pytest.raises(StrategyParameterValidationError, match=message):
        _descriptor().normalize_parameters(raw)


def test_string_parameters_are_trimmed_and_empty_values_rejected() -> None:
    descriptor = StrategyPluginDescriptor(
        "example",
        "1",
        "Example.",
        parameters=(StrategyParameterSpec("label", StrategyParameterKind.STRING),),
    )
    defaulted = StrategyPluginDescriptor(
        "defaulted",
        "1",
        "Example with a normalized optional default.",
        parameters=(
            StrategyParameterSpec(
                "label",
                StrategyParameterKind.STRING,
                required=False,
                default="  demo  ",
            ),
        ),
    )

    assert descriptor.normalize_parameters({"label": "  demo  "}) == (("label", "demo"),)
    assert defaulted.normalize_parameters({}) == (("label", "demo"),)
    with pytest.raises(StrategyParameterValidationError, match="empty"):
        descriptor.normalize_parameters({"label": "   "})


def test_indicator_capability_ignores_parameters_but_matches_exact_identity() -> None:
    descriptor = IndicatorDescriptor("ema", "1", (("period", 20),))
    capability = IndicatorCapability.from_descriptor(descriptor)

    assert capability == IndicatorCapability("ema", "1", 1)
    assert capability.canonical_key == (1, "ema", "1")


def test_indicator_compatibility_requires_exact_version_and_schema() -> None:
    descriptor = _descriptor()
    descriptor.ensure_indicator_compatibility((IndicatorCapability("ema", "1", 1),))

    with pytest.raises(StrategyIndicatorCompatibilityError, match="ema@1"):
        descriptor.ensure_indicator_compatibility((IndicatorCapability("ema", "2", 1),))
    with pytest.raises(StrategyIndicatorCompatibilityError, match="ema@1"):
        descriptor.ensure_indicator_compatibility((IndicatorCapability("ema", "1", 2),))
