"""Strict canonical codecs for Phase 4-05 plans and execution manifests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from app.backtesting.reports import ComparisonMetric
from app.optimization.canonical import canonical_json_bytes, decimal_text
from app.optimization.errors import IncompatibleWalkForwardDocumentError
from app.optimization.experiment_documents import decode_experiment_document
from app.optimization.temporal_documents import (
    _decode_snapshot,
    decode_temporal_document,
)
from app.optimization.walk_forward_domain import (
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
    WalkForwardOrderingPolicy,
    WalkForwardPlan,
    WalkForwardSelectionDirection,
    WalkForwardSelectionKind,
    WalkForwardSelectionPolicy,
    WalkForwardTieBreakPolicy,
    WalkForwardWindowKind,
    WalkForwardWindowPolicy,
    execution_manifest_payload,
    validate_walk_forward_execution_manifest,
    validate_walk_forward_plan,
    walk_forward_plan_payload,
)


def walk_forward_plan_to_document(plan: WalkForwardPlan) -> dict[str, object]:
    validate_walk_forward_plan(plan)
    return {
        "walk_forward_plan": walk_forward_plan_payload(plan),
        "checksum": plan.checksum,
        "walk_forward_plan_id": plan.walk_forward_plan_id,
    }


def canonical_walk_forward_plan_bytes(plan: WalkForwardPlan) -> bytes:
    return canonical_json_bytes(walk_forward_plan_to_document(plan))


def decode_walk_forward_plan_document(envelope: Mapping[str, object]) -> WalkForwardPlan:
    root = _mapping(envelope, "walk-forward plan envelope")
    _exact(root, {"walk_forward_plan", "checksum", "walk_forward_plan_id"})
    payload = _mapping(root["walk_forward_plan"], "walk-forward plan")
    _exact(
        payload,
        {
            "schema_version",
            "snapshot",
            "window_policy",
            "selection_policy",
            "search_space",
            "plugin",
            "backtest_configuration",
            "folds",
            "fold_count",
            "combination_count",
            "specs_per_fold",
            "total_specs",
            "trailing_candles",
            "max_total_specs",
            "ordering_policy",
        },
    )
    folds = tuple(_decode_fold_plan(item) for item in _sequence(payload["folds"]))
    if not folds:
        raise IncompatibleWalkForwardDocumentError("walk-forward folds are absent")
    first = folds[0].experiment_plan
    try:
        plan = WalkForwardPlan(
            snapshot=first.snapshot,
            window_policy=_decode_window_policy(payload["window_policy"]),
            selection_policy=_decode_selection_policy(payload["selection_policy"]),
            search_space=first.search_space,
            plugin=first.plugin,
            backtest_configuration=first.backtest_configuration,
            folds=folds,
            fold_count=_integer(payload["fold_count"]),
            combination_count=_integer(payload["combination_count"]),
            specs_per_fold=_integer(payload["specs_per_fold"]),
            total_specs=_integer(payload["total_specs"]),
            trailing_candles=_integer(payload["trailing_candles"]),
            max_total_specs=_integer(payload["max_total_specs"]),
            ordering_policy=WalkForwardOrderingPolicy(_text(payload["ordering_policy"])),
            schema_version=_integer(payload["schema_version"]),
            checksum=_text(root["checksum"]),
            walk_forward_plan_id=_text(root["walk_forward_plan_id"]),
        )
    except (ValueError, TypeError) as error:
        raise IncompatibleWalkForwardDocumentError(
            "walk-forward plan values are invalid"
        ) from error
    if walk_forward_plan_payload(plan) != dict(payload):
        raise IncompatibleWalkForwardDocumentError("walk-forward plan is not canonical")
    return plan


def walk_forward_execution_to_document(
    manifest: WalkForwardExecutionManifest,
) -> dict[str, object]:
    validate_walk_forward_execution_manifest(manifest)
    return {
        "walk_forward_execution": execution_manifest_payload(manifest),
        "checksum": manifest.checksum,
        "walk_forward_execution_id": manifest.walk_forward_execution_id,
    }


def canonical_walk_forward_execution_bytes(manifest: WalkForwardExecutionManifest) -> bytes:
    return canonical_json_bytes(walk_forward_execution_to_document(manifest))


def decode_walk_forward_execution_document(
    envelope: Mapping[str, object],
) -> WalkForwardExecutionManifest:
    root = _mapping(envelope, "walk-forward execution envelope")
    _exact(root, {"walk_forward_execution", "checksum", "walk_forward_execution_id"})
    payload = _mapping(root["walk_forward_execution"], "walk-forward execution")
    _exact(
        payload,
        {
            "schema_version",
            "walk_forward_plan_id",
            "plan_checksum",
            "snapshot",
            "window_policy",
            "selection_policy",
            "failure_policy",
            "ordering_policy",
            "folds",
            "fold_count",
            "completed_count",
            "failed_count",
            "status",
        },
    )
    folds = tuple(_decode_fold_result(item) for item in _sequence(payload["folds"]))
    try:
        manifest = WalkForwardExecutionManifest(
            walk_forward_plan_id=_text(payload["walk_forward_plan_id"]),
            plan_checksum=_text(payload["plan_checksum"]),
            snapshot=_decode_snapshot(payload["snapshot"]),
            window_policy=_decode_window_policy(payload["window_policy"]),
            selection_policy=_decode_selection_policy(payload["selection_policy"]),
            failure_policy=WalkForwardFailurePolicy(_text(payload["failure_policy"])),
            ordering_policy=WalkForwardOrderingPolicy(_text(payload["ordering_policy"])),
            folds=folds,
            fold_count=_integer(payload["fold_count"]),
            completed_count=_integer(payload["completed_count"]),
            failed_count=_integer(payload["failed_count"]),
            status=WalkForwardExecutionStatus(_text(payload["status"])),
            checksum=_text(root["checksum"]),
            walk_forward_execution_id=_text(root["walk_forward_execution_id"]),
            schema_version=_integer(payload["schema_version"]),
        )
    except (ValueError, TypeError) as error:
        raise IncompatibleWalkForwardDocumentError(
            "walk-forward execution values are invalid"
        ) from error
    if execution_manifest_payload(manifest) != dict(payload):
        raise IncompatibleWalkForwardDocumentError("walk-forward execution is not canonical")
    return manifest


def _decode_fold_plan(raw: object) -> WalkForwardFoldPlan:
    value = _mapping(raw, "fold envelope")
    _exact(value, {"fold", "checksum", "fold_id"})
    payload = _mapping(value["fold"], "fold")
    _exact(
        payload,
        {
            "fold_index",
            "selected_coverage",
            "temporal_plan",
            "temporal_plan_checksum",
            "temporal_plan_id",
            "experiment_plan",
        },
    )
    temporal = decode_temporal_document(_mapping(payload["temporal_plan"], "temporal plan"))
    experiment = decode_experiment_document(_mapping(payload["experiment_plan"], "experiment plan"))
    try:
        fold = WalkForwardFoldPlan(
            fold_index=_integer(payload["fold_index"]),
            selected_coverage=temporal.selected_coverage,
            temporal_plan=temporal,
            experiment_plan=experiment,
            checksum=_text(value["checksum"]),
            fold_id=_text(value["fold_id"]),
        )
    except (ValueError, TypeError) as error:
        raise IncompatibleWalkForwardDocumentError("walk-forward fold is invalid") from error
    if payload["temporal_plan_checksum"] != temporal.checksum:
        raise IncompatibleWalkForwardDocumentError("temporal checksum diverges")
    if payload["temporal_plan_id"] != temporal.plan_id:
        raise IncompatibleWalkForwardDocumentError("temporal id diverges")
    return fold


def _decode_window_policy(raw: object) -> WalkForwardWindowPolicy:
    value = _mapping(raw, "window policy")
    _exact(
        value,
        {
            "schema_version",
            "kind",
            "train_candles",
            "validation_candles",
            "test_candles",
            "warmup_candles",
            "max_folds",
        },
    )
    try:
        return WalkForwardWindowPolicy(
            train_candles=_integer(value["train_candles"]),
            validation_candles=_integer(value["validation_candles"]),
            test_candles=_integer(value["test_candles"]),
            warmup_candles=_integer(value["warmup_candles"]),
            max_folds=_integer(value["max_folds"]),
            kind=WalkForwardWindowKind(_text(value["kind"])),
            schema_version=_integer(value["schema_version"]),
        )
    except ValueError as error:
        raise IncompatibleWalkForwardDocumentError("window policy is invalid") from error


def _decode_selection_policy(raw: object) -> WalkForwardSelectionPolicy:
    value = _mapping(raw, "selection policy")
    _exact(
        value,
        {
            "schema_version",
            "kind",
            "metric",
            "direction",
            "tie_break",
            "missing_metric",
            "invalid_metric",
        },
    )
    if (
        value["missing_metric"] != "REJECT_CANDIDATE"
        or value["invalid_metric"] != "REJECT_CANDIDATE"
    ):
        raise IncompatibleWalkForwardDocumentError("metric failure policy is invalid")
    try:
        return WalkForwardSelectionPolicy(
            metric=ComparisonMetric(_text(value["metric"])),
            direction=WalkForwardSelectionDirection(_text(value["direction"])),
            tie_break=WalkForwardTieBreakPolicy(_text(value["tie_break"])),
            kind=WalkForwardSelectionKind(_text(value["kind"])),
            schema_version=_integer(value["schema_version"]),
        )
    except ValueError as error:
        raise IncompatibleWalkForwardDocumentError("selection policy is invalid") from error


def _decode_fold_result(raw: object) -> WalkForwardFoldResult:
    envelope = _mapping(raw, "fold result envelope")
    _exact(envelope, {"result", "checksum"})
    value = _mapping(envelope["result"], "fold result")
    _exact(
        value,
        {
            "fold_id",
            "fold_index",
            "experiment_id",
            "experiment_execution_id",
            "experiment_execution_checksum",
            "status",
            "selection_evidence",
            "selection",
            "holdout",
            "failure",
        },
    )
    try:
        return WalkForwardFoldResult(
            fold_id=_text(value["fold_id"]),
            fold_index=_integer(value["fold_index"]),
            experiment_id=_text(value["experiment_id"]),
            experiment_execution_id=_optional_text(value["experiment_execution_id"]),
            experiment_execution_checksum=_optional_text(value["experiment_execution_checksum"]),
            status=WalkForwardFoldStatus(_text(value["status"])),
            selection_evidence=(
                None
                if value["selection_evidence"] is None
                else _decode_selection_evidence(value["selection_evidence"])
            ),
            selection=None if value["selection"] is None else _decode_selection(value["selection"]),
            holdout=None if value["holdout"] is None else _decode_holdout(value["holdout"]),
            failure=None if value["failure"] is None else _decode_failure(value["failure"]),
            checksum=_text(envelope["checksum"]),
        )
    except (ValueError, TypeError) as error:
        raise IncompatibleWalkForwardDocumentError("fold result is invalid") from error


def _decode_selection(raw: object) -> FoldSelectionDecision:
    envelope = _mapping(raw, "selection envelope")
    _exact(envelope, {"decision", "checksum", "selection_id"})
    value = _mapping(envelope["decision"], "selection decision")
    _exact(
        value,
        {
            "fold_id",
            "fold_index",
            "policy",
            "combination_index",
            "combination_id",
            "parameters",
            "score",
            "rank",
            "eligible_count",
            "rejected_count",
            "rejection_reasons",
            "train",
            "validation",
            "selection_evidence_id",
            "selection_evidence_checksum",
        },
    )
    return FoldSelectionDecision(
        fold_id=_text(value["fold_id"]),
        fold_index=_integer(value["fold_index"]),
        policy=_decode_selection_policy(value["policy"]),
        combination_index=_integer(value["combination_index"]),
        combination_id=_text(value["combination_id"]),
        parameters=_decode_parameters(value["parameters"]),
        score=_decimal(value["score"]),
        rank=_integer(value["rank"]),
        eligible_count=_integer(value["eligible_count"]),
        rejected_count=_integer(value["rejected_count"]),
        rejection_reasons=tuple(_text(item) for item in _sequence(value["rejection_reasons"])),
        train=_decode_reference(value["train"]),
        validation=_decode_reference(value["validation"]),
        selection_evidence_id=_text(value["selection_evidence_id"]),
        selection_evidence_checksum=_text(value["selection_evidence_checksum"]),
        checksum=_text(envelope["checksum"]),
        selection_id=_text(envelope["selection_id"]),
    )


def _decode_selection_evidence(raw: object) -> FoldSelectionEvidence:
    envelope = _mapping(raw, "selection evidence envelope")
    _exact(envelope, {"evidence", "checksum", "selection_evidence_id"})
    value = _mapping(envelope["evidence"], "selection evidence")
    _exact(
        value,
        {
            "fold_id",
            "fold_index",
            "experiment_id",
            "policy",
            "candidates",
            "eligible_count",
            "rejected_count",
        },
    )
    return FoldSelectionEvidence(
        fold_id=_text(value["fold_id"]),
        fold_index=_integer(value["fold_index"]),
        experiment_id=_text(value["experiment_id"]),
        policy=_decode_selection_policy(value["policy"]),
        candidates=tuple(_decode_candidate(item) for item in _sequence(value["candidates"])),
        eligible_count=_integer(value["eligible_count"]),
        rejected_count=_integer(value["rejected_count"]),
        checksum=_text(envelope["checksum"]),
        selection_evidence_id=_text(envelope["selection_evidence_id"]),
    )


def _decode_candidate(raw: object) -> SelectionCandidateEvidence:
    value = _mapping(raw, "selection candidate")
    _exact(
        value,
        {
            "combination_index",
            "combination_id",
            "parameters",
            "status",
            "rejection_reason",
            "train",
            "validation",
            "validation_metric",
            "validation_score",
        },
    )
    try:
        return SelectionCandidateEvidence(
            combination_index=_integer(value["combination_index"]),
            combination_id=_text(value["combination_id"]),
            parameters=_decode_parameters(value["parameters"]),
            status=SelectionCandidateStatus(_text(value["status"])),
            rejection_reason=_optional_text(value["rejection_reason"]),
            train=None if value["train"] is None else _decode_reference(value["train"]),
            validation=(
                None if value["validation"] is None else _decode_reference(value["validation"])
            ),
            validation_metric=ComparisonMetric(_text(value["validation_metric"])),
            validation_score=(
                None if value["validation_score"] is None else _decimal(value["validation_score"])
            ),
        )
    except ValueError as error:
        raise IncompatibleWalkForwardDocumentError("selection candidate is invalid") from error


def _decode_reference(raw: object) -> SelectionExecutionReference:
    value = _mapping(raw, "selection reference")
    _exact(
        value,
        {"run_spec_id", "run_id", "logical_result_checksum", "artifact_path", "status"},
    )
    return SelectionExecutionReference(
        run_spec_id=_text(value["run_spec_id"]),
        run_id=_text(value["run_id"]),
        logical_result_checksum=_text(value["logical_result_checksum"]),
        artifact_path=_text(value["artifact_path"]),
        status=SelectionEvidenceStatus(_text(value["status"])),
    )


def _decode_holdout(raw: object) -> SelectedHoldoutResult:
    value = _mapping(raw, "holdout")
    _exact(value, {"run_spec_id", "run_id", "logical_result_checksum", "artifact_path", "metrics"})
    metrics = _mapping(value["metrics"], "holdout metrics")
    return SelectedHoldoutResult(
        run_spec_id=_text(value["run_spec_id"]),
        run_id=_text(value["run_id"]),
        logical_result_checksum=_text(value["logical_result_checksum"]),
        artifact_path=_text(value["artifact_path"]),
        metrics=tuple((name, _metric(raw_value)) for name, raw_value in metrics.items()),
    )


def _decode_failure(raw: object) -> WalkForwardFailure:
    value = _mapping(raw, "walk-forward failure")
    _exact(value, {"code", "message"})
    return WalkForwardFailure(_text(value["code"]), _text(value["message"]))


def _decode_parameters(raw: object) -> tuple[tuple[str, object], ...]:
    result: list[tuple[str, object]] = []
    for item in _sequence(raw):
        pair = _sequence(item)
        if len(pair) != 2:
            raise IncompatibleWalkForwardDocumentError("parameter entry is invalid")
        name = _text(pair[0])
        stored = _mapping(pair[1], "parameter value")
        _exact(stored, {"kind", "value"})
        kind = _text(stored["kind"])
        value = stored["value"]
        if kind == "bool" and isinstance(value, bool):
            decoded: object = value
        elif kind == "int" and not isinstance(value, bool) and isinstance(value, int):
            decoded = value
        elif kind == "str" and isinstance(value, str):
            decoded = value
        elif kind == "decimal":
            decoded = _decimal(value)
        else:
            raise IncompatibleWalkForwardDocumentError("parameter value is invalid")
        result.append((name, decoded))
    return tuple(result)


def _metric(raw: object) -> Decimal | int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise IncompatibleWalkForwardDocumentError("boolean metric is invalid")
    if isinstance(raw, int):
        return raw
    return _decimal(raw)


def _decimal(raw: object) -> Decimal:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise IncompatibleWalkForwardDocumentError("decimal is invalid")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise IncompatibleWalkForwardDocumentError("decimal is invalid") from None
    if not value.is_finite() or decimal_text(value) != raw:
        raise IncompatibleWalkForwardDocumentError("decimal is not canonical")
    return value


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
        raise IncompatibleWalkForwardDocumentError(f"{label} must be an object")
    return raw


def _sequence(raw: object) -> Sequence[object]:
    if not isinstance(raw, list):
        raise IncompatibleWalkForwardDocumentError("document sequence is invalid")
    return raw


def _exact(raw: Mapping[str, object], expected: set[str]) -> None:
    if set(raw) != expected:
        raise IncompatibleWalkForwardDocumentError("document fields are incompatible")


def _text(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        raise IncompatibleWalkForwardDocumentError("document text is invalid")
    return raw


def _optional_text(raw: object) -> str | None:
    return None if raw is None else _text(raw)


def _integer(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise IncompatibleWalkForwardDocumentError("document integer is invalid")
    return raw
