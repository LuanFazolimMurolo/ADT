"""Safe domain failures for operational paper-session materializations."""

from app.domain.errors import DomainConflictError, DomainError


class InvalidOperationalPaperSessionMaterializationSpecificationError(DomainError):
    code = "operational_paper_session_materialization_invalid_specification"
    default_message = "A especificação da materialização operacional de sessão paper é inválida."
    status_code = 400


class OperationalPaperSessionMaterializationBoundsExceededError(DomainError):
    code = "operational_paper_session_materialization_bounds_exceeded"
    default_message = "Um limite da materialização operacional de sessão paper foi excedido."
    status_code = 400


class OperationalPaperSessionMaterializationStateTransitionConflictError(DomainConflictError):
    code = "operational_paper_session_materialization_state_transition_conflict"
    default_message = (
        "A transição de estado da materialização operacional de sessão paper não é permitida."
    )


class OperationalPaperSessionMaterializationChecksumMismatchError(DomainConflictError):
    code = "operational_paper_session_materialization_checksum_mismatch"
    default_message = "O checksum da materialização operacional de sessão paper não confere."


class OperationalPaperSessionMaterializationProfileBindingConflictError(DomainConflictError):
    code = "operational_paper_session_materialization_profile_binding_conflict"
    default_message = "A autorização de capital não corresponde ao perfil operacional aprovado."


class OperationalPaperSessionMaterializationQuoteAssetConflictError(DomainConflictError):
    code = "operational_paper_session_materialization_quote_asset_conflict"
    default_message = (
        "O ativo cotado da autorização não corresponde ao instrumento operacional aprovado."
    )


class OperationalPaperSessionMaterializationConfigIdentityConflictError(DomainConflictError):
    code = "operational_paper_session_materialization_config_identity_conflict"
    default_message = (
        "A identidade da configuração paper não corresponde à materialização operacional."
    )
