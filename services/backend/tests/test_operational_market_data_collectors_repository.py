"""Adversarial PostgreSQL collector repository and concurrency contract tests."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

import app.operational_market_data_collectors as collectors
import app.repositories.operational_market_data_collectors as repository_module
from app.database import Database
from app.database.pool import DatabaseConnection
from app.domain.errors import PersistenceError, PersistenceUnavailableError
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
    operational_market_data_collector_command_from_row,
    operational_market_data_collector_epoch_from_row,
)
from tests.postgres_support import add_auth_user
from tests.test_operational_market_data_collectors_migration import (
    START_AT,
    _command_row,
    _epoch_row,
    _specification,
)


@pytest.fixture
def specification() -> collectors.OperationalMarketDataCollectorSpecification:
    return _specification()


@pytest.fixture
def repository(database: Database) -> PostgresOperationalMarketDataCollectorRepository:
    return PostgresOperationalMarketDataCollectorRepository(database)


@pytest_asyncio.fixture
async def pending(
    repository: PostgresOperationalMarketDataCollectorRepository,
    specification: collectors.OperationalMarketDataCollectorSpecification,
    auth_user_id: UUID,
) -> collectors.OperationalMarketDataCollectorEpoch:
    return await repository.start(
        specification, actor_id=auth_user_id, idempotency_key="start:1", now=START_AT
    )


@pytest_asyncio.fixture
async def claimed(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> collectors.OperationalMarketDataCollectorEpoch:
    return await repository.claim(
        pending.epoch_id,
        expected_record_version=1,
        worker_id=uuid4(),
        now=START_AT + timedelta(seconds=1),
        lease_expires_at=START_AT + timedelta(seconds=61),
    )


def _intent(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    kind: str,
) -> collectors.OperationalMarketDataCollectorCommandIntent:
    return collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=collectors.OperationalMarketDataCollectorCommandType(kind),
        expected_record_version=epoch.record_version,
    )


async def _command(
    repository: PostgresOperationalMarketDataCollectorRepository,
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    kind: str,
) -> collectors.OperationalMarketDataCollectorEpoch:
    await repository.request_command(
        _intent(epoch, kind),
        actor_id=epoch.start_requested_by,
        idempotency_key=f"{kind}:{epoch.record_version}",
        now=START_AT + timedelta(seconds=epoch.record_version + 2),
    )
    result = await repository.get(epoch.epoch_id)
    assert result is not None
    return result


@pytest.mark.asyncio
async def test_start_exact_replay_conflicts_and_history(
    repository: PostgresOperationalMarketDataCollectorRepository,
    specification: collectors.OperationalMarketDataCollectorSpecification,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    auth_user_id: UUID,
) -> None:
    assert await repository.get(pending.epoch_id) == pending
    assert await repository.get_current_for_scope(pending.scope) == pending
    assert (
        await repository.start(
            specification,
            actor_id=auth_user_id,
            idempotency_key="start:1",
            now=START_AT + timedelta(seconds=5),
        )
        == pending
    )
    divergent = replace(specification, interval_seconds=61)
    with pytest.raises(collectors.OperationalMarketDataCollectorIdempotencyConflictError):
        await repository.start(
            divergent, actor_id=auth_user_id, idempotency_key="start:1", now=START_AT
        )
    with pytest.raises(collectors.OperationalMarketDataCollectorCurrentEpochConflictError):
        await repository.start(
            specification, actor_id=auth_user_id, idempotency_key="start:2", now=START_AT
        )
    commands = await repository.list_commands(pending.epoch_id)
    assert len(commands) == 1
    start = commands[0]
    assert start.command_type is collectors.OperationalMarketDataCollectorCommandType.START
    assert start.expected_record_version is None
    assert start.resulting_record_version == 1
    assert start.intent_fingerprint == pending.start_intent_fingerprint
    assert start.requested_at == pending.start_requested_at
    assert await repository.get(uuid4()) is None
    assert await repository.list_commands(uuid4()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [True, False], ids=["same-intent", "distinct-intent"])
async def test_concurrent_starts(
    repository: PostgresOperationalMarketDataCollectorRepository,
    specification: collectors.OperationalMarketDataCollectorSpecification,
    auth_user_id: UUID,
    same_key: bool,
) -> None:
    for index in range(2):
        assert (
            await repository.resolve_start_replay(
                collectors.OperationalMarketDataCollectorStartIntent(
                    specification_checksum=collectors.operational_market_data_collector_specification_checksum(
                        specification
                    ),
                ),
                actor_id=auth_user_id,
                idempotency_key="race" if same_key else f"race:{index}",
            )
            is None
        )
    results = await asyncio.gather(
        *[
            repository.start(
                specification,
                actor_id=auth_user_id,
                idempotency_key="race" if same_key else f"race:{index}",
                now=START_AT,
            )
            for index in range(2)
        ],
        return_exceptions=True,
    )
    successes = [
        result
        for result in results
        if isinstance(result, collectors.OperationalMarketDataCollectorEpoch)
    ]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(successes) == (2 if same_key else 1)
    assert len({epoch.epoch_id for epoch in successes}) == 1
    if same_key:
        assert successes[0] == successes[1]
        assert not failures
    else:
        assert len(failures) == 1
        assert isinstance(
            failures[0], collectors.OperationalMarketDataCollectorCurrentEpochConflictError
        )
    assert await repository.get_current_for_scope(specification.scope) == successes[0]
    assert len(await repository.list_commands(successes[0].epoch_id)) == 1


@pytest.mark.asyncio
async def test_command_replay_after_later_mutation_and_divergent_keys(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    intent = _intent(pending, "PAUSE")
    command = await repository.request_command(
        intent, actor_id=pending.start_requested_by, idempotency_key="pause", now=START_AT
    )
    paused = await repository.get(pending.epoch_id)
    assert paused is not None
    assert paused.record_version == 2
    settled = await repository.settle_unclaimed(
        paused.epoch_id, expected_record_version=2, now=START_AT + timedelta(seconds=1)
    )
    assert settled.record_version == 3
    assert (
        await repository.request_command(
            intent, actor_id=pending.start_requested_by, idempotency_key="pause", now=START_AT
        )
        == command
    )
    assert await repository.get(pending.epoch_id) == settled
    with pytest.raises(collectors.OperationalMarketDataCollectorIdempotencyConflictError):
        await repository.request_command(
            _intent(settled, "RESUME"),
            actor_id=pending.start_requested_by,
            idempotency_key="pause",
            now=START_AT,
        )
    with pytest.raises(collectors.OperationalMarketDataCollectorRecordVersionConflictError):
        await repository.request_command(
            intent, actor_id=pending.start_requested_by, idempotency_key="new", now=START_AT
        )


@pytest.mark.asyncio
async def test_command_actor_scope_and_concurrent_replay(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    database_url: str,
) -> None:
    intent = _intent(pending, "PAUSE")
    results = await asyncio.gather(
        *[
            repository.request_command(
                intent, actor_id=pending.start_requested_by, idempotency_key="shared", now=START_AT
            )
            for _ in range(2)
        ]
    )
    assert results[0] == results[1]
    current = await repository.get(pending.epoch_id)
    assert current is not None
    assert current.record_version == 2
    other = uuid4()
    with psycopg.connect(database_url) as connection:
        add_auth_user(connection, other)
    stop = await repository.request_command(
        _intent(current, "STOP"), actor_id=other, idempotency_key="shared", now=START_AT
    )
    assert stop.actor_id == other
    assert stop.idempotency_key == results[0].idempotency_key
    assert len(await repository.list_commands(current.epoch_id)) == 3


@pytest.mark.asyncio
async def test_competing_claimers_have_exactly_one_winner(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    workers = [uuid4(), uuid4()]
    results = await asyncio.gather(
        *[
            repository.claim(
                pending.epoch_id,
                expected_record_version=1,
                worker_id=worker,
                now=START_AT + timedelta(seconds=1),
                lease_expires_at=START_AT + timedelta(seconds=61),
            )
            for worker in workers
        ],
        return_exceptions=True,
    )
    winners = [
        result
        for result in results
        if isinstance(result, collectors.OperationalMarketDataCollectorEpoch)
    ]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(winners) == len(failures) == 1
    assert isinstance(
        failures[0], collectors.OperationalMarketDataCollectorRecordVersionConflictError
    )
    winner = winners[0]
    assert winner.worker_claim is not None
    assert winner.worker_claim.worker_id in workers
    assert winner.record_version == 2
    assert winner.fencing_token == 1
    assert winner.observed_state.value == "STARTING"
    assert await repository.get(winner.epoch_id) == winner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["worker", "fence", "version", "equal", "before", "expiry", "after-expiry", "nonextending"],
)
async def test_renew_rejects_invalid_capabilities_and_times(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
    case: str,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    worker = uuid4() if case == "worker" else claim.worker_id
    fence = 2 if case == "fence" else 1
    version = 1 if case == "version" else 2
    seconds = {"equal": 1, "before": 0, "expiry": 61, "after-expiry": 62}.get(case, 2)
    expiry = claim.lease_expires_at if case == "nonextending" else START_AT + timedelta(seconds=80)
    error = (
        collectors.OperationalMarketDataCollectorRecordVersionConflictError
        if case == "version"
        else collectors.OperationalMarketDataCollectorLeaseError
    )
    with pytest.raises(error):
        await repository.renew(
            claimed.epoch_id,
            expected_record_version=version,
            worker_id=worker,
            fencing_token=fence,
            now=START_AT + timedelta(seconds=seconds),
            lease_expires_at=expiry,
        )
    assert await repository.get(claimed.epoch_id) == claimed


@pytest.mark.asyncio
async def test_valid_renew_hydrates_domain_result(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    now = START_AT + timedelta(seconds=2)
    expires = START_AT + timedelta(seconds=70)
    expected = collectors.renew_operational_market_data_collector_worker_claim(
        claimed,
        worker_id=claim.worker_id,
        fencing_token=1,
        heartbeat_at=now,
        lease_expires_at=expires,
    )
    actual = await repository.renew(
        claimed.epoch_id,
        expected_record_version=2,
        worker_id=claim.worker_id,
        fencing_token=1,
        now=now,
        lease_expires_at=expires,
    )
    assert actual == expected == await repository.get(claimed.epoch_id)


@pytest.mark.asyncio
async def test_recovery_expiry_and_race(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        await repository.recover(
            claimed.epoch_id,
            expected_record_version=2,
            worker_id=uuid4(),
            now=START_AT + timedelta(seconds=60),
            lease_expires_at=START_AT + timedelta(seconds=120),
        )
    workers = [uuid4(), uuid4()]
    results = await asyncio.gather(
        *[
            repository.recover(
                claimed.epoch_id,
                expected_record_version=2,
                worker_id=worker,
                now=START_AT + timedelta(seconds=61),
                lease_expires_at=START_AT + timedelta(seconds=121),
            )
            for worker in workers
        ],
        return_exceptions=True,
    )
    winners = [
        result
        for result in results
        if isinstance(result, collectors.OperationalMarketDataCollectorEpoch)
    ]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(winners) == len(failures) == 1
    assert isinstance(
        failures[0], collectors.OperationalMarketDataCollectorRecordVersionConflictError
    )
    winner = winners[0]
    assert winner.worker_claim is not None
    assert winner.worker_claim.worker_id in workers
    expected = collectors.recover_operational_market_data_collector_epoch(
        claimed,
        worker_id=winner.worker_claim.worker_id,
        recovered_at=START_AT + timedelta(seconds=61),
        lease_expires_at=START_AT + timedelta(seconds=121),
    )
    assert winner == expected == await repository.get_current_for_scope(claimed.scope)
    assert winner.epoch_id == claimed.epoch_id
    assert (winner.observed_state.value, winner.fencing_token, winner.record_version) == (
        "RECOVERING",
        2,
        3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "renew",
        "mark_starting",
        "mark_running",
        "mark_stopping",
        "settle_paused",
        "settle_stopped",
        "fail_claimed",
    ],
)
@pytest.mark.parametrize("current_version", [False, True], ids=["stale-version", "learned-version"])
async def test_aba_old_worker_cannot_mutate_after_recovery(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
    action: str,
    current_version: bool,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    recovered = await repository.recover(
        claimed.epoch_id,
        expected_record_version=2,
        worker_id=uuid4(),
        now=START_AT + timedelta(seconds=61),
        lease_expires_at=START_AT + timedelta(seconds=121),
    )
    version = recovered.record_version if current_version else claimed.record_version
    now = START_AT + timedelta(seconds=62)
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        if action == "renew":
            await repository.renew(
                claimed.epoch_id,
                expected_record_version=version,
                worker_id=claim.worker_id,
                fencing_token=1,
                now=now,
                lease_expires_at=START_AT + timedelta(seconds=122),
            )
        elif action == "fail_claimed":
            await repository.fail_claimed(
                claimed.epoch_id,
                expected_record_version=version,
                worker_id=claim.worker_id,
                fencing_token=1,
                now=now,
                failure_code=collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR,
            )
        else:
            methods = {
                "mark_starting": repository.mark_starting,
                "mark_running": repository.mark_running,
                "mark_stopping": repository.mark_stopping,
                "settle_paused": repository.settle_paused,
                "settle_stopped": repository.settle_stopped,
            }
            await methods[action](
                claimed.epoch_id,
                expected_record_version=version,
                worker_id=claim.worker_id,
                fencing_token=1,
                now=now,
            )
    assert await repository.get(claimed.epoch_id) == recovered


@pytest.mark.asyncio
async def test_lifecycle_hydration_history_and_later_start(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
    specification: collectors.OperationalMarketDataCollectorSpecification,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    running = await repository.mark_running(
        claimed.epoch_id,
        expected_record_version=2,
        worker_id=claim.worker_id,
        fencing_token=1,
        now=START_AT + timedelta(seconds=2),
    )
    assert running == collectors.mark_operational_market_data_collector_epoch_running(
        claimed,
        worker_id=claim.worker_id,
        fencing_token=1,
        observed_at=START_AT + timedelta(seconds=2),
    )
    requested = await _command(repository, running, "PAUSE")
    paused = await repository.settle_paused(
        requested.epoch_id,
        expected_record_version=requested.record_version,
        worker_id=claim.worker_id,
        fencing_token=1,
        now=START_AT + timedelta(seconds=10),
    )
    assert paused == collectors.settle_operational_market_data_collector_epoch_paused(
        requested,
        worker_id=claim.worker_id,
        fencing_token=1,
        observed_at=START_AT + timedelta(seconds=10),
    )
    assert paused == await repository.get(paused.epoch_id)
    resumed = await _command(repository, paused, "RESUME")
    reclaimed = await repository.claim(
        resumed.epoch_id,
        expected_record_version=resumed.record_version,
        worker_id=claim.worker_id,
        now=START_AT + timedelta(seconds=11),
        lease_expires_at=START_AT + timedelta(seconds=71),
    )
    assert reclaimed.fencing_token == 2
    stop = await _command(repository, reclaimed, "STOP")
    stopping = await repository.mark_stopping(
        stop.epoch_id,
        expected_record_version=stop.record_version,
        worker_id=claim.worker_id,
        fencing_token=2,
        now=START_AT + timedelta(seconds=12),
    )
    stopped = await repository.settle_stopped(
        stop.epoch_id,
        expected_record_version=stopping.record_version,
        worker_id=claim.worker_id,
        fencing_token=2,
        now=START_AT + timedelta(seconds=13),
    )
    assert stopped == collectors.settle_operational_market_data_collector_epoch_stopped(
        stopping,
        worker_id=claim.worker_id,
        fencing_token=2,
        observed_at=START_AT + timedelta(seconds=13),
    )
    assert stopped == await repository.get(stopped.epoch_id)
    assert await repository.get_current_for_scope(stopped.scope) is None
    history = await repository.list_commands(stopped.epoch_id)
    assert [item.command_type.value for item in history] == ["START", "PAUSE", "RESUME", "STOP"]
    assert [item.resulting_record_version for item in history] == [1, 4, 6, 8]
    assert await repository.list_commands(stopped.epoch_id, limit=2, offset=1) == history[1:3]
    assert (
        await repository.start(
            specification,
            actor_id=stopped.start_requested_by,
            idempotency_key="start:1",
            now=START_AT,
        )
        == stopped
    )
    later = await repository.start(
        specification,
        actor_id=stopped.start_requested_by,
        idempotency_key="start:later",
        now=START_AT + timedelta(seconds=14),
    )
    assert later.epoch_id != stopped.epoch_id
    assert later.specification == stopped.specification
    assert await repository.list_commands(stopped.epoch_id) == history


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [True, False], ids=["claimed", "unclaimed"])
async def test_failure_round_trip_and_version_conflict(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    owned: bool,
) -> None:
    now = START_AT + timedelta(seconds=2)
    code = collectors.OperationalMarketDataCollectorFailureCode.LOCAL_STATE_INVALID
    if owned:
        worker = uuid4()
        current = await repository.claim(
            pending.epoch_id,
            expected_record_version=1,
            worker_id=worker,
            now=START_AT,
            lease_expires_at=START_AT + timedelta(seconds=60),
        )
        with pytest.raises(collectors.OperationalMarketDataCollectorRecordVersionConflictError):
            await repository.fail_claimed(
                current.epoch_id,
                expected_record_version=1,
                worker_id=worker,
                fencing_token=1,
                now=now,
                failure_code=code,
            )
        failed = await repository.fail_claimed(
            current.epoch_id,
            expected_record_version=2,
            worker_id=worker,
            fencing_token=1,
            now=now,
            failure_code=code,
        )
        expected = collectors.fail_claimed_operational_market_data_collector_epoch(
            current,
            worker_id=worker,
            fencing_token=1,
            failed_at=now,
            failure_code=code,
        )
    else:
        with pytest.raises(collectors.OperationalMarketDataCollectorRecordVersionConflictError):
            await repository.fail_unclaimed(
                pending.epoch_id, expected_record_version=2, now=now, failure_code=code
            )
        failed = await repository.fail_unclaimed(
            pending.epoch_id, expected_record_version=1, now=now, failure_code=code
        )
        expected = collectors.fail_unclaimed_operational_market_data_collector_epoch(
            pending,
            failure_code=code,
            failed_at=now,
        )
    assert failed == expected == await repository.get(pending.epoch_id)
    assert failed.worker_claim is None
    assert failed.terminal_at == now
    assert failed.failure is not None
    assert failed.failure.code == code
    with pytest.raises(collectors.OperationalMarketDataCollectorStateTransitionConflictError):
        await repository.fail_unclaimed(
            failed.epoch_id,
            expected_record_version=failed.record_version,
            now=now,
            failure_code=code,
        )


@pytest.mark.asyncio
async def test_recovered_starting_and_stale_worker_version(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    with pytest.raises(collectors.OperationalMarketDataCollectorRecordVersionConflictError):
        await repository.mark_running(
            claimed.epoch_id,
            expected_record_version=1,
            worker_id=claim.worker_id,
            fencing_token=1,
            now=START_AT + timedelta(seconds=2),
        )
    recovered = await repository.recover(
        claimed.epoch_id,
        expected_record_version=2,
        worker_id=uuid4(),
        now=START_AT + timedelta(seconds=61),
        lease_expires_at=START_AT + timedelta(seconds=121),
    )
    assert recovered.worker_claim is not None
    starting = await repository.mark_starting(
        recovered.epoch_id,
        expected_record_version=3,
        worker_id=recovered.worker_claim.worker_id,
        fencing_token=2,
        now=START_AT + timedelta(seconds=62),
    )
    assert starting == collectors.mark_operational_market_data_collector_epoch_starting(
        recovered,
        worker_id=recovered.worker_claim.worker_id,
        fencing_token=2,
        observed_at=START_AT + timedelta(seconds=62),
    )


@pytest.mark.asyncio
async def test_missing_epoch_is_distinct_from_conflict(
    repository: PostgresOperationalMarketDataCollectorRepository,
) -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorNotFoundError):
        await repository.claim(
            uuid4(),
            expected_record_version=1,
            worker_id=uuid4(),
            now=START_AT,
            lease_expires_at=START_AT + timedelta(seconds=60),
        )
    with pytest.raises(collectors.OperationalMarketDataCollectorNotFoundError):
        await repository.renew(
            uuid4(),
            expected_record_version=1,
            worker_id=uuid4(),
            fencing_token=1,
            now=START_AT,
            lease_expires_at=START_AT + timedelta(seconds=60),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [
        ("record_version", True),
        ("epoch_checksum", "0" * 64),
        ("observed_state", "UNKNOWN"),
        ("worker_id", UUID(int=123)),
        ("failure_code", "INTERNAL_ERROR"),
        ("start_intent_fingerprint", "0" * 64),
        ("start_requested_at", START_AT.replace(tzinfo=None)),
    ],
)
async def test_hydration_rejects_corrupt_rows(
    pending: collectors.OperationalMarketDataCollectorEpoch,
    field: str,
    value: object,
) -> None:
    with pytest.raises(PersistenceError):
        operational_market_data_collector_epoch_from_row(_epoch_row(pending) | {field: value})


@pytest.mark.asyncio
async def test_command_hydration_and_database_history_immutable(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    database_url: str,
) -> None:
    history = await repository.list_commands(pending.epoch_id)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "select * from public.operational_market_data_collector_commands where epoch_id = %s",
            (pending.epoch_id,),
        ).fetchone()
    assert row is not None
    assert operational_market_data_collector_command_from_row(row) == history[0]
    for value in (None, True, 1):
        with pytest.raises(PersistenceError):
            operational_market_data_collector_command_from_row(
                row | {"expected_record_version": value}
            )
    for query in (
        "update public.operational_market_data_collector_commands set idempotency_key = 'x'",
        "delete from public.operational_market_data_collector_commands",
    ):
        with pytest.raises(psycopg.Error, match="collector_command_.*_forbidden"):
            with psycopg.connect(database_url) as connection:
                connection.execute(query)
    assert await repository.list_commands(pending.epoch_id) == history


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "version", "worker", "fence", "expired"])
async def test_conditional_update_guards_and_zero_row_error_classification(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Inject one stale SELECT result to independently exercise the real SQL WHERE.

    Normal row locking prevents this window. Bypassing that first read in the test
    proves the UPDATE itself rejects the stale capability, without relying on a
    trigger exception. The diagnostic SELECT still reads real PostgreSQL state.
    """
    claim = claimed.worker_claim
    assert claim is not None
    snapshot = claimed
    now = START_AT + timedelta(seconds=2)
    if case == "missing":
        other_id = uuid4()
        snapshot = replace(
            claimed,
            epoch_id=other_id,
            worker_claim=replace(claim, epoch_id=other_id),
            epoch_checksum=collectors.operational_market_data_collector_epoch_checksum(
                other_id, claimed.specification_checksum
            ),
        )
    elif case == "version":
        snapshot = replace(claimed, record_version=3)
    elif case == "worker":
        snapshot = replace(claimed, worker_claim=replace(claim, worker_id=uuid4()))
    elif case == "fence":
        snapshot = replace(claimed, fencing_token=2, worker_claim=replace(claim, fencing_token=2))
    elif case == "expired":
        now = claim.lease_expires_at
        snapshot = replace(
            claimed, worker_claim=replace(claim, lease_expires_at=START_AT + timedelta(seconds=120))
        )
    expected_errors = {
        "missing": collectors.OperationalMarketDataCollectorNotFoundError,
        "version": collectors.OperationalMarketDataCollectorRecordVersionConflictError,
        "worker": collectors.OperationalMarketDataCollectorLeaseError,
        "fence": collectors.OperationalMarketDataCollectorLeaseError,
        "expired": collectors.OperationalMarketDataCollectorLeaseError,
    }
    original_read = repository_module._epoch
    reads = 0

    async def stale_first_read(
        connection: DatabaseConnection,
        epoch_id: UUID,
        *,
        lock: bool = False,
    ) -> collectors.OperationalMarketDataCollectorEpoch | None:
        nonlocal reads
        reads += 1
        if reads == 1:
            return snapshot
        return await original_read(connection, epoch_id, lock=lock)

    monkeypatch.setattr(repository_module, "_epoch", stale_first_read)
    assert snapshot.worker_claim is not None
    with pytest.raises(expected_errors[case]) as caught:
        await repository.renew(
            snapshot.epoch_id,
            expected_record_version=snapshot.record_version,
            worker_id=snapshot.worker_claim.worker_id,
            fencing_token=snapshot.fencing_token,
            now=now,
            lease_expires_at=START_AT + timedelta(seconds=180),
        )
    assert caught.value.__cause__ is None  # No psycopg trigger rejection was translated.
    assert reads == 2  # Conditional UPDATE returned no row, then a bounded diagnostic read.
    assert await repository.get(claimed.epoch_id) == claimed


