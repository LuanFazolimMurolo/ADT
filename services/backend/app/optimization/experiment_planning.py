"""Pure orchestration for deterministic reproducible experiment plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.backtesting.domain import BacktestConfig, StrategyDescriptor
from app.market_data.datasets import DatasetManifest, DatasetSnapshot
from app.optimization.canonical import deterministic_id, document_checksum
from app.optimization.domain import (
    ParameterCombination,
    ParameterSearchExpansion,
    ParameterSearchSpace,
)
from app.optimization.errors import (
    IncompatibleExperimentDocumentError,
    IncompatibleExperimentPluginError,
    IncompatibleExperimentSearchSpaceError,
    IncompatibleExperimentSnapshotError,
    IncompatibleExperimentTemporalPlanError,
    InvalidSearchCombinationError,
    ParameterSearchError,
    TemporalSegmentationError,
)
from app.optimization.experiment_domain import (
    DEFAULT_MAX_RUN_SPECS,
    EXPERIMENT_SCHEMA_VERSION,
    PURPOSE_BY_TEMPORAL_ROLE,
    ExperimentBacktestConfiguration,
    ExperimentHoldoutPolicy,
    ExperimentOrderingPolicy,
    ExperimentPlan,
    ExperimentPluginReference,
    ExperimentRunPurpose,
    PlannedRunSpec,
    calculate_run_spec_cardinality,
    experiment_plan_payload,
    experiment_plan_values_payload,
    planned_run_spec_id_from_payload,
    planned_run_spec_values_payload,
    validate_experiment_backtest_configuration,
    validate_experiment_plan,
    validate_run_spec_limit,
)
from app.optimization.parameter_search import ParameterSearchService
from app.optimization.temporal_domain import (
    TemporalSegment,
    TemporalSegmentationPlan,
    TemporalSnapshotReference,
)
from app.optimization.temporal_segmentation import TemporalSegmentationService
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.domain import IndicatorCapability, StrategyPluginDescriptor
from app.strategies.errors import StrategyPluginError
from app.strategies.registry import StrategyPluginRegistry


@dataclass(frozen=True, slots=True)
class _PendingRunSpec:
    global_index: int
    combination: ParameterCombination
    segment: TemporalSegment
    backtest_config: BacktestConfig
    purpose: ExperimentRunPurpose
    eligible_for_model_selection: bool
    checksum: str


class ExperimentPlanningService:
    """Join validated contracts without candles, execution, persistence or I/O."""

    def __init__(
        self,
        registry: StrategyPluginRegistry | None = None,
        *,
        available_indicators: tuple[IndicatorCapability, ...] | None = None,
    ) -> None:
        self._registry = registry or StrategyPluginRegistry.builtins()
        self._available_indicators = (
            builtin_indicator_capabilities()
            if available_indicators is None
            else tuple(available_indicators)
        )
        self._parameter_search = ParameterSearchService(
            self._registry,
            available_indicators=self._available_indicators,
        )
        self._temporal = TemporalSegmentationService()

    def create(
        self,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
        temporal_plan: TemporalSegmentationPlan,
        search_space: ParameterSearchSpace,
        *,
        plugin_name: str,
        plugin_version: str,
        backtest_configuration: ExperimentBacktestConfiguration,
        max_run_specs: int = DEFAULT_MAX_RUN_SPECS,
    ) -> ExperimentPlan:
        """Create all combination-major, segment-minor planned run specs."""

        self._validate_snapshot_and_temporal(snapshot, manifest, temporal_plan)
        descriptor = self._compatible_plugin(search_space, plugin_name, plugin_version)
        validate_run_spec_limit(max_run_specs)
        try:
            cardinality = calculate_run_spec_cardinality(search_space.cardinality, max_run_specs)
        except AttributeError:
            raise IncompatibleExperimentSearchSpaceError(
                "parameter search-space contract is invalid"
            ) from None

        validate_experiment_backtest_configuration(backtest_configuration)
        # Every cheap structural/configuration check precedes plugin factory invocation.
        expansion = self._expand(search_space)
        plugin = _plugin_reference(descriptor)
        combinations = expansion.combinations
        pending = self._pending_specs(
            combinations,
            temporal_plan,
            snapshot=temporal_plan.snapshot,
            plugin=plugin,
            configuration=backtest_configuration,
        )
        semantic_specs: list[dict[str, object]] = [
            {
                "run_spec": _pending_payload(item),
                "checksum": item.checksum,
            }
            for item in pending
        ]
        identity_payload = experiment_plan_values_payload(
            snapshot=temporal_plan.snapshot,
            temporal_plan=temporal_plan,
            search_space=search_space,
            combinations=combinations,
            plugin=plugin,
            backtest_configuration=backtest_configuration,
            cardinality=cardinality,
            max_run_specs=max_run_specs,
            holdout_policy=ExperimentHoldoutPolicy.TEST_IS_FINAL_HOLDOUT,
            ordering_policy=ExperimentOrderingPolicy.COMBINATION_THEN_SEGMENT,
            schema_version=EXPERIMENT_SCHEMA_VERSION,
            specs=semantic_specs,
        )
        experiment_id = deterministic_id("adt-experiment-plan-v1", identity_payload)
        del identity_payload, semantic_specs
        run_specs = tuple(
            PlannedRunSpec(
                experiment_id=experiment_id,
                global_index=item.global_index,
                combination=item.combination,
                segment=item.segment,
                snapshot=temporal_plan.snapshot,
                plugin=plugin,
                backtest_config=item.backtest_config,
                purpose=item.purpose,
                eligible_for_model_selection=item.eligible_for_model_selection,
                checksum=item.checksum,
                run_spec_id=planned_run_spec_id_from_payload(
                    experiment_id, item.checksum, _pending_payload(item)
                ),
            )
            for item in pending
        )
        provisional = _plan_projection(
            snapshot=temporal_plan.snapshot,
            temporal_plan=temporal_plan,
            search_space=search_space,
            combinations=combinations,
            plugin=plugin,
            backtest_configuration=backtest_configuration,
            run_specs=run_specs,
            cardinality=cardinality,
            max_run_specs=max_run_specs,
            experiment_id=experiment_id,
        )
        return ExperimentPlan(
            snapshot=provisional.snapshot,
            temporal_plan=provisional.temporal_plan,
            search_space=provisional.search_space,
            combinations=provisional.combinations,
            plugin=provisional.plugin,
            backtest_configuration=provisional.backtest_configuration,
            run_specs=provisional.run_specs,
            cardinality=provisional.cardinality,
            max_run_specs=provisional.max_run_specs,
            checksum=document_checksum(experiment_plan_payload(provisional)),
            experiment_id=experiment_id,
        )

    def validate(
        self,
        plan: ExperimentPlan,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
    ) -> ExperimentPlan:
        """Revalidate hashes and source contracts before any future consumption."""

        if not isinstance(plan, ExperimentPlan):
            raise IncompatibleExperimentDocumentError("experiment plan contract is invalid")
        validate_experiment_plan(plan)
        self._validate_snapshot_and_temporal(snapshot, manifest, plan.temporal_plan)
        descriptor = self._compatible_plugin(
            plan.search_space, plan.plugin.name, plan.plugin.version
        )
        if _plugin_reference(descriptor) != plan.plugin:
            raise IncompatibleExperimentPluginError("registered plugin versions changed")
        validate_run_spec_limit(plan.max_run_specs)
        calculate_run_spec_cardinality(plan.search_space.cardinality, plan.max_run_specs)
        expansion = self._expand(plan.search_space)
        if expansion.combinations != plan.combinations:
            raise IncompatibleExperimentSearchSpaceError(
                "planned combinations diverge from deterministic expansion"
            )
        validate_experiment_backtest_configuration(plan.backtest_configuration)
        return plan

    def to_document(
        self,
        plan: ExperimentPlan,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
    ) -> dict[str, object]:
        """Return a fresh canonical envelope after full semantic revalidation."""

        from app.optimization.experiment_documents import experiment_to_document

        self.validate(plan, snapshot, manifest)
        return experiment_to_document(plan)

    def from_document(
        self,
        envelope: Mapping[str, object],
        *,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
    ) -> ExperimentPlan:
        """Strictly decode and bind a document to legitimate Phase 2C contracts."""

        from app.optimization.experiment_documents import decode_experiment_document

        plan = decode_experiment_document(envelope)
        return self.validate(plan, snapshot, manifest)

    def _validate_snapshot_and_temporal(
        self,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
        temporal_plan: TemporalSegmentationPlan,
    ) -> None:
        if not isinstance(snapshot, DatasetSnapshot) or not isinstance(manifest, DatasetManifest):
            raise IncompatibleExperimentSnapshotError(
                "snapshot and manifest contracts are required"
            )
        if not isinstance(temporal_plan, TemporalSegmentationPlan):
            raise IncompatibleExperimentTemporalPlanError("temporal plan contract is required")
        try:
            self._temporal.validate_for_snapshot(temporal_plan, snapshot, manifest)
        except TemporalSegmentationError as error:
            if "snapshot" in error.code:
                raise IncompatibleExperimentSnapshotError(error.message) from error
            raise IncompatibleExperimentTemporalPlanError(error.message) from error

    def _compatible_plugin(
        self,
        search_space: ParameterSearchSpace,
        plugin_name: str,
        plugin_version: str,
    ) -> StrategyPluginDescriptor:
        if not isinstance(search_space, ParameterSearchSpace):
            raise IncompatibleExperimentSearchSpaceError(
                "parameter search-space contract is required"
            )
        try:
            from app.optimization.domain import validate_search_space_structure

            validate_search_space_structure(search_space)
        except ParameterSearchError as error:
            raise IncompatibleExperimentSearchSpaceError(error.message) from error
        if plugin_name != search_space.plugin_name or plugin_version != search_space.plugin_version:
            raise IncompatibleExperimentPluginError(
                "selected plugin diverges from parameter search space"
            )
        try:
            descriptor = self._registry.resolve(plugin_name, plugin_version).descriptor
        except (AttributeError, TypeError, StrategyPluginError) as error:
            raise IncompatibleExperimentPluginError(
                "selected strategy plugin is not registered"
            ) from error
        if (
            descriptor.schema_version != search_space.plugin_schema_version
            or descriptor.lifecycle_version != search_space.plugin_lifecycle_version
        ):
            raise IncompatibleExperimentPluginError(
                "registered plugin descriptor versions diverge from search space"
            )
        return descriptor

    def _expand(self, search_space: ParameterSearchSpace) -> ParameterSearchExpansion:
        try:
            return self._parameter_search.expand(search_space)
        except InvalidSearchCombinationError:
            raise
        except ParameterSearchError as error:
            raise IncompatibleExperimentSearchSpaceError(error.message) from error

    @staticmethod
    def _pending_specs(
        combinations: tuple[ParameterCombination, ...],
        temporal_plan: TemporalSegmentationPlan,
        *,
        snapshot: TemporalSnapshotReference,
        plugin: ExperimentPluginReference,
        configuration: ExperimentBacktestConfiguration,
    ) -> tuple[_PendingRunSpec, ...]:
        pending: list[_PendingRunSpec] = []
        global_index = 0
        for combination in combinations:
            strategy = StrategyDescriptor(
                plugin.name,
                plugin.version,
                combination.parameters,
            )
            for segment in temporal_plan.segments:
                purpose = PURPOSE_BY_TEMPORAL_ROLE[segment.role]
                eligible = purpose is ExperimentRunPurpose.MODEL_SELECTION
                backtest_config = configuration.for_segment(
                    snapshot_id=snapshot.snapshot_id,
                    strategy=strategy,
                    segment=segment,
                )
                payload = planned_run_spec_values_payload(
                    global_index=global_index,
                    combination=combination,
                    segment=segment,
                    purpose=purpose,
                    eligible_for_model_selection=eligible,
                )
                pending.append(
                    _PendingRunSpec(
                        global_index=global_index,
                        combination=combination,
                        segment=segment,
                        backtest_config=backtest_config,
                        purpose=purpose,
                        eligible_for_model_selection=eligible,
                        checksum=document_checksum(payload),
                    )
                )
                global_index += 1
        return tuple(pending)


def _pending_payload(item: _PendingRunSpec) -> dict[str, object]:
    return planned_run_spec_values_payload(
        global_index=item.global_index,
        combination=item.combination,
        segment=item.segment,
        purpose=item.purpose,
        eligible_for_model_selection=item.eligible_for_model_selection,
    )


def _plugin_reference(descriptor: StrategyPluginDescriptor) -> ExperimentPluginReference:
    return ExperimentPluginReference(
        name=descriptor.name,
        version=descriptor.version,
        schema_version=descriptor.schema_version,
        lifecycle_version=descriptor.lifecycle_version,
    )


def _plan_projection(
    *,
    snapshot: TemporalSnapshotReference,
    temporal_plan: TemporalSegmentationPlan,
    search_space: ParameterSearchSpace,
    combinations: tuple[ParameterCombination, ...],
    plugin: ExperimentPluginReference,
    backtest_configuration: ExperimentBacktestConfiguration,
    run_specs: tuple[PlannedRunSpec, ...],
    cardinality: int,
    max_run_specs: int,
    experiment_id: str,
) -> ExperimentPlan:
    plan = object.__new__(ExperimentPlan)
    values = {
        "snapshot": snapshot,
        "temporal_plan": temporal_plan,
        "search_space": search_space,
        "combinations": combinations,
        "plugin": plugin,
        "backtest_configuration": backtest_configuration,
        "run_specs": run_specs,
        "cardinality": cardinality,
        "max_run_specs": max_run_specs,
        "checksum": "0" * 64,
        "experiment_id": experiment_id,
        "holdout_policy": ExperimentHoldoutPolicy.TEST_IS_FINAL_HOLDOUT,
        "ordering_policy": ExperimentOrderingPolicy.COMBINATION_THEN_SEGMENT,
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
    }
    for name, value in values.items():
        object.__setattr__(plan, name, value)
    return plan


__all__ = ["ExperimentPlanningService"]
