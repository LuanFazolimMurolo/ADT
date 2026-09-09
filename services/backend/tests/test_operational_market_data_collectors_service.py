"""Gate 2D application/control tests for operational market-data collectors."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import app.operational_market_data_collectors as collectors
from app.database import Database
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
)
from app.services.operational_market_data_collectors import (
    OperationalMarketDataCollectorService,
)

NOW = datetime(2026, 9, 9, 18, 0, tzinfo=UTC)


def _specification(
    *,
    interval_seconds: int = 60,
) -> collectors.OperationalMarketDataCollectorSpecification:
    return collectors.OperationalMarketDataCollectorSpecification(
        schema_version=1,
        collector_contract_version=1,
        scope=collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW,
        targets=(
            collectors.OperationalMarketDataCollectorTarget(
                symbol="BTC/USDT",
                timeframe="1m",
                bootstrap_candles=500,
            ),
            collectors.OperationalMarketDataCollectorTarget(
                symbol="ETH/USDT",
                timeframe="5m",
                bootstrap_candles=300,
            ),
        ),
        interval_seconds=interval_seconds,
        overlap_candles=2,
    )


def _command(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    command_type: collectors.OperationalMarketDataCollectorCommandType,
) -> collectors.OperationalMarketDataCollectorCommandIntent:
    return collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=command_type,
        expected_record_version=epoch.record_version,
    )


@pytest.fixture
def repository(
    database: Database,
) -> PostgresOperationalMarketDataCollectorRepository:
    return PostgresOperationalMarketDataCollectorRepository(database)


@pytest.fixture
def service(
    repository: PostgresOperationalMarketDataCollectorRepository,
) -> OperationalMarketDataCollectorService:
    return OperationalMarketDataCollectorService(repository=repository)


@pytest.mark.asyncio
async def test_start_persists_exact_specification_and_replays(
    service: OperationalMarketDataCollectorService,
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    specification = _specification()

    epoch = await service.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key="collector-service:start",
        requested_at=NOW,
    )

    assert epoch.specification == specification
    assert epoch.specification_checksum == (
        collectors.operational_market_data_collector_specification_checksum(specification)
    )
    assert epoch.scope is collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW
    assert epoch.desired_state is collectors.OperationalMarketDataCollectorDesiredState.RUNNING
    assert epoch.observed_state is collectors.OperationalMarketDataCollectorObservedState.PENDING
    assert epoch.record_version == 1

    assert await repository.get(epoch.epoch_id) == epoch

    replay = await service.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key="collector-service:start",
        requested_at=NOW + timedelta(seconds=10),
    )

    assert replay == epoch

    history = await service.list_commands(epoch.epoch_id)

    assert len(history) == 1
    assert history[0].command_type is collectors.OperationalMarketDataCollectorCommandType.START
    assert history[0].expected_record_version is None


@pytest.mark.asyncio
async def test_divergent_start_replay_conflicts_before_current_epoch_check(
    service: OperationalMarketDataCollectorService,
    auth_user_id: UUID,
) -> None:
    specification = _specification()

    await service.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key="collector-service:divergent",
        requested_at=NOW,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorIdempotencyConflictError):
        await service.start(
            replace(specification, interval_seconds=61),
            actor_id=auth_user_id,
            idempotency_key="collector-service:divergent",
            requested_at=NOW + timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_distinct_start_conflicts_with_current_scope(
    service: OperationalMarketDataCollectorService,
    auth_user_id: UUID,
) -> None:
    specification = _specification()

    original = await service.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key="collector-service:first",
        requested_at=NOW,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorCurrentEpochConflictError):
        await service.start(
            specification,
            actor_id=auth_user_id,
            idempotency_key="collector-service:second",
            requested_at=NOW + timedelta(seconds=1),
        )

    assert await service.get(original.epoch_id) == original


@pytest.mark.asyncio
async def test_terminal_exact_start_replay_and_new_epoch_with_new_key(
    service: OperationalMarketDataCollectorService,
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    specification = _specification()

    epoch = await service.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key="collector-service:terminal",
        requested_at=NOW,
    )

    stop_command = await service.stop(
        _command(
            epoch,
            collectors.OperationalMarketDataCollectorCommandType.STOP,
        ),
        actor_id=auth_user_id,
        idempotency_key="collector-service:terminal:stop",
        requested_at=NOW + timedelta(seconds=1),
    )

    assert (
        stop_command.desired_state is collectors.OperationalMarketDataCollectorDesiredState.STOPPED
    )

    stopped = await repository.settle_unclaimed(
        epoch.epoch_id,
        expected_record_version=stop_command.resulting_record_version,
        now=NOW + timedelta(seconds=2),
    )

    assert stopped.observed_state is collectors.OperationalMarketDataCollectorObservedState.STOPPED

    replay = await service.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key="collector-service:terminal",
        requested_at=NOW + timedelta(seconds=3),
    )

    assert replay == stopped

    next_epoch = await service.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key="collector-service:new-epoch",
        requested_at=NOW + timedelta(seconds=4),
    )

    assert next_epoch.epoch_id != epoch.epoch_id
    assert next_epoch.record_version == 1


@pytest.mark.asyncio
async def test_pause_resume_pause_before_physical_convergence(
    service: OperationalMarketDataCollectorService,
    auth_user_id: UUID,
) -> None:
    epoch = await service.start(
        _specification(),
        actor_id=auth_user_id,
        idempotency_key="collector-service:pre-convergence",
        requested_at=NOW,
    )

    pause = await service.pause(
        _command(
            epoch,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        ),
        actor_id=auth_user_id,
        idempotency_key="collector-service:pause:1",
        requested_at=NOW + timedelta(seconds=1),
    )

    paused_intent = await service.get(epoch.epoch_id)

    assert pause.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    assert (
        paused_intent.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    )
    assert (
        paused_intent.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.PENDING
    )

    resume = await service.resume(
        _command(
            paused_intent,
            collectors.OperationalMarketDataCollectorCommandType.RESUME,
        ),
        actor_id=auth_user_id,
        idempotency_key="collector-service:resume",
        requested_at=NOW + timedelta(seconds=2),
    )

    resumed_intent = await service.get(epoch.epoch_id)

    assert resume.desired_state is collectors.OperationalMarketDataCollectorDesiredState.RUNNING
    assert (
        resumed_intent.desired_state
        is collectors.OperationalMarketDataCollectorDesiredState.RUNNING
    )
    assert (
        resumed_intent.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.PENDING
    )

    pause_again = await service.pause(
        _command(
            resumed_intent,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        ),
        actor_id=auth_user_id,
        idempotency_key="collector-service:pause:2",
        requested_at=NOW + timedelta(seconds=3),
    )

    latest = await service.get(epoch.epoch_id)

    assert pause_again.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    assert latest.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    assert latest.observed_state is collectors.OperationalMarketDataCollectorObservedState.PENDING


@pytest.mark.asyncio
async def test_stop_from_paused_desired_state(
    service: OperationalMarketDataCollectorService,
    auth_user_id: UUID,
) -> None:
    epoch = await service.start(
        _specification(),
        actor_id=auth_user_id,
        idempotency_key="collector-service:stop-paused",
        requested_at=NOW,
    )

    await service.pause(
        _command(
            epoch,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        ),
        actor_id=auth_user_id,
        idempotency_key="collector-service:stop-paused:pause",
        requested_at=NOW + timedelta(seconds=1),
    )

    paused = await service.get(epoch.epoch_id)

    command = await service.stop(
        _command(
            paused,
            collectors.OperationalMarketDataCollectorCommandType.STOP,
        ),
        actor_id=auth_user_id,
        idempotency_key="collector-service:stop-paused:stop",
        requested_at=NOW + timedelta(seconds=2),
    )

    latest = await service.get(epoch.epoch_id)

    assert command.desired_state is collectors.OperationalMarketDataCollectorDesiredState.STOPPED
    assert latest.desired_state is collectors.OperationalMarketDataCollectorDesiredState.STOPPED
    assert latest.observed_state is collectors.OperationalMarketDataCollectorObservedState.PENDING


@pytest.mark.asyncio
async def test_command_method_rejects_wrong_kind_before_repository_mutation(
    service: OperationalMarketDataCollectorService,
    auth_user_id: UUID,
) -> None:
    epoch = await service.start(
        _specification(),
        actor_id=auth_user_id,
        idempotency_key="collector-service:wrong-kind",
        requested_at=NOW,
    )

    wrong = _command(
        epoch,
        collectors.OperationalMarketDataCollectorCommandType.RESUME,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
        await service.pause(
            wrong,
            actor_id=auth_user_id,
            idempotency_key="collector-service:wrong-kind:pause",
            requested_at=NOW + timedelta(seconds=1),
        )

    assert await service.get(epoch.epoch_id) == epoch
    assert len(await service.list_commands(epoch.epoch_id)) == 1


@pytest.mark.asyncio
async def test_invalid_service_input_fails_closed(
    service: OperationalMarketDataCollectorService,
    auth_user_id: UUID,
) -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        await service.start(  # type: ignore[arg-type]
            object(),
            actor_id=auth_user_id,
            idempotency_key="collector-service:invalid",
            requested_at=NOW,
        )

    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        await service.pause(  # type: ignore[arg-type]
            object(),
            actor_id=auth_user_id,
            idempotency_key="collector-service:invalid-command",
            requested_at=NOW,
        )


@pytest.mark.asyncio
async def test_get_and_list_commands_require_existing_epoch(
    service: OperationalMarketDataCollectorService,
) -> None:
    missing = uuid4()

    with pytest.raises(collectors.OperationalMarketDataCollectorNotFoundError):
        await service.get(missing)

    with pytest.raises(collectors.OperationalMarketDataCollectorNotFoundError):
        await service.list_commands(missing)


def test_service_module_is_control_plane_only() -> None:
    file_path = Path("services/backend/app/services/operational_market_data_collectors.py")

    source = file_path.read_text(encoding="utf-8")

    for forbidden in (
        "app.market_data.continuous",
        "ContinuousCollectionRunner",
        "ContinuousCollectionService",
        "DatasetLockManager",
        "flock",
        "Binance",
        "httpx",
        "requests",
        "asyncio.sleep",
        "time.sleep",
    ):
        assert forbidden not in source
