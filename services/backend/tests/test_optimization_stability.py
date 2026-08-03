"""Phase 4-06 deterministic stability and overfitting-control tests."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.reports import ComparisonMetric
from app.optimization import (
    IncompatibleStabilityDocumentError,
    IncompatibleStabilityReportError,
    IncompatibleStabilitySourceError,
    InvalidStabilityMetricError,
    InvalidStabilityPolicyError,
    OverfittingAssessment,
    ParameterStabilityAssessment,
    SelectedHoldoutResult,
    StabilityAnalysisPolicy,
    StabilityAnalysisService,
    StabilityAssessment,
    StabilityPublicationError,
    StabilityReport,
    StabilityReportRepository,
    WalkForwardExecutionManifest,
    WalkForwardExecutionStatus,
    WalkForwardFailure,
    WalkForwardFailurePolicy,
    WalkForwardFoldResult,
    WalkForwardFoldStatus,
    WalkForwardOrderingPolicy,
    WalkForwardPlan,
    WalkForwardSelectionDirection,
    WalkForwardSelectionPolicy,
    WalkForwardSelectionService,
    canonical_stability_report_bytes,
    decode_stability_report_document,
    maximum_stability_report_bytes,
    stability_report_to_document,
    validate_stability_report_against_walk_forward,
    validate_walk_forward_execution_manifest,
    verify_published_stability_report,
)
from app.optimization.canonical import deterministic_id, document_checksum
from app.optimization.stability_domain import (
    MAX_STABILITY_REPORT_BYTES,
    observation_values_payload,
    stability_report_payload,
)
from app.optimization.walk_forward_domain import (
    execution_manifest_payload,
    fold_result_payload,
)
from tests.test_optimization_walk_forward import _evidence, _plan


def _policy(
    *,
    direction: WalkForwardSelectionDirection = WalkForwardSelectionDirection.MAXIMIZE,
    minimum_completed_folds: int = 2,
    minimum_completion_ratio: str = "1",
    minimum_test_not_worse_ratio: str = "0.5",
    maximum_median_degradation: str = "0.05",
    maximum_worst_degradation: str = "0.1",
    maximum_parameter_turnover_ratio: str = "0.5",
) -> StabilityAnalysisPolicy:
    return StabilityAnalysisPolicy(
        metric=ComparisonMetric.TOTAL_RETURN,
        direction=direction,
        minimum_completed_folds=minimum_completed_folds,
        minimum_completion_ratio=Decimal(minimum_completion_ratio),
        minimum_test_not_worse_ratio=Decimal(minimum_test_not_worse_ratio),
        maximum_median_degradation=Decimal(maximum_median_degradation),
        maximum_worst_degradation=Decimal(maximum_worst_degradation),
        maximum_parameter_turnover_ratio=Decimal(maximum_parameter_turnover_ratio),
    )


def _source_validator(value: WalkForwardExecutionManifest) -> WalkForwardExecutionManifest:
    return value


def _execution(
    *,
    validation_scores: tuple[tuple[str, str], ...] = (("0.1", "0.05"), ("0.08", "0.04")),
    test_scores: tuple[str, ...] = ("0.11", "0.09"),
    fail_fold: int | None = None,
) -> tuple[WalkForwardPlan, WalkForwardExecutionManifest]:
    plan, _snapshot, _dataset_manifest = _plan()
    fold_results: list[WalkForwardFoldResult] = []
    for fold, fold_scores, test_score in zip(
        plan.folds,
        validation_scores,
        test_scores,
        strict=True,
    ):
        if fold.fold_index == fail_fold:
            provisional = object.__new__(WalkForwardFoldResult)
            values = {
                "fold_id": fold.fold_id,
                "fold_index": fold.fold_index,
                "experiment_id": fold.experiment_plan.experiment_id,
                "experiment_execution_id": None,
                "experiment_execution_checksum": None,
                "status": WalkForwardFoldStatus.FAILED_EXECUTION,
                "selection_evidence": None,
                "selection": None,
                "holdout": None,
                "failure": WalkForwardFailure("execution_failed", "fold failed"),
            }
            for name, value in values.items():
                object.__setattr__(provisional, name, value)
            object.__setattr__(provisional, "checksum", "0" * 64)
            fold_results.append(
                WalkForwardFoldResult(
                    **values,
                    checksum=document_checksum(fold_result_payload(provisional)),
                )
            )
            continue
        evidence = _evidence(
            fold,
            fold_scores,
            policy=plan.selection_policy,
        )
        decision = WalkForwardSelectionService().select(evidence)
        test_spec = fold.experiment_plan.run_specs[decision.combination_index * 3 + 2]
        run_id = f"{900 + fold.fold_index:064x}"
        holdout = SelectedHoldoutResult(
            run_spec_id=test_spec.run_spec_id,
            run_id=run_id,
            logical_result_checksum=f"{800 + fold.fold_index:064x}",
            artifact_path=f"backtests/{run_id}",
            metrics=(("total_return", Decimal(test_score)),),
        )
        provisional = object.__new__(WalkForwardFoldResult)
        values = {
            "fold_id": fold.fold_id,
            "fold_index": fold.fold_index,
            "experiment_id": fold.experiment_plan.experiment_id,
            "experiment_execution_id": f"{700 + fold.fold_index:064x}",
            "experiment_execution_checksum": f"{600 + fold.fold_index:064x}",
            "status": WalkForwardFoldStatus.COMPLETED,
            "selection_evidence": evidence,
            "selection": decision,
            "holdout": holdout,
            "failure": None,
        }
        for name, value in values.items():
            object.__setattr__(provisional, name, value)
        object.__setattr__(provisional, "checksum", "0" * 64)
        fold_results.append(
            WalkForwardFoldResult(
                **values,
                checksum=document_checksum(fold_result_payload(provisional)),
            )
        )
    folds = tuple(fold_results)
    completed = sum(item.status is WalkForwardFoldStatus.COMPLETED for item in folds)
    failed = len(folds) - completed
    status = (
        WalkForwardExecutionStatus.COMPLETED
        if failed == 0
        else WalkForwardExecutionStatus.FAILED
        if completed == 0
        else WalkForwardExecutionStatus.PARTIALLY_FAILED
    )
    provisional_manifest = object.__new__(WalkForwardExecutionManifest)
    manifest_values = {
        "walk_forward_plan_id": plan.walk_forward_plan_id,
        "plan_checksum": plan.checksum,
        "snapshot": plan.snapshot,
        "window_policy": plan.window_policy,
        "selection_policy": plan.selection_policy,
        "failure_policy": WalkForwardFailurePolicy.CONTINUE_AFTER_FOLD_FAILURE,
        "ordering_policy": WalkForwardOrderingPolicy.CHRONOLOGICAL_FOLDS,
        "folds": folds,
        "fold_count": len(folds),
        "completed_count": completed,
        "failed_count": failed,
        "status": status,
        "schema_version": 1,
    }
    for name, value in manifest_values.items():
        object.__setattr__(provisional_manifest, name, value)
    object.__setattr__(provisional_manifest, "checksum", "0" * 64)
    object.__setattr__(provisional_manifest, "walk_forward_execution_id", "0" * 64)
    payload = execution_manifest_payload(provisional_manifest)
    manifest = WalkForwardExecutionManifest(
        **manifest_values,
        checksum=document_checksum(payload),
        walk_forward_execution_id=deterministic_id(
            "adt-walk-forward-execution-v1",
            payload,
        ),
    )
    return plan, manifest


def _report(
    *,
    validation_scores: tuple[tuple[str, str], ...] = (("0.1", "0.05"), ("0.08", "0.04")),
    test_scores: tuple[str, ...] = ("0.11", "0.09"),
    fail_fold: int | None = None,
    policy: StabilityAnalysisPolicy | None = None,
) -> tuple[WalkForwardPlan, WalkForwardExecutionManifest, StabilityReport]:
    plan, execution = _execution(
        validation_scores=validation_scores,
        test_scores=test_scores,
        fail_fold=fail_fold,
    )
    report = StabilityAnalysisService(source_validator=_source_validator).analyze(
        plan,
        execution,
        policy or _policy(minimum_completion_ratio="0.5" if fail_fold is not None else "1"),
    )
    return plan, execution, report


def _resign_report(report: StabilityReport) -> None:
    payload = stability_report_payload(report)
    object.__setattr__(report, "checksum", document_checksum(payload))
    object.__setattr__(
        report,
        "stability_report_id",
        deterministic_id("adt-stability-report-v1", payload),
    )


def _resign_execution(execution: WalkForwardExecutionManifest, fold_index: int) -> None:
    fold = execution.folds[fold_index]
    object.__setattr__(fold, "checksum", document_checksum(fold_result_payload(fold)))
    payload = execution_manifest_payload(execution)
    object.__setattr__(execution, "checksum", document_checksum(payload))
    object.__setattr__(
        execution,
        "walk_forward_execution_id",
        deterministic_id("adt-walk-forward-execution-v1", payload),
    )


def test_stability_report_passes_explicit_controls() -> None:
    _plan_value, _execution_value, report = _report()

    assert report.assessment is StabilityAssessment.PASSED
    assert report.overfitting_assessment is OverfittingAssessment.NO_SIGNAL
    assert report.parameter_stability_assessment is ParameterStabilityAssessment.STABLE
    assert report.completed_count == 2
    assert report.test_not_worse_ratio.numerator == 2
    assert report.parameter_switch_count == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum_completed_folds", True),
        ("minimum_completed_folds", 1),
        ("minimum_completion_ratio", Decimal("1.1")),
        ("minimum_test_not_worse_ratio", Decimal("NaN")),
        ("maximum_median_degradation", Decimal("-0.1")),
        ("maximum_parameter_turnover_ratio", Decimal("1.1")),
    ],
)
def test_policy_rejects_invalid_exact_values(field: str, value: object) -> None:
    values = {
        "metric": ComparisonMetric.TOTAL_RETURN,
        "direction": WalkForwardSelectionDirection.MAXIMIZE,
        "minimum_completed_folds": 2,
        "minimum_completion_ratio": Decimal("1"),
        "minimum_test_not_worse_ratio": Decimal("0.5"),
        "maximum_median_degradation": Decimal("0.05"),
        "maximum_worst_degradation": Decimal("0.1"),
        "maximum_parameter_turnover_ratio": Decimal("0.5"),
    }
    values[field] = value
    with pytest.raises(
        (
            InvalidStabilityPolicyError,
            InvalidStabilityMetricError,
            IncompatibleStabilityReportError,
        )
    ):
        StabilityAnalysisPolicy(**values)  # type: ignore[arg-type]


def test_worse_holdouts_produce_possible_overfitting_signal() -> None:
    _plan_value, _execution_value, report = _report(
        test_scores=("0.01", "0.01"),
    )

    assert report.assessment is StabilityAssessment.FAILED
    assert report.overfitting_assessment is OverfittingAssessment.POSSIBLE_OVERFITTING
    assert report.degradation_distribution is not None
    assert report.degradation_distribution.maximum == Decimal("0.09")


def test_failed_fold_is_visible_and_can_make_data_insufficient() -> None:
    _plan_value, _execution_value, report = _report(fail_fold=1)

    assert report.completed_count == 1
    assert report.failed_count == 1
    assert report.assessment is StabilityAssessment.INSUFFICIENT_DATA
    assert report.observations[1].source_status is WalkForwardFoldStatus.FAILED_EXECUTION
    assert report.observations[1].test_score is None


def test_parameter_turnover_is_measured_between_completed_folds() -> None:
    _plan_value, _execution_value, report = _report(
        validation_scores=(("0.1", "0.05"), ("0.04", "0.08")),
        test_scores=("0.11", "0.09"),
        policy=_policy(maximum_parameter_turnover_ratio="0"),
    )

    assert report.parameter_transition_count == 1
    assert report.parameter_switch_count == 1
    assert report.parameter_stability_assessment is ParameterStabilityAssessment.UNSTABLE
    assert report.assessment is StabilityAssessment.FAILED


def test_policy_must_match_walk_forward_selection_metric_and_direction() -> None:
    plan, execution = _execution()
    mismatched = _policy(direction=WalkForwardSelectionDirection.MINIMIZE)

    with pytest.raises(IncompatibleStabilitySourceError):
        StabilityAnalysisService(source_validator=_source_validator).analyze(
            plan,
            execution,
            mismatched,
        )


def test_source_validator_is_mandatory_and_must_return_same_manifest() -> None:
    plan, execution = _execution()
    with pytest.raises(ValueError):
        StabilityAnalysisService(source_validator=None)  # type: ignore[arg-type]

    def hostile(_value: WalkForwardExecutionManifest) -> WalkForwardExecutionManifest:
        changed = deepcopy(execution)
        object.__setattr__(changed, "checksum", "f" * 64)
        return changed

    with pytest.raises(IncompatibleStabilitySourceError):
        StabilityAnalysisService(source_validator=hostile).analyze(
            plan,
            execution,
            _policy(),
        )


def test_source_validator_cannot_mutate_and_resign_the_input_manifest() -> None:
    plan, execution = _execution()
    validator_reached_valid_manifest = False

    def hostile(value: WalkForwardExecutionManifest) -> WalkForwardExecutionManifest:
        nonlocal validator_reached_valid_manifest
        object.__setattr__(
            value,
            "selection_policy",
            WalkForwardSelectionPolicy(
                metric=ComparisonMetric.TOTAL_RETURN,
                direction=WalkForwardSelectionDirection.MINIMIZE,
            ),
        )
        payload = execution_manifest_payload(value)
        object.__setattr__(value, "checksum", document_checksum(payload))
        object.__setattr__(
            value,
            "walk_forward_execution_id",
            deterministic_id("adt-walk-forward-execution-v1", payload),
        )
        validate_walk_forward_execution_manifest(value)
        validator_reached_valid_manifest = True
        return value

    with pytest.raises(IncompatibleStabilitySourceError):
        StabilityAnalysisService(source_validator=hostile).analyze(
            plan,
            execution,
            _policy(),
        )
    assert validator_reached_valid_manifest


def test_invalid_source_contract_is_mapped_to_stability_error() -> None:
    plan, execution = _execution()
    object.__setattr__(execution, "status", "FAILED")

    with pytest.raises(IncompatibleStabilitySourceError):
        StabilityAnalysisService(source_validator=_source_validator).analyze(
            plan,
            execution,
            _policy(),
        )


def test_minimum_completed_folds_cannot_exceed_source_fold_count() -> None:
    plan, execution = _execution()

    with pytest.raises(InvalidStabilityPolicyError):
        StabilityAnalysisService(source_validator=_source_validator).analyze(
            plan,
            execution,
            _policy(minimum_completed_folds=3),
        )


def test_payload_helper_rejects_hostile_control_without_attribute_leak() -> None:
    _plan_value, _execution_value, report = _report()
    object.__setattr__(report, "controls", (object(),))

    with pytest.raises(IncompatibleStabilityReportError):
        stability_report_payload(report)


def test_selected_holdout_metric_is_required() -> None:
    plan, execution = _execution()
    corrupted = deepcopy(execution)
    first = corrupted.folds[0]
    assert first.holdout is not None
    object.__setattr__(first.holdout, "metrics", (("net_profit", Decimal("1")),))
    _resign_execution(corrupted, 0)

    with pytest.raises(InvalidStabilityMetricError):
        StabilityAnalysisService(source_validator=_source_validator).analyze(
            plan,
            corrupted,
            _policy(),
        )


def test_canonical_document_round_trip() -> None:
    _plan_value, _execution_value, report = _report()
    document = stability_report_to_document(report)

    assert decode_stability_report_document(document) == report
    assert canonical_stability_report_bytes(report) == canonical_stability_report_bytes(
        decode_stability_report_document(document)
    )


@pytest.mark.parametrize("mutation", ["missing", "extra", "unknown_enum", "float"])
def test_document_rejects_noncanonical_values(mutation: str) -> None:
    _plan_value, _execution_value, report = _report()
    document = deepcopy(stability_report_to_document(report))
    payload = document["stability_report"]
    assert isinstance(payload, dict)
    if mutation == "missing":
        payload.pop("assessment")
    elif mutation == "extra":
        payload["unexpected"] = True
    elif mutation == "unknown_enum":
        payload["assessment"] = "MAYBE"
    else:
        policy = payload["policy"]
        assert isinstance(policy, dict)
        policy["minimum_completion_ratio"] = 0.5

    with pytest.raises(IncompatibleStabilityDocumentError):
        decode_stability_report_document(document)


def test_resigned_internal_degradation_tampering_is_rejected() -> None:
    _plan_value, _execution_value, report = _report()
    forged = deepcopy(report)
    observation = forged.observations[0]
    object.__setattr__(observation, "degradation", Decimal("0"))
    values = observation_values_payload(
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
    object.__setattr__(observation, "checksum", document_checksum(values))
    object.__setattr__(
        observation,
        "observation_id",
        deterministic_id("adt-stability-observation-v1", values),
    )
    _resign_report(forged)

    with pytest.raises(IncompatibleStabilityReportError):
        stability_report_to_document(forged)


def test_fully_resigned_report_still_must_match_walk_forward_source() -> None:
    plan, execution, report = _report()
    forged = deepcopy(report)
    observation = forged.observations[0]
    object.__setattr__(observation, "test_score", Decimal("0.50"))
    object.__setattr__(observation, "degradation", Decimal("-0.40"))
    object.__setattr__(observation, "test_not_worse", True)
    values = observation_values_payload(
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
    object.__setattr__(observation, "checksum", document_checksum(values))
    object.__setattr__(
        observation,
        "observation_id",
        deterministic_id("adt-stability-observation-v1", values),
    )
    # Rebuilding all aggregate values is intentionally omitted: internal validation rejects first.
    _resign_report(forged)

    with pytest.raises(IncompatibleStabilityReportError):
        validate_stability_report_against_walk_forward(
            forged,
            plan,
            execution,
            source_validator=_source_validator,
        )


def test_repository_publish_read_reuse_and_public_verification(tmp_path: Path) -> None:
    plan, execution, report = _report()
    repository = StabilityReportRepository(tmp_path)

    def semantic(value: StabilityReport) -> StabilityReport:
        return validate_stability_report_against_walk_forward(
            value,
            plan,
            execution,
            source_validator=_source_validator,
        )

    first = repository.publish(report, semantic_validator=semantic)
    second = repository.publish(report, semantic_validator=semantic)

    assert not first.reused
    assert second.reused
    assert (
        repository.read(execution.walk_forward_execution_id, report.stability_report_id) == report
    )
    assert (
        verify_published_stability_report(
            repository,
            report.stability_report_id,
            plan,
            execution,
            source_validator=_source_validator,
        )
        == report
    )


def test_repository_requires_semantic_validation(tmp_path: Path) -> None:
    _plan_value, _execution_value, report = _report()

    with pytest.raises(StabilityPublicationError):
        StabilityReportRepository(tmp_path).publish(report)


def test_repository_rejects_validator_that_mutates_and_resigns_report(
    tmp_path: Path,
) -> None:
    plan, execution, report = _report()
    repository = StabilityReportRepository(tmp_path)
    replacement = StabilityAnalysisService(source_validator=_source_validator).analyze(
        plan,
        execution,
        _policy(maximum_worst_degradation="0.2"),
    )

    def hostile(value: StabilityReport) -> StabilityReport:
        for field in fields(StabilityReport):
            object.__setattr__(value, field.name, getattr(replacement, field.name))
        return value

    with pytest.raises(StabilityPublicationError):
        repository.publish(report, semantic_validator=hostile)

    assert not repository.root.exists()


def test_repository_recovers_corrupt_target(tmp_path: Path) -> None:
    plan, execution, report = _report()
    repository = StabilityReportRepository(tmp_path)

    def semantic(value: StabilityReport) -> StabilityReport:
        return validate_stability_report_against_walk_forward(
            value,
            plan,
            execution,
            source_validator=_source_validator,
        )

    publication = repository.publish(report, semantic_validator=semantic)
    target = tmp_path / "market" / publication.relative_path
    (target / "report.json").write_text("{", encoding="utf-8")

    retried = repository.publish(report, semantic_validator=semantic)

    assert not retried.reused
    assert (
        repository.read(execution.walk_forward_execution_id, report.stability_report_id) == report
    )
    assert not tuple(target.parent.glob(".*.tmp-*"))


def test_repository_directory_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        StabilityReportRepository(tmp_path, directory=Path("../escape"))


def test_report_size_preflight_has_a_proven_bounded_ceiling() -> None:
    assert maximum_stability_report_bytes(1_000) < MAX_STABILITY_REPORT_BYTES
    with pytest.raises(IncompatibleStabilityReportError):
        maximum_stability_report_bytes(True)  # type: ignore[arg-type]


def test_report_contains_no_production_recommendation_or_global_strategy_rank() -> None:
    _plan_value, _execution_value, report = _report()
    encoded = canonical_stability_report_bytes(report).decode("utf-8")

    assert "production_recommendation" not in encoded
    assert "global_strategy_rank" not in encoded
    assert "paper_trading" not in encoded


def test_policy_change_changes_report_identity() -> None:
    plan, execution = _execution()
    service = StabilityAnalysisService(source_validator=_source_validator)
    first = service.analyze(plan, execution, _policy())  # type: ignore[arg-type]
    second = service.analyze(
        plan,
        execution,
        _policy(maximum_worst_degradation="0.2"),
    )

    assert first.stability_report_id != second.stability_report_id
    assert first.checksum != second.checksum


def test_report_json_is_small_and_canonical() -> None:
    _plan_value, _execution_value, report = _report()
    encoded = canonical_stability_report_bytes(report)

    assert len(encoded) < 64 * 1024
    assert json.loads(encoded.decode("utf-8"))["stability_report_id"] == report.stability_report_id
