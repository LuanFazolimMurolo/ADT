"""Stable, transport-independent ML contract failures."""

from app.domain.errors import InvalidDomainInputError


class MLContractError(InvalidDomainInputError):
    code = "ml_contract_error"
    default_message = "O contrato de dataset ML é inválido."


class InvalidMLContractError(MLContractError):
    code = "ml_invalid_contract"


class MLTemporalLeakageError(MLContractError):
    code = "ml_temporal_leakage"
    default_message = "O contrato ML viola um limite temporal de informação."


class MLIncompatibleReferenceError(MLContractError):
    code = "ml_incompatible_reference"
    default_message = "As referências do contrato ML são incompatíveis."


class MLDuplicateObservationError(MLContractError):
    code = "ml_duplicate_observation"
    default_message = "O dataset ML contém uma observação duplicada."


class MLUnsupportedSchemaError(MLContractError):
    code = "ml_unsupported_schema"
    default_message = "A versão do contrato ML não é suportada."


class MLCorruptDocumentError(MLContractError):
    code = "ml_corrupt_document"
    default_message = "O documento ML está corrompido ou não é canônico."
