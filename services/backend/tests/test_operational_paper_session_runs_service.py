"""Gate 2D: real authority chain, canonical local evidence and cooperative control."""

from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

import app.operational_paper_session_runs as runs
import app.services.operational_paper_session_runs as service_module
from app.backtesting.domain import StrategyParameters
from app.backtesting.strategy import BacktestStrategy
from app.database import Database
from app.database.pool import DatabaseConnection
from app.domain.models import SimulationDetails, SimulationStatus
from app.market_data.catalog import DatasetMetadata, JsonMarketDataCatalog
from app.market_data.domain import Candle, Exchange, MarketType
from app.market_data.integrity import (
    RAW_DATASET_VERSION_ALGORITHM,
    build_raw_partition_integrity_manifest,
)
from app.market_data.locks import DatasetLockManager
from app.market_data.operations import MarketDatasetSelector
from app.market_data.storage import ParquetCandleStore
from app.market_data.transaction import MarketDataTransactionCoordinator
from app.operational_paper_session_activations import (
    OperationalPaperSessionActivation,
    build_operational_paper_session_activation_specification,
)
from app.operational_paper_session_materializations import OperationalPaperSessionMaterialization
from app.operational_paper_session_profiles import (
    OperationalPaperSessionProfile,
    OperationalPaperSessionProfileRevision,
)
from app.paper_trading.domain import PaperSessionConfig, paper_config_checksum, paper_session_id
from app.paper_trading.repository import PaperTradingRepository
from app.paper_trading.service import PaperTradingService
from app.paper_trading.source import LocalRawPaperCandleSource
from app.repositories.operational_mandates import PostgresOperationalMandateRepository
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_session_activations import (
    PostgresOperationalPaperSessionActivationRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.repositories.simulations import SimulationRepository
from app.repositories.strategy_definitions import PostgresStrategyDefinitionRepository
from app.services.operational_paper_session_runs import OperationalPaperSessionRunService
from app.strategies.domain import StrategyPluginDescriptor
from app.strategies.registry import StrategyPluginRegistry
from tests.market_data_helpers import candle
from tests.test_operational_paper_session_materializations_repository import (
    PREPARED_AT,
    _plan_context,
)

NOW = PREPARED_AT + timedelta(days=1)
Code = runs.OperationalPaperSessionRunFailureCode


def _forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("forbidden network, recovery or execution call")


@pytest.fixture(autouse=True)
def no_execution_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx.Client, "request", _forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", _forbidden)
    monkeypatch.setattr(PaperTradingService, "run_once", _forbidden)
    monkeypatch.setattr(LocalRawPaperCandleSource, "load", _forbidden)
    monkeypatch.setattr(MarketDataTransactionCoordinator, "recover_dataset", _forbidden)
    monkeypatch.setattr(MarketDataTransactionCoordinator, "recover", _forbidden)


@dataclass(frozen=True)
class _Plugin:
    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        name="test-profile-plugin",
        version="2",
        description="Frozen test plugin",
        lifecycle_version=2,
    )

    def build(self, parameters: StrategyParameters) -> BacktestStrategy:
        raise AssertionError("eligibility must never instantiate a strategy")


