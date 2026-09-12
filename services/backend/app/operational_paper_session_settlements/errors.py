"""Domain errors for official operational paper-session settlements."""

from app.domain.errors import (
    DomainConflictError,
    DomainError,
    ResourceNotFoundError,
)


class OperationalPaperSessionSettlementNotFoundError(ResourceNotFoundError):
    code = "operational_paper_session_settlement_not_found"
    default_message = "O settlement da sessão paper não foi encontrado."


class InvalidOperationalPaperSessionSettlementSpecificationError(DomainError):
    code = "invalid_operational_paper_session_settlement_specification"
    default_message = "A especificação do settlement da sessão paper é inválida."
    status_code = 400


class OperationalPaperSessionSettlementBoundsExceededError(DomainError):
    code = "operational_paper_session_settlement_bounds_exceeded"
    default_message = "Um limite do settlement da sessão paper foi excedido."
    status_code = 400


class OperationalPaperSessionSettlementChecksumMismatchError(DomainConflictError):
    code = "operational_paper_session_settlement_checksum_mismatch"
    default_message = "O checksum do settlement da sessão paper diverge."


class OperationalPaperSessionSettlementIdempotencyConflictError(DomainConflictError):
    code = "operational_paper_session_settlement_idempotency_conflict"
    default_message = "A chave de idempotência já foi usada com outra intenção."


class OperationalPaperSessionSettlementEligibilityConflictError(DomainConflictError):
    code = "operational_paper_session_settlement_eligibility_conflict"
    default_message = "A sessão paper não está elegível para settlement."


class OperationalPaperSessionAlreadySettledError(DomainConflictError):
    code = "operational_paper_session_already_settled"
    default_message = "A sessão paper já possui settlement terminal."
