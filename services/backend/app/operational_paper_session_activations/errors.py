"""Safe domain failures for operational paper-session activations."""

from app.domain.errors import DomainConflictError, DomainError


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