def _publish_raw(
    catalog: JsonMarketDataCatalog,
    store: ParquetCandleStore,
    config: PaperSessionConfig,
    candles: tuple[Candle, ...],
) -> None:
    selector = MarketDatasetSelector(
        Exchange.BINANCE, MarketType.SPOT, config.pair, config.timeframe
    )
    transaction_id = uuid4().hex
    plan = store.plan_upsert(candles, transaction_id=transaction_id)
    assert plan.first_open_time is not None and plan.last_open_time is not None
    started = catalog.start_run(selector.canonical_key)
    metadata = DatasetMetadata(
        key=selector.canonical_key,
        exchange=selector.exchange.value,
        market_type=selector.market_type.value,
        symbol=config.pair.symbol,
        native_symbol=f"{config.pair.base}{config.pair.quote}",
        timeframe=config.timeframe.code,
        location=str(
            store.dataset_root(
                selector.exchange, selector.market_type, config.pair, config.timeframe
            ).relative_to(store.root.parent)
        ),
        first_open_time=plan.first_open_time.isoformat(),
        last_open_time=plan.last_open_time.isoformat(),
        candle_count=plan.candle_count,
        version=plan.checksum,
        updated_at=NOW.isoformat(),
        version_algorithm=RAW_DATASET_VERSION_ALGORITHM,
        partition_integrity=build_raw_partition_integrity_manifest(
            plan.checksum, plan.partition_integrity_entries
        ),
    )
    completed = replace(
        started,
        status="COMPLETED",
        finished_at=NOW.isoformat(),
        fetched_count=len(candles),
        stored_count=len(candles),
    )
    with catalog.acquire_lease() as lease:
        catalog_plan = catalog.prepare_completion(
            completed, metadata, transaction_id=transaction_id, lease=lease
        )
        MarketDataTransactionCoordinator(store, catalog).execute(
            plan, catalog_plan, intended_version=plan.checksum, catalog_lease=lease
        )


@dataclass
class _Context:
    service: OperationalPaperSessionRunService
    repository: PostgresOperationalPaperSessionRunRepository
    activation: OperationalPaperSessionActivation
    prepared: OperationalPaperSessionMaterialization
    config: PaperSessionConfig
    paper: PaperTradingRepository
    registry: StrategyPluginRegistry
    catalog: JsonMarketDataCatalog
    store: ParquetCandleStore
    data_dir: Path
    database: Database
    actor: UUID
    activations: PostgresOperationalPaperSessionActivationRepository
    authorizations: PostgresOperationalPaperCapitalAuthorizationRepository
    profiles: PostgresOperationalPaperSessionProfileRepository
    mandates: PostgresOperationalMandateRepository
    simulations: SimulationRepository
    materializations: PostgresOperationalPaperSessionMaterializationRepository

    @property
    def intent(self) -> runs.OperationalPaperSessionRunEpochStartIntent:
        return runs.OperationalPaperSessionRunEpochStartIntent(
            self.activation.activation_id, self.activation.activation_checksum
        )

    @property
    def config_path(self) -> Path:
        return (
            self.data_dir / "market" / "paper-trading" / self.activation.session_id / "config.json"
        )

    async def start(self, key: str = "start:service") -> runs.OperationalPaperSessionRunEpoch:
        return await self.service.start(
            self.intent, actor_id=self.actor, idempotency_key=key, requested_at=NOW
        )

    async def revoke(self, target: str) -> None:
        if target == "activation":
            await self.activations.revoke(
                self.activation.activation_id,
                expected_record_version=1,
                actor_id=self.actor,
                now=NOW,
            )
        elif target == "authorization":
            await self.authorizations.revoke(
                self.activation.authorization_binding.authorization_id,
                expected_record_version=1,
                actor_id=self.actor,
                now=NOW,
            )
        elif target == "profile":
            profile_id = self.activation.profile_binding.profile_id
            profile = await self.profiles.get(profile_id)
            assert profile is not None
            await self.profiles.archive(
                profile_id,
                expected_record_version=profile.record_version,
                actor_id=self.actor,
                now=NOW,
            )
        elif target == "mandate":
            mandate_id = self.activation.mandate_binding.mandate_id
            mandate = await self.mandates.get(mandate_id)
            assert mandate is not None
            await self.mandates.archive(
                mandate_id,
                expected_record_version=mandate.record_version,
                actor_id=self.actor,
                now=NOW,
            )
        elif target == "simulation":
            await self.revoke("authorization")
            await self.simulations.transition(
                self.activation.simulation_id, target_status=SimulationStatus.COMPLETED
            )
        else:
            raise AssertionError(target)


