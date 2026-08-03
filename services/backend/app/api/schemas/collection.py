"""Read-only continuous market collection API contracts."""

from __future__ import annotations

from datetime import datetime

from app.api.schemas.common import ApiSchema
from app.market_data.continuous import (
    ContinuousCollectionState,
    ContinuousTargetResult,
    validate_continuous_collection_state,
)


class ContinuousTargetResultResponse(ApiSchema):
    target: str
    status: str
    started_at: datetime
    finished_at: datetime
    latest_closed_end: datetime
    job_id: str | None
    fetched_count: int
    stored_count: int
    duplicate_count: int
    request_count: int
    error_code: str | None

    @classmethod
    def from_domain(cls, result: ContinuousTargetResult) -> ContinuousTargetResultResponse:
        if not isinstance(result, ContinuousTargetResult):
            raise ValueError("Continuous target result is invalid.")
        ContinuousTargetResult.__post_init__(result)
        return cls(
            target=result.target.key,
            status=result.status.value,
            started_at=result.started_at,
            finished_at=result.finished_at,
            latest_closed_end=result.latest_closed_end,
            job_id=result.job_id,
            fetched_count=result.fetched_count,
            stored_count=result.stored_count,
            duplicate_count=result.duplicate_count,
            request_count=result.request_count,
            error_code=result.error_code,
        )


class ContinuousCollectionStatusResponse(ApiSchema):
    schema_version: int
    cycle_index: int
    cycle_id: str
    status: str
    interval_seconds: int
    overlap_candles: int
    max_targets: int
    started_at: datetime
    finished_at: datetime
    next_cycle_at: datetime
    checksum: str
    results: list[ContinuousTargetResultResponse]

    @classmethod
    def from_domain(cls, state: ContinuousCollectionState) -> ContinuousCollectionStatusResponse:
        validate_continuous_collection_state(state)
        return cls(
            schema_version=state.schema_version,
            cycle_index=state.cycle_index,
            cycle_id=state.cycle_id,
            status=state.status.value,
            interval_seconds=state.policy.interval_seconds,
            overlap_candles=state.policy.overlap_candles,
            max_targets=state.policy.max_targets,
            started_at=state.started_at,
            finished_at=state.finished_at,
            next_cycle_at=state.next_cycle_at,
            checksum=state.checksum,
            results=[ContinuousTargetResultResponse.from_domain(item) for item in state.results],
        )
