"""Administrator HTTP contracts for operational paper-session activation grants."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiSchema
from app.api.schemas.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationAuthorizationBindingResponse,
    OperationalPaperSessionMaterializationMandateBindingResponse,
    OperationalPaperSessionMaterializationProfileBindingResponse,
)
from app.operational_paper_session_activations import (
    MAX_OPERATIONAL_PAPER_SESSION_ACTIVATION_IDEMPOTENCY_KEY_LENGTH,
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationState,
)


class OperationalPaperSessionActivationAuthorizeRequest(ApiSchema):
    """Authorize or replay one administrator-scoped materialization intent."""

    materialization_id: UUID
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=MAX_OPERATIONAL_PAPER_SESSION_ACTIVATION_IDEMPOTENCY_KEY_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class OperationalPaperSessionActivationRevokeRequest(ApiSchema):
    """Revoke one grant using optimistic concurrency."""

    expected_record_version: int = Field(strict=True, ge=1)


class OperationalPaperSessionActivationResponse(ApiSchema):
    """Auditable activation grant without persistence-only replay internals."""

    activation_id: UUID
    schema_version: int
    activation_contract_version: int
    state: OperationalPaperSessionActivationState
    record_version: int
    materialization_id: UUID
    materialization_checksum: str
    authorization_binding: OperationalPaperSessionMaterializationAuthorizationBindingResponse
    profile_binding: OperationalPaperSessionMaterializationProfileBindingResponse
    mandate_binding: OperationalPaperSessionMaterializationMandateBindingResponse
    simulation_id: UUID
    session_id: str
    config_checksum: str
    activation_checksum: str
    authorized_by: UUID
    authorized_at: datetime
    revoked_by: UUID | None
    revoked_at: datetime | None

    @classmethod
    def from_domain(cls, activation: OperationalPaperSessionActivation) -> Self:
        return cls(
            activation_id=activation.activation_id,
            schema_version=activation.schema_version,
            activation_contract_version=activation.activation_contract_version,
            state=activation.state,
            record_version=activation.record_version,
            materialization_id=activation.materialization_id,
            materialization_checksum=activation.materialization_checksum,
            authorization_binding=(
                OperationalPaperSessionMaterializationAuthorizationBindingResponse.from_domain(
                    activation.authorization_binding
                )
            ),
            profile_binding=(
                OperationalPaperSessionMaterializationProfileBindingResponse.from_domain(
                    activation.profile_binding
                )
            ),
            mandate_binding=(
                OperationalPaperSessionMaterializationMandateBindingResponse.from_domain(
                    activation.mandate_binding
                )
            ),
            simulation_id=activation.simulation_id,
            session_id=activation.session_id,
            config_checksum=activation.config_checksum,
            activation_checksum=activation.activation_checksum,
            authorized_by=activation.authorized_by,
            authorized_at=activation.authorized_at,
            revoked_by=activation.revoked_by,
            revoked_at=activation.revoked_at,
        )


class OperationalPaperSessionActivationListResponse(ApiSchema):
    """Bounded activation page with the independent filtered total."""

    items: list[OperationalPaperSessionActivationResponse]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_domain(
        cls,
        items: list[OperationalPaperSessionActivation],
        *,
        limit: int,
        offset: int,
        total: int,
    ) -> Self:
        return cls(
            items=[OperationalPaperSessionActivationResponse.from_domain(item) for item in items],
            limit=limit,
            offset=offset,
            total=total,
        )