@pytest_asyncio.fixture
async def context(
    database: Database,
    database_url: str,
    auth_user_id: UUID,
    tmp_path: Path,
) -> _Context:
    plan = await _plan_context(database_url, database, auth_user_id)
    materializations = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await materializations.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT)
    paper = PaperTradingRepository(tmp_path)
    paper.create(plan.config)
    materialized = await materializations.mark_materialized(
        prepared.materialization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=PREPARED_AT + timedelta(minutes=1),
    )
    activations = PostgresOperationalPaperSessionActivationRepository(database)
    activation = await activations.create(
        build_operational_paper_session_activation_specification(materialized),
        actor_id=auth_user_id,
        idempotency_key="activation:service",
        now=PREPARED_AT + timedelta(minutes=2),
    )
    catalog, store = JsonMarketDataCatalog(tmp_path), ParquetCandleStore(tmp_path)
    _publish_raw(
        catalog,
        store,
        plan.config,
        tuple(
            candle(plan.config.context_start + i * plan.config.timeframe.duration)
            for i in range(plan.config.warmup_candles + 2)
        ),
    )
    repository = PostgresOperationalPaperSessionRunRepository(database)
    authorizations = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    profiles = PostgresOperationalPaperSessionProfileRepository(database)
    mandates = PostgresOperationalMandateRepository(database)
    simulations = SimulationRepository(database)
    registry = StrategyPluginRegistry((_Plugin(),))
    service = OperationalPaperSessionRunService(
        repository=repository,
        activation_repository=activations,
        materialization_repository=materializations,
        authorization_repository=authorizations,
        profile_repository=profiles,
        mandate_repository=mandates,
        simulation_repository=simulations,
        paper_repository=paper,
        registry=registry,
        raw_catalog=catalog,
        raw_store=store,
        raw_locks=DatasetLockManager(tmp_path, timeout_seconds=1, stale_after_seconds=60),
        clock=lambda: NOW,
    )
    return _Context(
        service,
        repository,
        activation,
        prepared,
        plan.config,
        paper,
        registry,
        catalog,
        store,
        tmp_path,
        database,
        auth_user_id,
        activations,
        authorizations,
        profiles,
        mandates,
        simulations,
        materializations,
    )


def _command(
    epoch: runs.OperationalPaperSessionRunEpoch,
    kind: str,
) -> runs.OperationalPaperSessionRunEpochCommandIntent:
    return runs.OperationalPaperSessionRunEpochCommandIntent(
        epoch.epoch_id,
        epoch.epoch_checksum,
        runs.OperationalPaperSessionRunCommandType(kind),
        epoch.record_version,
    )


def _assert_failure(
    error: runs.OperationalPaperSessionRunStateTransitionConflictError, code: Code
) -> None:
    assert error.details == {"failure_code": code.value}
    assert error.__cause__ is None
    assert "private" not in str(error)


@pytest.mark.asyncio
async def test_new_start_and_execution_use_real_config_plugin_raw(context: _Context) -> None:
    epoch = await context.start()
    assert epoch.observed_state is runs.OperationalPaperSessionRunObservedState.PENDING
    assert epoch.desired_state is runs.OperationalPaperSessionRunDesiredState.RUNNING
    assert epoch.activation_id == context.activation.activation_id
    assert epoch.materialization_id == context.prepared.materialization_id
    assert epoch.config_checksum == paper_config_checksum(context.config)
    assert epoch.session_id == paper_session_id(context.config)
    assert epoch.start_requested_by == context.actor and epoch.start_requested_at == NOW
    assert epoch.worker_claim is None and epoch.fencing_token == 0
    assert await context.repository.get(epoch.epoch_id) == epoch
    assert await context.service.validate_execution_eligibility(epoch.epoch_id) == context.config
    assert await context.start() == epoch


@pytest.mark.asyncio
async def test_execution_snapshot_allows_only_same_claim_heartbeat_churn(
    context: _Context,
) -> None:
    epoch = await context.start()
    worker_id = uuid4()

    claimed = await context.repository.claim(
        epoch.epoch_id,
        expected_record_version=epoch.record_version,
        worker_id=worker_id,
        now=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=31),
    )

    assert claimed.worker_claim is not None

    renewed = await context.repository.renew(
        epoch.epoch_id,
        expected_record_version=claimed.record_version,
        worker_id=worker_id,
        fencing_token=claimed.worker_claim.fencing_token,
        now=NOW + timedelta(seconds=2),
        lease_expires_at=NOW + timedelta(seconds=32),
    )

    assert renewed.worker_claim is not None
    assert renewed.record_version > claimed.record_version
    assert (
        renewed.worker_claim.heartbeat_at
        > claimed.worker_claim.heartbeat_at
    )
    assert (
        renewed.worker_claim.lease_expires_at
        > claimed.worker_claim.lease_expires_at
    )

    assert context.service._same_execution_snapshot(
        claimed,
        renewed,
    )


