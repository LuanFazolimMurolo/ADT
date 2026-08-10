"""Phase 7-01B market-operation application-service tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from app.market_data.domain import DataRange, Exchange, MarketType, TradingPair
from app.market_data.errors import (
    MarketOperationNotFoundError,
    MarketOperationPlanConflictError,
)
from app.market_data.jobs import MarketJobCatalog
from app.market_data.operation_ports import MarketOperationRepository
from app.market_data.operations import (
    MarketDatasetSelector,
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    OperationProgress,
)
from app.market_data.planning import MarketDataPlanner, backfill_plan_checksum
from app.market_data.storage import ParquetCandleStore
from app.market_data.timeframes import get_timeframe
from app.services.market_operations import MarketOperationService
from tests.market_data_helpers import utc

_OPERATION_ID = UUID("11111111-1111-4111-8111-111111111111")
_REQUESTER_ID = UUID("22222222-2222-4222-8222-222222222222")


class FakeMarketOperationRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, MarketOperationSnapshot] = {}
        self.last_list: dict[str, object] | None = None

    async def create_idempotently(
        self,
        *,
        operation_id: UUID,
        request: MarketOperationRequest,
        plan: OperationPlanSummary,
        now: datetime,
    ) -> MarketOperationSnapshot:
        snapshot = MarketOperationSnapshot(
            operation_id=operation_id,
            request=request,
            plan=plan,
            state=MarketOperationState.PENDING,
            progress=OperationProgress(
                chunks_planned=plan.chunks_planned,
                chunks_completed=0,
                chunks_failed=0,
                candles_estimated=plan.estimated_candles,
                candles_received=0,
                candles_persisted=0,
                requests_completed=0,
                updated_at=now,
            ),
            created_at=now,
            updated_at=now,
            record_version=1,
        )
        self.items[operation_id] = snapshot
        return snapshot

    async def get(self, operation_id: UUID) -> MarketOperationSnapshot | None:
        return self.items.get(operation_id)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: MarketOperationState | None = None,
        requested_by: UUID | None = None,
        dataset_id: str | None = None,
    ) -> tuple[MarketOperationSnapshot, ...]:
        self.last_list = {
            "limit": limit,
            "offset": offset,
            "state": state,
            "requested_by": requested_by,
            "dataset_id": dataset_id,
        }
        return tuple(self.items.values())

    async def request_state(
        self,
        *,
        operation_id: UUID,
        target: MarketOperationState,
        expected_version: int,
        now: datetime,
        owner_id: UUID | None = None,
    ) -> MarketOperationSnapshot:
        assert owner_id is None
        current = self.items[operation_id]
        assert current.record_version == expected_version
        if current.state is target:
            return current
        updated = replace(
            current,
            state=target,
            updated_at=now,
            record_version=current.record_version + 1,
        )
        self.items[operation_id] = updated
        return updated


class FakePlanningLeases:
    def __init__(self) -> None:
        self.entries = 0

    @contextmanager
    def dataset_lease(self, instrument: object, timeframe: object) -> Iterator[object]:
        self.entries += 1
        yield object()


def _dataset(code: str = "1h") -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe(code),
    )


def _planner() -> MarketDataPlanner:
    return MarketDataPlanner(
        adapter_request_limit=1000,
        max_fetch_candles=1000,
        chunk_candles=3,
        max_total_candles=100,
        max_chunks=100,
        clock=lambda: utc(2026, 8, 10, 12),
    )


def _service(
    tmp_path: Path,
) -> tuple[MarketOperationService, FakeMarketOperationRepository, FakePlanningLeases]:
    repository = FakeMarketOperationRepository()
    leases = FakePlanningLeases()
    service = MarketOperationService(
        repository=cast(MarketOperationRepository, repository),
        planner=_planner(),
        store=ParquetCandleStore(tmp_path),
        planning_leases=leases,
        clock=lambda: utc(2026, 8, 10, 12),
        id_generator=lambda: _OPERATION_ID,
    )
    return service, repository, leases


def test_public_plan_checksum_matches_local_job_catalog(tmp_path: Path) -> None:
    plan = _planner().backfill(
        _dataset().canonical_key,
        get_timeframe("1h"),
        DataRange(utc(2026, 8, 1), utc(2026, 8, 1, 5)),
    )

    record = MarketJobCatalog(tmp_path).create(plan)

    assert record.plan_checksum == backfill_plan_checksum(plan)


def test_backfill_preview_is_bounded_and_checksum_deterministic(tmp_path: Path) -> None:
    service, _repository, _leases = _service(tmp_path)
    preview = service.plan_backfill(
        dataset=_dataset(),
        data_range=DataRange(utc(2026, 8, 1), utc(2026, 8, 1, 5)),
    )
    repeated = service.plan_backfill(
        dataset=_dataset(),
        data_range=preview.data_range,
    )

    assert preview.operation_type is MarketOperationType.RAW_BACKFILL
    assert preview.plan.checksum == repeated.plan.checksum
    assert preview.plan.chunks_planned == 2
    assert preview.plan.estimated_candles == 5
    assert preview.plan.estimated_requests == 2
    assert len(preview.plan.checksum) == 64


def test_incremental_preview_uses_local_dataset_lease(tmp_path: Path) -> None:
    service, _repository, leases = _service(tmp_path)
    run = service.plan_incremental(
        dataset=_dataset(),
        overlap_candles=0,
        start=utc(2026, 8, 10, 9),
    )

    assert run.action == "RUN"
    assert run.preview is not None
    assert run.preview.operation_type is MarketOperationType.RAW_INCREMENTAL_UPDATE
    assert run.preview.data_range == DataRange(
        utc(2026, 8, 10, 9),
        utc(2026, 8, 10, 12),
    )
    assert leases.entries == 1


@pytest.mark.asyncio
async def test_submit_recomputes_preview_and_persists_only_intent(tmp_path: Path) -> None:
    service, repository, _leases = _service(tmp_path)
    preview = service.plan_backfill(
        dataset=_dataset(),
        data_range=DataRange(utc(2026, 8, 1), utc(2026, 8, 1, 5)),
    )

    submitted = await service.submit(
        operation_type=preview.operation_type,
        dataset=preview.dataset,
        data_range=preview.data_range,
        plan_checksum=preview.plan.checksum,
        idempotency_key="phase7-01b-test",
        requested_by=_REQUESTER_ID,
    )

    assert submitted.operation_id == _OPERATION_ID
    assert submitted.state is MarketOperationState.PENDING
    assert submitted.request.plan_checksum == preview.plan.checksum
    assert submitted.plan == preview.plan
    assert repository.items == {_OPERATION_ID: submitted}


@pytest.mark.asyncio
async def test_submit_rejects_plan_checksum_drift(tmp_path: Path) -> None:
    service, repository, _leases = _service(tmp_path)

    with pytest.raises(MarketOperationPlanConflictError):
        await service.submit(
            operation_type=MarketOperationType.RAW_BACKFILL,
            dataset=_dataset(),
            data_range=DataRange(utc(2026, 8, 1), utc(2026, 8, 1, 5)),
            plan_checksum="0" * 64,
            idempotency_key="phase7-01b-conflict",
            requested_by=_REQUESTER_ID,
        )

    assert repository.items == {}


@pytest.mark.asyncio
async def test_incremental_submission_checksum_is_bound_to_job_type(tmp_path: Path) -> None:
    service, _repository, _leases = _service(tmp_path)
    incremental = service.plan_incremental(
        dataset=_dataset(),
        overlap_candles=0,
        start=utc(2026, 8, 10, 9),
    )
    assert incremental.preview is not None

    submitted = await service.submit(
        operation_type=MarketOperationType.RAW_INCREMENTAL_UPDATE,
        dataset=incremental.preview.dataset,
        data_range=incremental.preview.data_range,
        plan_checksum=incremental.preview.plan.checksum,
        idempotency_key="phase7-01b-incremental",
        requested_by=_REQUESTER_ID,
    )
    backfill = service.plan_backfill(
        dataset=_dataset(),
        data_range=incremental.preview.data_range,
    )

    assert submitted.request.plan_checksum != backfill.plan.checksum


@pytest.mark.asyncio
async def test_query_filters_and_not_found_are_application_level(tmp_path: Path) -> None:
    service, repository, _leases = _service(tmp_path)
    preview = service.plan_backfill(
        dataset=_dataset(),
        data_range=DataRange(utc(2026, 8, 1), utc(2026, 8, 1, 2)),
    )
    submitted = await service.submit(
        operation_type=preview.operation_type,
        dataset=preview.dataset,
        data_range=preview.data_range,
        plan_checksum=preview.plan.checksum,
        idempotency_key="phase7-01b-query",
        requested_by=_REQUESTER_ID,
    )

    listed = await service.list(
        limit=20,
        offset=0,
        state=MarketOperationState.PENDING,
        requested_by=_REQUESTER_ID,
        dataset=_dataset(),
    )

    assert listed == (submitted,)
    assert repository.last_list is not None
    assert repository.last_list["state"] is MarketOperationState.PENDING
    assert repository.last_list["requested_by"] == _REQUESTER_ID
    assert isinstance(repository.last_list["dataset_id"], str)

    with pytest.raises(MarketOperationNotFoundError):
        await service.get(UUID("33333333-3333-4333-8333-333333333333"))


@pytest.mark.asyncio
async def test_pause_resume_and_cancel_use_cooperative_domain_transitions(
    tmp_path: Path,
) -> None:
    service, repository, _leases = _service(tmp_path)
    preview = service.plan_backfill(
        dataset=_dataset(),
        data_range=DataRange(utc(2026, 8, 1), utc(2026, 8, 1, 2)),
    )
    current = await service.submit(
        operation_type=preview.operation_type,
        dataset=preview.dataset,
        data_range=preview.data_range,
        plan_checksum=preview.plan.checksum,
        idempotency_key="phase7-01b-control",
        requested_by=_REQUESTER_ID,
    )

    pause_requested = await service.pause(
        current.operation_id,
        expected_version=current.record_version,
    )
    assert pause_requested.state is MarketOperationState.PAUSE_REQUESTED

    repeated_pause = await service.pause(
        current.operation_id,
        expected_version=pause_requested.record_version,
    )
    assert repeated_pause == pause_requested

    paused = replace(
        pause_requested,
        state=MarketOperationState.PAUSED,
        record_version=pause_requested.record_version + 1,
    )
    repository.items[current.operation_id] = paused

    resumed = await service.resume(
        current.operation_id,
        expected_version=paused.record_version,
    )
    assert resumed.state is MarketOperationState.PENDING

    cancel_requested = await service.cancel(
        current.operation_id,
        expected_version=resumed.record_version,
    )
    assert cancel_requested.state is MarketOperationState.CANCEL_REQUESTED
