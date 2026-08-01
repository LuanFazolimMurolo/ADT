"""Fixed Decimal arithmetic policy shared by deterministic indicators."""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

_INDICATOR_CONTEXT = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
    Emin=-999_999,
    Emax=999_999,
)


@contextmanager
def indicator_decimal_context() -> Iterator[Context]:
    """Use a fixed context independent from ambient process Decimal settings."""

    with localcontext(_INDICATOR_CONTEXT) as context:
        yield context


def contextual(value: Decimal) -> Decimal:
    """Apply the active fixed context to one Decimal result."""

    return +value
