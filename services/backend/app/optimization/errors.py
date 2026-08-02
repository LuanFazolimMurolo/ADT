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


class ExperimentPlanningError(InvalidDomainInputError):
    """Base failure for deterministic experiment-planning contracts."""

    code = "experiment_planning_error"
    default_message = "O plano de experimento é inválido."


class UnsupportedExperimentSchemaError(ExperimentPlanningError):
    code = "unsupported_experiment_schema"
    default_message = "A versão do plano de experimento não é suportada."


class IncompatibleExperimentSnapshotError(ExperimentPlanningError):
    code = "incompatible_experiment_snapshot"
    default_message = "O snapshot é incompatível com o experimento."


class IncompatibleExperimentTemporalPlanError(ExperimentPlanningError):
    code = "incompatible_experiment_temporal_plan"
    default_message = "O plano temporal é incompatível com o experimento."


class IncompatibleExperimentSearchSpaceError(ExperimentPlanningError):
    code = "incompatible_experiment_search_space"
    default_message = "O espaço de parâmetros é incompatível com o experimento."


class IncompatibleExperimentPluginError(ExperimentPlanningError):
    code = "incompatible_experiment_plugin"
    default_message = "O plugin de estratégia é incompatível com o experimento."


class InvalidExperimentBacktestConfigurationError(ExperimentPlanningError):
    code = "invalid_experiment_backtest_configuration"
    default_message = "A configuração de backtest do experimento é inválida."


class InvalidExperimentCardinalityError(ExperimentPlanningError):
    code = "invalid_experiment_cardinality"
    default_message = "A cardinalidade do experimento é inválida."


class InvalidRunSpecLimitError(ExperimentPlanningError):
    code = "invalid_run_spec_limit"
    default_message = "O limite de especificações planejadas é inválido."


class RunSpecLimitExceededError(InvalidExperimentCardinalityError):
    code = "run_spec_limit_exceeded"
    default_message = "A cardinalidade do experimento excede o limite permitido."


class ExperimentRunOrderError(ExperimentPlanningError):
    code = "experiment_run_order_error"
    default_message = "As especificações planejadas estão fora da ordem canônica."


class ExperimentRunIndexError(ExperimentPlanningError):
    code = "experiment_run_index_error"
    default_message = "Um índice de especificação planejada é inválido."


class InvalidExperimentRunPurposeError(ExperimentPlanningError):
    code = "invalid_experiment_run_purpose"
    default_message = "O propósito temporal da especificação é inválido."


class ExperimentHoldoutPolicyError(ExperimentPlanningError):
    code = "experiment_holdout_policy_error"
    default_message = "A política de holdout do experimento é inválida."


class DuplicatePlannedRunSpecError(ExperimentPlanningError):
    code = "duplicate_planned_run_spec"
    default_message = "O experimento contém uma especificação planejada duplicada."


class IncompatibleExperimentDocumentError(ExperimentPlanningError):
    code = "incompatible_experiment_document"
    default_message = "O documento do experimento é incompatível."


class ExperimentChecksumError(IncompatibleExperimentDocumentError):
    code = "experiment_checksum_mismatch"
    default_message = "O checksum do experimento não confere."


class ExperimentIdentifierError(IncompatibleExperimentDocumentError):
    code = "experiment_identifier_mismatch"
    default_message = "O identificador do experimento não confere."


class PlannedRunSpecChecksumError(IncompatibleExperimentDocumentError):
    code = "planned_run_spec_checksum_mismatch"
    default_message = "O checksum da especificação planejada não confere."


class PlannedRunSpecIdentifierError(IncompatibleExperimentDocumentError):
    code = "planned_run_spec_identifier_mismatch"
    default_message = "O identificador da especificação planejada não confere."


class ExperimentExecutionError(InvalidDomainInputError):
    """Base failure for deterministic local experiment execution."""

    code = "experiment_execution_error"
    default_message = "A execução do experimento é inválida."


class InvalidExperimentExecutionPlanError(ExperimentExecutionError):
    code = "invalid_experiment_execution_plan"
    default_message = "O plano não pode ser executado com os contratos atuais."


class ExperimentExecutionLimitExceededError(ExperimentExecutionError):
    code = "experiment_execution_limit_exceeded"
    default_message = "A execução excede o limite local de especificações."


class ExperimentExecutionManifestLimitExceededError(ExperimentExecutionError):
    code = "experiment_execution_manifest_limit_exceeded"
    default_message = "O manifesto de execução excederia o limite local de bytes."


class InvalidExperimentExecutionTransitionError(ExperimentExecutionError):
    code = "invalid_experiment_execution_transition"
    default_message = "A transição de estado da execução é inválida."


