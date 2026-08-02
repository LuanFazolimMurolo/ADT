"""Bounded sequential executor for fully validated experiment plans."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.backtesting.artifacts import BacktestArtifactStore, build_run_id
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.errors import BacktestResultConflictError
from app.backtesting.verifier import BacktestResultVerifier
from app.domain.errors import DomainError
from app.market_data.datasets import DatasetManifest, DatasetSnapshot
from app.market_data.filesystem import market_root
from app.market_data.snapshots import MarketDatasetReader
from app.optimization.errors import (
    ExperimentExecutionArtifactVerificationError,
    ExperimentExecutionLimitExceededError,
    ExperimentExecutionManifestLimitExceededError,
    ExperimentPlanningError,
    InvalidExperimentExecutionPlanError,
)
from app.optimization.experiment_domain import (
    ExperimentPlan,
    PlannedRunSpec,
    validate_experiment_plan,
)
from app.optimization.experiment_execution_domain import (
    ABSOLUTE_MAX_EXECUTION_SPECS,
    DEFAULT_MAX_EXECUTION_SPECS,
    MAX_EXECUTION_MANIFEST_BYTES,
    ExperimentExecutionFailure,
    ExperimentExecutionManifest,
    PlannedRunExecution,
    PlannedRunExecutionStatus,
    build_experiment_execution_manifest,
    build_planned_run_execution,
    maximum_execution_manifest_size,
    validate_execution_manifest_against_plan,
    validate_execution_transition,
)
from app.optimization.experiment_execution_repository import (
    ExperimentExecutionPublication,
    ExperimentExecutionRepository,
)
from app.optimization.experiment_planning import ExperimentPlanningService
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.domain import IndicatorCapability
from app.strategies.errors import StrategyPluginError
from app.strategies.registry import StrategyPluginRegistry

EngineFactory = Callable[[], DeterministicBacktestEngine]
ContractLoader = Callable[[ExperimentPlan], tuple[DatasetSnapshot, DatasetManifest]]


def verify_published_execution_manifest(
    execution: ExperimentExecutionManifest,
    plan: ExperimentPlan,
    snapshot: DatasetSnapshot,
    artifact_store: BacktestArtifactStore,
    result_verifier: BacktestResultVerifier,
) -> ExperimentExecutionManifest:
    """Verify every successful artifact referenced by one immutable execution."""

    validate_execution_manifest_against_plan(execution, plan, snapshot)
    for record in execution.records:
        if record.status is PlannedRunExecutionStatus.FAILED:
            continue
        try:
            if record.run_id is None or record.artifact_path is None:
                raise ValueError("successful record is incomplete")
            expected_path = artifact_store.relative_run_path(record.run_id)
            if record.artifact_path != expected_path:
                raise ValueError("artifact path diverges from the configured store")
            target = artifact_store.root / record.run_id
            if not target.is_dir():
                raise ValueError("referenced artifact directory is absent")
            verification = result_verifier.verify(record.run_id)
        except Exception as error:
            raise ExperimentExecutionArtifactVerificationError() from error
        if (
            verification.run_id.value != record.run_id
            or verification.logical_result_checksum != record.logical_result_checksum
        ):
            raise ExperimentExecutionArtifactVerificationError(
                "O artefato verificado diverge do registro de execução."
            )
    return execution


class ExperimentExecutionService:
    """Execute one run spec at a time and continue after isolated failures."""

    def __init__(
        self,
        data_dir: Path,
        *,
        registry: StrategyPluginRegistry | None = None,
        available_indicators: tuple[IndicatorCapability, ...] | None = None,
        max_specs: int = DEFAULT_MAX_EXECUTION_SPECS,
        max_manifest_bytes: int = MAX_EXECUTION_MANIFEST_BYTES,
        engine_factory: EngineFactory | None = None,
        artifact_store: BacktestArtifactStore | None = None,
        result_verifier: BacktestResultVerifier | None = None,
        execution_repository: ExperimentExecutionRepository | None = None,
        contract_loader: ContractLoader | None = None,
    ) -> None:
        if (
            isinstance(max_specs, bool)
            or not isinstance(max_specs, int)
            or not 1 <= max_specs <= ABSOLUTE_MAX_EXECUTION_SPECS
        ):
            raise ValueError("experiment execution limit is invalid")
        if (
            isinstance(max_manifest_bytes, bool)
            or not isinstance(max_manifest_bytes, int)
            or not 1 <= max_manifest_bytes <= MAX_EXECUTION_MANIFEST_BYTES
        ):
            raise ValueError("experiment execution manifest limit is invalid")
        self._data_dir = data_dir
        self._registry = registry or StrategyPluginRegistry.builtins()
        self._available_indicators = (
            builtin_indicator_capabilities()
            if available_indicators is None
            else tuple(available_indicators)
        )
        self._planning = ExperimentPlanningService(
            self._registry, available_indicators=self._available_indicators
        )
        self._max_specs = max_specs
        self._max_manifest_bytes = max_manifest_bytes
        self._engine_factory = engine_factory or (
            lambda: DeterministicBacktestEngine.from_data_dir(data_dir)
        )
        self._store = artifact_store or BacktestArtifactStore(data_dir)
        self._verifier = result_verifier or BacktestResultVerifier(data_dir)
        self._repository = execution_repository or ExperimentExecutionRepository(data_dir)
        self._contract_loader = contract_loader or self._load_contracts
        self._market = market_root(data_dir)

    @property
    def artifact_store(self) -> BacktestArtifactStore:
        """Expose the configured official store to composing optimization services."""

        return self._store

    @property
    def result_verifier(self) -> BacktestResultVerifier:
        """Expose the configured official verifier to composing optimization services."""

        return self._verifier

    def execute(self, plan: ExperimentPlan) -> ExperimentExecutionPublication:
        """Validate once, then execute specs in their canonical stored order."""

        if not isinstance(plan, ExperimentPlan):
            raise InvalidExperimentExecutionPlanError("experiment plan contract is invalid")
        try:
            validate_experiment_plan(plan)
        except ExperimentPlanningError as error:
            raise InvalidExperimentExecutionPlanError(error.message) from error
        except Exception as error:
            raise InvalidExperimentExecutionPlanError() from error
        if len(plan.run_specs) > self._max_specs:
            raise ExperimentExecutionLimitExceededError()
        maximum_artifact_path = (self._store.root / ("f" * 64)).relative_to(self._market).as_posix()
        if maximum_execution_manifest_size(plan, maximum_artifact_path) > self._max_manifest_bytes:
            raise ExperimentExecutionManifestLimitExceededError()
        try:
            snapshot, manifest = self._contract_loader(plan)
            self._planning.validate(plan, snapshot, manifest)
        except ExperimentPlanningError as error:
            raise InvalidExperimentExecutionPlanError(error.message) from error
        except Exception as error:
            if isinstance(error, DomainError):
                raise InvalidExperimentExecutionPlanError(error.message) from error
            raise InvalidExperimentExecutionPlanError() from error

        records: list[PlannedRunExecution] = []
        for spec in plan.run_specs:
            validate_execution_transition(
                PlannedRunExecutionStatus.PENDING, PlannedRunExecutionStatus.RUNNING
            )
            try:
                record = self._execute_spec(spec, snapshot)
            except Exception as error:
                validate_execution_transition(
                    PlannedRunExecutionStatus.RUNNING, PlannedRunExecutionStatus.FAILED
                )
                record = self._failed_record(spec, _safe_failure(error))
            records.append(record)

        execution = build_experiment_execution_manifest(
            experiment_id=plan.experiment_id,
            plan_checksum=plan.checksum,
            plan_schema_version=plan.schema_version,
            ordering_policy=plan.ordering_policy,
            records=tuple(records),
        )
        verify_published_execution_manifest(
            execution,
            plan,
            snapshot,
            self._store,
            self._verifier,
        )
        return self._repository.publish(execution)

    def _execute_spec(self, spec: PlannedRunSpec, snapshot: DatasetSnapshot) -> PlannedRunExecution:
        expected_run_id = build_run_id(spec.backtest_config, snapshot)
        target = self._store.root / expected_run_id.value
        if target.exists():
            verification = self._verifier.verify(expected_run_id.value)
            return self._successful_record(
                spec,
                status=PlannedRunExecutionStatus.REUSED,
                run_id=verification.run_id.value,
                logical_checksum=verification.logical_result_checksum,
            )

        strategy = self._registry.build(
            spec.plugin.name,
            spec.plugin.version,
            dict(spec.combination.parameters),
            available_indicators=self._available_indicators,
        )
        engine = self._engine_factory()
        execution = engine.run(spec.backtest_config, strategy)
        result = self._store.publish(spec.backtest_config, execution)
        if result.run_id != expected_run_id:
            raise BacktestResultConflictError("A execução produziu identidade divergente.")
        verification = self._verifier.verify(expected_run_id.value)
        if verification.logical_result_checksum != result.logical_result_checksum:
            raise BacktestResultConflictError("A verificação produziu checksum divergente.")
        return self._successful_record(
            spec,
            status=PlannedRunExecutionStatus.COMPLETED,
            run_id=expected_run_id.value,
            logical_checksum=verification.logical_result_checksum,
        )

    def _successful_record(
        self,
        spec: PlannedRunSpec,
        *,
        status: PlannedRunExecutionStatus,
        run_id: str,
        logical_checksum: str,
    ) -> PlannedRunExecution:
        validate_execution_transition(PlannedRunExecutionStatus.RUNNING, status)
        relative = (self._store.root / run_id).relative_to(self._market).as_posix()
        return build_planned_run_execution(
            run_spec_id=spec.run_spec_id,
            experiment_id=spec.experiment_id,
            global_index=spec.global_index,
            combination_index=spec.combination.index,
            combination_id=spec.combination.combination_id,
            segment_index=spec.segment.index,
            segment_id=spec.segment.segment_id,
            purpose=spec.purpose,
            status=status,
            run_id=run_id,
            logical_result_checksum=logical_checksum,
            artifact_path=relative,
            reused=status is PlannedRunExecutionStatus.REUSED,
            verified=True,
        )

    @staticmethod
    def _failed_record(
        spec: PlannedRunSpec, error: ExperimentExecutionFailure
    ) -> PlannedRunExecution:
        return build_planned_run_execution(
            run_spec_id=spec.run_spec_id,
            experiment_id=spec.experiment_id,
            global_index=spec.global_index,
            combination_index=spec.combination.index,
            combination_id=spec.combination.combination_id,
            segment_index=spec.segment.index,
            segment_id=spec.segment.segment_id,
            purpose=spec.purpose,
            status=PlannedRunExecutionStatus.FAILED,
            error=error,
        )

    def _load_contracts(self, plan: ExperimentPlan) -> tuple[DatasetSnapshot, DatasetManifest]:
        reader = MarketDatasetReader(self._data_dir)
        snapshot = reader.open_snapshot(plan.snapshot.snapshot_id)
        return snapshot, reader.manifest()


def _safe_failure(error: Exception) -> ExperimentExecutionFailure:
    if isinstance(error, DomainError):
        return ExperimentExecutionFailure(error.code, error.message[:500])
    if isinstance(error, StrategyPluginError):
        return ExperimentExecutionFailure(
            "strategy_plugin_error", "O plugin de estratégia falhou durante a preparação."
        )
    return ExperimentExecutionFailure(
        "unexpected_execution_error", "A especificação falhou durante a execução local."
    )
