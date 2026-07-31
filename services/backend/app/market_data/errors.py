"""Stable, transport-independent market-data errors."""

from app.domain.errors import DomainConflictError, DomainError, InvalidDomainInputError


class MarketDataError(DomainError):
    """Base class for safe market-data failures."""

    code = "market_data_error"
    default_message = "Não foi possível processar os dados de mercado."


class UnknownInstrumentError(MarketDataError):
    """The source does not know the requested instrument."""

    code = "unknown_instrument"
    default_message = "O instrumento de mercado não foi encontrado."
    status_code = 404


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
