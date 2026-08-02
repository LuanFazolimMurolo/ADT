"""Sequential walk-forward execution and leakage-proof per-fold selection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.backtesting.artifacts import BacktestArtifactStore, build_run_id
from app.backtesting.query import BacktestRunReader
from app.backtesting.reports import ComparisonMetric
from app.backtesting.verifier import BacktestResultVerifier
from app.domain.errors import DomainError
from app.market_data.datasets import DatasetManifest, DatasetSnapshot
from app.market_data.snapshots import MarketDatasetReader
from app.optimization.canonical import decimal_text, deterministic_id, document_checksum
from app.optimization.errors import (
    IncompatibleWalkForwardExecutionError,
    InvalidWalkForwardCandidateError,
    InvalidWalkForwardHoldoutError,
    InvalidWalkForwardMetricError,
    MissingWalkForwardMetricError,
    NoEligibleWalkForwardCandidateError,
    WalkForwardError,
    WalkForwardLimitExceededError,
)
from app.optimization.experiment_domain import ExperimentRunPurpose, PlannedRunSpec
from app.optimization.experiment_execution import ExperimentExecutionService
from app.optimization.experiment_execution_domain import (
    ExperimentExecutionManifest,
    PlannedRunExecution,
    PlannedRunExecutionStatus,
    validate_execution_manifest_against_plan,
)
from app.optimization.experiment_execution_repository import ExperimentExecutionPublication
from app.optimization.walk_forward_domain import (
    MAX_HOLDOUT_METRIC_NAME_CHARACTERS,
    MAX_HOLDOUT_METRICS,
    MAX_WALK_FORWARD_ERROR_MESSAGE,
    MAX_WALK_FORWARD_MANIFEST_BYTES,
    FoldSelectionDecision,
    FoldSelectionEvidence,
    SelectedHoldoutResult,
    SelectionCandidateEvidence,
    SelectionCandidateStatus,
    SelectionEvidenceStatus,
    SelectionExecutionReference,
    WalkForwardExecutionManifest,
    WalkForwardExecutionStatus,
    WalkForwardFailure,
    WalkForwardFailurePolicy,
    WalkForwardFoldPlan,
    WalkForwardFoldResult,
    WalkForwardFoldStatus,
    WalkForwardPlan,
    WalkForwardSelectionDirection,
    WalkForwardSelectionPolicy,
    execution_manifest_payload,
    fold_result_payload,
    selection_decision_payload,
    selection_evidence_values_payload,
    validate_fold_selection_evidence,
    validate_selection_candidate,
    validate_selection_decision_against_evidence,
    validate_walk_forward_execution_manifest,
    validate_walk_forward_fold_plan,
    validate_walk_forward_plan,
)
from app.optimization.walk_forward_planning import (
    WalkForwardPlanningService,
    maximum_walk_forward_execution_bytes,
)
from app.optimization.walk_forward_repository import (
    WalkForwardExecutionPublication,
    WalkForwardRepository,
)

MetricLoader = Callable[[str], Mapping[str, object]]
ContractLoader = Callable[[WalkForwardPlan], tuple[DatasetSnapshot, DatasetManifest]]
FoldExecutor = Callable[[object], ExperimentExecutionPublication]
ExperimentExecutionLoader = Callable[[str, str], ExperimentExecutionManifest]


class WalkForwardSelectionService:
    """Rank projections containing TRAIN and VALIDATION evidence only."""

    def select(self, evidence: FoldSelectionEvidence) -> FoldSelectionDecision:
        validate_fold_selection_evidence(evidence)
        candidates = tuple(
            item for item in evidence.candidates if item.status is SelectionCandidateStatus.ELIGIBLE
        )
        if not candidates:
            raise NoEligibleWalkForwardCandidateError()
        reverse = evidence.policy.direction is WalkForwardSelectionDirection.MAXIMIZE
        score_order = sorted(
            candidates,
            key=_candidate_score,
            reverse=reverse,
        )
        best_score = _candidate_score(score_order[0])
        winner = min(
            (item for item in score_order if _candidate_score(item) == best_score),
            key=lambda item: (item.combination_index, item.combination_id),
        )
        if winner.train is None or winner.validation is None:
            raise InvalidWalkForwardCandidateError("winner evidence is incomplete")
        rejected_reasons = tuple(
            item.rejection_reason
            for item in evidence.candidates
            if item.status is SelectionCandidateStatus.REJECTED
        )
        compact = _compact_reasons(rejected_reasons)
        values = dict(
            fold_id=evidence.fold_id,
            fold_index=evidence.fold_index,
            policy=evidence.policy,
            combination_index=winner.combination_index,
            combination_id=winner.combination_id,
            parameters=winner.parameters,
            score=best_score,
            rank=1,
            eligible_count=evidence.eligible_count,
            rejected_count=evidence.rejected_count,
            rejection_reasons=compact,
            train=winner.train,
            validation=winner.validation,
            selection_evidence_id=evidence.selection_evidence_id,
            selection_evidence_checksum=evidence.checksum,
        )
        provisional = _decision_projection(**values)
        payload = selection_decision_payload(provisional)
        decision = FoldSelectionDecision(
            fold_id=evidence.fold_id,
            fold_index=evidence.fold_index,
            policy=evidence.policy,
            combination_index=winner.combination_index,
            combination_id=winner.combination_id,
            parameters=winner.parameters,
            score=best_score,
            rank=1,
            eligible_count=evidence.eligible_count,
            rejected_count=evidence.rejected_count,
            rejection_reasons=compact,
            train=winner.train,
            validation=winner.validation,
            selection_evidence_id=evidence.selection_evidence_id,
            selection_evidence_checksum=evidence.checksum,
            checksum=document_checksum(payload),
            selection_id=deterministic_id("adt-walk-forward-selection-v1", payload),
        )
        validate_selection_decision_against_evidence(decision, evidence)
        return decision


def build_fold_selection_evidence(
    fold: WalkForwardFoldPlan,
    execution: ExperimentExecutionManifest,
    policy: WalkForwardSelectionPolicy,
    *,
    snapshot: DatasetSnapshot,
    artifact_store: BacktestArtifactStore,
    result_verifier: BacktestResultVerifier,
    metric_loader: MetricLoader,
) -> FoldSelectionEvidence:
    """Authenticate and project exactly the TRAIN/VALIDATION candidate set."""

    try:
        validate_execution_manifest_against_plan(execution, fold.experiment_plan, snapshot)
    except Exception as error:
        raise IncompatibleWalkForwardExecutionError(
            "experiment execution diverges from the fold plan"
        ) from error
    candidates: list[SelectionCandidateEvidence] = []
    for combination in fold.experiment_plan.combinations:
        offset = combination.index * 3
        if offset + 1 >= len(execution.records):
            raise IncompatibleWalkForwardExecutionError("candidate records are absent")
        train_record = execution.records[offset]
        validation_record = execution.records[offset + 1]
        train_spec = fold.experiment_plan.run_specs[offset]
        validation_spec = fold.experiment_plan.run_specs[offset + 1]
        train_reference, train_reason = _verified_selection_reference(
            train_record,
            train_spec,
            snapshot,
            artifact_store,
            result_verifier,
            "train",
        )
        validation_reference, validation_reason = _verified_selection_reference(
            validation_record,
            validation_spec,
            snapshot,
            artifact_store,
            result_verifier,
            "validation",
        )
        reason = train_reason or validation_reason
        score: Decimal | None = None
        if reason is None:
            if validation_reference is None:
                raise IncompatibleWalkForwardExecutionError(
                    "verified VALIDATION reference is absent"
                )
            try:
                summary = metric_loader(validation_reference.run_id)
                metrics = summary.get("metrics")
                if not isinstance(metrics, Mapping):
                    raise MissingWalkForwardMetricError()
                score = _selection_metric(metrics, policy.metric)
            except (MissingWalkForwardMetricError, InvalidWalkForwardMetricError) as error:
                reason = error.code
            except Exception:
                reason = "validation_metric_unavailable"
        candidate = SelectionCandidateEvidence(
            combination_index=combination.index,
            combination_id=combination.combination_id,
            parameters=combination.parameters,
            status=(
                SelectionCandidateStatus.ELIGIBLE
                if reason is None
                else SelectionCandidateStatus.REJECTED
            ),
            rejection_reason=reason,
            train=train_reference,
            validation=validation_reference,
            validation_metric=policy.metric,
            validation_score=score,
        )
        validate_selection_candidate(candidate)
        candidates.append(candidate)
    typed_candidates = tuple(candidates)
    eligible_count = sum(
        item.status is SelectionCandidateStatus.ELIGIBLE for item in typed_candidates
    )
    values = selection_evidence_values_payload(
        fold_id=fold.fold_id,
        fold_index=fold.fold_index,
        experiment_id=fold.experiment_plan.experiment_id,
        policy=policy,
        candidates=typed_candidates,
        eligible_count=eligible_count,
        rejected_count=len(typed_candidates) - eligible_count,
    )
    evidence = FoldSelectionEvidence(
        fold_id=fold.fold_id,
        fold_index=fold.fold_index,
        experiment_id=fold.experiment_plan.experiment_id,
        policy=policy,
        candidates=typed_candidates,
        eligible_count=eligible_count,
        rejected_count=len(typed_candidates) - eligible_count,
        checksum=document_checksum(values),
        selection_evidence_id=deterministic_id("adt-walk-forward-selection-evidence-v1", values),
    )
    validate_fold_selection_evidence(evidence)
    return evidence


class WalkForwardExecutionService:
    """Execute folds chronologically, freeze selection, then resolve the winner TEST."""

    def __init__(
        self,
        data_dir: Path,
        *,
        planning_service: WalkForwardPlanningService | None = None,
        experiment_execution_service: ExperimentExecutionService | None = None,
        repository: WalkForwardRepository | None = None,
        metric_loader: MetricLoader | None = None,
        contract_loader: ContractLoader | None = None,
        artifact_store: BacktestArtifactStore | None = None,
        result_verifier: BacktestResultVerifier | None = None,
        max_manifest_bytes: int = MAX_WALK_FORWARD_MANIFEST_BYTES,
    ) -> None:
        if (
            isinstance(max_manifest_bytes, bool)
            or not isinstance(max_manifest_bytes, int)
            or not 1 <= max_manifest_bytes <= MAX_WALK_FORWARD_MANIFEST_BYTES
        ):
            raise ValueError("walk-forward manifest limit is invalid")
        self._data_dir = data_dir
        self._planning = planning_service or WalkForwardPlanningService()
        self._experiments = experiment_execution_service or ExperimentExecutionService(data_dir)
        self._repository = repository or WalkForwardRepository(data_dir)
        reader = BacktestRunReader(data_dir)
        self._metric_loader = metric_loader or reader.inspect
        self._artifact_store = artifact_store or self._experiments.artifact_store
        self._result_verifier = result_verifier or self._experiments.result_verifier
        self._contract_loader = contract_loader or self._load_contracts
        self._selection = WalkForwardSelectionService()
        self._max_manifest_bytes = max_manifest_bytes

    def execute(self, plan: WalkForwardPlan) -> WalkForwardExecutionPublication:
        """Preflight the whole plan before invoking one fold executor."""

        try:
            validate_walk_forward_plan(plan)
            if plan.fold_count * plan.combination_count * 3 != plan.total_specs:
                raise IncompatibleWalkForwardExecutionError("walk-forward cardinality diverges")
            if _maximum_manifest_size(plan) > self._max_manifest_bytes:
                raise WalkForwardLimitExceededError("walk-forward manifest would exceed byte limit")
            snapshot, dataset_manifest = self._contract_loader(plan)
            self._planning.validate(plan, snapshot, dataset_manifest)
        except WalkForwardError:
            raise
        except Exception as error:
            message = getattr(error, "message", "walk-forward preflight failed")
            raise IncompatibleWalkForwardExecutionError(message) from error

        results: list[WalkForwardFoldResult] = []
        executions: dict[tuple[str, str], ExperimentExecutionManifest] = {}
        for fold in plan.folds:
            results.append(self._execute_fold(plan, fold, snapshot, executions))
        manifest = _build_execution_manifest(plan, tuple(results))

        def semantic_validator(
            value: WalkForwardExecutionManifest,
        ) -> WalkForwardExecutionManifest:
            return validate_walk_forward_execution_manifest_against_plan(
                value,
                plan,
                execution_loader=lambda experiment_id, execution_id: executions[
                    (experiment_id, execution_id)
                ],
                snapshot=snapshot,
                artifact_store=self._artifact_store,
                result_verifier=self._result_verifier,
                metric_loader=self._metric_loader,
            )

        semantic_validator(manifest)
        return self._repository.publish(manifest, semantic_validator=semantic_validator)

    def _execute_fold(
        self,
        plan: WalkForwardPlan,
        fold: WalkForwardFoldPlan,
        snapshot: DatasetSnapshot,
        executions: dict[tuple[str, str], ExperimentExecutionManifest],
    ) -> WalkForwardFoldResult:
        try:
            publication = self._experiments.execute(fold.experiment_plan)
        except Exception as error:
            return _failed_fold(fold, WalkForwardFoldStatus.FAILED_EXECUTION, error, None)
        execution = publication.manifest
        try:
            validate_execution_manifest_against_plan(execution, fold.experiment_plan, snapshot)
        except Exception as error:
            return _failed_fold(fold, WalkForwardFoldStatus.FAILED_EXECUTION, error, None)
        executions[(execution.experiment_id, execution.experiment_execution_id)] = execution

        evidence: FoldSelectionEvidence | None = None
        try:
            evidence = build_fold_selection_evidence(
                fold,
                execution,
                plan.selection_policy,
                snapshot=snapshot,
                artifact_store=self._artifact_store,
                result_verifier=self._result_verifier,
                metric_loader=self._metric_loader,
            )
            decision = self._selection.select(evidence)
        except NoEligibleWalkForwardCandidateError as error:
            return _failed_fold(
                fold,
                WalkForwardFoldStatus.FAILED_NO_ELIGIBLE_CANDIDATE,
                error,
                publication,
                selection_evidence=evidence,
            )
        except Exception as error:
            return _failed_fold(
                fold,
                WalkForwardFoldStatus.FAILED_SELECTION,
                error,
                publication,
                selection_evidence=evidence,
            )

        try:
            holdout = self._resolve_holdout(fold, execution, decision, snapshot)
        except Exception as error:
            return _failed_fold(
                fold,
                WalkForwardFoldStatus.FAILED_HOLDOUT,
                error,
                publication,
                selection_evidence=evidence,
                selection=decision,
            )
        values = dict(
            fold_id=fold.fold_id,
            fold_index=fold.fold_index,
            experiment_id=fold.experiment_plan.experiment_id,
            experiment_execution_id=execution.experiment_execution_id,
            experiment_execution_checksum=execution.checksum,
            status=WalkForwardFoldStatus.COMPLETED,
            selection_evidence=evidence,
            selection=decision,
            holdout=holdout,
            failure=None,
        )
        provisional = _fold_result_projection(**values)
        return WalkForwardFoldResult(
            fold_id=fold.fold_id,
            fold_index=fold.fold_index,
            experiment_id=fold.experiment_plan.experiment_id,
            experiment_execution_id=execution.experiment_execution_id,
            experiment_execution_checksum=execution.checksum,
            status=WalkForwardFoldStatus.COMPLETED,
            selection_evidence=evidence,
            selection=decision,
            holdout=holdout,
            failure=None,
            checksum=document_checksum(fold_result_payload(provisional)),
        )

    def _resolve_holdout(
        self,
        fold: WalkForwardFoldPlan,
        execution: ExperimentExecutionManifest,
        decision: FoldSelectionDecision,
        snapshot: DatasetSnapshot,
    ) -> SelectedHoldoutResult:
        return _build_verified_holdout(
            fold,
            execution,
            decision,
            snapshot,
            self._artifact_store,
            self._result_verifier,
            self._metric_loader,
        )

    def _load_contracts(self, plan: WalkForwardPlan) -> tuple[DatasetSnapshot, DatasetManifest]:
        reader = MarketDatasetReader(self._data_dir)
        snapshot = reader.open_snapshot(plan.snapshot.snapshot_id)
        return snapshot, reader.manifest()


def validate_walk_forward_execution_manifest_against_plan(
    manifest: WalkForwardExecutionManifest,
    plan: WalkForwardPlan,
    *,
    execution_loader: ExperimentExecutionLoader,
    snapshot: DatasetSnapshot,
    artifact_store: BacktestArtifactStore,
    result_verifier: BacktestResultVerifier,
    metric_loader: MetricLoader,
) -> WalkForwardExecutionManifest:
    """Reconcile every fold, failure, selection and selected holdout with the plan."""

    try:
        validate_walk_forward_plan(plan)
        validate_walk_forward_execution_manifest(manifest)
    except WalkForwardError:
        raise
    except Exception as error:
        raise IncompatibleWalkForwardExecutionError(
            "walk-forward contracts cannot be validated"
        ) from error
    if (
        manifest.walk_forward_plan_id != plan.walk_forward_plan_id
        or manifest.plan_checksum != plan.checksum
        or manifest.snapshot != plan.snapshot
        or manifest.window_policy != plan.window_policy
        or manifest.selection_policy != plan.selection_policy
        or manifest.ordering_policy is not plan.ordering_policy
        or len(manifest.folds) != len(plan.folds)
    ):
        raise IncompatibleWalkForwardExecutionError("execution manifest diverges from its plan")
    for result, fold in zip(manifest.folds, plan.folds, strict=True):
        validate_walk_forward_fold_plan(fold)
        if (
            result.fold_id != fold.fold_id
            or result.fold_index != fold.fold_index
            or result.experiment_id != fold.experiment_plan.experiment_id
        ):
            raise IncompatibleWalkForwardExecutionError("fold result diverges from its plan")
        if result.experiment_execution_id is None:
            if result.status is not WalkForwardFoldStatus.FAILED_EXECUTION:
                raise IncompatibleWalkForwardExecutionError(
                    "post-execution fold has no execution reference"
                )
            continue
        try:
            execution = execution_loader(
                result.experiment_id,
                result.experiment_execution_id,
            )
        except Exception as error:
            raise IncompatibleWalkForwardExecutionError(
                "referenced experiment execution cannot be loaded"
            ) from error
        if (
            execution.experiment_execution_id != result.experiment_execution_id
            or execution.checksum != result.experiment_execution_checksum
        ):
            raise IncompatibleWalkForwardExecutionError(
                "referenced experiment execution identity diverges"
            )
        try:
            validate_execution_manifest_against_plan(execution, fold.experiment_plan, snapshot)
        except Exception as error:
            raise IncompatibleWalkForwardExecutionError(
                "referenced experiment execution diverges from fold plan"
            ) from error
        try:
            rebuilt_evidence = build_fold_selection_evidence(
                fold,
                execution,
                plan.selection_policy,
                snapshot=snapshot,
                artifact_store=artifact_store,
                result_verifier=result_verifier,
                metric_loader=metric_loader,
            )
        except Exception as error:
            if (
                result.status is WalkForwardFoldStatus.FAILED_SELECTION
                and result.selection_evidence is None
                and result.selection is None
                and result.holdout is None
                and result.failure == _safe_failure(error)
            ):
                continue
            raise IncompatibleWalkForwardExecutionError(
                "selection evidence cannot be reproduced"
            ) from error
        if result.selection_evidence != rebuilt_evidence:
            raise IncompatibleWalkForwardExecutionError(
                "stored selection evidence is not reproducible"
            )
        if rebuilt_evidence.eligible_count == 0:
            expected_failure = _safe_failure(NoEligibleWalkForwardCandidateError())
            if (
                result.status is not WalkForwardFoldStatus.FAILED_NO_ELIGIBLE_CANDIDATE
                or result.selection is not None
                or result.holdout is not None
                or result.failure != expected_failure
            ):
                raise IncompatibleWalkForwardExecutionError(
                    "fold state diverges from candidate eligibility"
                )
            continue
        try:
            recomputed = WalkForwardSelectionService().select(rebuilt_evidence)
        except Exception as error:
            if (
                result.status is WalkForwardFoldStatus.FAILED_SELECTION
                and result.selection is None
                and result.holdout is None
                and result.failure == _safe_failure(error)
            ):
                continue
            raise IncompatibleWalkForwardExecutionError(
                "selection failure is not reproducible"
            ) from error
        if result.status is WalkForwardFoldStatus.FAILED_SELECTION:
            raise IncompatibleWalkForwardExecutionError(
                "failed selection state is not reproducible"
            )
        if result.selection != recomputed:
            raise IncompatibleWalkForwardExecutionError(
                "stored selection decision is not reproducible"
            )
        combination = fold.experiment_plan.combinations[recomputed.combination_index]
        if (
            combination.index != recomputed.combination_index
            or combination.combination_id != recomputed.combination_id
            or combination.parameters != recomputed.parameters
            or recomputed.eligible_count + recomputed.rejected_count != plan.combination_count
        ):
            raise IncompatibleWalkForwardExecutionError(
                "selection winner diverges from experiment plan"
            )
        try:
            expected_holdout = _build_verified_holdout(
                fold,
                execution,
                recomputed,
                snapshot,
                artifact_store,
                result_verifier,
                metric_loader,
            )
        except Exception as error:
            if (
                result.status is WalkForwardFoldStatus.FAILED_HOLDOUT
                and result.holdout is None
                and result.failure == _safe_failure(error)
            ):
                continue
            raise IncompatibleWalkForwardExecutionError(
                "holdout failure is not reproducible"
            ) from error
        if (
            result.status is not WalkForwardFoldStatus.COMPLETED
            or result.holdout != expected_holdout
            or result.failure is not None
        ):
            raise IncompatibleWalkForwardExecutionError(
                "completed holdout result is not reproducible"
            )
    return manifest


def verify_published_walk_forward_execution(
    repository: WalkForwardRepository,
    plan: WalkForwardPlan,
    walk_forward_execution_id: str,
    *,
    execution_loader: ExperimentExecutionLoader,
    snapshot: DatasetSnapshot,
    artifact_store: BacktestArtifactStore,
    result_verifier: BacktestResultVerifier,
    metric_loader: MetricLoader,
) -> WalkForwardExecutionManifest:
    """Read and independently verify a published walk-forward execution."""

    manifest = repository.read(plan.walk_forward_plan_id, walk_forward_execution_id)
    return validate_walk_forward_execution_manifest_against_plan(
        manifest,
        plan,
        execution_loader=execution_loader,
        snapshot=snapshot,
        artifact_store=artifact_store,
        result_verifier=result_verifier,
        metric_loader=metric_loader,
    )


def _verified_selection_reference(
    record: PlannedRunExecution,
    spec: PlannedRunSpec,
    snapshot: DatasetSnapshot,
    artifact_store: BacktestArtifactStore,
    result_verifier: BacktestResultVerifier,
    label: str,
) -> tuple[SelectionExecutionReference | None, str | None]:
    successful = {PlannedRunExecutionStatus.COMPLETED, PlannedRunExecutionStatus.REUSED}
    if record.status not in successful or not record.verified:
        return None, f"{label}_not_verified"
    if (
        record.run_id is None
        or record.logical_result_checksum is None
        or record.artifact_path is None
    ):
        return None, f"{label}_record_incomplete"
    expected_run_id = build_run_id(spec.backtest_config, snapshot).value
    if record.run_id != expected_run_id:
        return None, f"{label}_run_id_mismatch"
    try:
        expected_path = artifact_store.relative_run_path(expected_run_id)
        if record.artifact_path != expected_path:
            return None, f"{label}_artifact_path_mismatch"
        verification = result_verifier.verify(expected_run_id)
    except Exception:
        return None, f"{label}_artifact_verification_failed"
    if (
        verification.run_id.value != expected_run_id
        or verification.logical_result_checksum != record.logical_result_checksum
    ):
        return None, f"{label}_artifact_checksum_mismatch"
    reference = SelectionExecutionReference(
        run_spec_id=record.run_spec_id,
        run_id=record.run_id,
        logical_result_checksum=record.logical_result_checksum,
        artifact_path=record.artifact_path,
        status=SelectionEvidenceStatus.VERIFIED_SUCCESS,
    )
    return reference, None


def _build_verified_holdout(
    fold: WalkForwardFoldPlan,
    execution: ExperimentExecutionManifest,
    decision: FoldSelectionDecision,
    snapshot: DatasetSnapshot,
    artifact_store: BacktestArtifactStore,
    result_verifier: BacktestResultVerifier,
    metric_loader: MetricLoader,
) -> SelectedHoldoutResult:
    offset = decision.combination_index * 3 + 2
    if offset >= len(fold.experiment_plan.run_specs) or offset >= len(execution.records):
        raise InvalidWalkForwardHoldoutError("winner TEST spec is absent")
    spec = fold.experiment_plan.run_specs[offset]
    record = execution.records[offset]
    if (
        spec.purpose is not ExperimentRunPurpose.FINAL_HOLDOUT
        or spec.combination.index != decision.combination_index
        or spec.combination.combination_id != decision.combination_id
        or spec.combination.parameters != decision.parameters
        or record.run_spec_id != spec.run_spec_id
        or record.combination_index != decision.combination_index
        or record.combination_id != decision.combination_id
        or record.segment_index != 2
        or record.purpose is not ExperimentRunPurpose.FINAL_HOLDOUT
        or record.status
        not in {PlannedRunExecutionStatus.COMPLETED, PlannedRunExecutionStatus.REUSED}
        or not record.verified
        or record.run_id is None
        or record.logical_result_checksum is None
        or record.artifact_path is None
    ):
        raise InvalidWalkForwardHoldoutError("winner TEST record diverges from its plan")
    expected_run_id = build_run_id(spec.backtest_config, snapshot).value
    expected_path = artifact_store.relative_run_path(expected_run_id)
    if record.run_id != expected_run_id or record.artifact_path != expected_path:
        raise InvalidWalkForwardHoldoutError("winner TEST reference is incompatible")
    try:
        verification = result_verifier.verify(expected_run_id)
    except Exception as error:
        raise InvalidWalkForwardHoldoutError("winner TEST verification failed") from error
    if (
        verification.run_id.value != expected_run_id
        or verification.logical_result_checksum != record.logical_result_checksum
    ):
        raise InvalidWalkForwardHoldoutError("winner TEST checksum diverges")
    try:
        summary = metric_loader(expected_run_id)
        metrics = summary.get("metrics")
    except Exception as error:
        raise InvalidWalkForwardHoldoutError("winner TEST metrics cannot be read") from error
    if not isinstance(metrics, Mapping):
        raise InvalidWalkForwardHoldoutError("winner TEST metrics are absent")
    return SelectedHoldoutResult(
        run_spec_id=spec.run_spec_id,
        run_id=expected_run_id,
        logical_result_checksum=record.logical_result_checksum,
        artifact_path=expected_path,
        metrics=_holdout_metrics(metrics),
    )


def _candidate_score(candidate: SelectionCandidateEvidence) -> Decimal:
    if not isinstance(candidate.validation_score, Decimal):
        raise InvalidWalkForwardCandidateError("eligible candidate score is absent")
    return candidate.validation_score


def _compact_reasons(reasons: tuple[str | None, ...]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for reason in reasons:
        if not isinstance(reason, str) or not reason:
            raise InvalidWalkForwardCandidateError("candidate rejection reason is invalid")
        counts[reason] = counts.get(reason, 0) + 1
    return tuple(f"{reason}:{counts[reason]}" for reason in sorted(counts))


def _selection_metric(metrics: Mapping[str, object], metric: ComparisonMetric) -> Decimal:
    if metric.value not in metrics or metrics[metric.value] is None:
        raise MissingWalkForwardMetricError()
    raw = metrics[metric.value]
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise InvalidWalkForwardMetricError()
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise InvalidWalkForwardMetricError() from None
    if not value.is_finite() or decimal_text(value) != raw:
        raise InvalidWalkForwardMetricError()
    return value


def _holdout_metrics(metrics: Mapping[str, object]) -> tuple[tuple[str, Decimal | int | None], ...]:
    try:
        metric_count = len(metrics)
    except Exception as error:
        raise InvalidWalkForwardHoldoutError("holdout metric collection is invalid") from error
    if not 1 <= metric_count <= MAX_HOLDOUT_METRICS:
        raise InvalidWalkForwardHoldoutError("holdout metric count exceeds its limit")
    try:
        names = tuple(metrics)
    except Exception as error:
        raise InvalidWalkForwardHoldoutError("holdout metric collection is invalid") from error
    if any(
        not isinstance(name, str) or not name or len(name) > MAX_HOLDOUT_METRIC_NAME_CHARACTERS
        for name in names
    ):
        raise InvalidWalkForwardHoldoutError("holdout metric name is invalid")
    projected: list[tuple[str, Decimal | int | None]] = []
    for name in sorted(names):
        try:
            raw = metrics[name]
        except Exception as error:
            raise InvalidWalkForwardHoldoutError("holdout metric cannot be read") from error
        if raw is None:
            value: Decimal | int | None = None
        elif isinstance(raw, bool) or isinstance(raw, float):
            raise InvalidWalkForwardHoldoutError("holdout metric type is invalid")
        elif isinstance(raw, int):
            value = raw
        elif isinstance(raw, str):
            try:
                decimal = Decimal(raw)
            except InvalidOperation:
                raise InvalidWalkForwardHoldoutError("holdout decimal is invalid") from None
            if not decimal.is_finite() or decimal_text(decimal) != raw:
                raise InvalidWalkForwardHoldoutError("holdout decimal is not canonical")
            value = decimal
        else:
            raise InvalidWalkForwardHoldoutError("holdout metric type is invalid")
        projected.append((name, value))
    if not projected:
        raise InvalidWalkForwardHoldoutError("holdout metrics are empty")
    return tuple(projected)


def _failed_fold(
    fold: WalkForwardFoldPlan,
    status: WalkForwardFoldStatus,
    error: Exception,
    publication: ExperimentExecutionPublication | None,
    *,
    selection_evidence: FoldSelectionEvidence | None = None,
    selection: FoldSelectionDecision | None = None,
) -> WalkForwardFoldResult:
    failure = _safe_failure(error)
    manifest = None if publication is None else publication.manifest
    values = dict(
        fold_id=fold.fold_id,
        fold_index=fold.fold_index,
        experiment_id=fold.experiment_plan.experiment_id,
        experiment_execution_id=None if manifest is None else manifest.experiment_execution_id,
        experiment_execution_checksum=None if manifest is None else manifest.checksum,
        status=status,
        selection_evidence=selection_evidence,
        selection=selection,
        holdout=None,
        failure=failure,
    )
    provisional = _fold_result_projection(**values)
    return WalkForwardFoldResult(
        fold_id=fold.fold_id,
        fold_index=fold.fold_index,
        experiment_id=fold.experiment_plan.experiment_id,
        experiment_execution_id=None if manifest is None else manifest.experiment_execution_id,
        experiment_execution_checksum=None if manifest is None else manifest.checksum,
        status=status,
        selection_evidence=selection_evidence,
        selection=selection,
        holdout=None,
        failure=failure,
        checksum=document_checksum(fold_result_payload(provisional)),
    )


def _safe_failure(error: Exception) -> WalkForwardFailure:
    if isinstance(error, DomainError):
        return WalkForwardFailure(error.code, error.message[:MAX_WALK_FORWARD_ERROR_MESSAGE])
    return WalkForwardFailure(
        "unexpected_walk_forward_error",
        "O fold falhou durante a execução local.",
    )


def _build_execution_manifest(
    plan: WalkForwardPlan,
    folds: tuple[WalkForwardFoldResult, ...],
) -> WalkForwardExecutionManifest:
    completed = sum(item.status is WalkForwardFoldStatus.COMPLETED for item in folds)
    failed = len(folds) - completed
    status = (
        WalkForwardExecutionStatus.COMPLETED
        if failed == 0
        else WalkForwardExecutionStatus.FAILED
        if completed == 0
        else WalkForwardExecutionStatus.PARTIALLY_FAILED
    )
    values = dict(
        walk_forward_plan_id=plan.walk_forward_plan_id,
        plan_checksum=plan.checksum,
        snapshot=plan.snapshot,
        window_policy=plan.window_policy,
        selection_policy=plan.selection_policy,
        failure_policy=WalkForwardFailurePolicy.CONTINUE_AFTER_FOLD_FAILURE,
        ordering_policy=plan.ordering_policy,
        folds=folds,
        fold_count=len(folds),
        completed_count=completed,
        failed_count=failed,
        status=status,
        schema_version=1,
    )
    provisional = _manifest_projection(**values)
    payload = execution_manifest_payload(provisional)
    return WalkForwardExecutionManifest(
        walk_forward_plan_id=plan.walk_forward_plan_id,
        plan_checksum=plan.checksum,
        snapshot=plan.snapshot,
        window_policy=plan.window_policy,
        selection_policy=plan.selection_policy,
        failure_policy=WalkForwardFailurePolicy.CONTINUE_AFTER_FOLD_FAILURE,
        ordering_policy=plan.ordering_policy,
        folds=folds,
        fold_count=len(folds),
        completed_count=completed,
        failed_count=failed,
        status=status,
        schema_version=1,
        checksum=document_checksum(payload),
        walk_forward_execution_id=deterministic_id("adt-walk-forward-execution-v1", payload),
    )


def _maximum_manifest_size(plan: WalkForwardPlan) -> int:
    return maximum_walk_forward_execution_bytes(
        plan.search_space,
        plan.fold_count,
        plan.combination_count,
    )


def _decision_projection(**values: object) -> FoldSelectionDecision:
    decision = object.__new__(FoldSelectionDecision)
    for key, value in values.items():
        object.__setattr__(decision, key, value)
    object.__setattr__(decision, "checksum", "0" * 64)
    object.__setattr__(decision, "selection_id", "0" * 64)
    return decision


def _fold_result_projection(**values: object) -> WalkForwardFoldResult:
    result = object.__new__(WalkForwardFoldResult)
    for key, value in values.items():
        object.__setattr__(result, key, value)
    object.__setattr__(result, "checksum", "0" * 64)
    return result


def _manifest_projection(**values: object) -> WalkForwardExecutionManifest:
    manifest = object.__new__(WalkForwardExecutionManifest)
    for key, value in values.items():
        object.__setattr__(manifest, key, value)
    object.__setattr__(manifest, "checksum", "0" * 64)
    object.__setattr__(manifest, "walk_forward_execution_id", "0" * 64)
    return manifest
