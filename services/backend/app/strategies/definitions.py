"""Versioned strategy-definition contracts and CRUD orchestration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol, TypeAlias
from uuid import UUID

from app.backtesting.domain import StrategyParameters, StrategyParameterValue
from app.backtesting.strategy import BacktestStrategy
from app.strategies.domain import (
    IndicatorCapability,
    RawStrategyParameters,
    StrategyParameterKind,
    StrategyPluginDescriptor,
)
from app.strategies.errors import (
    InvalidStrategyDefinitionError,
    StrategyDefinitionArchivedError,
    StrategyDefinitionCompatibilityError,
    StrategyDefinitionNotFoundError,
)
from app.strategies.protocols import StrategyPlugin
from app.strategies.registry import StrategyPluginRegistry

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
StoredParameterScalar: TypeAlias = None | bool | int | str


class StrategyDefinitionState(StrEnum):
    """Persisted lifecycle states for reusable strategy definitions."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True)
class StoredStrategyParameter:
    """One lossless JSON-compatible persisted strategy parameter."""

    name: str
    kind: StrategyParameterKind
    value: StoredParameterScalar

    def __post_init__(self) -> None:
        name = self.name.strip()
        if _SAFE_TOKEN.fullmatch(name) is None:
            raise InvalidStrategyDefinitionError("O nome do parâmetro persistido é inválido.")
        if not isinstance(self.kind, StrategyParameterKind):
            raise InvalidStrategyDefinitionError("O tipo do parâmetro persistido é inválido.")
        _validate_stored_scalar(self.kind, self.value)
        object.__setattr__(self, "name", name)

    def canonical_payload(self) -> dict[str, StoredParameterScalar]:
        """Return the exact JSON-compatible checksum representation."""

        return {"kind": self.kind.value, "value": self.value}


StrategyParameterDocument: TypeAlias = tuple[StoredStrategyParameter, ...]


@dataclass(frozen=True, slots=True)
class StrategyDefinitionSpec:
    """Validated mutable fields accepted by strategy-definition repositories."""

    display_name: str
    plugin_name: str
    plugin_version: str
    plugin_schema_version: int
    lifecycle_version: int
    parameters: StrategyParameterDocument
    parameters_checksum: str

    def __post_init__(self) -> None:
        display_name = self.display_name.strip()
        plugin_name = self.plugin_name.strip()
        plugin_version = self.plugin_version.strip()
        if not display_name or len(display_name) > 120:
            raise InvalidStrategyDefinitionError(
                "O nome da definição deve possuir entre 1 e 120 caracteres."
            )
        if (
            _SAFE_TOKEN.fullmatch(plugin_name) is None
            or _SAFE_TOKEN.fullmatch(plugin_version) is None
        ):
            raise InvalidStrategyDefinitionError("A identidade do plugin persistido é inválida.")
        if isinstance(self.plugin_schema_version, bool) or self.plugin_schema_version < 1:
            raise InvalidStrategyDefinitionError("A versão de schema persistida é inválida.")
        if isinstance(self.lifecycle_version, bool) or self.lifecycle_version < 1:
            raise InvalidStrategyDefinitionError("A versão de ciclo de vida persistida é inválida.")
        normalized = _normalize_document(self.parameters)
        if _SHA256.fullmatch(self.parameters_checksum) is None:
            raise InvalidStrategyDefinitionError("O checksum dos parâmetros é inválido.")
        if strategy_parameter_checksum(normalized) != self.parameters_checksum:
            raise InvalidStrategyDefinitionError("O checksum dos parâmetros não confere.")
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "plugin_name", plugin_name)
        object.__setattr__(self, "plugin_version", plugin_version)
        object.__setattr__(self, "parameters", normalized)


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    """One reusable and revisioned persisted strategy configuration."""

    id: UUID
    spec: StrategyDefinitionSpec
    state: StrategyDefinitionState
    revision: int
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, StrategyDefinitionState):
            raise InvalidStrategyDefinitionError("O estado da definição é inválido.")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise InvalidStrategyDefinitionError("A revisão da definição é inválida.")
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise InvalidStrategyDefinitionError(
                    "Os timestamps da definição devem estar em UTC."
                )
        if self.updated_at < self.created_at:
            raise InvalidStrategyDefinitionError("A atualização antecede a criação da definição.")
        if self.state is StrategyDefinitionState.ACTIVE and self.archived_at is not None:
            raise InvalidStrategyDefinitionError("Uma definição ativa não pode estar arquivada.")
        if self.state is StrategyDefinitionState.ARCHIVED:
            archived_at = self.archived_at
            if archived_at is None:
                raise InvalidStrategyDefinitionError("Uma definição arquivada exige archived_at.")
            if (
                archived_at.tzinfo is None
                or archived_at.utcoffset() != timedelta(0)
                or archived_at < self.created_at
                or archived_at > self.updated_at
            ):
                raise InvalidStrategyDefinitionError("O archived_at da definição é inválido.")


