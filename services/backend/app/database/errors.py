"""Safe translation of PostgreSQL failures into domain errors."""

from typing import NoReturn

from psycopg import Error, InterfaceError, OperationalError, errors

from app.domain.errors import (
    ActiveSimulationExistsError,
    InsufficientBalanceError,
    InvalidFinancialAmountError,
    LedgerImmutableError,
    PersistenceError,
    PersistenceUnavailableError,
    SimulationNotFoundError,
)

_INVALID_FINANCIAL_CONSTRAINTS = frozenset(
    {
        "simulation_runs_initial_capital_positive_check",
        "capital_movements_amount_nonzero_check",
        "capital_movements_amount_sign_check",
    }
)

_MISSING_SIMULATION_CONSTRAINTS = frozenset(
    {
        "capital_movements_simulation_id_fkey",
    }
)

_INITIAL_CAPITAL_MESSAGES = (
    "INITIAL_CAPITAL must be",
    "INITIAL_CAPITAL must equal",
)


def raise_domain_error(error: Error) -> NoReturn:
    """Raise a safe domain error for a known PostgreSQL failure."""
    constraint_name = error.diag.constraint_name
    primary_message = error.diag.message_primary or ""

    if isinstance(error, (OperationalError, InterfaceError)):
        raise PersistenceUnavailableError() from error

    if (
        constraint_name == "simulation_runs_single_active_uidx"
        or "simulation_runs_single_active_uidx" in primary_message
    ):
        raise ActiveSimulationExistsError() from error

    if "balance negative" in primary_message:
        raise InsufficientBalanceError() from error

    if (
        constraint_name in _MISSING_SIMULATION_CONSTRAINTS
        or "simulation that does not exist" in primary_message
    ):
        raise SimulationNotFoundError() from error

    if constraint_name in _INVALID_FINANCIAL_CONSTRAINTS:
        raise InvalidFinancialAmountError() from error

    if isinstance(error, errors.NumericValueOutOfRange):
        raise InvalidFinancialAmountError() from error

    if any(message in primary_message for message in _INITIAL_CAPITAL_MESSAGES):
        raise InvalidFinancialAmountError(
            message="O movimento de capital inicial é inválido."
        ) from error

    if (
        "append-only" in primary_message
        or constraint_name == "capital_movements_single_initial_capital_uidx"
    ):
        raise LedgerImmutableError() from error

    if isinstance(error, errors.DatabaseError):
        raise PersistenceError() from error

    raise PersistenceError() from error
