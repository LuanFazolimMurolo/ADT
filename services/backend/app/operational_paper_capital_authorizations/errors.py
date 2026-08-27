"""Safe domain failures for operational paper-capital authorizations."""

from app.domain.errors import DomainConflictError, DomainError, ResourceNotFoundError


class OperationalPaperCapitalAuthorizationNotFoundError(ResourceNotFoundError):
    code = "operational_paper_capital_authorization_not_found"
    default_message = "A autorização operacional de capital paper solicitada não foi encontrada."


class InvalidOperationalPaperCapitalAuthorizationSpecificationError(DomainError):
    code = "operational_paper_capital_authorization_invalid_specification"
    default_message = "A especificação da autorização operacional de capital paper é inválida."
    status_code = 400


class OperationalPaperCapitalAuthorizationBoundsExceededError(DomainError):
    code = "operational_paper_capital_authorization_bounds_exceeded"
    default_message = "Um limite da autorização operacional de capital paper foi excedido."
    status_code = 400


class OperationalPaperCapitalAuthorizationRecordVersionConflictError(DomainConflictError):
    code = "operational_paper_capital_authorization_record_version_conflict"
    default_message = (
        "A versão esperada da autorização operacional de capital paper está desatualizada."
    )


class OperationalPaperCapitalAuthorizationStateTransitionConflictError(DomainConflictError):
    code = "operational_paper_capital_authorization_state_transition_conflict"
    default_message = (
        "A transição de estado da autorização operacional de capital paper não é permitida."
    )


class OperationalPaperCapitalAuthorizationChecksumMismatchError(DomainConflictError):
    code = "operational_paper_capital_authorization_checksum_mismatch"
    default_message = "O checksum da autorização operacional de capital paper não confere."


class OperationalPaperCapitalAuthorizationIdempotencyConflictError(DomainConflictError):
    code = "operational_paper_capital_authorization_idempotency_conflict"
    default_message = "A chave de idempotência conflita com outra autorização de capital paper."


class OperationalPaperCapitalAuthorizationProfileStateConflictError(DomainConflictError):
    code = "operational_paper_capital_authorization_profile_state_conflict"
    default_message = (
        "O perfil operacional não está apto a receber uma autorização de capital paper."
    )


class OperationalPaperCapitalAuthorizationActiveProfileConflictError(DomainConflictError):
    code = "operational_paper_capital_authorization_active_profile_conflict"
    default_message = "O perfil operacional já possui uma autorização ativa de capital paper."


class OperationalPaperCapitalAuthorizationCurrencyMismatchError(DomainConflictError):
    code = "operational_paper_capital_authorization_currency_mismatch"
    default_message = "A moeda da simulação não corresponde ao ativo cotado do perfil operacional."


class OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError(DomainConflictError):
    code = "operational_paper_capital_authorization_insufficient_available_capital"
    default_message = "O capital paper disponível é insuficiente para esta autorização."


class OperationalPaperCapitalReservationConflictError(DomainConflictError):
    code = "operational_paper_capital_reservation_conflict"
    default_message = "A operação conflita com capital paper atualmente reservado."
