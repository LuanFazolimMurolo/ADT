"""Phase 4-05 deterministic rolling walk-forward tests."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.reports import ComparisonMetric
from app.backtesting.serialization import read_json_envelope
from app.market_data.datasets import DatasetManifest, DatasetSnapshot
from app.optimization import (
    FoldSelectionEvidence,
    IncompatibleWalkForwardDocumentError,
    IncompatibleWalkForwardExecutionError,
    IncompatibleWalkForwardSelectionError,
    InsufficientWalkForwardFoldsError,
    InvalidWalkForwardCandidateError,
    InvalidWalkForwardHoldoutError,
    InvalidWalkForwardWindowPolicyError,
    PlannedRunExecutionStatus,
    SelectedHoldoutResult,
    SelectionCandidateEvidence,
    SelectionCandidateStatus,
    SelectionEvidenceStatus,
    SelectionExecutionReference,
    WalkForwardExecutionManifest,
    WalkForwardExecutionService,
    WalkForwardExecutionStatus,
    WalkForwardFailure,
    WalkForwardFoldPlan,
    WalkForwardFoldStatus,
    WalkForwardLimitExceededError,
    WalkForwardPlan,
    WalkForwardPlanningService,
    WalkForwardPublicationError,
    WalkForwardRepository,
    WalkForwardSelectionDirection,
    WalkForwardSelectionPolicy,
    WalkForwardSelectionService,
    WalkForwardWindowPolicy,
    build_experiment_execution_manifest,
    build_planned_run_execution,
    canonical_walk_forward_plan_bytes,
    decode_walk_forward_plan_document,
    validate_selection_decision_against_evidence,
    validate_walk_forward_execution_manifest_against_plan,
    validate_walk_forward_plan,
    verify_published_walk_forward_execution,
    walk_forward_plan_to_document,
)
from app.optimization.canonical import (
    canonical_json_bytes,
    decimal_text,
    deterministic_id,
    document_checksum,
)
from app.optimization.errors import (
    IncompatibleExperimentPluginError,
    NoEligibleWalkForwardCandidateError,
)
from app.optimization.experiment_execution_domain import (
    ExperimentExecutionFailure,
    ExperimentExecutionManifest,
    PlannedRunExecution,
)
from app.optimization.walk_forward_documents import (
    decode_walk_forward_execution_document,
    walk_forward_execution_to_document,
)
from app.optimization.walk_forward_domain import (
    execution_manifest_payload,
    fold_result_payload,
    selection_decision_payload,
    selection_evidence_values_payload,
    validate_selection_policy,
    validate_window_policy,
    walk_forward_plan_payload,
)
from tests.test_optimization_experiment_execution import _service
from tests.test_optimization_experiment_planning import _configuration, _contracts, _space


def _policy(
    *,
    train: int = 4,
    validation: int = 2,
    test: int = 2,
    warmup: int = 1,
    max_folds: int = 50,
) -> WalkForwardWindowPolicy:
    return WalkForwardWindowPolicy(train, validation, test, warmup, max_folds)


def _selection(
    direction: WalkForwardSelectionDirection = WalkForwardSelectionDirection.MAXIMIZE,
    metric: ComparisonMetric = ComparisonMetric.TOTAL_RETURN,
) -> WalkForwardSelectionPolicy:
    return WalkForwardSelectionPolicy(metric, direction)


def _plan(
    *,
    policy: WalkForwardWindowPolicy | None = None,
    selection: WalkForwardSelectionPolicy | None = None,
    values: tuple[int, ...] = (2, 3),
    plugin_version: str = "2",
    max_total_specs: int = 30_000,
) -> tuple[WalkForwardPlan, DatasetSnapshot, DatasetManifest]:
    snapshot, manifest = _contracts(snapshot_candles=12)
    plan = WalkForwardPlanningService().create(
        snapshot,
        manifest,
        _space(values=values, plugin_version=plugin_version),
        plugin_name="ema-cross-example",
        plugin_version=plugin_version,
        backtest_configuration=_configuration(),
        window_policy=policy or _policy(warmup=0 if plugin_version == "1" else 1),
        selection_policy=selection or _selection(),
        max_total_specs=max_total_specs,
    )
    return plan, snapshot, manifest


def _execution(
    fold: WalkForwardFoldPlan,
    scores: tuple[object, ...],
    *,
    failed_test: int | None = None,
) -> tuple[ExperimentExecutionManifest, Callable[[str], dict[str, object]]]:
    records: list[PlannedRunExecution] = []
    for spec in fold.experiment_plan.run_specs:
        if failed_test == spec.combination.index and spec.segment.index == 2:
            record = build_planned_run_execution(
                run_spec_id=spec.run_spec_id,
                experiment_id=spec.experiment_id,
                global_index=spec.global_index,
                combination_index=spec.combination.index,
                combination_id=spec.combination.combination_id,
                segment_index=spec.segment.index,
                segment_id=spec.segment.segment_id,
                purpose=spec.purpose,
                status=PlannedRunExecutionStatus.FAILED,
                error=ExperimentExecutionFailure("test_failed", "TEST failed"),
            )
        else:
            run_id = f"{spec.global_index + 1:064x}"
            record = build_planned_run_execution(
                run_spec_id=spec.run_spec_id,
                experiment_id=spec.experiment_id,
                global_index=spec.global_index,
                combination_index=spec.combination.index,
                combination_id=spec.combination.combination_id,
                segment_index=spec.segment.index,
                segment_id=spec.segment.segment_id,
                purpose=spec.purpose,
                status=PlannedRunExecutionStatus.COMPLETED,
                run_id=run_id,
                logical_result_checksum=f"{spec.global_index + 100:064x}",
                artifact_path=f"backtests/{run_id}",
                verified=True,
            )
        records.append(record)
    manifest = build_experiment_execution_manifest(
        experiment_id=fold.experiment_plan.experiment_id,
        plan_checksum=fold.experiment_plan.checksum,
        plan_schema_version=fold.experiment_plan.schema_version,
        ordering_policy=fold.experiment_plan.ordering_policy,
        records=tuple(records),
    )
    metric_by_run = {
        records[index * 3 + 1].run_id: scores[index]
        for index in range(len(fold.experiment_plan.combinations))
    }

    def load(run_id: str) -> dict[str, object]:
        return {"metrics": {"total_return": metric_by_run[run_id]}}

    return manifest, load


def _evidence(
    fold: WalkForwardFoldPlan,
    scores: tuple[object, ...],
    *,
    rejected: set[int] | None = None,
    policy: WalkForwardSelectionPolicy | None = None,
) -> FoldSelectionEvidence:
    rejected = set() if rejected is None else rejected
    policy = _selection() if policy is None else policy
    candidates: list[SelectionCandidateEvidence] = []
    for combination, raw in zip(fold.experiment_plan.combinations, scores, strict=True):
        run_base = combination.index * 3
        train_run = f"{run_base + 1:064x}"
        validation_run = f"{run_base + 2:064x}"
        reason: str | None = "train_not_verified" if combination.index in rejected else None
        score: Decimal | None = None
        if reason is None:
            if isinstance(raw, str):
                try:
                    parsed = Decimal(raw)
                except Exception:
                    parsed = Decimal("NaN")
                if parsed.is_finite() and decimal_text(parsed) == raw:
                    score = parsed
                else:
                    reason = "invalid_walk_forward_metric"
            else:
                reason = "invalid_walk_forward_metric"
        train = SelectionExecutionReference(
            fold.experiment_plan.run_specs[run_base].run_spec_id,
            train_run,
            f"{run_base + 100:064x}",
            f"backtests/{train_run}",
            SelectionEvidenceStatus.VERIFIED_SUCCESS,
        )
        validation = SelectionExecutionReference(
            fold.experiment_plan.run_specs[run_base + 1].run_spec_id,
            validation_run,
            f"{run_base + 101:064x}",
            f"backtests/{validation_run}",
            SelectionEvidenceStatus.VERIFIED_SUCCESS,
        )
        candidates.append(
            SelectionCandidateEvidence(
                combination_index=combination.index,
                combination_id=combination.combination_id,
                parameters=combination.parameters,
                status=(
                    SelectionCandidateStatus.ELIGIBLE
                    if reason is None
                    else SelectionCandidateStatus.REJECTED
                ),
                rejection_reason=reason,
                train=train,
                validation=validation,
                validation_metric=ComparisonMetric.TOTAL_RETURN,
                validation_score=score,
            )
        )
    typed = tuple(candidates)
    eligible = sum(item.status is SelectionCandidateStatus.ELIGIBLE for item in typed)
    values = selection_evidence_values_payload(
        fold_id=fold.fold_id,
        fold_index=fold.fold_index,
        experiment_id=fold.experiment_plan.experiment_id,
        policy=policy,
        candidates=typed,
        eligible_count=eligible,
        rejected_count=len(typed) - eligible,
    )
    return FoldSelectionEvidence(
        fold_id=fold.fold_id,
        fold_index=fold.fold_index,
        experiment_id=fold.experiment_plan.experiment_id,
        policy=policy,
        candidates=typed,
        eligible_count=eligible,
        rejected_count=len(typed) - eligible,
        checksum=document_checksum(values),
        selection_evidence_id=deterministic_id("adt-walk-forward-selection-evidence-v1", values),
    )


def _real_metric_loader(root: Path) -> Callable[[str], dict[str, object]]:
    def load(run_id: str) -> dict[str, object]:
        result = read_json_envelope(
            root / "market" / "backtests" / run_id / "result.json",
            "result",
        )
        assert isinstance(result, dict)
        return result

    return load


@pytest.fixture(scope="module")
def execution_document(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("walk-forward-document")
    plan, snapshot, manifest = _plan()
    experiment_service, _calls = _service(root)
    publication = WalkForwardExecutionService(
        root,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(root),
        contract_loader=lambda _plan: (snapshot, manifest),
    ).execute(plan)
    return walk_forward_execution_to_document(publication.manifest)


def test_valid_plan_has_two_chronological_folds_and_exact_cardinality() -> None:
    plan, _snapshot, _manifest = _plan()

    assert plan.fold_count == 2
    assert plan.specs_per_fold == plan.combination_count * 3 == 6
    assert plan.total_specs == 12
    assert plan.trailing_candles == 1
    assert [item.fold_index for item in plan.folds] == [0, 1]


def test_rolling_folds_advance_exactly_one_test_width() -> None:
    plan, _snapshot, _manifest = _plan()
    first, second = plan.folds

    assert (
        second.selected_coverage.start - first.selected_coverage.start
        == first.temporal_plan.segments[2].duration
    )
    assert first.temporal_plan.segments[2].end == second.temporal_plan.segments[2].start


def test_multiple_folds_discard_only_trailing_incomplete_candles() -> None:
    plan, _snapshot, _manifest = _plan(policy=_policy(train=2, validation=1, test=2))

    assert plan.fold_count == 4
    assert plan.trailing_candles == 0
    assert all(
        left.temporal_plan.segments[2].end == right.temporal_plan.segments[2].start
        for left, right in zip(plan.folds, plan.folds[1:], strict=False)
    )


@pytest.mark.parametrize("field", ["train", "validation", "test"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_window_policy_rejects_non_positive_or_non_exact_counts(field: str, value: object) -> None:
    values: dict[str, object] = {"train": 4, "validation": 2, "test": 2}
    values[field] = value
    with pytest.raises(InvalidWalkForwardWindowPolicyError):
        _policy(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-1, True, 1.5, 10_000_001])
def test_window_policy_rejects_invalid_warmup(value: object) -> None:
    with pytest.raises(InvalidWalkForwardWindowPolicyError):
        _policy(warmup=value)  # type: ignore[arg-type]


def test_walk_forward_requires_at_least_two_complete_folds() -> None:
    with pytest.raises(InsufficientWalkForwardFoldsError):
        _plan(policy=_policy(train=6, validation=3, test=3, warmup=0))


def test_fold_limit_is_enforced_without_truncation() -> None:
    with pytest.raises(WalkForwardLimitExceededError):
        _plan(policy=_policy(train=2, validation=1, test=2, max_folds=2))


def test_global_spec_limit_is_preflighted() -> None:
    with pytest.raises(WalkForwardLimitExceededError):
        _plan(max_total_specs=11)


def test_lifecycle_one_rejects_positive_walk_forward_warmup() -> None:
    snapshot, manifest = _contracts(snapshot_candles=12)
    space = _space(plugin_version="1")

    with pytest.raises(IncompatibleExperimentPluginError):
        WalkForwardPlanningService().create(
            snapshot,
            manifest,
            space,
            plugin_name="ema-cross-example",
            plugin_version="1",
            backtest_configuration=_configuration(),
            window_policy=_policy(warmup=1),
            selection_policy=_selection(),
        )


def test_lifecycle_one_without_warmup_and_lifecycle_two_with_warmup_are_valid() -> None:
    legacy, _snapshot, _manifest = _plan(plugin_version="1")
    current, _snapshot, _manifest = _plan(plugin_version="2")

    assert legacy.plugin.lifecycle_version == 1
    assert legacy.window_policy.warmup_candles == 0
    assert current.plugin.lifecycle_version == 2
    assert current.window_policy.warmup_candles == 1


def test_plan_and_fold_ids_are_stable_and_semantic() -> None:
    first, _snapshot, _manifest = _plan()
    second, _snapshot, _manifest = _plan()
    changed, _snapshot, _manifest = _plan(
        selection=_selection(WalkForwardSelectionDirection.MINIMIZE)
    )

    assert first == second
    assert first.walk_forward_plan_id == second.walk_forward_plan_id
    assert [item.fold_id for item in first.folds] == [item.fold_id for item in second.folds]
    assert changed.walk_forward_plan_id != first.walk_forward_plan_id


def test_plan_document_is_canonical_strict_and_round_trips() -> None:
    plan, _snapshot, _manifest = _plan()
    document = walk_forward_plan_to_document(plan)

    assert canonical_walk_forward_plan_bytes(plan) == canonical_json_bytes(document)
    assert decode_walk_forward_plan_document(document) == plan


@pytest.mark.parametrize("mutation", ["missing", "extra", "checksum", "fold_order"])
def test_plan_document_rejects_structural_and_identity_tampering(mutation: str) -> None:
    plan, _snapshot, _manifest = _plan()
    document = deepcopy(walk_forward_plan_to_document(plan))
    payload = document["walk_forward_plan"]
    assert isinstance(payload, dict)
    if mutation == "missing":
        payload.pop("selection_policy")
    elif mutation == "extra":
        payload["future"] = True
    elif mutation == "checksum":
        document["checksum"] = "0" * 64
    else:
        folds = payload["folds"]
        assert isinstance(folds, list)
        folds.reverse()

    with pytest.raises(Exception):
        decode_walk_forward_plan_document(document)


def test_plan_contains_no_float_and_contracts_are_frozen() -> None:
    plan, _snapshot, _manifest = _plan()
    document = walk_forward_plan_to_document(plan)

    def visit(value: object) -> None:
        assert not isinstance(value, float)
        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(document)
    with pytest.raises(FrozenInstanceError):
        plan.fold_count = 10  # type: ignore[misc]


@pytest.mark.parametrize(
    ("direction", "scores", "winner"),
    [
        (WalkForwardSelectionDirection.MAXIMIZE, ("1", "2"), 1),
        (WalkForwardSelectionDirection.MINIMIZE, ("1", "2"), 0),
        (WalkForwardSelectionDirection.MAXIMIZE, ("2", "2"), 0),
    ],
)
def test_selection_uses_validation_direction_and_deterministic_tie_break(
    direction: WalkForwardSelectionDirection,
    scores: tuple[str, str],
    winner: int,
) -> None:
    plan, _snapshot, _manifest = _plan(selection=_selection(direction))
    evidence = _evidence(plan.folds[0], scores, policy=plan.selection_policy)
    decision = WalkForwardSelectionService().select(evidence)

    assert decision.combination_index == winner
    assert decision.rank == 1
    assert decision.score == Decimal(scores[winner])


@pytest.mark.parametrize("raw", [None, True, 1, 1.5, "NaN", "Infinity", "1.0", " 1"])
def test_invalid_or_missing_validation_metrics_reject_candidates(raw: object) -> None:
    plan, _snapshot, _manifest = _plan(values=(2,))
    evidence = _evidence(plan.folds[0], (raw,), policy=plan.selection_policy)

    with pytest.raises(NoEligibleWalkForwardCandidateError):
        WalkForwardSelectionService().select(evidence)


def test_selection_projection_never_loads_test_or_uses_test_status() -> None:
    plan, _snapshot, _manifest = _plan()
    evidence = _evidence(plan.folds[0], ("2", "1"), policy=plan.selection_policy)
    decision = WalkForwardSelectionService().select(evidence)

    assert decision.combination_index == 0
    assert not hasattr(evidence, "test")
    assert not any(hasattr(item, "test") for item in evidence.candidates)


def test_train_or_validation_failure_makes_only_that_candidate_ineligible() -> None:
    plan, _snapshot, _manifest = _plan()
    evidence = _evidence(
        plan.folds[0],
        ("5", "1"),
        rejected={0},
        policy=plan.selection_policy,
    )
    decision = WalkForwardSelectionService().select(evidence)

    assert decision.combination_index == 1
    assert decision.eligible_count == 1
    assert decision.rejected_count == 1


def test_non_winner_test_changes_do_not_change_selection_identity() -> None:
    plan, _snapshot, _manifest = _plan()
    first_evidence = _evidence(plan.folds[0], ("2", "1"), policy=plan.selection_policy)
    second_evidence = _evidence(plan.folds[0], ("2", "1"), policy=plan.selection_policy)
    first_decision = WalkForwardSelectionService().select(first_evidence)
    second_decision = WalkForwardSelectionService().select(second_evidence)

    assert first_decision.selection_id == second_decision.selection_id
    assert first_decision.checksum == second_decision.checksum


def test_execution_completes_folds_and_repository_round_trips(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, calls = _service(tmp_path)
    service = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(tmp_path),
        contract_loader=lambda _plan: (snapshot, manifest),
    )

    publication = service.execute(plan)
    decoded = decode_walk_forward_execution_document(
        walk_forward_execution_to_document(publication.manifest)
    )

    assert publication.manifest.status is WalkForwardExecutionStatus.COMPLETED
    assert all(
        item.status is WalkForwardFoldStatus.COMPLETED for item in publication.manifest.folds
    )
    assert decoded == publication.manifest
    assert len(calls) <= plan.total_specs
    assert (
        WalkForwardRepository(tmp_path).read(
            plan.walk_forward_plan_id,
            publication.manifest.walk_forward_execution_id,
        )
        == publication.manifest
    )


def test_rerun_reuses_artifacts_and_preserves_selection_then_manifest(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, _calls = _service(tmp_path)
    service = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(tmp_path),
        contract_loader=lambda _plan: (snapshot, manifest),
    )

    first = service.execute(plan)
    second = service.execute(plan)
    third = service.execute(plan)

    assert [item.selection.selection_id for item in first.manifest.folds if item.selection] == [
        item.selection.selection_id for item in second.manifest.folds if item.selection
    ]
    assert third.reused is True


def test_winner_test_failure_keeps_selection_and_never_promotes_runner_up(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, _calls = _service(tmp_path, fail_calls={2})
    service = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(tmp_path),
        contract_loader=lambda _plan: (snapshot, manifest),
    )

    execution = service.execute(plan).manifest
    first = execution.folds[0]

    assert first.status is WalkForwardFoldStatus.FAILED_HOLDOUT
    assert first.selection is not None
    assert first.selection.combination_index == 0
    assert first.holdout is None
    assert execution.folds[1].fold_index == 1
    assert execution.status is WalkForwardExecutionStatus.PARTIALLY_FAILED


def test_all_fold_execution_failures_produce_failed_aggregate(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, _calls = _service(tmp_path, fail_calls=set(range(plan.total_specs)))
    service = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=lambda _run_id: pytest.fail("metrics must not be loaded"),
        contract_loader=lambda _plan: (snapshot, manifest),
    )

    execution = service.execute(plan).manifest

    assert execution.status is WalkForwardExecutionStatus.FAILED
    assert execution.completed_count == 0
    assert execution.failed_count == plan.fold_count
    assert all(
        item.status is WalkForwardFoldStatus.FAILED_NO_ELIGIBLE_CANDIDATE
        for item in execution.folds
    )


def test_execution_verification_frontier_precedes_metric_access(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, _calls = _service(tmp_path)

    def metrics(run_id: str) -> dict[str, object]:
        return _real_metric_loader(tmp_path)(run_id)

    execution = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=metrics,
        contract_loader=lambda _plan: (snapshot, manifest),
    ).execute(plan)

    assert all(
        selection.train.status is SelectionEvidenceStatus.VERIFIED_SUCCESS
        and selection.validation.status is SelectionEvidenceStatus.VERIFIED_SUCCESS
        for fold in execution.manifest.folds
        if (selection := fold.selection) is not None
    )
    assert execution.manifest.status is WalkForwardExecutionStatus.COMPLETED


def test_mutated_plan_fails_before_fold_executor_and_repository_writes(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, calls = _service(tmp_path)
    object.__setattr__(plan, "total_specs", plan.total_specs + 1)
    before = tuple(tmp_path.rglob("*"))
    service = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=lambda _run_id: pytest.fail("metrics must not be loaded"),
        contract_loader=lambda _plan: (snapshot, manifest),
    )

    with pytest.raises(Exception):
        service.execute(plan)

    assert calls == []
    assert tuple(tmp_path.rglob("*")) == before


def test_execution_document_rejects_checksum_and_unknown_field_tampering(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, _calls = _service(tmp_path)
    execution = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(tmp_path),
        contract_loader=lambda _plan: (snapshot, manifest),
    ).execute(plan)
    document = walk_forward_execution_to_document(execution.manifest)
    changed_checksum = deepcopy(document)
    changed_checksum["checksum"] = "0" * 64
    changed_shape = deepcopy(document)
    payload = changed_shape["walk_forward_execution"]
    assert isinstance(payload, dict)
    payload["global_score"] = "1"

    with pytest.raises(Exception):
        decode_walk_forward_execution_document(changed_checksum)
    with pytest.raises(IncompatibleWalkForwardDocumentError):
        decode_walk_forward_execution_document(changed_shape)


def test_selection_document_keys_contain_no_test_evidence() -> None:
    plan, _snapshot, _manifest = _plan()
    evidence = _evidence(plan.folds[0], ("2", "1"), policy=plan.selection_policy)
    decision = WalkForwardSelectionService().select(evidence)
    payload = selection_decision_payload(decision)

    def keys(value: object) -> list[str]:
        if isinstance(value, dict):
            return list(value) + [item for nested in value.values() for item in keys(nested)]
        if isinstance(value, list):
            return [item for nested in value for item in keys(nested)]
        return []

    assert all("test" not in key.lower() for key in keys(payload))


def test_repository_rejects_unsafe_path_and_invalid_manifest_before_writes(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WalkForwardRepository(tmp_path, directory=Path("../escape"))
    repository = WalkForwardRepository(tmp_path)
    before = tuple(tmp_path.rglob("*"))

    with pytest.raises(Exception):
        repository.publish(object())  # type: ignore[arg-type]

    assert tuple(tmp_path.rglob("*")) == before == ()


def test_repository_rejects_corrupt_target(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, _calls = _service(tmp_path)
    service = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(tmp_path),
        contract_loader=lambda _plan: (snapshot, manifest),
    )
    publication = service.execute(plan)
    target = tmp_path / "market" / publication.relative_path / "publication.json"
    target.write_text("[]", encoding="utf-8")

    with pytest.raises(IncompatibleWalkForwardDocumentError):
        WalkForwardRepository(tmp_path).read(
            plan.walk_forward_plan_id,
            publication.manifest.walk_forward_execution_id,
        )


def test_no_global_score_or_ranking_is_persisted(tmp_path: Path) -> None:
    plan, snapshot, manifest = _plan()
    experiment_service, _calls = _service(tmp_path)
    service = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(tmp_path),
        contract_loader=lambda _plan: (snapshot, manifest),
    )

    document = walk_forward_execution_to_document(service.execute(plan).manifest)
    encoded = json.dumps(document, sort_keys=True)

    assert "global_score" not in encoded
    assert "global_ranking" not in encoded
    assert "overfitting" not in encoded


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("train_candles", True),
        ("train_candles", 1.5),
        ("train_candles", 0),
        ("train_candles", -1),
        ("train_candles", 10_000_001),
        ("validation_candles", True),
        ("validation_candles", 1.5),
        ("validation_candles", 0),
        ("validation_candles", -1),
        ("validation_candles", 10_000_001),
        ("test_candles", True),
        ("test_candles", 1.5),
        ("test_candles", 0),
        ("test_candles", -1),
        ("test_candles", 10_000_001),
        ("warmup_candles", True),
        ("warmup_candles", 1.5),
        ("warmup_candles", -1),
        ("warmup_candles", 10_000_001),
        ("max_folds", True),
        ("max_folds", 1.5),
        ("max_folds", 1),
        ("max_folds", 1_001),
    ],
)
def test_mutated_window_policy_contract_is_rejected(field: str, value: object) -> None:
    policy = _policy()
    object.__setattr__(policy, field, value)

    with pytest.raises(Exception):
        validate_window_policy(policy)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metric", "total_return"),
        ("metric", object()),
        ("metric", None),
        ("metric", True),
        ("direction", "MAXIMIZE"),
        ("direction", object()),
        ("direction", None),
        ("tie_break", "COMBINATION_INDEX_THEN_ID"),
        ("tie_break", object()),
        ("schema_version", True),
        ("schema_version", 2),
    ],
)
def test_mutated_selection_policy_contract_is_rejected(field: str, value: object) -> None:
    policy = _selection()
    object.__setattr__(policy, field, value)

    with pytest.raises(Exception):
        validate_selection_policy(policy)


@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "snapshot",
        "window",
        "selection",
        "search",
        "plugin",
        "configuration",
        "empty_folds",
        "fold_count",
        "combination_count",
        "specs_per_fold",
        "total_specs",
        "trailing",
        "max_total",
        "ordering",
        "checksum",
        "identifier",
        "duplicate_fold",
        "missing_fold",
        "fold_checksum",
    ],
)
def test_plan_document_mutation_matrix_is_rejected(mutation: str) -> None:
    plan, _snapshot, _manifest = _plan()
    document = deepcopy(walk_forward_plan_to_document(plan))
    payload = document["walk_forward_plan"]
    assert isinstance(payload, dict)
    if mutation == "schema":
        payload["schema_version"] = 2
    elif mutation == "snapshot":
        payload["snapshot"] = None
    elif mutation == "window":
        payload["window_policy"] = None
    elif mutation == "selection":
        payload["selection_policy"] = None
    elif mutation == "search":
        payload["search_space"] = None
    elif mutation == "plugin":
        payload["plugin"] = None
    elif mutation == "configuration":
        payload["backtest_configuration"] = None
    elif mutation == "empty_folds":
        payload["folds"] = []
    elif mutation == "fold_count":
        payload["fold_count"] = 3
    elif mutation == "combination_count":
        payload["combination_count"] = 3
    elif mutation == "specs_per_fold":
        payload["specs_per_fold"] = 7
    elif mutation == "total_specs":
        payload["total_specs"] = 13
    elif mutation == "trailing":
        payload["trailing_candles"] = -1
    elif mutation == "max_total":
        payload["max_total_specs"] = 0
    elif mutation == "ordering":
        payload["ordering_policy"] = "HASH_ORDER"
    elif mutation == "checksum":
        document["checksum"] = "0" * 64
    elif mutation == "identifier":
        document["walk_forward_plan_id"] = "0" * 64
    else:
        folds = payload["folds"]
        assert isinstance(folds, list)
        if mutation == "duplicate_fold":
            folds[1] = deepcopy(folds[0])
        elif mutation == "missing_fold":
            folds.pop()
        else:
            fold = folds[0]
            assert isinstance(fold, dict)
            fold["checksum"] = "0" * 64

    with pytest.raises(Exception):
        decode_walk_forward_plan_document(document)


@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "plan_id",
        "plan_checksum",
        "snapshot",
        "window",
        "selection",
        "failure_policy",
        "ordering",
        "empty_folds",
        "fold_count",
        "completed_count",
        "failed_count",
        "status",
        "checksum",
        "identifier",
        "duplicate_fold",
        "missing_fold",
        "fold_checksum",
        "winner",
        "selection_id",
    ],
)
def test_execution_document_mutation_matrix_is_rejected(
    execution_document: dict[str, object],
    mutation: str,
) -> None:
    document = deepcopy(execution_document)
    payload = document["walk_forward_execution"]
    assert isinstance(payload, dict)
    if mutation == "schema":
        payload["schema_version"] = 2
    elif mutation == "plan_id":
        payload["walk_forward_plan_id"] = "0" * 64
    elif mutation == "plan_checksum":
        payload["plan_checksum"] = "0" * 64
    elif mutation == "snapshot":
        payload["snapshot"] = None
    elif mutation == "window":
        payload["window_policy"] = None
    elif mutation == "selection":
        payload["selection_policy"] = None
    elif mutation == "failure_policy":
        payload["failure_policy"] = "STOP"
    elif mutation == "ordering":
        payload["ordering_policy"] = "HASH_ORDER"
    elif mutation == "empty_folds":
        payload["folds"] = []
    elif mutation == "fold_count":
        payload["fold_count"] = 3
    elif mutation == "completed_count":
        payload["completed_count"] = 0
    elif mutation == "failed_count":
        payload["failed_count"] = 2
    elif mutation == "status":
        payload["status"] = "FAILED"
    elif mutation == "checksum":
        document["checksum"] = "0" * 64
    elif mutation == "identifier":
        document["walk_forward_execution_id"] = "0" * 64
    else:
        folds = payload["folds"]
        assert isinstance(folds, list)
        if mutation == "duplicate_fold":
            folds[1] = deepcopy(folds[0])
        elif mutation == "missing_fold":
            folds.pop()
        elif mutation == "fold_checksum":
            fold = folds[0]
            assert isinstance(fold, dict)
            fold["checksum"] = "0" * 64
        else:
            fold = folds[0]
            assert isinstance(fold, dict)
            result = fold["result"]
            assert isinstance(result, dict)
            selection = result["selection"]
            assert isinstance(selection, dict)
            if mutation == "winner":
                decision = selection["decision"]
                assert isinstance(decision, dict)
                decision["combination_index"] = 99
            else:
                selection["selection_id"] = "0" * 64

    with pytest.raises(Exception):
        decode_walk_forward_execution_document(document)


@pytest.mark.parametrize(
    "policy",
    [
        WalkForwardWindowPolicy(5, 1, 2, 1),
        WalkForwardWindowPolicy(4, 2, 2, 0),
    ],
)
def test_resigned_plan_rejects_policy_that_diverges_from_folds(
    policy: WalkForwardWindowPolicy,
) -> None:
    plan, _snapshot, _manifest = _plan()
    object.__setattr__(plan, "window_policy", policy)
    payload = walk_forward_plan_payload(plan)
    object.__setattr__(plan, "checksum", document_checksum(payload))
    object.__setattr__(
        plan,
        "walk_forward_plan_id",
        deterministic_id("adt-walk-forward-plan-v1", payload),
    )

    with pytest.raises(Exception):
        validate_walk_forward_plan(plan)


def test_resigned_plan_rejects_false_trailing_candle_count() -> None:
    plan, _snapshot, _manifest = _plan()
    object.__setattr__(plan, "trailing_candles", plan.trailing_candles + 1)
    payload = walk_forward_plan_payload(plan)
    object.__setattr__(plan, "checksum", document_checksum(payload))
    object.__setattr__(
        plan,
        "walk_forward_plan_id",
        deterministic_id("adt-walk-forward-plan-v1", payload),
    )

    with pytest.raises(Exception):
        validate_walk_forward_plan(plan)


def test_ranking_public_contract_accepts_only_fold_selection_evidence() -> None:
    parameters = tuple(inspect.signature(WalkForwardSelectionService.select).parameters)

    assert parameters == ("self", "evidence")


def test_resigned_arbitrary_winner_is_rejected_against_complete_evidence() -> None:
    plan, _snapshot, _manifest = _plan()
    evidence = _evidence(plan.folds[0], ("5", "1"), policy=plan.selection_policy)
    decision = WalkForwardSelectionService().select(evidence)
    other = evidence.candidates[1]
    assert other.train is not None and other.validation is not None
    object.__setattr__(decision, "combination_index", other.combination_index)
    object.__setattr__(decision, "combination_id", other.combination_id)
    object.__setattr__(decision, "parameters", other.parameters)
    object.__setattr__(decision, "score", other.validation_score)
    object.__setattr__(decision, "train", other.train)
    object.__setattr__(decision, "validation", other.validation)
    payload = selection_decision_payload(decision)
    object.__setattr__(decision, "checksum", document_checksum(payload))
    object.__setattr__(
        decision,
        "selection_id",
        deterministic_id("adt-walk-forward-selection-v1", payload),
    )

    with pytest.raises(IncompatibleWalkForwardSelectionError):
        validate_selection_decision_against_evidence(decision, evidence)


def test_any_validation_evidence_change_changes_evidence_checksum() -> None:
    plan, _snapshot, _manifest = _plan()
    original = _evidence(plan.folds[0], ("5", "1"), policy=plan.selection_policy)
    changed = _evidence(plan.folds[0], ("4", "1"), policy=plan.selection_policy)

    assert changed.checksum != original.checksum
    assert changed.selection_evidence_id != original.selection_evidence_id


@pytest.mark.parametrize(
    "path",
    [
        "/backtests/{run_id}",
        "backtests//{run_id}",
        "backtests/./{run_id}",
        "backtests/../{run_id}",
        "backtests\\{run_id}",
        "C:\\backtests\\{run_id}",
        "backtests/{run_id}/extra",
        "https://example.invalid/backtests/{run_id}",
    ],
)
def test_selection_reference_rejects_noncanonical_artifact_paths(path: str) -> None:
    run_id = "1" * 64
    with pytest.raises(InvalidWalkForwardCandidateError):
        SelectionExecutionReference(
            "2" * 64,
            run_id,
            "3" * 64,
            path.format(run_id=run_id),
            SelectionEvidenceStatus.VERIFIED_SUCCESS,
        )


def test_holdout_metric_tuple_shape_fails_with_walk_forward_error() -> None:
    with pytest.raises(InvalidWalkForwardHoldoutError):
        SelectedHoldoutResult(
            "1" * 64,
            "2" * 64,
            "3" * 64,
            f"backtests/{'2' * 64}",
            (("metric",),),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("corruption", ["publication", "manifest", "extra", "missing"])
def test_repository_recovers_only_corrupt_final_target(
    tmp_path: Path,
    corruption: str,
) -> None:
    plan, snapshot, dataset_manifest = _plan()
    experiment_service, _calls = _service(tmp_path)
    publication = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(tmp_path),
        contract_loader=lambda _plan: (snapshot, dataset_manifest),
    ).execute(plan)
    target = tmp_path / "market" / publication.relative_path
    if corruption == "publication":
        (target / "publication.json").write_text("{", encoding="utf-8")
    elif corruption == "manifest":
        (target / "manifest.json").write_text("{", encoding="utf-8")
    elif corruption == "extra":
        (target / "extra.json").write_text("{}", encoding="utf-8")
    else:
        (target / "manifest.json").unlink()

    metric_loader = _real_metric_loader(tmp_path)

    def semantic_validator(
        value: WalkForwardExecutionManifest,
    ) -> WalkForwardExecutionManifest:
        return validate_walk_forward_execution_manifest_against_plan(
            value,
            plan,
            execution_loader=experiment_service._repository.read,
            snapshot=snapshot,
            artifact_store=experiment_service.artifact_store,
            result_verifier=experiment_service.result_verifier,
            metric_loader=metric_loader,
        )

    retried = WalkForwardRepository(tmp_path).publish(
        publication.manifest,
        semantic_validator=semantic_validator,
    )

    assert not retried.reused
    assert (
        WalkForwardRepository(tmp_path).read(
            plan.walk_forward_plan_id,
            publication.manifest.walk_forward_execution_id,
        )
        == publication.manifest
    )
    assert not tuple(target.parent.glob(".*.tmp-*"))


def test_publication_verifier_rebuilds_evidence_and_selected_holdout(tmp_path: Path) -> None:
    plan, snapshot, dataset_manifest = _plan()
    experiment_service, _calls = _service(tmp_path)
    metric_loader = _real_metric_loader(tmp_path)
    publication = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=metric_loader,
        contract_loader=lambda _plan: (snapshot, dataset_manifest),
    ).execute(plan)

    verified = verify_published_walk_forward_execution(
        WalkForwardRepository(tmp_path),
        plan,
        publication.manifest.walk_forward_execution_id,
        execution_loader=experiment_service._repository.read,
        snapshot=snapshot,
        artifact_store=experiment_service._store,
        result_verifier=experiment_service._verifier,
        metric_loader=metric_loader,
    )

    assert verified == publication.manifest


@pytest.mark.parametrize("mutation", ["duplicate_id", "index_gap", "metric_mismatch"])
def test_resigned_selection_evidence_rejects_invalid_complete_candidate_set(
    mutation: str,
) -> None:
    plan, _snapshot, _manifest = _plan()
    evidence = deepcopy(_evidence(plan.folds[0], ("5", "1"), policy=plan.selection_policy))
    target = evidence.candidates[1]
    if mutation == "duplicate_id":
        object.__setattr__(target, "combination_id", evidence.candidates[0].combination_id)
    elif mutation == "index_gap":
        object.__setattr__(target, "combination_index", 2)
    else:
        other_metric = next(
            metric for metric in ComparisonMetric if metric is not evidence.policy.metric
        )
        object.__setattr__(target, "validation_metric", other_metric)
    values = selection_evidence_values_payload(
        fold_id=evidence.fold_id,
        fold_index=evidence.fold_index,
        experiment_id=evidence.experiment_id,
        policy=evidence.policy,
        candidates=evidence.candidates,
        eligible_count=evidence.eligible_count,
        rejected_count=evidence.rejected_count,
    )
    object.__setattr__(evidence, "checksum", document_checksum(values))
    object.__setattr__(
        evidence,
        "selection_evidence_id",
        deterministic_id("adt-walk-forward-selection-evidence-v1", values),
    )

    with pytest.raises(IncompatibleWalkForwardSelectionError):
        WalkForwardSelectionService().select(evidence)


def test_repository_requires_semantic_validation_before_reuse(tmp_path: Path) -> None:
    plan, snapshot, dataset_manifest = _plan()
    experiment_service, _calls = _service(tmp_path)
    publication = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=_real_metric_loader(tmp_path),
        contract_loader=lambda _plan: (snapshot, dataset_manifest),
    ).execute(plan)

    with pytest.raises(WalkForwardPublicationError):
        WalkForwardRepository(tmp_path).publish(publication.manifest)

    assert (
        WalkForwardRepository(tmp_path).read(
            plan.walk_forward_plan_id,
            publication.manifest.walk_forward_execution_id,
        )
        == publication.manifest
    )


def test_resigned_failed_holdout_requires_a_real_holdout_failure(tmp_path: Path) -> None:
    plan, snapshot, dataset_manifest = _plan()
    experiment_service, _calls = _service(tmp_path)
    metric_loader = _real_metric_loader(tmp_path)
    publication = WalkForwardExecutionService(
        tmp_path,
        experiment_execution_service=experiment_service,
        metric_loader=metric_loader,
        contract_loader=lambda _plan: (snapshot, dataset_manifest),
    ).execute(plan)
    forged = deepcopy(publication.manifest)
    first = forged.folds[0]
    object.__setattr__(first, "status", WalkForwardFoldStatus.FAILED_HOLDOUT)
    object.__setattr__(first, "holdout", None)
    object.__setattr__(
        first,
        "failure",
        WalkForwardFailure(
            "invalid_walk_forward_holdout",
            "O holdout TEST selecionado é inválido.",
        ),
    )
    object.__setattr__(first, "checksum", document_checksum(fold_result_payload(first)))
    object.__setattr__(forged, "completed_count", forged.completed_count - 1)
    object.__setattr__(forged, "failed_count", forged.failed_count + 1)
    object.__setattr__(forged, "status", WalkForwardExecutionStatus.PARTIALLY_FAILED)
    payload = execution_manifest_payload(forged)
    object.__setattr__(forged, "checksum", document_checksum(payload))
    object.__setattr__(
        forged,
        "walk_forward_execution_id",
        deterministic_id("adt-walk-forward-execution-v1", payload),
    )

    with pytest.raises(IncompatibleWalkForwardExecutionError):
        validate_walk_forward_execution_manifest_against_plan(
            forged,
            plan,
            execution_loader=experiment_service._repository.read,
            snapshot=snapshot,
            artifact_store=experiment_service.artifact_store,
            result_verifier=experiment_service.result_verifier,
            metric_loader=metric_loader,
        )


def test_rejected_candidate_cannot_reuse_train_as_validation_reference() -> None:
    plan, _snapshot, _manifest = _plan()
    evidence = _evidence(plan.folds[0], ("5", "1"), policy=plan.selection_policy)
    original = evidence.candidates[0]
    assert original.train is not None

    with pytest.raises(InvalidWalkForwardCandidateError):
        SelectionCandidateEvidence(
            combination_index=original.combination_index,
            combination_id=original.combination_id,
            parameters=original.parameters,
            status=SelectionCandidateStatus.REJECTED,
            rejection_reason="validation_metric_unavailable",
            train=original.train,
            validation=original.train,
            validation_metric=original.validation_metric,
            validation_score=None,
        )
