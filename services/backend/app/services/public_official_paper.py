"""Read-only public projection of authoritative official PAPER state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)

PUBLIC_OFFICIAL_PAPER_LIMIT = 20


class PublicOfficialPaperAvailability(StrEnum):
    """Sanitized public operational availability classification."""

    RUNNING = "RUNNING"
    CONTROLLED_INACTIVE = "CONTROLLED_INACTIVE"
    CONVERGING = "CONVERGING"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PublicOfficialPaperItem:
    """One sanitized current official PAPER operational epoch."""

    session_id: str
    desired_state: str
    observed_state: str
    availability: PublicOfficialPaperAvailability
    ownership_fresh: bool | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class PublicOfficialPaperSnapshot:
    """Bounded deterministic public snapshot."""

    observed_at: datetime
    limit: int
    items: tuple[PublicOfficialPaperItem, ...]


class PublicOfficialPaperService:
    """Project current operational PAPER evidence without execution authority."""

    def __init__(
        self,
        repository: PostgresOperationalPaperSessionRunRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def get_snapshot(self) -> PublicOfficialPaperSnapshot:
        """Return at most the fixed public maximum of nonterminal epochs."""

        observed_at = self._clock()

        if observed_at.tzinfo is None:
            raise ValueError("public official PAPER clock must be timezone-aware")

        epochs = await self._repository.list_nonterminal(
            limit=PUBLIC_OFFICIAL_PAPER_LIMIT,
            offset=0,
        )

        ordered = sorted(
            epochs,
            key=lambda epoch: (
                str(epoch.session_id),
                str(epoch.epoch_id),
            ),
        )

        items = tuple(
            self._project_epoch(
                epoch,
                observed_at=observed_at,
            )
            for epoch in ordered[:PUBLIC_OFFICIAL_PAPER_LIMIT]
        )

        return PublicOfficialPaperSnapshot(
            observed_at=observed_at,
            limit=PUBLIC_OFFICIAL_PAPER_LIMIT,
            items=items,
        )

    @staticmethod
    def _state_value(value: object) -> str:
        state_value = getattr(value, "value", value)

        if not isinstance(state_value, str) or not state_value:
            return "UNKNOWN"

        return state_value

    @staticmethod
    def _failure_code(value: object | None) -> str | None:
        if value is None:
            return None

        code = getattr(value, "code", None)
        code_value = getattr(code, "value", code)

        if isinstance(code_value, str) and code_value:
            return code_value

        return "UNKNOWN"

    @staticmethod
    def _ownership_fresh(
        epoch: object,
        *,
        observed_at: datetime,
    ) -> bool | None:
        claim = getattr(epoch, "worker_claim", None)

        if claim is None:
            return None

        heartbeat_at = getattr(claim, "heartbeat_at", None)
        lease_expires_at = getattr(claim, "lease_expires_at", None)
        claim_fence = getattr(claim, "fencing_token", None)
        epoch_fence = getattr(epoch, "fencing_token", None)

        if not isinstance(heartbeat_at, datetime):
            return False

        if not isinstance(lease_expires_at, datetime):
            return False

        try:
            if heartbeat_at > observed_at:
                return False

            if lease_expires_at <= observed_at:
                return False
        except TypeError:
            return False

        if claim_fence != epoch_fence:
            return False

        return True

    @classmethod
    def _project_epoch(
        cls,
        epoch: object,
        *,
        observed_at: datetime,
    ) -> PublicOfficialPaperItem:
        desired = cls._state_value(getattr(epoch, "desired_state", "UNKNOWN"))
        observed = cls._state_value(getattr(epoch, "observed_state", "UNKNOWN"))

        claim = getattr(epoch, "worker_claim", None)
        failure = getattr(epoch, "failure", None)

        ownership_fresh = cls._ownership_fresh(
            epoch,
            observed_at=observed_at,
        )

        if failure is not None:
            availability = PublicOfficialPaperAvailability.FAILED
        elif desired == "RUNNING":
            if observed != "RUNNING":
                availability = PublicOfficialPaperAvailability.CONVERGING
            elif ownership_fresh is True:
                availability = PublicOfficialPaperAvailability.RUNNING
            else:
                availability = PublicOfficialPaperAvailability.DEGRADED
        elif desired in {"PAUSED", "STOPPED"}:
            if observed == desired:
                availability = PublicOfficialPaperAvailability.CONTROLLED_INACTIVE
            else:
                availability = PublicOfficialPaperAvailability.CONVERGING
        elif desired != observed:
            availability = PublicOfficialPaperAvailability.CONVERGING
        else:
            availability = PublicOfficialPaperAvailability.DEGRADED

        heartbeat_at = None if claim is None else getattr(claim, "heartbeat_at", None)

        lease_expires_at = None if claim is None else getattr(claim, "lease_expires_at", None)

        return PublicOfficialPaperItem(
            session_id=str(getattr(epoch, "session_id")),
            desired_state=desired,
            observed_state=observed,
            availability=availability,
            ownership_fresh=ownership_fresh,
            heartbeat_at=heartbeat_at,
            lease_expires_at=lease_expires_at,
            failure_code=cls._failure_code(failure),
        )
