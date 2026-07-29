"""Shared Pydantic types used at the HTTP boundary."""

from decimal import Decimal
from typing import Annotated, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    JsonValue,
    PlainSerializer,
    StringConstraints,
)

_MAX_INTEGER_MAGNITUDE = Decimal("1000000000000")
_MAX_DECIMAL_PLACES = 8


class ApiSchema(BaseModel):
    """Base for explicit API contracts.

    Unknown input fields are rejected so that adding a database column never
    silently expands the public API.
    """

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        from_attributes=True,
    )


def _validate_financial_decimal(value: Decimal) -> Decimal:
    """Match PostgreSQL ``numeric(20, 8)`` without converting through float."""

    if not value.is_finite():
        raise ValueError("Financial values must be finite.")

    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("Financial values must be finite.")
    if exponent < -_MAX_DECIMAL_PLACES:
        raise ValueError("Financial values may have at most 8 decimal places.")
    if abs(value) >= _MAX_INTEGER_MAGNITUDE:
        raise ValueError("Financial value exceeds the supported numeric range.")
    return value


def _validate_positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("Value must be greater than zero.")
    return value


def _validate_nonzero(value: Decimal) -> Decimal:
    if value == 0:
        raise ValueError("Value must not be zero.")
    return value


def _serialize_decimal(value: Decimal) -> str:
    """Emit a base-10 JSON string, preserving all stored decimal places."""

    return format(value, "f")


FinancialDecimal = Annotated[
    Decimal,
    AfterValidator(_validate_financial_decimal),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]
PositiveFinancialDecimal = Annotated[FinancialDecimal, AfterValidator(_validate_positive)]
NonZeroFinancialDecimal = Annotated[FinancialDecimal, AfterValidator(_validate_nonzero)]

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
JsonObject: TypeAlias = dict[str, JsonValue]
