"""Administrator HTTP contracts for operational paper-session run control."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field

import app.operational_paper_session_runs as runs
from app.api.schemas.common import ApiSchema
from app.api.schemas.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationAuthorizationBindingResponse,
    OperationalPaperSessionMaterializationMandateBindingResponse,
    OperationalPaperSessionMaterializationProfileBindingResponse,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class OperationalPaperSessionRunStartRequest(ApiSchema):
    """Create or replay one administrator-scoped START intent."""

    activation_id: UUID
    activation_checksum: str = Field(
        strict=True,
        pattern=_SHA256_PATTERN,
    )
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=(runs.MAX_OPERATIONAL_PAPER_SESSION_RUN_IDEMPOTENCY_KEY_LENGTH),
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class OperationalPaperSessionRunCommandRequest(ApiSchema):
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
        max_length=(runs.MAX_OPERATIONAL_PAPER_SESSION_RUN_IDEMPOTENCY_KEY_LENGTH),
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class OperationalPaperSessionRunLeaseResponse(ApiSchema):
    """Worker lease timing without internal worker identity."""

    claimed_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime

    @classmethod
    def from_domain(
        cls,
        claim: runs.OperationalPaperSessionRunWorkerClaim,
    ) -> Self:
        return cls(
            claimed_at=claim.claimed_at,
            heartbeat_at=claim.heartbeat_at,
            lease_expires_at=claim.lease_expires_at,
        )


class OperationalPaperSessionRunFailureResponse(ApiSchema):
    """Closed run failure evidence without arbitrary diagnostics."""

    code: runs.OperationalPaperSessionRunFailureCode
    failed_at: datetime

    @classmethod
    def from_domain(
        cls,
        failure: runs.OperationalPaperSessionRunFailure,
    ) -> Self:
        return cls(
            code=failure.code,
            failed_at=failure.failed_at,
        )


class OperationalPaperSessionRunEpochResponse(ApiSchema):
    """Auditable run epoch without replay secrets or worker identity."""

    epoch_id: UUID
    schema_version: int
    run_contract_version: int
    desired_state: runs.OperationalPaperSessionRunDesiredState
    observed_state: runs.OperationalPaperSessionRunObservedState
    record_version: int
    fencing_token: int

    activation_id: UUID
    activation_checksum: str
    materialization_id: UUID
    materialization_checksum: str

    authorization_binding: OperationalPaperSessionMaterializationAuthorizationBindingResponse
    profile_binding: OperationalPaperSessionMaterializationProfileBindingResponse
    mandate_binding: OperationalPaperSessionMaterializationMandateBindingResponse

    simulation_id: UUID
    session_id: str
    config_checksum: str
    epoch_checksum: str

    start_requested_by: UUID
    start_requested_at: datetime

    lease: OperationalPaperSessionRunLeaseResponse | None
    failure: OperationalPaperSessionRunFailureResponse | None
    terminal_at: datetime | None

    @classmethod
    def from_domain(
        cls,
        epoch: runs.OperationalPaperSessionRunEpoch,
    ) -> Self:
        return cls(
            epoch_id=epoch.epoch_id,
            schema_version=epoch.schema_version,
            run_contract_version=epoch.run_contract_version,
            desired_state=epoch.desired_state,
            observed_state=epoch.observed_state,
            record_version=epoch.record_version,
            fencing_token=epoch.fencing_token,
            activation_id=epoch.activation_id,
            activation_checksum=epoch.activation_checksum,
            materialization_id=epoch.materialization_id,
            materialization_checksum=epoch.materialization_checksum,
            authorization_binding=(
                OperationalPaperSessionMaterializationAuthorizationBindingResponse.from_domain(
                    epoch.authorization_binding
                )
            ),
            profile_binding=(
                OperationalPaperSessionMaterializationProfileBindingResponse.from_domain(
                    epoch.profile_binding
                )
            ),
            mandate_binding=(
                OperationalPaperSessionMaterializationMandateBindingResponse.from_domain(
                    epoch.mandate_binding
                )
            ),
            simulation_id=epoch.simulation_id,
            session_id=epoch.session_id,
            config_checksum=epoch.config_checksum,
            epoch_checksum=epoch.epoch_checksum,
            start_requested_by=epoch.start_requested_by,
            start_requested_at=epoch.start_requested_at,
            lease=(
                None
                if epoch.worker_claim is None
                else OperationalPaperSessionRunLeaseResponse.from_domain(epoch.worker_claim)
            ),
            failure=(
                None
                if epoch.failure is None
                else OperationalPaperSessionRunFailureResponse.from_domain(epoch.failure)
            ),
            terminal_at=epoch.terminal_at,
        )


class OperationalPaperSessionRunCommandResponse(ApiSchema):
    """Immutable administrator command without replay internals."""

    command_id: UUID
    command_contract_version: int
    epoch_id: UUID
    epoch_checksum: str
    command_type: runs.OperationalPaperSessionRunCommandType
    desired_state: runs.OperationalPaperSessionRunDesiredState
    expected_record_version: int | None
    resulting_record_version: int
    actor_id: UUID
    requested_at: datetime

    @classmethod
    def from_domain(
        cls,
        command: runs.OperationalPaperSessionRunEpochCommand,
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


class OperationalPaperSessionRunCommandListResponse(ApiSchema):
    """Bounded immutable command-history page."""

    items: list[OperationalPaperSessionRunCommandResponse]
    limit: int
    offset: int

    @classmethod
    def from_domain(
        cls,
        items: list[runs.OperationalPaperSessionRunEpochCommand],
        *,
        limit: int,
        offset: int,
    ) -> Self:
        return cls(
            items=[OperationalPaperSessionRunCommandResponse.from_domain(item) for item in items],
            limit=limit,
            offset=offset,
        )
