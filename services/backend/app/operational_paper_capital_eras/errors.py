"""Safe domain failures for operational paper-capital eras."""

from app.domain.errors import DomainConflictError, DomainError, ResourceNotFoundError


class OperationalPaperCapitalEraNotFoundError(ResourceNotFoundError):
    code = "operational_paper_capital_era_not_found"
    default_message = "A era oficial de capital paper solicitada não foi encontrada."


class InvalidOperationalPaperCapitalEraSpecificationError(DomainError):
    code = "operational_paper_capital_era_invalid_specification"
    default_message = "A especificação da era oficial de capital paper é inválida."
    status_code = 400


class OperationalPaperCapitalEraBoundsExceededError(DomainError):
    code = "operational_paper_capital_era_bounds_exceeded"
    default_message = "Um limite da era oficial de capital paper foi excedido."
    status_code = 400


class OperationalPaperCapitalEraChecksumMismatchError(DomainConflictError):
    code = "operational_paper_capital_era_checksum_mismatch"
    default_message = "O checksum da era oficial de capital paper não confere."


class OperationalPaperCapitalEraIdempotencyConflictError(DomainConflictError):
    code = "operational_paper_capital_era_idempotency_conflict"
    default_message = "A chave de idempotência conflita com outra designação de era oficial."


class OperationalPaperCapitalEraSimulationAlreadyDesignatedError(DomainConflictError):
    code = "operational_paper_capital_era_simulation_already_designated"
    default_message = "A simulação já pertence a uma era oficial de capital paper."


class OperationalPaperCapitalEraEligibilityConflictError(DomainConflictError):
    code = "operational_paper_capital_era_eligibility_conflict"
    default_message = "A simulação não está apta a ser designada como era oficial de capital paper."


class OperationalPaperCapitalEraActiveConflictError(DomainConflictError):
    code = "operational_paper_capital_era_active_conflict"
    default_message = "Já existe uma era oficial de capital paper ativa."
