"""Small input-shape validations for domain services."""

from decimal import Decimal

from app.domain.errors import InvalidDomainInputError, InvalidFinancialAmountError

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100


def validate_pagination(limit: int, offset: int) -> None:
    """Validate repository pagination bounds."""
    if limit < 1 or limit > MAX_PAGE_LIMIT or offset < 0:
        raise InvalidDomainInputError(
            message="Os parâmetros de paginação são inválidos.",
            details={"maximum_limit": MAX_PAGE_LIMIT},
        )


def require_nonblank(value: str, *, field_name: str) -> str:
    """Return a trimmed non-blank domain string."""
    normalized = value.strip()
    if not normalized:
        raise InvalidDomainInputError(
            message=f"O campo {field_name} não pode estar vazio.",
            details={"field": field_name},
        )
    return normalized


def require_finite_nonzero(amount: Decimal) -> None:
    """Reject non-finite and zero financial values at the input boundary."""
    if not amount.is_finite() or amount == 0:
        raise InvalidFinancialAmountError()
