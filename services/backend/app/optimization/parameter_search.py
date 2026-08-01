"""Validation, canonical documents and Cartesian expansion for parameter search."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from itertools import product
from typing import TypeAlias

from app.backtesting.domain import StrategyParameters
from app.optimization.canonical import deterministic_id, document_checksum
from app.optimization.documents import (
    decode_document,
    space_payload,
    to_document,
    verify_space_identity,
)
from app.optimization.domain import (
    ABSOLUTE_MAX_COMBINATIONS,
    DEFAULT_MAX_COMBINATIONS,
    SEARCH_SPACE_SCHEMA_VERSION,
    CombinationPolicy,
    FixedParameter,
    ParameterCombination,
    ParameterSearchExpansion,
    ParameterSearchSpace,
    SearchParameter,
    SearchScalar,
    calculate_cardinality,
    canonical_scalar_key,
    validate_search_space_structure,
)
from app.optimization.errors import (
    DuplicateSearchParameterValueError,
    EmptyParameterSearchSpaceError,
    EmptySearchParameterValuesError,
    IncompatibleSearchParameterTypeError,
    IncompatibleSearchSpaceDocumentError,
    InvalidCombinationLimitError,
    InvalidSearchCombinationError,
    SearchParameterConflictError,
    UnknownSearchParameterError,
)
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.definitions import (
    encode_strategy_parameters,
    strategy_parameter_checksum,
)
from app.strategies.domain import (
    IndicatorCapability,
    StrategyParameterSpec,
    StrategyPluginDescriptor,
)
from app.strategies.errors import StrategyPluginError
from app.strategies.registry import StrategyPluginRegistry

RawSearchSpace: TypeAlias = Mapping[str, Sequence[object]]
RawFixedParameters: TypeAlias = Mapping[str, object]


class ParameterSearchService:
    """Construct and expand bounded spaces against the real plugin registry."""

    def __init__(
        self,
        registry: StrategyPluginRegistry | None = None,
        *,
        available_indicators: tuple[IndicatorCapability, ...] | None = None,
    ) -> None:
        self._registry = registry or StrategyPluginRegistry.builtins()
        self._available_indicators = (
            builtin_indicator_capabilities()
            if available_indicators is None
            else tuple(available_indicators)
        )

    def create(
        self,
        plugin_name: str,
        plugin_version: str,
        search_parameters: RawSearchSpace,
        *,
        fixed_parameters: RawFixedParameters | None = None,
        max_combinations: int = DEFAULT_MAX_COMBINATIONS,
    ) -> ParameterSearchSpace:
        """Canonicalize a finite space and reject any invalid combination."""

        descriptor = self._registry.resolve(plugin_name, plugin_version).descriptor
        _validate_limit(max_combinations)
        fixed = _normalize_fixed(descriptor, fixed_parameters or {})
        searchable = _normalize_search(descriptor, search_parameters)
        overlap = sorted({item.name for item in fixed} & {item.name for item in searchable})
        if overlap:
            raise SearchParameterConflictError(
                f"parameters are both fixed and searchable: {', '.join(overlap)}"
            )
        cardinality = _cardinality(searchable, max_combinations)
        payload = space_payload(
            descriptor,
            fixed,
            searchable,
            schema_version=SEARCH_SPACE_SCHEMA_VERSION,
            combination_policy=CombinationPolicy.REJECT_SPACE,
            cardinality=cardinality,
            max_combinations=max_combinations,
        )
        checksum = document_checksum(payload)
        search_space_id = deterministic_id("adt-parameter-search-space-v1", payload)
        space = ParameterSearchSpace(
            plugin_name=descriptor.name,
            plugin_version=descriptor.version,
            plugin_schema_version=descriptor.schema_version,
            plugin_lifecycle_version=descriptor.lifecycle_version,
            fixed_parameters=fixed,
            search_parameters=searchable,
            cardinality=cardinality,
            max_combinations=max_combinations,
            checksum=checksum,
            search_space_id=search_space_id,
        )
        for index, raw in enumerate(_raw_combinations(space)):
            self._build_combination(space, index, raw)
        return space

    def expand(self, space: ParameterSearchSpace) -> ParameterSearchExpansion:
        """Materialize every combination only after bounded cardinality validation."""

        validate_search_space_structure(space)
        descriptor = self._compatible_descriptor(space)
        expected_payload = space_payload(
            descriptor,
            space.fixed_parameters,
            space.search_parameters,
            schema_version=space.schema_version,
            combination_policy=space.combination_policy,
            cardinality=space.cardinality,
            max_combinations=space.max_combinations,
        )
        verify_space_identity(space, expected_payload)
        combinations = tuple(
            self._build_combination(space, index, raw)
            for index, raw in enumerate(_raw_combinations(space))
        )
        return ParameterSearchExpansion(space, combinations)

    def from_document(self, envelope: Mapping[str, object]) -> ParameterSearchSpace:
        """Verify and reconstruct a canonical search space without ambiguity."""

        decoded = decode_document(envelope)
        self._compatible_descriptor(decoded)
        recreated = self.create(
            decoded.plugin_name,
            decoded.plugin_version,
            {item.name: item.values for item in decoded.search_parameters},
            fixed_parameters={item.name: item.value for item in decoded.fixed_parameters},
            max_combinations=decoded.max_combinations,
        )
        if to_document(recreated) != dict(envelope):
            raise IncompatibleSearchSpaceDocumentError("search-space document is not canonical")
        return recreated

    def _compatible_descriptor(self, space: ParameterSearchSpace) -> StrategyPluginDescriptor:
        descriptor = self._registry.resolve(space.plugin_name, space.plugin_version).descriptor
        if (
            descriptor.schema_version != space.plugin_schema_version
            or descriptor.lifecycle_version != space.plugin_lifecycle_version
        ):
            raise IncompatibleSearchSpaceDocumentError("plugin descriptor versions changed")
        return descriptor

    def _build_combination(
        self,
        space: ParameterSearchSpace,
        index: int,
        raw: dict[str, object],
    ) -> ParameterCombination:
        try:
            strategy = self._registry.build(
                space.plugin_name,
                space.plugin_version,
                raw,
                available_indicators=self._available_indicators,
            )
        except StrategyPluginError as error:
            raise InvalidSearchCombinationError(
                f"combination {index} rejected by strategy factory: {error}"
            ) from error
        descriptor = self._registry.resolve(space.plugin_name, space.plugin_version).descriptor
        parameters = _search_parameters(strategy.descriptor.parameters)
        document = encode_strategy_parameters(descriptor, strategy.descriptor.parameters)
        checksum = strategy_parameter_checksum(document)
        identity_payload = {
            "schema_version": 1,
            "search_space_id": space.search_space_id,
            "index": index,
            "parameters_checksum": checksum,
        }
        return ParameterCombination(
            index=index,
            parameters=parameters,
            parameter_document=document,
            parameters_checksum=checksum,
            combination_id=deterministic_id("adt-parameter-combination-v1", identity_payload),
        )


def _normalize_fixed(
    descriptor: StrategyPluginDescriptor,
    raw: RawFixedParameters,
) -> tuple[FixedParameter, ...]:
    specs = {item.name: item for item in descriptor.parameters}
    _raise_unknown(raw, specs)
    fixed: list[FixedParameter] = []
    for spec in descriptor.parameters:
        if spec.name not in raw:
            continue
        fixed.append(FixedParameter(spec.name, spec.kind, _normalize_value(spec, raw[spec.name])))
    return tuple(fixed)


def _normalize_search(
    descriptor: StrategyPluginDescriptor,
    raw: RawSearchSpace,
) -> tuple[SearchParameter, ...]:
    if not raw:
        raise EmptyParameterSearchSpaceError()
    specs = {item.name: item for item in descriptor.parameters}
    _raise_unknown(raw, specs)
    result: list[SearchParameter] = []
    for spec in descriptor.parameters:
        if spec.name not in raw:
            continue
        values = raw[spec.name]
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise IncompatibleSearchParameterTypeError(
                f"search values for {spec.name} must be one finite sequence"
            )
        if not values:
            raise EmptySearchParameterValuesError(f"search parameter {spec.name} has no values")
        normalized = tuple(_normalize_value(spec, value) for value in values)
        keys = tuple(canonical_scalar_key(spec.kind, value) for value in normalized)
        if len(keys) != len(set(keys)):
            raise DuplicateSearchParameterValueError(
                f"search parameter {spec.name} has duplicate canonical values"
            )
        ordered = tuple(
            value
            for _key, value in sorted(
                zip(keys, normalized, strict=True),
                key=lambda item: item[0],
            )
        )
        result.append(SearchParameter(spec.name, spec.kind, ordered))
    return tuple(result)


def _normalize_value(spec: StrategyParameterSpec, raw: object) -> SearchScalar:
    if isinstance(raw, (list, dict, set, bytearray)) or raw is None:
        raise IncompatibleSearchParameterTypeError(
            f"parameter {spec.name} must be an immutable supported scalar"
        )
    try:
        value = spec.normalize(raw)
    except StrategyPluginError as error:
        raise IncompatibleSearchParameterTypeError(str(error)) from error
    if value is None or not isinstance(value, (bool, int, Decimal, str)):
        raise IncompatibleSearchParameterTypeError(
            f"parameter {spec.name} must be bool, int, Decimal or string"
        )
    try:
        canonical_scalar_key(spec.kind, value)
    except IncompatibleSearchSpaceDocumentError as error:
        raise IncompatibleSearchParameterTypeError(str(error)) from error
    return value


def _raise_unknown(
    raw: Mapping[str, object],
    specs: Mapping[str, StrategyParameterSpec],
) -> None:
    if any(not isinstance(key, str) for key in raw):
        raise UnknownSearchParameterError("parameter names must be strings")
    unknown = sorted(key for key in raw if key not in specs)
    if unknown:
        raise UnknownSearchParameterError(f"unknown parameters: {', '.join(unknown)}")


def _cardinality(
    parameters: tuple[SearchParameter, ...],
    maximum: int,
) -> int:
    return calculate_cardinality(parameters, maximum)


def _validate_limit(maximum: int) -> None:
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise InvalidCombinationLimitError("maximum combinations must be positive")
    if maximum > ABSOLUTE_MAX_COMBINATIONS:
        raise InvalidCombinationLimitError(
            f"maximum combinations exceeds absolute limit {ABSOLUTE_MAX_COMBINATIONS}"
        )


def _raw_combinations(space: ParameterSearchSpace) -> Iterator[dict[str, object]]:
    fixed: dict[str, object] = {item.name: item.value for item in space.fixed_parameters}
    dimensions = tuple(item.values for item in space.search_parameters)
    for values in product(*dimensions):
        raw = dict(fixed)
        raw.update(
            (parameter.name, value)
            for parameter, value in zip(space.search_parameters, values, strict=True)
        )
        yield raw


def _search_parameters(parameters: StrategyParameters) -> tuple[tuple[str, SearchScalar], ...]:
    result: list[tuple[str, SearchScalar]] = []
    for name, value in parameters:
        if value is None or not isinstance(value, (bool, int, Decimal, str)):
            raise IncompatibleSearchSpaceDocumentError(
                "strategy factory returned an unsupported normalized parameter"
            )
        result.append((name, value))
    return tuple(result)
