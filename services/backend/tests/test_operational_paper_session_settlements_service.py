"""Gate 2D local-evidence settlement application-service tests."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

import app.operational_paper_session_settlements as settlements
from app.paper_trading.errors import PaperSessionVerificationError
from app.services.operational_paper_session_settlements import (
    OperationalPaperSessionSettlementService,
)
from tests.test_operational_paper_session_settlements_domain import (
    _binding,
    _epoch,
    _portfolio,
)

ACTOR_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _fixture() -> tuple[object, object, object, object, object, object, tuple[object, object]]:
    epoch = _epoch()
    binding = _binding()
    portfolio = _portfolio()

    assert binding.session_id == epoch.session_id
    assert binding.config_checksum == epoch.config_checksum
    assert epoch.terminal_at is not None

    config = SimpleNamespace(initial_capital=Decimal("1000"))
    state = SimpleNamespace(
        session_id=binding.session_id,
        config_checksum=binding.config_checksum,
        state_id=binding.state_id,
        checksum=binding.state_checksum,
        dataset_version=binding.dataset_version,
        source_checksum=binding.source_checksum,
        portfolio=portfolio,
    )

    result = object()

    repository = SimpleNamespace(
        get_by_session=AsyncMock(return_value=None),
        settle=AsyncMock(return_value=result),
    )
    run_repository = SimpleNamespace(get=AsyncMock(return_value=epoch))
    paper_service = SimpleNamespace(
        verify_settlement_evidence=Mock(return_value=(config, state, binding))
    )

    return (
        epoch,
        binding,
        config,
        state,
        result,
        repository,
        (run_repository, paper_service),
    )


def _service(
    repository: object,
    dependencies: tuple[object, object],
    *,
    now: object,
) -> OperationalPaperSessionSettlementService:
    run_repository, paper_service = dependencies
    return OperationalPaperSessionSettlementService(
        repository=cast(Any, repository),
        run_repository=cast(Any, run_repository),
        paper_service=cast(Any, paper_service),
        clock=cast(Any, lambda: now),
    )


@pytest.mark.asyncio
async def test_service_derives_all_financial_and_local_evidence() -> None:
    epoch, binding, config, state, result, repository, dependencies = _fixture()
    assert epoch.terminal_at is not None

    now = epoch.terminal_at + timedelta(seconds=1)
    service = _service(repository, dependencies, now=now)

    intent = settlements.OperationalPaperSessionSettlementIntent(
        epoch.epoch_id,
        epoch.epoch_checksum,
    )

    actual = await service.settle(
        intent,
        actor_id=ACTOR_ID,
        idempotency_key="settlement:service:1",
    )

    assert actual is result
    repository.settle.assert_awaited_once_with(
        intent,
        persisted_state_binding=binding,
        initial_capital=config.initial_capital,
        portfolio=state.portfolio,
        actor_id=ACTOR_ID,
        idempotency_key="settlement:service:1",
        now=now,
    )

    run_repository, paper_service = dependencies
    run_repository.get.assert_awaited_once_with(epoch.epoch_id)
    paper_service.verify_settlement_evidence.assert_called_once_with(epoch.session_id)


@pytest.mark.asyncio
async def test_exact_committed_replay_does_not_require_local_artifacts() -> None:
    epoch, _binding_value, _config, _state, _result, repository, dependencies = _fixture()

    intent = settlements.OperationalPaperSessionSettlementIntent(
        epoch.epoch_id,
        epoch.epoch_checksum,
    )
    existing = SimpleNamespace(
        settled_by=ACTOR_ID,
        settle_idempotency_key="settlement:replay",
        settle_intent_fingerprint=(
            settlements.operational_paper_session_settlement_intent_fingerprint(intent)
        ),
    )
    repository.get_by_session.return_value = existing

    service = _service(repository, dependencies, now=epoch.terminal_at)
    actual = await service.settle(
        intent,
        actor_id=ACTOR_ID,
        idempotency_key="settlement:replay",
    )

    assert actual is existing
    repository.settle.assert_not_awaited()

    _run_repository, paper_service = dependencies
    paper_service.verify_settlement_evidence.assert_not_called()


@pytest.mark.asyncio
async def test_existing_session_with_distinct_key_is_already_settled() -> None:
    epoch, _binding_value, _config, _state, _result, repository, dependencies = _fixture()

    intent = settlements.OperationalPaperSessionSettlementIntent(
        epoch.epoch_id,
        epoch.epoch_checksum,
    )
    repository.get_by_session.return_value = SimpleNamespace(
        settled_by=ACTOR_ID,
        settle_idempotency_key="settlement:first",
        settle_intent_fingerprint=(
            settlements.operational_paper_session_settlement_intent_fingerprint(intent)
        ),
    )

    service = _service(repository, dependencies, now=epoch.terminal_at)

    with pytest.raises(settlements.OperationalPaperSessionAlreadySettledError):
        await service.settle(
            intent,
            actor_id=ACTOR_ID,
            idempotency_key="settlement:second",
        )

    repository.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_epoch_checksum_mismatch_rejects_before_local_evidence() -> None:
    epoch, _binding_value, _config, _state, _result, repository, dependencies = _fixture()

    service = _service(repository, dependencies, now=epoch.terminal_at)
    intent = settlements.OperationalPaperSessionSettlementIntent(
        epoch.epoch_id,
        "0" * 64,
    )

    with pytest.raises(settlements.OperationalPaperSessionSettlementEligibilityConflictError):
        await service.settle(
            intent,
            actor_id=ACTOR_ID,
            idempotency_key="settlement:checksum-mismatch",
        )

    repository.get_by_session.assert_not_awaited()
    repository.settle.assert_not_awaited()

    _run_repository, paper_service = dependencies
    paper_service.verify_settlement_evidence.assert_not_called()


@pytest.mark.asyncio
async def test_failed_local_verification_never_reaches_repository() -> None:
    epoch, _binding_value, _config, _state, _result, repository, dependencies = _fixture()

    _run_repository, paper_service = dependencies
    paper_service.verify_settlement_evidence.side_effect = PaperSessionVerificationError()

    service = _service(repository, dependencies, now=epoch.terminal_at)
    intent = settlements.OperationalPaperSessionSettlementIntent(
        epoch.epoch_id,
        epoch.epoch_checksum,
    )

    with pytest.raises(settlements.OperationalPaperSessionSettlementEligibilityConflictError):
        await service.settle(
            intent,
            actor_id=ACTOR_ID,
            idempotency_key="settlement:invalid-local",
        )

    repository.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_inconsistent_verified_binding_never_reaches_repository() -> None:
    epoch, binding, config, state, _result, repository, dependencies = _fixture()

    _run_repository, paper_service = dependencies
    bad_checksum = "0" * 64 if binding.state_checksum != "0" * 64 else "1" * 64
    assert bad_checksum != binding.state_checksum
    bad_binding = replace(binding, state_checksum=bad_checksum)
    paper_service.verify_settlement_evidence.return_value = (
        config,
        state,
        bad_binding,
    )

    service = _service(repository, dependencies, now=epoch.terminal_at)
    intent = settlements.OperationalPaperSessionSettlementIntent(
        epoch.epoch_id,
        epoch.epoch_checksum,
    )

    with pytest.raises(settlements.OperationalPaperSessionSettlementEligibilityConflictError):
        await service.settle(
            intent,
            actor_id=ACTOR_ID,
            idempotency_key="settlement:bad-binding",
        )

    repository.settle.assert_not_awaited()


def test_service_api_accepts_no_financial_or_artifact_evidence() -> None:
    settle_parameters = set(
        inspect.signature(OperationalPaperSessionSettlementService.settle).parameters
    )
    assert settle_parameters == {
        "self",
        "intent",
        "actor_id",
        "idempotency_key",
    }

    forbidden = {
        "portfolio",
        "initial_capital",
        "persisted_state_binding",
        "state_id",
        "state_checksum",
        "dataset_version",
        "source_checksum",
        "timeline_id",
        "timeline_content_checksum",
    }
    assert settle_parameters.isdisjoint(forbidden)

    init_parameters = set(
        inspect.signature(OperationalPaperSessionSettlementService.__init__).parameters
    )
    assert "paper_service" in init_parameters
    assert "paper_repository" not in init_parameters
    assert "persisted_state_verifier" not in init_parameters
