"""Deterministic rolling walk-forward planner composed from Phases 4-01 to 4-03."""

from __future__ import annotations

from datetime import timedelta

from app.market_data.datasets import DatasetManifest, DatasetSnapshot
from app.market_data.domain import DataRange
from app.market_data.errors import MarketDataError
from app.market_data.snapshots import validate_snapshot_contract
from app.market_data.timeframes import get_timeframe
from app.optimization.canonical import canonical_json_bytes, deterministic_id, document_checksum
from app.optimization.documents import to_document as search_space_to_document
from app.optimization.domain import ParameterSearchSpace, validate_search_space_structure
from app.optimization.errors import (
    IncompatibleWalkForwardPlanError,
    InsufficientWalkForwardFoldsError,
    WalkForwardLimitExceededError,
)
from app.optimization.experiment_domain import ExperimentBacktestConfiguration
from app.optimization.experiment_planning import ExperimentPlanningService
from app.optimization.temporal_segmentation import TemporalSegmentationService
from app.optimization.walk_forward_domain import (
    ABSOLUTE_MAX_TOTAL_SPECS,
    DEFAULT_MAX_TOTAL_SPECS,
    MAX_WALK_FORWARD_MANIFEST_BYTES,
    WalkForwardFoldPlan,
    WalkForwardOrderingPolicy,
    WalkForwardPlan,
    WalkForwardSelectionPolicy,
    WalkForwardWindowPolicy,
    fold_plan_values_payload,
    validate_selection_policy,
    validate_walk_forward_plan,
    validate_window_policy,
    walk_forward_plan_values_payload,
)
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.domain import IndicatorCapability
from app.strategies.registry import StrategyPluginRegistry


class WalkForwardPlanningService:
    """Create chronological folds without reading candles or executing backtests."""

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
        self._temporal = TemporalSegmentationService()
        self._experiments = ExperimentPlanningService(
            self._registry,
            available_indicators=self._available_indicators,
        )

    def create(
        self,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
        search_space: ParameterSearchSpace,
        *,
        plugin_name: str,
        plugin_version: str,
        backtest_configuration: ExperimentBacktestConfiguration,
        window_policy: WalkForwardWindowPolicy,
        selection_policy: WalkForwardSelectionPolicy,
        max_total_specs: int = DEFAULT_MAX_TOTAL_SPECS,
    ) -> WalkForwardPlan:
        """Build every complete rolling fold after global cardinality preflight."""

        try:
            validate_snapshot_contract(snapshot, manifest)
        except MarketDataError as error:
            raise IncompatibleWalkForwardPlanError(error.message) from error
        validate_window_policy(window_policy)
        validate_selection_policy(selection_policy)
        combination_count = validate_search_space_structure(search_space)
        _validate_total_limit(max_total_specs)
        timeframe = get_timeframe(manifest.target_timeframe)
        total_snapshot_candles = _slot_count(snapshot.data_range, timeframe.duration)
        window_candles = (
            window_policy.train_candles
            + window_policy.validation_candles
            + window_policy.test_candles
        )
        usable_after_warmup = total_snapshot_candles - window_policy.warmup_candles
        fold_count = (
            0
            if usable_after_warmup < window_candles
            else 1 + (usable_after_warmup - window_candles) // window_policy.test_candles
        )
        if fold_count < 2:
            raise InsufficientWalkForwardFoldsError()
        if fold_count > window_policy.max_folds:
            raise WalkForwardLimitExceededError("fold count exceeds the configured maximum")
        specs_per_fold = combination_count * 3
        total_specs = fold_count * specs_per_fold
        if total_specs > max_total_specs:
            raise WalkForwardLimitExceededError("total spec count exceeds the configured maximum")
        if (
            maximum_walk_forward_plan_bytes(search_space, fold_count, total_specs)
            > MAX_WALK_FORWARD_MANIFEST_BYTES
            or maximum_walk_forward_execution_bytes(
                search_space,
                fold_count,
                combination_count,
            )
            > MAX_WALK_FORWARD_MANIFEST_BYTES
        ):
            raise WalkForwardLimitExceededError(
                "walk-forward plan would exceed its document byte limit"
            )
        trailing_candles = usable_after_warmup - (
            window_candles + (fold_count - 1) * window_policy.test_candles
        )

        folds: list[WalkForwardFoldPlan] = []
        first_start = snapshot.data_range.start + timeframe.duration * window_policy.warmup_candles
        window_duration = timeframe.duration * window_candles
        for fold_index in range(fold_count):
            selected_start = first_start + timeframe.duration * (
                fold_index * window_policy.test_candles
            )
            selected = DataRange(selected_start, selected_start + window_duration)
            temporal_plan = self._temporal.create(
                snapshot,
                manifest,
                selected,
                train_candles=window_policy.train_candles,
                validation_candles=window_policy.validation_candles,
                test_candles=window_policy.test_candles,
                warmup_candles=window_policy.warmup_candles,
            )
            experiment_plan = self._experiments.create(
                snapshot,
                manifest,
                temporal_plan,
                search_space,
                plugin_name=plugin_name,
                plugin_version=plugin_version,
                backtest_configuration=backtest_configuration,
                max_run_specs=specs_per_fold,
            )
            payload = fold_plan_values_payload(
                fold_index,
                temporal_plan.selected_coverage,
                temporal_plan,
                experiment_plan,
            )
            folds.append(
                WalkForwardFoldPlan(
                    fold_index=fold_index,
                    selected_coverage=temporal_plan.selected_coverage,
                    temporal_plan=temporal_plan,
                    experiment_plan=experiment_plan,
                    checksum=document_checksum(payload),
                    fold_id=deterministic_id("adt-walk-forward-fold-v1", payload),
                )
            )

        first_experiment = folds[0].experiment_plan
        typed_folds = tuple(folds)
        ordering = WalkForwardOrderingPolicy.CHRONOLOGICAL_FOLDS
        payload = walk_forward_plan_values_payload(
            snapshot=first_experiment.snapshot,
            window_policy=window_policy,
            selection_policy=selection_policy,
            search_space=search_space,
            plugin=first_experiment.plugin,
            backtest_configuration=backtest_configuration,
            folds=typed_folds,
            fold_count=fold_count,
            combination_count=combination_count,
            specs_per_fold=specs_per_fold,
            total_specs=total_specs,
            trailing_candles=trailing_candles,
            max_total_specs=max_total_specs,
            ordering_policy=ordering,
            schema_version=1,
        )
        if len(canonical_json_bytes(payload)) > MAX_WALK_FORWARD_MANIFEST_BYTES:
            raise WalkForwardLimitExceededError("walk-forward plan exceeds its document byte limit")
        return WalkForwardPlan(
            snapshot=first_experiment.snapshot,
            window_policy=window_policy,
            selection_policy=selection_policy,
            search_space=search_space,
            plugin=first_experiment.plugin,
            backtest_configuration=backtest_configuration,
            folds=typed_folds,
            fold_count=fold_count,
            combination_count=combination_count,
            specs_per_fold=specs_per_fold,
            total_specs=total_specs,
            trailing_candles=trailing_candles,
            max_total_specs=max_total_specs,
            ordering_policy=ordering,
            schema_version=1,
            checksum=document_checksum(payload),
            walk_forward_plan_id=deterministic_id("adt-walk-forward-plan-v1", payload),
        )

    def validate(
        self,
        plan: WalkForwardPlan,
        snapshot: DatasetSnapshot,
        manifest: DatasetManifest,
    ) -> WalkForwardPlan:
        """Revalidate the full plan and every nested 4-02/4-03 contract."""

        try:
            validate_walk_forward_plan(plan)
            validate_snapshot_contract(snapshot, manifest)
            for fold in plan.folds:
                self._temporal.validate_for_snapshot(fold.temporal_plan, snapshot, manifest)
                self._experiments.validate(fold.experiment_plan, snapshot, manifest)
        except IncompatibleWalkForwardPlanError:
            raise
        except Exception as error:
            message = getattr(error, "message", "walk-forward plan validation failed")
            raise IncompatibleWalkForwardPlanError(message) from error
        return plan


