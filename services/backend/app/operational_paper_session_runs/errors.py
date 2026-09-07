"""Safe domain failures for operational paper-session run control."""

from app.domain.errors import DomainConflictError, DomainError, ResourceNotFoundError


class InvalidOperationalPaperSessionRunSpecificationError(DomainError):
    code = "operational_paper_session_run_invalid_specification"
    default_message = "A especificação operacional de execução paper é inválida."
    status_code = 400


class OperationalPaperSessionRunBoundsExceededError(DomainError):
    code = "operational_paper_session_run_bounds_exceeded"
    default_message = "Um limite da execução operacional paper foi excedido."
    status_code = 400


class OperationalPaperSessionRunChecksumMismatchError(DomainConflictError):
    code = "operational_paper_session_run_checksum_mismatch"
    default_message = "O checksum da execução operacional paper não confere."


class OperationalPaperSessionRunStateTransitionConflictError(DomainConflictError):
    code = "operational_paper_session_run_state_transition_conflict"
    default_message = "A transição de estado da execução operacional paper não é permitida."


class OperationalPaperSessionRunCommandConflictError(DomainConflictError):
    code = "operational_paper_session_run_command_conflict"
    default_message = "O comando solicitado não é permitido para a execução operacional paper."


class OperationalPaperSessionRunLeaseError(DomainConflictError):
    code = "operational_paper_session_run_lease_invalid"
    default_message = "A posse operacional do worker paper é inválida."


class OperationalPaperSessionRunNotFoundError(ResourceNotFoundError):
    code = "operational_paper_session_run_not_found"
    default_message = "A execução operacional paper solicitada não foi encontrada."


class OperationalPaperSessionRunRecordVersionConflictError(DomainConflictError):
    code = "operational_paper_session_run_record_version_conflict"
    default_message = "A versão esperada da execução operacional paper está desatualizada."


class OperationalPaperSessionRunIdempotencyConflictError(DomainConflictError):
    code = "operational_paper_session_run_idempotency_conflict"
    default_message = "A chave de idempotência conflita com outro comando operacional paper."


class OperationalPaperSessionRunCurrentEpochConflictError(DomainConflictError):
    code = "operational_paper_session_run_current_epoch_conflict"
    default_message = "A sessão paper já possui uma execução operacional não terminal."
