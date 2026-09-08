"""Fenced operational worker for one persisted paper-session run epoch."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import app.operational_paper_session_runs as runs
from app.market_data.errors import MarketJobLockTimeoutError
from app.paper_trading.errors import (
    InvalidPaperSessionError,
    PaperSessionConflictError,
    PaperSessionCorruptError,
    PaperSessionDataUnavailableError,
    PaperSessionNotFoundError,
    PaperSessionVerificationError,
    PaperTradingError,
)
from app.paper_trading.service import PaperTradingService
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.services.operational_paper_session_runs import OperationalPaperSessionRunService

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
_Code = runs.OperationalPaperSessionRunFailureCode
_Epoch = runs.OperationalPaperSessionRunEpoch
_Desired = runs.OperationalPaperSessionRunDesiredState
_Observed = runs.OperationalPaperSessionRunObservedState


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunWorkerPolicy:
    """Bounded lease policy; heartbeat must remain strictly inside the lease."""

    lease_duration_seconds: float = 30.0
    heartbeat_interval_seconds: float = 10.0

    def __post_init__(self) -> None:
        lease = self.lease_duration_seconds
        heartbeat = self.heartbeat_interval_seconds
        if (
            isinstance(lease, bool)
            or isinstance(heartbeat, bool)
            or not math.isfinite(lease)
            or not math.isfinite(heartbeat)
            or lease <= 0
            or heartbeat <= 0
            or heartbeat >= lease
        ):
            raise ValueError("A política de lease do worker paper é inválida.")

    @property
    def lease_duration(self) -> timedelta:
        return timedelta(seconds=self.lease_duration_seconds)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunWorkerResult:
    """One bounded worker ownership interval."""

    epoch: _Epoch
    cycles_completed: int
    exit_code: _Code | None = None


class OperationalPaperSessionRunWorker:
    """Execute cycles only while PostgreSQL fencing and fresh authority remain valid."""

    def __init__(
        self,
        repository: PostgresOperationalPaperSessionRunRepository,
        control: OperationalPaperSessionRunService,
        paper: PaperTradingService,
        *,
        policy: OperationalPaperSessionRunWorkerPolicy | None = None,
        worker_id: UUID | None = None,
        clock: Clock = _utc_now,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._repository = repository
        self._control = control
        self._paper = paper
        self._policy = policy or OperationalPaperSessionRunWorkerPolicy()
        self._worker_id = worker_id or uuid4()
        self._clock = clock
        self._sleeper = sleeper
        self._stop_requested = asyncio.Event()

    @property
    def worker_id(self) -> UUID:
        """Stable identity for the lifetime of this worker instance."""
        return self._worker_id

    def request_stop(self) -> None:
        """Request local process shutdown without forging an administrative STOP."""
        self._stop_requested.set()

    async def run_epoch(
        self,
        epoch_id: UUID,
        *,
        max_cycles: int | None = None,
    ) -> OperationalPaperSessionRunWorkerResult:
        """Own one epoch until a control boundary, failure, or bounded local shutdown."""
        if max_cycles is not None and (isinstance(max_cycles, bool) or max_cycles <= 0):
            raise ValueError("O limite de ciclos do worker paper é inválido.")

        cycles_completed = 0
        epoch = await self._load(epoch_id)

        if self._terminal(epoch):
            return OperationalPaperSessionRunWorkerResult(epoch, 0)

        try:
            epoch = await self._acquire_or_settle(epoch)

            if self._terminal(epoch) or epoch.observed_state is _Observed.PAUSED:
                return OperationalPaperSessionRunWorkerResult(epoch, 0)

            claim = self._require_own_claim(epoch)
            fence = claim.fencing_token

            epoch = await self._prepare_running(epoch, fence)
            if self._terminal(epoch) or epoch.observed_state is _Observed.PAUSED:
                return OperationalPaperSessionRunWorkerResult(epoch, 0)

            while True:
                if self._stop_requested.is_set():
                    return OperationalPaperSessionRunWorkerResult(
                        await self._load(epoch_id),
                        cycles_completed,
                    )

                # Fresh eligibility is mandatory before the first and every next cycle.
                await self._control.validate_execution_eligibility(epoch_id)

                epoch = await self._latest_owned(epoch_id, fence)
                if epoch.desired_state is not _Desired.RUNNING:
                    epoch = await self._settle_control_boundary(epoch, fence)
                    return OperationalPaperSessionRunWorkerResult(
                        epoch,
                        cycles_completed,
                    )

                if epoch.observed_state is not _Observed.RUNNING:
                    epoch = await self._mark_running(epoch_id, fence)

                await self._run_cycle_with_heartbeat(
                    epoch_id,
                    epoch.session_id,
                    fence,
                )
                cycles_completed += 1

                epoch = await self._latest_owned(epoch_id, fence)

                if epoch.desired_state is not _Desired.RUNNING:
                    epoch = await self._settle_control_boundary(epoch, fence)
                    return OperationalPaperSessionRunWorkerResult(
                        epoch,
                        cycles_completed,
                    )

                if max_cycles is not None and cycles_completed >= max_cycles:
                    return OperationalPaperSessionRunWorkerResult(
                        epoch,
                        cycles_completed,
                    )

        except runs.OperationalPaperSessionRunLeaseError:
            current = await self._repository.get(epoch_id)
            if current is None:
                raise
            return OperationalPaperSessionRunWorkerResult(
                current,
                cycles_completed,
                _Code.LEASE_LOST,
            )
        except runs.OperationalPaperSessionRunStateTransitionConflictError as error:
            code = self._failure_code_from_eligibility(error)
            failed = await self._fail_if_owned(epoch_id, code)
            return OperationalPaperSessionRunWorkerResult(
                failed,
                cycles_completed,
                code,
            )
        except MarketJobLockTimeoutError:
            code = _Code.LOCAL_RUNNER_BUSY
            failed = await self._fail_if_owned(epoch_id, code)
            return OperationalPaperSessionRunWorkerResult(
                failed,
                cycles_completed,
                code,
            )
        except PaperSessionDataUnavailableError:
            code = _Code.RAW_NOT_READY
            failed = await self._fail_if_owned(epoch_id, code)
            return OperationalPaperSessionRunWorkerResult(
                failed,
                cycles_completed,
                code,
            )
        except PaperSessionNotFoundError:
            code = _Code.CONFIG_UNAVAILABLE
            failed = await self._fail_if_owned(epoch_id, code)
            return OperationalPaperSessionRunWorkerResult(
                failed,
                cycles_completed,
                code,
            )
        except (
            InvalidPaperSessionError,
            PaperSessionConflictError,
            PaperSessionCorruptError,
            PaperSessionVerificationError,
        ):
            code = _Code.LOCAL_STATE_INVALID
            failed = await self._fail_if_owned(epoch_id, code)
            return OperationalPaperSessionRunWorkerResult(
                failed,
                cycles_completed,
                code,
            )
        except PaperTradingError:
            code = _Code.INTERNAL_ERROR
            failed = await self._fail_if_owned(epoch_id, code)
            return OperationalPaperSessionRunWorkerResult(
                failed,
                cycles_completed,
                code,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            code = _Code.INTERNAL_ERROR
            failed = await self._fail_if_owned(epoch_id, code)
            return OperationalPaperSessionRunWorkerResult(
                failed,
                cycles_completed,
                code,
            )

    async def _load(self, epoch_id: UUID) -> _Epoch:
        epoch = await self._repository.get(epoch_id)
        if epoch is None:
            raise runs.OperationalPaperSessionRunNotFoundError()
        return epoch

    async def _acquire_or_settle(self, epoch: _Epoch) -> _Epoch:
        now = self._now()

        if epoch.worker_claim is None:
            if epoch.desired_state is not _Desired.RUNNING:
                return await self._repository.settle_unclaimed(
                    epoch.epoch_id,
                    expected_record_version=epoch.record_version,
                    now=now,
                )
            return await self._repository.claim(
                epoch.epoch_id,
                expected_record_version=epoch.record_version,
                worker_id=self._worker_id,
                lease_expires_at=now + self._policy.lease_duration,
                now=now,
            )

        claim = epoch.worker_claim
        if claim.worker_id == self._worker_id and now < claim.lease_expires_at:
            return epoch

        if now >= claim.lease_expires_at:
            return await self._repository.recover(
                epoch.epoch_id,
                expected_record_version=epoch.record_version,
                worker_id=self._worker_id,
                lease_expires_at=now + self._policy.lease_duration,
                now=now,
            )

        raise runs.OperationalPaperSessionRunLeaseError()

    async def _prepare_running(self, epoch: _Epoch, fence: int) -> _Epoch:
        if epoch.desired_state is not _Desired.RUNNING:
            return await self._settle_control_boundary(epoch, fence)

        if epoch.observed_state in {
            _Observed.PENDING,
            _Observed.PAUSED,
            _Observed.RECOVERING,
        }:
            epoch = await self._mark_starting(epoch.epoch_id, fence)

        if epoch.observed_state is _Observed.STARTING:
            await self._control.validate_execution_eligibility(epoch.epoch_id)
            latest = await self._latest_owned(epoch.epoch_id, fence)
            if latest.desired_state is not _Desired.RUNNING:
                return await self._settle_control_boundary(latest, fence)
            return await self._mark_running(epoch.epoch_id, fence)

        if epoch.observed_state is _Observed.RUNNING:
            return epoch

        raise runs.OperationalPaperSessionRunStateTransitionConflictError(
            details={"failure_code": _Code.LOCAL_STATE_INVALID.value}
        )

    async def _run_cycle_with_heartbeat(
        self,
        epoch_id: UUID,
        session_id: str,
        fence: int,
    ) -> None:
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_loop(epoch_id, fence, stop))
        cycle = asyncio.create_task(asyncio.to_thread(self._paper.run_once, session_id))

        cancelled = False
        try:
            try:
                await asyncio.shield(cycle)
            except asyncio.CancelledError:
                cancelled = True
                # Paper execution is synchronous. Never abandon it mid-cycle.
                await asyncio.shield(cycle)
        finally:
            stop.set()

        heartbeat_error: BaseException | None = None
        if heartbeat.done():
            try:
                heartbeat.result()
            except BaseException as error:
                heartbeat_error = error
        else:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            except BaseException as error:
                heartbeat_error = error

        cycle_error: BaseException | None = None
        if cycle.done():
            try:
                cycle.result()
            except BaseException as error:
                cycle_error = error

        if cancelled:
            raise asyncio.CancelledError

        if heartbeat_error is not None:
            raise heartbeat_error

        if cycle_error is not None:
            raise cycle_error

    async def _heartbeat_loop(
        self,
        epoch_id: UUID,
        fence: int,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            await self._sleeper(self._policy.heartbeat_interval_seconds)
            if stop.is_set():
                return
            await self._renew_once(epoch_id, fence)

    async def _renew_once(self, epoch_id: UUID, fence: int) -> _Epoch:
        for _attempt in range(3):
            current = await self._latest_owned(epoch_id, fence)
            claim = self._require_own_claim(current, fence=fence)
            now = self._now()

            if now >= claim.lease_expires_at:
                raise runs.OperationalPaperSessionRunLeaseError()

            try:
                return await self._repository.renew(
                    epoch_id,
                    expected_record_version=current.record_version,
                    worker_id=self._worker_id,
                    fencing_token=fence,
                    lease_expires_at=now + self._policy.lease_duration,
                    now=now,
                )
            except runs.OperationalPaperSessionRunRecordVersionConflictError:
                continue

        await self._latest_owned(epoch_id, fence)
        raise runs.OperationalPaperSessionRunLeaseError()

    async def _latest_owned(self, epoch_id: UUID, fence: int) -> _Epoch:
        epoch = await self._load(epoch_id)
        self._require_own_claim(epoch, fence=fence)
        return epoch

    def _require_own_claim(
        self,
        epoch: _Epoch,
        *,
        fence: int | None = None,
    ) -> runs.OperationalPaperSessionRunWorkerClaim:
        claim = epoch.worker_claim
        if (
            claim is None
            or claim.worker_id != self._worker_id
            or (fence is not None and claim.fencing_token != fence)
        ):
            raise runs.OperationalPaperSessionRunLeaseError()
        return claim

    async def _mark_starting(self, epoch_id: UUID, fence: int) -> _Epoch:
        for _attempt in range(3):
            current = await self._latest_owned(epoch_id, fence)
            try:
                return await self._repository.mark_starting(
                    epoch_id,
                    expected_record_version=current.record_version,
                    worker_id=self._worker_id,
                    fencing_token=fence,
                    now=self._now(),
                )
            except runs.OperationalPaperSessionRunRecordVersionConflictError:
                continue

        current = await self._latest_owned(epoch_id, fence)

        if current.desired_state is not _Desired.RUNNING:
            return await self._settle_control_boundary(current, fence)

        if current.observed_state in {
            _Observed.STARTING,
            _Observed.RUNNING,
        }:
            return current

        raise runs.OperationalPaperSessionRunStateTransitionConflictError(
            details={"failure_code": _Code.LOCAL_STATE_INVALID.value}
        )

    async def _mark_running(self, epoch_id: UUID, fence: int) -> _Epoch:
        for _attempt in range(3):
            current = await self._latest_owned(epoch_id, fence)
            if current.desired_state is not _Desired.RUNNING:
                return await self._settle_control_boundary(current, fence)
            if current.observed_state is _Observed.RUNNING:
                return current
            try:
                return await self._repository.mark_running(
                    epoch_id,
                    expected_record_version=current.record_version,
                    worker_id=self._worker_id,
                    fencing_token=fence,
                    now=self._now(),
                )
            except runs.OperationalPaperSessionRunRecordVersionConflictError:
                continue

        current = await self._latest_owned(epoch_id, fence)

        if current.desired_state is not _Desired.RUNNING:
            return await self._settle_control_boundary(current, fence)

        if current.observed_state is _Observed.RUNNING:
            return current

        raise runs.OperationalPaperSessionRunStateTransitionConflictError(
            details={"failure_code": _Code.LOCAL_STATE_INVALID.value}
        )

    async def _settle_control_boundary(
        self,
        epoch: _Epoch,
        fence: int,
    ) -> _Epoch:
        if epoch.desired_state is _Desired.PAUSED:
            if epoch.observed_state is _Observed.PAUSED:
                return epoch
            for _attempt in range(3):
                current = await self._latest_owned(epoch.epoch_id, fence)
                if current.desired_state is not _Desired.PAUSED:
                    return await self._settle_control_boundary(current, fence)
                try:
                    return await self._repository.settle_paused(
                        epoch.epoch_id,
                        expected_record_version=current.record_version,
                        worker_id=self._worker_id,
                        fencing_token=fence,
                        now=self._now(),
                    )
                except runs.OperationalPaperSessionRunRecordVersionConflictError:
                    continue

            current = await self._latest_owned(epoch.epoch_id, fence)

            if current.desired_state is not _Desired.PAUSED:
                return await self._settle_control_boundary(current, fence)

            if current.observed_state is _Observed.PAUSED:
                return current

            raise runs.OperationalPaperSessionRunStateTransitionConflictError(
                details={"failure_code": _Code.LOCAL_STATE_INVALID.value}
            )

        if epoch.desired_state is _Desired.STOPPED:
            current = await self._latest_owned(epoch.epoch_id, fence)

            if current.observed_state in {
                _Observed.STARTING,
                _Observed.RUNNING,
                _Observed.RECOVERING,
            }:
                for _attempt in range(3):
                    current = await self._latest_owned(
                        epoch.epoch_id,
                        fence,
                    )

                    if current.desired_state is not _Desired.STOPPED:
                        return await self._settle_control_boundary(
                            current,
                            fence,
                        )

                    if current.observed_state in {
                        _Observed.STOPPING,
                        _Observed.STOPPED,
                    }:
                        break

                    if current.observed_state not in {
                        _Observed.STARTING,
                        _Observed.RUNNING,
                        _Observed.RECOVERING,
                    }:
                        break

                    try:
                        current = await self._repository.mark_stopping(
                            epoch.epoch_id,
                            expected_record_version=current.record_version,
                            worker_id=self._worker_id,
                            fencing_token=fence,
                            now=self._now(),
                        )
                        break
                    except runs.OperationalPaperSessionRunRecordVersionConflictError:
                        continue

                current = await self._latest_owned(
                    epoch.epoch_id,
                    fence,
                )

                if current.desired_state is not _Desired.STOPPED:
                    return await self._settle_control_boundary(
                        current,
                        fence,
                    )

                if current.observed_state in {
                    _Observed.STARTING,
                    _Observed.RUNNING,
                }:
                    raise runs.OperationalPaperSessionRunStateTransitionConflictError(
                        details={"failure_code": _Code.LOCAL_STATE_INVALID.value}
                    )

            if current.observed_state is _Observed.STOPPED:
                return current

            for _attempt in range(3):
                current = await self._latest_owned(
                    epoch.epoch_id,
                    fence,
                )

                if current.desired_state is not _Desired.STOPPED:
                    return await self._settle_control_boundary(
                        current,
                        fence,
                    )

                if current.observed_state is _Observed.STOPPED:
                    return current

                try:
                    return await self._repository.settle_stopped(
                        epoch.epoch_id,
                        expected_record_version=current.record_version,
                        worker_id=self._worker_id,
                        fencing_token=fence,
                        now=self._now(),
                    )
                except runs.OperationalPaperSessionRunRecordVersionConflictError:
                    continue

            current = await self._latest_owned(
                epoch.epoch_id,
                fence,
            )

            if current.observed_state is _Observed.STOPPED:
                return current

            raise runs.OperationalPaperSessionRunStateTransitionConflictError(
                details={"failure_code": _Code.LOCAL_STATE_INVALID.value}
            )

        return epoch

    async def _fail_if_owned(self, epoch_id: UUID, code: _Code) -> _Epoch:
        current = await self._load(epoch_id)
        claim = current.worker_claim

        if claim is None or claim.worker_id != self._worker_id:
            return current

        fence = claim.fencing_token

        for _attempt in range(3):
            current = await self._load(epoch_id)
            claim = current.worker_claim
            if claim is None or claim.worker_id != self._worker_id or claim.fencing_token != fence:
                return current

            try:
                return await self._repository.fail_claimed(
                    epoch_id,
                    expected_record_version=current.record_version,
                    worker_id=self._worker_id,
                    fencing_token=fence,
                    code=code,
                    now=self._now(),
                )
            except runs.OperationalPaperSessionRunRecordVersionConflictError:
                continue
            except runs.OperationalPaperSessionRunLeaseError:
                return await self._load(epoch_id)

        return await self._load(epoch_id)

    @staticmethod
    def _failure_code_from_eligibility(
        error: runs.OperationalPaperSessionRunStateTransitionConflictError,
    ) -> _Code:
        value = (error.details or {}).get("failure_code")
        if isinstance(value, str):
            try:
                return _Code(value)
            except ValueError:
                pass
        return _Code.INTERNAL_ERROR

    @staticmethod
    def _terminal(epoch: _Epoch) -> bool:
        return epoch.observed_state in {
            _Observed.STOPPED,
            _Observed.FAILED,
        }

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("O relógio do worker paper é inválido.")
        return value.astimezone(UTC)
