"""Stable domain failures for deterministic parameter search."""

from app.domain.errors import InvalidDomainInputError


class ParameterSearchError(InvalidDomainInputError):
    """Base failure for finite parameter-search contracts."""

    code = "parameter_search_error"
    default_message = "O espaço de busca de parâmetros é inválido."


class UnknownSearchParameterError(ParameterSearchError):
    code = "unknown_search_parameter"
    default_message = "O espaço de busca contém um parâmetro desconhecido."


class IncompatibleSearchParameterTypeError(ParameterSearchError):
    code = "incompatible_search_parameter_type"
    default_message = "Um valor do espaço de busca possui tipo incompatível."


class DuplicateSearchParameterValueError(ParameterSearchError):
    code = "duplicate_search_parameter_value"
    default_message = "Um parâmetro pesquisável possui valores canônicos duplicados."


class SearchParameterConflictError(ParameterSearchError):
    code = "search_parameter_conflict"
    default_message = "Um parâmetro não pode ser fixo e pesquisável simultaneamente."


class EmptyParameterSearchSpaceError(ParameterSearchError):
    code = "empty_parameter_search_space"
    default_message = "O espaço deve possuir pelo menos um parâmetro pesquisável."


class EmptySearchParameterValuesError(ParameterSearchError):
    code = "empty_search_parameter_values"
    default_message = "Todo parâmetro pesquisável deve possuir ao menos um valor."


class InvalidCombinationLimitError(ParameterSearchError):
    code = "invalid_combination_limit"
    default_message = "O limite de combinações é inválido."


class SearchCardinalityExceededError(ParameterSearchError):
    code = "search_cardinality_exceeded"
    default_message = "A cardinalidade do espaço excede o limite permitido."


class InvalidSearchCombinationError(ParameterSearchError):
    code = "invalid_search_combination"
    default_message = "Uma combinação foi rejeitada pela factory da estratégia."


class IncompatibleSearchSpaceDocumentError(ParameterSearchError):
    code = "incompatible_search_space_document"
    default_message = "O documento do espaço de busca é incompatível."


class UnsupportedSearchSpaceSchemaError(IncompatibleSearchSpaceDocumentError):
    code = "unsupported_search_space_schema"
    default_message = "A versão do documento do espaço de busca não é suportada."


class SearchSpaceChecksumError(IncompatibleSearchSpaceDocumentError):
    code = "search_space_checksum_mismatch"
    default_message = "O checksum do espaço de busca não confere."
