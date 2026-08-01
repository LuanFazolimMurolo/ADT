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


class TemporalSegmentationError(InvalidDomainInputError):
    """Base failure for deterministic temporal-segmentation contracts."""

    code = "temporal_segmentation_error"
    default_message = "O plano de segmentação temporal é inválido."


class UnsupportedTemporalSegmentationSchemaError(TemporalSegmentationError):
    code = "unsupported_temporal_segmentation_schema"
    default_message = "A versão do plano de segmentação temporal não é suportada."


class IncompatibleTemporalSnapshotError(TemporalSegmentationError):
    code = "incompatible_temporal_snapshot"
    default_message = "O snapshot é incompatível com o plano temporal."


class InvalidTemporalCoverageError(TemporalSegmentationError):
    code = "invalid_temporal_coverage"
    default_message = "A cobertura temporal é inválida."


class NonUtcTemporalTimestampError(InvalidTemporalCoverageError):
    code = "non_utc_temporal_timestamp"
    default_message = "Os limites temporais devem possuir timezone UTC explícito."


class InvalidTemporalTimeframeError(TemporalSegmentationError):
    code = "invalid_temporal_timeframe"
    default_message = "O timeframe do plano temporal é inválido."


class MisalignedTemporalBoundaryError(InvalidTemporalCoverageError):
    code = "misaligned_temporal_boundary"
    default_message = "Um limite temporal não está alinhado ao timeframe."


class InvalidTemporalCandleCountError(TemporalSegmentationError):
    code = "invalid_temporal_candle_count"
    default_message = "Uma quantidade de velas do plano temporal é inválida."


class InsufficientTemporalCoverageError(InvalidTemporalCoverageError):
    code = "insufficient_temporal_coverage"
    default_message = "A cobertura do snapshot é insuficiente para o plano temporal."


class TemporalCandleCountMismatchError(InvalidTemporalCoverageError):
    code = "temporal_candle_count_mismatch"
    default_message = "A soma das velas diverge da cobertura selecionada."


class TemporalSegmentOverlapError(TemporalSegmentationError):
    code = "temporal_segment_overlap"
    default_message = "Os segmentos temporais possuem sobreposição."


class TemporalSegmentOrderError(TemporalSegmentationError):
    code = "temporal_segment_order"
    default_message = "Os segmentos temporais estão fora da ordem canônica."


class TemporalSegmentGapError(TemporalSegmentationError):
    code = "temporal_segment_gap"
    default_message = "A política temporal não permite lacunas entre segmentos."


class InvalidTemporalWarmupError(TemporalSegmentationError):
    code = "invalid_temporal_warmup"
    default_message = "A quantidade de velas de warmup é inválida."


class TemporalWarmupUnavailableError(InsufficientTemporalCoverageError):
    code = "temporal_warmup_unavailable"
    default_message = "O snapshot não possui histórico anterior suficiente para o warmup."


class IncompatibleTemporalDocumentError(TemporalSegmentationError):
    code = "incompatible_temporal_document"
    default_message = "O documento de segmentação temporal é incompatível."


class TemporalChecksumError(IncompatibleTemporalDocumentError):
    code = "temporal_checksum_mismatch"
    default_message = "Um checksum do plano temporal não confere."


class TemporalIdentifierError(IncompatibleTemporalDocumentError):
    code = "temporal_identifier_mismatch"
    default_message = "Um identificador do plano temporal não confere."