class StrategyDefinitionRepository(Protocol):
    """Persistence boundary used by the versioned CRUD service."""

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        include_archived: bool,
    ) -> tuple[list[StrategyDefinition], int]: ...

    async def get(self, definition_id: UUID) -> StrategyDefinition | None: ...

    async def create(
        self,
        spec: StrategyDefinitionSpec,
        *,
        actor_id: UUID,
    ) -> StrategyDefinition: ...

    async def replace(
        self,
        definition_id: UUID,
        spec: StrategyDefinitionSpec,
        *,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition: ...

    async def archive(
        self,
        definition_id: UUID,
        *,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition: ...


class StrategyDefinitionService:
    """Validate plugin contracts before every CRUD and runtime operation."""

    def __init__(
        self,
        repository: StrategyDefinitionRepository,
        *,
        registry: StrategyPluginRegistry | None = None,
        available_indicators: tuple[IndicatorCapability, ...] = (),
    ) -> None:
        self._repository = repository
        self._registry = registry or StrategyPluginRegistry.builtins()
        self._available_indicators = available_indicators

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        include_archived: bool = False,
    ) -> tuple[list[StrategyDefinition], int]:
        """List bounded definitions and reject incompatible persisted records."""

        _validate_pagination(limit, offset)
        definitions, total = await self._repository.list(
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )
        for definition in definitions:
            self._validate_persisted(definition)
        return definitions, total

    async def get(self, definition_id: UUID) -> StrategyDefinition:
        """Return one validated persisted strategy definition."""

        definition = await self._repository.get(definition_id)
        if definition is None:
            raise StrategyDefinitionNotFoundError()
        self._validate_persisted(definition)
        return definition

    async def create(
        self,
        *,
        display_name: str,
        plugin_name: str,
        plugin_version: str,
        parameters: RawStrategyParameters,
        actor_id: UUID,
    ) -> StrategyDefinition:
        """Create a definition only after canonical plugin validation."""

        spec = self._build_spec(display_name, plugin_name, plugin_version, parameters)
        return await self._repository.create(spec, actor_id=actor_id)

    async def replace(
        self,
        definition_id: UUID,
        *,
        display_name: str,
        plugin_name: str,
        plugin_version: str,
        parameters: RawStrategyParameters,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition:
        """Replace mutable fields with optimistic concurrency."""

        _validate_expected_revision(expected_revision)
        current = await self.get(definition_id)
        if current.state is StrategyDefinitionState.ARCHIVED:
            raise StrategyDefinitionArchivedError()
        spec = self._build_spec(display_name, plugin_name, plugin_version, parameters)
        return await self._repository.replace(
            definition_id,
            spec,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )

    async def archive(
        self,
        definition_id: UUID,
        *,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition:
        """Archive a definition through a one-way revisioned transition."""

        _validate_expected_revision(expected_revision)
        current = await self.get(definition_id)
        if current.state is StrategyDefinitionState.ARCHIVED:
            raise StrategyDefinitionArchivedError()
        return await self._repository.archive(
            definition_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )

    async def build(self, definition_id: UUID) -> BacktestStrategy:
        """Build fresh strategy state from a validated active definition."""

        definition = await self.get(definition_id)
        if definition.state is StrategyDefinitionState.ARCHIVED:
            raise StrategyDefinitionArchivedError()
        plugin = self._resolve_definition_plugin(definition)
        parameters = decode_strategy_parameters(plugin.descriptor, definition.spec.parameters)
        return self._registry.build(
            definition.spec.plugin_name,
            definition.spec.plugin_version,
            dict(parameters),
            available_indicators=self._available_indicators,
        )

    def _build_spec(
        self,
        display_name: str,
        plugin_name: str,
        plugin_version: str,
        parameters: RawStrategyParameters,
    ) -> StrategyDefinitionSpec:
        try:
            plugin = self._registry.resolve(plugin_name, plugin_version)
            strategy = self._registry.build(
                plugin.descriptor.name,
                plugin.descriptor.version,
                parameters,
                available_indicators=self._available_indicators,
            )
            document = encode_strategy_parameters(
                plugin.descriptor,
                strategy.descriptor.parameters,
            )
        except ValueError as error:
            raise InvalidStrategyDefinitionError() from error
        return StrategyDefinitionSpec(
            display_name=display_name,
            plugin_name=plugin.descriptor.name,
            plugin_version=plugin.descriptor.version,
            plugin_schema_version=plugin.descriptor.schema_version,
            lifecycle_version=plugin.descriptor.lifecycle_version,
            parameters=document,
            parameters_checksum=strategy_parameter_checksum(document),
        )

    def _validate_persisted(self, definition: StrategyDefinition) -> None:
        plugin = self._resolve_definition_plugin(definition)
        try:
            parameters = decode_strategy_parameters(
                plugin.descriptor,
                definition.spec.parameters,
            )
            self._registry.build(
                plugin.descriptor.name,
                plugin.descriptor.version,
                dict(parameters),
                available_indicators=self._available_indicators,
            )
        except ValueError as error:
            raise StrategyDefinitionCompatibilityError() from error

    def _resolve_definition_plugin(self, definition: StrategyDefinition) -> StrategyPlugin:
        try:
            plugin = self._registry.resolve(
                definition.spec.plugin_name,
                definition.spec.plugin_version,
            )
        except ValueError as error:
            raise StrategyDefinitionCompatibilityError() from error
        descriptor = plugin.descriptor
        if (
            definition.spec.plugin_schema_version != descriptor.schema_version
            or definition.spec.lifecycle_version != descriptor.lifecycle_version
        ):
            raise StrategyDefinitionCompatibilityError()
        return plugin


def encode_strategy_parameters(
    descriptor: StrategyPluginDescriptor,
    parameters: StrategyParameters,
) -> StrategyParameterDocument:
    """Encode normalized parameters without losing Decimal/string identity."""

    specs = {item.name: item for item in descriptor.parameters}
    if tuple(name for name, _value in parameters) != tuple(sorted(specs)):
        raise InvalidStrategyDefinitionError("Os parâmetros normalizados estão incompletos.")
    document: list[StoredStrategyParameter] = []
    for name, value in parameters:
        spec = specs[name]
        stored: StoredParameterScalar
        if spec.kind is StrategyParameterKind.DECIMAL:
            if not isinstance(value, Decimal) or not value.is_finite():
                raise InvalidStrategyDefinitionError("O parâmetro Decimal normalizado é inválido.")
            stored = _decimal_text(value)
        elif value is None or isinstance(value, (bool, int, str)):
            stored = value
        else:
            raise InvalidStrategyDefinitionError(
                "O parâmetro normalizado possui um tipo persistível inválido."
            )
        document.append(StoredStrategyParameter(name, spec.kind, stored))
    return tuple(document)


def decode_strategy_parameters(
    descriptor: StrategyPluginDescriptor,
    document: StrategyParameterDocument,
) -> StrategyParameters:
    """Decode and revalidate one persisted parameter document."""

    normalized_document = _normalize_document(document)
    specs = {item.name: item for item in descriptor.parameters}
    if tuple(item.name for item in normalized_document) != tuple(sorted(specs)):
        raise StrategyDefinitionCompatibilityError()
    raw: dict[str, object] = {}
    for item in normalized_document:
        spec = specs[item.name]
        if item.kind is not spec.kind:
            raise StrategyDefinitionCompatibilityError()
        if item.kind is StrategyParameterKind.DECIMAL:
            if not isinstance(item.value, str):
                raise StrategyDefinitionCompatibilityError()
            try:
                value: StrategyParameterValue = Decimal(item.value)
            except InvalidOperation:
                raise StrategyDefinitionCompatibilityError() from None
        else:
            value = item.value
        raw[item.name] = value
    try:
        return descriptor.normalize_parameters(raw)
    except ValueError as error:
        raise StrategyDefinitionCompatibilityError() from error


def strategy_parameter_checksum(document: StrategyParameterDocument) -> str:
    """Return a stable SHA-256 over the lossless parameter document."""

    normalized = _normalize_document(document)
    payload = {item.name: item.canonical_payload() for item in normalized}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def strategy_parameter_document_to_json(
    document: StrategyParameterDocument,
) -> dict[str, dict[str, StoredParameterScalar]]:
    """Project the immutable document into a JSONB-ready mapping."""

    return {item.name: item.canonical_payload() for item in _normalize_document(document)}


def strategy_parameter_document_from_json(
    payload: Mapping[str, object],
) -> StrategyParameterDocument:
    """Parse a strict JSONB mapping without accepting extra entry fields."""

    document: list[StoredStrategyParameter] = []
    for raw_name, raw_entry in payload.items():
        if not isinstance(raw_name, str) or not isinstance(raw_entry, Mapping):
            raise InvalidStrategyDefinitionError("O documento JSON de parâmetros é inválido.")
        if set(raw_entry) != {"kind", "value"}:
            raise InvalidStrategyDefinitionError("A entrada JSON de parâmetro é inválida.")
        raw_kind = raw_entry["kind"]
        if not isinstance(raw_kind, str):
            raise InvalidStrategyDefinitionError("O tipo JSON do parâmetro é inválido.")
        try:
            kind = StrategyParameterKind(raw_kind)
        except ValueError:
            raise InvalidStrategyDefinitionError(
                "O tipo JSON do parâmetro é desconhecido."
            ) from None
        value = raw_entry["value"]
        if not (value is None or isinstance(value, (bool, int, str))) or isinstance(value, float):
            raise InvalidStrategyDefinitionError("O valor JSON do parâmetro é inválido.")
        document.append(StoredStrategyParameter(raw_name, kind, value))
    return _normalize_document(tuple(document))


def _normalize_document(document: StrategyParameterDocument) -> StrategyParameterDocument:
    names = [item.name for item in document]
    if len(names) != len(set(names)):
        raise InvalidStrategyDefinitionError("Os parâmetros persistidos devem ser únicos.")
    return tuple(sorted(document, key=lambda item: item.name))


def _validate_stored_scalar(kind: StrategyParameterKind, value: StoredParameterScalar) -> None:
    if kind is StrategyParameterKind.BOOLEAN:
        valid = isinstance(value, bool)
    elif kind is StrategyParameterKind.INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif kind in {StrategyParameterKind.DECIMAL, StrategyParameterKind.STRING}:
        valid = isinstance(value, str) and bool(value)
    else:
        valid = False
    if not valid:
        raise InvalidStrategyDefinitionError(
            "O valor persistido não corresponde ao tipo declarado."
        )


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise InvalidStrategyDefinitionError("O parâmetro Decimal deve ser finito.")
    if value == 0:
        return "0"

    sign, raw_digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise InvalidStrategyDefinitionError("O parâmetro Decimal deve ser finito.")

    digits = list(raw_digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1

    point_position = len(digits) + exponent
    sign_length = 1 if sign else 0
    if exponent >= 0:
        output_length = sign_length + len(digits) + exponent
    elif point_position > 0:
        output_length = sign_length + len(digits) + 1
    else:
        output_length = sign_length + 2 + (-point_position) + len(digits)
    if output_length > 128:
        raise InvalidStrategyDefinitionError(
            "O parâmetro Decimal excede o limite de 128 caracteres."
        )

    coefficient = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        unsigned = coefficient + ("0" * exponent)
    elif point_position > 0:
        unsigned = coefficient[:point_position] + "." + coefficient[point_position:]
    else:
        unsigned = "0." + ("0" * (-point_position)) + coefficient
    return ("-" if sign else "") + unsigned


def _validate_pagination(limit: int, offset: int) -> None:
    if isinstance(limit, bool) or limit < 1 or limit > 100:
        raise InvalidStrategyDefinitionError("O limite deve estar entre 1 e 100.")
    if isinstance(offset, bool) or offset < 0:
        raise InvalidStrategyDefinitionError("O deslocamento não pode ser negativo.")


def _validate_expected_revision(expected_revision: int) -> None:
    if isinstance(expected_revision, bool) or expected_revision < 1:
        raise InvalidStrategyDefinitionError("A revisão esperada deve ser positiva.")
