"""Phase 7 market-data operational-control application service."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.market_data.domain import DataRange, Instrument, Timeframe
from app.market_data.errors import (
    InvalidMarketOperationRequestError,
    MarketOperationNotFoundError,
    MarketOperationPlanConflictError,
)
from app.market_data.operation_ports import (
    MarketOperationRepository,
    OperationClock,
    OperationIdGenerator,
)
from app.market_data.operations import (
    MarketDatasetSelector,
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    encode_dataset_id,
    request_cancel,
    request_pause,
    request_resume,
)
from app.market_data.planning import (
    BackfillPlan,
    MarketDataPlanner,
    MarketJobType,
    backfill_plan_checksum,
)
from app.market_data.storage import ParquetCandleStore


class DatasetPlanningLeaseProvider(Protocol):
    """Provide the recovered local dataset lease required by incremental planning."""

    def dataset_lease(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> AbstractContextManager[object]: ...


@dataclass(frozen=True, slots=True)
class MarketOperationPlanPreview:
    """Backend-owned bounded plan shown before administrator confirmation."""

    operation_type: MarketOperationType
    dataset: MarketDatasetSelector
    data_range: DataRange
    plan: OperationPlanSummary


@dataclass(frozen=True, slots=True)
class IncrementalMarketOperationPlanPreview:
    """Incremental planning result, including the explicit no-op case."""

    action: Literal["RUN", "NOOP"]
    preview: MarketOperationPlanPreview | None
    last_open_time: datetime | None
    latest_closed_end: datetime


class MarketOperationService:
    """Plan and persist operational intent without executing market-data work."""

    def __init__(
        self,
        *,
        repository: MarketOperationRepository,
        planner: MarketDataPlanner,
        store: ParquetCandleStore,
        planning_leases: DatasetPlanningLeaseProvider,
        clock: OperationClock,
        id_generator: OperationIdGenerator,
    ) -> None:
        self._repository = repository
        self._planner = planner
        self._store = store
        self._planning_leases = planning_leases
        self._clock = clock
        self._id_generator = id_generator

    def observed_at(self) -> datetime:
        """Return the authoritative server instant for lease-time presentation."""
        return self._clock()

    def plan_backfill(
        self,
        *,
        dataset: MarketDatasetSelector,
        data_range: DataRange,
    ) -> MarketOperationPlanPreview:
        """Create one read-only bounded RAW backfill preview."""
        now = self._clock()
        plan = self._planner.backfill(
            dataset.canonical_key,
            dataset.timeframe,
            data_range,
            job_type=MarketJobType.BACKFILL,
            latest_closed_at=now,
        )
        return _preview(
            MarketOperationType.RAW_BACKFILL,
            dataset,
            plan,
            created_at=now,
        )

    def plan_incremental(
        self,
        *,
        dataset: MarketDatasetSelector,
        overlap_candles: int,
        start: datetime | None = None,
    ) -> IncrementalMarketOperationPlanPreview:
        """Plan one local-state-aware RAW incremental update under its dataset lease."""
        now = self._clock()
        instrument = _planning_instrument(dataset)
        with self._planning_leases.dataset_lease(instrument, dataset.timeframe):
            planned = self._planner.incremental(
                self._store,
                instrument,
                dataset.timeframe,
                now=now,
                overlap_candles=overlap_candles,
                start=start,
            )

        if planned.backfill is None:
            return IncrementalMarketOperationPlanPreview(
                action="NOOP",
                preview=None,
                last_open_time=planned.last_open_time,
                latest_closed_end=planned.latest_closed_end,
            )

        return IncrementalMarketOperationPlanPreview(
            action="RUN",
            preview=_preview(
                MarketOperationType.RAW_INCREMENTAL_UPDATE,
                dataset,
                planned.backfill,
                created_at=now,
            ),
            last_open_time=planned.last_open_time,
            latest_closed_end=planned.latest_closed_end,
        )

    async def submit(
        self,
        *,
        operation_type: MarketOperationType,
        dataset: MarketDatasetSelector,
        data_range: DataRange,
        plan_checksum: str,
        idempotency_key: str,
        requested_by: UUID,
    ) -> MarketOperationSnapshot:
        """Recompute the bounded chunk plan and persist only administrative intent."""
        now = self._clock()
        plan = self._planner.backfill(
            dataset.canonical_key,
            dataset.timeframe,
            data_range,
            job_type=_job_type(operation_type),
            latest_closed_at=now,
        )
        checksum = backfill_plan_checksum(plan)
        if checksum != plan_checksum:
            raise MarketOperationPlanConflictError()

        summary = _plan_summary(plan, created_at=now)
        request = MarketOperationRequest(
            operation_type=operation_type,
            dataset=dataset,
            data_range=data_range,
            plan_checksum=checksum,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )
        operation_id = self._id_generator()
        if not isinstance(operation_id, UUID) or operation_id.int == 0:
            raise InvalidMarketOperationRequestError()

        return await self._repository.create_idempotently(
            operation_id=operation_id,
            request=request,
            plan=summary,
            now=now,
        )

    async def get(self, operation_id: UUID) -> MarketOperationSnapshot:
        """Return one operation or raise the stable not-found domain error."""
        operation = await self._repository.get(operation_id)
        if operation is None:
            raise MarketOperationNotFoundError()
        return operation

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: MarketOperationState | None = None,
        requested_by: UUID | None = None,
        dataset: MarketDatasetSelector | None = None,
    ) -> tuple[MarketOperationSnapshot, ...]:
        """List sanitized operations through the bounded repository contract."""
        return await self._repository.list(
            limit=limit,
            offset=offset,
            state=state,
            requested_by=requested_by,
            dataset_id=None if dataset is None else encode_dataset_id(dataset),
        )

    async def pause(
        self,
        operation_id: UUID,
        *,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        """Request an idempotent cooperative pause."""
        return await self._request_control(
            operation_id,
            expected_version=expected_version,
            action="pause",
        )

    async def resume(
        self,
        operation_id: UUID,
        *,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        """Resume a paused operation by returning it to the pending queue."""
        return await self._request_control(
            operation_id,
            expected_version=expected_version,
            action="resume",
        )

    async def cancel(
        self,
        operation_id: UUID,
        *,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        """Request idempotent cooperative cancellation."""
        return await self._request_control(
            operation_id,
            expected_version=expected_version,
            action="cancel",
        )

    async def _request_control(
        self,
        operation_id: UUID,
        *,
        expected_version: int,
        action: Literal["pause", "resume", "cancel"],
    ) -> MarketOperationSnapshot:
        current = await self.get(operation_id)
        if action == "pause":
            target = request_pause(current.state)
        elif action == "resume":
            target = request_resume(current.state)
        else:
            target = request_cancel(current.state)

        return await self._repository.request_state(
            operation_id=operation_id,
            target=target,
            expected_version=expected_version,
            now=self._clock(),
        )


def _preview(
    operation_type: MarketOperationType,
    dataset: MarketDatasetSelector,
    plan: BackfillPlan,
    *,
    created_at: datetime,
) -> MarketOperationPlanPreview:
    return MarketOperationPlanPreview(
        operation_type=operation_type,
        dataset=dataset,
        data_range=plan.data_range,
        plan=_plan_summary(plan, created_at=created_at),
    )


def _plan_summary(
    plan: BackfillPlan,
    *,
    created_at: datetime,
) -> OperationPlanSummary:
    return OperationPlanSummary(
        checksum=backfill_plan_checksum(plan),
        chunks_planned=len(plan.chunks),
        estimated_candles=plan.expected_candles,
        estimated_requests=len(plan.chunks),
        created_at=created_at,
    )


def _job_type(operation_type: MarketOperationType) -> MarketJobType:
    if operation_type is MarketOperationType.RAW_BACKFILL:
        return MarketJobType.BACKFILL
    if operation_type is MarketOperationType.RAW_INCREMENTAL_UPDATE:
        return MarketJobType.INCREMENTAL
    raise InvalidMarketOperationRequestError()


def _planning_instrument(dataset: MarketDatasetSelector) -> Instrument:
    return Instrument(
        exchange=dataset.exchange,
        market_type=dataset.market_type,
        pair=dataset.pair,
        native_symbol=f"{dataset.pair.base}{dataset.pair.quote}",
        active=True,
    )
