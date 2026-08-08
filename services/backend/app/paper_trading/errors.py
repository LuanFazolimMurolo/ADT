"""Stable transport-independent errors for local paper trading."""

from app.domain.errors import (
    DomainConflictError,
    InvalidDomainInputError,
    PersistenceError,
    ResourceNotFoundError,
)


class PaperTradingError(InvalidDomainInputError):
    code = "paper_trading_error"
    default_message = "Não foi possível processar a sessão de paper trading."


class InvalidPaperSessionError(InvalidDomainInputError):
    code = "invalid_paper_session"
    default_message = "A configuração da sessão de paper trading é inválida."


class PaperSessionNotFoundError(ResourceNotFoundError):
    code = "paper_session_not_found"
    default_message = "A sessão de paper trading não foi encontrada."


class PaperSessionConflictError(DomainConflictError):
    code = "paper_session_conflict"
    default_message = "A sessão existente diverge da configuração solicitada."


class PaperSessionDataUnavailableError(DomainConflictError):
    code = "paper_session_data_unavailable"
    default_message = "Não existem candles locais suficientes para executar a sessão."


class PaperSessionCorruptError(PersistenceError):
    code = "paper_session_corrupt"
    default_message = "O estado persistido da sessão de paper trading está corrompido."


class PaperSessionVerificationError(PersistenceError):
    code = "paper_session_verification_failed"
    default_message = "A sessão de paper trading não pôde ser verificada."


class PaperPortfolioTimelineNotFoundError(ResourceNotFoundError):
    code = "paper_portfolio_timeline_not_found"
    default_message = "A timeline de portfólio do estado atual ainda não foi publicada."


class PaperRunnerStateNotFoundError(ResourceNotFoundError):
    code = "paper_runner_state_not_found"
    default_message = "O runner de paper trading ainda não publicou um ciclo."


class PaperRunnerCorruptError(PersistenceError):
    code = "paper_runner_corrupt"
    default_message = "O estado persistido do runner de paper trading está corrompido."
