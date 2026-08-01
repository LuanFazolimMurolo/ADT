"""Versioned canonical document codec for finite parameter spaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Protocol, TypeAlias

from app.optimization.canonical import (
    canonical_json_bytes,
    decimal_text,
    deterministic_id,
    document_checksum,
    integer_text,
)
from app.optimization.domain import (
    SUPPORTED_SEARCH_SPACE_SCHEMA_VERSIONS,
    CombinationPolicy,
    FixedParameter,
    ParameterSearchSpace,
    SearchParameter,
    SearchScalar,
    validate_search_space_structure,
)
from app.optimization.errors import (
    IncompatibleSearchSpaceDocumentError,
    SearchSpaceChecksumError,
    UnsupportedSearchSpaceSchemaError,
)
from app.strategies.domain import StrategyParameterKind

SearchSpaceEnvelope: TypeAlias = dict[str, object]


class PluginIdentity(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def schema_version(self) -> int: ...

    @property
    def lifecycle_version(self) -> int: ...


def space_payload(
    descriptor: PluginIdentity,
    fixed: tuple[FixedParameter, ...],
    searchable: tuple[SearchParameter, ...],
    *,
    schema_version: int,
    combination_policy: CombinationPolicy,
    cardinality: int,
    max_combinations: int,
) -> dict[str, object]:
    """Build the exact versioned payload covered by checksum and identity."""

    return {
        "schema_version": schema_version,
        "plugin": {
            "name": descriptor.name,
            "version": descriptor.version,
            "schema_version": descriptor.schema_version,
            "lifecycle_version": descriptor.lifecycle_version,
        },
        "combination_policy": combination_policy.value,
        "max_combinations": max_combinations,
        "cardinality": cardinality,
        "fixed_parameters": [_fixed_payload(item) for item in fixed],
        "search_parameters": [_search_payload(item) for item in searchable],
    }


def to_document(space: ParameterSearchSpace) -> SearchSpaceEnvelope:
    """Return a fresh JSON-compatible canonical checksum envelope."""

    validate_search_space_structure(space)
    descriptor = _SpaceDescriptorProjection(
        name=space.plugin_name,
        version=space.plugin_version,
        schema_version=space.plugin_schema_version,
        lifecycle_version=space.plugin_lifecycle_version,
    )
    payload = space_payload(
        descriptor,
        space.fixed_parameters,
        space.search_parameters,
        schema_version=space.schema_version,
        combination_policy=space.combination_policy,
        cardinality=space.cardinality,
        max_combinations=space.max_combinations,
    )
    verify_space_identity(space, payload)
    return {
        "search_space": payload,
        "checksum": space.checksum,
        "search_space_id": space.search_space_id,
    }


def canonical_document_bytes(space: ParameterSearchSpace) -> bytes:
    return canonical_json_bytes(to_document(space))


def decode_document(envelope: Mapping[str, object]) -> ParameterSearchSpace:
    """Verify envelope integrity and strictly decode JSON-compatible fields."""

    payload, checksum, search_space_id = _parse_envelope(envelope)
    schema_version = payload.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in SUPPORTED_SEARCH_SPACE_SCHEMA_VERSIONS
    ):
        raise UnsupportedSearchSpaceSchemaError(
            f"unsupported search-space schema version: {schema_version}"
        )
    if document_checksum(payload) != checksum:
        raise SearchSpaceChecksumError()
    if deterministic_id("adt-parameter-search-space-v1", payload) != search_space_id:
        raise IncompatibleSearchSpaceDocumentError("search-space identifier does not match")

    plugin = _mapping(payload.get("plugin"), "plugin")
    if set(plugin) != {"name", "version", "schema_version", "lifecycle_version"}:
        raise IncompatibleSearchSpaceDocumentError("plugin identity fields are invalid")
    raw_policy = payload.get("combination_policy")
    if not isinstance(raw_policy, str):
        raise IncompatibleSearchSpaceDocumentError("combination policy is invalid")
    try:
        policy = CombinationPolicy(raw_policy)
    except ValueError:
        raise IncompatibleSearchSpaceDocumentError("combination policy is unsupported") from None
    space = ParameterSearchSpace(
        plugin_name=_required_str(plugin, "name"),
        plugin_version=_required_str(plugin, "version"),
        plugin_schema_version=_required_int(plugin, "schema_version"),
        plugin_lifecycle_version=_required_int(plugin, "lifecycle_version"),
        fixed_parameters=_decode_fixed(payload.get("fixed_parameters")),
        search_parameters=_decode_search(payload.get("search_parameters")),
        cardinality=_required_int(payload, "cardinality"),
        max_combinations=_required_int(payload, "max_combinations"),
        checksum=checksum,
        search_space_id=search_space_id,
        combination_policy=policy,
        schema_version=schema_version,
    )
    verify_space_identity(space, payload)
    return space


def verify_space_identity(space: ParameterSearchSpace, payload: object) -> None:
    """Reject a mutated or internally inconsistent immutable space."""

    if document_checksum(payload) != space.checksum:
        raise SearchSpaceChecksumError()
    if deterministic_id("adt-parameter-search-space-v1", payload) != space.search_space_id:
        raise IncompatibleSearchSpaceDocumentError("search-space identifier does not match")


class _SpaceDescriptorProjection:
    def __init__(
        self,
        *,
        name: str,
        version: str,
        schema_version: int,
        lifecycle_version: int,
    ) -> None:
        self.name = name
        self.version = version
        self.schema_version = schema_version
        self.lifecycle_version = lifecycle_version


def _fixed_payload(parameter: FixedParameter) -> dict[str, object]:
    return {
        "name": parameter.name,
        "kind": parameter.kind.value,
        "value": _stored_value(parameter.kind, parameter.value),
    }


def _search_payload(parameter: SearchParameter) -> dict[str, object]:
    return {
        "name": parameter.name,
        "kind": parameter.kind.value,
        "values": [_stored_value(parameter.kind, value) for value in parameter.values],
    }


def _stored_value(kind: StrategyParameterKind, value: SearchScalar) -> object:
    if kind is StrategyParameterKind.DECIMAL:
        if not isinstance(value, Decimal):
            raise IncompatibleSearchSpaceDocumentError("Decimal kind has non-Decimal value")
        return decimal_text(value)
    if kind is StrategyParameterKind.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise IncompatibleSearchSpaceDocumentError("integer kind has non-integer value")
        integer_text(value)
    return value


def _parse_envelope(
    envelope: Mapping[str, object],
) -> tuple[dict[str, object], str, str]:
    if set(envelope) != {"search_space", "checksum", "search_space_id"}:
        raise IncompatibleSearchSpaceDocumentError("search-space envelope fields are invalid")
    payload = _mapping(envelope.get("search_space"), "search_space")
    if set(payload) != {
        "schema_version",
        "plugin",
        "combination_policy",
        "max_combinations",
        "cardinality",
        "fixed_parameters",
        "search_parameters",
    }:
        raise IncompatibleSearchSpaceDocumentError("search-space payload fields are invalid")
    checksum = envelope.get("checksum")
    search_space_id = envelope.get("search_space_id")
    if not isinstance(checksum, str) or not isinstance(search_space_id, str):
        raise IncompatibleSearchSpaceDocumentError("search-space identity fields are invalid")
    return payload, checksum, search_space_id


def _decode_fixed(raw: object) -> tuple[FixedParameter, ...]:
    entries = _sequence(raw, "fixed_parameters")
    result: list[FixedParameter] = []
    for entry in entries:
        item = _mapping(entry, "fixed parameter")
        if set(item) != {"name", "kind", "value"}:
            raise IncompatibleSearchSpaceDocumentError("fixed parameter fields are invalid")
        name = _required_str(item, "name")
        kind = _kind(item.get("kind"))
        result.append(FixedParameter(name, kind, _decoded_value(kind, item.get("value"))))
    return tuple(result)


def _decode_search(raw: object) -> tuple[SearchParameter, ...]:
    entries = _sequence(raw, "search_parameters")
    result: list[SearchParameter] = []
    for entry in entries:
        item = _mapping(entry, "search parameter")
        if set(item) != {"name", "kind", "values"}:
            raise IncompatibleSearchSpaceDocumentError("search parameter fields are invalid")
        name = _required_str(item, "name")
        kind = _kind(item.get("kind"))
        values = tuple(
            _decoded_value(kind, value) for value in _sequence(item.get("values"), "values")
        )
        result.append(SearchParameter(name, kind, values))
    return tuple(result)


def _decoded_value(kind: StrategyParameterKind, raw: object) -> SearchScalar:
    if kind is StrategyParameterKind.BOOLEAN:
        if not isinstance(raw, bool):
            raise IncompatibleSearchSpaceDocumentError("boolean document value is invalid")
        return raw
    if kind is StrategyParameterKind.INTEGER:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise IncompatibleSearchSpaceDocumentError("integer document value is invalid")
        integer_text(raw)
        return raw
    if kind is StrategyParameterKind.DECIMAL:
        if not isinstance(raw, str):
            raise IncompatibleSearchSpaceDocumentError("Decimal document value is invalid")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise IncompatibleSearchSpaceDocumentError(
                "Decimal document value is invalid"
            ) from None
        if decimal_text(value) != raw:
            raise IncompatibleSearchSpaceDocumentError("Decimal document value is not canonical")
        return value
    if not isinstance(raw, str):
        raise IncompatibleSearchSpaceDocumentError("string document value is invalid")
    return raw


def _mapping(raw: object, label: str) -> dict[str, object]:
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise IncompatibleSearchSpaceDocumentError(f"{label} must be an object")
    return dict(raw)


def _sequence(raw: object, label: str) -> tuple[object, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise IncompatibleSearchSpaceDocumentError(f"{label} must be an array")
    return tuple(raw)


def _required_str(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise IncompatibleSearchSpaceDocumentError(f"{key} must be a non-empty string")
    return value


def _required_int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise IncompatibleSearchSpaceDocumentError(f"{key} must be an integer")
    return value


def _kind(raw: object) -> StrategyParameterKind:
    if not isinstance(raw, str):
        raise IncompatibleSearchSpaceDocumentError("parameter kind is invalid")
    try:
        return StrategyParameterKind(raw)
    except ValueError:
        raise IncompatibleSearchSpaceDocumentError("parameter kind is unsupported") from None