@pytest.mark.asyncio
async def test_execution_snapshot_rejects_admin_control_change(
    context: _Context,
) -> None:
    epoch = await context.start()
    worker_id = uuid4()

    claimed = await context.repository.claim(
        epoch.epoch_id,
        expected_record_version=epoch.record_version,
        worker_id=worker_id,
        now=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(seconds=31),
    )

    await context.service.pause(
        _command(claimed, "PAUSE"),
        actor_id=context.actor,
        idempotency_key="snapshot:pause",
        requested_at=NOW + timedelta(seconds=2),
    )

    paused = await context.repository.get(epoch.epoch_id)

    assert paused is not None
    assert (
        paused.desired_state
        is runs.OperationalPaperSessionRunDesiredState.PAUSED
    )
    assert not context.service._same_execution_snapshot(
        claimed,
        paused,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target", ["activation", "authorization", "profile", "mandate", "simulation"]
)
async def test_terminal_replay_skips_every_fresh_dependency(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    epoch = await context.start()
    await context.service.stop(
        _command(epoch, "STOP"),
        actor_id=context.actor,
        idempotency_key="stop",
        requested_at=NOW,
    )
    stopped = await context.repository.settle_unclaimed(
        epoch.epoch_id,
        expected_record_version=2,
        now=NOW,
    )
    await context.revoke(target)
    context.config_path.unlink()
    monkeypatch.setattr(context.activations, "get", _forbidden)
    monkeypatch.setattr(context.paper, "load_config", _forbidden)
    monkeypatch.setattr(context.registry, "resolve", _forbidden)
    monkeypatch.setattr(context.store, "read_verified", _forbidden)
    assert await context.start() == stopped


@pytest.mark.asyncio
async def test_divergent_replay_precedes_fresh_dependencies(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await context.start()
    monkeypatch.setattr(context.activations, "get", _forbidden)
    with pytest.raises(runs.OperationalPaperSessionRunIdempotencyConflictError):
        await context.service.start(
            replace(context.intent, activation_checksum="0" * 64),
            actor_id=context.actor,
            idempotency_key="start:service",
            requested_at=NOW,
        )


@pytest.mark.asyncio
async def test_current_epoch_conflict(context: _Context) -> None:
    original = await context.start()
    with pytest.raises(runs.OperationalPaperSessionRunCurrentEpochConflictError):
        await context.start("distinct")
    assert await context.repository.get(original.epoch_id) == original
    assert len(await context.repository.list_commands(original.epoch_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", [False, True], ids=["start", "execution"])
@pytest.mark.parametrize(
    "target", ["activation", "authorization", "profile", "mandate", "simulation"]
)
async def test_fresh_authority_loss_fails_closed(
    context: _Context, execution: bool, target: str
) -> None:
    epoch = await context.start() if execution else None
    await context.revoke(target)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        if epoch is None:
            await context.start()
        else:
            await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(
        caught.value, Code.ACTIVATION_REVOKED if target == "activation" else Code.AUTHORITY_LOST
    )
    assert await context.repository.get_current_for_session(context.activation.session_id) == epoch


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", [False, True], ids=["start", "execution"])
@pytest.mark.parametrize(
    "target", ["activation", "materialization", "authorization", "profile", "mandate"]
)
async def test_missing_authority_fails_closed(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    execution: bool,
    target: str,
) -> None:
    epoch = await context.start() if execution else None
    repository, method = {
        "activation": (context.activations, "get"),
        "materialization": (context.materializations, "get"),
        "authorization": (context.authorizations, "get"),
        "profile": (context.profiles, "get_current"),
        "mandate": (context.mandates, "get_current"),
    }[target]

    async def missing(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(repository, method, missing)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        if epoch is None:
            await context.start()
        else:
            await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, Code.AUTHORITY_LOST)


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", [False, True], ids=["start", "execution"])
async def test_prepared_materialization_is_not_execution_authority(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    execution: bool,
) -> None:
    epoch = await context.start() if execution else None

    async def prepared(*args: object) -> OperationalPaperSessionMaterialization:
        return context.prepared

    monkeypatch.setattr(context.materializations, "get", prepared)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        if epoch is None:
            await context.start()
        else:
            await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, Code.AUTHORITY_LOST)


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", [False, True], ids=["start", "execution"])
@pytest.mark.parametrize(
    "failure", ["missing", "invalid", "checksum", "capital", "identity", "plugin", "raw"]
)
async def test_local_evidence_failures(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    execution: bool,
    failure: str,
) -> None:
    epoch = await context.start() if execution else None
    code = Code.CONFIG_UNAVAILABLE
    if failure == "missing":
        context.config_path.unlink()
    elif failure == "invalid":
        context.config_path.write_text("private invalid document")
    elif failure in {"checksum", "capital", "identity"}:
        code = Code.CONFIG_IDENTITY_CONFLICT
        if failure == "checksum":
            monkeypatch.setattr(service_module, "paper_config_checksum", lambda config: "0" * 64)
        else:
            changed = (
                replace(context.config, initial_capital=Decimal("41"))
                if failure == "capital"
                else replace(
                    context.config,
                    start_at=context.config.start_at + context.config.timeframe.duration,
                )
            )
            monkeypatch.setattr(context.paper, "load_config", lambda session_id: changed)
    elif failure == "plugin":
        code = Code.PLUGIN_UNAVAILABLE
        monkeypatch.setattr(context.registry, "resolve", StrategyPluginRegistry(()).resolve)
    else:
        code = Code.RAW_NOT_READY
        for path in context.store.root.rglob("*.parquet"):
            path.unlink()
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        if epoch is None:
            await context.start()
        else:
            await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, code)
    assert await context.repository.get_current_for_session(context.activation.session_id) == epoch


@pytest.mark.asyncio
async def test_pause_resume_are_desired_only_and_stop_after_authority_loss(
    context: _Context,
) -> None:
    epoch = await context.start()
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError):
        await context.service.resume(
            _command(epoch, "RESUME"),
            actor_id=context.actor,
            idempotency_key="early",
            requested_at=NOW,
        )
    command = await context.service.pause(
        _command(epoch, "PAUSE"),
        actor_id=context.actor,
        idempotency_key="pause",
        requested_at=NOW,
    )
    paused_request = await context.repository.get(epoch.epoch_id)
    assert paused_request is not None
    assert paused_request.desired_state is runs.OperationalPaperSessionRunDesiredState.PAUSED
    assert paused_request.observed_state is runs.OperationalPaperSessionRunObservedState.PENDING
    assert command.resulting_record_version == 2
    paused = await context.repository.settle_unclaimed(
        epoch.epoch_id, expected_record_version=2, now=NOW
    )
    await context.service.resume(
        _command(paused, "RESUME"),
        actor_id=context.actor,
        idempotency_key="resume",
        requested_at=NOW,
    )
    resumed = await context.repository.get(epoch.epoch_id)
    assert resumed is not None
    assert resumed.desired_state is runs.OperationalPaperSessionRunDesiredState.RUNNING
    assert resumed.observed_state is runs.OperationalPaperSessionRunObservedState.PAUSED
    assert resumed.worker_claim is None and resumed.fencing_token == 0
    await context.revoke("activation")
    await context.revoke("authorization")
    await context.service.stop(
        _command(resumed, "STOP"),
        actor_id=context.actor,
        idempotency_key="stop",
        requested_at=NOW,
    )
    stopped_request = await context.repository.get(epoch.epoch_id)
    assert stopped_request is not None
    assert stopped_request.desired_state is runs.OperationalPaperSessionRunDesiredState.STOPPED
    assert stopped_request.observed_state is resumed.observed_state


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["PAUSE", "STOP"])
async def test_desired_controls_forbid_next_cycle(context: _Context, kind: str) -> None:
    epoch = await context.start()
    operation = context.service.pause if kind == "PAUSE" else context.service.stop
    await operation(
        _command(epoch, kind), actor_id=context.actor, idempotency_key=kind, requested_at=NOW
    )
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, Code.LOCAL_STATE_INVALID)