def _slot_count(data_range: DataRange, duration: timedelta) -> int:
    quotient, remainder = divmod(data_range.end - data_range.start, duration)
    if remainder != timedelta(0):
        raise IncompatibleWalkForwardPlanError("snapshot coverage is not timeframe aligned")
    return quotient


def _validate_total_limit(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= ABSOLUTE_MAX_TOTAL_SPECS
    ):
        raise WalkForwardLimitExceededError("total spec limit is invalid")


# Fixed envelopes are derived from the bounded canonical contracts: identifiers/checksums,
# temporal/configuration objects, 128 holdout metrics and 128 compact rejection reasons.
# The complete canonical search-space document is additionally charged once per run spec
# (and twice per candidate in the final evidence), although one combination can contain
# only one value from each search dimension and is therefore strictly smaller.
_DOCUMENT_ENVELOPE_BYTES = 65_536
_PLAN_FOLD_FIXED_BYTES = 65_536
_RUN_SPEC_FIXED_BYTES = 16_384
_EXECUTION_FOLD_FIXED_BYTES = 131_072
_SELECTION_CANDIDATE_FIXED_BYTES = 4_096


def maximum_walk_forward_plan_bytes(
    search_space: ParameterSearchSpace,
    fold_count: int,
    total_specs: int,
) -> int:
    """Return a conservative upper bound before expansion or factory calls."""

    validate_search_space_structure(search_space)
    _preflight_count(fold_count, "fold count")
    _preflight_count(total_specs, "total spec count")
    search_bytes = len(canonical_json_bytes(search_space_to_document(search_space)))
    return (
        _DOCUMENT_ENVELOPE_BYTES
        + search_bytes
        + fold_count * (_PLAN_FOLD_FIXED_BYTES + search_bytes)
        + total_specs * (_RUN_SPEC_FIXED_BYTES + search_bytes)
    )


def maximum_walk_forward_execution_bytes(
    search_space: ParameterSearchSpace,
    fold_count: int,
    combination_count: int,
) -> int:
    """Bound the final manifest including every candidate and maximum holdout data."""

    validate_search_space_structure(search_space)
    _preflight_count(fold_count, "fold count")
    _preflight_count(combination_count, "combination count")
    search_bytes = len(canonical_json_bytes(search_space_to_document(search_space)))
    candidates = fold_count * combination_count
    return (
        _DOCUMENT_ENVELOPE_BYTES
        + fold_count * _EXECUTION_FOLD_FIXED_BYTES
        + candidates * (_SELECTION_CANDIDATE_FIXED_BYTES + 2 * search_bytes)
    )


def _preflight_count(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WalkForwardLimitExceededError(f"{label} is invalid for byte preflight")
