"""Safe, transport-agnostic domain errors."""

from collections.abc import Mapping
from typing import ClassVar


class DomainError(Exception):
    """Base error containing only information safe to return to a client."""

    code: ClassVar[str] = "domain_error"
    default_message: ClassVar[str] = "Não foi possível concluir a operação."
    status_code: ClassVar[int] = 400

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = dict(details) if details is not None else None
        super().__init__(self.message)


class InvalidDomainInputError(DomainError):
    """The requested domain operation has invalid input."""

    code = "invalid_input"
    default_message = "Os dados informados são inválidos."
    status_code = 400


class InvalidFinancialAmountError(InvalidDomainInputError):
    """A financial amount is non-finite, zero, or has the wrong sign."""

    code = "invalid_financial_amount"
    default_message = "O valor financeiro informado é inválido."


class ResourceNotFoundError(DomainError):
    """A requested resource does not exist."""

    code = "not_found"
    default_message = "O recurso solicitado não foi encontrado."
    status_code = 404


class SimulationNotFoundError(ResourceNotFoundError):
    """The requested simulation does not exist."""

    code = "simulation_not_found"
    default_message = "A simulação solicitada não foi encontrada."


class SettingNotFoundError(ResourceNotFoundError):
    """The requested setting does not exist."""

    code = "setting_not_found"
    default_message = "A configuração solicitada não foi encontrada."


class DomainConflictError(DomainError):
    """The operation conflicts with current persisted state."""

    code = "conflict"
    default_message = "A operação conflita com o estado atual do recurso."
    status_code = 409


class ActiveSimulationExistsError(DomainConflictError):
    """Only one active simulation may exist."""

    code = "active_simulation_exists"
    default_message = "Já existe uma simulação ativa."


class InsufficientBalanceError(DomainConflictError):
    """A movement would make the simulation balance negative."""

    code = "insufficient_balance"
    default_message = "O saldo da simulação é insuficiente para este movimento."


class SimulationTerminalError(DomainConflictError):
    """A completed or cancelled simulation cannot be changed again."""

    code = "simulation_terminal"
    default_message = "A simulação já está encerrada."


class LedgerImmutableError(DomainConflictError):
    """Existing ledger or audit rows cannot be mutated."""

    code = "ledger_immutable"
    default_message = "O histórico financeiro é imutável."


class PersistenceUnavailableError(DomainError):
    """The database cannot currently serve requests."""

    code = "database_unavailable"
    default_message = "O banco de dados está temporariamente indisponível."
    status_code = 503


class PersistenceError(DomainError):
    """An unexpected persistence failure occurred."""

    code = "persistence_error"
    default_message = "Não foi possível persistir os dados."
    status_code = 500
