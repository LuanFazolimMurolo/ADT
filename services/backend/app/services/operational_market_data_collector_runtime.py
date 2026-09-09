"""Physical continuous-collector adapter for one operational collector cycle."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import app.operational_market_data_collectors as collectors
from app.market_data.continuous import (
    Clock,
    ContinuousCollectionPolicy,
    ContinuousCollectionRunner,
    ContinuousCollectionService,
    ContinuousCollectionState,
    ContinuousCollectionStateStore,
    ContinuousCollectionTarget,
    DatasetLeaseProvider,
    IncrementalExecutor,
    IncrementalPlanner,
    InstrumentLookup,
    StartupHook,
    collection_target_from_text,
)
from app.market_data.errors import (
    MarketDataInconsistencyError,
    MarketDataStorageError,
)
from app.market_data.locks import DatasetLockManager
from app.market_data.storage import ParquetCandleStore
from app.services.operational_market_data_collector_worker import (
    CycleStartupHook,
)

_Specification = collectors.OperationalMarketDataCollectorSpecification
_Scope = collectors.OperationalMarketDataCollectorScope


class _CollectionCycleNotDueError(Exception):
    """Internal control flow raised under the collector flock."""


class OperationalMarketDataCollectorRuntimeExecutor:
    """Map one frozen operational specification onto the existing collector."""

    def __init__(
        self,
        *,
        instruments: InstrumentLookup,
        history: DatasetLeaseProvider,
        planner: IncrementalPlanner,
        executor: IncrementalExecutor,
        store: ParquetCandleStore,
        state_store: ContinuousCollectionStateStore,
        lock_manager: DatasetLockManager,
        recovery_hook: StartupHook | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._instruments = instruments
        self._history = history
        self._planner = planner
        self._executor = executor
        self._store = store
        self._state_store = state_store
        self._lock_manager = lock_manager
        self._recovery_hook = recovery_hook
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute_cycle(
        self,
        specification: _Specification,
        *,
        startup_hook: CycleStartupHook,
    ) -> bool:
        """Execute one due complete cycle; return False when cadence is not due."""
        targets, policy = self._runtime_contract(specification)

        service = ContinuousCollectionService(
            instruments=self._instruments,
            history=self._history,
            planner=self._planner,
            executor=self._executor,
            store=self._store,
            policy=policy,
            clock=self._clock,
        )

        async def guarded_startup() -> None:
            # First revalidation occurs after the volume-wide flock is held.
            await startup_hook()

            previous = self._read_local_state()

            if (
                previous is not None
                and self._same_collection_contract(
                    previous,
                    targets,
                    policy,
                )
                and self._now() < previous.next_cycle_at
            ):
                raise _CollectionCycleNotDueError()

            # Preserve the existing collector's abandoned-job recovery
            # immediately before a due physical cycle.
            await self._run_recovery_hook()

            # Recovery/local inspection is outside PostgreSQL row locks and may
            # take time. Revalidate the operational claim/fence immediately
            # before allowing physical collection to begin.
            await startup_hook()

        runner = ContinuousCollectionRunner(
            service=service,
            state_store=self._state_store,
            lock_manager=self._lock_manager,
            clock=self._clock,
            startup_hook=guarded_startup,
        )

        try:
            await runner.run(
                targets,
                max_cycles=1,
            )
        except _CollectionCycleNotDueError:
            return False

        return True

    @staticmethod
    def _runtime_contract(
        specification: _Specification,
    ) -> tuple[
        tuple[ContinuousCollectionTarget, ...],
        ContinuousCollectionPolicy,
    ]:
        if not isinstance(
            specification,
            _Specification,
        ):
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()

        try:
            # Revalidate the complete frozen operational document before
            # translating it into the older physical collector contract.
            collectors.operational_market_data_collector_specification_checksum(specification)

            if specification.scope is not _Scope.BINANCE_SPOT_RAW:
                raise collectors.InvalidOperationalMarketDataCollectorSpecificationError()

            targets = tuple(
                collection_target_from_text(
                    f"{target.symbol}:{target.timeframe}",
                    bootstrap_candles=target.bootstrap_candles,
                )
                for target in specification.targets
            )

            policy = ContinuousCollectionPolicy(
                interval_seconds=specification.interval_seconds,
                overlap_candles=specification.overlap_candles,
                max_targets=len(targets),
            )
        except collectors.InvalidOperationalMarketDataCollectorSpecificationError:
            raise
        except Exception:
            raise collectors.InvalidOperationalMarketDataCollectorSpecificationError() from None

        return targets, policy

    def _read_local_state(
        self,
    ) -> ContinuousCollectionState | None:
        try:
            return self._state_store.read()
        except MarketDataStorageError:
            raise MarketDataInconsistencyError("O estado local do collector é inválido.") from None

    @staticmethod
    def _same_collection_contract(
        state: ContinuousCollectionState,
        targets: tuple[ContinuousCollectionTarget, ...],
        policy: ContinuousCollectionPolicy,
    ) -> bool:
        return (
            state.policy == policy and tuple(result.target for result in state.results) == targets
        )

    async def _run_recovery_hook(self) -> None:
        if self._recovery_hook is None:
            return

        result = self._recovery_hook()

        if inspect.isawaitable(result):
            await result

    def _now(self) -> datetime:
        value = self._clock()

        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise MarketDataInconsistencyError("O relógio do runtime collector é inválido.")

        return value.astimezone(UTC)
