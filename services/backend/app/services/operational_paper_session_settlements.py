"""Verified local-evidence application service for official paper settlement."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import app.operational_paper_session_runs as runs
import app.operational_paper_session_settlements as settlements
from app.paper_trading.errors import (
    PaperSessionCorruptError,
    PaperSessionNotFoundError,
    PaperSessionVerificationError,
)
from app.paper_trading.service import PaperTradingService
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.repositories.operational_paper_session_settlements import (
    PostgresOperationalPaperSessionSettlementRepository,
)

OperationalPaperSessionSettlementClock = Callable[[], datetime]

_LOCAL_EVIDENCE_ERRORS = (
    PaperSessionCorruptError,
    PaperSessionNotFoundError,
    PaperSessionVerificationError,
)


def _actor_id(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise settlements.InvalidOperationalPaperSessionSettlementSpecificationError()
    return value


def _canonical_intent(
    value: object,
) -> settlements.OperationalPaperSessionSettlementIntent:
    if not isinstance(value, settlements.OperationalPaperSessionSettlementIntent):
        raise settlements.InvalidOperationalPaperSessionSettlementSpecificationError()
    return settlements.OperationalPaperSessionSettlementIntent(
        epoch_id=value.epoch_id,
        epoch_checksum=value.epoch_checksum,
    )


def _require_eligible(condition: bool) -> None:
    if not condition:
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()


class OperationalPaperSessionSettlementService:
    """Bridge one verified local paper authority into atomic PostgreSQL settlement."""

    def __init__(
        self,
        *,
        repository: PostgresOperationalPaperSessionSettlementRepository,
        run_repository: PostgresOperationalPaperSessionRunRepository,
        paper_service: PaperTradingService,
        clock: OperationalPaperSessionSettlementClock,
    ) -> None:
        self._repository = repository
        self._runs = run_repository
        self._paper_service = paper_service
        self._clock = clock

    async def settle(
        self,
        intent: settlements.OperationalPaperSessionSettlementIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> settlements.OperationalPaperSessionSettlement:
        """Verify canonical local evidence before requesting atomic settlement."""

        canonical_intent = _canonical_intent(intent)
        actor = _actor_id(actor_id)
        key = settlements.validate_operational_paper_session_settlement_idempotency_key(
            idempotency_key
        )

        epoch = await self._runs.get(canonical_intent.epoch_id)
        _require_eligible(epoch is not None)
        assert epoch is not None

        _require_eligible(
            epoch.epoch_checksum == canonical_intent.epoch_checksum
            and epoch.desired_state is runs.OperationalPaperSessionRunDesiredState.STOPPED
            and epoch.observed_state is runs.OperationalPaperSessionRunObservedState.STOPPED
            and epoch.terminal_at is not None
        )

        # Historical replay does not reacquire mutable filesystem evidence.
        existing = await self._repository.get_by_session(epoch.session_id)
        if existing is not None:
            expected_fingerprint = (
                settlements.operational_paper_session_settlement_intent_fingerprint(
                    canonical_intent
                )
            )
            if existing.settled_by == actor and existing.settle_idempotency_key == key:
                if existing.settle_intent_fingerprint != expected_fingerprint:
                    raise settlements.OperationalPaperSessionSettlementIdempotencyConflictError()
                return existing
            raise settlements.OperationalPaperSessionAlreadySettledError()

        try:
            config, state, binding = self._paper_service.verify_settlement_evidence(
                epoch.session_id
            )

            _require_eligible(
                state.session_id == epoch.session_id
                and state.config_checksum == epoch.config_checksum
                and binding.session_id == state.session_id
                and binding.config_checksum == state.config_checksum
                and binding.state_id == state.state_id
                and binding.state_checksum == state.checksum
                and binding.dataset_version == state.dataset_version
                and binding.source_checksum == state.source_checksum
            )

        except settlements.OperationalPaperSessionSettlementEligibilityConflictError:
            raise
        except _LOCAL_EVIDENCE_ERRORS:
            raise settlements.OperationalPaperSessionSettlementEligibilityConflictError() from None

        return await self._repository.settle(
            canonical_intent,
            persisted_state_binding=binding,
            initial_capital=config.initial_capital,
            portfolio=state.portfolio,
            actor_id=actor,
            idempotency_key=key,
            now=self._clock(),
        )
