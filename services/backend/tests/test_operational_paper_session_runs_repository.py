"""Gate 2C preflight: domain-authorized mutations must be persistable."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

import app.operational_paper_session_runs as runs
import app.repositories.operational_paper_session_runs as repository_module
from app.database import Database
from app.database.pool import DatabaseConnection
from app.domain.errors import PersistenceError
from app.repositories.operational_paper_session_activations import (
    PostgresOperationalPaperSessionActivationRepository,
)
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
    operational_paper_session_run_command_from_row,
    operational_paper_session_run_epoch_from_row,
)
from tests.postgres_support import add_auth_user
from tests.test_operational_paper_session_activations_migration import (
    _insert as _insert_activation,
)
from tests.test_operational_paper_session_activations_migration import (
    _row as _activation_row,
)
from tests.test_operational_paper_session_activations_migration import (
    _valid_activation,
)
from tests.test_operational_paper_session_runs_migration import (
    START_AT,
    _assert_stored_epoch,
    _epoch_row,
    _persist_change,
    _persist_start,
    _start,
    _update_epoch_row,
)


@pytest.mark.asyncio
async def test_domain_authorized_lease_renewal_is_persistable(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    """Prove domain/SQL lease-renewal alignment before implementing the adapter.

    No repository, trigger bypass or manually fabricated target state is used:
    START, claim and renewal all come from the public domain functions.
    The renewal advances the heartbeat and extends the existing lease.
    """
    activation = await _valid_activation(database_url, database, auth_user_id)
    with psycopg.connect(database_url) as connection:
        _insert_activation(connection, _activation_row(activation))
    pending, command = _start(activation)
    _persist_start(database_url, pending, command)
    claimed = runs.claim_operational_paper_session_run_epoch(
        pending,
        worker_id=uuid4(),
        claimed_at=START_AT + timedelta(seconds=1),
        lease_expires_at=START_AT + timedelta(seconds=61),
    )
    _persist_change(database_url, claimed)
    claim = claimed.worker_claim
    assert claim is not None
    renewed = runs.renew_operational_paper_session_run_worker_claim(
        claimed,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        heartbeat_at=claim.heartbeat_at + timedelta(seconds=1),
        lease_expires_at=claim.lease_expires_at + timedelta(seconds=1),
    )
    assert renewed.worker_claim is not None
    assert renewed.worker_claim.heartbeat_at == claim.heartbeat_at + timedelta(seconds=1)
    assert renewed.worker_claim.heartbeat_at < claim.lease_expires_at
    assert renewed.worker_claim.lease_expires_at > claim.lease_expires_at
    assert renewed.record_version == claimed.record_version + 1
    assert renewed.fencing_token == claimed.fencing_token

    try:
        with psycopg.connect(database_url) as connection:
            _update_epoch_row(connection, renewed.epoch_id, _epoch_row(renewed))
    except psycopg.Error:
        # Confirm rollback before preserving the exact PostgreSQL exception.
        _assert_stored_epoch(database_url, claimed)
        raise
    _assert_stored_epoch(database_url, renewed)


@pytest.fixture
def repository(database: Database) -> PostgresOperationalPaperSessionRunRepository:
    return PostgresOperationalPaperSessionRunRepository(database)


@pytest_asyncio.fixture
async def specification(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> runs.OperationalPaperSessionRunEpochSpecification:
    activation = await _valid_activation(database_url, database, auth_user_id)
    with psycopg.connect(database_url) as connection:
        _insert_activation(connection, _activation_row(activation))
    return runs.build_operational_paper_session_run_epoch_specification(activation)


@pytest_asyncio.fixture
async def pending(
    repository: PostgresOperationalPaperSessionRunRepository,
    specification: runs.OperationalPaperSessionRunEpochSpecification,
    auth_user_id: UUID,
) -> runs.OperationalPaperSessionRunEpoch:
    return await repository.start(
        specification, actor_id=auth_user_id, idempotency_key="start:1", now=START_AT
    )


@pytest_asyncio.fixture
async def claimed(
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
) -> runs.OperationalPaperSessionRunEpoch:
    return await repository.claim(
        pending.epoch_id,
        expected_record_version=1,
        worker_id=uuid4(),
        now=START_AT + timedelta(seconds=1),
        lease_expires_at=START_AT + timedelta(seconds=61),
    )


def _intent(
    epoch: runs.OperationalPaperSessionRunEpoch,
    kind: str,
) -> runs.OperationalPaperSessionRunEpochCommandIntent:
    return runs.OperationalPaperSessionRunEpochCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=runs.OperationalPaperSessionRunCommandType(kind),
        expected_record_version=epoch.record_version,
    )


async def _command(
    repository: PostgresOperationalPaperSessionRunRepository,
    epoch: runs.OperationalPaperSessionRunEpoch,
    kind: str,
) -> runs.OperationalPaperSessionRunEpoch:
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
    repository: PostgresOperationalPaperSessionRunRepository,
    specification: runs.OperationalPaperSessionRunEpochSpecification,
    pending: runs.OperationalPaperSessionRunEpoch,
    auth_user_id: UUID,
) -> None:
    assert await repository.get(pending.epoch_id) == pending
    assert await repository.get_current_for_session(pending.session_id) == pending
    assert (
        await repository.start(
            specification,
            actor_id=auth_user_id,
            idempotency_key="start:1",
            now=START_AT + timedelta(seconds=5),
        )
        == pending
    )
    divergent = replace(specification, activation_checksum="0" * 64)
    with pytest.raises(runs.OperationalPaperSessionRunIdempotencyConflictError):
        await repository.start(
            divergent, actor_id=auth_user_id, idempotency_key="start:1", now=START_AT
        )
    with pytest.raises(runs.OperationalPaperSessionRunCurrentEpochConflictError):
        await repository.start(
            specification, actor_id=auth_user_id, idempotency_key="start:2", now=START_AT
        )
    commands = await repository.list_commands(pending.epoch_id)
    assert len(commands) == 1
    start = commands[0]
    assert start.command_type is runs.OperationalPaperSessionRunCommandType.START
    assert start.expected_record_version is None
    assert start.resulting_record_version == 1
    assert start.intent_fingerprint == pending.start_intent_fingerprint
    assert start.requested_at == pending.start_requested_at
    assert await repository.get(uuid4()) is None
    assert await repository.get_current_for_session("0" * 64) is None
    assert await repository.list_commands(uuid4()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [True, False], ids=["same-intent", "distinct-intent"])
async def test_concurrent_starts(
    repository: PostgresOperationalPaperSessionRunRepository,
    specification: runs.OperationalPaperSessionRunEpochSpecification,
    auth_user_id: UUID,
    same_key: bool,
) -> None:
    for index in range(2):
        assert (
            await repository.resolve_start_replay(
                runs.OperationalPaperSessionRunEpochStartIntent(
                    activation_id=specification.activation_id,
                    activation_checksum=specification.activation_checksum,
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
        result for result in results if isinstance(result, runs.OperationalPaperSessionRunEpoch)
    ]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(successes) == (2 if same_key else 1)
    assert len({epoch.epoch_id for epoch in successes}) == 1
    if same_key:
        assert successes[0] == successes[1]
        assert not failures
    else:
        assert len(failures) == 1
        assert isinstance(failures[0], runs.OperationalPaperSessionRunCurrentEpochConflictError)
    assert await repository.get_current_for_session(specification.session_id) == successes[0]
    assert len(await repository.list_commands(successes[0].epoch_id)) == 1


@pytest.mark.asyncio
async def test_command_replay_after_later_mutation_and_divergent_keys(
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
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
    with pytest.raises(runs.OperationalPaperSessionRunIdempotencyConflictError):
        await repository.request_command(
            _intent(settled, "RESUME"),
            actor_id=pending.start_requested_by,
            idempotency_key="pause",
            now=START_AT,
        )
    with pytest.raises(runs.OperationalPaperSessionRunRecordVersionConflictError):
        await repository.request_command(
            intent, actor_id=pending.start_requested_by, idempotency_key="new", now=START_AT
        )


@pytest.mark.asyncio
async def test_command_actor_scope_and_concurrent_replay(
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
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
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
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
        result for result in results if isinstance(result, runs.OperationalPaperSessionRunEpoch)
    ]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(winners) == len(failures) == 1
    assert isinstance(failures[0], runs.OperationalPaperSessionRunRecordVersionConflictError)
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
    repository: PostgresOperationalPaperSessionRunRepository,
    claimed: runs.OperationalPaperSessionRunEpoch,
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
        runs.OperationalPaperSessionRunRecordVersionConflictError
        if case == "version"
        else runs.OperationalPaperSessionRunLeaseError
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
    repository: PostgresOperationalPaperSessionRunRepository,
    claimed: runs.OperationalPaperSessionRunEpoch,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    now = START_AT + timedelta(seconds=2)
    expires = START_AT + timedelta(seconds=70)
    expected = runs.renew_operational_paper_session_run_worker_claim(
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
    repository: PostgresOperationalPaperSessionRunRepository,
    claimed: runs.OperationalPaperSessionRunEpoch,
) -> None:
    with pytest.raises(runs.OperationalPaperSessionRunLeaseError):
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
        result for result in results if isinstance(result, runs.OperationalPaperSessionRunEpoch)
    ]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(winners) == len(failures) == 1
    assert isinstance(failures[0], runs.OperationalPaperSessionRunRecordVersionConflictError)
    winner = winners[0]
    assert winner.worker_claim is not None
    assert winner.worker_claim.worker_id in workers
    expected = runs.recover_operational_paper_session_run_epoch(
        claimed,
        worker_id=winner.worker_claim.worker_id,
        recovered_at=START_AT + timedelta(seconds=61),
        lease_expires_at=START_AT + timedelta(seconds=121),
    )
    assert winner == expected == await repository.get_current_for_session(claimed.session_id)
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
    repository: PostgresOperationalPaperSessionRunRepository,
    claimed: runs.OperationalPaperSessionRunEpoch,
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
    with pytest.raises(runs.OperationalPaperSessionRunLeaseError):
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
                code=runs.OperationalPaperSessionRunFailureCode.INTERNAL_ERROR,
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
    repository: PostgresOperationalPaperSessionRunRepository,
    claimed: runs.OperationalPaperSessionRunEpoch,
    specification: runs.OperationalPaperSessionRunEpochSpecification,
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
    assert running == runs.mark_operational_paper_session_run_epoch_running(
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
    assert paused == runs.settle_operational_paper_session_run_epoch_paused(
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
    assert stopped == runs.settle_operational_paper_session_run_epoch_stopped(
        stopping,
        worker_id=claim.worker_id,
        fencing_token=2,
        observed_at=START_AT + timedelta(seconds=13),
    )
    assert stopped == await repository.get(stopped.epoch_id)
    assert await repository.get_current_for_session(stopped.session_id) is None
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
    assert later.session_id == stopped.session_id
    assert await repository.list_commands(stopped.epoch_id) == history


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [True, False], ids=["claimed", "unclaimed"])
async def test_failure_round_trip_and_version_conflict(
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
    owned: bool,
) -> None:
    now = START_AT + timedelta(seconds=2)
    code = runs.OperationalPaperSessionRunFailureCode.RAW_NOT_READY
    if owned:
        worker = uuid4()
        current = await repository.claim(
            pending.epoch_id,
            expected_record_version=1,
            worker_id=worker,
            now=START_AT,
            lease_expires_at=START_AT + timedelta(seconds=60),
        )
        with pytest.raises(runs.OperationalPaperSessionRunRecordVersionConflictError):
            await repository.fail_claimed(
                current.epoch_id,
                expected_record_version=1,
                worker_id=worker,
                fencing_token=1,
                now=now,
                code=code,
            )
        failed = await repository.fail_claimed(
            current.epoch_id,
            expected_record_version=2,
            worker_id=worker,
            fencing_token=1,
            now=now,
            code=code,
        )
        expected = runs.fail_claimed_operational_paper_session_run_epoch(
            current,
            worker_id=worker,
            fencing_token=1,
            failed_at=now,
            code=code,
        )
    else:
        with pytest.raises(runs.OperationalPaperSessionRunRecordVersionConflictError):
            await repository.fail_unclaimed(
                pending.epoch_id, expected_record_version=2, now=now, code=code
            )
        failed = await repository.fail_unclaimed(
            pending.epoch_id, expected_record_version=1, now=now, code=code
        )
        expected = runs.fail_unclaimed_operational_paper_session_run_epoch(
            pending,
            code=code,
            failed_at=now,
        )
    assert failed == expected == await repository.get(pending.epoch_id)
    assert failed.worker_claim is None
    assert failed.terminal_at == now
    assert failed.failure is not None
    assert failed.failure.code == code
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError):
        await repository.fail_unclaimed(
            failed.epoch_id, expected_record_version=failed.record_version, now=now, code=code
        )


@pytest.mark.asyncio
async def test_recovered_starting_and_stale_worker_version(
    repository: PostgresOperationalPaperSessionRunRepository,
    claimed: runs.OperationalPaperSessionRunEpoch,
) -> None:
    claim = claimed.worker_claim
    assert claim is not None
    with pytest.raises(runs.OperationalPaperSessionRunRecordVersionConflictError):
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
    assert starting == runs.mark_operational_paper_session_run_epoch_starting(
        recovered,
        worker_id=recovered.worker_claim.worker_id,
        fencing_token=2,
        observed_at=START_AT + timedelta(seconds=62),
    )


@pytest.mark.asyncio
async def test_missing_epoch_is_distinct_from_conflict(
    repository: PostgresOperationalPaperSessionRunRepository,
) -> None:
    with pytest.raises(runs.OperationalPaperSessionRunNotFoundError):
        await repository.claim(
            uuid4(),
            expected_record_version=1,
            worker_id=uuid4(),
            now=START_AT,
            lease_expires_at=START_AT + timedelta(seconds=60),
        )
    with pytest.raises(runs.OperationalPaperSessionRunNotFoundError):
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
    pending: runs.OperationalPaperSessionRunEpoch,
    field: str,
    value: object,
) -> None:
    with pytest.raises(PersistenceError):
        operational_paper_session_run_epoch_from_row(_epoch_row(pending) | {field: value})


@pytest.mark.asyncio
async def test_command_hydration_and_database_history_immutable(
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
    database_url: str,
) -> None:
    history = await repository.list_commands(pending.epoch_id)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            "select * from public.operational_paper_session_run_commands where epoch_id = %s",
            (pending.epoch_id,),
        ).fetchone()
    assert row is not None
    assert operational_paper_session_run_command_from_row(row) == history[0]
    for value in (None, True, 1):
        with pytest.raises(PersistenceError):
            operational_paper_session_run_command_from_row(row | {"expected_record_version": value})
    for query in (
        "update public.operational_paper_session_run_commands set idempotency_key = 'x'",
        "delete from public.operational_paper_session_run_commands",
    ):
        with pytest.raises(psycopg.Error, match="run_command_.*_forbidden"):
            with psycopg.connect(database_url) as connection:
                connection.execute(query)
    assert await repository.list_commands(pending.epoch_id) == history


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "version", "worker", "fence", "expired"])
async def test_conditional_update_guards_and_zero_row_error_classification(
    repository: PostgresOperationalPaperSessionRunRepository,
    claimed: runs.OperationalPaperSessionRunEpoch,
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
            claimed, epoch_id=other_id, worker_claim=replace(claim, epoch_id=other_id)
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
        "missing": runs.OperationalPaperSessionRunNotFoundError,
        "version": runs.OperationalPaperSessionRunRecordVersionConflictError,
        "worker": runs.OperationalPaperSessionRunLeaseError,
        "fence": runs.OperationalPaperSessionRunLeaseError,
        "expired": runs.OperationalPaperSessionRunLeaseError,
    }
    original_read = repository_module._epoch
    reads = 0

    async def stale_first_read(
        connection: DatabaseConnection,
        epoch_id: UUID,
        *,
        lock: bool = False,
    ) -> runs.OperationalPaperSessionRunEpoch | None:
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
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
) -> None:
    for intent, expected in (
        (
            replace(_intent(pending, "PAUSE"), epoch_checksum="0" * 64),
            runs.OperationalPaperSessionRunChecksumMismatchError,
        ),
        (
            replace(_intent(pending, "PAUSE"), epoch_id=uuid4()),
            runs.OperationalPaperSessionRunNotFoundError,
        ),
        (_intent(pending, "RESUME"), runs.OperationalPaperSessionRunCommandConflictError),
    ):
        with pytest.raises(expected):
            await repository.request_command(
                intent, actor_id=pending.start_requested_by, idempotency_key="invalid", now=START_AT
            )
    assert await repository.get(pending.epoch_id) == pending
    assert len(await repository.list_commands(pending.epoch_id)) == 1


@pytest.mark.asyncio
async def test_command_chronology_failure_rolls_back_and_is_sanitized(
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
) -> None:
    await repository.request_command(
        _intent(pending, "PAUSE"),
        actor_id=pending.start_requested_by,
        idempotency_key="pause",
        now=START_AT + timedelta(seconds=10),
    )
    current = await repository.get(pending.epoch_id)
    assert current is not None
    with pytest.raises(runs.OperationalPaperSessionRunCommandConflictError) as caught:
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
async def test_start_upstream_error_rolls_back_without_raw_diagnostics(
    repository: PostgresOperationalPaperSessionRunRepository,
    specification: runs.OperationalPaperSessionRunEpochSpecification,
    auth_user_id: UUID,
) -> None:
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await repository.start(
            replace(specification, activation_id=uuid4()),
            actor_id=auth_user_id,
            idempotency_key="missing-activation",
            now=START_AT,
        )
    assert "activation_missing" not in str(caught.value)
    assert isinstance(caught.value.__cause__, psycopg.Error)
    assert await repository.get_current_for_session(specification.session_id) is None
    actual = await repository.start(
        specification, actor_id=auth_user_id, idempotency_key="missing-activation", now=START_AT
    )
    assert actual.record_version == 1


@pytest.mark.asyncio
async def test_start_and_command_share_actor_key_namespace(
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
    specification: runs.OperationalPaperSessionRunEpochSpecification,
) -> None:
    with pytest.raises(runs.OperationalPaperSessionRunIdempotencyConflictError):
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
    with pytest.raises(runs.OperationalPaperSessionRunIdempotencyConflictError):
        await repository.start(
            specification, actor_id=pending.start_requested_by, idempotency_key="stop", now=START_AT
        )
    assert await repository.get(pending.epoch_id) == stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, offset", [(0, 0), (101, 0), (1, -1), (1, 1 << 63)])
async def test_command_history_is_bounded(
    repository: PostgresOperationalPaperSessionRunRepository,
    limit: int,
    offset: int,
) -> None:
    with pytest.raises(runs.OperationalPaperSessionRunBoundsExceededError):
        await repository.list_commands(uuid4(), limit=limit, offset=offset)


@pytest.mark.asyncio
async def test_invalid_inputs_fail_without_issuing_mutations(
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
) -> None:
    with pytest.raises(runs.InvalidOperationalPaperSessionRunSpecificationError):
        await repository.get(UUID(int=0))
    with pytest.raises(runs.InvalidOperationalPaperSessionRunSpecificationError):
        await repository.get_current_for_session("not-a-session")
    with pytest.raises(runs.OperationalPaperSessionRunRecordVersionConflictError):
        await repository.claim(
            pending.epoch_id,
            expected_record_version=True,
            worker_id=uuid4(),
            now=START_AT,
            lease_expires_at=START_AT + timedelta(seconds=60),
        )
    with pytest.raises(runs.InvalidOperationalPaperSessionRunSpecificationError):
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
    with pytest.raises(runs.OperationalPaperSessionRunRecordVersionConflictError) as caught:
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
) -> AsyncIterator[PostgresOperationalPaperSessionRunRepository]:
    database = _ReadOnlyDatabase(database_url, min_size=1, max_size=1, timeout=2)
    await database.open()
    try:
        yield PostgresOperationalPaperSessionRunRepository(database)
    finally:
        await database.close()


def _start_intent(
    epoch: runs.OperationalPaperSessionRunEpoch,
) -> runs.OperationalPaperSessionRunEpochStartIntent:
    return runs.OperationalPaperSessionRunEpochStartIntent(
        activation_id=epoch.activation_id,
        activation_checksum=epoch.activation_checksum,
    )


@pytest.mark.asyncio
async def test_resolve_start_replay_miss_is_read_only(
    replay_repository: PostgresOperationalPaperSessionRunRepository,
    database: Database,
    auth_user_id: UUID,
) -> None:
    async def counts() -> list[int]:
        async with database.transaction() as connection:
            cursor = await connection.execute(
                "select count(*) as count from public.operational_paper_session_run_epochs "
                "union all "
                "select count(*) as count from public.operational_paper_session_run_commands"
            )
            return [int(row["count"]) for row in await cursor.fetchall()]

    before = await counts()
    assert before == [0, 0]
    assert (
        await replay_repository.resolve_start_replay(
            runs.OperationalPaperSessionRunEpochStartIntent(
                activation_id=uuid4(), activation_checksum="0" * 64
            ),
            actor_id=auth_user_id,
            idempotency_key="unused",
        )
        is None
    )
    assert await counts() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("with_claim", [False, True], ids=["pending", "claimed"])
async def test_resolve_start_replay_current_preserves_all_runtime_and_commands(
    repository: PostgresOperationalPaperSessionRunRepository,
    replay_repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
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
async def test_resolve_start_replay_terminal_history_after_authority_loss(
    repository: PostgresOperationalPaperSessionRunRepository,
    replay_repository: PostgresOperationalPaperSessionRunRepository,
    database: Database,
    pending: runs.OperationalPaperSessionRunEpoch,
    specification: runs.OperationalPaperSessionRunEpochSpecification,
) -> None:
    stop = await _command(repository, pending, "STOP")
    stopped = await repository.settle_unclaimed(
        stop.epoch_id,
        expected_record_version=stop.record_version,
        now=START_AT + timedelta(seconds=10),
    )
    assert stopped.observed_state is runs.OperationalPaperSessionRunObservedState.STOPPED
    later = await repository.start(
        specification,
        actor_id=pending.start_requested_by,
        idempotency_key="later-start",
        now=START_AT + timedelta(seconds=11),
    )
    activations = PostgresOperationalPaperSessionActivationRepository(database)
    activation = await activations.get(pending.activation_id)
    assert activation is not None
    await activations.revoke(
        activation.activation_id,
        expected_record_version=activation.record_version,
        actor_id=pending.start_requested_by,
        now=START_AT + timedelta(seconds=12),
    )
    commands = await repository.list_commands(stopped.epoch_id)
    assert (
        await replay_repository.resolve_start_replay(
            _start_intent(pending),
            actor_id=pending.start_requested_by,
            idempotency_key=pending.start_idempotency_key,
        )
        == stopped
    )
    assert await repository.get(stopped.epoch_id) == stopped
    assert await repository.get_current_for_session(stopped.session_id) == later
    assert await repository.list_commands(stopped.epoch_id) == commands
    assert len(commands) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["activation_id", "activation_checksum"])
async def test_resolve_start_replay_divergent_intent_conflicts(
    replay_repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
    changed_field: str,
) -> None:
    intent = _start_intent(pending)
    divergent = (
        replace(intent, activation_id=uuid4())
        if changed_field == "activation_id"
        else replace(intent, activation_checksum="0" * 64)
    )
    with pytest.raises(runs.OperationalPaperSessionRunIdempotencyConflictError):
        await replay_repository.resolve_start_replay(
            divergent,
            actor_id=pending.start_requested_by,
            idempotency_key=pending.start_idempotency_key,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["PAUSE", "RESUME", "STOP"])
async def test_resolve_start_replay_command_collision_conflicts(
    repository: PostgresOperationalPaperSessionRunRepository,
    replay_repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
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
    with pytest.raises(runs.OperationalPaperSessionRunIdempotencyConflictError):
        await replay_repository.resolve_start_replay(
            _start_intent(pending),
            actor_id=pending.start_requested_by,
            idempotency_key=command.idempotency_key,
        )
    assert (await repository.list_commands(current.epoch_id))[-1] == command


@pytest.mark.asyncio
async def test_resolve_start_replay_actor_scope_on_terminal_history(
    repository: PostgresOperationalPaperSessionRunRepository,
    replay_repository: PostgresOperationalPaperSessionRunRepository,
    database_url: str,
    pending: runs.OperationalPaperSessionRunEpoch,
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
    repository: PostgresOperationalPaperSessionRunRepository,
    pending: runs.OperationalPaperSessionRunEpoch,
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
    assert stop_requested.desired_state is runs.OperationalPaperSessionRunDesiredState.STOPPED
    assert stop_requested.observed_state is runs.OperationalPaperSessionRunObservedState.PENDING

    # Desired STOPPED is still operationally nonterminal until settlement.
    assert await repository.list_nonterminal() == [stop_requested]

    stopped = await repository.settle_unclaimed(
        pending.epoch_id,
        expected_record_version=stop_requested.record_version,
        now=START_AT + timedelta(seconds=2),
    )

    assert stopped.observed_state is runs.OperationalPaperSessionRunObservedState.STOPPED
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
    repository: PostgresOperationalPaperSessionRunRepository,
    limit: int,
    offset: int,
) -> None:
    with pytest.raises(runs.OperationalPaperSessionRunBoundsExceededError):
        await repository.list_nonterminal(
            limit=limit,
            offset=offset,
        )
