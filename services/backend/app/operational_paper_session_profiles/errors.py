"""Safe domain failures for operational paper-session profiles."""

from app.domain.errors import DomainConflictError, DomainError, ResourceNotFoundError


class OperationalPaperSessionProfileNotFoundError(ResourceNotFoundError):
    code = "operational_paper_session_profile_not_found"
    default_message = "O perfil operacional de sessão paper solicitado não foi encontrado."


class InvalidOperationalPaperSessionProfileSpecificationError(DomainError):
    code = "operational_paper_session_profile_invalid_specification"
    default_message = "A especificação do perfil operacional de sessão paper é inválida."
    status_code = 400


class InvalidOperationalPaperSessionProfileStrategySnapshotError(DomainError):
    code = "operational_paper_session_profile_invalid_strategy_snapshot"
    default_message = "O snapshot de estratégia do perfil operacional é inválido."
    status_code = 400


class OperationalPaperSessionProfileBoundsExceededError(DomainError):
    code = "operational_paper_session_profile_bounds_exceeded"
    default_message = "Um limite do perfil operacional de sessão paper foi excedido."
    status_code = 400


class OperationalPaperSessionProfileRevisionConflictError(DomainConflictError):
    code = "operational_paper_session_profile_revision_conflict"
    default_message = "A revisão esperada do perfil operacional está desatualizada."


class OperationalPaperSessionProfileRecordVersionConflictError(DomainConflictError):
    code = "operational_paper_session_profile_record_version_conflict"
    default_message = "A versão esperada do perfil operacional está desatualizada."


class OperationalPaperSessionProfileStateTransitionConflictError(DomainConflictError):
    code = "operational_paper_session_profile_state_transition_conflict"
    default_message = "A transição de estado do perfil operacional não é permitida."


class OperationalPaperSessionProfileChecksumMismatchError(DomainConflictError):
    code = "operational_paper_session_profile_checksum_mismatch"
    default_message = "O checksum do perfil operacional de sessão paper não confere."


class OperationalPaperSessionProfileIdempotencyConflictError(DomainConflictError):
    code = "operational_paper_session_profile_idempotency_conflict"
    default_message = "A chave de idempotência conflita com outra solicitação."
