"""Fenced worker for one operational market-data collector epoch."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import app.operational_market_data_collectors as collectors
from app.domain.errors import PersistenceError, PersistenceUnavailableError
from app.market_data.errors import (
    MarketDataInconsistencyError,
    MarketJobLockTimeoutError,
)
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
)

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
CycleStartupHook = Callable[[], Awaitable[None]]

_Code = collectors.OperationalMarketDataCollectorFailureCode
_Epoch = collectors.OperationalMarketDataCollectorEpoch
_Specification = collectors.OperationalMarketDataCollectorSpecification
_Desired = collectors.OperationalMarketDataCollectorDesiredState
_Observed = collectors.OperationalMarketDataCollectorObservedState


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OperationalMarketDataCollectorCycleExecutor(Protocol):
    """Execute at most one due physical cycle for one frozen specification."""

    async def execute_cycle(
        self,
        specification: _Specification,
        *,
        startup_hook: CycleStartupHook,
    ) -> bool:
        """Return True only when one complete physical cycle was persisted."""
        ...


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorWorkerPolicy:
    """Bounded PostgreSQL lease policy for one collector worker."""

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
            raise ValueError("A política de lease do collector é inválida.")

    @property
    def lease_duration(self) -> timedelta:
        return timedelta(seconds=self.lease_duration_seconds)


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorWorkerResult:
    """One bounded ownership interval for one collector epoch."""

    epoch: _Epoch
    cycles_completed: int
    exit_code: _Code | None = None


@dataclass(frozen=True, slots=True)
class _ControlBoundaryReachedError(Exception):
    epoch: _Epoch


class OperationalMarketDataCollectorWorker:
    """Execute cycles only while the exact PostgreSQL claim/fence remains valid."""

    def __init__(
        self,
        repository: PostgresOperationalMarketDataCollectorRepository,
        executor: OperationalMarketDataCollectorCycleExecutor,
        *,
        policy: OperationalMarketDataCollectorWorkerPolicy | None = None,
        worker_id: UUID | None = None,
        clock: Clock = _utc_now,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._policy = policy or OperationalMarketDataCollectorWorkerPolicy()
        self._worker_id = worker_id or uuid4()
        self._clock = clock
        self._sleeper = sleeper
        self._stop_requested = asyncio.Event()

    @property
    def worker_id(self) -> UUID:
        return self._worker_id

    def request_stop(self) -> None:
        """Request local shutdown without forging administrator STOP."""
        self._stop_requested.set()

    async def run_epoch(
        self,
        epoch_id: UUID,
        *,
        max_cycles: int | None = None,
    ) -> OperationalMarketDataCollectorWorkerResult:
        if max_cycles is not None and (isinstance(max_cycles, bool) or max_cycles <= 0):
            raise ValueError("O limite de ciclos do collector é inválido.")

        cycles_completed = 0
        epoch = await self._load(epoch_id)
        fallback = epoch

        if self._terminal(epoch):
            return OperationalMarketDataCollectorWorkerResult(epoch, 0)

        try:
            epoch = await self._acquire_or_settle(epoch)
            fallback = epoch

            if self._terminal(epoch) or epoch.observed_state is _Observed.PAUSED:
                return OperationalMarketDataCollectorWorkerResult(epoch, 0)

            claim = self._require_own_claim(epoch)
            fence = claim.fencing_token

            epoch = await self._prepare_running(epoch, fence)
            fallback = epoch

            if self._terminal(epoch) or epoch.observed_state is _Observed.PAUSED:
                return OperationalMarketDataCollectorWorkerResult(epoch, 0)

            specification = epoch.specification
            specification_checksum = epoch.specification_checksum

            while True:
                if self._stop_requested.is_set():
                    current = await self._load(epoch_id)
                    return OperationalMarketDataCollectorWorkerResult(
                        current,
                        cycles_completed,
                    )

                try:
                    epoch = await self._require_cycle_authority(
                        epoch_id,
                        fence,
                        specification_checksum,
                    )
                except _ControlBoundaryReachedError as boundary:
                    epoch = await self._settle_control_boundary(
                        boundary.epoch,
                        fence,
                    )
                    return OperationalMarketDataCollectorWorkerResult(
                        epoch,
                        cycles_completed,
                    )

                async def startup_hook() -> None:
                    await self._require_cycle_authority(
                        epoch_id,
                        fence,
                        specification_checksum,
                    )

                try:
                    executed = await self._run_cycle_with_heartbeat(
                        epoch_id,
                        specification,
                        fence,
                        startup_hook,
                    )
                except _ControlBoundaryReachedError as boundary:
                    epoch = await self._settle_control_boundary(
                        boundary.epoch,
                        fence,
                    )
                    return OperationalMarketDataCollectorWorkerResult(
                        epoch,
                        cycles_completed,
                    )

                if executed:
                    cycles_completed += 1

                epoch = await self._latest_owned(
                    epoch_id,
                    fence,
                    require_unexpired=True,
                )
                fallback = epoch

                if epoch.desired_state is not _Desired.RUNNING:
                    epoch = await self._settle_control_boundary(
                        epoch,
                        fence,
                    )
                    return OperationalMarketDataCollectorWorkerResult(
                        epoch,
                        cycles_completed,
                    )

                # Refresh the full lease at every completed execution
                # boundary. The physical cycle may consume most of the
                # original lease, especially when synchronous local I/O
                # temporarily starves the background heartbeat task.
                epoch = await self._renew_once(
                    epoch_id,
                    fence,
                )
                fallback = epoch

                if epoch.desired_state is not _Desired.RUNNING:
                    epoch = await self._settle_control_boundary(
                        epoch,
                        fence,
                    )
                    return OperationalMarketDataCollectorWorkerResult(
                        epoch,
                        cycles_completed,
                    )

                if max_cycles is not None and cycles_completed >= max_cycles:
                    return OperationalMarketDataCollectorWorkerResult(
                        epoch,
                        cycles_completed,
                    )

                # False means the runtime adapter intentionally did no physical
                # cycle, normally because the persisted local cadence is not due.
                # Return to the supervisor instead of busy-spinning.
                if not executed:
                    return OperationalMarketDataCollectorWorkerResult(
                        epoch,
                        cycles_completed,
                    )

        except collectors.OperationalMarketDataCollectorLeaseError:
            current = await self._safe_load(epoch_id, fallback)
            return OperationalMarketDataCollectorWorkerResult(
                current,
                cycles_completed,
                _Code.LEASE_LOST,
            )
        except PersistenceUnavailableError:
            return OperationalMarketDataCollectorWorkerResult(
                fallback,
                cycles_completed,
                _Code.DATABASE_UNAVAILABLE,
            )
        except collectors.InvalidOperationalMarketDataCollectorSpecificationError:
            failed = await self._fail_if_owned(
                epoch_id,
                _Code.COLLECTOR_SPEC_INVALID,
            )
            return OperationalMarketDataCollectorWorkerResult(
                failed,
                cycles_completed,
                _Code.COLLECTOR_SPEC_INVALID,
            )
        except MarketJobLockTimeoutError:
            failed = await self._fail_if_owned(
                epoch_id,
                _Code.LOCAL_COLLECTOR_BUSY,
            )
            return OperationalMarketDataCollectorWorkerResult(
                failed,
                cycles_completed,
                _Code.LOCAL_COLLECTOR_BUSY,
            )
        except MarketDataInconsistencyError:
            failed = await self._fail_if_owned(
                epoch_id,
                _Code.LOCAL_STATE_INVALID,
            )
            return OperationalMarketDataCollectorWorkerResult(
                failed,
                cycles_completed,
                _Code.LOCAL_STATE_INVALID,
            )
        except collectors.OperationalMarketDataCollectorStateTransitionConflictError as error:
            code = self._failure_code_from_transition(error)
            failed = await self._fail_if_owned(epoch_id, code)
            return OperationalMarketDataCollectorWorkerResult(
                failed,
                cycles_completed,
                code,
            )
        except asyncio.CancelledError:
            raise
        except PersistenceError:
            return OperationalMarketDataCollectorWorkerResult(
                fallback,
                cycles_completed,
                _Code.INTERNAL_ERROR,
            )
        except Exception:
            failed = await self._fail_if_owned(
                epoch_id,
                _Code.INTERNAL_ERROR,
            )
            return OperationalMarketDataCollectorWorkerResult(
                failed,
                cycles_completed,
                _Code.INTERNAL_ERROR,
            )

    async def _load(self, epoch_id: UUID) -> _Epoch:
        epoch = await self._repository.get(epoch_id)

        if epoch is None:
            raise collectors.OperationalMarketDataCollectorNotFoundError()

        return epoch

    async def _safe_load(
        self,
        epoch_id: UUID,
        fallback: _Epoch,
    ) -> _Epoch:
        try:
            return await self._load(epoch_id)
        except PersistenceError:
            return fallback

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

        if claim.worker_id == self._worker_id:
            if now < claim.lease_expires_at:
                return epoch

            # Never resurrect the expired identity. Rotate the local
            # worker identity first, then perform an ordinary fenced
            # recovery with the fresh identity.
            replacement_worker_id = uuid4()

            recovered = await self._repository.recover(
                epoch.epoch_id,
                expected_record_version=epoch.record_version,
                worker_id=replacement_worker_id,
                lease_expires_at=now + self._policy.lease_duration,
                now=now,
            )

            self._worker_id = replacement_worker_id
            return recovered

        if now >= claim.lease_expires_at:
            return await self._repository.recover(
                epoch.epoch_id,
                expected_record_version=epoch.record_version,
                worker_id=self._worker_id,
                lease_expires_at=now + self._policy.lease_duration,
                now=now,
            )

        raise collectors.OperationalMarketDataCollectorLeaseError()

    async def _prepare_running(
        self,
        epoch: _Epoch,
        fence: int,
    ) -> _Epoch:
        if epoch.desired_state is not _Desired.RUNNING:
            return await self._settle_control_boundary(
                epoch,
                fence,
            )

        if epoch.observed_state in {
            _Observed.PENDING,
            _Observed.PAUSED,
            _Observed.RECOVERING,
        }:
            epoch = await self._mark_starting(
                epoch.epoch_id,
                fence,
            )

        if epoch.observed_state is _Observed.STARTING:
            current = await self._latest_owned(
                epoch.epoch_id,
                fence,
                require_unexpired=True,
            )

            if current.desired_state is not _Desired.RUNNING:
                return await self._settle_control_boundary(
                    current,
                    fence,
                )

            return await self._mark_running(
                epoch.epoch_id,
                fence,
            )

        if epoch.observed_state is _Observed.RUNNING:
            return epoch

        raise collectors.OperationalMarketDataCollectorStateTransitionConflictError(
            details={
                "failure_code": _Code.LOCAL_STATE_INVALID.value,
            }
        )

    async def _require_cycle_authority(
        self,
        epoch_id: UUID,
        fence: int,
        specification_checksum: str,
    ) -> _Epoch:
        epoch = await self._latest_owned(
            epoch_id,
            fence,
            require_unexpired=True,
        )

        actual_checksum = collectors.operational_market_data_collector_specification_checksum(
            epoch.specification
        )

        if (
            epoch.specification_checksum != specification_checksum
            or actual_checksum != specification_checksum
        ):
            raise collectors.OperationalMarketDataCollectorStateTransitionConflictError(
                details={
                    "failure_code": _Code.LOCAL_STATE_INVALID.value,
                }
            )

        if epoch.desired_state is not _Desired.RUNNING:
            raise _ControlBoundaryReachedError(epoch)

        if epoch.observed_state is not _Observed.RUNNING:
            raise collectors.OperationalMarketDataCollectorStateTransitionConflictError(
                details={
                    "failure_code": _Code.LOCAL_STATE_INVALID.value,
                }
            )

        return epoch

    async def _run_cycle_with_heartbeat(
        self,
        epoch_id: UUID,
        specification: _Specification,
        fence: int,
        startup_hook: CycleStartupHook,
    ) -> bool:
        stop = asyncio.Event()

        heartbeat = asyncio.create_task(
            self._heartbeat_loop(
                epoch_id,
                fence,
                stop,
            )
        )

        cycle = asyncio.create_task(
            self._executor.execute_cycle(
                specification,
                startup_hook=startup_hook,
            )
        )

        cancelled = False

        try:
            try:
                await asyncio.shield(cycle)
            except asyncio.CancelledError:
                cancelled = True
                # Physical market-data execution is boundary-cooperative.
                # Never abandon one in-progress cycle midway.
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
        executed = False

        if cycle.done():
            try:
                executed = cycle.result()
            except BaseException as error:
                cycle_error = error

        if cancelled:
            raise asyncio.CancelledError

        if heartbeat_error is not None:
            raise heartbeat_error

        if cycle_error is not None:
            raise cycle_error

        return executed

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

            await self._renew_once(
                epoch_id,
                fence,
            )

    async def _renew_once(
        self,
        epoch_id: UUID,
        fence: int,
    ) -> _Epoch:
        for _attempt in range(3):
            current = await self._latest_owned(
                epoch_id,
                fence,
                require_unexpired=True,
            )

            now = self._now()

            claim = self._require_own_claim(
                current,
                fence=fence,
                require_unexpired=True,
            )

            if now < claim.heartbeat_at:
                raise collectors.OperationalMarketDataCollectorLeaseError()

            lease_expires_at = (
                now + self._policy.lease_duration
            )

            # Renewal is internally idempotent. A bounded cycle may
            # finish without advancing an injected/frozen test clock,
            # or another heartbeat may already provide equal-or-better
            # lease coverage. Do not submit a forbidden no-op mutation
            # to the strict domain/repository contract.
            if (
                now == claim.heartbeat_at
                or lease_expires_at
                <= claim.lease_expires_at
            ):
                return current

            try:
                return await self._repository.renew(
                    epoch_id,
                    expected_record_version=current.record_version,
                    worker_id=self._worker_id,
                    fencing_token=fence,
                    lease_expires_at=lease_expires_at,
                    now=now,
                )
            except collectors.OperationalMarketDataCollectorRecordVersionConflictError:
                continue

        await self._latest_owned(
            epoch_id,
            fence,
            require_unexpired=True,
        )

        raise collectors.OperationalMarketDataCollectorLeaseError()

    async def _latest_owned(
        self,
        epoch_id: UUID,
        fence: int,
        *,
        require_unexpired: bool = False,
    ) -> _Epoch:
        epoch = await self._load(epoch_id)

        self._require_own_claim(
            epoch,
            fence=fence,
            require_unexpired=require_unexpired,
        )

        return epoch

    def _require_own_claim(
        self,
        epoch: _Epoch,
        *,
        fence: int | None = None,
        require_unexpired: bool = False,
    ) -> collectors.OperationalMarketDataCollectorWorkerClaim:
        claim = epoch.worker_claim

        if (
            claim is None
            or claim.worker_id != self._worker_id
            or (fence is not None and claim.fencing_token != fence)
            or (require_unexpired and self._now() >= claim.lease_expires_at)
        ):
            raise collectors.OperationalMarketDataCollectorLeaseError()

        return claim

    async def _mark_starting(
        self,
        epoch_id: UUID,
        fence: int,
    ) -> _Epoch:
        for _attempt in range(3):
            current = await self._latest_owned(
                epoch_id,
                fence,
                require_unexpired=True,
            )

            if current.desired_state is not _Desired.RUNNING:
                return await self._settle_control_boundary(
                    current,
                    fence,
                )

            if current.observed_state in {
                _Observed.STARTING,
                _Observed.RUNNING,
            }:
                return current

            try:
                return await self._repository.mark_starting(
                    epoch_id,
                    expected_record_version=current.record_version,
                    worker_id=self._worker_id,
                    fencing_token=fence,
                    now=self._now(),
                )
            except collectors.OperationalMarketDataCollectorRecordVersionConflictError:
                continue

        current = await self._latest_owned(
            epoch_id,
            fence,
            require_unexpired=True,
        )

        if current.desired_state is not _Desired.RUNNING:
            return await self._settle_control_boundary(
                current,
                fence,
            )

        if current.observed_state in {
            _Observed.STARTING,
            _Observed.RUNNING,
        }:
            return current

        raise collectors.OperationalMarketDataCollectorStateTransitionConflictError(
            details={
                "failure_code": _Code.LOCAL_STATE_INVALID.value,
            }
        )

    async def _mark_running(
        self,
        epoch_id: UUID,
        fence: int,
    ) -> _Epoch:
        for _attempt in range(3):
            current = await self._latest_owned(
                epoch_id,
                fence,
                require_unexpired=True,
            )

            if current.desired_state is not _Desired.RUNNING:
                return await self._settle_control_boundary(
                    current,
                    fence,
                )

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
            except collectors.OperationalMarketDataCollectorRecordVersionConflictError:
                continue

        current = await self._latest_owned(
            epoch_id,
            fence,
            require_unexpired=True,
        )

        if current.desired_state is not _Desired.RUNNING:
            return await self._settle_control_boundary(
                current,
                fence,
            )

        if current.observed_state is _Observed.RUNNING:
            return current

        raise collectors.OperationalMarketDataCollectorStateTransitionConflictError(
            details={
                "failure_code": _Code.LOCAL_STATE_INVALID.value,
            }
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
                current = await self._latest_owned(
                    epoch.epoch_id,
                    fence,
                )

                if current.desired_state is not _Desired.PAUSED:
                    return await self._settle_control_boundary(
                        current,
                        fence,
                    )

                if current.observed_state is _Observed.PAUSED:
                    return current

                try:
                    return await self._repository.settle_paused(
                        epoch.epoch_id,
                        expected_record_version=current.record_version,
                        worker_id=self._worker_id,
                        fencing_token=fence,
                        now=self._now(),
                    )
                except collectors.OperationalMarketDataCollectorRecordVersionConflictError:
                    continue

            current = await self._latest_owned(
                epoch.epoch_id,
                fence,
            )

            if current.desired_state is not _Desired.PAUSED:
                return await self._settle_control_boundary(
                    current,
                    fence,
                )

            if current.observed_state is _Observed.PAUSED:
                return current

            raise collectors.OperationalMarketDataCollectorStateTransitionConflictError(
                details={
                    "failure_code": _Code.LOCAL_STATE_INVALID.value,
                }
            )

        if epoch.desired_state is _Desired.STOPPED:
            current = await self._latest_owned(
                epoch.epoch_id,
                fence,
            )

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

                    try:
                        current = await self._repository.mark_stopping(
                            epoch.epoch_id,
                            expected_record_version=current.record_version,
                            worker_id=self._worker_id,
                            fencing_token=fence,
                            now=self._now(),
                        )
                        break
                    except collectors.OperationalMarketDataCollectorRecordVersionConflictError:
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
                except collectors.OperationalMarketDataCollectorRecordVersionConflictError:
                    continue

            current = await self._latest_owned(
                epoch.epoch_id,
                fence,
            )

            if current.observed_state is _Observed.STOPPED:
                return current

            raise collectors.OperationalMarketDataCollectorStateTransitionConflictError(
                details={
                    "failure_code": _Code.LOCAL_STATE_INVALID.value,
                }
            )

        return epoch

    async def _fail_if_owned(
        self,
        epoch_id: UUID,
        code: _Code,
    ) -> _Epoch:
        current = await self._load(epoch_id)

        if self._terminal(current):
            return current

        claim = current.worker_claim

        if claim is None:
            for _attempt in range(3):
                current = await self._load(epoch_id)

                if self._terminal(current):
                    return current

                if current.worker_claim is not None:
                    break

                try:
                    return await self._repository.fail_unclaimed(
                        epoch_id,
                        expected_record_version=current.record_version,
                        failure_code=code,
                        now=self._now(),
                    )
                except collectors.OperationalMarketDataCollectorRecordVersionConflictError:
                    continue

            current = await self._load(epoch_id)
            claim = current.worker_claim

            if claim is None:
                return current

        if claim.worker_id != self._worker_id:
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
                    failure_code=code,
                    now=self._now(),
                )
            except collectors.OperationalMarketDataCollectorRecordVersionConflictError:
                continue
            except collectors.OperationalMarketDataCollectorLeaseError:
                return await self._load(epoch_id)

        return await self._load(epoch_id)

    @staticmethod
    def _failure_code_from_transition(
        error: collectors.OperationalMarketDataCollectorStateTransitionConflictError,
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
            raise ValueError("O relógio do worker collector é inválido.")

        return value.astimezone(UTC)
