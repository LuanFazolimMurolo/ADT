"""Shared Pydantic types used at the HTTP boundary."""

import re
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    StringConstraints,
)
from typing_extensions import TypeAliasType

_MAX_INTEGER_MAGNITUDE = Decimal("1000000000000")
_MAX_DECIMAL_PLACES = 8
_DECIMAL_INPUT_PATTERN = re.compile(r"-?\d+(?:\.\d{1,8})?\Z")


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
    """Validate finite fixed-scale values, including wider SQL aggregates."""

    if not value.is_finite():
        raise ValueError("Financial values must be finite.")

    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("Financial values must be finite.")
    if exponent < -_MAX_DECIMAL_PLACES:
        raise ValueError("Financial values may have at most 8 decimal places.")
    return value


def _validate_stored_financial_magnitude(value: Decimal) -> Decimal:
    """Match one PostgreSQL ``numeric(20, 8)`` input without limiting SUM output."""

    if abs(value) >= _MAX_INTEGER_MAGNITUDE:
        raise ValueError("Financial value exceeds the supported numeric range.")
    return value


def _validate_decimal_string_input(value: object) -> object:
    """Accept only ordinary base-10 JSON strings at financial input boundaries."""

    if not isinstance(value, str) or _DECIMAL_INPUT_PATTERN.fullmatch(value) is None:
        raise ValueError("Financial values must be base-10 decimal strings.")
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
FinancialDecimalStringInput = Annotated[
    FinancialDecimal,
    BeforeValidator(_validate_decimal_string_input, json_schema_input_type=str),
    AfterValidator(_validate_stored_financial_magnitude),
]
PositiveFinancialDecimalStringInput = Annotated[
    FinancialDecimalStringInput,
    AfterValidator(_validate_positive),
]
NonZeroFinancialDecimalStringInput = Annotated[
    FinancialDecimalStringInput,
    AfterValidator(_validate_nonzero),
]

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
if TYPE_CHECKING:
    JsonValue: TypeAlias = (
        dict[str, "JsonValue"] | list["JsonValue"] | str | int | float | bool | None
    )
else:
    JsonValue = TypeAliasType(
        "JsonValue",
        dict[str, "JsonValue"] | list["JsonValue"] | str | int | float | bool | None,
    )
JsonObject: TypeAlias = dict[str, JsonValue]