@pytest.mark.asyncio
async def test_command_checksum_missing_and_lifecycle_errors(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    for intent, expected in (
        (
            replace(_intent(pending, "PAUSE"), epoch_checksum="0" * 64),
            collectors.OperationalMarketDataCollectorChecksumMismatchError,
        ),
        (
            replace(_intent(pending, "PAUSE"), epoch_id=uuid4()),
            collectors.OperationalMarketDataCollectorNotFoundError,
        ),
        (_intent(pending, "RESUME"), collectors.OperationalMarketDataCollectorCommandConflictError),
    ):
        with pytest.raises(expected):
            await repository.request_command(
                intent, actor_id=pending.start_requested_by, idempotency_key="invalid", now=START_AT
            )
    assert await repository.get(pending.epoch_id) == pending
    assert len(await repository.list_commands(pending.epoch_id)) == 1


@pytest.mark.asyncio
async def test_command_chronology_failure_rolls_back_and_is_sanitized(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    await repository.request_command(
        _intent(pending, "PAUSE"),
        actor_id=pending.start_requested_by,
        idempotency_key="pause",
        now=START_AT + timedelta(seconds=10),
    )
    current = await repository.get(pending.epoch_id)
    assert current is not None
    with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError) as caught:
        await repository.request_command(
            _intent(current, "STOP"),
            actor_id=current.start_requested_by,
            idempotency_key="stop",
            now=START_AT + timedelta(seconds=9),
        )
    assert "chronology_invalid" not in str(caught.value)
    assert isinstance(caught.value.__cause__, psycopg.Error)
    assert await repository.get(pending.epoch_id) == current
    assert len(await repository.list_commands(pending.epoch_id)) == 2


@pytest.mark.asyncio
async def test_start_and_command_share_actor_key_namespace(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    specification: collectors.OperationalMarketDataCollectorSpecification,
) -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorIdempotencyConflictError):
        await repository.request_command(
            _intent(pending, "PAUSE"),
            actor_id=pending.start_requested_by,
            idempotency_key=pending.start_idempotency_key,
            now=START_AT,
        )
    await repository.request_command(
        _intent(pending, "STOP"),
        actor_id=pending.start_requested_by,
        idempotency_key="stop",
        now=START_AT,
    )
    stopped = await repository.settle_unclaimed(
        pending.epoch_id, expected_record_version=2, now=START_AT
    )
    with pytest.raises(collectors.OperationalMarketDataCollectorIdempotencyConflictError):
        await repository.start(
            specification, actor_id=pending.start_requested_by, idempotency_key="stop", now=START_AT
        )
    assert await repository.get(pending.epoch_id) == stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, offset", [(0, 0), (101, 0), (1, -1), (1, 1 << 63)])
