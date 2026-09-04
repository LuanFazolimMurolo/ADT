"""Safe domain failures for operational paper-session activations."""

from app.domain.errors import DomainConflictError, DomainError, ResourceNotFoundError


class InvalidOperationalPaperSessionActivationSpecificationError(DomainError):
    code = "operational_paper_session_activation_invalid_specification"
    default_message = "A especificação da ativação operacional de sessão paper é inválida."
    status_code = 400


class OperationalPaperSessionActivationBoundsExceededError(DomainError):
    code = "operational_paper_session_activation_bounds_exceeded"
    default_message = "Um limite da ativação operacional de sessão paper foi excedido."
    status_code = 400


class OperationalPaperSessionActivationChecksumMismatchError(DomainConflictError):
    code = "operational_paper_session_activation_checksum_mismatch"
    default_message = "O checksum da ativação operacional de sessão paper não confere."


class OperationalPaperSessionActivationStateTransitionConflictError(DomainConflictError):
    code = "operational_paper_session_activation_state_transition_conflict"
    default_message = (
        "A transição de estado da ativação operacional de sessão paper não é permitida."
    )


class OperationalPaperSessionActivationNotFoundError(ResourceNotFoundError):
    code = "operational_paper_session_activation_not_found"
    default_message = "A ativação operacional de sessão paper solicitada não foi encontrada."


class OperationalPaperSessionActivationRecordVersionConflictError(DomainConflictError):
    code = "operational_paper_session_activation_record_version_conflict"
    default_message = (
        "A versão esperada da ativação operacional de sessão paper está desatualizada."
    )


class OperationalPaperSessionActivationIdempotencyConflictError(DomainConflictError):
    code = "operational_paper_session_activation_idempotency_conflict"
    default_message = (
        "A chave de idempotência conflita com outra ativação operacional de sessão paper."
    )


class OperationalPaperSessionActivationCurrentGrantConflictError(DomainConflictError):
    code = "operational_paper_session_activation_current_grant_conflict"
    default_message = "A materialização já possui uma ativação operacional autorizada."
