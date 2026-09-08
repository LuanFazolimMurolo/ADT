"""Gate 2D controlled reopen: transport-independent run reads."""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import pytest

import app.operational_paper_session_runs as runs
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.services.operational_paper_session_runs import (
    OperationalPaperSessionRunService,
)


class _ReadRepository:
    def __init__(
        self,
        *,
        epoch: runs.OperationalPaperSessionRunEpoch | None,
        commands: (list[runs.OperationalPaperSessionRunEpochCommand] | None) = None,
        list_error: Exception | None = None,
    ) -> None:
        self.epoch = epoch
        self.commands = [] if commands is None else commands
        self.list_error = list_error
        self.get_calls: list[UUID] = []
        self.list_calls: list[tuple[UUID, int, int]] = []

    async def get(
        self,
        epoch_id: UUID,
    ) -> runs.OperationalPaperSessionRunEpoch | None:
        self.get_calls.append(epoch_id)
        return self.epoch

    async def list_commands(
        self,
        epoch_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[runs.OperationalPaperSessionRunEpochCommand]:
        self.list_calls.append((epoch_id, limit, offset))
        if self.list_error is not None:
            raise self.list_error
        return self.commands


def _service(
    repository: _ReadRepository,
) -> OperationalPaperSessionRunService:
    service = object.__new__(OperationalPaperSessionRunService)
    object.__setattr__(
        service,
        "_repository",
        cast(
            PostgresOperationalPaperSessionRunRepository,
            repository,
        ),
    )
    return service


@pytest.mark.asyncio
async def test_get_forwards_exact_epoch_id_and_preserves_identity() -> None:
    epoch_id = uuid4()
    epoch = cast(
        runs.OperationalPaperSessionRunEpoch,
        object(),
    )
    repository = _ReadRepository(epoch=epoch)
    service = _service(repository)

    result = await service.get(epoch_id)

    assert result is epoch
    assert repository.get_calls == [epoch_id]
    assert repository.list_calls == []


@pytest.mark.asyncio
async def test_get_missing_raises_stable_run_not_found() -> None:
    epoch_id = uuid4()
    repository = _ReadRepository(epoch=None)
    service = _service(repository)

    with pytest.raises(runs.OperationalPaperSessionRunNotFoundError):
        await service.get(epoch_id)

    assert repository.get_calls == [epoch_id]
    assert repository.list_calls == []


@pytest.mark.asyncio
async def test_list_commands_requires_existing_epoch_and_forwards_bounds() -> None:
    epoch_id = uuid4()
    epoch = cast(
        runs.OperationalPaperSessionRunEpoch,
        object(),
    )
    first = cast(
        runs.OperationalPaperSessionRunEpochCommand,
        object(),
    )
    second = cast(
        runs.OperationalPaperSessionRunEpochCommand,
        object(),
    )

    repository = _ReadRepository(
        epoch=epoch,
        commands=[first, second],
    )
    service = _service(repository)

    result = await service.list_commands(
        epoch_id,
        limit=17,
        offset=23,
    )

    assert result == [first, second]
    assert result[0] is first
    assert result[1] is second
    assert repository.get_calls == [epoch_id]
    assert repository.list_calls == [
        (epoch_id, 17, 23),
    ]


@pytest.mark.asyncio
async def test_list_commands_missing_epoch_never_queries_history() -> None:
    epoch_id = uuid4()
    repository = _ReadRepository(epoch=None)
    service = _service(repository)

    with pytest.raises(runs.OperationalPaperSessionRunNotFoundError):
        await service.list_commands(epoch_id)

    assert repository.get_calls == [epoch_id]
    assert repository.list_calls == []


@pytest.mark.asyncio
async def test_list_commands_preserves_repository_error_identity() -> None:
    epoch_id = uuid4()
    epoch = cast(
        runs.OperationalPaperSessionRunEpoch,
        object(),
    )
    error = runs.OperationalPaperSessionRunBoundsExceededError()

    repository = _ReadRepository(
        epoch=epoch,
        list_error=error,
    )
    service = _service(repository)

    with pytest.raises(runs.OperationalPaperSessionRunBoundsExceededError) as caught:
        await service.list_commands(
            epoch_id,
            limit=0,
            offset=0,
        )

    assert caught.value is error
    assert repository.get_calls == [epoch_id]
    assert repository.list_calls == [
        (epoch_id, 0, 0),
    ]
