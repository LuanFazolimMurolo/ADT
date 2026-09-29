"""Administrative paper control and fresh, local-only execution eligibility."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from uuid import UUID

import app.operational_paper_session_runs as runs
from app.domain.errors import SimulationNotFoundError
from app.domain.models import SimulationStatus
from app.market_data.catalog import JsonMarketDataCatalog
from app.market_data.domain import DataRange, Exchange, MarketType
from app.market_data.locks import DatasetLockManager
from app.market_data.operations import MarketDatasetSelector, encode_dataset_id
from app.market_data.planning import expected_candle_count
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.raw_dataset_query import LocalRawDatasetReadService
from app.market_data.storage import ParquetCandleStore
from app.operational_mandates import OperationalMandateState
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationSpecification,
    OperationalPaperCapitalAuthorizationState,
)
from app.operational_paper_session_activations import (
    OperationalPaperSessionActivationState,
    build_operational_paper_session_activation_specification,
    operational_paper_session_activation_specification_checksum,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationState,
    build_operational_paper_session_materialization_plan,
    operational_paper_session_materialization_specification_checksum,
)
from app.operational_paper_session_profiles import (
    OperationalPaperSessionProfileState,
    OperationalPaperSessionProfileStrategySnapshot,
)
from app.paper_trading.domain import PaperSessionConfig, paper_config_checksum, paper_session_id
from app.paper_trading.repository import PaperTradingRepository
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
from app.strategies.registry import StrategyPluginRegistry

_Code = runs.OperationalPaperSessionRunFailureCode


def _require(condition: bool, code: _Code = _Code.AUTHORITY_LOST) -> None:
    if not condition:
        raise runs.OperationalPaperSessionRunStateTransitionConflictError(
            details={"failure_code": code.value}
        )


@contextmanager
def _failure_boundary(code: _Code) -> Iterator[None]:
    """Keep only closed codes; never carry infrastructure exception text or causes."""
    try:
        yield
    except runs.OperationalPaperSessionRunStateTransitionConflictError as error:
        value = (error.details or {}).get("failure_code")
        safe_code = (
            _Code(value)
            if isinstance(value, str) and value in {item.value for item in _Code}
            else code
        )
        raise runs.OperationalPaperSessionRunStateTransitionConflictError(
            details={"failure_code": safe_code.value}
        ) from None
    except Exception:
        raise runs.OperationalPaperSessionRunStateTransitionConflictError(
            details={"failure_code": code.value}
        ) from None


@dataclass(frozen=True, slots=True)
class _Authority:
    specification: runs.OperationalPaperSessionRunEpochSpecification
    config: PaperSessionConfig
    strategy: OperationalPaperSessionProfileStrategySnapshot


class OperationalPaperSessionRunService:
    """Compose closed repositories without executing cycles or owning worker claims.

    Eligibility returns a verified config for one cycle boundary, never a cached
    capability. Gate 2E must additionally prove its current claim/fence and call this
    operation again before each cycle. PostgreSQL START insertion independently
    locks/revalidates the full authority chain until commit.
    """

    def __init__(
        self,
        *,
        repository: PostgresOperationalPaperSessionRunRepository,
        activation_repository: PostgresOperationalPaperSessionActivationRepository,
        materialization_repository: PostgresOperationalPaperSessionMaterializationRepository,
        authorization_repository: PostgresOperationalPaperCapitalAuthorizationRepository,
        profile_repository: PostgresOperationalPaperSessionProfileRepository,
        mandate_repository: PostgresOperationalMandateRepository,
        simulation_repository: SimulationRepository,
        paper_repository: PaperTradingRepository,
        registry: StrategyPluginRegistry,
        raw_catalog: JsonMarketDataCatalog,
        raw_store: ParquetCandleStore,
        raw_locks: DatasetLockManager,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._activations = activation_repository
        self._materializations = materialization_repository
        self._authorizations = authorization_repository
        self._profiles = profile_repository
        self._mandates = mandate_repository
        self._simulations = simulation_repository
        self._paper = paper_repository
        self._registry = registry
        self._catalog = raw_catalog
        self._datasets = LocalRawDatasetReadService(raw_catalog)
        self._store = raw_store
        self._locks = raw_locks
        self._clock = clock

    async def start(
        self,
        intent: runs.OperationalPaperSessionRunEpochStartIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> runs.OperationalPaperSessionRunEpoch:
        replay = await self._repository.resolve_start_replay(
            intent, actor_id=actor_id, idempotency_key=idempotency_key
        )
        if replay is not None:
            return replay
        authority = await self._authority(intent)
        self._validate_local(authority)
        # All local work is finished before these bounded database reads. The
        # repository's insert trigger closes the remaining DB race through commit.
        _require(await self._authority(intent) == authority)
        current = await self._repository.get_current_for_session(authority.specification.session_id)
        if current is not None:
            # A concurrent identical START may have committed after the first miss.
            replay = await self._repository.resolve_start_replay(
                intent, actor_id=actor_id, idempotency_key=idempotency_key
            )
            if replay is not None:
                return replay
            raise runs.OperationalPaperSessionRunCurrentEpochConflictError()
        return await self._repository.start(
            authority.specification,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=requested_at,
        )

    async def pause(
        self,
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> runs.OperationalPaperSessionRunEpochCommand:
        self._require_command(intent, runs.OperationalPaperSessionRunCommandType.PAUSE)
        return await self._repository.request_command(
            intent, actor_id=actor_id, idempotency_key=idempotency_key, now=requested_at
        )

    async def resume(
        self,
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> runs.OperationalPaperSessionRunEpochCommand:
        self._require_command(intent, runs.OperationalPaperSessionRunCommandType.RESUME)
        await self._execution_eligibility(intent.epoch_id, resuming=True)
        return await self._repository.request_command(
            intent, actor_id=actor_id, idempotency_key=idempotency_key, now=requested_at
        )

    async def stop(
        self,
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> runs.OperationalPaperSessionRunEpochCommand:
        self._require_command(intent, runs.OperationalPaperSessionRunCommandType.STOP)
        return await self._repository.request_command(
            intent, actor_id=actor_id, idempotency_key=idempotency_key, now=requested_at
        )

    async def validate_execution_eligibility(self, epoch_id: UUID) -> PaperSessionConfig:
        """Fresh first/every-cycle validation; does not settle or interrupt execution."""
        return await self._execution_eligibility(epoch_id, resuming=False)

    async def get(
        self,
        epoch_id: UUID,
    ) -> runs.OperationalPaperSessionRunEpoch:
        """Return one exact persisted run epoch."""
        epoch = await self._repository.get(epoch_id)
        if epoch is None:
            raise runs.OperationalPaperSessionRunNotFoundError()
        return epoch

    async def list_commands(
        self,
        epoch_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[runs.OperationalPaperSessionRunEpochCommand]:
        """Return bounded immutable command history for one existing epoch."""
        await self.get(epoch_id)
        return await self._repository.list_commands(
            epoch_id,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _require_command(
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
        kind: runs.OperationalPaperSessionRunCommandType,
    ) -> None:
        if not isinstance(intent, runs.OperationalPaperSessionRunEpochCommandIntent):
            raise runs.InvalidOperationalPaperSessionRunSpecificationError()
        if intent.command_type is not kind:
            raise runs.OperationalPaperSessionRunCommandConflictError()

    async def _execution_eligibility(self, epoch_id: UUID, *, resuming: bool) -> PaperSessionConfig:
        epoch = await self._executable_epoch(epoch_id, resuming=resuming)
        intent = runs.OperationalPaperSessionRunEpochStartIntent(
            activation_id=epoch.activation_id, activation_checksum=epoch.activation_checksum
        )
        authority = await self._authority(intent)
        _require(
            runs.operational_paper_session_run_epoch_specification_checksum(authority.specification)
            == epoch.epoch_checksum
        )
        config = self._validate_local(authority)
        _require(await self._authority(intent) == authority)
        latest = await self._executable_epoch(epoch_id, resuming=resuming)
        _require(latest.epoch_checksum == epoch.epoch_checksum)
        return config

    @staticmethod
    def _same_execution_snapshot(
        expected: runs.OperationalPaperSessionRunEpoch,
        current: runs.OperationalPaperSessionRunEpoch | None,
    ) -> bool:
        """Ignore only record-version and lease-heartbeat churn."""
        if current is None:
            return False

        for field in fields(expected):
            if field.name in {"record_version", "worker_claim"}:
                continue
            if getattr(expected, field.name) != getattr(current, field.name):
                return False

        expected_claim = expected.worker_claim
        current_claim = current.worker_claim

        if expected_claim is None or current_claim is None:
            return expected_claim is current_claim

        return (
            expected_claim.epoch_id == current_claim.epoch_id
            and expected_claim.worker_id == current_claim.worker_id
            and expected_claim.fencing_token == current_claim.fencing_token
            and expected_claim.claimed_at == current_claim.claimed_at
        )

    async def _executable_epoch(
        self, epoch_id: UUID, *, resuming: bool
    ) -> runs.OperationalPaperSessionRunEpoch:
        with _failure_boundary(_Code.INTERNAL_ERROR):
            epoch = await self._repository.get(epoch_id)
            _require(epoch is not None, _Code.LOCAL_STATE_INVALID)
            assert epoch is not None
            _require(
                not runs.operational_paper_session_run_epoch_is_terminal(epoch),
                _Code.LOCAL_STATE_INVALID,
            )
            if resuming:
                _require(
                    epoch.observed_state is runs.OperationalPaperSessionRunObservedState.PAUSED
                    and epoch.desired_state
                    is not runs.OperationalPaperSessionRunDesiredState.STOPPED,
                    _Code.LOCAL_STATE_INVALID,
                )
            else:
                _require(
                    epoch.desired_state is runs.OperationalPaperSessionRunDesiredState.RUNNING
                    and epoch.observed_state
                    not in {
                        runs.OperationalPaperSessionRunObservedState.PAUSED,
                        runs.OperationalPaperSessionRunObservedState.STOPPING,
                    },
                    _Code.LOCAL_STATE_INVALID,
                )
            current = await self._repository.get_current_for_session(epoch.session_id)
            _require(
                self._same_execution_snapshot(epoch, current),
                _Code.LOCAL_STATE_INVALID,
            )
            return epoch

    async def _authority(
        self, intent: runs.OperationalPaperSessionRunEpochStartIntent
    ) -> _Authority:
        with _failure_boundary(_Code.INTERNAL_ERROR):
            activation = await self._activations.get(intent.activation_id)
            _require(activation is not None)
            assert activation is not None
            _require(
                activation.state is OperationalPaperSessionActivationState.AUTHORIZED,
                _Code.ACTIVATION_REVOKED,
            )
            _require(
                activation.activation_id == intent.activation_id
                and activation.activation_checksum == intent.activation_checksum
            )
            materialization = await self._materializations.get(activation.materialization_id)
            _require(materialization is not None)
            assert materialization is not None
            _require(
                materialization.state is OperationalPaperSessionMaterializationState.MATERIALIZED
            )
            _require(
                materialization.materialization_id == activation.materialization_id
                and materialization.materialization_checksum == activation.materialization_checksum
                and operational_paper_session_activation_specification_checksum(
                    build_operational_paper_session_activation_specification(materialization)
                )
                == activation.activation_checksum
            )
            authorization = await self._authorizations.get(
                activation.authorization_binding.authorization_id
            )
            _require(authorization is not None)
            assert authorization is not None
            _require(
                authorization.state is OperationalPaperCapitalAuthorizationState.AUTHORIZED
                and authorization.authorization_id
                == activation.authorization_binding.authorization_id
                and authorization.authorization_checksum
                == activation.authorization_binding.authorization_checksum
                and authorization.simulation_id == activation.simulation_id
            )
            profile_pair = await self._profiles.get_current(activation.profile_binding.profile_id)
            _require(profile_pair is not None)
            assert profile_pair is not None
            profile, revision = profile_pair
            binding = activation.profile_binding
            _require(
                profile.profile_id == binding.profile_id
                and profile.state is OperationalPaperSessionProfileState.APPROVED
                and profile.approved_revision == binding.approved_revision
                and profile.approved_checksum == binding.specification_checksum
                and revision.profile_id == binding.profile_id
                and revision.revision == binding.approved_revision
                and revision.specification_checksum == binding.specification_checksum
            )
            mandate_pair = await self._mandates.get_current(activation.mandate_binding.mandate_id)
            _require(mandate_pair is not None)
            assert mandate_pair is not None
            mandate, mandate_revision = mandate_pair
            mandate_binding = activation.mandate_binding
            _require(
                mandate.mandate_id == mandate_binding.mandate_id
                and mandate.state is OperationalMandateState.APPROVED
                and mandate.approved_revision == mandate_binding.approved_revision
                and mandate.approved_checksum == mandate_binding.specification_checksum
                and mandate_revision.mandate_id == mandate_binding.mandate_id
                and mandate_revision.revision == mandate_binding.approved_revision
                and mandate_revision.specification_checksum
                == mandate_binding.specification_checksum
                and revision.specification.selected_instrument
                in mandate_revision.specification.instruments
            )
            try:
                simulation = (await self._simulations.get(activation.simulation_id)).simulation
            except SimulationNotFoundError:
                raise runs.OperationalPaperSessionRunStateTransitionConflictError(
                    details={"failure_code": _Code.AUTHORITY_LOST.value}
                ) from None
            _require(
                simulation.id == activation.simulation_id
                and simulation.status is SimulationStatus.ACTIVE
                and simulation.currency == authorization.quote_asset
                and simulation.currency == revision.specification.selected_instrument.pair.quote
            )
            with _failure_boundary(_Code.AUTHORITY_LOST):
                plan = build_operational_paper_session_materialization_plan(
                    authorization_id=authorization.authorization_id,
                    authorization_specification=OperationalPaperCapitalAuthorizationSpecification(
                        schema_version=authorization.schema_version,
                        profile_binding=authorization.profile_binding,
                        simulation_id=authorization.simulation_id,
                        quote_asset=authorization.quote_asset,
                        authorized_capital=authorization.authorized_capital,
                    ),
                    authorization_checksum=authorization.authorization_checksum,
                    profile_revision=revision,
                )
            _require(
                operational_paper_session_materialization_specification_checksum(plan.specification)
                == materialization.materialization_checksum
            )
            return _Authority(
                runs.build_operational_paper_session_run_epoch_specification(activation),
                plan.config,
                revision.specification.strategy_snapshot,
            )

    def _validate_local(self, authority: _Authority) -> PaperSessionConfig:
        with _failure_boundary(_Code.CONFIG_UNAVAILABLE):
            config = self._paper.load_config(authority.specification.session_id)
        with _failure_boundary(_Code.CONFIG_IDENTITY_CONFLICT):
            _require(
                config == authority.config
                and paper_session_id(config) == authority.specification.session_id
                and paper_config_checksum(config) == authority.specification.config_checksum,
                _Code.CONFIG_IDENTITY_CONFLICT,
            )
        with _failure_boundary(_Code.PLUGIN_UNAVAILABLE):
            frozen = authority.strategy
            descriptor = self._registry.resolve(
                frozen.plugin_name, frozen.plugin_version
            ).descriptor
            _require(
                descriptor.name == frozen.plugin_name
                and descriptor.version == frozen.plugin_version
                and descriptor.schema_version == frozen.plugin_schema_version
                and descriptor.lifecycle_version == frozen.strategy_lifecycle_version,
                _Code.PLUGIN_UNAVAILABLE,
            )
        with _failure_boundary(_Code.RAW_NOT_READY):
            self._validate_raw(config)
        return config

    def _validate_raw(self, config: PaperSessionConfig) -> None:
        """Reuse catalog integrity, verified storage and quality without recovery.

        Check exactly the complete replay range, including warmup, bounded by the
        frozen max_candles. A persisted FULL_DATASET quality baseline is not needed:
        the existing validator checks the actual authenticated rows for this range.
        """
        selector = MarketDatasetSelector(
            Exchange.BINANCE, MarketType.SPOT, config.pair, config.timeframe
        )
        with self._locks.snapshot(selector.canonical_key):
            snapshot = self._datasets.get(encode_dataset_id(selector))
            start, end = snapshot.coverage_start, snapshot.coverage_end
            _require(
                start is not None
                and end is not None
                and start <= config.context_start
                and end > config.start_at,
                _Code.RAW_NOT_READY,
            )
            assert end is not None
            data_range = DataRange(config.context_start, end)
            expected = expected_candle_count(data_range, config.timeframe)
            _require(0 < expected <= config.max_candles, _Code.RAW_NOT_READY)
            metadata = self._catalog.get_dataset_snapshot(
                selector.canonical_key, timeout_seconds=10
            )
            _require(metadata is not None, _Code.RAW_NOT_READY)
            assert metadata is not None
            manifest = metadata.partition_integrity
            _require(manifest is not None, _Code.RAW_NOT_READY)
            assert manifest is not None
            _require(manifest.bound_dataset_version == snapshot.version, _Code.RAW_NOT_READY)
            candles = self._store.read_verified(
                selector.exchange,
                selector.market_type,
                config.pair,
                config.timeframe,
                data_range,
                {entry.relative_path: entry.checksum for entry in manifest.entries},
            )
            quality = MarketDataQualityValidator().validate(
                candles, timeframe=config.timeframe, expected_range=data_range, now=self._clock()
            )
            _require(quality.is_valid and len(candles) == expected, _Code.RAW_NOT_READY)
            _require(
                all(
                    candle.is_closed
                    and candle.close_time
                    in {
                        candle.open_time + config.timeframe.duration,
                        candle.open_time + config.timeframe.duration - timedelta(milliseconds=1),
                    }
                    for candle in candles
                ),
                _Code.RAW_NOT_READY,
            )
