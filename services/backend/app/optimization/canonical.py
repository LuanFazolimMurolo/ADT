"""Lossless context-independent canonicalization for parameter search."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from app.optimization.errors import IncompatibleSearchSpaceDocumentError

MAX_CANONICAL_DECIMAL_CHARACTERS = 128
MAX_CANONICAL_INTEGER_DIGITS = 128
_MAX_CANONICAL_INTEGER_MAGNITUDE = (10**MAX_CANONICAL_INTEGER_DIGITS) - 1


@dataclass(frozen=True, slots=True)
class _DecimalLayout:
    sign: int
    digits: tuple[int, ...]
    digit_count: int
    exponent: int
    point_position: int
    output_length: int


def decimal_text(value: Decimal) -> str:
    """Serialize a finite Decimal without consulting the active Decimal context."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise IncompatibleSearchSpaceDocumentError("Decimal values must be finite")
    if value == 0:
        return "0"
    layout = _decimal_layout(value)
    if layout.output_length > MAX_CANONICAL_DECIMAL_CHARACTERS:
        raise IncompatibleSearchSpaceDocumentError(
            f"Decimal canonical text exceeds {MAX_CANONICAL_DECIMAL_CHARACTERS} characters"
        )

    coefficient = "".join(str(digit) for digit in layout.digits[: layout.digit_count])
    if layout.exponent >= 0:
        unsigned = coefficient + ("0" * layout.exponent)
    elif layout.point_position > 0:
        unsigned = coefficient[: layout.point_position] + "." + coefficient[layout.point_position :]
    else:
        unsigned = "0." + ("0" * (-layout.point_position)) + coefficient
    return ("-" if layout.sign else "") + unsigned


def canonical_decimal_length(value: Decimal) -> int:
    """Calculate exact canonical output size without constructing zero padding."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise IncompatibleSearchSpaceDocumentError("Decimal values must be finite")
    if value == 0:
        return 1
    return _decimal_layout(value).output_length


def integer_text(value: int) -> str:
    """Serialize a bounded integer before Python's configurable int-to-str limit."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise IncompatibleSearchSpaceDocumentError("integer values must be exact integers")
    if value > _MAX_CANONICAL_INTEGER_MAGNITUDE or value < -_MAX_CANONICAL_INTEGER_MAGNITUDE:
        raise IncompatibleSearchSpaceDocumentError(
            f"integer exceeds {MAX_CANONICAL_INTEGER_DIGITS} canonical digits"
        )
    return str(value)


def _decimal_layout(value: Decimal) -> _DecimalLayout:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise IncompatibleSearchSpaceDocumentError("Decimal values must be finite")
    sign, digits, raw_exponent = value.as_tuple()
    if not isinstance(raw_exponent, int):  # pragma: no cover - finite Decimal guarantee
        raise IncompatibleSearchSpaceDocumentError("Decimal exponent is invalid")

    digit_count = len(digits)
    exponent = raw_exponent
    while digit_count > 1 and digits[digit_count - 1] == 0:
        digit_count -= 1
        exponent += 1

    point_position = digit_count + exponent
    sign_length = 1 if sign else 0
    if exponent >= 0:
        output_length = sign_length + digit_count + exponent
    elif point_position > 0:
        output_length = sign_length + digit_count + 1
    else:
        output_length = sign_length + 2 + (-point_position) + digit_count
    return _DecimalLayout(
        sign=sign,
        digits=digits,
        digit_count=digit_count,
        exponent=exponent,
        point_position=point_position,
        output_length=output_length,
    )


def canonical_json_bytes(value: object) -> bytes:
    """Encode an already JSON-compatible value with stable JSON settings."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise IncompatibleSearchSpaceDocumentError(
            "search-space document is not canonical JSON"
        ) from None


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def document_checksum(payload: object) -> str:
    return sha256_hex(canonical_json_bytes(payload))


def deterministic_id(namespace: str, payload: object) -> str:
    encoded = namespace.encode("ascii") + b"\x00" + canonical_json_bytes(payload)
    return sha256_hex(encoded)
