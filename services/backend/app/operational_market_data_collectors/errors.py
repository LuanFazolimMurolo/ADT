"""Safe domain failures for operational market-data collector control."""

from app.domain.errors import DomainConflictError, DomainError, ResourceNotFoundError


class InvalidOperationalMarketDataCollectorSpecificationError(DomainError):
    code = "operational_market_data_collector_invalid_specification"
    default_message = "A especificação do collector operacional é inválida."
    status_code = 400


class OperationalMarketDataCollectorBoundsExceededError(DomainError):
    code = "operational_market_data_collector_bounds_exceeded"
    default_message = "Um limite do collector operacional foi excedido."
    status_code = 400


class OperationalMarketDataCollectorChecksumMismatchError(DomainConflictError):
    code = "operational_market_data_collector_checksum_mismatch"
    default_message = "O checksum do collector operacional não confere."


class OperationalMarketDataCollectorStateTransitionConflictError(DomainConflictError):
    code = "operational_market_data_collector_state_transition_conflict"
    default_message = "A transição de estado do collector operacional não é permitida."


class OperationalMarketDataCollectorCommandConflictError(DomainConflictError):
    code = "operational_market_data_collector_command_conflict"
    default_message = "O comando solicitado não é permitido para o collector operacional."


class OperationalMarketDataCollectorLeaseError(DomainConflictError):
    code = "operational_market_data_collector_lease_invalid"
    default_message = "A posse operacional do worker do collector é inválida."


class OperationalMarketDataCollectorNotFoundError(ResourceNotFoundError):
    code = "operational_market_data_collector_not_found"
    default_message = "A execução operacional do collector não foi encontrada."


class OperationalMarketDataCollectorRecordVersionConflictError(DomainConflictError):
    code = "operational_market_data_collector_record_version_conflict"
    default_message = "A versão esperada do collector operacional está desatualizada."


class OperationalMarketDataCollectorIdempotencyConflictError(DomainConflictError):
    code = "operational_market_data_collector_idempotency_conflict"
    default_message = "A chave de idempotência conflita com outro comando do collector."


class OperationalMarketDataCollectorCurrentEpochConflictError(DomainConflictError):
    code = "operational_market_data_collector_current_epoch_conflict"
    default_message = "Já existe uma execução operacional não terminal do collector."
