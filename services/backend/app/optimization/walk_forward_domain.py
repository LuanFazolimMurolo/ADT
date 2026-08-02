"""Immutable deterministic contracts for Phase 4-05 walk-forward evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.backtesting.reports import ComparisonMetric
from app.market_data.timeframes import get_timeframe
from app.optimization.artifact_paths import is_canonical_artifact_path
from app.optimization.canonical import (
    decimal_text,
    deterministic_id,
    document_checksum,
    integer_text,
)
from app.optimization.documents import to_document as search_space_to_document
from app.optimization.domain import ParameterSearchSpace, validate_search_space_structure
from app.optimization.errors import (
    IncompatibleWalkForwardExecutionError,
    IncompatibleWalkForwardFoldError,
    IncompatibleWalkForwardPlanError,
    IncompatibleWalkForwardSelectionError,
    InvalidWalkForwardCandidateError,
    InvalidWalkForwardHoldoutError,
    InvalidWalkForwardMetricError,
    InvalidWalkForwardSelectionPolicyError,
    InvalidWalkForwardWindowPolicyError,
    UnknownWalkForwardMetricError,
    WalkForwardChecksumError,
    WalkForwardIdentifierError,
    WalkForwardLimitExceededError,
    WalkForwardSelectionLeakageError,
)
from app.optimization.experiment_documents import experiment_to_document
from app.optimization.experiment_domain import (
    ExperimentBacktestConfiguration,
    ExperimentPlan,
    ExperimentPluginReference,
    backtest_configuration_payload,
    validate_experiment_backtest_configuration,
    validate_experiment_plan,
    validate_experiment_plugin_reference,
)
from app.optimization.temporal_documents import temporal_to_document
from app.optimization.temporal_domain import (
    TemporalCoverage,
    TemporalSegmentationPlan,
    TemporalSnapshotReference,
    temporal_coverage_payload,
    temporal_snapshot_payload,
    validate_temporal_coverage,
    validate_temporal_segmentation_plan,
    validate_temporal_snapshot_reference,
)

WALK_FORWARD_SCHEMA_VERSION = 1
WINDOW_POLICY_SCHEMA_VERSION = 1
SELECTION_POLICY_SCHEMA_VERSION = 1
DEFAULT_MAX_FOLDS = 50
ABSOLUTE_MAX_FOLDS = 1_000
DEFAULT_MAX_TOTAL_SPECS = 30_000
ABSOLUTE_MAX_TOTAL_SPECS = 300_000
MAX_WALK_FORWARD_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_WALK_FORWARD_ERROR_MESSAGE = 500
MAX_HOLDOUT_METRICS = 128
MAX_HOLDOUT_METRIC_NAME_CHARACTERS = 128
MAX_HOLDOUT_INTEGER_DIGITS = 128
MAX_SELECTION_REJECTION_REASONS = 128
MAX_SELECTION_REJECTION_REASON_CHARACTERS = 128

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_METRICS = frozenset(ComparisonMetric)


class WalkForwardWindowKind(StrEnum):
    ROLLING_FIXED_NON_OVERLAPPING_TEST = "ROLLING_FIXED_NON_OVERLAPPING_TEST"


class WalkForwardSelectionKind(StrEnum):
    SINGLE_VALIDATION_METRIC = "SINGLE_VALIDATION_METRIC"


class WalkForwardSelectionDirection(StrEnum):
    MAXIMIZE = "MAXIMIZE"
    MINIMIZE = "MINIMIZE"


class WalkForwardTieBreakPolicy(StrEnum):
    COMBINATION_INDEX_THEN_ID = "COMBINATION_INDEX_THEN_ID"


class WalkForwardOrderingPolicy(StrEnum):
    CHRONOLOGICAL_FOLDS = "CHRONOLOGICAL_FOLDS"


class WalkForwardFailurePolicy(StrEnum):
    CONTINUE_AFTER_FOLD_FAILURE = "CONTINUE_AFTER_FOLD_FAILURE"


class SelectionEvidenceStatus(StrEnum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"


class SelectionCandidateStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"


class WalkForwardFoldStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED_EXECUTION = "FAILED_EXECUTION"
    FAILED_NO_ELIGIBLE_CANDIDATE = "FAILED_NO_ELIGIBLE_CANDIDATE"
    FAILED_SELECTION = "FAILED_SELECTION"
    FAILED_HOLDOUT = "FAILED_HOLDOUT"


class WalkForwardExecutionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class WalkForwardWindowPolicy:
    train_candles: int
    validation_candles: int
    test_candles: int
    warmup_candles: int
    max_folds: int = DEFAULT_MAX_FOLDS
    kind: WalkForwardWindowKind = WalkForwardWindowKind.ROLLING_FIXED_NON_OVERLAPPING_TEST
    schema_version: int = WINDOW_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_window_policy(self)


@dataclass(frozen=True, slots=True)
class WalkForwardSelectionPolicy:
    metric: ComparisonMetric
    direction: WalkForwardSelectionDirection
    tie_break: WalkForwardTieBreakPolicy = WalkForwardTieBreakPolicy.COMBINATION_INDEX_THEN_ID
    kind: WalkForwardSelectionKind = WalkForwardSelectionKind.SINGLE_VALIDATION_METRIC
    schema_version: int = SELECTION_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_selection_policy(self)


@dataclass(frozen=True, slots=True)
class WalkForwardFoldPlan:
    fold_index: int
    selected_coverage: TemporalCoverage
    temporal_plan: TemporalSegmentationPlan
    experiment_plan: ExperimentPlan
    checksum: str
    fold_id: str

    def __post_init__(self) -> None:
        validate_walk_forward_fold_plan(self)


@dataclass(frozen=True, slots=True)
class WalkForwardPlan:
    snapshot: TemporalSnapshotReference
    window_policy: WalkForwardWindowPolicy
    selection_policy: WalkForwardSelectionPolicy
    search_space: ParameterSearchSpace
    plugin: ExperimentPluginReference
    backtest_configuration: ExperimentBacktestConfiguration
    folds: tuple[WalkForwardFoldPlan, ...]
    fold_count: int
    combination_count: int
    specs_per_fold: int
    total_specs: int
    trailing_candles: int
    max_total_specs: int
    checksum: str
    walk_forward_plan_id: str
    ordering_policy: WalkForwardOrderingPolicy = WalkForwardOrderingPolicy.CHRONOLOGICAL_FOLDS
    schema_version: int = WALK_FORWARD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_walk_forward_plan(self)


@dataclass(frozen=True, slots=True)
class SelectionExecutionReference:
    run_spec_id: str
    run_id: str
    logical_result_checksum: str
    artifact_path: str
    status: SelectionEvidenceStatus = SelectionEvidenceStatus.VERIFIED_SUCCESS

    def __post_init__(self) -> None:
        validate_selection_reference(self)


@dataclass(frozen=True, slots=True)
class SelectionCandidateEvidence:
    combination_index: int
    combination_id: str
    parameters: tuple[tuple[str, object], ...]
    status: SelectionCandidateStatus
    rejection_reason: str | None
    train: SelectionExecutionReference | None
    validation: SelectionExecutionReference | None
    validation_metric: ComparisonMetric
    validation_score: Decimal | None

    def __post_init__(self) -> None:
        validate_selection_candidate(self)


@dataclass(frozen=True, slots=True)
class FoldSelectionEvidence:
    """Authenticated TRAIN/VALIDATION-only projection for one complete candidate set."""

    fold_id: str
    fold_index: int
    experiment_id: str
    policy: WalkForwardSelectionPolicy
    candidates: tuple[SelectionCandidateEvidence, ...]
    eligible_count: int
    rejected_count: int
    checksum: str
    selection_evidence_id: str

    def __post_init__(self) -> None:
        validate_fold_selection_evidence(self)


@dataclass(frozen=True, slots=True)
class FoldSelectionDecision:
    fold_id: str
    fold_index: int
    policy: WalkForwardSelectionPolicy
    combination_index: int
    combination_id: str
    parameters: tuple[tuple[str, object], ...]
    score: Decimal
    rank: int
    eligible_count: int
    rejected_count: int
    rejection_reasons: tuple[str, ...]
    train: SelectionExecutionReference
    validation: SelectionExecutionReference
    selection_evidence_id: str
    selection_evidence_checksum: str
    checksum: str
    selection_id: str

    def __post_init__(self) -> None:
        validate_selection_decision(self)


MetricValue = Decimal | int | None


@dataclass(frozen=True, slots=True)
class SelectedHoldoutResult:
    run_spec_id: str
    run_id: str
    logical_result_checksum: str
    artifact_path: str
    metrics: tuple[tuple[str, MetricValue], ...]

    def __post_init__(self) -> None:
        validate_holdout_result(self)


@dataclass(frozen=True, slots=True)
class WalkForwardFailure:
    code: str
    message: str

    def __post_init__(self) -> None:
        validate_walk_forward_failure(self)


@dataclass(frozen=True, slots=True)
class WalkForwardFoldResult:
    fold_id: str
    fold_index: int
    experiment_id: str
    experiment_execution_id: str | None
    experiment_execution_checksum: str | None
    status: WalkForwardFoldStatus
    selection_evidence: FoldSelectionEvidence | None
    selection: FoldSelectionDecision | None
    holdout: SelectedHoldoutResult | None
    failure: WalkForwardFailure | None
    checksum: str

    def __post_init__(self) -> None:
        validate_walk_forward_fold_result(self)


@dataclass(frozen=True, slots=True)
class WalkForwardExecutionManifest:
    walk_forward_plan_id: str
    plan_checksum: str
    snapshot: TemporalSnapshotReference
    window_policy: WalkForwardWindowPolicy
    selection_policy: WalkForwardSelectionPolicy
    failure_policy: WalkForwardFailurePolicy
    ordering_policy: WalkForwardOrderingPolicy
    folds: tuple[WalkForwardFoldResult, ...]
    fold_count: int
    completed_count: int
    failed_count: int
    status: WalkForwardExecutionStatus
    checksum: str
    walk_forward_execution_id: str
    schema_version: int = WALK_FORWARD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_walk_forward_execution_manifest(self)


def validate_window_policy(policy: WalkForwardWindowPolicy) -> None:
    if not isinstance(policy, WalkForwardWindowPolicy):
        raise InvalidWalkForwardWindowPolicyError()
    if policy.kind is not WalkForwardWindowKind.ROLLING_FIXED_NON_OVERLAPPING_TEST:
        raise InvalidWalkForwardWindowPolicyError("unsupported walk-forward window policy")
    _exact_int(policy.schema_version, "window policy schema", minimum=1, maximum=1)
    for label, value in (
        ("train candles", policy.train_candles),
        ("validation candles", policy.validation_candles),
        ("test candles", policy.test_candles),
    ):
        _exact_int(value, label, minimum=1, maximum=10_000_000)
    _exact_int(policy.warmup_candles, "warmup candles", minimum=0, maximum=10_000_000)
    _exact_int(policy.max_folds, "maximum folds", minimum=2, maximum=ABSOLUTE_MAX_FOLDS)


def validate_selection_policy(policy: WalkForwardSelectionPolicy) -> None:
    if not isinstance(policy, WalkForwardSelectionPolicy):
        raise InvalidWalkForwardSelectionPolicyError()
    if policy.kind is not WalkForwardSelectionKind.SINGLE_VALIDATION_METRIC:
        raise InvalidWalkForwardSelectionPolicyError("unsupported selection policy")
    if not isinstance(policy.metric, ComparisonMetric) or policy.metric not in _SUPPORTED_METRICS:
        raise UnknownWalkForwardMetricError()
    if not isinstance(policy.direction, WalkForwardSelectionDirection):
        raise InvalidWalkForwardSelectionPolicyError("selection direction is invalid")
    if policy.tie_break is not WalkForwardTieBreakPolicy.COMBINATION_INDEX_THEN_ID:
        raise InvalidWalkForwardSelectionPolicyError("tie-break policy is invalid")
    _exact_int(policy.schema_version, "selection policy schema", minimum=1, maximum=1)


def window_policy_payload(policy: WalkForwardWindowPolicy) -> dict[str, object]:
    validate_window_policy(policy)
    return {
        "schema_version": policy.schema_version,
        "kind": policy.kind.value,
        "train_candles": policy.train_candles,
        "validation_candles": policy.validation_candles,
        "test_candles": policy.test_candles,
        "warmup_candles": policy.warmup_candles,
        "max_folds": policy.max_folds,
    }


def selection_policy_payload(policy: WalkForwardSelectionPolicy) -> dict[str, object]:
    validate_selection_policy(policy)
    return {
        "schema_version": policy.schema_version,
        "kind": policy.kind.value,
        "metric": policy.metric.value,
        "direction": policy.direction.value,
        "tie_break": policy.tie_break.value,
        "missing_metric": "REJECT_CANDIDATE",
        "invalid_metric": "REJECT_CANDIDATE",
    }


def fold_plan_payload(fold: WalkForwardFoldPlan) -> dict[str, object]:
    return fold_plan_values_payload(
        fold.fold_index,
        fold.selected_coverage,
        fold.temporal_plan,
        fold.experiment_plan,
    )


def fold_plan_values_payload(
    fold_index: int,
    selected_coverage: TemporalCoverage,
    temporal_plan: TemporalSegmentationPlan,
    experiment_plan: ExperimentPlan,
) -> dict[str, object]:
    return {
        "fold_index": fold_index,
        "selected_coverage": temporal_coverage_payload(selected_coverage),
        "temporal_plan": temporal_to_document(temporal_plan),
        "temporal_plan_checksum": temporal_plan.checksum,
        "temporal_plan_id": temporal_plan.plan_id,
        "experiment_plan": experiment_to_document(experiment_plan),
    }


def fold_plan_document(fold: WalkForwardFoldPlan) -> dict[str, object]:
    return {"fold": fold_plan_payload(fold), "checksum": fold.checksum, "fold_id": fold.fold_id}


def validate_walk_forward_fold_plan(fold: WalkForwardFoldPlan) -> None:
    if not isinstance(fold, WalkForwardFoldPlan):
        raise IncompatibleWalkForwardFoldError()
    _exact_int(fold.fold_index, "fold index", minimum=0, maximum=ABSOLUTE_MAX_FOLDS - 1)
    if not isinstance(fold.selected_coverage, TemporalCoverage):
        raise IncompatibleWalkForwardFoldError("fold coverage is invalid")
    validate_temporal_coverage(fold.selected_coverage)
    if not isinstance(fold.temporal_plan, TemporalSegmentationPlan):
        raise IncompatibleWalkForwardFoldError("temporal plan is invalid")
    validate_temporal_segmentation_plan(fold.temporal_plan)
    if fold.temporal_plan.selected_coverage != fold.selected_coverage:
        raise IncompatibleWalkForwardFoldError("fold coverage diverges from temporal plan")
    if not isinstance(fold.experiment_plan, ExperimentPlan):
        raise IncompatibleWalkForwardFoldError("experiment plan is invalid")
    validate_experiment_plan(fold.experiment_plan)
    if fold.experiment_plan.temporal_plan != fold.temporal_plan:
        raise IncompatibleWalkForwardFoldError("fold plans diverge")
    payload = fold_plan_payload(fold)
    if fold.checksum != document_checksum(payload):
        raise WalkForwardChecksumError("fold checksum does not match its payload")
    if fold.fold_id != deterministic_id("adt-walk-forward-fold-v1", payload):
        raise WalkForwardIdentifierError("fold id does not match its payload")


def walk_forward_plan_payload(plan: WalkForwardPlan) -> dict[str, object]:
    return walk_forward_plan_values_payload(
        snapshot=plan.snapshot,
        window_policy=plan.window_policy,
        selection_policy=plan.selection_policy,
        search_space=plan.search_space,
        plugin=plan.plugin,
        backtest_configuration=plan.backtest_configuration,
        folds=plan.folds,
        fold_count=plan.fold_count,
        combination_count=plan.combination_count,
        specs_per_fold=plan.specs_per_fold,
        total_specs=plan.total_specs,
        trailing_candles=plan.trailing_candles,
        max_total_specs=plan.max_total_specs,
        ordering_policy=plan.ordering_policy,
        schema_version=plan.schema_version,
    )


def walk_forward_plan_values_payload(
    *,
    snapshot: TemporalSnapshotReference,
    window_policy: WalkForwardWindowPolicy,
    selection_policy: WalkForwardSelectionPolicy,
    search_space: ParameterSearchSpace,
    plugin: ExperimentPluginReference,
    backtest_configuration: ExperimentBacktestConfiguration,
    folds: tuple[WalkForwardFoldPlan, ...],
    fold_count: int,
    combination_count: int,
    specs_per_fold: int,
    total_specs: int,
    trailing_candles: int,
    max_total_specs: int,
    ordering_policy: WalkForwardOrderingPolicy,
    schema_version: int,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "snapshot": temporal_snapshot_payload(snapshot),
        "window_policy": window_policy_payload(window_policy),
        "selection_policy": selection_policy_payload(selection_policy),
        "search_space": search_space_to_document(search_space),
        "plugin": {
            "name": plugin.name,
            "version": plugin.version,
            "schema_version": plugin.schema_version,
            "lifecycle_version": plugin.lifecycle_version,
        },
        "backtest_configuration": backtest_configuration_payload(backtest_configuration),
        "folds": [fold_plan_document(item) for item in folds],
        "fold_count": fold_count,
        "combination_count": combination_count,
        "specs_per_fold": specs_per_fold,
        "total_specs": total_specs,
        "trailing_candles": trailing_candles,
        "max_total_specs": max_total_specs,
        "ordering_policy": ordering_policy.value,
    }


def validate_walk_forward_plan(plan: WalkForwardPlan) -> None:
    if not isinstance(plan, WalkForwardPlan):
        raise IncompatibleWalkForwardPlanError()
    _exact_int(plan.schema_version, "walk-forward schema", minimum=1, maximum=1)
    if plan.ordering_policy is not WalkForwardOrderingPolicy.CHRONOLOGICAL_FOLDS:
        raise IncompatibleWalkForwardPlanError("walk-forward ordering policy is invalid")
    if not isinstance(plan.snapshot, TemporalSnapshotReference):
        raise IncompatibleWalkForwardPlanError("snapshot reference is invalid")
    validate_temporal_snapshot_reference(plan.snapshot)
    validate_window_policy(plan.window_policy)
    validate_selection_policy(plan.selection_policy)
    validate_search_space_structure(plan.search_space)
    validate_experiment_plugin_reference(plan.plugin)
    validate_experiment_backtest_configuration(plan.backtest_configuration)
    if not isinstance(plan.folds, tuple) or len(plan.folds) < 2:
        raise IncompatibleWalkForwardPlanError("walk-forward requires at least two folds")
    if len(plan.folds) > plan.window_policy.max_folds:
        raise WalkForwardLimitExceededError("fold count exceeds configured limit")
    try:
        duration = get_timeframe(plan.snapshot.timeframe).duration
    except Exception as error:
        raise IncompatibleWalkForwardPlanError("snapshot timeframe is unsupported") from error
    total_duration = plan.snapshot.available_coverage.end - plan.snapshot.available_coverage.start
    total_candles, remainder = divmod(total_duration, duration)
    if remainder.total_seconds() != 0:
        raise IncompatibleWalkForwardPlanError("snapshot coverage is not timeframe aligned")
    policy = plan.window_policy
    window_candles = policy.train_candles + policy.validation_candles + policy.test_candles
    usable_candles = total_candles - policy.warmup_candles
    expected_fold_count = (
        0
        if usable_candles < window_candles
        else 1 + (usable_candles - window_candles) // policy.test_candles
    )
    expected_trailing = (
        usable_candles - (window_candles + (expected_fold_count - 1) * policy.test_candles)
        if expected_fold_count
        else usable_candles
    )
    first_start = plan.snapshot.available_coverage.start + duration * policy.warmup_candles
    for index, fold in enumerate(plan.folds):
        if not isinstance(fold, WalkForwardFoldPlan):
            raise IncompatibleWalkForwardFoldError()
        validate_walk_forward_fold_plan(fold)
        if fold.fold_index != index:
            raise IncompatibleWalkForwardFoldError("fold indexes are not contiguous")
        if fold.temporal_plan.snapshot != plan.snapshot:
            raise IncompatibleWalkForwardFoldError("fold snapshot diverges from plan")
        if fold.experiment_plan.search_space != plan.search_space:
            raise IncompatibleWalkForwardFoldError("fold search space diverges from plan")
        if fold.experiment_plan.plugin != plan.plugin:
            raise IncompatibleWalkForwardFoldError("fold plugin diverges from plan")
        if fold.experiment_plan.backtest_configuration != plan.backtest_configuration:
            raise IncompatibleWalkForwardFoldError("fold backtest configuration diverges")
        temporal = fold.temporal_plan
        if (
            temporal.train_candles != policy.train_candles
            or temporal.validation_candles != policy.validation_candles
            or temporal.test_candles != policy.test_candles
            or temporal.warmup_candles != policy.warmup_candles
        ):
            raise IncompatibleWalkForwardFoldError("fold temporal counts diverge from policy")
        expected_start = first_start + duration * (index * policy.test_candles)
        expected_end = expected_start + duration * window_candles
        if (
            fold.selected_coverage.start != expected_start
            or fold.selected_coverage.end != expected_end
            or fold.selected_coverage.candle_count != window_candles
        ):
            raise IncompatibleWalkForwardFoldError("fold coverage diverges from rolling policy")
        test_segment = fold.temporal_plan.segments[2]
        expected_test_start = expected_start + duration * (
            policy.train_candles + policy.validation_candles
        )
        if (
            test_segment.start != expected_test_start
            or test_segment.end != expected_test_start + duration * policy.test_candles
        ):
            raise IncompatibleWalkForwardFoldError("fold TEST coverage diverges from policy")
        if index and plan.folds[index - 1].temporal_plan.segments[2].end != test_segment.start:
            raise IncompatibleWalkForwardFoldError("fold TEST ranges are not contiguous")
    expected_specs_per_fold = plan.combination_count * 3
    expected_total = len(plan.folds) * expected_specs_per_fold
    for value, expected, label in (
        (plan.fold_count, expected_fold_count, "fold count"),
        (plan.combination_count, plan.search_space.cardinality, "combination count"),
        (plan.specs_per_fold, expected_specs_per_fold, "specs per fold"),
        (plan.total_specs, expected_total, "total specs"),
    ):
        if value != expected:
            raise IncompatibleWalkForwardPlanError(f"{label} is inconsistent")
    _exact_int(plan.trailing_candles, "trailing candles", minimum=0, maximum=10_000_000)
    if plan.trailing_candles != expected_trailing:
        raise IncompatibleWalkForwardPlanError("trailing candle count is inconsistent")
    consumed_end = plan.folds[-1].selected_coverage.end + duration * plan.trailing_candles
    if consumed_end != plan.snapshot.available_coverage.end:
        raise IncompatibleWalkForwardPlanError("final snapshot coverage is inconsistent")
    _exact_int(
        plan.max_total_specs,
        "maximum total specs",
        minimum=1,
        maximum=ABSOLUTE_MAX_TOTAL_SPECS,
    )
    if plan.total_specs > plan.max_total_specs:
        raise WalkForwardLimitExceededError("total spec count exceeds configured limit")
    payload = walk_forward_plan_payload(plan)
    if plan.checksum != document_checksum(payload):
        raise WalkForwardChecksumError("walk-forward plan checksum does not match")
    if plan.walk_forward_plan_id != deterministic_id("adt-walk-forward-plan-v1", payload):
        raise WalkForwardIdentifierError("walk-forward plan id does not match")


def validate_selection_reference(reference: SelectionExecutionReference) -> None:
    if not isinstance(reference, SelectionExecutionReference):
        raise InvalidWalkForwardCandidateError("selection reference is invalid")
    for label, value in (
        ("run spec id", reference.run_spec_id),
        ("run id", reference.run_id),
        ("logical checksum", reference.logical_result_checksum),
    ):
        _sha256(value, label)
    if not is_canonical_artifact_path(reference.artifact_path, reference.run_id):
        raise InvalidWalkForwardCandidateError("artifact path is unsafe or non-canonical")
    if reference.status is not SelectionEvidenceStatus.VERIFIED_SUCCESS:
        raise InvalidWalkForwardCandidateError("selection evidence is not successful")


def validate_selection_candidate(candidate: SelectionCandidateEvidence) -> None:
    if not isinstance(candidate, SelectionCandidateEvidence):
        raise InvalidWalkForwardCandidateError()
    _exact_int(candidate.combination_index, "combination index", minimum=0, maximum=100_000)
    _sha256(candidate.combination_id, "combination id")
    _validate_parameters(candidate.parameters)
    if not isinstance(candidate.status, SelectionCandidateStatus):
        raise InvalidWalkForwardCandidateError("candidate status is invalid")
    if not isinstance(candidate.validation_metric, ComparisonMetric):
        raise UnknownWalkForwardMetricError()
    if candidate.status is SelectionCandidateStatus.ELIGIBLE:
        if candidate.rejection_reason is not None:
            raise InvalidWalkForwardCandidateError("eligible candidate has a rejection reason")
        if candidate.train is None or candidate.validation is None:
            raise InvalidWalkForwardCandidateError("eligible candidate evidence is incomplete")
        validate_selection_reference(candidate.train)
        validate_selection_reference(candidate.validation)
        if candidate.train == candidate.validation:
            raise InvalidWalkForwardCandidateError("TRAIN and VALIDATION references must differ")
        _finite_decimal(candidate.validation_score, "validation score")
    elif (
        not isinstance(candidate.rejection_reason, str)
        or not candidate.rejection_reason
        or len(candidate.rejection_reason) > MAX_SELECTION_REJECTION_REASON_CHARACTERS
        or candidate.validation_score is not None
    ):
        raise InvalidWalkForwardCandidateError("rejected candidate evidence is inconsistent")
    else:
        if candidate.train is not None:
            validate_selection_reference(candidate.train)
        if candidate.validation is not None:
            validate_selection_reference(candidate.validation)
    if (
        candidate.train is not None
        and candidate.validation is not None
        and candidate.train == candidate.validation
    ):
        raise InvalidWalkForwardCandidateError("TRAIN and VALIDATION references must differ")
    _reject_test_keys(selection_candidate_payload(candidate))


def selection_reference_payload(reference: SelectionExecutionReference) -> dict[str, object]:
    validate_selection_reference(reference)
    return {
        "run_spec_id": reference.run_spec_id,
        "run_id": reference.run_id,
        "logical_result_checksum": reference.logical_result_checksum,
        "artifact_path": reference.artifact_path,
        "status": reference.status.value,
    }


def selection_candidate_payload(candidate: SelectionCandidateEvidence) -> dict[str, object]:
    if not isinstance(candidate, SelectionCandidateEvidence):
        raise InvalidWalkForwardCandidateError()
    if not isinstance(candidate.status, SelectionCandidateStatus):
        raise InvalidWalkForwardCandidateError("candidate status is invalid")
    if not isinstance(candidate.validation_metric, ComparisonMetric):
        raise UnknownWalkForwardMetricError()
    if not isinstance(candidate.parameters, tuple):
        raise InvalidWalkForwardCandidateError("candidate parameters must be a tuple")
    return {
        "combination_index": candidate.combination_index,
        "combination_id": candidate.combination_id,
        "parameters": [[name, _parameter_value(value)] for name, value in candidate.parameters],
        "status": candidate.status.value,
        "rejection_reason": candidate.rejection_reason,
        "train": None if candidate.train is None else selection_reference_payload(candidate.train),
        "validation": (
            None
            if candidate.validation is None
            else selection_reference_payload(candidate.validation)
        ),
        "validation_metric": candidate.validation_metric.value,
        "validation_score": (
            None if candidate.validation_score is None else decimal_text(candidate.validation_score)
        ),
    }


def selection_evidence_values_payload(
    *,
    fold_id: str,
    fold_index: int,
    experiment_id: str,
    policy: WalkForwardSelectionPolicy,
    candidates: tuple[SelectionCandidateEvidence, ...],
    eligible_count: int,
    rejected_count: int,
) -> dict[str, object]:
    return {
        "fold_id": fold_id,
        "fold_index": fold_index,
        "experiment_id": experiment_id,
        "policy": selection_policy_payload(policy),
        "candidates": [selection_candidate_payload(item) for item in candidates],
        "eligible_count": eligible_count,
        "rejected_count": rejected_count,
    }


def selection_evidence_payload(evidence: FoldSelectionEvidence) -> dict[str, object]:
    if not isinstance(evidence, FoldSelectionEvidence):
        raise IncompatibleWalkForwardSelectionError("selection evidence is invalid")
    return selection_evidence_values_payload(
        fold_id=evidence.fold_id,
        fold_index=evidence.fold_index,
        experiment_id=evidence.experiment_id,
        policy=evidence.policy,
        candidates=evidence.candidates,
        eligible_count=evidence.eligible_count,
        rejected_count=evidence.rejected_count,
    )


def validate_fold_selection_evidence(evidence: FoldSelectionEvidence) -> None:
    if not isinstance(evidence, FoldSelectionEvidence):
        raise IncompatibleWalkForwardSelectionError("selection evidence is invalid")
    _sha256(evidence.fold_id, "fold id")
    _exact_int(evidence.fold_index, "fold index", minimum=0, maximum=ABSOLUTE_MAX_FOLDS - 1)
    _sha256(evidence.experiment_id, "experiment id")
    validate_selection_policy(evidence.policy)
    if not isinstance(evidence.candidates, tuple) or not evidence.candidates:
        raise IncompatibleWalkForwardSelectionError("candidate evidence set is invalid")
    for candidate in evidence.candidates:
        validate_selection_candidate(candidate)
    ordering = tuple((item.combination_index, item.combination_id) for item in evidence.candidates)
    indexes = tuple(item.combination_index for item in evidence.candidates)
    identifiers = tuple(item.combination_id for item in evidence.candidates)
    if ordering != tuple(sorted(ordering)):
        raise IncompatibleWalkForwardSelectionError("candidate evidence order is invalid")
    if indexes != tuple(range(len(evidence.candidates))) or len(set(identifiers)) != len(
        identifiers
    ):
        raise IncompatibleWalkForwardSelectionError(
            "candidate evidence identities are duplicated or incomplete"
        )
    if any(item.validation_metric is not evidence.policy.metric for item in evidence.candidates):
        raise IncompatibleWalkForwardSelectionError(
            "candidate metric diverges from the selection policy"
        )
    eligible = sum(item.status is SelectionCandidateStatus.ELIGIBLE for item in evidence.candidates)
    rejected = len(evidence.candidates) - eligible
    if (
        evidence.eligible_count != eligible
        or evidence.rejected_count != rejected
        or eligible + rejected != len(evidence.candidates)
    ):
        raise IncompatibleWalkForwardSelectionError("candidate evidence counts are invalid")
    payload = selection_evidence_payload(evidence)
    _reject_test_keys(payload)
    if evidence.checksum != document_checksum(payload):
        raise WalkForwardChecksumError("selection evidence checksum does not match")
    if evidence.selection_evidence_id != deterministic_id(
        "adt-walk-forward-selection-evidence-v1", payload
    ):
        raise WalkForwardIdentifierError("selection evidence id does not match")


def selection_decision_payload(decision: FoldSelectionDecision) -> dict[str, object]:
    if not isinstance(decision, FoldSelectionDecision):
        raise IncompatibleWalkForwardSelectionError()
    payload = {
        "fold_id": decision.fold_id,
        "fold_index": decision.fold_index,
        "policy": selection_policy_payload(decision.policy),
        "combination_index": decision.combination_index,
        "combination_id": decision.combination_id,
        "parameters": [[name, _parameter_value(value)] for name, value in decision.parameters],
        "score": decimal_text(decision.score),
        "rank": decision.rank,
        "eligible_count": decision.eligible_count,
        "rejected_count": decision.rejected_count,
        "rejection_reasons": list(decision.rejection_reasons),
        "train": selection_reference_payload(decision.train),
        "validation": selection_reference_payload(decision.validation),
        "selection_evidence_id": decision.selection_evidence_id,
        "selection_evidence_checksum": decision.selection_evidence_checksum,
    }
    _reject_test_keys(payload)
    return payload


def validate_selection_decision(decision: FoldSelectionDecision) -> None:
    if not isinstance(decision, FoldSelectionDecision):
        raise IncompatibleWalkForwardSelectionError()
    validate_selection_policy(decision.policy)
    _sha256(decision.fold_id, "fold id")
    _exact_int(decision.fold_index, "fold index", minimum=0, maximum=ABSOLUTE_MAX_FOLDS - 1)
    _exact_int(decision.combination_index, "combination index", minimum=0, maximum=100_000)
    _sha256(decision.combination_id, "combination id")
    _validate_parameters(decision.parameters)
    _finite_decimal(decision.score, "selection score")
    if decision.rank != 1:
        raise IncompatibleWalkForwardSelectionError("winner rank must be one")
    _exact_int(decision.eligible_count, "eligible count", minimum=1, maximum=100_000)
    _exact_int(decision.rejected_count, "rejected count", minimum=0, maximum=100_000)
    if not isinstance(decision.rejection_reasons, tuple) or any(
        not isinstance(item, str) or not item or len(item) > 128
        for item in decision.rejection_reasons
    ):
        raise IncompatibleWalkForwardSelectionError("rejection reasons are invalid")
    validate_selection_reference(decision.train)
    validate_selection_reference(decision.validation)
    if decision.train == decision.validation:
        raise IncompatibleWalkForwardSelectionError("TRAIN and VALIDATION references must differ")
    _sha256(decision.selection_evidence_id, "selection evidence id")
    _sha256(decision.selection_evidence_checksum, "selection evidence checksum")
    payload = selection_decision_payload(decision)
    if decision.checksum != document_checksum(payload):
        raise WalkForwardChecksumError("selection checksum does not match")
    if decision.selection_id != deterministic_id("adt-walk-forward-selection-v1", payload):
        raise WalkForwardIdentifierError("selection id does not match")


def validate_selection_decision_against_evidence(
    decision: FoldSelectionDecision,
    evidence: FoldSelectionEvidence,
) -> None:
    """Recompute the complete deterministic ranking from authenticated evidence."""

    validate_selection_decision(decision)
    validate_fold_selection_evidence(evidence)
    if (
        decision.fold_id != evidence.fold_id
        or decision.fold_index != evidence.fold_index
        or decision.policy != evidence.policy
        or decision.selection_evidence_id != evidence.selection_evidence_id
        or decision.selection_evidence_checksum != evidence.checksum
    ):
        raise IncompatibleWalkForwardSelectionError(
            "selection decision diverges from its evidence set"
        )
    eligible = tuple(
        item for item in evidence.candidates if item.status is SelectionCandidateStatus.ELIGIBLE
    )
    if not eligible:
        raise IncompatibleWalkForwardSelectionError("selection has no eligible candidate")
    reverse = evidence.policy.direction is WalkForwardSelectionDirection.MAXIMIZE
    score_order = sorted(
        eligible,
        key=lambda item: _required_candidate_score(item),
        reverse=reverse,
    )
    best_score = _required_candidate_score(score_order[0])
    winner = min(
        (item for item in score_order if _required_candidate_score(item) == best_score),
        key=lambda item: (item.combination_index, item.combination_id),
    )
    reasons = tuple(
        item.rejection_reason
        for item in evidence.candidates
        if item.status is SelectionCandidateStatus.REJECTED
    )
    compact_reasons = _compact_rejection_reasons(reasons)
    if (
        decision.combination_index != winner.combination_index
        or decision.combination_id != winner.combination_id
        or decision.parameters != winner.parameters
        or decision.score != best_score
        or decision.rank != 1
        or decision.eligible_count != evidence.eligible_count
        or decision.rejected_count != evidence.rejected_count
        or decision.rejection_reasons != compact_reasons
        or decision.train != winner.train
        or decision.validation != winner.validation
    ):
        raise IncompatibleWalkForwardSelectionError("selection winner is not reproducible")
    payload = selection_decision_payload(decision)
    if decision.checksum != document_checksum(payload) or decision.selection_id != deterministic_id(
        "adt-walk-forward-selection-v1", payload
    ):
        raise IncompatibleWalkForwardSelectionError("selection identity is not reproducible")


def validate_holdout_result(result: SelectedHoldoutResult) -> None:
    if not isinstance(result, SelectedHoldoutResult):
        raise InvalidWalkForwardHoldoutError()
    for label, value in (
        ("run spec id", result.run_spec_id),
        ("run id", result.run_id),
        ("logical checksum", result.logical_result_checksum),
    ):
        _sha256(value, label)
    if not is_canonical_artifact_path(result.artifact_path, result.run_id):
        raise InvalidWalkForwardHoldoutError("holdout artifact path is invalid")
    if (
        not isinstance(result.metrics, tuple)
        or not result.metrics
        or len(result.metrics) > MAX_HOLDOUT_METRICS
    ):
        raise InvalidWalkForwardHoldoutError("holdout metrics are empty")
    names: list[str] = []
    for item in result.metrics:
        if not isinstance(item, tuple) or len(item) != 2:
            raise InvalidWalkForwardHoldoutError("holdout metric entry is invalid")
        name, metric_value = item
        if not isinstance(name, str) or not name or len(name) > MAX_HOLDOUT_METRIC_NAME_CHARACTERS:
            raise InvalidWalkForwardHoldoutError("holdout metric name is invalid")
        names.append(name)
        _metric_value(metric_value)
    if names != sorted(names) or len(names) != len(set(names)):
        raise InvalidWalkForwardHoldoutError("holdout metrics are not canonically ordered")


def holdout_payload(result: SelectedHoldoutResult) -> dict[str, object]:
    validate_holdout_result(result)
    return {
        "run_spec_id": result.run_spec_id,
        "run_id": result.run_id,
        "logical_result_checksum": result.logical_result_checksum,
        "artifact_path": result.artifact_path,
        "metrics": {name: _metric_value(value) for name, value in result.metrics},
    }


def fold_result_payload(result: WalkForwardFoldResult) -> dict[str, object]:
    if not isinstance(result, WalkForwardFoldResult):
        raise IncompatibleWalkForwardExecutionError("fold result is invalid")
    if not isinstance(result.status, WalkForwardFoldStatus):
        raise IncompatibleWalkForwardExecutionError("fold status is invalid")
    if result.selection_evidence is not None and not isinstance(
        result.selection_evidence, FoldSelectionEvidence
    ):
        raise IncompatibleWalkForwardExecutionError("selection evidence is invalid")
    if result.selection_evidence is not None:
        validate_fold_selection_evidence(result.selection_evidence)
    if result.selection is not None and not isinstance(result.selection, FoldSelectionDecision):
        raise IncompatibleWalkForwardExecutionError("selection decision is invalid")
    if result.selection is not None:
        validate_selection_decision(result.selection)
    if result.holdout is not None and not isinstance(result.holdout, SelectedHoldoutResult):
        raise IncompatibleWalkForwardExecutionError("holdout result is invalid")
    if result.holdout is not None:
        validate_holdout_result(result.holdout)
    if result.failure is not None and not isinstance(result.failure, WalkForwardFailure):
        raise IncompatibleWalkForwardExecutionError("fold failure is invalid")
    if result.failure is not None:
        validate_walk_forward_failure(result.failure)
    return {
        "fold_id": result.fold_id,
        "fold_index": result.fold_index,
        "experiment_id": result.experiment_id,
        "experiment_execution_id": result.experiment_execution_id,
        "experiment_execution_checksum": result.experiment_execution_checksum,
        "status": result.status.value,
        "selection_evidence": (
            None
            if result.selection_evidence is None
            else {
                "evidence": selection_evidence_payload(result.selection_evidence),
                "checksum": result.selection_evidence.checksum,
                "selection_evidence_id": result.selection_evidence.selection_evidence_id,
            }
        ),
        "selection": None
        if result.selection is None
        else {
            "decision": selection_decision_payload(result.selection),
            "checksum": result.selection.checksum,
            "selection_id": result.selection.selection_id,
        },
        "holdout": None if result.holdout is None else holdout_payload(result.holdout),
        "failure": None
        if result.failure is None
        else {"code": result.failure.code, "message": result.failure.message},
    }


def validate_walk_forward_fold_result(result: WalkForwardFoldResult) -> None:
    if not isinstance(result, WalkForwardFoldResult):
        raise IncompatibleWalkForwardExecutionError("fold result is invalid")
    _sha256(result.fold_id, "fold id")
    _sha256(result.experiment_id, "experiment id")
    _exact_int(result.fold_index, "fold index", minimum=0, maximum=ABSOLUTE_MAX_FOLDS - 1)
    if not isinstance(result.status, WalkForwardFoldStatus):
        raise IncompatibleWalkForwardExecutionError("fold status is invalid")
    if result.experiment_execution_id is not None:
        _sha256(result.experiment_execution_id, "experiment execution id")
    if result.experiment_execution_checksum is not None:
        _sha256(result.experiment_execution_checksum, "experiment execution checksum")
    if (result.experiment_execution_id is None) != (result.experiment_execution_checksum is None):
        raise IncompatibleWalkForwardExecutionError("execution reference is incomplete")
    if result.selection is not None:
        validate_selection_decision(result.selection)
        if result.selection.fold_id != result.fold_id:
            raise IncompatibleWalkForwardExecutionError("selection belongs to another fold")
        if result.selection.fold_index != result.fold_index:
            raise IncompatibleWalkForwardExecutionError("selection fold index diverges")
    if result.selection_evidence is not None:
        validate_fold_selection_evidence(result.selection_evidence)
        if (
            result.selection_evidence.fold_id != result.fold_id
            or result.selection_evidence.fold_index != result.fold_index
            or result.selection_evidence.experiment_id != result.experiment_id
        ):
            raise IncompatibleWalkForwardExecutionError("selection evidence belongs elsewhere")
    if result.selection is not None:
        if result.selection_evidence is None:
            raise IncompatibleWalkForwardExecutionError("selection evidence is absent")
        validate_selection_decision_against_evidence(result.selection, result.selection_evidence)
    if result.holdout is not None:
        validate_holdout_result(result.holdout)
    if result.failure is not None:
        validate_walk_forward_failure(result.failure)
    post_execution = {
        WalkForwardFoldStatus.COMPLETED,
        WalkForwardFoldStatus.FAILED_NO_ELIGIBLE_CANDIDATE,
        WalkForwardFoldStatus.FAILED_SELECTION,
        WalkForwardFoldStatus.FAILED_HOLDOUT,
    }
    if result.status in post_execution and result.experiment_execution_id is None:
        raise IncompatibleWalkForwardExecutionError("post-execution fold reference is absent")
    if result.status is WalkForwardFoldStatus.COMPLETED:
        if result.selection is None or result.holdout is None or result.failure is not None:
            raise IncompatibleWalkForwardExecutionError("completed fold is incomplete")
    elif result.status is WalkForwardFoldStatus.FAILED_HOLDOUT:
        if (
            result.selection_evidence is None
            or result.selection is None
            or result.holdout is not None
            or result.failure is None
        ):
            raise IncompatibleWalkForwardExecutionError("failed holdout state is inconsistent")
    elif result.status is WalkForwardFoldStatus.FAILED_NO_ELIGIBLE_CANDIDATE:
        if (
            result.selection_evidence is None
            or result.selection_evidence.eligible_count != 0
            or result.selection is not None
            or result.holdout is not None
            or result.failure is None
        ):
            raise IncompatibleWalkForwardExecutionError("no-eligible fold state is inconsistent")
    elif result.status is WalkForwardFoldStatus.FAILED_SELECTION:
        if result.selection is not None or result.holdout is not None or result.failure is None:
            raise IncompatibleWalkForwardExecutionError("failed selection state is inconsistent")
    elif result.status is WalkForwardFoldStatus.FAILED_EXECUTION:
        if (
            result.experiment_execution_id is not None
            or result.experiment_execution_checksum is not None
            or result.selection_evidence is not None
            or result.selection is not None
            or result.holdout is not None
            or result.failure is None
        ):
            raise IncompatibleWalkForwardExecutionError("failed execution state is inconsistent")
    if result.checksum != document_checksum(fold_result_payload(result)):
        raise WalkForwardChecksumError("fold result checksum does not match")


def execution_manifest_payload(manifest: WalkForwardExecutionManifest) -> dict[str, object]:
    if not isinstance(manifest, WalkForwardExecutionManifest):
        raise IncompatibleWalkForwardExecutionError()
    validate_walk_forward_execution_manifest_values(manifest)
    return {
        "schema_version": manifest.schema_version,
        "walk_forward_plan_id": manifest.walk_forward_plan_id,
        "plan_checksum": manifest.plan_checksum,
        "snapshot": temporal_snapshot_payload(manifest.snapshot),
        "window_policy": window_policy_payload(manifest.window_policy),
        "selection_policy": selection_policy_payload(manifest.selection_policy),
        "failure_policy": manifest.failure_policy.value,
        "ordering_policy": manifest.ordering_policy.value,
        "folds": [
            {"result": fold_result_payload(item), "checksum": item.checksum}
            for item in manifest.folds
        ],
        "fold_count": manifest.fold_count,
        "completed_count": manifest.completed_count,
        "failed_count": manifest.failed_count,
        "status": manifest.status.value,
    }


def validate_walk_forward_execution_manifest(manifest: WalkForwardExecutionManifest) -> None:
    if not isinstance(manifest, WalkForwardExecutionManifest):
        raise IncompatibleWalkForwardExecutionError()
    _exact_int(manifest.schema_version, "walk-forward schema", minimum=1, maximum=1)
    _sha256(manifest.walk_forward_plan_id, "walk-forward plan id")
    _sha256(manifest.plan_checksum, "walk-forward plan checksum")
    validate_temporal_snapshot_reference(manifest.snapshot)
    validate_window_policy(manifest.window_policy)
    validate_selection_policy(manifest.selection_policy)
    if manifest.failure_policy is not WalkForwardFailurePolicy.CONTINUE_AFTER_FOLD_FAILURE:
        raise IncompatibleWalkForwardExecutionError("failure policy is invalid")
    if manifest.ordering_policy is not WalkForwardOrderingPolicy.CHRONOLOGICAL_FOLDS:
        raise IncompatibleWalkForwardExecutionError("ordering policy is invalid")
    if not isinstance(manifest.folds, tuple) or len(manifest.folds) < 2:
        raise IncompatibleWalkForwardExecutionError("execution folds are invalid")
    for index, result in enumerate(manifest.folds):
        validate_walk_forward_fold_result(result)
        if result.fold_index != index:
            raise IncompatibleWalkForwardExecutionError("execution folds are out of order")
    completed = sum(item.status is WalkForwardFoldStatus.COMPLETED for item in manifest.folds)
    failed = len(manifest.folds) - completed
    expected_status = (
        WalkForwardExecutionStatus.COMPLETED
        if failed == 0
        else WalkForwardExecutionStatus.FAILED
        if completed == 0
        else WalkForwardExecutionStatus.PARTIALLY_FAILED
    )
    if (
        manifest.fold_count != len(manifest.folds)
        or manifest.completed_count != completed
        or manifest.failed_count != failed
        or manifest.status is not expected_status
    ):
        raise IncompatibleWalkForwardExecutionError("aggregate execution state is inconsistent")
    payload = execution_manifest_payload(manifest)
    if manifest.checksum != document_checksum(payload):
        raise WalkForwardChecksumError("walk-forward execution checksum does not match")
    if manifest.walk_forward_execution_id != deterministic_id(
        "adt-walk-forward-execution-v1", payload
    ):
        raise WalkForwardIdentifierError("walk-forward execution id does not match")


def validate_walk_forward_execution_manifest_values(
    manifest: WalkForwardExecutionManifest,
) -> None:
    """Validate enum-bearing top-level values before payload field access."""

    if not isinstance(manifest, WalkForwardExecutionManifest):
        raise IncompatibleWalkForwardExecutionError()
    if not isinstance(manifest.failure_policy, WalkForwardFailurePolicy):
        raise IncompatibleWalkForwardExecutionError("failure policy is invalid")
    if not isinstance(manifest.ordering_policy, WalkForwardOrderingPolicy):
        raise IncompatibleWalkForwardExecutionError("ordering policy is invalid")
    if not isinstance(manifest.status, WalkForwardExecutionStatus):
        raise IncompatibleWalkForwardExecutionError("aggregate execution status is invalid")


def validate_walk_forward_failure(failure: WalkForwardFailure) -> None:
    if not isinstance(failure, WalkForwardFailure):
        raise IncompatibleWalkForwardExecutionError("fold failure is invalid")
    if not isinstance(failure.code, str) or not failure.code or len(failure.code) > 128:
        raise IncompatibleWalkForwardExecutionError("walk-forward failure code is invalid")
    if (
        not isinstance(failure.message, str)
        or not failure.message
        or len(failure.message) > MAX_WALK_FORWARD_ERROR_MESSAGE
    ):
        raise IncompatibleWalkForwardExecutionError("walk-forward failure message is invalid")


def _exact_int(value: object, label: str, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InvalidWalkForwardWindowPolicyError(f"{label} is outside its exact integer limit")


def _sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise WalkForwardIdentifierError(f"{label} is invalid")


def _finite_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidWalkForwardMetricError(f"{label} must be one finite Decimal")
    try:
        decimal_text(value)
    except Exception as error:
        raise InvalidWalkForwardMetricError(
            f"{label} exceeds its canonical character limit"
        ) from error
    return value


def _validate_parameters(parameters: object) -> None:
    if not isinstance(parameters, tuple):
        raise InvalidWalkForwardCandidateError("candidate parameters must be a tuple")
    names: list[str] = []
    for item in parameters:
        if not isinstance(item, tuple) or len(item) != 2:
            raise InvalidWalkForwardCandidateError("candidate parameter entry is invalid")
        name, value = item
        if not isinstance(name, str) or not name:
            raise InvalidWalkForwardCandidateError("candidate parameter name is invalid")
        names.append(name)
        _parameter_value(value)
    if names != sorted(names) or len(names) != len(set(names)):
        raise InvalidWalkForwardCandidateError("candidate parameters are not canonical")


def _parameter_value(value: object) -> object:
    if isinstance(value, bool):
        return {"kind": "bool", "value": value}
    if isinstance(value, str):
        if not value or value != value.strip() or len(value) > 128:
            raise InvalidWalkForwardCandidateError("candidate string value is invalid")
        return {"kind": "str", "value": value}
    if isinstance(value, int):
        try:
            integer_text(value)
        except Exception as error:
            raise InvalidWalkForwardCandidateError(
                "candidate integer value exceeds its limit"
            ) from error
        return {"kind": "int", "value": value}
    if isinstance(value, Decimal) and value.is_finite():
        try:
            encoded = decimal_text(value)
        except Exception as error:
            raise InvalidWalkForwardCandidateError(
                "candidate decimal value exceeds its limit"
            ) from error
        return {"kind": "decimal", "value": encoded}
    raise InvalidWalkForwardCandidateError("candidate parameter value is invalid")


def _metric_value(value: MetricValue) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidWalkForwardHoldoutError("boolean metric is invalid")
    if isinstance(value, int):
        try:
            encoded_integer = integer_text(value)
        except Exception as error:
            raise InvalidWalkForwardHoldoutError(
                "integer metric exceeds its digit limit"
            ) from error
        if len(encoded_integer.lstrip("-")) > MAX_HOLDOUT_INTEGER_DIGITS:
            raise InvalidWalkForwardHoldoutError("integer metric exceeds its digit limit")
        return value
    if isinstance(value, Decimal) and value.is_finite():
        try:
            return decimal_text(value)
        except Exception as error:
            raise InvalidWalkForwardHoldoutError(
                "decimal metric exceeds its character limit"
            ) from error
    raise InvalidWalkForwardHoldoutError("holdout metric value is invalid")


def _reject_test_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and "test" in key.lower():
                raise WalkForwardSelectionLeakageError()
            _reject_test_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_test_keys(nested)


def _required_candidate_score(candidate: SelectionCandidateEvidence) -> Decimal:
    if not isinstance(candidate.validation_score, Decimal):
        raise IncompatibleWalkForwardSelectionError("eligible candidate score is absent")
    return candidate.validation_score


def _compact_rejection_reasons(reasons: tuple[str | None, ...]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for reason in reasons:
        if not isinstance(reason, str) or not reason:
            raise IncompatibleWalkForwardSelectionError("candidate rejection reason is invalid")
        counts[reason] = counts.get(reason, 0) + 1
    compact = tuple(f"{reason}:{counts[reason]}" for reason in sorted(counts))
    if len(compact) > MAX_SELECTION_REJECTION_REASONS or any(
        len(item) > MAX_SELECTION_REJECTION_REASON_CHARACTERS for item in compact
    ):
        raise IncompatibleWalkForwardSelectionError("selection rejection summary exceeds limits")
    return compact