@pytest.mark.asyncio
async def test_resume_after_authority_loss_fails(context: _Context) -> None:
    epoch = await context.start()
    await context.service.pause(
        _command(epoch, "PAUSE"), actor_id=context.actor, idempotency_key="pause", requested_at=NOW
    )
    paused = await context.repository.settle_unclaimed(
        epoch.epoch_id, expected_record_version=2, now=NOW
    )
    await context.revoke("activation")
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.service.resume(
            _command(paused, "RESUME"),
            actor_id=context.actor,
            idempotency_key="resume",
            requested_at=NOW,
        )
    _assert_failure(caught.value, Code.ACTIVATION_REVOKED)
    assert await context.repository.get(epoch.epoch_id) == paused


@pytest.mark.asyncio
async def test_toctou_after_local_validation_persists_no_epoch(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = context.activations.get
    calls = 0
    local_passed = False
    local = context.service._validate_local

    def validate(authority: service_module._Authority) -> PaperSessionConfig:
        nonlocal local_passed
        config = local(authority)
        local_passed = True
        return config

    async def change_before_final(activation_id: UUID) -> OperationalPaperSessionActivation | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            assert local_passed
            await context.revoke("activation")
        return await original(activation_id)

    monkeypatch.setattr(context.service, "_validate_local", validate)
    monkeypatch.setattr(context.activations, "get", change_before_final)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.start()
    _assert_failure(caught.value, Code.ACTIVATION_REVOKED)
    assert await context.repository.get_current_for_session(context.activation.session_id) is None
    async with context.database.transaction() as connection:
        for table in (
            "operational_paper_session_run_epochs",
            "operational_paper_session_run_commands",
        ):
            cursor = await connection.execute(f"select count(*) as count from public.{table}")  # noqa: S608
            assert (await cursor.fetchone()) == {"count": 0}


@pytest.mark.asyncio
async def test_local_work_has_no_open_database_transaction(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = context.database.transaction
    active = 0

    @asynccontextmanager
    async def tracked_transaction() -> AsyncIterator[DatabaseConnection]:
        nonlocal active
        async with transaction() as connection:
            active += 1
            try:
                yield connection
            finally:
                active -= 1

    local = context.service._validate_local
    calls = 0

    def checked(authority: service_module._Authority) -> PaperSessionConfig:
        nonlocal calls
        assert active == 0
        calls += 1
        return local(authority)

    monkeypatch.setattr(context.database, "transaction", tracked_transaction)
    monkeypatch.setattr(context.service, "_validate_local", checked)
    epoch = await context.start()
    await context.service.validate_execution_eligibility(epoch.epoch_id)
    assert calls == 2 and active == 0


@pytest.mark.asyncio
async def test_latest_mutable_strategy_is_not_execution_authority(context: _Context) -> None:
    revision = await context.profiles.get_revision(context.activation.profile_binding.profile_id, 1)
    assert revision is not None
    frozen = revision.specification.strategy_snapshot
    await PostgresStrategyDefinitionRepository(context.database).archive(
        frozen.strategy_definition_id,
        expected_revision=frozen.source_revision,
        actor_id=context.actor,
    )
    epoch = await context.start()
    assert await context.service.validate_execution_eligibility(epoch.epoch_id) == context.config


@pytest.mark.asyncio
async def test_concurrent_identical_service_starts_share_original_epoch(context: _Context) -> None:
    results = await asyncio.gather(context.start(), context.start())
    assert results[0] == results[1]
    assert len(await context.repository.list_commands(results[0].epoch_id)) == 1


def test_service_has_no_network_or_execution_imports() -> None:
    source = inspect.getsource(service_module)
    for forbidden in (
        "httpx",
        "requests",
        "Binance",
        "run_once",
        "recover_dataset",
        "import socket",
    ):
        assert forbidden not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", [False, True], ids=["start", "execution"])
@pytest.mark.parametrize("change", ["status", "currency", "identity"])
async def test_simulation_evidence_is_checked_independently(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    execution: bool,
    change: str,
) -> None:
    epoch = await context.start() if execution else None
    details = await context.simulations.get(context.activation.simulation_id)
    if change == "status":
        changed = replace(details.simulation, status=SimulationStatus.COMPLETED)
    elif change == "currency":
        changed = replace(details.simulation, currency="USD")
    else:
        changed = replace(details.simulation, id=uuid4())

    async def different(simulation_id: UUID) -> SimulationDetails:
        return replace(details, simulation=changed)

    monkeypatch.setattr(context.simulations, "get", different)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        if epoch is None:
            await context.start()
        else:
            await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, Code.AUTHORITY_LOST)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "checksum"])
