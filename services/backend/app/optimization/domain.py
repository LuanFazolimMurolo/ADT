"""Immutable public contracts for finite deterministic parameter search."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TypeAlias

from app.optimization.canonical import decimal_text, deterministic_id, integer_text
from app.optimization.errors import (
    DuplicateSearchParameterValueError,
    EmptyParameterSearchSpaceError,
    EmptySearchParameterValuesError,
    IncompatibleSearchParameterTypeError,
    IncompatibleSearchSpaceDocumentError,
    InvalidCombinationLimitError,
    SearchCardinalityExceededError,
    SearchParameterConflictError,
    UnsupportedSearchSpaceSchemaError,
)
from app.strategies.definitions import (
    StoredStrategyParameter,
    StrategyParameterDocument,
    strategy_parameter_checksum,
)
from app.strategies.domain import StrategyParameterKind
from app.strategies.errors import InvalidStrategyDefinitionError

DEFAULT_MAX_COMBINATIONS = 1_000
ABSOLUTE_MAX_COMBINATIONS = 100_000
SEARCH_SPACE_SCHEMA_VERSION = 1
SUPPORTED_SEARCH_SPACE_SCHEMA_VERSIONS = frozenset({SEARCH_SPACE_SCHEMA_VERSION})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

SearchScalar: TypeAlias = bool | int | Decimal | str


class CombinationPolicy(StrEnum):
    """How semantic failures in individual combinations are handled."""

    REJECT_SPACE = "REJECT_SPACE"


@dataclass(frozen=True, slots=True)
class FixedParameter:
    """One explicitly fixed, normalized plugin parameter."""

    name: str
    kind: StrategyParameterKind
    value: SearchScalar

    def __post_init__(self) -> None:
        validate_fixed_parameter(self)


@dataclass(frozen=True, slots=True)
class SearchParameter:
    """One plugin parameter and its canonical finite value set."""

    name: str
    kind: StrategyParameterKind
    values: tuple[SearchScalar, ...]

    def __post_init__(self) -> None:
        validate_search_parameter(self)


@dataclass(frozen=True, slots=True)
class ParameterSearchSpace:
    """Validated canonical identity of one finite parameter space."""

    plugin_name: str
    plugin_version: str
    plugin_schema_version: int
    plugin_lifecycle_version: int
    fixed_parameters: tuple[FixedParameter, ...]
    search_parameters: tuple[SearchParameter, ...]
    cardinality: int
    max_combinations: int
    checksum: str
    search_space_id: str
    combination_policy: CombinationPolicy = CombinationPolicy.REJECT_SPACE
    schema_version: int = SEARCH_SPACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_search_space_structure(self)


@dataclass(frozen=True, slots=True)
class ParameterCombination:
    """One future-planner-ready normalized strategy configuration."""

    index: int
    parameters: tuple[tuple[str, SearchScalar], ...]
    parameter_document: StrategyParameterDocument
    parameters_checksum: str
    combination_id: str

    def __post_init__(self) -> None:
        validate_parameter_combination_structure(self)


@dataclass(frozen=True, slots=True)
class ParameterSearchExpansion:
    """Strict expansion result; rejected combinations are never omitted."""

    space: ParameterSearchSpace
    combinations: tuple[ParameterCombination, ...]
    rejected_combinations: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.space, ParameterSearchSpace):
            raise IncompatibleSearchSpaceDocumentError("expansion search space is invalid")
        validate_search_space_structure(self.space)
        if not isinstance(self.combinations, tuple):
            raise IncompatibleSearchSpaceDocumentError("expanded combinations must be a tuple")
        if (
            isinstance(self.rejected_combinations, bool)
            or not isinstance(self.rejected_combinations, int)
            or self.rejected_combinations != 0
        ):
            raise IncompatibleSearchSpaceDocumentError(
                "REJECT_SPACE expansions cannot contain rejected combinations"
            )
        if len(self.combinations) != self.space.cardinality:
            raise IncompatibleSearchSpaceDocumentError(
                "expanded combination count diverges from cardinality"
            )
        for item in self.combinations:
            validate_parameter_combination(item, self.space)
        if tuple(item.index for item in self.combinations) != tuple(range(self.space.cardinality)):
            raise IncompatibleSearchSpaceDocumentError(
                "combination indexes must be contiguous and deterministic"
            )


def validate_fixed_parameter(parameter: FixedParameter) -> None:
    """Validate one already-canonical public fixed-parameter contract."""

    _validate_parameter_name(parameter.name)
    _validate_scalar(parameter.kind, parameter.value)


def validate_search_parameter(parameter: SearchParameter) -> None:
    """Reject non-canonical public search dimensions without rewriting them."""

    _validate_parameter_name(parameter.name)
    if not isinstance(parameter.kind, StrategyParameterKind):
        raise IncompatibleSearchParameterTypeError("search parameter kind is invalid")
    if not isinstance(parameter.values, tuple):
        raise IncompatibleSearchParameterTypeError("search parameter values must be a tuple")
    if not parameter.values:
        raise EmptySearchParameterValuesError(f"search parameter {parameter.name} has no values")
    keys: list[tuple[int, str]] = []
    for value in parameter.values:
        _validate_scalar(parameter.kind, value)
        keys.append(canonical_scalar_key(parameter.kind, value))
    if len(keys) != len(set(keys)):
        raise DuplicateSearchParameterValueError(
            f"search parameter {parameter.name} has duplicate canonical values"
        )
    if keys != sorted(keys):
        raise IncompatibleSearchParameterTypeError(
            f"search parameter {parameter.name} values are not in canonical order"
        )


def validate_search_space_structure(space: ParameterSearchSpace) -> int:
    """Revalidate all structural invariants without invoking any strategy factory."""

    if not isinstance(space, ParameterSearchSpace):
        raise IncompatibleSearchSpaceDocumentError("parameter search space is invalid")
    if (
        isinstance(space.schema_version, bool)
        or not isinstance(space.schema_version, int)
        or space.schema_version not in SUPPORTED_SEARCH_SPACE_SCHEMA_VERSIONS
    ):
        raise UnsupportedSearchSpaceSchemaError(
            f"unsupported search-space schema version: {space.schema_version}"
        )
    _validate_safe_token(space.plugin_name, "plugin name")
    _validate_safe_token(space.plugin_version, "plugin version")
    _validate_positive_integer(space.plugin_schema_version, "plugin schema version")
    _validate_positive_integer(space.plugin_lifecycle_version, "plugin lifecycle version")
    if not isinstance(space.fixed_parameters, tuple):
        raise IncompatibleSearchSpaceDocumentError("fixed parameters must be a tuple")
    if not isinstance(space.search_parameters, tuple):
        raise IncompatibleSearchSpaceDocumentError("search parameters must be a tuple")
    if not space.search_parameters:
        raise EmptyParameterSearchSpaceError()
    if not isinstance(space.combination_policy, CombinationPolicy):
        raise IncompatibleSearchSpaceDocumentError("combination policy is invalid")
    if space.combination_policy is not CombinationPolicy.REJECT_SPACE:
        raise IncompatibleSearchSpaceDocumentError("combination policy is unsupported")

    fixed_names: list[str] = []
    for fixed_parameter in space.fixed_parameters:
        if not isinstance(fixed_parameter, FixedParameter):
            raise IncompatibleSearchSpaceDocumentError("fixed parameter contract is invalid")
        validate_fixed_parameter(fixed_parameter)
        fixed_names.append(fixed_parameter.name)
    search_names: list[str] = []
    for search_parameter in space.search_parameters:
        if not isinstance(search_parameter, SearchParameter):
            raise IncompatibleSearchSpaceDocumentError("search parameter contract is invalid")
        validate_search_parameter(search_parameter)
        search_names.append(search_parameter.name)
    _validate_unique_canonical_names(fixed_names, "fixed")
    _validate_unique_canonical_names(search_names, "search")
    overlap = sorted(set(fixed_names) & set(search_names))
    if overlap:
        raise SearchParameterConflictError(
            f"parameters are both fixed and searchable: {', '.join(overlap)}"
        )

    _validate_combination_limit(space.max_combinations)
    if isinstance(space.cardinality, bool) or not isinstance(space.cardinality, int):
        raise IncompatibleSearchSpaceDocumentError("search cardinality must be an integer")
    if space.cardinality < 1:
        raise IncompatibleSearchSpaceDocumentError("search cardinality must be positive")
    calculated = calculate_cardinality(space.search_parameters, space.max_combinations)
    if calculated != space.cardinality:
        raise IncompatibleSearchSpaceDocumentError(
            "search cardinality diverges from the exact dimension product"
        )
    _validate_sha256(space.checksum, "search-space checksum")
    _validate_sha256(space.search_space_id, "search-space id")
    return calculated


def validate_parameter_combination_structure(combination: ParameterCombination) -> None:
    """Validate one combination without needing its parent search-space identity."""

    if not isinstance(combination, ParameterCombination):
        raise IncompatibleSearchSpaceDocumentError("parameter combination is invalid")
    if (
        isinstance(combination.index, bool)
        or not isinstance(combination.index, int)
        or combination.index < 0
    ):
        raise IncompatibleSearchSpaceDocumentError(
            "combination index must be a non-negative integer"
        )
    parameters = combination.parameters
    if not isinstance(parameters, tuple):
        raise IncompatibleSearchSpaceDocumentError("combination parameters must be a tuple")
    parameter_names: list[str] = []
    parameter_values: list[SearchScalar] = []
    for entry in parameters:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise IncompatibleSearchSpaceDocumentError(
                "each normalized parameter must be a two-item tuple"
            )
        raw_name, raw_value = entry
        _validate_parameter_name(raw_name)
        if not isinstance(raw_value, (bool, int, Decimal, str)):
            raise IncompatibleSearchParameterTypeError(
                "normalized parameter value has an unsupported type"
            )
        parameter_names.append(raw_name)
        parameter_values.append(raw_value)
    _validate_unique_canonical_names(parameter_names, "combination")

    document = combination.parameter_document
    decoded = decode_parameter_document_scalars(document)
    document_names = [name for name, _kind, _value in decoded]
    if parameter_names != document_names:
        raise IncompatibleSearchSpaceDocumentError(
            "normalized parameters diverge from parameter document names"
        )
    for parameter_value, (_name, kind, document_value) in zip(
        parameter_values, decoded, strict=True
    ):
        canonical_scalar_key(kind, parameter_value)
        if type(parameter_value) is not type(document_value) or parameter_value != document_value:
            raise IncompatibleSearchSpaceDocumentError(
                "normalized parameters diverge from their typed document"
            )

    _validate_sha256(combination.parameters_checksum, "parameter checksum")
    _validate_sha256(combination.combination_id, "combination id")
    try:
        expected_checksum = strategy_parameter_checksum(document)
    except InvalidStrategyDefinitionError as error:
        raise IncompatibleSearchSpaceDocumentError("parameter document is incompatible") from error
    if combination.parameters_checksum != expected_checksum:
        raise IncompatibleSearchSpaceDocumentError("parameter checksum does not match")


def validate_parameter_combination(
    combination: ParameterCombination,
    space: ParameterSearchSpace,
) -> None:
    """Bind a structurally valid combination to one exact search space."""

    if not isinstance(space, ParameterSearchSpace):
        raise IncompatibleSearchSpaceDocumentError("parameter search space is invalid")
    validate_search_space_structure(space)
    validate_parameter_combination_structure(combination)
    if combination.index >= space.cardinality:
        raise IncompatibleSearchSpaceDocumentError("combination index exceeds search space")
    expected_id = deterministic_id(
        "adt-parameter-combination-v1",
        {
            "schema_version": 1,
            "search_space_id": space.search_space_id,
            "index": combination.index,
            "parameters_checksum": combination.parameters_checksum,
        },
    )
    if combination.combination_id != expected_id:
        raise IncompatibleSearchSpaceDocumentError("combination identifier does not match")


def decode_parameter_document_scalars(
    document: StrategyParameterDocument,
) -> tuple[tuple[str, StrategyParameterKind, SearchScalar], ...]:
    """Strictly decode a typed Phase 3C document without plugin coercion."""

    if not isinstance(document, tuple):
        raise IncompatibleSearchSpaceDocumentError("parameter document must be a tuple")
    result: list[tuple[str, StrategyParameterKind, SearchScalar]] = []
    names: list[str] = []
    for item in document:
        if not isinstance(item, StoredStrategyParameter):
            raise IncompatibleSearchSpaceDocumentError("parameter document entry is invalid")
        raw_name = item.name
        raw_kind = item.kind
        raw_value = item.value
        _validate_parameter_name(raw_name)
        if not isinstance(raw_kind, StrategyParameterKind):
            raise IncompatibleSearchSpaceDocumentError("parameter document kind is invalid")
        try:
            recreated = StoredStrategyParameter(raw_name, raw_kind, raw_value)
        except (InvalidStrategyDefinitionError, TypeError, ValueError) as error:
            raise IncompatibleSearchSpaceDocumentError(
                "parameter document entry is incompatible"
            ) from error
        if recreated != item:
            raise IncompatibleSearchSpaceDocumentError("parameter document entry is not canonical")
        value: SearchScalar
        if raw_kind is StrategyParameterKind.DECIMAL:
            if not isinstance(raw_value, str):
                raise IncompatibleSearchSpaceDocumentError("Decimal parameter text is invalid")
            try:
                value = Decimal(raw_value)
            except InvalidOperation:
                raise IncompatibleSearchSpaceDocumentError(
                    "Decimal parameter text is invalid"
                ) from None
            if decimal_text(value) != raw_value:
                raise IncompatibleSearchSpaceDocumentError(
                    "Decimal parameter text is not canonical"
                )
        elif raw_kind is StrategyParameterKind.BOOLEAN:
            if not isinstance(raw_value, bool):
                raise IncompatibleSearchSpaceDocumentError("boolean parameter is invalid")
            value = raw_value
        elif raw_kind is StrategyParameterKind.INTEGER:
            if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                raise IncompatibleSearchSpaceDocumentError("integer parameter is invalid")
            integer_text(raw_value)
            value = raw_value
        else:
            if not isinstance(raw_value, str):
                raise IncompatibleSearchSpaceDocumentError("string parameter is invalid")
            value = raw_value
        names.append(raw_name)
        result.append((raw_name, raw_kind, value))
    _validate_unique_canonical_names(names, "parameter document")
    return tuple(result)


def calculate_cardinality(
    parameters: tuple[SearchParameter, ...],
    maximum: int,
) -> int:
    """Calculate a bounded exact product before any Cartesian materialization."""

    _validate_combination_limit(maximum)
    if not isinstance(parameters, tuple) or not parameters:
        raise EmptyParameterSearchSpaceError()
    cardinality = 1
    for parameter in parameters:
        if not isinstance(parameter, SearchParameter):
            raise IncompatibleSearchSpaceDocumentError("search parameter contract is invalid")
        validate_search_parameter(parameter)
        count = len(parameter.values)
        if cardinality > maximum // count:
            raise SearchCardinalityExceededError(
                f"search cardinality exceeds requested limit {maximum}"
            )
        cardinality *= count
    return cardinality


def canonical_scalar_key(kind: StrategyParameterKind, value: object) -> tuple[int, str]:
    """Return the deterministic ordering and duplicate-detection key for one scalar."""

    if kind is StrategyParameterKind.BOOLEAN:
        if not isinstance(value, bool):
            raise IncompatibleSearchParameterTypeError("boolean parameter value is invalid")
        return 0, "1" if value else "0"
    if kind is StrategyParameterKind.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise IncompatibleSearchParameterTypeError("integer parameter value is invalid")
        return 1, integer_text(value)
    if kind is StrategyParameterKind.DECIMAL:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise IncompatibleSearchParameterTypeError("Decimal parameter value is invalid")
        return 2, decimal_text(value)
    if kind is StrategyParameterKind.STRING:
        if not isinstance(value, str) or not value or value != value.strip():
            raise IncompatibleSearchParameterTypeError("string parameter value is invalid")
        return 3, value
    raise IncompatibleSearchParameterTypeError("parameter kind is invalid")


def _validate_scalar(kind: StrategyParameterKind, value: object) -> None:
    if not isinstance(kind, StrategyParameterKind):
        raise IncompatibleSearchParameterTypeError("parameter kind is invalid")
    canonical_scalar_key(kind, value)


def _validate_parameter_name(value: object) -> None:
    _validate_safe_token(value, "parameter name")


def _validate_safe_token(value: object, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise IncompatibleSearchSpaceDocumentError(f"{label} must be canonical text")
    if _SAFE_TOKEN.fullmatch(value) is None:
        raise IncompatibleSearchSpaceDocumentError(f"{label} must be a safe identifier")


def _validate_positive_integer(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise IncompatibleSearchSpaceDocumentError(f"{label} must be a positive integer")


def _validate_combination_limit(maximum: object) -> None:
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise InvalidCombinationLimitError("maximum combinations must be positive")
    if maximum > ABSOLUTE_MAX_COMBINATIONS:
        raise InvalidCombinationLimitError(
            f"maximum combinations exceeds absolute limit {ABSOLUTE_MAX_COMBINATIONS}"
        )


def _validate_unique_canonical_names(names: list[str], label: str) -> None:
    if len(names) != len(set(names)):
        raise IncompatibleSearchSpaceDocumentError(f"{label} parameter names must be unique")
    if names != sorted(names):
        raise IncompatibleSearchSpaceDocumentError(
            f"{label} parameters must use canonical name order"
        )


def _validate_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise IncompatibleSearchSpaceDocumentError(f"{label} must be lowercase SHA-256")