async def test_command_history_is_bounded(
    repository: PostgresOperationalMarketDataCollectorRepository,
    limit: int,
    offset: int,
) -> None:
    with pytest.raises(collectors.OperationalMarketDataCollectorBoundsExceededError):
        await repository.list_commands(uuid4(), limit=limit, offset=offset)


@pytest.mark.asyncio
async def test_invalid_inputs_fail_without_issuing_mutations(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        await repository.get(UUID(int=0))
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        await repository.get_current_for_scope("unsupported")
    with pytest.raises(collectors.OperationalMarketDataCollectorRecordVersionConflictError):
        await repository.claim(
            pending.epoch_id,
            expected_record_version=True,
            worker_id=uuid4(),
            now=START_AT,
            lease_expires_at=START_AT + timedelta(seconds=60),
        )
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        await repository.claim(
            pending.epoch_id,
            expected_record_version=1,
            worker_id=uuid4(),
            now=START_AT.replace(tzinfo=None),
            lease_expires_at=START_AT + timedelta(seconds=60),
        )
    assert await repository.get(pending.epoch_id) == pending


@pytest.mark.parametrize(
    "error_type", [psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected]
)
def test_transaction_concurrency_errors_are_safe_version_conflicts(
    error_type: type[psycopg.Error],
) -> None:
    diagnostic = "private database transaction diagnostic"
    with pytest.raises(
        collectors.OperationalMarketDataCollectorRecordVersionConflictError
    ) as caught:
        repository_module._raise_database_error(error_type(diagnostic))
    assert diagnostic not in str(caught.value)


class _ReadOnlyDatabase(Database):
    """Have PostgreSQL reject writes and row locks in resolver integration tests."""

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[DatabaseConnection]:
        async with super().transaction() as connection:
            await connection.execute("set transaction read only")
            yield connection


@pytest_asyncio.fixture
async def replay_repository(
    database_url: str,
) -> AsyncIterator[PostgresOperationalMarketDataCollectorRepository]:
    database = _ReadOnlyDatabase(database_url, min_size=1, max_size=1, timeout=2)
    await database.open()
    try:
        yield PostgresOperationalMarketDataCollectorRepository(database)
    finally:
        await database.close()


def _start_intent(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
) -> collectors.OperationalMarketDataCollectorStartIntent:
    return collectors.OperationalMarketDataCollectorStartIntent(
        specification_checksum=epoch.specification_checksum,
    )


@pytest.mark.asyncio
async def test_resolve_start_replay_miss_is_read_only(
    replay_repository: PostgresOperationalMarketDataCollectorRepository,
    database: Database,
    auth_user_id: UUID,
) -> None:
    async def counts() -> list[int]:
        async with database.transaction() as connection:
            cursor = await connection.execute(
                "select count(*) as count from public.operational_market_data_collector_epochs "
                "union all "
                "select count(*) as count from public.operational_market_data_collector_commands"
            )
            return [int(row["count"]) for row in await cursor.fetchall()]

    before = await counts()
    assert before == [0, 0]
    assert (
        await replay_repository.resolve_start_replay(
            collectors.OperationalMarketDataCollectorStartIntent(specification_checksum="0" * 64),
            actor_id=auth_user_id,
            idempotency_key="unused",
        )
        is None
    )
    assert await counts() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("with_claim", [False, True], ids=["pending", "claimed"])
async def test_resolve_start_replay_current_preserves_all_runtime_and_commands(
    repository: PostgresOperationalMarketDataCollectorRepository,
    replay_repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    with_claim: bool,
) -> None:
    current = pending
    if with_claim:
        current = await repository.claim(
            pending.epoch_id,
            expected_record_version=pending.record_version,
            worker_id=uuid4(),
            now=START_AT + timedelta(seconds=1),
            lease_expires_at=START_AT + timedelta(seconds=61),
        )
    commands = await repository.list_commands(current.epoch_id)
    replay = await replay_repository.resolve_start_replay(
        _start_intent(pending),
        actor_id=pending.start_requested_by,
        idempotency_key=pending.start_idempotency_key,
    )
    assert replay == current
    # Whole-aggregate equality includes version, desired/observed state and claim/fence.
    assert await repository.get(current.epoch_id) == current
    assert await repository.list_commands(current.epoch_id) == commands
    assert len(commands) == 1


@pytest.mark.asyncio
async def test_resolve_start_replay_divergent_intent_conflicts(
    replay_repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    intent = _start_intent(pending)
    divergent = replace(intent, specification_checksum="0" * 64)
    with pytest.raises(collectors.OperationalMarketDataCollectorIdempotencyConflictError):
        await replay_repository.resolve_start_replay(
            divergent,
            actor_id=pending.start_requested_by,
            idempotency_key=pending.start_idempotency_key,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["PAUSE", "RESUME", "STOP"])
async def test_resolve_start_replay_command_collision_conflicts(
    repository: PostgresOperationalMarketDataCollectorRepository,
    replay_repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    kind: str,
) -> None:
    current = pending
    if kind == "RESUME":
        paused = await _command(repository, current, "PAUSE")
        current = await repository.settle_unclaimed(
            paused.epoch_id,
            expected_record_version=paused.record_version,
            now=START_AT + timedelta(seconds=10),
        )
    command = await repository.request_command(
        _intent(current, kind),
        actor_id=current.start_requested_by,
        idempotency_key="command-collision",
        now=START_AT + timedelta(seconds=11),
    )
    with pytest.raises(collectors.OperationalMarketDataCollectorIdempotencyConflictError):
        await replay_repository.resolve_start_replay(
            _start_intent(pending),
            actor_id=pending.start_requested_by,
            idempotency_key=command.idempotency_key,
        )
    assert (await repository.list_commands(current.epoch_id))[-1] == command


@pytest.mark.asyncio
async def test_resolve_start_replay_actor_scope_on_terminal_history(
    repository: PostgresOperationalMarketDataCollectorRepository,
    replay_repository: PostgresOperationalMarketDataCollectorRepository,
    database_url: str,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    other = uuid4()
    with psycopg.connect(database_url) as connection:
        add_auth_user(connection, other)
    stop = await _command(repository, pending, "STOP")
    stopped = await repository.settle_unclaimed(
        stop.epoch_id,
        expected_record_version=stop.record_version,
        now=START_AT + timedelta(seconds=10),
    )
    assert (
        await replay_repository.resolve_start_replay(
            _start_intent(pending), actor_id=other, idempotency_key=pending.start_idempotency_key
        )
        is None
    )
    assert (
        await replay_repository.resolve_start_replay(
            _start_intent(pending),
            actor_id=pending.start_requested_by,
            idempotency_key=pending.start_idempotency_key,
        )
        == stopped
    )


@pytest.mark.asyncio
async def test_list_nonterminal_is_bounded_read_only_and_excludes_terminal(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    before = await repository.get(pending.epoch_id)
    assert before == pending

    listed = await repository.list_nonterminal()
    assert listed == [pending]

    assert await repository.list_nonterminal(limit=1, offset=0) == [pending]
    assert await repository.list_nonterminal(limit=1, offset=1) == []

    after_read = await repository.get(pending.epoch_id)
    assert after_read == before

    await repository.request_command(
        _intent(pending, "STOP"),
        actor_id=pending.start_requested_by,
        idempotency_key="discovery:stop",
        now=START_AT + timedelta(seconds=1),
    )

    stop_requested = await repository.get(pending.epoch_id)
    assert stop_requested is not None
    assert (
        stop_requested.desired_state
        is collectors.OperationalMarketDataCollectorDesiredState.STOPPED
    )
    assert (
        stop_requested.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.PENDING
    )

    # Desired STOPPED is still operationally nonterminal until settlement.
    assert await repository.list_nonterminal() == [stop_requested]

    stopped = await repository.settle_unclaimed(
        pending.epoch_id,
        expected_record_version=stop_requested.record_version,
        now=START_AT + timedelta(seconds=2),
    )

    assert stopped.observed_state is collectors.OperationalMarketDataCollectorObservedState.STOPPED
    assert await repository.list_nonterminal() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit, offset",
    [
        (0, 0),
        (101, 0),
        (True, 0),
        (1, -1),
        (1, True),
        (1, 1 << 63),
    ],
)
async def test_list_nonterminal_rejects_unbounded_pagination(
    repository: PostgresOperationalMarketDataCollectorRepository,
    limit: int,
    offset: int,
) -> None:
    expected = (
        collectors.InvalidOperationalMarketDataCollectorSpecificationError
        if type(limit) is not int or type(offset) is not int
        else collectors.OperationalMarketDataCollectorBoundsExceededError
    )
    with pytest.raises(expected):
        await repository.list_nonterminal(
            limit=limit,
            offset=offset,
        )


@pytest.mark.parametrize(
    "case",
    [
        "object",
        "string",
        "invalid-json",
        "empty",
        "extra",
        "missing",
        "scalar",
        "lowercase",
        "timeframe",
        "bootstrap-bool",
        "bootstrap-float",
        "bootstrap-string",
        "bootstrap-zero",
        "bootstrap-overflow",
        "unordered",
        "duplicate",
    ],
)
def test_target_json_corruption_is_never_silently_normalized(case: str) -> None:
    epoch, _ = collectors.start_operational_market_data_collector_epoch(
        epoch_id=uuid4(),
        command_id=uuid4(),
        specification=_specification(),
        start_intent=collectors.OperationalMarketDataCollectorStartIntent(
            collectors.operational_market_data_collector_specification_checksum(_specification())
        ),
        requested_by=uuid4(),
        requested_at=START_AT,
        idempotency_key="json",
    )
    row = _epoch_row(epoch)
    targets = [
        {"symbol": t.symbol, "timeframe": t.timeframe, "bootstrap_candles": t.bootstrap_candles}
        for t in epoch.specification.targets
    ]
    replacement: object = targets
    if case in {"object", "string", "invalid-json", "empty", "scalar"}:
        replacement = {
            "object": {},
            "string": "[]",
            "invalid-json": "[",
            "empty": [],
            "scalar": [None],
        }[case]
    elif case == "extra":
        targets[0]["exchange"] = "BINANCE"
    elif case == "missing":
        del targets[0]["timeframe"]
    elif case == "lowercase":
        targets[0]["symbol"] = "btc/usdt"
    elif case == "timeframe":
        targets[0]["timeframe"] = "2m"
    elif case.startswith("bootstrap"):
        targets[0]["bootstrap_candles"] = {
            "bootstrap-bool": True,
            "bootstrap-float": 500.0,
            "bootstrap-string": "500",
            "bootstrap-zero": 0,
            "bootstrap-overflow": 1_000_001,
        }[case]
    elif case == "unordered":
        targets.reverse()
    else:
        targets[1] = targets[0]
    with pytest.raises(PersistenceError):
        operational_market_data_collector_epoch_from_row(row | {"targets": replacement})


@pytest.mark.asyncio
async def test_exact_json_serialization_and_timestamp_normalization(
    database: Database,
    pending: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    async with database.transaction() as connection:
        await connection.execute("set local time zone 'America/Sao_Paulo'")
        cursor = await connection.execute(
            "select * from public.operational_market_data_collector_epochs where epoch_id = %s",
            (pending.epoch_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["targets"] == [
            {"symbol": "BTC/USDT", "timeframe": "1m", "bootstrap_candles": 500},
            {"symbol": "ETH/USDT", "timeframe": "5m", "bootstrap_candles": 300},
        ]
        hydrated = operational_market_data_collector_epoch_from_row(row)
        assert hydrated == pending
        assert all(
            isinstance(t, collectors.OperationalMarketDataCollectorTarget)
            for t in hydrated.specification.targets
        )
        assert hydrated.start_requested_at.tzinfo is START_AT.tzinfo


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [
        ("worker_claimed_at", None),
        ("worker_heartbeat_at", START_AT),
        ("worker_lease_expires_at", START_AT),
        ("worker_id", UUID(int=0)),
        ("fencing_token", True),
        ("failure_at", START_AT),
        ("failure_code", "UNKNOWN"),
        ("schema_version", True),
        ("collector_contract_version", 1.0),
        ("epoch_id", "not-a-uuid"),
        ("record_version", 1 << 63),
    ],
)
async def test_worker_and_epoch_corruption_is_persistence_error(
    claimed: collectors.OperationalMarketDataCollectorEpoch,
    field: str,
    value: object,
) -> None:
    with pytest.raises(PersistenceError):
        operational_market_data_collector_epoch_from_row(_epoch_row(claimed) | {field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [
        ("expected_record_version", False),
        ("expected_record_version", 0.0),
        ("expected_record_version", "0"),
        ("expected_record_version", -1),
        ("command_type", "OTHER"),
        ("actor_id", UUID(int=0)),
        ("requested_at", START_AT.replace(tzinfo=None)),
        ("resulting_record_version", True),
        ("epoch_checksum", "INVALID"),
        ("command_contract_version", True),
    ],
)
async def test_command_corruption_is_persistence_error(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    field: str,
    value: object,
) -> None:
    command = (await repository.list_commands(pending.epoch_id))[0]
    with pytest.raises(PersistenceError):
        operational_market_data_collector_command_from_row(_command_row(command) | {field: value})


@pytest.mark.asyncio
async def test_desired_state_reversal_before_physical_convergence(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    running = await repository.mark_running(
        claimed.epoch_id,
        expected_record_version=2,
        worker_id=claim.worker_id,
        fencing_token=1,
        now=START_AT + timedelta(seconds=2),
    )
    pause = await _command(repository, running, "PAUSE")
    assert (pause.observed_state.value, pause.desired_state.value) == ("RUNNING", "PAUSED")
    resumed = await _command(repository, pause, "RESUME")
    assert (resumed.observed_state.value, resumed.desired_state.value) == ("RUNNING", "RUNNING")
    assert resumed.worker_claim == claim
    pause = await _command(repository, resumed, "PAUSE")
    paused = await repository.settle_paused(
        pause.epoch_id,
        expected_record_version=pause.record_version,
        worker_id=claim.worker_id,
        fencing_token=1,
        now=START_AT + timedelta(seconds=15),
    )
    resumed = await _command(repository, paused, "RESUME")
    assert (resumed.observed_state.value, resumed.desired_state.value) == ("PAUSED", "RUNNING")
    paused_again = await _command(repository, resumed, "PAUSE")
    assert (paused_again.observed_state.value, paused_again.desired_state.value) == (
        "PAUSED",
        "PAUSED",
    )
    assert paused_again.worker_claim is None
    stopped = await _command(repository, paused_again, "STOP")
    assert stopped.desired_state.value == "STOPPED"
    terminal = await repository.settle_unclaimed(
        stopped.epoch_id,
        expected_record_version=stopped.record_version,
        now=START_AT + timedelta(seconds=20),
    )
    assert terminal.observed_state.value == "STOPPED"


@pytest.mark.asyncio
async def test_identical_start_race_forces_replay_after_loser_rollback(
    repository: PostgresOperationalMarketDataCollectorRepository,
    specification: collectors.OperationalMarketDataCollectorSpecification,
    auth_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = asyncio.Barrier(2)
    insert = repository_module._insert
    attempts = 0

    async def synchronized_insert(
        connection: DatabaseConnection,
        table: str,
        values: Mapping[str, object],
    ) -> Mapping[str, object]:
        nonlocal attempts
        if table == repository_module._EPOCHS:
            attempts += 1
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return await insert(connection, table, values)

    monkeypatch.setattr(repository_module, "_insert", synchronized_insert)
    results = await asyncio.wait_for(
        asyncio.gather(
            *[
                repository.start(
                    specification,
                    actor_id=auth_user_id,
                    idempotency_key="forced-race",
                    now=START_AT,
                )
                for _ in range(2)
            ]
        ),
        timeout=15,
    )
    assert attempts == 2  # Both initial replay checks missed before either INSERT.
    assert results[0] == results[1]
    assert await repository.list_nonterminal() == [results[0]]
    assert len(await repository.list_commands(results[0].epoch_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "command"])
async def test_deferred_atomicity_failure_rolls_back_entire_repository_transaction(
    database: Database,
    repository: PostgresOperationalMarketDataCollectorRepository,
    specification: collectors.OperationalMarketDataCollectorSpecification,
    auth_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    if operation == "start":
        insert = repository_module._insert

        async def omit_command(
            connection: DatabaseConnection,
            table: str,
            values: Mapping[str, object],
        ) -> Mapping[str, object]:
            if table == repository_module._COMMANDS:
                return values
            return await insert(connection, table, values)

        monkeypatch.setattr(repository_module, "_insert", omit_command)
        with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
            await repository.start(
                specification, actor_id=auth_user_id, idempotency_key="atomic", now=START_AT
            )
        assert await repository.get_current_for_scope() is None
    else:
        epoch = await repository.start(
            specification, actor_id=auth_user_id, idempotency_key="atomic", now=START_AT
        )

        async def omit_update(
            connection: DatabaseConnection,
            current: collectors.OperationalMarketDataCollectorEpoch,
            target: collectors.OperationalMarketDataCollectorEpoch,
            *,
            mode: str,
            now: datetime,
        ) -> collectors.OperationalMarketDataCollectorEpoch:
            return target

        monkeypatch.setattr(repository, "_update", omit_update)
        with pytest.raises(collectors.OperationalMarketDataCollectorCommandConflictError):
            await repository.request_command(
                _intent(epoch, "PAUSE"),
                actor_id=auth_user_id,
                idempotency_key="atomic-command",
                now=START_AT,
            )
        assert await repository.get(epoch.epoch_id) == epoch
        assert len(await repository.list_commands(epoch.epoch_id)) == 1
    async with database.transaction() as connection:
        cursor = await connection.execute(
            "select count(*) as count from public.operational_market_data_collector_commands"
        )
        assert (await cursor.fetchone())["count"] == (0 if operation == "start" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["mark_running", "fail_claimed", "settle_paused"])
async def test_expired_worker_cannot_perform_normal_mutations(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
    method: str,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    kwargs = dict(
        expected_record_version=claimed.record_version,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        now=claim.lease_expires_at,
    )
    if method == "fail_claimed":
        kwargs["failure_code"] = collectors.OperationalMarketDataCollectorFailureCode.LEASE_LOST
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        await getattr(repository, method)(claimed.epoch_id, **kwargs)
    assert await repository.get(claimed.epoch_id) == claimed


@pytest.mark.asyncio
async def test_duplicate_claim_and_recovering_with_same_identity_are_rejected(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        await repository.claim(
            claimed.epoch_id,
            expected_record_version=2,
            worker_id=uuid4(),
            now=START_AT + timedelta(seconds=2),
            lease_expires_at=START_AT + timedelta(seconds=100),
        )
    with pytest.raises(collectors.OperationalMarketDataCollectorLeaseError):
        await repository.recover(
            claimed.epoch_id,
            expected_record_version=2,
            worker_id=claim.worker_id,
            now=claim.lease_expires_at,
            lease_expires_at=START_AT + timedelta(seconds=100),
        )


def test_repository_has_only_database_and_domain_dependencies() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(repository_module))
    allowed = {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "typing",
        "uuid",
        "psycopg",
        "psycopg.types.json",
        "app.operational_market_data_collectors",
        "app.database.errors",
        "app.database.pool",
        "app.domain.errors",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "eval", "exec", "__import__"}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"sleep", "flock", "run_once", "collect", "read_text"}


@pytest.mark.parametrize(
    "constraint, error_class",
    [
        (
            "op_md_collector_epoch_one_current_per_scope_uidx",
            collectors.OperationalMarketDataCollectorCurrentEpochConflictError,
        ),
        (
            "op_md_collector_epoch_actor_start_idempotency_key",
            collectors.OperationalMarketDataCollectorIdempotencyConflictError,
        ),
        (
            "op_md_collector_command_actor_idempotency_key",
            collectors.OperationalMarketDataCollectorIdempotencyConflictError,
        ),
        (
            "op_md_collector_command_epoch_result_version_key",
            collectors.OperationalMarketDataCollectorRecordVersionConflictError,
        ),
    ],
)
def test_named_postgres_constraints_have_closed_translation(
    database_url: str,
    constraint: str,
    error_class: type[Exception],
) -> None:
    from psycopg import sql

    with pytest.raises(psycopg.Error) as database_error:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                sql.SQL(
                    "do $body$ begin raise exception using errcode = '23505', "
                    "constraint = {}, message = 'private diagnostic'; end $body$"
                ).format(sql.Literal(constraint))
            )
    with pytest.raises(error_class) as caught:
        repository_module._raise_database_error(database_error.value)
    assert "private diagnostic" not in str(caught.value)
    assert constraint not in str(caught.value)


@pytest.mark.parametrize(
    "message, error_class",
    [
        (
            "operational_market_data_collector_epoch_recovery_invalid",
            collectors.OperationalMarketDataCollectorLeaseError,
        ),
        (
            "operational_market_data_collector_epoch_fence_mutation_invalid",
            collectors.OperationalMarketDataCollectorLeaseError,
        ),
        (
            "operational_market_data_collector_epoch_terminal",
            collectors.OperationalMarketDataCollectorStateTransitionConflictError,
        ),
        (
            "operational_market_data_collector_command_epoch_checksum_mismatch",
            collectors.OperationalMarketDataCollectorChecksumMismatchError,
        ),
        (
            "operational_market_data_collector_command_epoch_missing",
            collectors.OperationalMarketDataCollectorNotFoundError,
        ),
        (
            "operational_market_data_collector_command_not_applied",
            collectors.OperationalMarketDataCollectorCommandConflictError,
        ),
        ("operational_market_data_collector_new_unknown_worker_failure", PersistenceError),
        ("unrecognized database failure", PersistenceError),
        ("unknown operational database failure", PersistenceUnavailableError),
    ],
)
def test_postgres_message_translation_is_closed_and_sanitized(
    database_url: str,
    message: str,
    error_class: type[Exception],
) -> None:
    from psycopg import sql

    with pytest.raises(psycopg.Error) as database_error:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                sql.SQL(
                    "do $body$ begin raise exception using errcode = {}, message = {}; end $body$"
                ).format(
                    sql.Literal("P0001" if error_class is PersistenceError else "55000"),
                    sql.Literal(message),
                )
            )
    with pytest.raises(error_class) as caught:
        repository_module._raise_database_error(database_error.value)
    assert str(caught.value) != message
    assert "operational_market_data_collector_" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["desired", "observed", "recover-lease", "unclaimed"])
async def test_conditional_update_state_and_lease_guards(
    repository: PostgresOperationalMarketDataCollectorRepository,
    claimed: collectors.OperationalMarketDataCollectorEpoch,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    now = START_AT + timedelta(seconds=10)
    snapshot = claimed
    if case == "desired":
        snapshot = replace(
            claimed, desired_state=collectors.OperationalMarketDataCollectorDesiredState.PAUSED
        )
    elif case == "observed":
        snapshot = replace(
            claimed, observed_state=collectors.OperationalMarketDataCollectorObservedState.RUNNING
        )
    elif case == "recover-lease":
        snapshot = replace(claimed, worker_claim=replace(claim, lease_expires_at=now))
    else:
        snapshot = replace(
            claimed,
            worker_claim=None,
            observed_state=collectors.OperationalMarketDataCollectorObservedState.PENDING,
        )
    read = repository_module._epoch
    reads = 0

    async def stale_first_read(
        connection: DatabaseConnection,
        epoch_id: UUID,
        *,
        lock: bool = False,
    ) -> collectors.OperationalMarketDataCollectorEpoch | None:
        nonlocal reads
        reads += 1
        if reads == 1:
            return snapshot
        return await read(connection, epoch_id, lock=lock)

    monkeypatch.setattr(repository_module, "_epoch", stale_first_read)
    error_class = (
        collectors.OperationalMarketDataCollectorStateTransitionConflictError
        if case in {"desired", "observed"}
        else collectors.OperationalMarketDataCollectorLeaseError
    )
    with pytest.raises(error_class) as caught:
        if case == "recover-lease":
            await repository.recover(
                claimed.epoch_id,
                expected_record_version=2,
                worker_id=uuid4(),
                now=now,
                lease_expires_at=START_AT + timedelta(seconds=100),
            )
        elif case == "unclaimed":
            await repository.fail_unclaimed(
                claimed.epoch_id,
                expected_record_version=2,
                failure_code=collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR,
                now=now,
            )
        else:
            await repository.renew(
                claimed.epoch_id,
                expected_record_version=2,
                worker_id=claim.worker_id,
                fencing_token=1,
                now=now,
                lease_expires_at=START_AT + timedelta(seconds=100),
            )
    assert caught.value.__cause__ is None
    assert reads == 2
    assert await repository.get(claimed.epoch_id) == claimed


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing-epoch", "metadata", "specification"])
async def test_start_replay_rejects_inconsistent_persisted_binding(
    repository: PostgresOperationalMarketDataCollectorRepository,
    specification: collectors.OperationalMarketDataCollectorSpecification,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    read = repository_module._epoch

    async def corrupt_read(
        connection: DatabaseConnection,
        epoch_id: UUID,
        *,
        lock: bool = False,
    ) -> collectors.OperationalMarketDataCollectorEpoch | None:
        epoch = await read(connection, epoch_id, lock=lock)
        assert epoch is not None
        if case == "missing-epoch":
            return None
        if case == "metadata":
            return replace(epoch, start_idempotency_key="unrelated")
        other = replace(specification, interval_seconds=61)
        checksum = collectors.operational_market_data_collector_specification_checksum(other)
        return replace(
            epoch,
            specification=other,
            specification_checksum=checksum,
            epoch_checksum=collectors.operational_market_data_collector_epoch_checksum(
                epoch_id, checksum
            ),
            start_intent_fingerprint=collectors.operational_market_data_collector_start_intent_fingerprint(
                collectors.OperationalMarketDataCollectorStartIntent(checksum)
            ),
        )

    monkeypatch.setattr(repository_module, "_epoch", corrupt_read)
    with pytest.raises(PersistenceError):
        await repository.start(
            specification,
            actor_id=pending.start_requested_by,
            idempotency_key=pending.start_idempotency_key,
            now=START_AT,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "PAUSED"])
async def test_commands_can_reverse_desired_state_without_worker(
    repository: PostgresOperationalMarketDataCollectorRepository,
    pending: collectors.OperationalMarketDataCollectorEpoch,
    state: str,
) -> None:
    paused = await _command(repository, pending, "PAUSE")
    if state == "PAUSED":
        paused = await repository.settle_unclaimed(
            paused.epoch_id,
            expected_record_version=paused.record_version,
            now=START_AT + timedelta(seconds=5),
        )
    resumed = await _command(repository, paused, "RESUME")
    paused_again = await _command(repository, resumed, "PAUSE")
    assert paused_again.desired_state.value == "PAUSED"
    assert paused_again.observed_state.value == state
    assert paused_again.worker_claim is None
    assert len(await repository.list_commands(pending.epoch_id)) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, offset", [(True, 0), (1, False), (1.0, 0), (1, "0")])
async def test_command_pagination_rejects_noninteger_types(
    repository: PostgresOperationalMarketDataCollectorRepository,
    limit: object,
    offset: object,
) -> None:
    with pytest.raises(collectors.InvalidOperationalMarketDataCollectorSpecificationError):
        await repository.list_commands(uuid4(), limit=limit, offset=offset)