async def test_exact_approved_profile_binding_is_required(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    pair = await context.profiles.get_current(context.activation.profile_binding.profile_id)
    assert pair is not None
    profile, revision = pair
    changed = (
        replace(profile, current_revision=2, approved_revision=2)
        if change == "revision"
        else replace(profile, approved_checksum="0" * 64)
    )

    async def different(
        profile_id: UUID,
    ) -> tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision]:
        return changed, revision

    monkeypatch.setattr(context.profiles, "get_current", different)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.start()
    _assert_failure(caught.value, Code.AUTHORITY_LOST)
    assert await context.repository.get_current_for_session(context.activation.session_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["activation_checksum", "materialization_id"])
async def test_exact_frozen_activation_materialization_binding_is_required(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    intent = context.intent
    if change == "activation_checksum":
        intent = replace(intent, activation_checksum="0" * 64)
    else:
        materialization = await context.materializations.get(context.activation.materialization_id)
        assert materialization is not None
        changed = replace(materialization, materialization_id=uuid4())

        async def different(materialization_id: UUID) -> OperationalPaperSessionMaterialization:
            return changed

        monkeypatch.setattr(context.materializations, "get", different)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.service.start(
            intent, actor_id=context.actor, idempotency_key="binding", requested_at=NOW
        )
    _assert_failure(caught.value, Code.AUTHORITY_LOST)


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", [False, True], ids=["start", "execution"])
@pytest.mark.parametrize("change", ["gap", "warmup", "open", "ohlc", "future", "integrity"])
async def test_raw_unfit_rows_are_rejected_without_repair(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    execution: bool,
    change: str,
) -> None:
    epoch = await context.start() if execution else None
    rows = tuple(
        candle(context.config.context_start + i * context.config.timeframe.duration)
        for i in range(context.config.warmup_candles + 2)
    )
    if change == "gap":
        rows = rows[:5] + rows[6:]
    elif change == "warmup":
        rows = rows[1:]
    elif change == "open":
        rows = rows[:-1] + (replace(rows[-1], is_closed=False),)
    elif change == "ohlc":
        corrupt = copy.copy(rows[-1])
        object.__setattr__(corrupt, "high", Decimal("1"))
        rows = rows[:-1] + (corrupt,)
    elif change == "future":
        rows = tuple(
            candle(context.config.context_start + i * context.config.timeframe.duration)
            for i in range(50)
        )
    else:
        # Canonically valid replacement rows whose logical checksum differs from the catalog.
        rows = rows[:-1] + (replace(rows[-1], close=Decimal("106")),)
    if change in {"open", "ohlc"}:
        # The canonical writer rejects these; exercise defensive reader validation.
        monkeypatch.setattr(context.store, "read_verified", lambda *args: rows)
    else:
        for path in context.store.root.rglob("*.parquet"):
            path.unlink()
        if change == "integrity":
            receipt = context.store.upsert(rows)
            receipt.commit()
        else:
            _publish_raw(context.catalog, context.store, context.config, rows)
    before = {path: path.read_bytes() for path in context.store.root.rglob("*.parquet")}
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        if epoch is None:
            await context.start()
        else:
            await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, Code.RAW_NOT_READY)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dependency, code",
    [
        ("paper", Code.CONFIG_UNAVAILABLE),
        ("plugin", Code.PLUGIN_UNAVAILABLE),
        ("raw", Code.RAW_NOT_READY),
        ("database", Code.INTERNAL_ERROR),
    ],
)
async def test_eligibility_errors_never_expose_raw_diagnostics(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
    dependency: str,
    code: Code,
) -> None:
    diagnostic = "private /tmp/ADT_DATA_DIR postgresql://user:password@hostname PID=123 SQL"

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise OSError(diagnostic)

    async def async_fail(*args: object, **kwargs: object) -> NoReturn:
        fail()

    if dependency == "paper":
        monkeypatch.setattr(context.paper, "load_config", fail)
    elif dependency == "plugin":
        monkeypatch.setattr(context.registry, "resolve", fail)
    elif dependency == "raw":
        monkeypatch.setattr(context.store, "read_verified", fail)
    else:
        monkeypatch.setattr(context.activations, "get", async_fail)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.start()
    _assert_failure(caught.value, code)
    assert diagnostic not in str(caught.value) and diagnostic not in repr(caught.value.details)
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_authority_race_after_final_reads_is_rejected_by_insert_trigger(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = context.repository.start

    async def raced(
        specification: runs.OperationalPaperSessionRunEpochSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> runs.OperationalPaperSessionRunEpoch:
        await context.revoke("activation")
        return await original(
            specification, actor_id=actor_id, idempotency_key=idempotency_key, now=now
        )

    monkeypatch.setattr(context.repository, "start", raced)
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError):
        await context.start()
    assert await context.repository.get_current_for_session(context.activation.session_id) is None


@pytest.mark.asyncio
async def test_paused_and_terminal_epochs_are_not_execution_eligible(context: _Context) -> None:
    epoch = await context.start()
    await context.service.pause(
        _command(epoch, "PAUSE"), actor_id=context.actor, idempotency_key="pause", requested_at=NOW
    )
    paused = await context.repository.settle_unclaimed(
        epoch.epoch_id, expected_record_version=2, now=NOW
    )
    await context.service.resume(
        _command(paused, "RESUME"),
        actor_id=context.actor,
        idempotency_key="resume",
        requested_at=NOW,
    )
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, Code.LOCAL_STATE_INVALID)
    resumed = await context.repository.get(epoch.epoch_id)
    assert resumed is not None
    await context.service.stop(
        _command(resumed, "STOP"), actor_id=context.actor, idempotency_key="stop", requested_at=NOW
    )
    await context.repository.settle_unclaimed(
        epoch.epoch_id, expected_record_version=resumed.record_version + 1, now=NOW
    )
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, Code.LOCAL_STATE_INVALID)


@pytest.mark.asyncio
async def test_authority_loss_before_second_cycle_does_not_mutate_running_epoch(
    context: _Context,
) -> None:
    epoch = await context.start()
    claimed = await context.repository.claim(
        epoch.epoch_id,
        expected_record_version=epoch.record_version,
        worker_id=uuid4(),
        now=NOW,
        lease_expires_at=NOW + timedelta(seconds=60),
    )
    claim = claimed.worker_claim
    assert claim is not None
    # Initial claim already enters STARTING; mark_starting is for recovery.
    assert claimed.observed_state is runs.OperationalPaperSessionRunObservedState.STARTING
    assert await context.service.validate_execution_eligibility(epoch.epoch_id) == context.config
    running = await context.repository.mark_running(
        epoch.epoch_id,
        expected_record_version=claimed.record_version,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        now=NOW,
    )
    await context.revoke("authorization")
    with pytest.raises(runs.OperationalPaperSessionRunStateTransitionConflictError) as caught:
        await context.service.validate_execution_eligibility(epoch.epoch_id)
    _assert_failure(caught.value, Code.AUTHORITY_LOST)
    assert await context.repository.get(epoch.epoch_id) == running
