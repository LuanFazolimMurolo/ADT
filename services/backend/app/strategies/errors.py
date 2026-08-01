"""Typed failures for versioned strategy plugins and definitions."""

from app.domain.errors import (
    DomainConflictError,
    InvalidDomainInputError,
    ResourceNotFoundError,
)


class StrategyPluginError(ValueError):
    """Base error for strategy-plugin contract failures."""


class InvalidStrategyPluginError(StrategyPluginError):
    """Raised when a plugin descriptor or factory violates its contract."""


class UnsupportedStrategyPluginSchemaError(StrategyPluginError):
    """Raised for an unknown future strategy-plugin schema version."""


class UnsupportedStrategyLifecycleError(StrategyPluginError):
    """Raised for an unknown future strategy lifecycle version."""


class StrategyPluginNotFoundError(StrategyPluginError):
    """Raised when a plugin identity is not explicitly registered."""


class DuplicateStrategyPluginError(StrategyPluginError):
    """Raised when two plugins claim the same canonical identity."""


class StrategyParameterValidationError(StrategyPluginError):
    """Raised when supplied plugin parameters violate the declared schema."""


class StrategyIndicatorCompatibilityError(StrategyPluginError):
    """Raised when required indicator capabilities are not available."""


class InvalidStrategyDefinitionError(InvalidDomainInputError):
    """A persisted strategy definition or CRUD input is invalid."""

    code = "invalid_strategy_definition"
    default_message = "A definição de estratégia informada é inválida."


class StrategyDefinitionNotFoundError(ResourceNotFoundError):
    """The requested persisted strategy definition does not exist."""

    code = "strategy_definition_not_found"
    default_message = "A definição de estratégia não foi encontrada."


class StrategyDefinitionConflictError(DomainConflictError):
    """Base conflict for persisted strategy-definition state."""

    code = "strategy_definition_conflict"
    default_message = "A definição de estratégia conflita com o estado persistido."


class StrategyDefinitionNameConflictError(StrategyDefinitionConflictError):
    """Another definition already owns the normalized display name."""

    code = "strategy_definition_name_conflict"
    default_message = "Já existe uma definição de estratégia com esse nome."


class StrategyDefinitionRevisionConflictError(StrategyDefinitionConflictError):
    """Optimistic concurrency rejected an outdated expected revision."""

    code = "strategy_definition_revision_conflict"
    default_message = "A definição de estratégia foi alterada por outra operação."


class StrategyDefinitionArchivedError(StrategyDefinitionConflictError):
    """Archived definitions cannot be modified or executed."""

    code = "strategy_definition_archived"
    default_message = "A definição de estratégia está arquivada."


class StrategyDefinitionCompatibilityError(StrategyDefinitionConflictError):
    """Persisted plugin metadata is not compatible with the current runtime."""

    code = "strategy_definition_incompatible"
    default_message = "A definição de estratégia não é compatível com esta versão do sistema."
