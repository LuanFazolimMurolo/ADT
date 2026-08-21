"""Safe domain failures for operational mandates."""

from app.domain.errors import DomainConflictError, DomainError, ResourceNotFoundError


class OperationalMandateNotFoundError(ResourceNotFoundError):
    """The requested operational mandate does not exist."""

    code = "operational_mandate_not_found"
    default_message = "O mandato operacional solicitado não foi encontrado."


class InvalidOperationalMandateSpecificationError(DomainError):
    """The supplied mandate specification or snapshot is invalid."""

    code = "operational_mandate_invalid_specification"
    default_message = "A especificação do mandato operacional é inválida."
    status_code = 400


class UnsupportedOperationalMandateCapabilityError(DomainError):
    """The canonical instrument is outside the supported operational boundary."""

    code = "operational_mandate_unsupported_capability"
    default_message = "A capacidade solicitada não é suportada para mandatos operacionais."
    status_code = 400


class OperationalMandateBoundsExceededError(DomainError):
    """One bounded mandate input is empty or exceeds its contract."""

    code = "operational_mandate_bounds_exceeded"
    default_message = "Um limite do mandato operacional foi excedido."
    status_code = 400


class OperationalMandateRevisionConflictError(DomainConflictError):
    """The expected specification revision is stale."""

    code = "operational_mandate_revision_conflict"
    default_message = "A revisão esperada do mandato operacional está desatualizada."


class OperationalMandateRecordVersionConflictError(DomainConflictError):
    """The expected aggregate record version is stale."""

    code = "operational_mandate_record_version_conflict"
    default_message = "A versão esperada do mandato operacional está desatualizada."


class OperationalMandateStateTransitionConflictError(DomainConflictError):
    """The requested lifecycle transition is not allowed."""

    code = "operational_mandate_state_transition_conflict"
    default_message = "A transição de estado do mandato operacional não é permitida."


class OperationalMandateChecksumMismatchError(DomainConflictError):
    """The stored checksum does not match canonical specification semantics."""

    code = "operational_mandate_checksum_mismatch"
    default_message = "O checksum da especificação do mandato operacional não confere."


class OperationalMandateIdempotencyConflictError(DomainConflictError):
    """An idempotency key was reused for different create semantics."""

    code = "operational_mandate_idempotency_conflict"
    default_message = "A chave de idempotência conflita com outra solicitação."
