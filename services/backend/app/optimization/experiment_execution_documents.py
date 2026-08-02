"""Strict canonical codec for experiment execution manifests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.optimization.canonical import canonical_json_bytes
from app.optimization.errors import (
    ExperimentExecutionChecksumError,
    ExperimentExecutionIdentifierError,
    IncompatibleExperimentExecutionDocumentError,
    UnsupportedExperimentExecutionSchemaError,
)
from app.optimization.experiment_domain import (
    ExperimentOrderingPolicy,
    ExperimentRunPurpose,
)
from app.optimization.experiment_execution_domain import (
    SUPPORTED_EXPERIMENT_EXECUTION_SCHEMA_VERSIONS,
    ExperimentExecutionFailure,
    ExperimentExecutionManifest,
    ExperimentExecutionStatus,
    ExperimentFailurePolicy,
    ExperimentWarmupPolicy,
    PlannedRunExecution,
    PlannedRunExecutionStatus,
    build_experiment_execution_manifest,
    build_planned_run_execution,
    experiment_execution_values_payload,
    validate_experiment_execution_manifest,
)


def experiment_execution_to_document(
    manifest: ExperimentExecutionManifest,
) -> dict[str, object]:
    validate_experiment_execution_manifest(manifest)
    return {
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


def canonical_experiment_execution_document_bytes(
    manifest: ExperimentExecutionManifest,
) -> bytes:
    validate_experiment_execution_manifest(manifest)
    return canonical_json_bytes(experiment_execution_to_document(manifest))


def decode_experiment_execution_document(
    envelope: Mapping[str, object],
) -> ExperimentExecutionManifest:
    root = _mapping(envelope, "execution envelope")
    _exact(root, {"execution_manifest", "checksum", "experiment_execution_id"})
    payload = _mapping(root["execution_manifest"], "execution manifest")
    _exact(
        payload,
        {
            "schema_version",
            "experiment_id",
            "plan_checksum",
            "plan_schema_version",
            "failure_policy",
            "warmup_policy",
            "ordering_policy",
            "total_count",
            "completed_count",
            "reused_count",
            "failed_count",
            "status",
            "records",
        },
    )
    schema_version = _integer(payload["schema_version"])
    if schema_version not in SUPPORTED_EXPERIMENT_EXECUTION_SCHEMA_VERSIONS:
        raise UnsupportedExperimentExecutionSchemaError()
    records = tuple(_decode_record(item) for item in _sequence(payload["records"]))
    try:
        built = build_experiment_execution_manifest(
            experiment_id=_text(payload["experiment_id"]),
            plan_checksum=_text(payload["plan_checksum"]),
            plan_schema_version=_integer(payload["plan_schema_version"]),
            ordering_policy=ExperimentOrderingPolicy(_text(payload["ordering_policy"])),
            records=records,
        )
        supplied_policy = ExperimentFailurePolicy(_text(payload["failure_policy"]))
        supplied_warmup = ExperimentWarmupPolicy(_text(payload["warmup_policy"]))
        supplied_status = ExperimentExecutionStatus(_text(payload["status"]))
    except (TypeError, ValueError) as error:
        raise IncompatibleExperimentExecutionDocumentError(str(error)) from None
    expected_values = (
        built.failure_policy,
        built.warmup_policy,
        built.status,
        built.total_count,
        built.completed_count,
        built.reused_count,
        built.failed_count,
    )
    supplied_values = (
        supplied_policy,
        supplied_warmup,
        supplied_status,
        _integer(payload["total_count"]),
        _integer(payload["completed_count"]),
        _integer(payload["reused_count"]),
        _integer(payload["failed_count"]),
    )
    if supplied_values != expected_values:
        raise IncompatibleExperimentExecutionDocumentError(
            "execution aggregate diverges from records"
        )
    if _text(root["checksum"]) != built.checksum:
        raise ExperimentExecutionChecksumError()
    if _text(root["experiment_execution_id"]) != built.experiment_execution_id:
        raise ExperimentExecutionIdentifierError()
    return built


def _decode_record(raw: object) -> PlannedRunExecution:
    value = _mapping(raw, "execution record")
    _exact(
        value,
        {
            "schema_version",
            "run_spec_id",
            "experiment_id",
            "global_index",
            "combination_index",
            "combination_id",
            "segment_index",
            "segment_id",
            "purpose",
            "status",
            "run_id",
            "logical_result_checksum",
            "artifact_path",
            "error",
            "reused",
            "verified",
            "checksum",
            "execution_record_id",
        },
    )
    if _integer(value["schema_version"]) not in SUPPORTED_EXPERIMENT_EXECUTION_SCHEMA_VERSIONS:
        raise UnsupportedExperimentExecutionSchemaError()
    error_raw = value["error"]
    error = None
    if error_raw is not None:
        error_value = _mapping(error_raw, "execution failure")
        _exact(error_value, {"code", "message"})
        error = ExperimentExecutionFailure(
            code=_text(error_value["code"]), message=_text(error_value["message"])
        )
    try:
        built = build_planned_run_execution(
            run_spec_id=_text(value["run_spec_id"]),
            experiment_id=_text(value["experiment_id"]),
            global_index=_integer(value["global_index"]),
            combination_index=_integer(value["combination_index"]),
            combination_id=_text(value["combination_id"]),
            segment_index=_integer(value["segment_index"]),
            segment_id=_text(value["segment_id"]),
            purpose=ExperimentRunPurpose(_text(value["purpose"])),
            status=PlannedRunExecutionStatus(_text(value["status"])),
            run_id=_optional_text(value["run_id"]),
            logical_result_checksum=_optional_text(value["logical_result_checksum"]),
            artifact_path=_optional_text(value["artifact_path"]),
            error=error,
            reused=_boolean(value["reused"]),
            verified=_boolean(value["verified"]),
        )
    except (TypeError, ValueError) as error_value:
        raise IncompatibleExperimentExecutionDocumentError(str(error_value)) from None
    if _text(value["checksum"]) != built.checksum:
        raise ExperimentExecutionChecksumError("execution record checksum does not match")
    if _text(value["execution_record_id"]) != built.execution_record_id:
        raise ExperimentExecutionIdentifierError("execution record id does not match")
    return built


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
        raise IncompatibleExperimentExecutionDocumentError(f"{label} must be an object")
    return raw


def _sequence(raw: object) -> Sequence[object]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise IncompatibleExperimentExecutionDocumentError("records must be an array")
    return raw


def _exact(raw: Mapping[str, object], fields: set[str]) -> None:
    if set(raw) != fields:
        raise IncompatibleExperimentExecutionDocumentError("document fields are incompatible")


def _text(raw: object) -> str:
    if not isinstance(raw, str):
        raise IncompatibleExperimentExecutionDocumentError("document text is invalid")
    return raw


def _optional_text(raw: object) -> str | None:
    return None if raw is None else _text(raw)


def _integer(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise IncompatibleExperimentExecutionDocumentError("document integer is invalid")
    return raw


def _boolean(raw: object) -> bool:
    if not isinstance(raw, bool):
        raise IncompatibleExperimentExecutionDocumentError("document boolean is invalid")
    return raw
