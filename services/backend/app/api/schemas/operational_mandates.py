"""Administrative operational-mandate HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiSchema
from app.domain.errors import DomainError
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.operational_mandates import (
    MAX_OPERATIONAL_MANDATE_IDEMPOTENCY_KEY_LENGTH,
    MAX_OPERATIONAL_MANDATE_INSTRUMENTS,
    OperationalMandate,
    OperationalMandateInstrument,
    OperationalMandateRevision,
    OperationalMandateSpecification,
    OperationalMandateState,
)
from app.operational_mandates.errors import InvalidOperationalMandateSpecificationError

_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class OperationalMandateInstrumentRequest(ApiSchema):
    """One canonical instrument accepted at the administrator boundary."""

    exchange: Exchange
    market_type: MarketType
    base_asset: str = Field(strict=True)
    quote_asset: str = Field(strict=True)

    def to_domain(self) -> OperationalMandateInstrument:
        """Build the existing canonical instrument domain contract."""

        try:
            pair = TradingPair(self.base_asset, self.quote_asset)
        except DomainError:
            raise InvalidOperationalMandateSpecificationError() from None
        return OperationalMandateInstrument(
            exchange=self.exchange,
            market_type=self.market_type,
            pair=pair,
        )


class OperationalMandateSpecificationRequest(ApiSchema):
    """One complete revision specification supplied by an administrator."""

    schema_version: int = Field(strict=True, ge=1, le=1)
    name: str = Field(strict=True)
    description: str = Field(strict=True)
    instruments: list[OperationalMandateInstrumentRequest] = Field(
        min_length=1,
        max_length=MAX_OPERATIONAL_MANDATE_INSTRUMENTS,
    )

    def to_domain(self) -> OperationalMandateSpecification:
        """Delegate normalization and business validation to the domain."""

        return OperationalMandateSpecification(
            schema_version=self.schema_version,
            name=self.name,
            description=self.description,
            instruments=tuple(item.to_domain() for item in self.instruments),
        )


class OperationalMandateCreateRequest(ApiSchema):
    """Create or replay one actor-scoped draft intent."""

    specification: OperationalMandateSpecificationRequest
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=MAX_OPERATIONAL_MANDATE_IDEMPOTENCY_KEY_LENGTH,
        pattern=_IDEMPOTENCY_KEY_PATTERN,
    )


class OperationalMandateReplaceRequest(ApiSchema):
    """Replace one draft specification using both concurrency tokens."""

    specification: OperationalMandateSpecificationRequest
    expected_revision: int = Field(strict=True, ge=1)
    expected_record_version: int = Field(strict=True, ge=1)


class OperationalMandateApproveRequest(ApiSchema):
    """Approve one exact draft revision, checksum, and aggregate version."""

    expected_revision: int = Field(strict=True, ge=1)
    expected_checksum: str = Field(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=_SHA256_PATTERN,
    )
    expected_record_version: int = Field(strict=True, ge=1)


class OperationalMandateArchiveRequest(ApiSchema):
    """Archive one mandate using optimistic concurrency."""

    expected_record_version: int = Field(strict=True, ge=1)


class OperationalMandateInstrumentResponse(ApiSchema):
    """Canonical instrument identity without adapter metadata."""

    exchange: Exchange
    market_type: MarketType
    base_asset: str
    quote_asset: str

    @classmethod
    def from_domain(cls, instrument: OperationalMandateInstrument) -> Self:
        return cls(
            exchange=instrument.exchange,
            market_type=instrument.market_type,
            base_asset=instrument.pair.base,
            quote_asset=instrument.pair.quote,
        )


class OperationalMandateSpecificationResponse(ApiSchema):
    """Canonical persisted specification safe for administrator review."""

    schema_version: int
    name: str
    description: str
    instruments: list[OperationalMandateInstrumentResponse]

    @classmethod
    def from_domain(cls, specification: OperationalMandateSpecification) -> Self:
        return cls(
            schema_version=specification.schema_version,
            name=specification.name,
            description=specification.description,
            instruments=[
                OperationalMandateInstrumentResponse.from_domain(instrument)
                for instrument in specification.instruments
            ],
        )


class OperationalMandateResponse(ApiSchema):
    """Auditable aggregate without persistence-only idempotency internals."""

    mandate_id: UUID
    state: OperationalMandateState
    current_revision: int
    record_version: int
    approved_revision: int | None
    approved_checksum: str | None
    created_by: UUID
    created_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    archived_by: UUID | None
    archived_at: datetime | None

    @classmethod
    def from_domain(cls, mandate: OperationalMandate) -> Self:
        return cls(
            mandate_id=mandate.mandate_id,
            state=mandate.state,
            current_revision=mandate.current_revision,
            record_version=mandate.record_version,
            approved_revision=mandate.approved_revision,
            approved_checksum=mandate.approved_checksum,
            created_by=mandate.created_by,
            created_at=mandate.created_at,
            approved_by=mandate.approved_by,
            approved_at=mandate.approved_at,
            archived_by=mandate.archived_by,
            archived_at=mandate.archived_at,
        )


class OperationalMandateRevisionResponse(ApiSchema):
    """One immutable specification revision and its audit identity."""

    mandate_id: UUID
    revision: int
    specification: OperationalMandateSpecificationResponse
    specification_checksum: str
    created_by: UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, revision: OperationalMandateRevision) -> Self:
        return cls(
            mandate_id=revision.mandate_id,
            revision=revision.revision,
            specification=OperationalMandateSpecificationResponse.from_domain(
                revision.specification
            ),
            specification_checksum=revision.specification_checksum,
            created_by=revision.created_by,
            created_at=revision.created_at,
        )


class OperationalMandateCurrentResponse(ApiSchema):
    """One aggregate paired with its exact current immutable revision."""

    mandate: OperationalMandateResponse
    revision: OperationalMandateRevisionResponse

    @classmethod
    def from_domain(
        cls,
        current: tuple[OperationalMandate, OperationalMandateRevision],
    ) -> Self:
        mandate, revision = current
        return cls(
            mandate=OperationalMandateResponse.from_domain(mandate),
            revision=OperationalMandateRevisionResponse.from_domain(revision),
        )


class OperationalMandateListResponse(ApiSchema):
    """Bounded current-mandate page with the independent filtered total."""

    items: list[OperationalMandateCurrentResponse]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_domain(
        cls,
        items: list[tuple[OperationalMandate, OperationalMandateRevision]],
        *,
        limit: int,
        offset: int,
        total: int,
    ) -> Self:
        return cls(
            items=[OperationalMandateCurrentResponse.from_domain(item) for item in items],
            limit=limit,
            offset=offset,
            total=total,
        )


class OperationalMandateRevisionListResponse(ApiSchema):
    """Bounded immutable revision-history page with its independent total."""

    items: list[OperationalMandateRevisionResponse]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_domain(
        cls,
        items: list[OperationalMandateRevision],
        *,
        limit: int,
        offset: int,
        total: int,
    ) -> Self:
        return cls(
            items=[OperationalMandateRevisionResponse.from_domain(item) for item in items],
            limit=limit,
            offset=offset,
            total=total,
        )
