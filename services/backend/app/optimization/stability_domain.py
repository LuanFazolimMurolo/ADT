"""Immutable deterministic contracts for Phase 4-06 stability analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import StrEnum

from app.backtesting.reports import ComparisonMetric
from app.optimization.canonical import decimal_text, deterministic_id, document_checksum
from app.optimization.errors import (
    IncompatibleStabilityReportError,
    InvalidStabilityMetricError,
    InvalidStabilityPolicyError,
    StabilityChecksumError,
    StabilityIdentifierError,
)
from app.optimization.walk_forward_domain import (
    WalkForwardFoldStatus,
    WalkForwardSelectionDirection,
)

STABILITY_REPORT_SCHEMA_VERSION = 1
STABILITY_POLICY_SCHEMA_VERSION = 1
MAX_STABILITY_REPORT_BYTES = 16 * 1024 * 1024
MAX_STABILITY_FOLDS = 1_000
STABILITY_BYTES_PER_FOLD_UPPER_BOUND = 8 * 1024
STABILITY_BASE_BYTES_UPPER_BOUND = 64 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_CONTEXT = Context(
    prec=512,
    rounding=ROUND_HALF_EVEN,
    Emin=-999_999,
    Emax=999_999,
)


class StabilityAnalysisKind(StrEnum):
    DETERMINISTIC_OOS_STABILITY = "DETERMINISTIC_OOS_STABILITY"


class StabilityControlName(StrEnum):
    MIN_COMPLETED_FOLDS = "MIN_COMPLETED_FOLDS"
    MIN_COMPLETION_RATIO = "MIN_COMPLETION_RATIO"
    MIN_TEST_NOT_WORSE_RATIO = "MIN_TEST_NOT_WORSE_RATIO"
    MAX_MEDIAN_DEGRADATION = "MAX_MEDIAN_DEGRADATION"
    MAX_WORST_DEGRADATION = "MAX_WORST_DEGRADATION"
    MAX_PARAMETER_TURNOVER_RATIO = "MAX_PARAMETER_TURNOVER_RATIO"


class StabilityAssessment(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class OverfittingAssessment(StrEnum):
    NO_SIGNAL = "NO_SIGNAL"
    POSSIBLE_OVERFITTING = "POSSIBLE_OVERFITTING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ParameterStabilityAssessment(StrEnum):
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class StabilityAnalysisPolicy:
    metric: ComparisonMetric
    direction: WalkForwardSelectionDirection
    minimum_completed_folds: int
    minimum_completion_ratio: Decimal
    minimum_test_not_worse_ratio: Decimal
    maximum_median_degradation: Decimal
    maximum_worst_degradation: Decimal
    maximum_parameter_turnover_ratio: Decimal
    kind: StabilityAnalysisKind = StabilityAnalysisKind.DETERMINISTIC_OOS_STABILITY
    schema_version: int = STABILITY_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_stability_policy(self)


@dataclass(frozen=True, slots=True)
class ExactRatio:
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        validate_exact_ratio(self)


@dataclass(frozen=True, slots=True)
class MetricDistribution:
    count: int
    minimum: Decimal
    median: Decimal
    maximum: Decimal

    def __post_init__(self) -> None:
        validate_metric_distribution(self)


@dataclass(frozen=True, slots=True)
class StabilityFoldObservation:
    fold_id: str
    fold_index: int
    source_status: WalkForwardFoldStatus
    selection_id: str | None
    combination_index: int | None
    combination_id: str | None
    parameter_set_id: str | None
    validation_score: Decimal | None
    test_score: Decimal | None
    degradation: Decimal | None
    test_not_worse: bool | None
    parameter_changed_from_previous_completed: bool | None
    checksum: str
    observation_id: str

    def __post_init__(self) -> None:
        validate_stability_fold_observation(self)


@dataclass(frozen=True, slots=True)
class StabilityControlResult:
    name: StabilityControlName
    passed: bool

    def __post_init__(self) -> None:
        validate_stability_control_result(self)


def validate_stability_control_result(result: StabilityControlResult) -> None:
    if not isinstance(result, StabilityControlResult):
        raise IncompatibleStabilityReportError("stability control result is invalid")
    if not isinstance(result.name, StabilityControlName) or not isinstance(
        result.passed,
        bool,
    ):
        raise IncompatibleStabilityReportError("stability control result is invalid")


@dataclass(frozen=True, slots=True)
class StabilityReport:
    walk_forward_plan_id: str
    plan_checksum: str
    walk_forward_execution_id: str
    execution_checksum: str
    policy: StabilityAnalysisPolicy
    observations: tuple[StabilityFoldObservation, ...]
    fold_count: int
    completed_count: int
    failed_count: int
    completion_ratio: ExactRatio
    test_not_worse_count: int
    test_not_worse_ratio: ExactRatio
    parameter_transition_count: int
    parameter_switch_count: int
    parameter_turnover_ratio: ExactRatio
    validation_distribution: MetricDistribution | None
    test_distribution: MetricDistribution | None
    degradation_distribution: MetricDistribution | None
    controls: tuple[StabilityControlResult, ...]
    overfitting_assessment: OverfittingAssessment
    parameter_stability_assessment: ParameterStabilityAssessment
    assessment: StabilityAssessment
    checksum: str
    stability_report_id: str
    schema_version: int = STABILITY_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_stability_report(self)


def validate_stability_policy(policy: StabilityAnalysisPolicy) -> None:
    if not isinstance(policy, StabilityAnalysisPolicy):
        raise InvalidStabilityPolicyError()
    if policy.kind is not StabilityAnalysisKind.DETERMINISTIC_OOS_STABILITY:
        raise InvalidStabilityPolicyError("unsupported stability policy")
    _exact_int(policy.schema_version, "stability policy schema", 1, 1)
    if not isinstance(policy.metric, ComparisonMetric):
        raise InvalidStabilityPolicyError("stability metric is invalid")
    if not isinstance(policy.direction, WalkForwardSelectionDirection):
        raise InvalidStabilityPolicyError("stability direction is invalid")
    _exact_int(
        policy.minimum_completed_folds,
        "minimum completed folds",
        2,
        MAX_STABILITY_FOLDS,
    )
    _ratio_threshold(policy.minimum_completion_ratio, "minimum completion ratio")
    _ratio_threshold(policy.minimum_test_not_worse_ratio, "minimum TEST-not-worse ratio")
    _nonnegative_decimal(
        policy.maximum_median_degradation,
        "maximum median degradation",
    )
    _nonnegative_decimal(
        policy.maximum_worst_degradation,
        "maximum worst degradation",
    )
    _ratio_threshold(
        policy.maximum_parameter_turnover_ratio,
        "maximum parameter turnover ratio",
    )


def stability_policy_payload(policy: StabilityAnalysisPolicy) -> dict[str, object]:
    validate_stability_policy(policy)
    return {
        "schema_version": policy.schema_version,
        "kind": policy.kind.value,
        "metric": policy.metric.value,
        "direction": policy.direction.value,
        "minimum_completed_folds": policy.minimum_completed_folds,
        "minimum_completion_ratio": decimal_text(policy.minimum_completion_ratio),
        "minimum_test_not_worse_ratio": decimal_text(policy.minimum_test_not_worse_ratio),
        "maximum_median_degradation": decimal_text(policy.maximum_median_degradation),
        "maximum_worst_degradation": decimal_text(policy.maximum_worst_degradation),
        "maximum_parameter_turnover_ratio": decimal_text(
            policy.maximum_parameter_turnover_ratio
        ),
    }


def validate_exact_ratio(ratio: ExactRatio) -> None:
    if not isinstance(ratio, ExactRatio):
        raise IncompatibleStabilityReportError("exact ratio is invalid")
    _exact_int(ratio.numerator, "ratio numerator", 0, MAX_STABILITY_FOLDS)
    _exact_int(ratio.denominator, "ratio denominator", 1, MAX_STABILITY_FOLDS)
    if ratio.numerator > ratio.denominator:
        raise IncompatibleStabilityReportError("exact ratio exceeds one")


def ratio_payload(ratio: ExactRatio) -> dict[str, object]:
    validate_exact_ratio(ratio)
    return {"numerator": ratio.numerator, "denominator": ratio.denominator}


def validate_metric_distribution(distribution: MetricDistribution) -> None:
    if not isinstance(distribution, MetricDistribution):
        raise InvalidStabilityMetricError("metric distribution is invalid")
    _exact_int(distribution.count, "metric distribution count", 1, MAX_STABILITY_FOLDS)
    minimum = _finite_decimal(distribution.minimum, "metric distribution minimum")
    median = _finite_decimal(distribution.median, "metric distribution median")
    maximum = _finite_decimal(distribution.maximum, "metric distribution maximum")
    if not minimum <= median <= maximum:
        raise InvalidStabilityMetricError("metric distribution ordering is invalid")


def metric_distribution_payload(distribution: MetricDistribution) -> dict[str, object]:
    validate_metric_distribution(distribution)
    return {
        "count": distribution.count,
        "minimum": decimal_text(distribution.minimum),
        "median": decimal_text(distribution.median),
        "maximum": decimal_text(distribution.maximum),
    }


def observation_values_payload(
    *,
    fold_id: str,
    fold_index: int,
    source_status: WalkForwardFoldStatus,
    selection_id: str | None,
    combination_index: int | None,
    combination_id: str | None,
    parameter_set_id: str | None,
    validation_score: Decimal | None,
    test_score: Decimal | None,
    degradation: Decimal | None,
    test_not_worse: bool | None,
    parameter_changed_from_previous_completed: bool | None,
) -> dict[str, object]:
    if not isinstance(source_status, WalkForwardFoldStatus):
        raise IncompatibleStabilityReportError("fold source status is invalid")
    return {
        "fold_id": fold_id,
        "fold_index": fold_index,
        "source_status": source_status.value,
        "selection_id": selection_id,
        "combination_index": combination_index,
        "combination_id": combination_id,
        "parameter_set_id": parameter_set_id,
        "validation_score": None if validation_score is None else decimal_text(validation_score),
        "test_score": None if test_score is None else decimal_text(test_score),
        "degradation": None if degradation is None else decimal_text(degradation),
        "test_not_worse": test_not_worse,
        "parameter_changed_from_previous_completed": parameter_changed_from_previous_completed,
    }


def observation_payload(observation: StabilityFoldObservation) -> dict[str, object]:
    validate_stability_fold_observation(observation)
    return observation_values_payload(
        fold_id=observation.fold_id,
        fold_index=observation.fold_index,
        source_status=observation.source_status,
        selection_id=observation.selection_id,
        combination_index=observation.combination_index,
        combination_id=observation.combination_id,
        parameter_set_id=observation.parameter_set_id,
        validation_score=observation.validation_score,
        test_score=observation.test_score,
        degradation=observation.degradation,
        test_not_worse=observation.test_not_worse,
        parameter_changed_from_previous_completed=(
            observation.parameter_changed_from_previous_completed
        ),
    )


def validate_stability_fold_observation_values(
    observation: StabilityFoldObservation,
) -> None:
    if not isinstance(observation, StabilityFoldObservation):
        raise IncompatibleStabilityReportError("fold observation is invalid")
    _sha256(observation.fold_id, "fold id")
    _exact_int(observation.fold_index, "fold index", 0, MAX_STABILITY_FOLDS - 1)
    if not isinstance(observation.source_status, WalkForwardFoldStatus):
        raise IncompatibleStabilityReportError("fold source status is invalid")
    completed = observation.source_status is WalkForwardFoldStatus.COMPLETED
    optional_values = (
        observation.selection_id,
        observation.combination_index,
        observation.combination_id,
        observation.parameter_set_id,
        observation.validation_score,
        observation.test_score,
        observation.degradation,
        observation.test_not_worse,
    )
    if completed:
        if any(value is None for value in optional_values):
            raise IncompatibleStabilityReportError("completed fold observation is incomplete")
        _sha256(observation.selection_id, "selection id")
        _exact_int(
            observation.combination_index,
            "combination index",
            0,
            100_000 - 1,
        )
        _sha256(observation.combination_id, "combination id")
        _sha256(observation.parameter_set_id, "parameter set id")
        _finite_decimal(observation.validation_score, "validation score")
        _finite_decimal(observation.test_score, "TEST score")
        _finite_decimal(observation.degradation, "degradation")
        if not isinstance(observation.test_not_worse, bool):
            raise IncompatibleStabilityReportError("TEST comparison flag is invalid")
        if observation.parameter_changed_from_previous_completed is not None and not isinstance(
            observation.parameter_changed_from_previous_completed,
            bool,
        ):
            raise IncompatibleStabilityReportError("parameter-change flag is invalid")
    elif any(value is not None for value in optional_values) or (
        observation.parameter_changed_from_previous_completed is not None
    ):
        raise IncompatibleStabilityReportError("failed fold observation contains metrics")


def validate_stability_fold_observation(observation: StabilityFoldObservation) -> None:
    validate_stability_fold_observation_values(observation)
    payload = observation_payload_values_only(observation)
    if observation.checksum != document_checksum(payload):
        raise StabilityChecksumError("fold observation checksum does not match")
    if observation.observation_id != deterministic_id("adt-stability-observation-v1", payload):
        raise StabilityIdentifierError("fold observation id does not match")


def observation_payload_values_only(observation: StabilityFoldObservation) -> dict[str, object]:
    validate_stability_fold_observation_values(observation)
    return observation_values_payload(
        fold_id=observation.fold_id,
        fold_index=observation.fold_index,
        source_status=observation.source_status,
        selection_id=observation.selection_id,
        combination_index=observation.combination_index,
        combination_id=observation.combination_id,
        parameter_set_id=observation.parameter_set_id,
        validation_score=observation.validation_score,
        test_score=observation.test_score,
        degradation=observation.degradation,
        test_not_worse=observation.test_not_worse,
        parameter_changed_from_previous_completed=(
            observation.parameter_changed_from_previous_completed
        ),
    )


def stability_report_payload(report: StabilityReport) -> dict[str, object]:
    validate_stability_report_values(report)
    return {
        "schema_version": report.schema_version,
        "walk_forward_plan_id": report.walk_forward_plan_id,
        "plan_checksum": report.plan_checksum,
        "walk_forward_execution_id": report.walk_forward_execution_id,
        "execution_checksum": report.execution_checksum,
        "policy": stability_policy_payload(report.policy),
        "observations": [
            {
                "observation": observation_payload_values_only(item),
                "checksum": item.checksum,
                "observation_id": item.observation_id,
            }
            for item in report.observations
        ],
        "fold_count": report.fold_count,
        "completed_count": report.completed_count,
        "failed_count": report.failed_count,
        "completion_ratio": ratio_payload(report.completion_ratio),
        "test_not_worse_count": report.test_not_worse_count,
        "test_not_worse_ratio": ratio_payload(report.test_not_worse_ratio),
        "parameter_transition_count": report.parameter_transition_count,
        "parameter_switch_count": report.parameter_switch_count,
        "parameter_turnover_ratio": ratio_payload(report.parameter_turnover_ratio),
        "validation_distribution": (
            None
            if report.validation_distribution is None
            else metric_distribution_payload(report.validation_distribution)
        ),
        "test_distribution": (
            None
            if report.test_distribution is None
            else metric_distribution_payload(report.test_distribution)
        ),
        "degradation_distribution": (
            None
            if report.degradation_distribution is None
            else metric_distribution_payload(report.degradation_distribution)
        ),
        "controls": [
            {"name": item.name.value, "passed": item.passed} for item in report.controls
        ],
        "overfitting_assessment": report.overfitting_assessment.value,
        "parameter_stability_assessment": report.parameter_stability_assessment.value,
        "assessment": report.assessment.value,
    }


def validate_stability_report_values(report: StabilityReport) -> None:
    if not isinstance(report, StabilityReport):
        raise IncompatibleStabilityReportError()
    _exact_int(report.schema_version, "stability report schema", 1, 1)
    for label, value in (
        ("walk-forward plan id", report.walk_forward_plan_id),
        ("walk-forward plan checksum", report.plan_checksum),
        ("walk-forward execution id", report.walk_forward_execution_id),
        ("walk-forward execution checksum", report.execution_checksum),
        ("stability report checksum", report.checksum),
        ("stability report id", report.stability_report_id),
    ):
        _sha256(value, label)
    validate_stability_policy(report.policy)
    if (
        not isinstance(report.observations, tuple)
        or not 2 <= len(report.observations) <= MAX_STABILITY_FOLDS
        or any(not isinstance(item, StabilityFoldObservation) for item in report.observations)
    ):
        raise IncompatibleStabilityReportError("stability observations are invalid")
    for observation in report.observations:
        validate_stability_fold_observation(observation)
    for label, value in (
        ("fold count", report.fold_count),
        ("completed count", report.completed_count),
        ("failed count", report.failed_count),
        ("TEST-not-worse count", report.test_not_worse_count),
        ("parameter transition count", report.parameter_transition_count),
        ("parameter switch count", report.parameter_switch_count),
    ):
        _exact_int(value, label, 0, MAX_STABILITY_FOLDS)
    for ratio in (
        report.completion_ratio,
        report.test_not_worse_ratio,
        report.parameter_turnover_ratio,
    ):
        validate_exact_ratio(ratio)
    for distribution in (
        report.validation_distribution,
        report.test_distribution,
        report.degradation_distribution,
    ):
        if distribution is not None:
            validate_metric_distribution(distribution)
    if not isinstance(report.controls, tuple) or any(
        not isinstance(item, StabilityControlResult) for item in report.controls
    ):
        raise IncompatibleStabilityReportError("stability controls are invalid")
    for control in report.controls:
        validate_stability_control_result(control)
    if not isinstance(report.overfitting_assessment, OverfittingAssessment):
        raise IncompatibleStabilityReportError("overfitting assessment is invalid")
    if not isinstance(report.parameter_stability_assessment, ParameterStabilityAssessment):
        raise IncompatibleStabilityReportError("parameter stability assessment is invalid")
    if not isinstance(report.assessment, StabilityAssessment):
        raise IncompatibleStabilityReportError("stability assessment is invalid")


def validate_stability_report(report: StabilityReport) -> None:
    validate_stability_report_values(report)
    for index, observation in enumerate(report.observations):
        if observation.fold_index != index:
            raise IncompatibleStabilityReportError("stability observations are out of order")
    if len({item.fold_id for item in report.observations}) != len(report.observations):
        raise IncompatibleStabilityReportError("stability observations contain duplicate folds")

    completed = tuple(
        item
        for item in report.observations
        if item.source_status is WalkForwardFoldStatus.COMPLETED
    )
    completed_count = len(completed)
    failed_count = len(report.observations) - completed_count
    if (
        report.fold_count != len(report.observations)
        or report.completed_count != completed_count
        or report.failed_count != failed_count
    ):
        raise IncompatibleStabilityReportError("stability fold counts are inconsistent")
    expected_completion = ExactRatio(completed_count, len(report.observations))
    if report.completion_ratio != expected_completion:
        raise IncompatibleStabilityReportError("completion ratio is inconsistent")

    test_not_worse_count = sum(item.test_not_worse is True for item in completed)
    expected_not_worse = ExactRatio(test_not_worse_count, completed_count or 1)
    if (
        report.test_not_worse_count != test_not_worse_count
        or report.test_not_worse_ratio != expected_not_worse
    ):
        raise IncompatibleStabilityReportError("TEST-not-worse ratio is inconsistent")

    transitions = max(completed_count - 1, 0)
    switches = sum(item.parameter_changed_from_previous_completed is True for item in completed)
    expected_turnover = ExactRatio(switches, transitions or 1)
    if (
        report.parameter_transition_count != transitions
        or report.parameter_switch_count != switches
        or report.parameter_turnover_ratio != expected_turnover
    ):
        raise IncompatibleStabilityReportError("parameter turnover is inconsistent")
    if completed and completed[0].parameter_changed_from_previous_completed is not None:
        raise IncompatibleStabilityReportError(
            "first completed fold cannot declare a parameter change"
        )
    if any(
        item.parameter_changed_from_previous_completed is None for item in completed[1:]
    ):
        raise IncompatibleStabilityReportError("parameter transitions are incomplete")

    previous_parameter_set_id: str | None = None
    for item in completed:
        validation_score = _required_decimal(item.validation_score)
        test_score = _required_decimal(item.test_score)
        expected_degradation = signed_degradation(
            validation_score,
            test_score,
            report.policy.direction,
        )
        if item.degradation != expected_degradation:
            raise IncompatibleStabilityReportError("fold degradation is inconsistent")
        if item.test_not_worse is not test_is_not_worse(
            validation_score,
            test_score,
            report.policy.direction,
        ):
            raise IncompatibleStabilityReportError("fold TEST comparison is inconsistent")
        expected_change = (
            None
            if previous_parameter_set_id is None
            else item.parameter_set_id != previous_parameter_set_id
        )
        if item.parameter_changed_from_previous_completed is not expected_change:
            raise IncompatibleStabilityReportError("fold parameter transition is inconsistent")
        previous_parameter_set_id = item.parameter_set_id

    validation_values = tuple(_required_decimal(item.validation_score) for item in completed)
    test_values = tuple(_required_decimal(item.test_score) for item in completed)
    degradation_values = tuple(_required_decimal(item.degradation) for item in completed)
    expected_validation = distribution_for(validation_values)
    expected_test = distribution_for(test_values)
    expected_degradation = distribution_for(degradation_values)
    if (
        report.validation_distribution != expected_validation
        or report.test_distribution != expected_test
        or report.degradation_distribution != expected_degradation
    ):
        raise IncompatibleStabilityReportError("metric distributions are inconsistent")

    expected_controls = controls_for(
        report.policy,
        completed_count=completed_count,
        completion_ratio=expected_completion,
        test_not_worse_ratio=expected_not_worse,
        parameter_turnover_ratio=expected_turnover,
        degradation_distribution=expected_degradation,
    )
    if report.controls != expected_controls:
        raise IncompatibleStabilityReportError("stability controls are inconsistent")
    expected_overfitting, expected_parameter, expected_assessment = assessments_for(
        report.policy,
        expected_controls,
        completed_count,
    )
    if (
        report.overfitting_assessment is not expected_overfitting
        or report.parameter_stability_assessment is not expected_parameter
        or report.assessment is not expected_assessment
    ):
        raise IncompatibleStabilityReportError("stability assessments are inconsistent")

    payload = stability_report_payload(report)
    if report.checksum != document_checksum(payload):
        raise StabilityChecksumError("stability report checksum does not match")
    if report.stability_report_id != deterministic_id("adt-stability-report-v1", payload):
        raise StabilityIdentifierError("stability report id does not match")


def distribution_for(values: tuple[Decimal, ...]) -> MetricDistribution | None:
    if not values:
        return None
    normalized = tuple(_finite_decimal(value, "distribution metric") for value in values)
    ordered = tuple(sorted(normalized))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[middle]
    else:
        with localcontext(_DECIMAL_CONTEXT):
            median = (ordered[middle - 1] + ordered[middle]) / Decimal(2)
        _finite_decimal(median, "distribution median")
    return MetricDistribution(len(ordered), ordered[0], median, ordered[-1])


def controls_for(
    policy: StabilityAnalysisPolicy,
    *,
    completed_count: int,
    completion_ratio: ExactRatio,
    test_not_worse_ratio: ExactRatio,
    parameter_turnover_ratio: ExactRatio,
    degradation_distribution: MetricDistribution | None,
) -> tuple[StabilityControlResult, ...]:
    validate_stability_policy(policy)
    median_degradation = (
        None if degradation_distribution is None else degradation_distribution.median
    )
    worst_degradation = (
        None if degradation_distribution is None else degradation_distribution.maximum
    )
    return (
        StabilityControlResult(
            StabilityControlName.MIN_COMPLETED_FOLDS,
            completed_count >= policy.minimum_completed_folds,
        ),
        StabilityControlResult(
            StabilityControlName.MIN_COMPLETION_RATIO,
            ratio_at_least(completion_ratio, policy.minimum_completion_ratio),
        ),
        StabilityControlResult(
            StabilityControlName.MIN_TEST_NOT_WORSE_RATIO,
            completed_count > 0
            and ratio_at_least(
                test_not_worse_ratio,
                policy.minimum_test_not_worse_ratio,
            ),
        ),
        StabilityControlResult(
            StabilityControlName.MAX_MEDIAN_DEGRADATION,
            median_degradation is not None
            and median_degradation <= policy.maximum_median_degradation,
        ),
        StabilityControlResult(
            StabilityControlName.MAX_WORST_DEGRADATION,
            worst_degradation is not None
            and worst_degradation <= policy.maximum_worst_degradation,
        ),
        StabilityControlResult(
            StabilityControlName.MAX_PARAMETER_TURNOVER_RATIO,
            ratio_at_most(
                parameter_turnover_ratio,
                policy.maximum_parameter_turnover_ratio,
            ),
        ),
    )


def assessments_for(
    policy: StabilityAnalysisPolicy,
    controls: tuple[StabilityControlResult, ...],
    completed_count: int,
) -> tuple[OverfittingAssessment, ParameterStabilityAssessment, StabilityAssessment]:
    validate_stability_policy(policy)
    by_name = {item.name: item.passed for item in controls}
    if len(by_name) != len(StabilityControlName):
        raise IncompatibleStabilityReportError("stability control set is incomplete")
    if completed_count < policy.minimum_completed_folds:
        return (
            OverfittingAssessment.INSUFFICIENT_DATA,
            ParameterStabilityAssessment.INSUFFICIENT_DATA,
            StabilityAssessment.INSUFFICIENT_DATA,
        )
    overfitting = (
        OverfittingAssessment.NO_SIGNAL
        if all(
            by_name[name]
            for name in (
                StabilityControlName.MIN_TEST_NOT_WORSE_RATIO,
                StabilityControlName.MAX_MEDIAN_DEGRADATION,
                StabilityControlName.MAX_WORST_DEGRADATION,
            )
        )
        else OverfittingAssessment.POSSIBLE_OVERFITTING
    )
    parameter = (
        ParameterStabilityAssessment.STABLE
        if all(
            by_name[name]
            for name in (
                StabilityControlName.MIN_COMPLETION_RATIO,
                StabilityControlName.MAX_PARAMETER_TURNOVER_RATIO,
            )
        )
        else ParameterStabilityAssessment.UNSTABLE
    )
    assessment = (
        StabilityAssessment.PASSED
        if all(item.passed for item in controls)
        else StabilityAssessment.FAILED
    )
    return overfitting, parameter, assessment


def ratio_at_least(ratio: ExactRatio, threshold: Decimal) -> bool:
    validate_exact_ratio(ratio)
    numerator, denominator = _decimal_fraction(_ratio_threshold(threshold, "ratio threshold"))
    return ratio.numerator * denominator >= numerator * ratio.denominator


def ratio_at_most(ratio: ExactRatio, threshold: Decimal) -> bool:
    validate_exact_ratio(ratio)
    numerator, denominator = _decimal_fraction(_ratio_threshold(threshold, "ratio threshold"))
    return ratio.numerator * denominator <= numerator * ratio.denominator


def signed_degradation(
    validation_score: Decimal,
    test_score: Decimal,
    direction: WalkForwardSelectionDirection,
) -> Decimal:
    validation = _finite_decimal(validation_score, "validation score")
    test = _finite_decimal(test_score, "TEST score")
    if not isinstance(direction, WalkForwardSelectionDirection):
        raise InvalidStabilityPolicyError("stability direction is invalid")
    with localcontext(_DECIMAL_CONTEXT):
        value = (
            validation - test
            if direction is WalkForwardSelectionDirection.MAXIMIZE
            else test - validation
        )
    return _finite_decimal(value, "signed degradation")


def test_is_not_worse(
    validation_score: Decimal,
    test_score: Decimal,
    direction: WalkForwardSelectionDirection,
) -> bool:
    validation = _finite_decimal(validation_score, "validation score")
    test = _finite_decimal(test_score, "TEST score")
    if direction is WalkForwardSelectionDirection.MAXIMIZE:
        return test >= validation
    if direction is WalkForwardSelectionDirection.MINIMIZE:
        return test <= validation
    raise InvalidStabilityPolicyError("stability direction is invalid")


def maximum_stability_report_bytes(fold_count: int) -> int:
    _exact_int(fold_count, "stability fold count", 2, MAX_STABILITY_FOLDS)
    return STABILITY_BASE_BYTES_UPPER_BOUND + (
        fold_count * STABILITY_BYTES_PER_FOLD_UPPER_BOUND
    )


def _exact_int(value: object, label: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise IncompatibleStabilityReportError(f"{label} is outside its exact integer limit")


def _sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise StabilityIdentifierError(f"{label} is invalid")


def _finite_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidStabilityMetricError(f"{label} must be one finite Decimal")
    try:
        decimal_text(value)
    except Exception as error:
        raise InvalidStabilityMetricError(f"{label} exceeds its canonical limit") from error
    return value


def _ratio_threshold(value: object, label: str) -> Decimal:
    decimal = _finite_decimal(value, label)
    if not Decimal(0) <= decimal <= Decimal(1):
        raise InvalidStabilityPolicyError(f"{label} must be between zero and one")
    return decimal


def _nonnegative_decimal(value: object, label: str) -> Decimal:
    decimal = _finite_decimal(value, label)
    if decimal < 0:
        raise InvalidStabilityPolicyError(f"{label} must be nonnegative")
    return decimal


def _decimal_fraction(value: Decimal) -> tuple[int, int]:
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise InvalidStabilityMetricError("decimal exponent is invalid")
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    if sign:
        coefficient = -coefficient
    if exponent >= 0:
        return coefficient * (10**exponent), 1
    return coefficient, 10 ** (-exponent)


def _required_decimal(value: Decimal | None) -> Decimal:
    if not isinstance(value, Decimal):
        raise IncompatibleStabilityReportError("completed observation metric is absent")
    return value
