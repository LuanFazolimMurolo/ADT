"""Gate 2E worker fencing and lease regressions."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio

import app.operational_paper_session_runs as runs
from app.database import Database
from app.market_data.errors import MarketJobLockTimeoutError
from app.paper_trading.service import PaperTradingService
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.services.operational_paper_session_run_worker import (
    OperationalPaperSessionRunWorker,
    OperationalPaperSessionRunWorkerPolicy,
)
from app.services.operational_paper_session_runs import OperationalPaperSessionRunService
from tests.test_operational_paper_session_activations_migration import (
    _insert as _insert_activation,
)
from tests.test_operational_paper_session_activations_migration import (
    _row as _activation_row,
)
from tests.test_operational_paper_session_activations_migration import (
    _valid_activation,
)
from tests.test_operational_paper_session_runs_migration import START_AT
from tests.test_operational_paper_session_runs_repository import _intent

NOW = datetime(2026, 9, 8, 22, 30, tzinfo=UTC)


@dataclass(frozen=True)
class _Claim:
    worker_id: UUID
    fencing_token: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class _Epoch:
    epoch_id: UUID
    record_version: int
    worker_claim: _Claim


class _ConflictRepository:
    def __init__(self) -> None:
        self.renew_calls = 0

    async def renew(self, *_args: object, **_kwargs: object) -> object:
        self.renew_calls += 1
        raise runs.OperationalPaperSessionRunRecordVersionConflictError()


def _worker(
    repository: _ConflictRepository,
    *,
    worker_id: UUID,
) -> OperationalPaperSessionRunWorker:
    return OperationalPaperSessionRunWorker(
        cast(PostgresOperationalPaperSessionRunRepository, repository),
        cast(OperationalPaperSessionRunService, object()),
        cast(PaperTradingService, object()),
        worker_id=worker_id,
        policy=OperationalPaperSessionRunWorkerPolicy(
            lease_duration_seconds=30,
            heartbeat_interval_seconds=10,
        ),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_renew_fails_closed_after_bounded_record_version_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    epoch_id = uuid4()
    repository = _ConflictRepository()
    worker = _worker(repository, worker_id=worker_id)

    epoch = _Epoch(
        epoch_id=epoch_id,
        record_version=7,
        worker_claim=_Claim(
            worker_id=worker_id,
            fencing_token=3,
            lease_expires_at=NOW + timedelta(seconds=20),
        ),
    )

    async def latest_owned(_epoch_id: UUID, _fence: int) -> runs.OperationalPaperSessionRunEpoch:
        return cast(runs.OperationalPaperSessionRunEpoch, epoch)

    monkeypatch.setattr(worker, "_latest_owned", latest_owned)

    with pytest.raises(runs.OperationalPaperSessionRunLeaseError):
        await worker._renew_once(epoch_id, 3)

    assert repository.renew_calls == 3


@dataclass(frozen=True)
class _TransitionEpoch:
    epoch_id: UUID
    record_version: int
    worker_claim: _Claim
    desired_state: runs.OperationalPaperSessionRunDesiredState
    observed_state: runs.OperationalPaperSessionRunObservedState


class _MarkRunningConflictRepository:
    def __init__(self) -> None:
        self.calls = 0

    async def mark_running(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise runs.OperationalPaperSessionRunRecordVersionConflictError()


@pytest.mark.asyncio
async def test_mark_running_fails_closed_after_bounded_record_version_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    epoch_id = uuid4()
    repository = _MarkRunningConflictRepository()

    worker = OperationalPaperSessionRunWorker(
        cast(PostgresOperationalPaperSessionRunRepository, repository),
        cast(OperationalPaperSessionRunService, object()),
        cast(PaperTradingService, object()),
        worker_id=worker_id,
        policy=OperationalPaperSessionRunWorkerPolicy(
            lease_duration_seconds=30,
            heartbeat_interval_seconds=10,
        ),
        clock=lambda: NOW,
    )

    epoch = _TransitionEpoch(
        epoch_id=epoch_id,
        record_version=11,
        worker_claim=_Claim(
            worker_id=worker_id,
            fencing_token=4,
            lease_expires_at=NOW + timedelta(seconds=20),
        ),
        desired_state=runs.OperationalPaperSessionRunDesiredState.RUNNING,
        observed_state=runs.OperationalPaperSessionRunObservedState.STARTING,
    )

    async def latest_owned(
        _epoch_id: UUID,
        _fence: int,
    ) -> runs.OperationalPaperSessionRunEpoch:
        return cast(runs.OperationalPaperSessionRunEpoch, epoch)

    monkeypatch.setattr(worker, "_latest_owned", latest_owned)

    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError):
        await worker._mark_running(epoch_id, 4)

    assert repository.calls == 3


class _MarkStartingConflictRepository:
    def __init__(self) -> None:
        self.calls = 0

    async def mark_starting(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise runs.OperationalPaperSessionRunRecordVersionConflictError()


class _PauseConflictRepository:
    def __init__(self) -> None:
        self.calls = 0

    async def settle_paused(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise runs.OperationalPaperSessionRunRecordVersionConflictError()


class _StopConflictRepository:
    def __init__(self) -> None:
        self.calls = 0

    async def settle_stopped(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise runs.OperationalPaperSessionRunRecordVersionConflictError()


def _transition_worker(
    repository: object,
    *,
    worker_id: UUID,
) -> OperationalPaperSessionRunWorker:
    return OperationalPaperSessionRunWorker(
        cast(PostgresOperationalPaperSessionRunRepository, repository),
        cast(OperationalPaperSessionRunService, object()),
        cast(PaperTradingService, object()),
        worker_id=worker_id,
        policy=OperationalPaperSessionRunWorkerPolicy(
            lease_duration_seconds=30,
            heartbeat_interval_seconds=10,
        ),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_mark_starting_requires_postcondition_after_version_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    epoch_id = uuid4()
    repository = _MarkStartingConflictRepository()
    worker = _transition_worker(repository, worker_id=worker_id)

    epoch = _TransitionEpoch(
        epoch_id=epoch_id,
        record_version=15,
        worker_claim=_Claim(
            worker_id=worker_id,
            fencing_token=5,
            lease_expires_at=NOW + timedelta(seconds=20),
        ),
        desired_state=runs.OperationalPaperSessionRunDesiredState.RUNNING,
        observed_state=runs.OperationalPaperSessionRunObservedState.PENDING,
    )

    async def latest_owned(
        _epoch_id: UUID,
        _fence: int,
    ) -> runs.OperationalPaperSessionRunEpoch:
        return cast(runs.OperationalPaperSessionRunEpoch, epoch)

    monkeypatch.setattr(worker, "_latest_owned", latest_owned)

    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError):
        await worker._mark_starting(epoch_id, 5)

    assert repository.calls == 3


@pytest.mark.asyncio
async def test_pause_boundary_requires_paused_postcondition_after_version_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    epoch_id = uuid4()
    repository = _PauseConflictRepository()
    worker = _transition_worker(repository, worker_id=worker_id)

    epoch = _TransitionEpoch(
        epoch_id=epoch_id,
        record_version=21,
        worker_claim=_Claim(
            worker_id=worker_id,
            fencing_token=6,
            lease_expires_at=NOW + timedelta(seconds=20),
        ),
        desired_state=runs.OperationalPaperSessionRunDesiredState.PAUSED,
        observed_state=runs.OperationalPaperSessionRunObservedState.RUNNING,
    )

    async def latest_owned(
        _epoch_id: UUID,
        _fence: int,
    ) -> runs.OperationalPaperSessionRunEpoch:
        return cast(runs.OperationalPaperSessionRunEpoch, epoch)

    monkeypatch.setattr(worker, "_latest_owned", latest_owned)

    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError):
        await worker._settle_control_boundary(
            cast(runs.OperationalPaperSessionRunEpoch, epoch),
            6,
        )

    assert repository.calls == 3


@pytest.mark.asyncio
async def test_stop_boundary_requires_stopped_postcondition_after_version_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    epoch_id = uuid4()
    repository = _StopConflictRepository()
    worker = _transition_worker(repository, worker_id=worker_id)

    epoch = _TransitionEpoch(
        epoch_id=epoch_id,
        record_version=31,
        worker_claim=_Claim(
            worker_id=worker_id,
            fencing_token=7,
            lease_expires_at=NOW + timedelta(seconds=20),
        ),
        desired_state=runs.OperationalPaperSessionRunDesiredState.STOPPED,
        observed_state=runs.OperationalPaperSessionRunObservedState.STOPPING,
    )

    async def latest_owned(
        _epoch_id: UUID,
        _fence: int,
    ) -> runs.OperationalPaperSessionRunEpoch:
        return cast(runs.OperationalPaperSessionRunEpoch, epoch)

    monkeypatch.setattr(worker, "_latest_owned", latest_owned)

    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError):
        await worker._settle_control_boundary(
            cast(runs.OperationalPaperSessionRunEpoch, epoch),
            7,
        )

    assert repository.calls == 3


class _StepClock:
    def __init__(
        self,
        value: datetime,
        *,
        step: timedelta = timedelta(milliseconds=10),
    ) -> None:
        self._value = value
        self._step = step

    def __call__(self) -> datetime:
        self._value += self._step
        return self._value


class _PassingControl:
    def __init__(self) -> None:
        self.calls = 0

    async def validate_execution_eligibility(self, _epoch_id: UUID) -> object:
        self.calls += 1
        return object()


class _FailingControl(_PassingControl):
    def __init__(
        self,
        *,
        fail_on_call: int,
        code: runs.OperationalPaperSessionRunFailureCode,
    ) -> None:
        super().__init__()
        self._fail_on_call = fail_on_call
        self._code = code

    async def validate_execution_eligibility(self, _epoch_id: UUID) -> object:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise runs.OperationalPaperSessionRunStateTransitionConflictError(
                details={"failure_code": self._code.value}
            )
        return object()


class _BlockingControl(_PassingControl):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def validate_execution_eligibility(
        self,
        _epoch_id: UUID,
    ) -> object:
        self.calls += 1

        if self.calls == 1:
            self.started.set()
            await self.release.wait()

        return object()


class _CountingPaper:
    def __init__(self) -> None:
        self.calls = 0
        self.session_ids: list[str] = []

    def run_once(self, session_id: str) -> object:
        self.calls += 1
        self.session_ids.append(session_id)
        return object()


class _BlockingPaper(_CountingPaper):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def run_once(self, session_id: str) -> object:
        self.calls += 1
        self.session_ids.append(session_id)
        self.started.set()

        if not self.release.wait(timeout=5):
            raise RuntimeError("bounded test cycle was not released")

        return object()


@pytest.fixture
def pg_run_repository(
    database: Database,
) -> PostgresOperationalPaperSessionRunRepository:
    return PostgresOperationalPaperSessionRunRepository(database)


@pytest_asyncio.fixture
async def pg_pending_epoch(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
) -> runs.OperationalPaperSessionRunEpoch:
    activation = await _valid_activation(
        database_url,
        database,
        auth_user_id,
    )

    with psycopg.connect(database_url) as connection:
        _insert_activation(
            connection,
            _activation_row(activation),
        )

    specification = runs.build_operational_paper_session_run_epoch_specification(activation)

    return await pg_run_repository.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key=f"worker:start:{uuid4().hex}",
        now=START_AT,
    )


def _functional_worker(
    repository: PostgresOperationalPaperSessionRunRepository,
    control: object,
    paper: object,
    *,
    worker_id: UUID,
    clock: _StepClock,
    policy: OperationalPaperSessionRunWorkerPolicy | None = None,
) -> OperationalPaperSessionRunWorker:
    return OperationalPaperSessionRunWorker(
        repository,
        cast(OperationalPaperSessionRunService, control),
        cast(PaperTradingService, paper),
        worker_id=worker_id,
        policy=policy,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_real_repository_claim_starting_running_and_one_cycle(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    worker_id = uuid4()
    clock = _StepClock(START_AT + timedelta(seconds=1))
    control = _PassingControl()
    paper = _CountingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
    )

    result = await worker.run_epoch(
        pg_pending_epoch.epoch_id,
        max_cycles=1,
    )

    assert result.cycles_completed == 1
    assert result.exit_code is None
    assert paper.calls == 1
    assert paper.session_ids == [pg_pending_epoch.session_id]
    assert control.calls == 2

    persisted = await pg_run_repository.get(pg_pending_epoch.epoch_id)
    assert persisted is not None
    assert persisted.observed_state is runs.OperationalPaperSessionRunObservedState.RUNNING
    assert persisted.desired_state is runs.OperationalPaperSessionRunDesiredState.RUNNING
    assert persisted.worker_claim is not None
    assert persisted.worker_claim.worker_id == worker_id
    assert persisted.worker_claim.fencing_token == 1


@pytest.mark.asyncio
async def test_expired_claim_recovers_same_epoch_with_higher_fence(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    old_worker_id = uuid4()

    old_claim = await pg_run_repository.claim(
        pg_pending_epoch.epoch_id,
        expected_record_version=pg_pending_epoch.record_version,
        worker_id=old_worker_id,
        now=START_AT + timedelta(seconds=1),
        lease_expires_at=START_AT + timedelta(seconds=2),
    )

    assert old_claim.worker_claim is not None
    assert old_claim.worker_claim.fencing_token == 1

    new_worker_id = uuid4()
    clock = _StepClock(START_AT + timedelta(seconds=3))
    control = _PassingControl()
    paper = _CountingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=new_worker_id,
        clock=clock,
    )

    result = await worker.run_epoch(
        pg_pending_epoch.epoch_id,
        max_cycles=1,
    )

    assert result.cycles_completed == 1
    assert paper.calls == 1
    assert result.epoch.worker_claim is not None
    assert result.epoch.worker_claim.worker_id == new_worker_id
    assert result.epoch.worker_claim.fencing_token == 2

    persisted = await pg_run_repository.get(pg_pending_epoch.epoch_id)
    assert persisted is not None
    assert persisted.worker_claim is not None
    assert persisted.worker_claim.worker_id == new_worker_id
    assert persisted.worker_claim.fencing_token == 2

    now = clock()

    with pytest.raises(runs.OperationalPaperSessionRunLeaseError):
        await pg_run_repository.renew(
            pg_pending_epoch.epoch_id,
            expected_record_version=persisted.record_version,
            worker_id=old_worker_id,
            fencing_token=1,
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )


@pytest.mark.asyncio
async def test_heartbeat_renews_real_postgres_lease_during_slow_eligibility(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    worker_id = uuid4()
    clock = _StepClock(
        START_AT + timedelta(seconds=1),
        step=timedelta(milliseconds=50),
    )
    control = _BlockingControl()
    paper = _CountingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
        policy=OperationalPaperSessionRunWorkerPolicy(
            lease_duration_seconds=5,
            heartbeat_interval_seconds=0.01,
        ),
    )

    task = asyncio.create_task(
        worker.run_epoch(
            pg_pending_epoch.epoch_id,
            max_cycles=1,
        )
    )

    try:
        await asyncio.wait_for(
            control.started.wait(),
            timeout=3,
        )

        before = await pg_run_repository.get(
            pg_pending_epoch.epoch_id
        )
        assert before is not None
        assert before.worker_claim is not None
        assert (
            before.observed_state
            is runs.OperationalPaperSessionRunObservedState.STARTING
        )

        before_heartbeat = before.worker_claim.heartbeat_at
        before_version = before.record_version

        await asyncio.sleep(0.06)

        during = await pg_run_repository.get(
            pg_pending_epoch.epoch_id
        )
        assert during is not None
        assert during.worker_claim is not None

        assert during.worker_claim.worker_id == worker_id
        assert during.worker_claim.fencing_token == 1
        assert during.worker_claim.heartbeat_at > before_heartbeat
        assert during.record_version > before_version
        assert (
            during.observed_state
            is runs.OperationalPaperSessionRunObservedState.STARTING
        )
        assert paper.calls == 0
    finally:
        control.release.set()

    result = await asyncio.wait_for(
        task,
        timeout=3,
    )

    assert result.cycles_completed == 1
    assert result.exit_code is None
    assert control.calls == 2
    assert paper.calls == 1
    assert paper.session_ids == [pg_pending_epoch.session_id]

    persisted = await pg_run_repository.get(
        pg_pending_epoch.epoch_id
    )
    assert persisted is not None
    assert (
        persisted.observed_state
        is runs.OperationalPaperSessionRunObservedState.RUNNING
    )
    assert (
        persisted.desired_state
        is runs.OperationalPaperSessionRunDesiredState.RUNNING
    )
    assert persisted.worker_claim is not None
    assert persisted.worker_claim.worker_id == worker_id
    assert persisted.worker_claim.fencing_token == 1


@pytest.mark.asyncio
async def test_heartbeat_renews_real_postgres_lease_during_slow_cycle(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    worker_id = uuid4()
    clock = _StepClock(
        START_AT + timedelta(seconds=1),
        step=timedelta(milliseconds=50),
    )
    control = _PassingControl()
    paper = _BlockingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
        policy=OperationalPaperSessionRunWorkerPolicy(
            lease_duration_seconds=5,
            heartbeat_interval_seconds=0.01,
        ),
    )

    task = asyncio.create_task(
        worker.run_epoch(
            pg_pending_epoch.epoch_id,
            max_cycles=1,
        )
    )

    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(paper.started.wait, 2),
            timeout=3,
        )
        assert started

        before = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert before is not None
        assert before.worker_claim is not None

        before_heartbeat = before.worker_claim.heartbeat_at
        before_version = before.record_version

        await asyncio.sleep(0.06)

        during = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert during is not None
        assert during.worker_claim is not None

        assert during.worker_claim.worker_id == worker_id
        assert during.worker_claim.fencing_token == 1
        assert during.worker_claim.heartbeat_at > before_heartbeat
        assert during.record_version > before_version
        assert paper.calls == 1
    finally:
        paper.release.set()

    result = await asyncio.wait_for(task, timeout=3)

    assert result.cycles_completed == 1
    assert result.exit_code is None
    assert paper.calls == 1


@pytest.mark.asyncio
async def test_pause_requested_during_cycle_settles_only_at_boundary(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
    auth_user_id: UUID,
) -> None:
    worker_id = uuid4()
    clock = _StepClock(START_AT + timedelta(seconds=1))
    control = _PassingControl()
    paper = _BlockingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
    )

    task = asyncio.create_task(
        worker.run_epoch(
            pg_pending_epoch.epoch_id,
            max_cycles=2,
        )
    )

    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(paper.started.wait, 2),
            timeout=3,
        )
        assert started

        running = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert running is not None
        assert running.observed_state is runs.OperationalPaperSessionRunObservedState.RUNNING

        await pg_run_repository.request_command(
            _intent(running, "PAUSE"),
            actor_id=auth_user_id,
            idempotency_key=f"worker:pause:{uuid4().hex}",
            now=clock(),
        )

        during = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert during is not None
        assert during.desired_state is runs.OperationalPaperSessionRunDesiredState.PAUSED
        assert during.observed_state is runs.OperationalPaperSessionRunObservedState.RUNNING
        assert paper.calls == 1
    finally:
        paper.release.set()

    result = await asyncio.wait_for(task, timeout=3)

    assert result.cycles_completed == 1
    assert paper.calls == 1
    assert result.epoch.desired_state is runs.OperationalPaperSessionRunDesiredState.PAUSED
    assert result.epoch.observed_state is runs.OperationalPaperSessionRunObservedState.PAUSED


@pytest.mark.asyncio
async def test_stop_requested_during_cycle_stops_only_after_boundary(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
    auth_user_id: UUID,
) -> None:
    worker_id = uuid4()
    clock = _StepClock(START_AT + timedelta(seconds=1))
    control = _PassingControl()
    paper = _BlockingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
    )

    task = asyncio.create_task(
        worker.run_epoch(
            pg_pending_epoch.epoch_id,
            max_cycles=2,
        )
    )

    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(paper.started.wait, 2),
            timeout=3,
        )
        assert started

        running = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert running is not None

        await pg_run_repository.request_command(
            _intent(running, "STOP"),
            actor_id=auth_user_id,
            idempotency_key=f"worker:stop:{uuid4().hex}",
            now=clock(),
        )

        during = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert during is not None
        assert during.desired_state is runs.OperationalPaperSessionRunDesiredState.STOPPED
        assert during.observed_state is runs.OperationalPaperSessionRunObservedState.RUNNING
        assert paper.calls == 1
    finally:
        paper.release.set()

    result = await asyncio.wait_for(task, timeout=3)

    assert result.cycles_completed == 1
    assert paper.calls == 1
    assert result.epoch.desired_state is runs.OperationalPaperSessionRunDesiredState.STOPPED
    assert result.epoch.observed_state is runs.OperationalPaperSessionRunObservedState.STOPPED


@pytest.mark.asyncio
async def test_authority_loss_before_next_cycle_fails_without_second_execution(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    worker_id = uuid4()
    clock = _StepClock(START_AT + timedelta(seconds=1))
    control = _FailingControl(
        fail_on_call=3,
        code=runs.OperationalPaperSessionRunFailureCode.AUTHORITY_LOST,
    )
    paper = _CountingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
    )

    result = await worker.run_epoch(
        pg_pending_epoch.epoch_id,
        max_cycles=2,
    )

    assert result.cycles_completed == 1
    assert paper.calls == 1
    assert control.calls == 3
    assert result.exit_code is runs.OperationalPaperSessionRunFailureCode.AUTHORITY_LOST

    persisted = await pg_run_repository.get(pg_pending_epoch.epoch_id)
    assert persisted is not None
    assert persisted.observed_state is runs.OperationalPaperSessionRunObservedState.FAILED
    assert persisted.failure is not None
    assert persisted.failure.code is runs.OperationalPaperSessionRunFailureCode.AUTHORITY_LOST


class _BusyPaper(_CountingPaper):
    def run_once(self, session_id: str) -> object:
        self.calls += 1
        self.session_ids.append(session_id)
        raise MarketJobLockTimeoutError()


@dataclass(frozen=True)
class _FailurePersistenceEpoch:
    epoch_id: UUID
    record_version: int
    worker_claim: _Claim
    desired_state: runs.OperationalPaperSessionRunDesiredState
    observed_state: runs.OperationalPaperSessionRunObservedState
    session_id: str
    failure: object | None = None


class _FailurePersistenceConflictRepository:
    def __init__(
        self,
        epoch: _FailurePersistenceEpoch,
    ) -> None:
        self.epoch = epoch
        self.fail_calls = 0

    async def get(self, _epoch_id: UUID) -> object:
        return self.epoch

    async def fail_claimed(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        self.fail_calls += 1
        raise runs.OperationalPaperSessionRunRecordVersionConflictError()


@pytest.mark.asyncio
async def test_local_lock_timeout_persists_local_runner_busy(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    worker_id = uuid4()
    clock = _StepClock(START_AT + timedelta(seconds=1))
    control = _PassingControl()
    paper = _BusyPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
    )

    result = await worker.run_epoch(
        pg_pending_epoch.epoch_id,
        max_cycles=1,
    )

    assert result.cycles_completed == 0
    assert paper.calls == 1
    assert result.exit_code is runs.OperationalPaperSessionRunFailureCode.LOCAL_RUNNER_BUSY

    persisted = await pg_run_repository.get(pg_pending_epoch.epoch_id)
    assert persisted is not None
    assert persisted.observed_state is runs.OperationalPaperSessionRunObservedState.FAILED
    assert persisted.failure is not None
    assert persisted.failure.code is runs.OperationalPaperSessionRunFailureCode.LOCAL_RUNNER_BUSY
    assert persisted.worker_claim is None


@pytest.mark.asyncio
async def test_recovery_during_cycle_fences_old_worker_without_overwrite(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    old_worker_id = uuid4()
    new_worker_id = uuid4()

    clock = _StepClock(START_AT + timedelta(seconds=1))
    control = _PassingControl()
    paper = _BlockingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=old_worker_id,
        clock=clock,
        policy=OperationalPaperSessionRunWorkerPolicy(
            lease_duration_seconds=30,
            heartbeat_interval_seconds=20,
        ),
    )

    task = asyncio.create_task(
        worker.run_epoch(
            pg_pending_epoch.epoch_id,
            max_cycles=2,
        )
    )

    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(paper.started.wait, 2),
            timeout=3,
        )
        assert started

        owned = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert owned is not None
        assert owned.worker_claim is not None
        assert owned.worker_claim.worker_id == old_worker_id
        assert owned.worker_claim.fencing_token == 1

        recover_at = owned.worker_claim.lease_expires_at + timedelta(seconds=1)

        recovered = await pg_run_repository.recover(
            owned.epoch_id,
            expected_record_version=owned.record_version,
            worker_id=new_worker_id,
            now=recover_at,
            lease_expires_at=recover_at + timedelta(seconds=30),
        )

        assert recovered.worker_claim is not None
        assert recovered.worker_claim.worker_id == new_worker_id
        assert recovered.worker_claim.fencing_token == 2
        assert recovered.observed_state is runs.OperationalPaperSessionRunObservedState.RECOVERING
    finally:
        paper.release.set()

    result = await asyncio.wait_for(task, timeout=3)

    assert result.cycles_completed == 1
    assert paper.calls == 1
    assert result.exit_code is runs.OperationalPaperSessionRunFailureCode.LEASE_LOST

    persisted = await pg_run_repository.get(pg_pending_epoch.epoch_id)
    assert persisted is not None
    assert persisted.worker_claim is not None
    assert persisted.worker_claim.worker_id == new_worker_id
    assert persisted.worker_claim.fencing_token == 2
    assert persisted.observed_state is runs.OperationalPaperSessionRunObservedState.RECOVERING
    assert persisted.failure is None


@pytest.mark.asyncio
async def test_heartbeat_survives_admin_record_version_change(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
    auth_user_id: UUID,
) -> None:
    worker_id = uuid4()

    clock = _StepClock(
        START_AT + timedelta(seconds=1),
        step=timedelta(milliseconds=50),
    )
    control = _PassingControl()
    paper = _BlockingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
        policy=OperationalPaperSessionRunWorkerPolicy(
            lease_duration_seconds=5,
            heartbeat_interval_seconds=0.25,
        ),
    )

    task = asyncio.create_task(
        worker.run_epoch(
            pg_pending_epoch.epoch_id,
            max_cycles=2,
        )
    )

    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(paper.started.wait, 2),
            timeout=3,
        )
        assert started

        running = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert running is not None
        assert running.worker_claim is not None

        await pg_run_repository.request_command(
            _intent(running, "PAUSE"),
            actor_id=auth_user_id,
            idempotency_key=f"worker:admin-churn:{uuid4().hex}",
            now=clock(),
        )

        after_command = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert after_command is not None
        assert after_command.worker_claim is not None
        assert after_command.desired_state is runs.OperationalPaperSessionRunDesiredState.PAUSED

        command_version = after_command.record_version
        command_heartbeat = after_command.worker_claim.heartbeat_at

        renewed = None

        for _attempt in range(100):
            await asyncio.sleep(0.01)

            candidate = await pg_run_repository.get(pg_pending_epoch.epoch_id)

            assert candidate is not None
            assert candidate.worker_claim is not None

            if (
                candidate.record_version > command_version
                and candidate.worker_claim.heartbeat_at > command_heartbeat
            ):
                renewed = candidate
                break

        assert renewed is not None
        assert renewed.worker_claim is not None
        assert renewed.worker_claim.worker_id == worker_id
        assert renewed.worker_claim.fencing_token == 1
        assert renewed.desired_state is runs.OperationalPaperSessionRunDesiredState.PAUSED
        assert paper.calls == 1
    finally:
        paper.release.set()

    result = await asyncio.wait_for(task, timeout=3)

    assert result.cycles_completed == 1
    assert paper.calls == 1
    assert result.exit_code is None
    assert result.epoch.observed_state is runs.OperationalPaperSessionRunObservedState.PAUSED


@pytest.mark.asyncio
async def test_local_shutdown_does_not_forge_administrative_stop(
    pg_run_repository: PostgresOperationalPaperSessionRunRepository,
    pg_pending_epoch: runs.OperationalPaperSessionRunEpoch,
) -> None:
    worker_id = uuid4()
    clock = _StepClock(START_AT + timedelta(seconds=1))
    control = _PassingControl()
    paper = _BlockingPaper()

    worker = _functional_worker(
        pg_run_repository,
        control,
        paper,
        worker_id=worker_id,
        clock=clock,
    )

    task = asyncio.create_task(
        worker.run_epoch(
            pg_pending_epoch.epoch_id,
            max_cycles=2,
        )
    )

    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(paper.started.wait, 2),
            timeout=3,
        )
        assert started

        worker.request_stop()

        during = await pg_run_repository.get(pg_pending_epoch.epoch_id)
        assert during is not None
        assert during.desired_state is runs.OperationalPaperSessionRunDesiredState.RUNNING
        assert during.observed_state is runs.OperationalPaperSessionRunObservedState.RUNNING
    finally:
        paper.release.set()

    result = await asyncio.wait_for(task, timeout=3)

    assert result.cycles_completed == 1
    assert paper.calls == 1
    assert result.exit_code is None

    persisted = await pg_run_repository.get(pg_pending_epoch.epoch_id)
    assert persisted is not None
    assert persisted.desired_state is runs.OperationalPaperSessionRunDesiredState.RUNNING
    assert persisted.observed_state is runs.OperationalPaperSessionRunObservedState.RUNNING
    assert persisted.failure is None


@pytest.mark.asyncio
async def test_failure_persistence_churn_never_fabricates_failed_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    epoch_id = uuid4()

    epoch = _FailurePersistenceEpoch(
        epoch_id=epoch_id,
        record_version=41,
        worker_claim=_Claim(
            worker_id=worker_id,
            fencing_token=9,
            lease_expires_at=NOW + timedelta(seconds=20),
        ),
        desired_state=(runs.OperationalPaperSessionRunDesiredState.RUNNING),
        observed_state=(runs.OperationalPaperSessionRunObservedState.RUNNING),
        session_id="a" * 64,
    )

    repository = _FailurePersistenceConflictRepository(epoch)

    control = _FailingControl(
        fail_on_call=1,
        code=runs.OperationalPaperSessionRunFailureCode.INTERNAL_ERROR,
    )

    worker = OperationalPaperSessionRunWorker(
        cast(
            PostgresOperationalPaperSessionRunRepository,
            repository,
        ),
        cast(OperationalPaperSessionRunService, control),
        cast(PaperTradingService, _CountingPaper()),
        worker_id=worker_id,
        clock=lambda: NOW,
    )

    async def acquired(
        value: runs.OperationalPaperSessionRunEpoch,
    ) -> runs.OperationalPaperSessionRunEpoch:
        return value

    async def prepared(
        value: runs.OperationalPaperSessionRunEpoch,
        _fence: int,
    ) -> runs.OperationalPaperSessionRunEpoch:
        return value

    monkeypatch.setattr(
        worker,
        "_acquire_or_settle",
        acquired,
    )
    monkeypatch.setattr(
        worker,
        "_prepare_running",
        prepared,
    )

    result = await worker.run_epoch(epoch_id, max_cycles=1)

    assert repository.fail_calls == 3
    assert result.exit_code is runs.OperationalPaperSessionRunFailureCode.INTERNAL_ERROR

    # The exit reason is known, but FAILED was never persisted.
    assert result.epoch.observed_state is runs.OperationalPaperSessionRunObservedState.RUNNING
    assert result.epoch.failure is None
