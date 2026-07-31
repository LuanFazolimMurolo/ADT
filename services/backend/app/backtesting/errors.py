"""Stable, transport-independent backtesting errors."""

from app.domain.errors import (
    DomainConflictError,
    DomainError,
    InvalidDomainInputError,
    PersistenceError,
    ResourceNotFoundError,
)


class BacktestError(DomainError):
    """Base class for safe deterministic-backtest failures."""

    code = "backtest_error"
    default_message = "Não foi possível processar o backtest."


class SnapshotMissingError(ResourceNotFoundError):
    code = "snapshot_missing"
    default_message = "O snapshot solicitado não foi encontrado."


class SnapshotInvalidError(BacktestError):
    code = "snapshot_invalid"
    default_message = "O snapshot solicitado é inválido."


class SnapshotChangedError(DomainConflictError):
    code = "snapshot_changed"
    default_message = "O snapshot mudou durante o backtest."


class StrategyFailureError(BacktestError):
    code = "strategy_failure"
    default_message = "A estratégia falhou durante o backtest."


class InvalidOrderIntentError(InvalidDomainInputError):
    code = "invalid_order_intent"
    default_message = "A intenção de ordem é inválida."


class InsufficientCashError(DomainConflictError):
    code = "insufficient_cash"
    default_message = "O caixa cotado é insuficiente para a ordem."


class InsufficientPositionError(DomainConflictError):
    code = "insufficient_position"
    default_message = "A posição base é insuficiente para a ordem."


class RiskLimitReachedError(DomainConflictError):
    code = "risk_limit_reached"
    default_message = "A ordem excede os limites de risco do backtest."


class MaximumCandlesExceededError(InvalidDomainInputError):
    code = "maximum_candles_exceeded"
    default_message = "O backtest excede o limite seguro de candles."


class MaximumEventsExceededError(DomainConflictError):
    code = "maximum_events_exceeded"
    default_message = "O backtest excedeu o limite seguro de eventos."


class BacktestResultCorruptError(PersistenceError):
    code = "result_corrupt"
    default_message = "Os artefatos do backtest estão corrompidos."


class BacktestResultConflictError(DomainConflictError):
    code = "result_conflict"
    default_message = "O resultado existente diverge da execução solicitada."


class UnsupportedBacktestMarketError(InvalidDomainInputError):
    code = "unsupported_market"
    default_message = "O mercado solicitado não é suportado pelo backtest."


class UnsupportedOrderTypeError(InvalidDomainInputError):
    code = "unsupported_order_type"
    default_message = "O tipo de ordem solicitado não é suportado."


class BacktestRunMissingError(ResourceNotFoundError):
    code = "backtest_run_missing"
    default_message = "O resultado de backtest solicitado não existe."


class UnsupportedStrategyError(InvalidDomainInputError):
    code = "unsupported_strategy"
    default_message = "A estratégia solicitada não está registrada para execução local."