class IncompatibleExperimentExecutionDocumentError(ExperimentExecutionError):
    code = "incompatible_experiment_execution_document"
    default_message = "O manifesto de execução é incompatível."


class UnsupportedExperimentExecutionSchemaError(IncompatibleExperimentExecutionDocumentError):
    code = "unsupported_experiment_execution_schema"
    default_message = "A versão do manifesto de execução não é suportada."


class ExperimentExecutionChecksumError(IncompatibleExperimentExecutionDocumentError):
    code = "experiment_execution_checksum_mismatch"
    default_message = "O checksum do manifesto de execução não confere."


class ExperimentExecutionIdentifierError(IncompatibleExperimentExecutionDocumentError):
    code = "experiment_execution_identifier_mismatch"
    default_message = "O identificador do manifesto de execução não confere."


class ExperimentExecutionPublicationError(ExperimentExecutionError):
    code = "experiment_execution_publication_error"
    default_message = "Não foi possível publicar o manifesto de execução."


class ExperimentExecutionArtifactVerificationError(ExperimentExecutionError):
    code = "experiment_execution_artifact_verification_error"
    default_message = "Um artefato referenciado pela execução não pôde ser verificado."


class WalkForwardError(InvalidDomainInputError):
    """Base error for deterministic walk-forward contracts."""

    code = "walk_forward_error"
    default_message = "O contrato de walk-forward é inváido."


class InvalidWalkForwardWindowPolicyError(WalkForwardError):
    code = "invalid_walk_forward_window_policy"
    default_message = "A política temporal de walk-forward é inváida."


class InsufficientWalkForwardFoldsError(WalkForwardError):
    code = "insufficient_walk_forward_folds"
    default_message = "O snapshot não comporta ao menos dois folds completos."


class WalkForwardLimitExceededError(WalkForwardError):
    code = "walk_forward_limit_exceeded"
    default_message = "Um limite operacional de walk-forward foi excedido."


class IncompatibleWalkForwardFoldError(WalkForwardError):
    code = "incompatible_walk_forward_fold"
    default_message = "O fold de walk-forward é incompatível."


class IncompatibleWalkForwardPlanError(WalkForwardError):
    code = "incompatible_walk_forward_plan"
    default_message = "O plano de walk-forward é incompatível."


class InvalidWalkForwardSelectionPolicyError(WalkForwardError):
    code = "invalid_walk_forward_selection_policy"
    default_message = "A política de seleção de walk-forward é inváida."


class UnknownWalkForwardMetricError(WalkForwardError):
    code = "unknown_walk_forward_metric"
    default_message = "A métrica de seleção não é suportada."


class MissingWalkForwardMetricError(WalkForwardError):
    code = "missing_walk_forward_metric"
    default_message = "A métrica de seleção está ausente."


class InvalidWalkForwardMetricError(WalkForwardError):
    code = "invalid_walk_forward_metric"
    default_message = "A métrica de seleção é inváida."


class InvalidWalkForwardCandidateError(WalkForwardError):
    code = "invalid_walk_forward_candidate"
    default_message = "A evidência do candidato de walk-forward é inváida."


class NoEligibleWalkForwardCandidateError(WalkForwardError):
    code = "no_eligible_walk_forward_candidate"
    default_message = "Nenhum candidato elegível foi encontrado no fold."


class WalkForwardSelectionLeakageError(WalkForwardError):
    code = "walk_forward_selection_leakage"
    default_message = "A seleção de walk-forward contém evidência de TEST."


class IncompatibleWalkForwardSelectionError(WalkForwardError):
    code = "incompatible_walk_forward_selection"
    default_message = "A decisão de seleção de walk-forward é incompatível."


class InvalidWalkForwardHoldoutError(WalkForwardError):
    code = "invalid_walk_forward_holdout"
    default_message = "O holdout TEST selecionado é inváido."


class IncompatibleWalkForwardExecutionError(WalkForwardError):
    code = "incompatible_walk_forward_execution"
    default_message = "A execução walk-forward é incompatível."


class IncompatibleWalkForwardDocumentError(WalkForwardError):
    code = "incompatible_walk_forward_document"
    default_message = "O documento walk-forward é incompatível."


class WalkForwardChecksumError(IncompatibleWalkForwardDocumentError):
    code = "walk_forward_checksum_error"
    default_message = "O checksum walk-forward é inváido."


class WalkForwardIdentifierError(IncompatibleWalkForwardDocumentError):
    code = "walk_forward_identifier_error"
    default_message = "A identidade walk-forward é inváida."


class WalkForwardPublicationError(WalkForwardError):
    code = "walk_forward_publication_error"
    default_message = "A publicação walk-forward falhou."
