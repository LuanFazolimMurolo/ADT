"""Immutable contracts for reproducible Phase 4 experiment planning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.backtesting.domain import (
    SUPPORTED_BACKTEST_SCHEMA_VERSIONS,
    BacktestConfig,
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    IntrabarPolicy,
    RiskLimits,
    SlippageKind,
    SlippageModel,
    StrategyDescriptor,
)
from app.market_data.domain import DataRange
from app.optimization.canonical import decimal_text, deterministic_id, document_checksum
from app.optimization.domain import (
    ABSOLUTE_MAX_COMBINATIONS,
    DEFAULT_MAX_COMBINATIONS,
    ParameterCombination,
    ParameterSearchSpace,
    SearchScalar,
    decode_parameter_document_scalars,
    validate_parameter_combination_structure,
    validate_search_space_structure,
)
from app.optimization.domain import (
    validate_parameter_combination as validate_search_combination,
)
from app.optimization.errors import (
    DuplicatePlannedRunSpecError,
    ExperimentChecksumError,
    ExperimentHoldoutPolicyError,
    ExperimentIdentifierError,
    ExperimentRunIndexError,
    ExperimentRunOrderError,
    IncompatibleExperimentDocumentError,
    IncompatibleExperimentPluginError,
    IncompatibleExperimentSearchSpaceError,
    IncompatibleExperimentSnapshotError,
    IncompatibleExperimentTemporalPlanError,
    IncompatibleSearchSpaceDocumentError,
    InvalidExperimentBacktestConfigurationError,
    InvalidExperimentCardinalityError,
    InvalidExperimentRunPurposeError,
    InvalidRunSpecLimitError,
    ParameterSearchError,
    PlannedRunSpecChecksumError,
    PlannedRunSpecIdentifierError,
    RunSpecLimitExceededError,
    UnsupportedExperimentSchemaError,
)
from app.optimization.temporal_domain import (
    CANONICAL_TEMPORAL_ROLES,
    TemporalSegment,
    TemporalSegmentationPlan,
    TemporalSegmentRole,
    TemporalSnapshotReference,
    temporal_snapshot_payload,
    validate_temporal_segment,
    validate_temporal_segmentation_plan,
    validate_temporal_snapshot_reference,
)
from app.strategies.definitions import StrategyParameterDocument

EXPERIMENT_SCHEMA_VERSION = 1
SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS = frozenset({EXPERIMENT_SCHEMA_VERSION})
SEGMENTS_PER_COMBINATION = len(CANONICAL_TEMPORAL_ROLES)
DEFAULT_MAX_RUN_SPECS = DEFAULT_MAX_COMBINATIONS * SEGMENTS_PER_COMBINATION
ABSOLUTE_MAX_RUN_SPECS = 30_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExperimentRunPurpose(StrEnum):
    """Semantic use of one planned temporal run."""

    TRAINING = "TRAINING"
    MODEL_SELECTION = "MODEL_SELECTION"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"


class ExperimentHoldoutPolicy(StrEnum):
    """Versioned rule protecting TEST from future model selection."""

    TEST_IS_FINAL_HOLDOUT = "TEST_IS_FINAL_HOLDOUT"


class ExperimentOrderingPolicy(StrEnum):
    """Canonical materialization order for planned runs."""

    COMBINATION_THEN_SEGMENT = "COMBINATION_THEN_SEGMENT"


PURPOSE_BY_TEMPORAL_ROLE = {
    TemporalSegmentRole.TRAIN: ExperimentRunPurpose.TRAINING,
    TemporalSegmentRole.VALIDATION: ExperimentRunPurpose.MODEL_SELECTION,
    TemporalSegmentRole.TEST: ExperimentRunPurpose.FINAL_HOLDOUT,
}


@dataclass(frozen=True, slots=True)
class ExperimentPluginReference:
    """Registered plugin identity bound into the experiment manifest."""

    name: str
    version: str
    schema_version: int
    lifecycle_version: int

    def __post_init__(self) -> None:
        validate_experiment_plugin_reference(self)


@dataclass(frozen=True, slots=True)
class ExperimentBacktestConfiguration:
    """Canonical experiment-wide projection of per-run ``BacktestConfig`` fields."""

    initial_capital: Decimal
    execution: ExecutionAssumptions
    constraints: InstrumentConstraints
    risk_limits: RiskLimits
    history_window: int
    max_candles: int
    max_orders: int
    max_events: int
    engine_version: str
    schema_version: int

    def __post_init__(self) -> None:
        validate_experiment_backtest_configuration(self)

    def for_segment(
        self,
        *,
        snapshot_id: str,
        strategy: StrategyDescriptor,
        segment: TemporalSegment,
    ) -> BacktestConfig:
        """Create the existing Phase 3A config for a future context-aware run."""

        validate_experiment_backtest_configuration(self)
        validate_temporal_segment(segment)
        context_candles = segment.candle_count + segment.warmup_candles
        if context_candles > self.max_candles:
            raise InvalidExperimentBacktestConfigurationError(
                "segment context exceeds the configured backtest candle limit"
            )
        if segment.warmup_candles > self.history_window:
            raise InvalidExperimentBacktestConfigurationError(
                "history window is smaller than the required temporal warmup"
            )
        try:
            return BacktestConfig(
                snapshot_id=snapshot_id,
                data_range=segment.context_range,
                strategy=strategy,
                initial_capital=self.initial_capital,
                execution=self.execution,
                constraints=self.constraints,
                risk_limits=self.risk_limits,
                history_window=self.history_window,
                max_candles=self.max_candles,
                max_orders=self.max_orders,
                max_events=self.max_events,
                engine_version=self.engine_version,
                schema_version=self.schema_version,
            )
        except (TypeError, ValueError) as error:
            raise InvalidExperimentBacktestConfigurationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class PlannedRunSpec:
    """One immutable future-run specification, never a completed backtest result."""

    experiment_id: str
    global_index: int
    combination: ParameterCombination
    segment: TemporalSegment
    snapshot: TemporalSnapshotReference
    plugin: ExperimentPluginReference
    backtest_config: BacktestConfig
    purpose: ExperimentRunPurpose
    eligible_for_model_selection: bool
    checksum: str
    run_spec_id: str
    schema_version: int = EXPERIMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_planned_run_spec(self)

    @property
    def context_range(self) -> DataRange:
        return self.segment.context_range

    @property
    def evaluation_range(self) -> DataRange:
        return self.segment.evaluation.data_range

    @property
    def warmup_candles(self) -> int:
        return self.segment.warmup_candles


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    """Complete canonical manifest joining Phases 2C, 4-01, 4-02 and 3A."""

    snapshot: TemporalSnapshotReference
    temporal_plan: TemporalSegmentationPlan
    search_space: ParameterSearchSpace
    combinations: tuple[ParameterCombination, ...]
    plugin: ExperimentPluginReference
    backtest_configuration: ExperimentBacktestConfiguration
    run_specs: tuple[PlannedRunSpec, ...]
    cardinality: int
    max_run_specs: int
    checksum: str
    experiment_id: str
    holdout_policy: ExperimentHoldoutPolicy = ExperimentHoldoutPolicy.TEST_IS_FINAL_HOLDOUT
    ordering_policy: ExperimentOrderingPolicy = ExperimentOrderingPolicy.COMBINATION_THEN_SEGMENT
    schema_version: int = EXPERIMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_experiment_plan(self)


def validate_experiment_plugin_reference(reference: ExperimentPluginReference) -> None:
    if not isinstance(reference, ExperimentPluginReference):
        raise IncompatibleExperimentPluginError("plugin reference contract is invalid")
    if (
        not isinstance(reference.name, str)
        or reference.name != reference.name.strip()
        or not isinstance(reference.version, str)
        or reference.version != reference.version.strip()
    ):
        raise IncompatibleExperimentPluginError("plugin identity is not canonical")
    try:
        descriptor = StrategyDescriptor(reference.name, reference.version)
    except (AttributeError, TypeError, ValueError) as error:
        raise IncompatibleExperimentPluginError("plugin identity is invalid") from error
    if descriptor.name != reference.name or descriptor.version != reference.version:
        raise IncompatibleExperimentPluginError("plugin identity is not canonical")
    for label, value in (
        ("plugin schema version", reference.schema_version),
        ("plugin lifecycle version", reference.lifecycle_version),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise IncompatibleExperimentPluginError(f"{label} must be a positive integer")


def validate_experiment_backtest_configuration(
    configuration: ExperimentBacktestConfiguration,
) -> None:
    """Revalidate the small projection and every reused Phase 3A contract."""

    if not isinstance(configuration, ExperimentBacktestConfiguration):
        raise InvalidExperimentBacktestConfigurationError(
            "experiment backtest configuration contract is invalid"
        )
    if not isinstance(configuration.initial_capital, Decimal):
        raise InvalidExperimentBacktestConfigurationError("initial capital must be Decimal")
    if (
        not isinstance(configuration.engine_version, str)
        or configuration.engine_version != configuration.engine_version.strip()
    ):
        raise InvalidExperimentBacktestConfigurationError(
            "engine version must be a canonical safe token"
        )
    if (
        isinstance(configuration.schema_version, bool)
        or not isinstance(configuration.schema_version, int)
        or configuration.schema_version not in SUPPORTED_BACKTEST_SCHEMA_VERSIONS
    ):
        raise InvalidExperimentBacktestConfigurationError("backtest schema version is unsupported")
    _canonical_decimal(configuration.initial_capital, "initial capital", positive=True)
    if not isinstance(configuration.execution, ExecutionAssumptions):
        raise InvalidExperimentBacktestConfigurationError("execution assumptions are invalid")
    execution = configuration.execution
    if not isinstance(execution.fees, FeeModel):
        raise InvalidExperimentBacktestConfigurationError("fee model is invalid")
    if not isinstance(execution.slippage, SlippageModel):
        raise InvalidExperimentBacktestConfigurationError("slippage model is invalid")
    if execution.slippage.kind is not SlippageKind.FIXED_BPS:
        raise InvalidExperimentBacktestConfigurationError("slippage model is unsupported")
    if execution.intrabar_policy is not IntrabarPolicy.CONSERVATIVE:
        raise InvalidExperimentBacktestConfigurationError("intrabar policy is unsupported")
    if not isinstance(execution.force_close_at_end, bool):
        raise InvalidExperimentBacktestConfigurationError("force-close policy must be boolean")
    for label, value in (
        ("maker fee", execution.fees.maker_fee_bps),
        ("taker fee", execution.fees.taker_fee_bps),
        ("fixed slippage", execution.slippage.fixed_bps),
    ):
        _canonical_decimal(value, label, nonnegative=True, maximum=Decimal("1000"))

    constraints = configuration.constraints
    if not isinstance(constraints, InstrumentConstraints):
        raise InvalidExperimentBacktestConfigurationError("instrument constraints are invalid")
    for label, value in (
        ("minimum quantity", constraints.minimum_quantity),
        ("quantity step", constraints.quantity_step),
        ("price tick", constraints.price_tick),
    ):
        _canonical_decimal(value, label, positive=True)
    _canonical_decimal(constraints.minimum_notional, "minimum notional", nonnegative=True)
    if constraints.maximum_notional is not None:
        _canonical_decimal(constraints.maximum_notional, "maximum notional", positive=True)
        if constraints.maximum_notional < constraints.minimum_notional:
            raise InvalidExperimentBacktestConfigurationError(
                "maximum notional is below minimum notional"
            )

    risk = configuration.risk_limits
    if not isinstance(risk, RiskLimits):
        raise InvalidExperimentBacktestConfigurationError("risk limits are invalid")
    for optional_label, optional_value in (
        ("maximum order notional", risk.max_order_notional),
        ("maximum position notional", risk.max_position_notional),
    ):
        if optional_value is not None:
            _canonical_decimal(optional_value, optional_label, positive=True)
    if risk.max_drawdown_pct is not None:
        _canonical_decimal(
            risk.max_drawdown_pct,
            "maximum drawdown percentage",
            nonnegative=True,
            maximum=Decimal("100"),
        )
    _canonical_decimal(risk.minimum_quote_reserve, "minimum quote reserve", nonnegative=True)
    for integer_label, integer_value in (
        ("maximum open orders", risk.max_open_orders),
        ("maximum total orders", risk.max_total_orders),
    ):
        _positive_integer(integer_value, integer_label)
    if risk.max_open_orders > risk.max_total_orders:
        raise InvalidExperimentBacktestConfigurationError(
            "maximum open orders exceeds maximum total orders"
        )
    for boolean_label, boolean_value in (
        ("stop-on-drawdown policy", risk.stop_on_max_drawdown),
        ("all-in policy", risk.allow_all_in),
    ):
        if not isinstance(boolean_value, bool):
            raise InvalidExperimentBacktestConfigurationError(f"{boolean_label} must be boolean")

    for limit_label, limit_value in (
        ("history window", configuration.history_window),
        ("maximum candles", configuration.max_candles),
        ("maximum orders", configuration.max_orders),
        ("maximum events", configuration.max_events),
    ):
        _positive_integer(limit_value, limit_label)
    if configuration.history_window > configuration.max_candles:
        raise InvalidExperimentBacktestConfigurationError("history window exceeds maximum candles")
    if configuration.max_orders > risk.max_total_orders:
        raise InvalidExperimentBacktestConfigurationError(
            "backtest order limit exceeds the risk order limit"
        )
    try:
        BacktestConfig(
            snapshot_id="0" * 64,
            data_range=_validation_range(),
            strategy=StrategyDescriptor("experiment-validation", "1"),
            initial_capital=configuration.initial_capital,
            execution=configuration.execution,
            constraints=configuration.constraints,
            risk_limits=configuration.risk_limits,
            history_window=configuration.history_window,
            max_candles=configuration.max_candles,
            max_orders=configuration.max_orders,
            max_events=configuration.max_events,
            engine_version=configuration.engine_version,
            schema_version=configuration.schema_version,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise InvalidExperimentBacktestConfigurationError(str(error)) from error


def _validate_parameter_combination(
    combination: ParameterCombination,
    space: ParameterSearchSpace,
) -> None:
    """Map the reusable Phase 4-01 validator onto the 4-03 error taxonomy."""

    try:
        validate_search_combination(combination, space)
    except ParameterSearchError as error:
        raise IncompatibleExperimentSearchSpaceError(error.message) from error


def validate_planned_run_spec(spec: PlannedRunSpec) -> None:
    """Revalidate one planned spec, including holdout semantics and hashes."""

    if not isinstance(spec, PlannedRunSpec):
        raise IncompatibleExperimentDocumentError("planned run contract is invalid")
    _sha256(spec.experiment_id, "experiment id", identifier=True)
    if (
        isinstance(spec.schema_version, bool)
        or not isinstance(spec.schema_version, int)
        or spec.schema_version not in SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS
    ):
        raise UnsupportedExperimentSchemaError(
            f"unsupported planned-run schema version: {spec.schema_version}"
        )
    if isinstance(spec.global_index, bool) or not isinstance(spec.global_index, int):
        raise ExperimentRunIndexError("global run index must be an integer")
    if spec.global_index < 0:
        raise ExperimentRunIndexError("global run index must be non-negative")
    if not isinstance(spec.combination, ParameterCombination):
        raise IncompatibleExperimentSearchSpaceError("planned combination is invalid")
    try:
        validate_parameter_combination_structure(spec.combination)
    except ParameterSearchError as error:
        raise IncompatibleExperimentSearchSpaceError(error.message) from error
    if not isinstance(spec.segment, TemporalSegment):
        raise IncompatibleExperimentTemporalPlanError("planned segment is invalid")
    validate_temporal_segment(spec.segment)
    if not isinstance(spec.snapshot, TemporalSnapshotReference):
        raise IncompatibleExperimentSnapshotError("planned snapshot reference is invalid")
    validate_temporal_snapshot_reference(spec.snapshot)
    validate_experiment_plugin_reference(spec.plugin)
    if not isinstance(spec.backtest_config, BacktestConfig):
        raise InvalidExperimentBacktestConfigurationError("planned backtest config is invalid")
    if not isinstance(spec.purpose, ExperimentRunPurpose):
        raise InvalidExperimentRunPurposeError("planned run purpose is invalid")
    expected_purpose = PURPOSE_BY_TEMPORAL_ROLE[spec.segment.role]
    if spec.purpose is not expected_purpose:
        raise InvalidExperimentRunPurposeError(
            "run purpose diverges from its temporal segment role"
        )
    expected_eligibility = spec.segment.role is TemporalSegmentRole.VALIDATION
    if not isinstance(spec.eligible_for_model_selection, bool):
        raise ExperimentHoldoutPolicyError("selection eligibility must be boolean")
    if spec.eligible_for_model_selection is not expected_eligibility:
        raise ExperimentHoldoutPolicyError(
            "only VALIDATION may be eligible for future model selection"
        )
    if spec.segment.role is TemporalSegmentRole.TEST and spec.eligible_for_model_selection:
        raise ExperimentHoldoutPolicyError("TEST must remain the final holdout")
    expected_strategy = StrategyDescriptor(
        spec.plugin.name,
        spec.plugin.version,
        spec.combination.parameters,
    )
    if spec.backtest_config.strategy != expected_strategy:
        raise IncompatibleExperimentPluginError(
            "backtest strategy diverges from the planned combination"
        )
    if spec.backtest_config.snapshot_id != spec.snapshot.snapshot_id:
        raise IncompatibleExperimentSnapshotError("backtest config is bound to another snapshot")
    if spec.backtest_config.data_range != spec.segment.context_range:
        raise InvalidExperimentBacktestConfigurationError(
            "backtest config range must equal the segment context range"
        )
    expected_checksum = document_checksum(planned_run_spec_payload(spec))
    if spec.checksum != expected_checksum:
        raise PlannedRunSpecChecksumError()
    expected_id = planned_run_spec_id(spec.experiment_id, spec.checksum, spec)
    if spec.run_spec_id != expected_id:
        raise PlannedRunSpecIdentifierError()


def validate_experiment_plan(plan: ExperimentPlan) -> None:
    """Revalidate the complete in-memory manifest without performing I/O."""

    if not isinstance(plan, ExperimentPlan):
        raise IncompatibleExperimentDocumentError("experiment plan contract is invalid")
    if (
        isinstance(plan.schema_version, bool)
        or not isinstance(plan.schema_version, int)
        or plan.schema_version not in SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS
    ):
        raise UnsupportedExperimentSchemaError(
            f"unsupported experiment schema version: {plan.schema_version}"
        )
    if plan.holdout_policy is not ExperimentHoldoutPolicy.TEST_IS_FINAL_HOLDOUT:
        raise ExperimentHoldoutPolicyError("experiment holdout policy is unsupported")
    if plan.ordering_policy is not ExperimentOrderingPolicy.COMBINATION_THEN_SEGMENT:
        raise ExperimentRunOrderError("experiment ordering policy is unsupported")
    if not isinstance(plan.temporal_plan, TemporalSegmentationPlan):
        raise IncompatibleExperimentTemporalPlanError("temporal plan contract is invalid")
    validate_temporal_segmentation_plan(plan.temporal_plan)
    if not isinstance(plan.search_space, ParameterSearchSpace):
        raise IncompatibleExperimentSearchSpaceError("parameter search space is invalid")
    try:
        validate_search_space_structure(plan.search_space)
    except ParameterSearchError as error:
        raise IncompatibleExperimentSearchSpaceError(error.message) from error
    if not isinstance(plan.snapshot, TemporalSnapshotReference):
        raise IncompatibleExperimentSnapshotError("snapshot reference contract is invalid")
    validate_temporal_snapshot_reference(plan.snapshot)
    if plan.snapshot != plan.temporal_plan.snapshot:
        raise IncompatibleExperimentSnapshotError("experiment snapshot diverges from temporal plan")
    validate_experiment_plugin_reference(plan.plugin)
    if (
        plan.plugin.name != plan.search_space.plugin_name
        or plan.plugin.version != plan.search_space.plugin_version
        or plan.plugin.schema_version != plan.search_space.plugin_schema_version
        or plan.plugin.lifecycle_version != plan.search_space.plugin_lifecycle_version
    ):
        raise IncompatibleExperimentPluginError(
            "experiment plugin diverges from parameter search space"
        )
    validate_experiment_backtest_configuration(plan.backtest_configuration)
    validate_run_spec_limit(plan.max_run_specs)
    expected_cardinality = calculate_run_spec_cardinality(
        plan.search_space.cardinality, plan.max_run_specs
    )
    if plan.cardinality != expected_cardinality:
        raise InvalidExperimentCardinalityError(
            "experiment cardinality diverges from combinations times three"
        )
    if not isinstance(plan.combinations, tuple):
        raise IncompatibleExperimentSearchSpaceError("combinations must be a tuple")
    if len(plan.combinations) != plan.search_space.cardinality:
        raise IncompatibleExperimentSearchSpaceError(
            "combination count diverges from search cardinality"
        )
    for index, combination in enumerate(plan.combinations):
        _validate_parameter_combination(combination, plan.search_space)
        if combination.index != index:
            raise ExperimentRunOrderError("combination indexes are not contiguous")
    if not isinstance(plan.run_specs, tuple):
        raise ExperimentRunOrderError("planned runs must be a tuple")
    if len(plan.run_specs) != plan.cardinality:
        raise InvalidExperimentCardinalityError(
            "planned run count diverges from experiment cardinality"
        )
    run_ids: set[str] = set()
    pairs: set[tuple[int, int]] = set()
    for global_index, spec in enumerate(plan.run_specs):
        if not isinstance(spec, PlannedRunSpec):
            raise ExperimentRunOrderError("planned run contract is invalid")
        if spec.global_index != global_index:
            raise ExperimentRunIndexError("global run indexes are not contiguous")
        validate_planned_run_spec(spec)
        if spec.experiment_id != plan.experiment_id:
            raise ExperimentIdentifierError("planned run belongs to another experiment")
        combination_index, segment_index = divmod(global_index, SEGMENTS_PER_COMBINATION)
        if spec.combination != plan.combinations[combination_index]:
            raise ExperimentRunOrderError("planned combination order is invalid")
        if spec.segment != plan.temporal_plan.segments[segment_index]:
            raise ExperimentRunOrderError("planned segment order is invalid")
        if spec.snapshot != plan.snapshot or spec.plugin != plan.plugin:
            raise IncompatibleExperimentDocumentError(
                "planned run references diverge from the experiment"
            )
        expected_config = plan.backtest_configuration.for_segment(
            snapshot_id=plan.snapshot.snapshot_id,
            strategy=StrategyDescriptor(
                plan.plugin.name,
                plan.plugin.version,
                spec.combination.parameters,
            ),
            segment=spec.segment,
        )
        if spec.backtest_config != expected_config:
            raise InvalidExperimentBacktestConfigurationError(
                "planned run config diverges from experiment configuration"
            )
        pair = (spec.combination.index, spec.segment.index)
        if pair in pairs or spec.run_spec_id in run_ids:
            raise DuplicatePlannedRunSpecError()
        pairs.add(pair)
        run_ids.add(spec.run_spec_id)
    if len(pairs) != plan.cardinality:
        raise DuplicatePlannedRunSpecError()

    _sha256(plan.experiment_id, "experiment id", identifier=True)
    if plan.experiment_id != experiment_plan_id(plan):
        raise ExperimentIdentifierError()
    if plan.checksum != document_checksum(experiment_plan_payload(plan)):
        raise ExperimentChecksumError()


def validate_run_spec_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidRunSpecLimitError("maximum planned runs must be a positive integer")
    if value > ABSOLUTE_MAX_RUN_SPECS:
        raise InvalidRunSpecLimitError(
            f"maximum planned runs exceeds absolute limit {ABSOLUTE_MAX_RUN_SPECS}"
        )
    return value


def calculate_run_spec_cardinality(combinations: object, maximum: object) -> int:
    """Validate the exact three-way product before any run-spec materialization."""

    limit = validate_run_spec_limit(maximum)
    if isinstance(combinations, bool) or not isinstance(combinations, int) or combinations < 1:
        raise InvalidExperimentCardinalityError(
            "parameter combination count must be a positive integer"
        )
    if combinations > ABSOLUTE_MAX_COMBINATIONS:
        raise InvalidExperimentCardinalityError(
            "parameter combination count exceeds the Phase 4-01 absolute limit"
        )
    if combinations > limit // SEGMENTS_PER_COMBINATION:
        raise RunSpecLimitExceededError(f"planned run cardinality exceeds requested limit {limit}")
    return combinations * SEGMENTS_PER_COMBINATION


def plugin_reference_payload(reference: ExperimentPluginReference) -> dict[str, object]:
    return {
        "name": reference.name,
        "version": reference.version,
        "schema_version": reference.schema_version,
        "lifecycle_version": reference.lifecycle_version,
    }


def backtest_configuration_payload(
    configuration: ExperimentBacktestConfiguration,
) -> dict[str, object]:
    validate_experiment_backtest_configuration(configuration)
    return {
        "initial_capital": decimal_text(configuration.initial_capital),
        "execution": {
            "fees": {
                "maker_fee_bps": decimal_text(configuration.execution.fees.maker_fee_bps),
                "taker_fee_bps": decimal_text(configuration.execution.fees.taker_fee_bps),
            },
            "slippage": {
                "kind": configuration.execution.slippage.kind.value,
                "fixed_bps": decimal_text(configuration.execution.slippage.fixed_bps),
            },
            "intrabar_policy": configuration.execution.intrabar_policy.value,
            "force_close_at_end": configuration.execution.force_close_at_end,
        },
        "constraints": {
            "minimum_quantity": decimal_text(configuration.constraints.minimum_quantity),
            "quantity_step": decimal_text(configuration.constraints.quantity_step),
            "price_tick": decimal_text(configuration.constraints.price_tick),
            "minimum_notional": decimal_text(configuration.constraints.minimum_notional),
            "maximum_notional": _optional_decimal(configuration.constraints.maximum_notional),
        },
        "risk_limits": {
            "max_order_notional": _optional_decimal(configuration.risk_limits.max_order_notional),
            "max_position_notional": _optional_decimal(
                configuration.risk_limits.max_position_notional
            ),
            "max_open_orders": configuration.risk_limits.max_open_orders,
            "max_total_orders": configuration.risk_limits.max_total_orders,
            "max_drawdown_pct": _optional_decimal(configuration.risk_limits.max_drawdown_pct),
            "stop_on_max_drawdown": configuration.risk_limits.stop_on_max_drawdown,
            "allow_all_in": configuration.risk_limits.allow_all_in,
            "minimum_quote_reserve": decimal_text(configuration.risk_limits.minimum_quote_reserve),
        },
        "limits": {
            "history_window": configuration.history_window,
            "max_candles": configuration.max_candles,
            "max_orders": configuration.max_orders,
            "max_events": configuration.max_events,
        },
        "engine_version": configuration.engine_version,
        "backtest_schema_version": configuration.schema_version,
    }


def parameter_document_payload(document: StrategyParameterDocument) -> list[dict[str, object]]:
    try:
        decode_parameter_document_scalars(document)
    except ParameterSearchError as error:
        raise IncompatibleExperimentSearchSpaceError(error.message) from error
    return [{"name": item.name, "kind": item.kind.value, "value": item.value} for item in document]


def normalized_parameters_from_document(
    document: StrategyParameterDocument,
) -> tuple[tuple[str, SearchScalar], ...]:
    """Decode the existing typed strategy document without plugin coercion."""

    try:
        decoded = decode_parameter_document_scalars(document)
    except ParameterSearchError as error:
        raise IncompatibleExperimentSearchSpaceError(error.message) from error
    return tuple((name, value) for name, _kind, value in decoded)


def combination_payload(combination: ParameterCombination) -> dict[str, object]:
    parameters = parameter_document_payload(combination.parameter_document)
    return {
        "index": combination.index,
        "combination_id": combination.combination_id,
        "parameters_checksum": combination.parameters_checksum,
        "parameters": [dict(item) for item in parameters],
        "parameter_document": [dict(item) for item in parameters],
    }


def combination_reference_payload(combination: ParameterCombination) -> dict[str, object]:
    try:
        validate_parameter_combination_structure(combination)
    except ParameterSearchError as error:
        raise IncompatibleExperimentSearchSpaceError(error.message) from error
    return {
        "index": combination.index,
        "combination_id": combination.combination_id,
        "parameters_checksum": combination.parameters_checksum,
    }


def segment_reference_payload(segment: TemporalSegment) -> dict[str, object]:
    if not isinstance(segment, TemporalSegment):
        raise IncompatibleExperimentTemporalPlanError("planned segment is invalid")
    validate_temporal_segment(segment)
    return {
        "index": segment.index,
        "segment_id": segment.segment_id,
        "checksum": segment.checksum,
    }


def planned_run_spec_values_payload(
    *,
    global_index: int,
    combination: ParameterCombination,
    segment: TemporalSegment,
    purpose: ExperimentRunPurpose,
    eligible_for_model_selection: bool,
    schema_version: int = EXPERIMENT_SCHEMA_VERSION,
) -> dict[str, object]:
    if isinstance(global_index, bool) or not isinstance(global_index, int) or global_index < 0:
        raise ExperimentRunIndexError("global run index must be a non-negative integer")
    if not isinstance(purpose, ExperimentRunPurpose):
        raise InvalidExperimentRunPurposeError("planned run purpose is invalid")
    if not isinstance(eligible_for_model_selection, bool):
        raise ExperimentHoldoutPolicyError("selection eligibility must be boolean")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS
    ):
        raise UnsupportedExperimentSchemaError(
            f"unsupported planned-run schema version: {schema_version}"
        )
    return {
        "schema_version": schema_version,
        "global_index": global_index,
        "combination_reference": combination_reference_payload(combination),
        "segment_reference": segment_reference_payload(segment),
        "purpose": purpose.value,
        "eligible_for_model_selection": eligible_for_model_selection,
    }


def planned_run_spec_payload(spec: PlannedRunSpec) -> dict[str, object]:
    return {
        "schema_version": spec.schema_version,
        "global_index": spec.global_index,
        "combination_reference": combination_reference_payload(spec.combination),
        "segment_reference": segment_reference_payload(spec.segment),
        "purpose": spec.purpose.value,
        "eligible_for_model_selection": spec.eligible_for_model_selection,
    }


def planned_run_spec_envelope_payload(spec: PlannedRunSpec) -> dict[str, object]:
    return {
        "run_spec": planned_run_spec_payload(spec),
        "checksum": spec.checksum,
        "run_spec_id": spec.run_spec_id,
    }


def planned_run_spec_id(experiment_id: str, checksum: str, spec: PlannedRunSpec) -> str:
    return planned_run_spec_id_from_payload(experiment_id, checksum, planned_run_spec_payload(spec))


def planned_run_spec_id_from_payload(
    experiment_id: str,
    checksum: str,
    payload: dict[str, object],
) -> str:
    return deterministic_id(
        "adt-planned-experiment-run-spec-v1",
        {
            "experiment_id": experiment_id,
            "run_spec_checksum": checksum,
            "run_spec": payload,
        },
    )


def experiment_plan_identity_payload(plan: ExperimentPlan) -> dict[str, object]:
    return experiment_plan_values_payload(
        snapshot=plan.snapshot,
        temporal_plan=plan.temporal_plan,
        search_space=plan.search_space,
        combinations=plan.combinations,
        plugin=plan.plugin,
        backtest_configuration=plan.backtest_configuration,
        cardinality=plan.cardinality,
        max_run_specs=plan.max_run_specs,
        holdout_policy=plan.holdout_policy,
        ordering_policy=plan.ordering_policy,
        schema_version=plan.schema_version,
        specs=[
            {"run_spec": planned_run_spec_payload(spec), "checksum": spec.checksum}
            for spec in plan.run_specs
        ],
    )


def experiment_plan_payload(plan: ExperimentPlan) -> dict[str, object]:
    return experiment_plan_values_payload(
        snapshot=plan.snapshot,
        temporal_plan=plan.temporal_plan,
        search_space=plan.search_space,
        combinations=plan.combinations,
        plugin=plan.plugin,
        backtest_configuration=plan.backtest_configuration,
        cardinality=plan.cardinality,
        max_run_specs=plan.max_run_specs,
        holdout_policy=plan.holdout_policy,
        ordering_policy=plan.ordering_policy,
        schema_version=plan.schema_version,
        specs=[planned_run_spec_envelope_payload(spec) for spec in plan.run_specs],
    )


def experiment_plan_values_payload(
    *,
    snapshot: TemporalSnapshotReference,
    temporal_plan: TemporalSegmentationPlan,
    search_space: ParameterSearchSpace,
    combinations: tuple[ParameterCombination, ...],
    plugin: ExperimentPluginReference,
    backtest_configuration: ExperimentBacktestConfiguration,
    cardinality: int,
    max_run_specs: int,
    holdout_policy: ExperimentHoldoutPolicy,
    ordering_policy: ExperimentOrderingPolicy,
    schema_version: int,
    specs: list[dict[str, object]],
) -> dict[str, object]:
    from app.optimization.documents import to_document
    from app.optimization.temporal_documents import temporal_to_document

    return {
        "schema_version": schema_version,
        "snapshot": temporal_snapshot_payload(snapshot),
        "temporal_plan": temporal_to_document(temporal_plan),
        "parameter_search_space": to_document(search_space),
        "combinations": [combination_payload(item) for item in combinations],
        "plugin": plugin_reference_payload(plugin),
        "backtest_configuration": backtest_configuration_payload(backtest_configuration),
        "holdout_policy": holdout_policy.value,
        "ordering_policy": ordering_policy.value,
        "max_run_specs": max_run_specs,
        "cardinality": cardinality,
        "run_specs": specs,
    }


def experiment_plan_id(plan: ExperimentPlan) -> str:
    return deterministic_id("adt-experiment-plan-v1", experiment_plan_identity_payload(plan))


def _canonical_decimal(
    value: object,
    label: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
    maximum: Decimal | None = None,
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidExperimentBacktestConfigurationError(f"{label} must be finite Decimal")
    try:
        decimal_text(value)
    except IncompatibleSearchSpaceDocumentError as error:
        raise InvalidExperimentBacktestConfigurationError(str(error)) from error
    if positive and value <= 0:
        raise InvalidExperimentBacktestConfigurationError(f"{label} must be positive")
    if nonnegative and value < 0:
        raise InvalidExperimentBacktestConfigurationError(f"{label} must be nonnegative")
    if maximum is not None and value > maximum:
        raise InvalidExperimentBacktestConfigurationError(f"{label} exceeds its maximum")


def _positive_integer(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidExperimentBacktestConfigurationError(f"{label} must be a positive integer")


def _sha256(value: object, label: str, *, identifier: bool = False) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        if identifier:
            raise ExperimentIdentifierError(f"{label} must be lowercase SHA-256")
        raise ExperimentChecksumError(f"{label} must be lowercase SHA-256")


def _optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else decimal_text(value)


def _validation_range() -> DataRange:
    from datetime import UTC, datetime, timedelta

    start = datetime(2000, 1, 1, tzinfo=UTC)
    return DataRange(start, start + timedelta(seconds=1))


__all__ = [
    "ABSOLUTE_MAX_RUN_SPECS",
    "DEFAULT_MAX_RUN_SPECS",
    "EXPERIMENT_SCHEMA_VERSION",
    "PURPOSE_BY_TEMPORAL_ROLE",
    "SEGMENTS_PER_COMBINATION",
    "SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS",
    "ExperimentBacktestConfiguration",
    "ExperimentHoldoutPolicy",
    "ExperimentOrderingPolicy",
    "ExperimentPlan",
    "ExperimentPluginReference",
    "ExperimentRunPurpose",
    "PlannedRunSpec",
    "calculate_run_spec_cardinality",
    "combination_reference_payload",
    "experiment_plan_id",
    "experiment_plan_payload",
    "experiment_plan_values_payload",
    "normalized_parameters_from_document",
    "planned_run_spec_id_from_payload",
    "planned_run_spec_values_payload",
    "segment_reference_payload",
    "validate_experiment_backtest_configuration",
    "validate_experiment_plan",
    "validate_experiment_plugin_reference",
    "validate_planned_run_spec",
    "validate_run_spec_limit",
]
