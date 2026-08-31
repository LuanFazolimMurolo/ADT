"""Administrator HTTP contracts for operational paper-capital authorizations."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import (
    ApiSchema,
    FinancialDecimal,
    PositiveFinancialDecimalStringInput,
)
from app.operational_paper_capital_authorizations import (
    MAX_OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_IDEMPOTENCY_KEY_LENGTH,
    OperationalPaperCapitalAuthorization,
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalAuthorizationState,
)

_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class OperationalPaperCapitalAuthorizationProfileBindingRequest(ApiSchema):
    """Exact approved paper-profile binding supplied by an administrator."""

    profile_id: UUID
    approved_revision: int = Field(strict=True, ge=1)
    specification_checksum: str = Field(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=_SHA256_PATTERN,
    )

    def to_domain(self) -> OperationalPaperCapitalAuthorizationProfileBinding:
        return OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=self.profile_id,
            approved_revision=self.approved_revision,
            specification_checksum=self.specification_checksum,
        )


class OperationalPaperCapitalAuthorizationIntentRequest(ApiSchema):
    """One paper-capital authorization intent."""

    profile_binding: OperationalPaperCapitalAuthorizationProfileBindingRequest
    simulation_id: UUID
    quote_asset: str = Field(strict=True)
    authorized_capital: PositiveFinancialDecimalStringInput

    def to_domain(self) -> OperationalPaperCapitalAuthorizationCreateIntent:
        return OperationalPaperCapitalAuthorizationCreateIntent(
            profile_binding=self.profile_binding.to_domain(),
            simulation_id=self.simulation_id,
            quote_asset=self.quote_asset,
            authorized_capital=self.authorized_capital,
        )


class OperationalPaperCapitalAuthorizationCreateRequest(ApiSchema):
    """Create or replay one administrator-scoped authorization intent."""

    intent: OperationalPaperCapitalAuthorizationIntentRequest
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=MAX_OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_IDEMPOTENCY_KEY_LENGTH,
        pattern=_IDEMPOTENCY_KEY_PATTERN,
    )


class OperationalPaperCapitalAuthorizationRevokeRequest(ApiSchema):
    """Revoke one authorization using optimistic concurrency."""

    expected_record_version: int = Field(strict=True, ge=1)


class OperationalPaperCapitalAuthorizationProfileBindingResponse(ApiSchema):
    """Exact approved paper-profile authority captured by an authorization."""

    profile_id: UUID
    approved_revision: int
    specification_checksum: str

    @classmethod
    def from_domain(
        cls,
        binding: OperationalPaperCapitalAuthorizationProfileBinding,
    ) -> Self:
        return cls(
            profile_id=binding.profile_id,
            approved_revision=binding.approved_revision,
            specification_checksum=binding.specification_checksum,
        )


class OperationalPaperCapitalAuthorizationResponse(ApiSchema):
    """Auditable paper-capital authorization without persistence-only replay internals."""

    authorization_id: UUID
    schema_version: int
    state: OperationalPaperCapitalAuthorizationState
    record_version: int
    profile_binding: OperationalPaperCapitalAuthorizationProfileBindingResponse
    simulation_id: UUID
    quote_asset: str
    authorized_capital: FinancialDecimal
    authorization_checksum: str
    created_by: UUID
    created_at: datetime
    revoked_by: UUID | None
    revoked_at: datetime | None

    @classmethod
    def from_domain(
        cls,
        authorization: OperationalPaperCapitalAuthorization,
    ) -> Self:
        return cls(
            authorization_id=authorization.authorization_id,
            schema_version=authorization.schema_version,
            state=authorization.state,
            record_version=authorization.record_version,
            profile_binding=OperationalPaperCapitalAuthorizationProfileBindingResponse.from_domain(
                authorization.profile_binding
            ),
            simulation_id=authorization.simulation_id,
            quote_asset=authorization.quote_asset,
            authorized_capital=authorization.authorized_capital,
            authorization_checksum=authorization.authorization_checksum,
            created_by=authorization.created_by,
            created_at=authorization.created_at,
            revoked_by=authorization.revoked_by,
            revoked_at=authorization.revoked_at,
        )


class OperationalPaperCapitalAuthorizationListResponse(ApiSchema):
    """Bounded authorization page with the independent filtered total."""

    items: list[OperationalPaperCapitalAuthorizationResponse]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_domain(
        cls,
        items: list[OperationalPaperCapitalAuthorization],
        *,
        limit: int,
        offset: int,
        total: int,
    ) -> Self:
        return cls(
            items=[
                OperationalPaperCapitalAuthorizationResponse.from_domain(item) for item in items
            ],
            limit=limit,
            offset=offset,
            total=total,
        )
