"""Administrator HTTP contracts for operational market-data collector control."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field

import app.operational_market_data_collectors as collectors
from app.api.schemas.common import ApiSchema

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class OperationalMarketDataCollectorTargetRequest(ApiSchema):
    """One exact physical target requested for a collector epoch."""

    symbol: str = Field(
        strict=True,
        min_length=1,
    )
    timeframe: str = Field(
        strict=True,
        min_length=1,
    )
    bootstrap_candles: int = Field(
        strict=True,
        ge=1,
    )

    def to_domain(
        self,
    ) -> collectors.OperationalMarketDataCollectorTarget:
        return collectors.OperationalMarketDataCollectorTarget(
            symbol=self.symbol,
            timeframe=self.timeframe,
            bootstrap_candles=self.bootstrap_candles,
        )


class OperationalMarketDataCollectorSpecificationRequest(ApiSchema):
    """Frozen collector target and cadence specification."""

    schema_version: int = Field(
        strict=True,
        ge=1,
    )
    collector_contract_version: int = Field(
        strict=True,
        ge=1,
    )
    scope: collectors.OperationalMarketDataCollectorScope
    targets: list[OperationalMarketDataCollectorTargetRequest] = Field(
        min_length=1,
    )
    interval_seconds: int = Field(
        strict=True,
        ge=1,
    )
    overlap_candles: int = Field(
        strict=True,
        ge=0,
    )

    def to_domain(
        self,
    ) -> collectors.OperationalMarketDataCollectorSpecification:
        return collectors.OperationalMarketDataCollectorSpecification(
            schema_version=self.schema_version,
            collector_contract_version=self.collector_contract_version,
            scope=self.scope,
            targets=tuple(target.to_domain() for target in self.targets),
            interval_seconds=self.interval_seconds,
            overlap_candles=self.overlap_candles,
        )


class OperationalMarketDataCollectorStartRequest(ApiSchema):
    """Create or replay one administrator-scoped collector START."""

    specification: OperationalMarketDataCollectorSpecificationRequest
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=(collectors.MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_IDEMPOTENCY_KEY_LENGTH),
        pattern=_IDEMPOTENCY_PATTERN,
    )


class OperationalMarketDataCollectorCommandRequest(ApiSchema):
    """Request one lifecycle command using optimistic concurrency."""

    epoch_checksum: str = Field(
        strict=True,
        pattern=_SHA256_PATTERN,
    )
    expected_record_version: int = Field(
        strict=True,
        ge=1,
    )
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=(collectors.MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_IDEMPOTENCY_KEY_LENGTH),
        pattern=_IDEMPOTENCY_PATTERN,
    )


class OperationalMarketDataCollectorTargetResponse(ApiSchema):
    """Sanitized frozen collector target."""

    symbol: str
    timeframe: str
    bootstrap_candles: int

    @classmethod
    def from_domain(
        cls,
        target: collectors.OperationalMarketDataCollectorTarget,
    ) -> Self:
        return cls(
            symbol=target.symbol,
            timeframe=target.timeframe,
            bootstrap_candles=target.bootstrap_candles,
        )


class OperationalMarketDataCollectorSpecificationResponse(ApiSchema):
    """Frozen collector specification exposed for administrator audit."""

    schema_version: int
    collector_contract_version: int
    scope: collectors.OperationalMarketDataCollectorScope
    targets: list[OperationalMarketDataCollectorTargetResponse]
    interval_seconds: int
    overlap_candles: int

    @classmethod
    def from_domain(
        cls,
        specification: collectors.OperationalMarketDataCollectorSpecification,
    ) -> Self:
        return cls(
            schema_version=specification.schema_version,
            collector_contract_version=(specification.collector_contract_version),
            scope=specification.scope,
            targets=[
                OperationalMarketDataCollectorTargetResponse.from_domain(target)
                for target in specification.targets
            ],
            interval_seconds=specification.interval_seconds,
            overlap_candles=specification.overlap_candles,
        )


class OperationalMarketDataCollectorLeaseResponse(ApiSchema):
    """Lease timing without internal worker identity."""

    claimed_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime

    @classmethod
    def from_domain(
        cls,
        claim: collectors.OperationalMarketDataCollectorWorkerClaim,
    ) -> Self:
        return cls(
            claimed_at=claim.claimed_at,
            heartbeat_at=claim.heartbeat_at,
            lease_expires_at=claim.lease_expires_at,
        )


class OperationalMarketDataCollectorFailureResponse(ApiSchema):
    """Closed collector failure evidence without arbitrary diagnostics."""

    code: collectors.OperationalMarketDataCollectorFailureCode
    failed_at: datetime

    @classmethod
    def from_domain(
        cls,
        failure: collectors.OperationalMarketDataCollectorFailure,
    ) -> Self:
        return cls(
            code=failure.code,
            failed_at=failure.failed_at,
        )


class OperationalMarketDataCollectorEpochResponse(ApiSchema):
    """Auditable epoch without replay secrets or worker identity."""

    epoch_id: UUID
    specification: OperationalMarketDataCollectorSpecificationResponse
    specification_checksum: str
    desired_state: collectors.OperationalMarketDataCollectorDesiredState
    observed_state: collectors.OperationalMarketDataCollectorObservedState
    record_version: int
    fencing_token: int
    epoch_checksum: str
    start_requested_by: UUID
    start_requested_at: datetime
    lease: OperationalMarketDataCollectorLeaseResponse | None
    failure: OperationalMarketDataCollectorFailureResponse | None
    terminal_at: datetime | None

    @classmethod
    def from_domain(
        cls,
        epoch: collectors.OperationalMarketDataCollectorEpoch,
    ) -> Self:
        return cls(
            epoch_id=epoch.epoch_id,
            specification=(
                OperationalMarketDataCollectorSpecificationResponse.from_domain(epoch.specification)
            ),
            specification_checksum=epoch.specification_checksum,
            desired_state=epoch.desired_state,
            observed_state=epoch.observed_state,
            record_version=epoch.record_version,
            fencing_token=epoch.fencing_token,
            epoch_checksum=epoch.epoch_checksum,
            start_requested_by=epoch.start_requested_by,
            start_requested_at=epoch.start_requested_at,
            lease=(
                None
                if epoch.worker_claim is None
                else OperationalMarketDataCollectorLeaseResponse.from_domain(epoch.worker_claim)
            ),
            failure=(
                None
                if epoch.failure is None
                else OperationalMarketDataCollectorFailureResponse.from_domain(epoch.failure)
            ),
            terminal_at=epoch.terminal_at,
        )


class OperationalMarketDataCollectorCommandResponse(ApiSchema):
    """Immutable administrator command without replay internals."""

    command_id: UUID
    command_contract_version: int
    epoch_id: UUID
    epoch_checksum: str
    command_type: collectors.OperationalMarketDataCollectorCommandType
    desired_state: collectors.OperationalMarketDataCollectorDesiredState
    expected_record_version: int | None
    resulting_record_version: int
    actor_id: UUID
    requested_at: datetime

    @classmethod
    def from_domain(
        cls,
        command: collectors.OperationalMarketDataCollectorCommand,
    ) -> Self:
        return cls(
            command_id=command.command_id,
            command_contract_version=command.command_contract_version,
            epoch_id=command.epoch_id,
            epoch_checksum=command.epoch_checksum,
            command_type=command.command_type,
            desired_state=command.desired_state,
            expected_record_version=command.expected_record_version,
            resulting_record_version=command.resulting_record_version,
            actor_id=command.actor_id,
            requested_at=command.requested_at,
        )


class OperationalMarketDataCollectorCommandListResponse(ApiSchema):
    """Bounded immutable collector-command history page."""

    items: list[OperationalMarketDataCollectorCommandResponse]
    limit: int
    offset: int

    @classmethod
    def from_domain(
        cls,
        items: list[collectors.OperationalMarketDataCollectorCommand],
        *,
        limit: int,
        offset: int,
    ) -> Self:
        return cls(
            items=[
                OperationalMarketDataCollectorCommandResponse.from_domain(item) for item in items
            ],
            limit=limit,
            offset=offset,
        )
