"""Deterministic bounded parameter-search contracts (Phase 4-01)."""

from app.optimization.canonical import (
    MAX_CANONICAL_DECIMAL_CHARACTERS,
    MAX_CANONICAL_INTEGER_DIGITS,
)
from app.optimization.documents import canonical_document_bytes, to_document
from app.optimization.domain import (
    ABSOLUTE_MAX_COMBINATIONS,
    DEFAULT_MAX_COMBINATIONS,
    SEARCH_SPACE_SCHEMA_VERSION,
    SUPPORTED_SEARCH_SPACE_SCHEMA_VERSIONS,
    CombinationPolicy,
    FixedParameter,
    ParameterCombination,
    ParameterSearchExpansion,
    ParameterSearchSpace,
    SearchParameter,
)
from app.optimization.errors import (
    DuplicateSearchParameterValueError,
    EmptyParameterSearchSpaceError,
    EmptySearchParameterValuesError,
    IncompatibleSearchParameterTypeError,
    IncompatibleSearchSpaceDocumentError,
    InvalidCombinationLimitError,
    InvalidSearchCombinationError,
    ParameterSearchError,
    SearchCardinalityExceededError,
    SearchParameterConflictError,
    SearchSpaceChecksumError,
    UnknownSearchParameterError,
    UnsupportedSearchSpaceSchemaError,
)
from app.optimization.parameter_search import ParameterSearchService

__all__ = [
    "ABSOLUTE_MAX_COMBINATIONS",
    "DEFAULT_MAX_COMBINATIONS",
    "MAX_CANONICAL_DECIMAL_CHARACTERS",
    "MAX_CANONICAL_INTEGER_DIGITS",
    "SEARCH_SPACE_SCHEMA_VERSION",
    "SUPPORTED_SEARCH_SPACE_SCHEMA_VERSIONS",
    "CombinationPolicy",
    "DuplicateSearchParameterValueError",
    "EmptyParameterSearchSpaceError",
    "EmptySearchParameterValuesError",
    "FixedParameter",
    "IncompatibleSearchParameterTypeError",
    "IncompatibleSearchSpaceDocumentError",
    "InvalidCombinationLimitError",
    "InvalidSearchCombinationError",
    "ParameterCombination",
    "ParameterSearchError",
    "ParameterSearchExpansion",
    "ParameterSearchService",
    "ParameterSearchSpace",
    "SearchCardinalityExceededError",
    "SearchParameter",
    "SearchParameterConflictError",
    "SearchSpaceChecksumError",
    "UnknownSearchParameterError",
    "UnsupportedSearchSpaceSchemaError",
    "canonical_document_bytes",
    "to_document",
]
