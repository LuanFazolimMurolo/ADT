"""Versioned deterministic contracts for local sequential experiment execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.backtesting.artifacts import build_run_id
from app.market_data.datasets import DatasetSnapshot
from app.optimization.artifact_paths import is_canonical_artifact_path
from app.optimization.canonical import canonical_json_bytes, deterministic_id, document_checksum
from app.optimization.errors import (
    ExperimentPlanningError,
    IncompatibleExperimentExecutionDocumentError,
    InvalidExperimentExecutionTransitionError,
)
from app.optimization.experiment_domain import (
    SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS,
    ExperimentOrderingPolicy,
    ExperimentPlan,
    ExperimentRunPurpose,
    PlannedRunSpec,
    validate_experiment_plan,
)

EXPERIMENT_EXECUTION_SCHEMA_VERSION = 1
SUPPORTED_EXPERIMENT_EXECUTION_SCHEMA_VERSIONS = frozenset({1})
DEFAULT_MAX_EXECUTION_SPECS = 3_000
ABSOLUTE_MAX_EXECUTION_SPECS = 30_000
MAX_EXECUTION_ERROR_CHARACTERS = 500
MAX_EXECUTION_MANIFEST_BYTES = 16 * 1024 * 1024
_MANIFEST_AGGREGATE_MARGIN_BYTES = 256
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PURPOSE_BY_SEGMENT_INDEX = (
    ExperimentRunPurpose.TRAINING,
    ExperimentRunPurpose.MODEL_SELECTION,
    ExperimentRunPurpose.FINAL_HOLDOUT,
)


class PlannedRunExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REUSED = "REUSED"


class ExperimentExecutionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"


class ExperimentFailurePolicy(StrEnum):
    CONTINUE_AFTER_FAILURE = "CONTINUE_AFTER_FAILURE"


class ExperimentWarmupPolicy(StrEnum):
    WARMUP_OBSERVATION_ONLY = "WARMUP_OBSERVATION_ONLY"


_TRANSITIONS = {
    PlannedRunExecutionStatus.PENDING: frozenset({PlannedRunExecutionStatus.RUNNING}),
    PlannedRunExecutionStatus.RUNNING: frozenset(
        {
            PlannedRunExecutionStatus.COMPLETED,
            PlannedRunExecutionStatus.FAILED,
            PlannedRunExecutionStatus.REUSED,
        }
    ),
}


def validate_execution_transition(
    current: PlannedRunExecutionStatus,
    target: PlannedRunExecutionStatus,
) -> None:
    """Reject implicit, repeated and terminal state transitions."""

    if not isinstance(current, PlannedRunExecutionStatus) or not isinstance(
        target, PlannedRunExecutionStatus
    ):
        raise InvalidExperimentExecutionTransitionError("execution transition states are invalid")
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise InvalidExperimentExecutionTransitionError(
            f"execution state cannot transition from {current} to {target}"
        )


@dataclass(frozen=True, slots=True)
class ExperimentExecutionFailure:
    code: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not isinstance(self.message, str):
            raise IncompatibleExperimentExecutionDocumentError("failure contract is invalid")
        message = self.message.strip().replace("\r", " ").replace("\n", " ")
        if _SAFE_CODE.fullmatch(self.code) is None:
            raise IncompatibleExperimentExecutionDocumentError("failure code is invalid")
        if not message or len(message) > MAX_EXECUTION_ERROR_CHARACTERS:
            raise IncompatibleExperimentExecutionDocumentError("failure message is invalid")
        object.__setattr__(self, "message", message)


def validate_experiment_execution_failure(error: ExperimentExecutionFailure) -> None:
    if (
        not isinstance(error, ExperimentExecutionFailure)
        or not isinstance(error.code, str)
        or not isinstance(error.message, str)
        or _SAFE_CODE.fullmatch(error.code) is None
        or not error.message
        or error.message != error.message.strip().replace("\r", " ").replace("\n", " ")
        or len(error.message) > MAX_EXECUTION_ERROR_CHARACTERS
    ):
        raise IncompatibleExperimentExecutionDocumentError("failure contract is invalid")


@dataclass(frozen=True, slots=True)
class PlannedRunExecution:
    run_spec_id: str
    experiment_id: str
    global_index: int
    combination_index: int
    combination_id: str
    segment_index: int
    segment_id: str
    purpose: ExperimentRunPurpose
    status: PlannedRunExecutionStatus
    run_id: str | None
    logical_result_checksum: str | None
    artifact_path: str | None
    error: ExperimentExecutionFailure | None
    reused: bool
    verified: bool
    checksum: str
    execution_record_id: str
    schema_version: int = EXPERIMENT_EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_planned_run_execution(self)


@dataclass(frozen=True, slots=True)
class ExperimentExecutionManifest:
    experiment_id: str
    plan_checksum: str
    plan_schema_version: int
    failure_policy: ExperimentFailurePolicy
    warmup_policy: ExperimentWarmupPolicy
    ordering_policy: ExperimentOrderingPolicy
    total_count: int
    completed_count: int
    reused_count: int
    failed_count: int
    status: ExperimentExecutionStatus
    records: tuple[PlannedRunExecution, ...]
    checksum: str
    experiment_execution_id: str
    schema_version: int = EXPERIMENT_EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_experiment_execution_manifest(self)


def failure_payload(error: ExperimentExecutionFailure) -> dict[str, str]:
    validate_experiment_execution_failure(error)
    return {"code": error.code, "message": error.message}


def planned_run_execution_values_payload(
    *,
    run_spec_id: str,
    experiment_id: str,
    global_index: int,
    combination_index: int,
    combination_id: str,
    segment_index: int,
    segment_id: str,
    purpose: ExperimentRunPurpose,
    status: PlannedRunExecutionStatus,
    run_id: str | None,
    logical_result_checksum: str | None,
    artifact_path: str | None,
    error: ExperimentExecutionFailure | None,
    reused: bool,
    verified: bool,
    schema_version: int = EXPERIMENT_EXECUTION_SCHEMA_VERSION,
) -> dict[str, object]:
    _validate_planned_run_execution_values(
        run_spec_id=run_spec_id,
        experiment_id=experiment_id,
        global_index=global_index,
        combination_index=combination_index,
        combination_id=combination_id,
        segment_index=segment_index,
        segment_id=segment_id,
        purpose=purpose,
        status=status,
        run_id=run_id,
        logical_result_checksum=logical_result_checksum,
        artifact_path=artifact_path,
        error=error,
        reused=reused,
        verified=verified,
        schema_version=schema_version,
    )
    return {
        "schema_version": schema_version,
        "run_spec_id": run_spec_id,
        "experiment_id": experiment_id,
        "global_index": global_index,
        "combination_index": combination_index,
        "combination_id": combination_id,
        "segment_index": segment_index,
        "segment_id": segment_id,
        "purpose": purpose.value,
        "status": status.value,
        "run_id": run_id,
        "logical_result_checksum": logical_result_checksum,
        "artifact_path": artifact_path,
        "error": None if error is None else failure_payload(error),
        "reused": reused,
        "verified": verified,
    }


def planned_run_execution_payload(record: PlannedRunExecution) -> dict[str, object]:
    validate_planned_run_execution(record)
    payload = planned_run_execution_values_payload(
        run_spec_id=record.run_spec_id,
        experiment_id=record.experiment_id,
        global_index=record.global_index,
        combination_index=record.combination_index,
        combination_id=record.combination_id,
        segment_index=record.segment_index,
        segment_id=record.segment_id,
        purpose=record.purpose,
        status=record.status,
        run_id=record.run_id,
        logical_result_checksum=record.logical_result_checksum,
        artifact_path=record.artifact_path,
        error=record.error,
        reused=record.reused,
        verified=record.verified,
        schema_version=record.schema_version,
    )
    payload.update({"checksum": record.checksum, "execution_record_id": record.execution_record_id})
    return payload


def build_planned_run_execution(
    *,
    run_spec_id: str,
    experiment_id: str,
    global_index: int,
    combination_index: int,
    combination_id: str,
    segment_index: int,
    segment_id: str,
    purpose: ExperimentRunPurpose,
    status: PlannedRunExecutionStatus,
    run_id: str | None = None,
    logical_result_checksum: str | None = None,
    artifact_path: str | None = None,
    error: ExperimentExecutionFailure | None = None,
    reused: bool = False,
    verified: bool = False,
) -> PlannedRunExecution:
    values = planned_run_execution_values_payload(
        run_spec_id=run_spec_id,
        experiment_id=experiment_id,
        global_index=global_index,
        combination_index=combination_index,
        combination_id=combination_id,
        segment_index=segment_index,
        segment_id=segment_id,
        purpose=purpose,
        status=status,
        run_id=run_id,
        logical_result_checksum=logical_result_checksum,
        artifact_path=artifact_path,
        error=error,
        reused=reused,
        verified=verified,
    )
    checksum = document_checksum(values)
    return PlannedRunExecution(
        run_spec_id=run_spec_id,
        experiment_id=experiment_id,
        global_index=global_index,
        combination_index=combination_index,
        combination_id=combination_id,
        segment_index=segment_index,
        segment_id=segment_id,
        purpose=purpose,
        status=status,
        run_id=run_id,
        logical_result_checksum=logical_result_checksum,
        artifact_path=artifact_path,
        error=error,
        reused=reused,
        verified=verified,
        checksum=checksum,
        execution_record_id=deterministic_id(
            "adt-experiment-run-execution-v1",
            {"run_spec_id": run_spec_id, "checksum": checksum},
        ),
    )


def experiment_execution_values_payload(
    *,
    experiment_id: str,
    plan_checksum: str,
    plan_schema_version: int,
    failure_policy: ExperimentFailurePolicy,
    warmup_policy: ExperimentWarmupPolicy,
    ordering_policy: ExperimentOrderingPolicy,
    total_count: int,
    completed_count: int,
    reused_count: int,
    failed_count: int,
    status: ExperimentExecutionStatus,
    records: tuple[PlannedRunExecution, ...],
    schema_version: int = EXPERIMENT_EXECUTION_SCHEMA_VERSION,
) -> dict[str, object]:
    _validate_experiment_execution_values(
        experiment_id=experiment_id,
        plan_checksum=plan_checksum,
        plan_schema_version=plan_schema_version,
        failure_policy=failure_policy,
        warmup_policy=warmup_policy,
        ordering_policy=ordering_policy,
        total_count=total_count,
        completed_count=completed_count,
        reused_count=reused_count,
        failed_count=failed_count,
        status=status,
        records=records,
        schema_version=schema_version,
    )
    return {
        "schema_version": schema_version,
        "experiment_id": experiment_id,
        "plan_checksum": plan_checksum,
        "plan_schema_version": plan_schema_version,
        "failure_policy": failure_policy.value,
        "warmup_policy": warmup_policy.value,
        "ordering_policy": ordering_policy.value,
        "total_count": total_count,
        "completed_count": completed_count,
        "reused_count": reused_count,
        "failed_count": failed_count,
        "status": status.value,
        "records": [planned_run_execution_payload(record) for record in records],
    }


def build_experiment_execution_manifest(
    *,
    experiment_id: str,
    plan_checksum: str,
    plan_schema_version: int,
    ordering_policy: ExperimentOrderingPolicy,
    records: tuple[PlannedRunExecution, ...],
) -> ExperimentExecutionManifest:
    _validate_manifest_build_arguments(
        experiment_id=experiment_id,
        plan_checksum=plan_checksum,
        plan_schema_version=plan_schema_version,
        ordering_policy=ordering_policy,
        records=records,
    )
    completed = sum(record.status is PlannedRunExecutionStatus.COMPLETED for record in records)
    reused = sum(record.status is PlannedRunExecutionStatus.REUSED for record in records)
    failed = sum(record.status is PlannedRunExecutionStatus.FAILED for record in records)
    status = (
        ExperimentExecutionStatus.COMPLETED
        if failed == 0
        else ExperimentExecutionStatus.FAILED
        if failed == len(records)
        else ExperimentExecutionStatus.PARTIALLY_FAILED
    )
    values = experiment_execution_values_payload(
        experiment_id=experiment_id,
        plan_checksum=plan_checksum,
        plan_schema_version=plan_schema_version,
        failure_policy=ExperimentFailurePolicy.CONTINUE_AFTER_FAILURE,
        warmup_policy=ExperimentWarmupPolicy.WARMUP_OBSERVATION_ONLY,
        ordering_policy=ordering_policy,
        total_count=len(records),
        completed_count=completed,
        reused_count=reused,
        failed_count=failed,
        status=status,
        records=records,
    )
    checksum = document_checksum(values)
    return ExperimentExecutionManifest(
        experiment_id=experiment_id,
        plan_checksum=plan_checksum,
        plan_schema_version=plan_schema_version,
        failure_policy=ExperimentFailurePolicy.CONTINUE_AFTER_FAILURE,
        warmup_policy=ExperimentWarmupPolicy.WARMUP_OBSERVATION_ONLY,
        ordering_policy=ordering_policy,
        total_count=len(records),
        completed_count=completed,
        reused_count=reused,
        failed_count=failed,
        status=status,
        records=records,
        checksum=checksum,
        experiment_execution_id=deterministic_id(
            "adt-experiment-execution-v1",
            {"experiment_id": experiment_id, "checksum": checksum},
        ),
    )


def maximum_execution_manifest_size(plan: ExperimentPlan, artifact_path: str) -> int:
    """Return a conservative canonical upper bound for a terminal manifest."""

    validate_experiment_plan(plan)
    worst_error = ExperimentExecutionFailure(
        "x" * 64,
        "\U0010ffff" * MAX_EXECUTION_ERROR_CHARACTERS,
    )
    records: list[PlannedRunExecution] = []
    for spec in plan.run_specs:
        candidates = (
            _maximum_record_candidate(
                spec,
                status=PlannedRunExecutionStatus.FAILED,
                error=worst_error,
            ),
            _maximum_record_candidate(
                spec,
                status=PlannedRunExecutionStatus.COMPLETED,
                run_id="f" * 64,
                logical_result_checksum="f" * 64,
                artifact_path=artifact_path,
                verified=True,
            ),
            _maximum_record_candidate(
                spec,
                status=PlannedRunExecutionStatus.REUSED,
                run_id="f" * 64,
                logical_result_checksum="f" * 64,
                artifact_path=artifact_path,
                reused=True,
                verified=True,
            ),
        )
        records.append(
            max(
                candidates,
                key=lambda candidate: len(
                    canonical_json_bytes(planned_run_execution_payload(candidate))
                ),
            )
        )
    manifest = build_experiment_execution_manifest(
        experiment_id=plan.experiment_id,
        plan_checksum=plan.checksum,
        plan_schema_version=plan.schema_version,
        ordering_policy=plan.ordering_policy,
        records=tuple(records),
    )
    envelope = {
        "execution_manifest": experiment_execution_values_payload(
            experiment_id=manifest.experiment_id,
            plan_checksum=manifest.plan_checksum,
            plan_schema_version=manifest.plan_schema_version,
            failure_policy=manifest.failure_policy,
            warmup_policy=manifest.warmup_policy,
            ordering_policy=manifest.ordering_policy,
            total_count=manifest.total_count,
            completed_count=manifest.completed_count,
            reused_count=manifest.reused_count,
            failed_count=manifest.failed_count,
            status=manifest.status,
            records=manifest.records,
            schema_version=manifest.schema_version,
        ),
        "checksum": manifest.checksum,
        "experiment_execution_id": manifest.experiment_execution_id,
    }
    return len(canonical_json_bytes(envelope)) + _MANIFEST_AGGREGATE_MARGIN_BYTES


def _maximum_record_candidate(
    spec: PlannedRunSpec,
    *,
    status: PlannedRunExecutionStatus,
    run_id: str | None = None,
    logical_result_checksum: str | None = None,
    artifact_path: str | None = None,
    error: ExperimentExecutionFailure | None = None,
    reused: bool = False,
    verified: bool = False,
) -> PlannedRunExecution:
    return build_planned_run_execution(
        run_spec_id=spec.run_spec_id,
        experiment_id=spec.experiment_id,
        global_index=spec.global_index,
        combination_index=spec.combination.index,
        combination_id=spec.combination.combination_id,
        segment_index=spec.segment.index,
        segment_id=spec.segment.segment_id,
        purpose=spec.purpose,
        status=status,
        run_id=run_id,
        logical_result_checksum=logical_result_checksum,
        artifact_path=artifact_path,
        error=error,
        reused=reused,
        verified=verified,
    )


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise IncompatibleExperimentExecutionDocumentError(f"{label} is invalid")


def _validate_planned_run_execution_values(
    *,
    run_spec_id: object,
    experiment_id: object,
    global_index: object,
    combination_index: object,
    combination_id: object,
    segment_index: object,
    segment_id: object,
    purpose: object,
    status: object,
    run_id: object,
    logical_result_checksum: object,
    artifact_path: object,
    error: object,
    reused: object,
    verified: object,
    schema_version: object,
) -> None:
    for value, label in (
        (run_spec_id, "run spec id"),
        (experiment_id, "experiment id"),
        (combination_id, "combination id"),
        (segment_id, "segment id"),
    ):
        _require_sha256(value, label)
    indexes = (global_index, combination_index, segment_index)
    if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in indexes):
        raise IncompatibleExperimentExecutionDocumentError("record index is invalid")
    assert isinstance(global_index, int)
    assert isinstance(combination_index, int)
    assert isinstance(segment_index, int)
    if segment_index not in range(len(_PURPOSE_BY_SEGMENT_INDEX)):
        raise IncompatibleExperimentExecutionDocumentError("record segment index is invalid")
    if global_index != combination_index * 3 + segment_index:
        raise IncompatibleExperimentExecutionDocumentError(
            "record global index diverges from combination and segment"
        )
    if not isinstance(purpose, ExperimentRunPurpose):
        raise IncompatibleExperimentExecutionDocumentError("record purpose is invalid")
    if purpose is not _PURPOSE_BY_SEGMENT_INDEX[segment_index]:
        raise IncompatibleExperimentExecutionDocumentError("record purpose is invalid")
    if not isinstance(status, PlannedRunExecutionStatus):
        raise IncompatibleExperimentExecutionDocumentError("record status is invalid")
    if not isinstance(reused, bool) or not isinstance(verified, bool):
        raise IncompatibleExperimentExecutionDocumentError("record flags are invalid")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in SUPPORTED_EXPERIMENT_EXECUTION_SCHEMA_VERSIONS
    ):
        raise IncompatibleExperimentExecutionDocumentError("record schema is unsupported")
    for value, label in (
        (run_id, "run id"),
        (logical_result_checksum, "logical result checksum"),
        (artifact_path, "artifact path"),
    ):
        if value is not None and not isinstance(value, str):
            raise IncompatibleExperimentExecutionDocumentError(f"{label} is invalid")
    if error is not None:
        if not isinstance(error, ExperimentExecutionFailure):
            raise IncompatibleExperimentExecutionDocumentError("failure contract is invalid")
        validate_experiment_execution_failure(error)
    terminal_success = status in {
        PlannedRunExecutionStatus.COMPLETED,
        PlannedRunExecutionStatus.REUSED,
    }
    if terminal_success:
        if not isinstance(run_id, str):
            raise IncompatibleExperimentExecutionDocumentError(
                "successful execution record is inconsistent"
            )
        if (
            _SHA256.fullmatch(run_id) is None
            or not isinstance(logical_result_checksum, str)
            or _SHA256.fullmatch(logical_result_checksum) is None
            or not isinstance(artifact_path, str)
            or not _valid_artifact_path(artifact_path, run_id)
            or error is not None
            or not verified
            or reused is not (status is PlannedRunExecutionStatus.REUSED)
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "successful execution record is inconsistent"
            )
    elif status is PlannedRunExecutionStatus.FAILED:
        if (
            any(value is not None for value in (run_id, logical_result_checksum, artifact_path))
            or error is None
            or reused
            or verified
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "failed execution record is inconsistent"
            )
    else:
        raise IncompatibleExperimentExecutionDocumentError("manifest record is not terminal")


def _validate_record_collection(
    records: object,
    *,
    experiment_id: str,
) -> tuple[PlannedRunExecution, ...]:
    if not isinstance(records, tuple):
        raise IncompatibleExperimentExecutionDocumentError("records must be a tuple")
    if not records:
        raise IncompatibleExperimentExecutionDocumentError("execution cardinality is invalid")
    for record in records:
        validate_planned_run_execution(record)
    if tuple(record.global_index for record in records) != tuple(range(len(records))):
        raise IncompatibleExperimentExecutionDocumentError("records are out of canonical order")
    if len({record.run_spec_id for record in records}) != len(records):
        raise IncompatibleExperimentExecutionDocumentError("record references are duplicated")
    if len({record.execution_record_id for record in records}) != len(records):
        raise IncompatibleExperimentExecutionDocumentError("record identities are duplicated")
    if any(record.experiment_id != experiment_id for record in records):
        raise IncompatibleExperimentExecutionDocumentError("record experiment diverges")
    seen_combination_ids: set[str] = set()
    for offset in range(0, len(records), len(_PURPOSE_BY_SEGMENT_INDEX)):
        group = records[offset : offset + len(_PURPOSE_BY_SEGMENT_INDEX)]
        combination_index = offset // len(_PURPOSE_BY_SEGMENT_INDEX)
        if (
            len(group) != len(_PURPOSE_BY_SEGMENT_INDEX)
            or group[0].combination_id in seen_combination_ids
            or any(record.combination_index != combination_index for record in group)
            or tuple(record.segment_index for record in group) != (0, 1, 2)
            or len({record.combination_id for record in group}) != 1
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "record groups are out of canonical order"
            )
        seen_combination_ids.add(group[0].combination_id)
    return records


def _validate_manifest_build_arguments(
    *,
    experiment_id: object,
    plan_checksum: object,
    plan_schema_version: object,
    ordering_policy: object,
    records: object,
) -> None:
    _require_sha256(experiment_id, "experiment id")
    _require_sha256(plan_checksum, "plan checksum")
    if (
        isinstance(plan_schema_version, bool)
        or not isinstance(plan_schema_version, int)
        or plan_schema_version not in SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS
    ):
        raise IncompatibleExperimentExecutionDocumentError("plan schema is unsupported")
    if not isinstance(ordering_policy, ExperimentOrderingPolicy):
        raise IncompatibleExperimentExecutionDocumentError("ordering policy is invalid")
    assert isinstance(experiment_id, str)
    _validate_record_collection(records, experiment_id=experiment_id)


def _validate_experiment_execution_values(
    *,
    experiment_id: object,
    plan_checksum: object,
    plan_schema_version: object,
    failure_policy: object,
    warmup_policy: object,
    ordering_policy: object,
    total_count: object,
    completed_count: object,
    reused_count: object,
    failed_count: object,
    status: object,
    records: object,
    schema_version: object,
) -> None:
    _validate_manifest_build_arguments(
        experiment_id=experiment_id,
        plan_checksum=plan_checksum,
        plan_schema_version=plan_schema_version,
        ordering_policy=ordering_policy,
        records=records,
    )
    if failure_policy is not ExperimentFailurePolicy.CONTINUE_AFTER_FAILURE:
        raise IncompatibleExperimentExecutionDocumentError("failure policy is invalid")
    if warmup_policy is not ExperimentWarmupPolicy.WARMUP_OBSERVATION_ONLY:
        raise IncompatibleExperimentExecutionDocumentError("warmup policy is invalid")
    if not isinstance(status, ExperimentExecutionStatus):
        raise IncompatibleExperimentExecutionDocumentError("execution status is invalid")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in SUPPORTED_EXPERIMENT_EXECUTION_SCHEMA_VERSIONS
    ):
        raise IncompatibleExperimentExecutionDocumentError("execution schema is unsupported")
    counts = (total_count, completed_count, reused_count, failed_count)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise IncompatibleExperimentExecutionDocumentError("execution counts are invalid")
    assert isinstance(records, tuple)
    assert isinstance(total_count, int)
    assert isinstance(completed_count, int)
    assert isinstance(reused_count, int)
    assert isinstance(failed_count, int)
    if total_count != len(records):
        raise IncompatibleExperimentExecutionDocumentError("execution cardinality is invalid")
    expected_completed = sum(
        record.status is PlannedRunExecutionStatus.COMPLETED for record in records
    )
    expected_reused = sum(record.status is PlannedRunExecutionStatus.REUSED for record in records)
    expected_failed = sum(record.status is PlannedRunExecutionStatus.FAILED for record in records)
    expected_status = (
        ExperimentExecutionStatus.COMPLETED
        if expected_failed == 0
        else ExperimentExecutionStatus.FAILED
        if expected_failed == total_count
        else ExperimentExecutionStatus.PARTIALLY_FAILED
    )
    if (
        completed_count,
        reused_count,
        failed_count,
        status,
    ) != (expected_completed, expected_reused, expected_failed, expected_status):
        raise IncompatibleExperimentExecutionDocumentError("aggregate status is inconsistent")


def validate_planned_run_execution(record: PlannedRunExecution) -> None:
    if not isinstance(record, PlannedRunExecution):
        raise IncompatibleExperimentExecutionDocumentError("record contract is invalid")
    if (
        not isinstance(record.purpose, ExperimentRunPurpose)
        or not isinstance(record.status, PlannedRunExecutionStatus)
        or not isinstance(record.reused, bool)
        or not isinstance(record.verified, bool)
    ):
        raise IncompatibleExperimentExecutionDocumentError("record contract is invalid")
    if (
        isinstance(record.schema_version, bool)
        or not isinstance(record.schema_version, int)
        or record.schema_version not in SUPPORTED_EXPERIMENT_EXECUTION_SCHEMA_VERSIONS
    ):
        raise IncompatibleExperimentExecutionDocumentError("record schema is unsupported")
    indexes = (record.global_index, record.combination_index, record.segment_index)
    if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in indexes):
        raise IncompatibleExperimentExecutionDocumentError("record index is invalid")
    if record.segment_index not in range(len(_PURPOSE_BY_SEGMENT_INDEX)):
        raise IncompatibleExperimentExecutionDocumentError("record segment index is invalid")
    if record.global_index != record.combination_index * 3 + record.segment_index:
        raise IncompatibleExperimentExecutionDocumentError(
            "record global index diverges from combination and segment"
        )
    if record.purpose is not _PURPOSE_BY_SEGMENT_INDEX[record.segment_index]:
        raise IncompatibleExperimentExecutionDocumentError("record purpose is invalid")
    for identifier in (
        record.run_spec_id,
        record.experiment_id,
        record.combination_id,
        record.segment_id,
        record.checksum,
        record.execution_record_id,
    ):
        if not isinstance(identifier, str) or _SHA256.fullmatch(identifier) is None:
            raise IncompatibleExperimentExecutionDocumentError("record identifier is invalid")
    if record.error is not None:
        validate_experiment_execution_failure(record.error)
    terminal_success = record.status in {
        PlannedRunExecutionStatus.COMPLETED,
        PlannedRunExecutionStatus.REUSED,
    }
    if terminal_success:
        if (
            not isinstance(record.run_id, str)
            or _SHA256.fullmatch(record.run_id) is None
            or not isinstance(record.logical_result_checksum, str)
            or _SHA256.fullmatch(record.logical_result_checksum) is None
            or not isinstance(record.artifact_path, str)
            or not _valid_artifact_path(record.artifact_path, record.run_id)
            or record.error is not None
            or not record.verified
            or record.reused is not (record.status is PlannedRunExecutionStatus.REUSED)
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "successful execution record is inconsistent"
            )
    elif record.status is PlannedRunExecutionStatus.FAILED:
        if (
            any(
                value is not None
                for value in (record.run_id, record.logical_result_checksum, record.artifact_path)
            )
            or record.error is None
            or record.reused
            or record.verified
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "failed execution record is inconsistent"
            )
    else:
        raise IncompatibleExperimentExecutionDocumentError("manifest record is not terminal")
    values = planned_run_execution_values_payload(
        run_spec_id=record.run_spec_id,
        experiment_id=record.experiment_id,
        global_index=record.global_index,
        combination_index=record.combination_index,
        combination_id=record.combination_id,
        segment_index=record.segment_index,
        segment_id=record.segment_id,
        purpose=record.purpose,
        status=record.status,
        run_id=record.run_id,
        logical_result_checksum=record.logical_result_checksum,
        artifact_path=record.artifact_path,
        error=record.error,
        reused=record.reused,
        verified=record.verified,
        schema_version=record.schema_version,
    )
    if record.checksum != document_checksum(values):
        raise IncompatibleExperimentExecutionDocumentError("record checksum does not match")
    expected_id = deterministic_id(
        "adt-experiment-run-execution-v1",
        {"run_spec_id": record.run_spec_id, "checksum": record.checksum},
    )
    if record.execution_record_id != expected_id:
        raise IncompatibleExperimentExecutionDocumentError("record id does not match")


def validate_experiment_execution_manifest(manifest: ExperimentExecutionManifest) -> None:
    if not isinstance(manifest, ExperimentExecutionManifest):
        raise IncompatibleExperimentExecutionDocumentError("execution contract is invalid")
    if (
        not isinstance(manifest.failure_policy, ExperimentFailurePolicy)
        or not isinstance(manifest.warmup_policy, ExperimentWarmupPolicy)
        or not isinstance(manifest.ordering_policy, ExperimentOrderingPolicy)
        or not isinstance(manifest.status, ExperimentExecutionStatus)
        or not isinstance(manifest.records, tuple)
    ):
        raise IncompatibleExperimentExecutionDocumentError("execution contract is invalid")
    if (
        isinstance(manifest.schema_version, bool)
        or not isinstance(manifest.schema_version, int)
        or manifest.schema_version not in SUPPORTED_EXPERIMENT_EXECUTION_SCHEMA_VERSIONS
    ):
        raise IncompatibleExperimentExecutionDocumentError("execution schema is unsupported")
    if (
        isinstance(manifest.plan_schema_version, bool)
        or not isinstance(manifest.plan_schema_version, int)
        or manifest.plan_schema_version not in SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS
    ):
        raise IncompatibleExperimentExecutionDocumentError("plan schema is unsupported")
    counts = (
        manifest.total_count,
        manifest.completed_count,
        manifest.reused_count,
        manifest.failed_count,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in counts):
        raise IncompatibleExperimentExecutionDocumentError("execution counts are invalid")
    if (
        not isinstance(manifest.experiment_id, str)
        or _SHA256.fullmatch(manifest.experiment_id) is None
        or not isinstance(manifest.plan_checksum, str)
        or _SHA256.fullmatch(manifest.plan_checksum) is None
        or not isinstance(manifest.checksum, str)
        or not isinstance(manifest.experiment_execution_id, str)
    ):
        raise IncompatibleExperimentExecutionDocumentError("plan reference is invalid")
    if manifest.total_count < 1 or manifest.total_count != len(manifest.records):
        raise IncompatibleExperimentExecutionDocumentError("execution cardinality is invalid")
    for record in manifest.records:
        validate_planned_run_execution(record)
    if tuple(record.global_index for record in manifest.records) != tuple(
        range(manifest.total_count)
    ):
        raise IncompatibleExperimentExecutionDocumentError("records are out of canonical order")
    if len({record.run_spec_id for record in manifest.records}) != manifest.total_count:
        raise IncompatibleExperimentExecutionDocumentError("record references are duplicated")
    if len({record.execution_record_id for record in manifest.records}) != manifest.total_count:
        raise IncompatibleExperimentExecutionDocumentError("record identities are duplicated")
    if any(record.experiment_id != manifest.experiment_id for record in manifest.records):
        raise IncompatibleExperimentExecutionDocumentError("record experiment diverges")
    seen_combinations: set[int] = set()
    seen_combination_ids: set[str] = set()
    for offset in range(0, manifest.total_count, len(_PURPOSE_BY_SEGMENT_INDEX)):
        group = manifest.records[offset : offset + len(_PURPOSE_BY_SEGMENT_INDEX)]
        combination_index = offset // len(_PURPOSE_BY_SEGMENT_INDEX)
        if (
            len(group) != len(_PURPOSE_BY_SEGMENT_INDEX)
            or combination_index in seen_combinations
            or group[0].combination_id in seen_combination_ids
            or any(record.combination_index != combination_index for record in group)
            or tuple(record.segment_index for record in group) != (0, 1, 2)
            or len({record.combination_id for record in group}) != 1
            or any(
                record.global_index != record.combination_index * 3 + record.segment_index
                for record in group
            )
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "record groups are out of canonical order"
            )
        seen_combinations.add(combination_index)
        seen_combination_ids.add(group[0].combination_id)
    completed = sum(
        record.status is PlannedRunExecutionStatus.COMPLETED for record in manifest.records
    )
    reused = sum(record.status is PlannedRunExecutionStatus.REUSED for record in manifest.records)
    failed = sum(record.status is PlannedRunExecutionStatus.FAILED for record in manifest.records)
    expected_status = (
        ExperimentExecutionStatus.COMPLETED
        if failed == 0
        else ExperimentExecutionStatus.FAILED
        if failed == manifest.total_count
        else ExperimentExecutionStatus.PARTIALLY_FAILED
    )
    if (completed, reused, failed, expected_status) != (
        manifest.completed_count,
        manifest.reused_count,
        manifest.failed_count,
        manifest.status,
    ) or completed + reused + failed != manifest.total_count:
        raise IncompatibleExperimentExecutionDocumentError("aggregate status is inconsistent")
    values = experiment_execution_values_payload(
        experiment_id=manifest.experiment_id,
        plan_checksum=manifest.plan_checksum,
        plan_schema_version=manifest.plan_schema_version,
        failure_policy=manifest.failure_policy,
        warmup_policy=manifest.warmup_policy,
        ordering_policy=manifest.ordering_policy,
        total_count=manifest.total_count,
        completed_count=manifest.completed_count,
        reused_count=manifest.reused_count,
        failed_count=manifest.failed_count,
        status=manifest.status,
        records=manifest.records,
        schema_version=manifest.schema_version,
    )
    if manifest.checksum != document_checksum(values):
        raise IncompatibleExperimentExecutionDocumentError("execution checksum does not match")
    expected_id = deterministic_id(
        "adt-experiment-execution-v1",
        {"experiment_id": manifest.experiment_id, "checksum": manifest.checksum},
    )
    if manifest.experiment_execution_id != expected_id:
        raise IncompatibleExperimentExecutionDocumentError("execution id does not match")


def validate_execution_manifest_against_plan(
    manifest: ExperimentExecutionManifest,
    plan: ExperimentPlan,
    snapshot: DatasetSnapshot,
) -> None:
    """Reconcile every immutable execution reference with its validated plan."""

    validate_experiment_execution_manifest(manifest)
    try:
        validate_experiment_plan(plan)
    except ExperimentPlanningError as error:
        raise IncompatibleExperimentExecutionDocumentError(error.message) from error
    except Exception as error:
        raise IncompatibleExperimentExecutionDocumentError(
            "experiment plan contract is invalid"
        ) from error
    if not isinstance(snapshot, DatasetSnapshot):
        raise IncompatibleExperimentExecutionDocumentError("dataset snapshot contract is invalid")
    if (
        snapshot.snapshot_id != plan.snapshot.snapshot_id
        or snapshot.checksum != plan.snapshot.snapshot_checksum
        or snapshot.dataset_key != plan.snapshot.dataset_key
        or snapshot.dataset_version != plan.snapshot.dataset_version
        or snapshot.data_range != plan.snapshot.available_coverage.data_range
    ):
        raise IncompatibleExperimentExecutionDocumentError(
            "dataset snapshot diverges from the execution plan"
        )
    if (
        manifest.experiment_id != plan.experiment_id
        or manifest.plan_checksum != plan.checksum
        or manifest.plan_schema_version != plan.schema_version
        or manifest.ordering_policy is not plan.ordering_policy
        or manifest.total_count != plan.cardinality
        or len(manifest.records) != len(plan.run_specs)
    ):
        raise IncompatibleExperimentExecutionDocumentError(
            "execution manifest diverges from its plan"
        )
    for record, spec in zip(manifest.records, plan.run_specs, strict=True):
        if (
            record.run_spec_id != spec.run_spec_id
            or record.experiment_id != spec.experiment_id
            or record.global_index != spec.global_index
            or record.combination_index != spec.combination.index
            or record.combination_id != spec.combination.combination_id
            or record.segment_index != spec.segment.index
            or record.segment_id != spec.segment.segment_id
            or record.purpose is not spec.purpose
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "an execution record diverges from its planned run"
            )
        if record.status in {
            PlannedRunExecutionStatus.COMPLETED,
            PlannedRunExecutionStatus.REUSED,
        }:
            expected_run_id = build_run_id(spec.backtest_config, snapshot).value
            if record.run_id != expected_run_id:
                raise IncompatibleExperimentExecutionDocumentError(
                    "execution run id diverges from its planned backtest"
                )


def _valid_artifact_path(value: str, run_id: str) -> bool:
    return is_canonical_artifact_path(value, run_id)
