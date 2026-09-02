"""Administrator HTTP contracts for operational paper-session materializations."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from app.api.schemas.common import ApiSchema
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationMandateBinding,
    OperationalPaperSessionMaterializationProfileBinding,
    OperationalPaperSessionMaterializationState,
)


class OperationalPaperSessionMaterializationCreateRequest(ApiSchema):
    """Materialize or reconcile one authoritative capital authorization."""

    authorization_id: UUID


class OperationalPaperSessionMaterializationAuthorizationBindingResponse(ApiSchema):
    """Exact capital-authorization evidence captured by a materialization."""

    authorization_id: UUID
    authorization_checksum: str

    @classmethod
    def from_domain(
        cls,
        binding: OperationalPaperSessionMaterializationAuthorizationBinding,
    ) -> Self:
        return cls(
            authorization_id=binding.authorization_id,
            authorization_checksum=binding.authorization_checksum,
        )


class OperationalPaperSessionMaterializationProfileBindingResponse(ApiSchema):
    """Exact approved paper-profile evidence captured by a materialization."""

    profile_id: UUID
    approved_revision: int
    specification_checksum: str

    @classmethod
    def from_domain(
        cls,
        binding: OperationalPaperSessionMaterializationProfileBinding,
    ) -> Self:
        return cls(
            profile_id=binding.profile_id,
            approved_revision=binding.approved_revision,
            specification_checksum=binding.specification_checksum,
        )


class OperationalPaperSessionMaterializationMandateBindingResponse(ApiSchema):
    """Exact approved mandate evidence captured by a materialization."""

    mandate_id: UUID
    approved_revision: int
    specification_checksum: str

    @classmethod
    def from_domain(
        cls,
        binding: OperationalPaperSessionMaterializationMandateBinding,
    ) -> Self:
        return cls(
            mandate_id=binding.mandate_id,
            approved_revision=binding.approved_revision,
            specification_checksum=binding.specification_checksum,
        )


class OperationalPaperSessionMaterializationResponse(ApiSchema):
    """Auditable materialization provenance without executable config bytes."""

    materialization_id: UUID
    schema_version: int
    materialization_contract_version: int
    state: OperationalPaperSessionMaterializationState
    record_version: int
    authorization_binding: OperationalPaperSessionMaterializationAuthorizationBindingResponse
    profile_binding: OperationalPaperSessionMaterializationProfileBindingResponse
    mandate_binding: OperationalPaperSessionMaterializationMandateBindingResponse
    simulation_id: UUID
    config_checksum: str
    session_id: str
    materialization_checksum: str
    prepared_by: UUID
    prepared_at: datetime
    materialized_by: UUID | None
    materialized_at: datetime | None

    @classmethod
    def from_domain(
        cls,
        materialization: OperationalPaperSessionMaterialization,
    ) -> Self:
        return cls(
            materialization_id=materialization.materialization_id,
            schema_version=materialization.schema_version,
            materialization_contract_version=(materialization.materialization_contract_version),
            state=materialization.state,
            record_version=materialization.record_version,
            authorization_binding=(
                OperationalPaperSessionMaterializationAuthorizationBindingResponse.from_domain(
                    materialization.authorization_binding
                )
            ),
            profile_binding=(
                OperationalPaperSessionMaterializationProfileBindingResponse.from_domain(
                    materialization.profile_binding
                )
            ),
            mandate_binding=(
                OperationalPaperSessionMaterializationMandateBindingResponse.from_domain(
                    materialization.mandate_binding
                )
            ),
            simulation_id=materialization.simulation_id,
            config_checksum=materialization.config_checksum,
            session_id=materialization.session_id,
            materialization_checksum=materialization.materialization_checksum,
            prepared_by=materialization.prepared_by,
            prepared_at=materialization.prepared_at,
            materialized_by=materialization.materialized_by,
            materialized_at=materialization.materialized_at,
        )


class OperationalPaperSessionMaterializationListResponse(ApiSchema):
    """Bounded materialization page with the independent filtered total."""

    items: list[OperationalPaperSessionMaterializationResponse]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_domain(
        cls,
        items: list[OperationalPaperSessionMaterialization],
        *,
        limit: int,
        offset: int,
        total: int,
    ) -> Self:
        return cls(
            items=[
                OperationalPaperSessionMaterializationResponse.from_domain(item) for item in items
            ],
            limit=limit,
            offset=offset,
            total=total,
        )
