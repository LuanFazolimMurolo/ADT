"""Deterministic Phase 4-06 analysis over verified walk-forward holdouts."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.optimization.canonical import canonical_json_bytes, deterministic_id, document_checksum
from app.optimization.errors import (
    IncompatibleStabilityReportError,
    IncompatibleStabilitySourceError,
    InvalidStabilityMetricError,
    InvalidStabilityPolicyError,
    StabilityAnalysisError,
    StabilityLimitExceededError,
    WalkForwardError,
)
from app.optimization.stability_domain import (
    MAX_STABILITY_REPORT_BYTES,
    ExactRatio,
    StabilityAnalysisPolicy,
    StabilityFoldObservation,
    StabilityReport,
    assessments_for,
    controls_for,
    distribution_for,
    maximum_stability_report_bytes,
    observation_values_payload,
    signed_degradation,
    stability_report_payload,
    test_is_not_worse,
    validate_stability_policy,
    validate_stability_report,
)
from app.optimization.walk_forward_domain import (
    SelectedHoldoutResult,
    WalkForwardExecutionManifest,
    WalkForwardFoldResult,
    WalkForwardFoldStatus,
    WalkForwardPlan,
    selection_decision_payload,
    validate_walk_forward_execution_manifest,
    validate_walk_forward_plan,
)

SourceValidator = Callable[
    [WalkForwardExecutionManifest],
    WalkForwardExecutionManifest,
]


class StabilityAnalysisService:
    """Create one bounded report only from an independently verified source."""

    def __init__(self, *, source_validator: SourceValidator) -> None:
        if not callable(source_validator):
            raise ValueError("stability source validator is required")
        self._source_validator = source_validator

    def analyze(
        self,
        plan: WalkForwardPlan,
        execution: WalkForwardExecutionManifest,
        policy: StabilityAnalysisPolicy,
    ) -> StabilityReport:
        _validate_source_contracts(plan, execution)
        validate_stability_policy(policy)
        if maximum_stability_report_bytes(plan.fold_count) > MAX_STABILITY_REPORT_BYTES:
            raise StabilityLimitExceededError("stability report would exceed its byte limit")
        verified = _validate_source(execution, self._source_validator)
        _validate_source_links(plan, verified, policy)
        report = _build_report(plan, verified, policy)
        encoded = canonical_json_bytes(
            {
                "stability_report": stability_report_payload(report),
                "checksum": report.checksum,
                "stability_report_id": report.stability_report_id,
            }
        )
        if len(encoded) > MAX_STABILITY_REPORT_BYTES:
            raise StabilityLimitExceededError("stability report exceeds its byte limit")
        return report


def validate_stability_report_against_walk_forward(
    report: StabilityReport,
    plan: WalkForwardPlan,
    execution: WalkForwardExecutionManifest,
    *,
    source_validator: SourceValidator,
) -> StabilityReport:
    """Recompute the complete report from its authenticated Phase 4-05 source."""

    validate_stability_report(report)
    rebuilt = StabilityAnalysisService(source_validator=source_validator).analyze(
        plan,
        execution,
        report.policy,
    )
    if rebuilt != report:
        raise IncompatibleStabilityReportError(
            "stability report diverges from its walk-forward source"
        )
    return report


def verify_published_stability_report(
    repository: object,
    report_id: str,
    plan: WalkForwardPlan,
    execution: WalkForwardExecutionManifest,
    *,
    source_validator: SourceValidator,
) -> StabilityReport:
    """Read and independently recompute one published stability report."""

    read = getattr(repository, "read", None)
    if not callable(read):
        raise IncompatibleStabilityReportError("stability repository is invalid")
    try:
        report = read(execution.walk_forward_execution_id, report_id)
    except StabilityAnalysisError:
        raise
    except Exception as error:
        raise IncompatibleStabilityReportError(
            "published stability report cannot be read"
        ) from error
    if not isinstance(report, StabilityReport):
        raise IncompatibleStabilityReportError("published stability report is invalid")
    return validate_stability_report_against_walk_forward(
        report,
        plan,
        execution,
        source_validator=source_validator,
    )


def _build_report(
    plan: WalkForwardPlan,
    execution: WalkForwardExecutionManifest,
    policy: StabilityAnalysisPolicy,
) -> StabilityReport:
    observations: list[StabilityFoldObservation] = []
    previous_parameter_set_id: str | None = None
    for planned, result in zip(plan.folds, execution.folds, strict=True):
        if result.status is WalkForwardFoldStatus.COMPLETED:
            observation = _completed_observation(
                planned.fold_id,
                planned.fold_index,
                result,
                policy,
                previous_parameter_set_id,
            )
            previous_parameter_set_id = observation.parameter_set_id
        else:
            observation = _failed_observation(planned.fold_id, planned.fold_index, result)
        observations.append(observation)

    typed_observations = tuple(observations)
    completed = tuple(
        item
        for item in typed_observations
        if item.source_status is WalkForwardFoldStatus.COMPLETED
    )
    completed_count = len(completed)
    failed_count = len(typed_observations) - completed_count
    completion_ratio = ExactRatio(completed_count, len(typed_observations))
    test_not_worse_count = sum(item.test_not_worse is True for item in completed)
    test_not_worse_ratio = ExactRatio(test_not_worse_count, completed_count or 1)
    transition_count = max(completed_count - 1, 0)
    switch_count = sum(
        item.parameter_changed_from_previous_completed is True for item in completed
    )
    turnover_ratio = ExactRatio(switch_count, transition_count or 1)
    validation_distribution = distribution_for(
        tuple(_required_metric(item.validation_score) for item in completed)
    )
    test_distribution = distribution_for(
        tuple(_required_metric(item.test_score) for item in completed)
    )
    degradation_distribution = distribution_for(
        tuple(_required_metric(item.degradation) for item in completed)
    )
    controls = controls_for(
        policy,
        completed_count=completed_count,
        completion_ratio=completion_ratio,
        test_not_worse_ratio=test_not_worse_ratio,
        parameter_turnover_ratio=turnover_ratio,
        degradation_distribution=degradation_distribution,
    )
    overfitting, parameter_stability, assessment = assessments_for(
        policy,
        controls,
        completed_count,
    )
    provisional = _report_projection(
        walk_forward_plan_id=plan.walk_forward_plan_id,
        plan_checksum=plan.checksum,
        walk_forward_execution_id=execution.walk_forward_execution_id,
        execution_checksum=execution.checksum,
        policy=policy,
        observations=typed_observations,
        fold_count=len(typed_observations),
        completed_count=completed_count,
        failed_count=failed_count,
        completion_ratio=completion_ratio,
        test_not_worse_count=test_not_worse_count,
        test_not_worse_ratio=test_not_worse_ratio,
        parameter_transition_count=transition_count,
        parameter_switch_count=switch_count,
        parameter_turnover_ratio=turnover_ratio,
        validation_distribution=validation_distribution,
        test_distribution=test_distribution,
        degradation_distribution=degradation_distribution,
        controls=controls,
        overfitting_assessment=overfitting,
        parameter_stability_assessment=parameter_stability,
        assessment=assessment,
        schema_version=1,
    )
    payload = stability_report_payload(provisional)
    return StabilityReport(
        walk_forward_plan_id=plan.walk_forward_plan_id,
        plan_checksum=plan.checksum,
        walk_forward_execution_id=execution.walk_forward_execution_id,
        execution_checksum=execution.checksum,
        policy=policy,
        observations=typed_observations,
        fold_count=len(typed_observations),
        completed_count=completed_count,
        failed_count=failed_count,
        completion_ratio=completion_ratio,
        test_not_worse_count=test_not_worse_count,
        test_not_worse_ratio=test_not_worse_ratio,
        parameter_transition_count=transition_count,
        parameter_switch_count=switch_count,
        parameter_turnover_ratio=turnover_ratio,
        validation_distribution=validation_distribution,
        test_distribution=test_distribution,
        degradation_distribution=degradation_distribution,
        controls=controls,
        overfitting_assessment=overfitting,
        parameter_stability_assessment=parameter_stability,
        assessment=assessment,
        checksum=document_checksum(payload),
        stability_report_id=deterministic_id("adt-stability-report-v1", payload),
        schema_version=1,
    )


def _completed_observation(
    fold_id: str,
    fold_index: int,
    result: WalkForwardFoldResult,
    policy: StabilityAnalysisPolicy,
    previous_parameter_set_id: str | None,
) -> StabilityFoldObservation:
    selection = result.selection
    holdout = result.holdout
    if selection is None or holdout is None:
        raise IncompatibleStabilitySourceError(
            "completed walk-forward fold lacks selection or holdout"
        )
    if (
        selection.policy.metric is not policy.metric
        or selection.policy.direction is not policy.direction
    ):
        raise IncompatibleStabilitySourceError("fold selection policy diverges from analysis")
    validation_score = selection.score
    test_score = _selected_metric(holdout, policy)
    degradation = signed_degradation(validation_score, test_score, policy.direction)
    not_worse = test_is_not_worse(validation_score, test_score, policy.direction)
    parameter_set_id = deterministic_id(
        "adt-stability-parameter-set-v1",
        selection_decision_payload(selection)["parameters"],
    )
    changed = (
        None
        if previous_parameter_set_id is None
        else parameter_set_id != previous_parameter_set_id
    )
    values = observation_values_payload(
        fold_id=fold_id,
        fold_index=fold_index,
        source_status=result.status,
        selection_id=selection.selection_id,
        combination_index=selection.combination_index,
        combination_id=selection.combination_id,
        parameter_set_id=parameter_set_id,
        validation_score=validation_score,
        test_score=test_score,
        degradation=degradation,
        test_not_worse=not_worse,
        parameter_changed_from_previous_completed=changed,
    )
    return StabilityFoldObservation(
        fold_id=fold_id,
        fold_index=fold_index,
        source_status=result.status,
        selection_id=selection.selection_id,
        combination_index=selection.combination_index,
        combination_id=selection.combination_id,
        parameter_set_id=parameter_set_id,
        validation_score=validation_score,
        test_score=test_score,
        degradation=degradation,
        test_not_worse=not_worse,
        parameter_changed_from_previous_completed=changed,
        checksum=document_checksum(values),
        observation_id=deterministic_id("adt-stability-observation-v1", values),
    )


def _failed_observation(
    fold_id: str,
    fold_index: int,
    result: WalkForwardFoldResult,
) -> StabilityFoldObservation:
    if result.status is WalkForwardFoldStatus.COMPLETED:
        raise IncompatibleStabilitySourceError("completed fold cannot be projected as failed")
    values = observation_values_payload(
        fold_id=fold_id,
        fold_index=fold_index,
        source_status=result.status,
        selection_id=None,
        combination_index=None,
        combination_id=None,
        parameter_set_id=None,
        validation_score=None,
        test_score=None,
        degradation=None,
        test_not_worse=None,
        parameter_changed_from_previous_completed=None,
    )
    return StabilityFoldObservation(
        fold_id=fold_id,
        fold_index=fold_index,
        source_status=result.status,
        selection_id=None,
        combination_index=None,
        combination_id=None,
        parameter_set_id=None,
        validation_score=None,
        test_score=None,
        degradation=None,
        test_not_worse=None,
        parameter_changed_from_previous_completed=None,
        checksum=document_checksum(values),
        observation_id=deterministic_id("adt-stability-observation-v1", values),
    )


def _selected_metric(
    holdout: SelectedHoldoutResult,
    policy: StabilityAnalysisPolicy,
) -> Decimal:
    matches = tuple(value for name, value in holdout.metrics if name == policy.metric.value)
    if len(matches) != 1:
        raise InvalidStabilityMetricError("selected TEST metric is absent or duplicated")
    raw = matches[0]
    if isinstance(raw, bool) or raw is None:
        raise InvalidStabilityMetricError("selected TEST metric is invalid")
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, Decimal) and raw.is_finite():
        return raw
    raise InvalidStabilityMetricError("selected TEST metric is invalid")


def _validate_source_contracts(
    plan: WalkForwardPlan,
    execution: WalkForwardExecutionManifest,
) -> None:
    try:
        validate_walk_forward_plan(plan)
        validate_walk_forward_execution_manifest(execution)
    except WalkForwardError as error:
        raise IncompatibleStabilitySourceError(
            "walk-forward source contracts are invalid"
        ) from error


def _validate_source(
    execution: WalkForwardExecutionManifest,
    validator: SourceValidator,
) -> WalkForwardExecutionManifest:
    expected_execution_id = execution.walk_forward_execution_id
    expected_checksum = execution.checksum
    try:
        verified = validator(execution)
        validate_walk_forward_execution_manifest(verified)
    except StabilityAnalysisError:
        raise
    except WalkForwardError as error:
        raise IncompatibleStabilitySourceError(
            "walk-forward source verification returned an invalid manifest"
        ) from error
    except Exception as error:
        raise IncompatibleStabilitySourceError(
            "walk-forward source verification failed"
        ) from error
    if verified != execution:
        raise IncompatibleStabilitySourceError(
            "walk-forward source validator returned incompatible content"
        )
    if (
        verified.walk_forward_execution_id != expected_execution_id
        or verified.checksum != expected_checksum
    ):
        raise IncompatibleStabilitySourceError(
            "walk-forward source changed during verification"
        )
    return verified


def _validate_source_links(
    plan: WalkForwardPlan,
    execution: WalkForwardExecutionManifest,
    policy: StabilityAnalysisPolicy,
) -> None:
    if (
        execution.walk_forward_plan_id != plan.walk_forward_plan_id
        or execution.plan_checksum != plan.checksum
        or execution.fold_count != plan.fold_count
        or len(execution.folds) != len(plan.folds)
    ):
        raise IncompatibleStabilitySourceError("walk-forward source diverges from its plan")
    if (
        policy.metric is not plan.selection_policy.metric
        or policy.direction is not plan.selection_policy.direction
    ):
        raise IncompatibleStabilitySourceError(
            "stability policy must use the walk-forward selection metric and direction"
        )
    if policy.minimum_completed_folds > plan.fold_count:
        raise InvalidStabilityPolicyError(
            "minimum completed folds exceeds the walk-forward fold count"
        )
    for planned, result in zip(plan.folds, execution.folds, strict=True):
        if planned.fold_id != result.fold_id or planned.fold_index != result.fold_index:
            raise IncompatibleStabilitySourceError("walk-forward fold identity diverges")


def _required_metric(value: Decimal | None) -> Decimal:
    if not isinstance(value, Decimal):
        raise IncompatibleStabilityReportError("completed stability metric is absent")
    return value


def _report_projection(**values: object) -> StabilityReport:
    report = object.__new__(StabilityReport)
    for key, value in values.items():
        object.__setattr__(report, key, value)
    object.__setattr__(report, "checksum", "0" * 64)
    object.__setattr__(report, "stability_report_id", "0" * 64)
    return report
