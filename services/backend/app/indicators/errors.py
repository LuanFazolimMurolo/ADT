"""Typed errors for deterministic indicator evaluation."""


class IndicatorError(ValueError):
    """Base error for indicator-domain failures."""


class InvalidIndicatorInputError(IndicatorError):
    """Raised when an indicator or source series violates its contract."""


class FutureDataAccessError(IndicatorError):
    """Raised when a bounded series view is asked for future information."""


class UnsupportedIndicatorSchemaError(IndicatorError):
    """Raised when an indicator descriptor uses an unsupported schema version."""
