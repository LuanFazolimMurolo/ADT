"""Administrative strategy-definition request and response contracts."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.api.schemas.common import ApiSchema, JsonValue
from app.api.schemas.pagination import PageMeta
from app.strategies.definitions import StrategyDefinition, StrategyDefinitionState
from app.strategies.domain import RawStrategyParameters, StrategyParameterKind

_SAFE_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SAFE_PARAMETER_NAME = re.compile(_SAFE_TOKEN_PATTERN)
_DECIMAL_TEXT = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")
SafeStrategyToken = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=_SAFE_TOKEN_PATTERN),
]
StrategyDisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]


class StrategyParameterInput(ApiSchema):
    """One explicitly typed strategy parameter at the HTTP boundary."""

    kind: StrategyParameterKind
    value: JsonValue

    @model_validator(mode="after")
    def validate_exact_type(self) -> Self:
        """Reject JSON coercion and ambiguous Decimal/string values."""

        value = self.value
        if self.kind is StrategyParameterKind.BOOLEAN:
            valid = isinstance(value, bool)
        elif self.kind is StrategyParameterKind.INTEGER:
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif self.kind is StrategyParameterKind.DECIMAL:
            valid = False
            if (
                isinstance(value, str)
                and len(value) <= 128
                and _DECIMAL_TEXT.fullmatch(value) is not None
            ):
                try:
                    valid = Decimal(value).is_finite()
                except InvalidOperation:
                    valid = False
        else:
            valid = isinstance(value, str)
        if not valid:
            raise ValueError("Parameter value does not match its declared kind.")
        return self

    def to_domain(self) -> object:
        """Convert Decimal text while preserving every other exact JSON scalar."""

        if self.kind is StrategyParameterKind.DECIMAL:
            return Decimal(str(self.value))
        return self.value


class StrategyDefinitionWriteRequest(ApiSchema):
    """Fields shared by create and full replacement operations."""

    display_name: StrategyDisplayName
    plugin_name: SafeStrategyToken
    plugin_version: SafeStrategyToken
    parameters: dict[str, StrategyParameterInput] = Field(
        default_factory=dict,
        max_length=64,
    )

    @field_validator("parameters")
    @classmethod
    def validate_parameter_names(
        cls,
        parameters: dict[str, StrategyParameterInput],
    ) -> dict[str, StrategyParameterInput]:
        """Bound parameter identifiers before plugin-specific validation."""

        if any(_SAFE_PARAMETER_NAME.fullmatch(name) is None for name in parameters):
            raise ValueError("Parameter names must be safe identifiers.")
        return parameters

    def raw_parameters(self) -> RawStrategyParameters:
        """Build the exact untrusted mapping consumed by the domain service."""

        return {name: parameter.to_domain() for name, parameter in self.parameters.items()}


class StrategyDefinitionCreateRequest(StrategyDefinitionWriteRequest):
    """Create one reusable active strategy definition."""


class StrategyDefinitionReplaceRequest(StrategyDefinitionWriteRequest):
    """Replace mutable fields using optimistic concurrency."""

    expected_revision: int = Field(ge=1)


class StrategyDefinitionArchiveRequest(ApiSchema):
    """Archive one definition using optimistic concurrency."""

    expected_revision: int = Field(ge=1)


class StrategyParameterResponse(ApiSchema):
    """Lossless persisted parameter representation."""

    kind: StrategyParameterKind
    value: JsonValue


class StrategyDefinitionResponse(ApiSchema):
    """Administrative projection of one validated strategy definition."""

    id: UUID
    display_name: str
    plugin_name: str
    plugin_version: str
    plugin_schema_version: int
    lifecycle_version: int
    parameters: dict[str, StrategyParameterResponse]
    parameters_checksum: str
    state: StrategyDefinitionState
    revision: int
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None

    @classmethod
    def from_domain(cls, definition: StrategyDefinition) -> Self:
        """Project a definition without weakening its typed parameter document."""

        return cls(
            id=definition.id,
            display_name=definition.spec.display_name,
            plugin_name=definition.spec.plugin_name,
            plugin_version=definition.spec.plugin_version,
            plugin_schema_version=definition.spec.plugin_schema_version,
            lifecycle_version=definition.spec.lifecycle_version,
            parameters={
                item.name: StrategyParameterResponse(kind=item.kind, value=item.value)
                for item in definition.spec.parameters
            },
            parameters_checksum=definition.spec.parameters_checksum,
            state=definition.state,
            revision=definition.revision,
            created_by=definition.created_by,
            updated_by=definition.updated_by,
            created_at=definition.created_at,
            updated_at=definition.updated_at,
            archived_at=definition.archived_at,
        )


class StrategyDefinitionListResponse(ApiSchema):
    """Bounded administrative strategy-definition page."""

    items: list[StrategyDefinitionResponse]
    pagination: PageMeta
