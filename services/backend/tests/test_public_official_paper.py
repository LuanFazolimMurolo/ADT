from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.services.public_official_paper import (
    PUBLIC_OFFICIAL_PAPER_LIMIT,
    PublicOfficialPaperAvailability,
    PublicOfficialPaperService,
)

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)


class State(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class FailureCode(StrEnum):
    INTERNAL_ERROR = "INTERNAL_ERROR"


class FakeRepository:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.calls: list[tuple[int, int]] = []

    async def list_nonterminal(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[object]:
        self.calls.append((limit, offset))
        return self.values[:limit]


def claim(
    *,
    heartbeat_at: datetime = NOW - timedelta(seconds=5),
    lease_expires_at: datetime = NOW + timedelta(seconds=30),
    fencing_token: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
        fencing_token=fencing_token,
    )


def epoch(
    number: int,
    *,
    session_id: str | None = None,
    desired: State = State.RUNNING,
    observed: State = State.RUNNING,
    worker_claim: object | None = None,
    fencing_token: int = 3,
    failure: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        epoch_id=UUID(int=number),
        session_id=session_id or f"session-{number:02d}",
        desired_state=desired,
        observed_state=observed,
        worker_claim=worker_claim,
        fencing_token=fencing_token,
        failure=failure,
    )


@pytest.mark.asyncio
async def test_snapshot_is_fixed_bounded_and_deterministically_sorted() -> None:
    values = [
        epoch(
            number,
            session_id=f"session-{number:02d}",
            worker_claim=claim(),
        )
        for number in range(30, 0, -1)
    ]

    repository = FakeRepository(values)

    service = PublicOfficialPaperService(
        Any.__new__(Any) if False else repository,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    snapshot = await service.get_snapshot()

    assert repository.calls == [(PUBLIC_OFFICIAL_PAPER_LIMIT, 0)]
    assert snapshot.limit == 20
    assert len(snapshot.items) == 20

    assert [item.session_id for item in snapshot.items] == sorted(
        item.session_id for item in snapshot.items
    )


@pytest.mark.asyncio
async def test_running_requires_fresh_current_ownership() -> None:
    repository = FakeRepository(
        [
            epoch(
                1,
                worker_claim=claim(),
            ),
            epoch(
                2,
                worker_claim=claim(
                    lease_expires_at=NOW,
                ),
            ),
            epoch(
                3,
                worker_claim=None,
            ),
            epoch(
                4,
                worker_claim=claim(
                    heartbeat_at=NOW + timedelta(seconds=1),
                ),
            ),
            epoch(
                5,
                worker_claim=claim(
                    fencing_token=2,
                ),
                fencing_token=3,
            ),
        ]
    )

    service = PublicOfficialPaperService(
        repository,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    snapshot = await service.get_snapshot()

    availability = {item.session_id: item.availability for item in snapshot.items}

    assert availability["session-01"] is PublicOfficialPaperAvailability.RUNNING
    assert availability["session-02"] is PublicOfficialPaperAvailability.DEGRADED
    assert availability["session-03"] is PublicOfficialPaperAvailability.DEGRADED
    assert availability["session-04"] is PublicOfficialPaperAvailability.DEGRADED
    assert availability["session-05"] is PublicOfficialPaperAvailability.DEGRADED


@pytest.mark.asyncio
async def test_state_convergence_inactivity_and_failure_are_distinct() -> None:
    repository = FakeRepository(
        [
            epoch(
                1,
                desired=State.RUNNING,
                observed=State.PAUSED,
                worker_claim=claim(),
            ),
            epoch(
                2,
                desired=State.PAUSED,
                observed=State.PAUSED,
            ),
            epoch(
                3,
                desired=State.STOPPED,
                observed=State.STOPPED,
            ),
            epoch(
                4,
                worker_claim=claim(),
                failure=SimpleNamespace(
                    code=FailureCode.INTERNAL_ERROR,
                ),
            ),
        ]
    )

    service = PublicOfficialPaperService(
        repository,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    snapshot = await service.get_snapshot()

    availability = [item.availability for item in snapshot.items]

    assert availability == [
        PublicOfficialPaperAvailability.CONVERGING,
        PublicOfficialPaperAvailability.CONTROLLED_INACTIVE,
        PublicOfficialPaperAvailability.CONTROLLED_INACTIVE,
        PublicOfficialPaperAvailability.FAILED,
    ]

    assert snapshot.items[-1].failure_code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_public_projection_does_not_expose_worker_identity_or_fencing() -> None:
    repository = FakeRepository(
        [
            epoch(
                1,
                worker_claim=claim(),
            )
        ]
    )

    service = PublicOfficialPaperService(
        repository,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    snapshot = await service.get_snapshot()
    item = snapshot.items[0]

    assert not hasattr(item, "worker_id")
    assert not hasattr(item, "fencing_token")
    assert not hasattr(item, "record_version")
