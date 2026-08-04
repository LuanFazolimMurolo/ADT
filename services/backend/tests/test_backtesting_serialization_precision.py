"""Lossless canonical Decimal serialization regressions."""

from decimal import Decimal, localcontext

from app.backtesting.serialization import canonical_value, decimal_text


def test_decimal_text_preserves_digits_beyond_ambient_precision() -> None:
    value = Decimal("0.27983539094650205761316872427983539094650205761317")

    with localcontext() as context:
        context.prec = 7

        assert decimal_text(value) == ("0.27983539094650205761316872427983539094650205761317")
        assert canonical_value(value) == ("0.27983539094650205761316872427983539094650205761317")


def test_decimal_text_is_canonical_without_losing_integer_zeroes() -> None:
    assert decimal_text(Decimal("1200.00")) == "1200"
    assert decimal_text(Decimal("1.2300E+3")) == "1230"
    assert decimal_text(Decimal("-0.000")) == "0"
