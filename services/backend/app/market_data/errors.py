"""Stable, transport-independent market-data errors."""

from app.domain.errors import (
    DomainConflictError,
    DomainError,
    InvalidDomainInputError,
    PersistenceError,
    ResourceNotFoundError,
)


class MarketDataError(DomainError):
    """Base class for safe market-data failures."""

    code = "market_data_error"
    default_message = "Não foi possível processar os dados de mercado."


class UnknownInstrumentError(MarketDataError):
    """The source does not know the requested instrument."""

    code = "unknown_instrument"
    default_message = "O instrumento de mercado não foi encontrado."
    status_code = 404


class InactiveInstrumentError(DomainConflictError):
    """The requested instrument is known but not currently tradable."""

    code = "inactive_instrument"
    default_message = "O instrumento está temporariamente inativo para negociação."


class AssetCatalogLimitError(MarketDataError):
    """The source catalog exceeds a configured defensive bound."""

    code = "asset_catalog_limit"
    default_message = "O catálogo de ativos excede o limite configurado."
    status_code = 503


class UnsupportedTimeframeError(InvalidDomainInputError):
    """The source does not support the requested timeframe."""

    code = "unsupported_timeframe"
    default_message = "O timeframe solicitado não é suportado."


class InvalidMarketResponseError(MarketDataError):
    """The public source returned an unexpected payload."""

    code = "invalid_market_response"
    default_message = "A fonte retornou uma resposta de mercado inválida."
    status_code = 502


class MarketRateLimitError(MarketDataError):
    """The public source asked the client to back off."""

    code = "market_rate_limit"
    default_message = "A fonte de mercado limitou temporariamente as requisições."
    status_code = 429

    def __init__(self, retry_after_seconds: float | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        details = (
            {"retry_after_seconds": retry_after_seconds}
            if retry_after_seconds is not None
            else None
        )
        super().__init__(details=details)


class MarketDataUnavailableError(MarketDataError):
    """The public source is temporarily unavailable."""

    code = "market_data_unavailable"
    default_message = "A fonte de mercado está temporariamente indisponível."
    status_code = 503


class InvalidDataRangeError(InvalidDomainInputError):
    """The requested historical interval is invalid."""

    code = "invalid_data_range"
    default_message = "O intervalo de dados de mercado é inválido."


class MarketDataInconsistencyError(DomainConflictError):
    """Canonical candles failed consistency validation."""

    code = "market_data_inconsistency"
    default_message = "Os dados de mercado apresentam inconsistências."


class MarketDataStorageError(MarketDataError):
    """The local dataset could not be persisted safely."""

    code = "market_data_storage"
    default_message = "Não foi possível persistir o dataset de mercado."
    status_code = 500


class MarketJobNotFoundError(MarketDataError):
    code = "market_job_not_found"
    default_message = "O job de dados de mercado não foi encontrado."
    status_code = 404


class MarketJobLockTimeoutError(MarketDataError):
    code = "market_job_lock_timeout"
    default_message = "Outro job já está processando este dataset."
    status_code = 409


class InvalidMarketOperationRequestError(InvalidDomainInputError):
    """The operational request violates the Phase 2D domain contract."""

    code = "invalid_market_operation_request"
    default_message = "A solicitação operacional de market data é inválida."


class InvalidOperationTransitionError(DomainConflictError):
    """The requested lifecycle transition is not allowed."""

    code = "invalid_operation_transition"
    default_message = "A transição de estado da operação não é permitida."


class OperationIdempotencyConflictError(DomainConflictError):
    """One idempotency key was reused for a different canonical request."""

    code = "operation_idempotency_conflict"
    default_message = "A chave de idempotência já identifica outra solicitação."


class InvalidDatasetIdError(InvalidDomainInputError):
    """An opaque HTTP dataset identifier is malformed or non-canonical."""

    code = "invalid_dataset_id"
    default_message = "O identificador do dataset é inválido."


class InvalidOperationLeaseError(DomainConflictError):
    """A worker lease is invalid, expired or owned by another worker."""

    code = "invalid_operation_lease"
    default_message = "A lease da operação é inválida."


class OperationProgressRegressionError(DomainConflictError):
    """Persisted operational progress may only move forward."""

    code = "operation_progress_regression"
    default_message = "O progresso da operação não pode regredir."


class MarketOperationTerminalError(DomainConflictError):
    """A terminal operation cannot be changed."""

    code = "market_operation_terminal"
    default_message = "A operação de market data já está em estado terminal."


class OperationVersionConflictError(DomainConflictError):
    """An optimistic operation version does not match the expected successor."""

    code = "operation_version_conflict"
    default_message = "A versão da operação diverge do estado esperado."


class MarketOperationNotFoundError(ResourceNotFoundError):
    """The requested operational record does not exist."""

    code = "market_operation_not_found"
    default_message = "A operação de market data não foi encontrada."


class InvalidPersistedOperationError(PersistenceError):
    """A database row violates the immutable operation-domain contract."""

    code = "invalid_persisted_operation"
    default_message = "O estado persistido da operação de market data é inválido."
