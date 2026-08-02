"""Phase 4-04 deterministic local experiment execution tests."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from app.backtesting.artifacts import BacktestArtifactStore, build_run_id
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.serialization import read_json_envelope
from app.backtesting.verifier import BacktestResultVerifier
from app.optimization import (
    ExperimentExecutionArtifactVerificationError,
    ExperimentExecutionChecksumError,
    ExperimentExecutionFailure,
    ExperimentExecutionLimitExceededError,
    ExperimentExecutionManifest,
    ExperimentExecutionManifestLimitExceededError,
    ExperimentExecutionRepository,
    ExperimentExecutionService,
    ExperimentExecutionStatus,
    ExperimentPlan,
    ExperimentPlanningService,
    IncompatibleExperimentExecutionDocumentError,
    InvalidExperimentExecutionPlanError,
    InvalidExperimentExecutionTransitionError,
    ParameterSearchService,
    PlannedRunExecution,
    PlannedRunExecutionStatus,
    build_experiment_execution_manifest,
    build_planned_run_execution,
    canonical_experiment_execution_document_bytes,
    decode_experiment_execution_document,
    experiment_execution_to_document,
    maximum_execution_manifest_size,
    validate_execution_manifest_against_plan,
    validate_execution_transition,
    validate_experiment_execution_manifest,
    verify_published_execution_manifest,
)
from app.optimization import experiment_execution_repository as execution_repository_module
from app.optimization.errors import ExperimentExecutionPublicationError
from app.optimization.experiment_execution_domain import (
    MAX_EXECUTION_MANIFEST_BYTES,
    ExperimentFailurePolicy,
    ExperimentWarmupPolicy,
    experiment_execution_values_payload,
    failure_payload,
)
from app.strategies.registry import StrategyPluginRegistry
from tests.test_backtesting_engine import FakeSnapshotReader, _candles
from tests.test_optimization_experiment_planning import (
    _configuration,
    _contracts,
    _LifecyclePlanningPlugin,
    _plan,
    _temporal,
)


def _service(
    tmp_path: Path,
    *,
    fail_calls: set[int] | None = None,
    max_specs: int = 3_000,
    max_manifest_bytes: int = 16 * 1024 * 1024,
    contract_calls: list[int] | None = None,
    registry: StrategyPluginRegistry | None = None,
) -> tuple[ExperimentExecutionService, list[int]]:
    plan, snapshot, manifest = _plan()
    rows = _candles(*(str(100 + index) for index in range(12)))
    calls: list[int] = []

    def engine_factory() -> DeterministicBacktestEngine:
        calls.append(len(calls))
        if fail_calls and calls[-1] in fail_calls:
            return _FailingEngine()  # type: ignore[return-value]
        return DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows))

    snapshot_factory = lambda _data_dir: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    store = BacktestArtifactStore(tmp_path, snapshot_factory=snapshot_factory)
    verifier = BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory)

    def load_contracts(_plan_value: object) -> object:
        if contract_calls is not None:
            contract_calls.append(1)
        return snapshot, manifest

    service = ExperimentExecutionService(
        tmp_path,
        max_specs=max_specs,
        max_manifest_bytes=max_manifest_bytes,
        engine_factory=engine_factory,
        artifact_store=store,
        result_verifier=verifier,
        contract_loader=load_contracts,  # type: ignore[arg-type]
        registry=registry,
        available_indicators=() if registry is not None else None,
    )
    return service, calls


def _custom_lifecycle_plan(
    lifecycle_version: int,
) -> tuple[ExperimentPlan, StrategyPluginRegistry]:
    snapshot, manifest = _contracts()
    registry = StrategyPluginRegistry((_LifecyclePlanningPlugin(lifecycle_version),))
    search_space = ParameterSearchService(registry, available_indicators=()).create(
        "lifecycle-planning",
        "1",
        {"period": [1]},
    )
    plan = ExperimentPlanningService(registry, available_indicators=()).create(
        snapshot,
        manifest,
        _temporal(snapshot, manifest, warmup=0),
        search_space,
        plugin_name="lifecycle-planning",
        plugin_version="1",
        backtest_configuration=_configuration(),
    )
    return plan, registry


class _FailingEngine:
    def run(self, _config: object, _strategy: object) -> object:
        raise RuntimeError("sensitive implementation detail")


def _failed_manifest(plan: ExperimentPlan) -> ExperimentExecutionManifest:
    records = tuple(
        build_planned_run_execution(
            run_spec_id=spec.run_spec_id,
            experiment_id=spec.experiment_id,
            global_index=spec.global_index,
            combination_index=spec.combination.index,
            combination_id=spec.combination.combination_id,
            segment_index=spec.segment.index,
            segment_id=spec.segment.segment_id,
            purpose=spec.purpose,
            status=PlannedRunExecutionStatus.FAILED,
            error=ExperimentExecutionFailure("failed", "bounded failure"),
        )
        for spec in plan.run_specs
    )
    return build_experiment_execution_manifest(
        experiment_id=plan.experiment_id,
        plan_checksum=plan.checksum,
        plan_schema_version=plan.schema_version,
        ordering_policy=plan.ordering_policy,
        records=records,
    )


def _rebuild_success_record(
    record: PlannedRunExecution,
    *,
    run_id: str | None = None,
    logical_checksum: str | None = None,
    artifact_path: str | None = None,
) -> PlannedRunExecution:
    selected_run_id = record.run_id if run_id is None else run_id
    return build_planned_run_execution(
        run_spec_id=record.run_spec_id,
        experiment_id=record.experiment_id,
        global_index=record.global_index,
        combination_index=record.combination_index,
        combination_id=record.combination_id,
        segment_index=record.segment_index,
        segment_id=record.segment_id,
        purpose=record.purpose,
        status=record.status,
        run_id=selected_run_id,
        logical_result_checksum=(
            record.logical_result_checksum if logical_checksum is None else logical_checksum
        ),
        artifact_path=record.artifact_path if artifact_path is None else artifact_path,
        reused=record.reused,
        verified=record.verified,
    )


def _rebuild_manifest(
    execution: ExperimentExecutionManifest,
    records: tuple[PlannedRunExecution, ...],
) -> ExperimentExecutionManifest:
    return build_experiment_execution_manifest(
        experiment_id=execution.experiment_id,
        plan_checksum=execution.plan_checksum,
        plan_schema_version=execution.plan_schema_version,
        ordering_policy=execution.ordering_policy,
        records=records,
    )


def test_executor_runs_sequentially_and_publishes_verified_results(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    service, calls = _service(tmp_path)

    publication = service.execute(plan)

    execution = publication.manifest
    assert execution.status is ExperimentExecutionStatus.COMPLETED
    assert execution.total_count == plan.cardinality == len(calls)
    assert execution.completed_count == plan.cardinality
    assert execution.reused_count == execution.failed_count == 0
    assert [record.global_index for record in execution.records] == list(range(plan.cardinality))
    assert all(record.verified for record in execution.records)
    assert all(record.status is PlannedRunExecutionStatus.COMPLETED for record in execution.records)
    assert all(not Path(record.artifact_path or "").is_absolute() for record in execution.records)
    assert publication.relative_path.parts[:2] == ("optimization", "experiments")
    for spec, record in zip(plan.run_specs, execution.records, strict=True):
        result = read_json_envelope(
            tmp_path / "market" / (record.artifact_path or "") / "result.json",
            "result",
        )
        assert result["candles_processed"] == spec.segment.candle_count


def test_rerun_verifies_and_reuses_without_engine_execution(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    first_service, first_calls = _service(tmp_path)
    first_service.execute(plan)
    assert len(first_calls) == plan.cardinality
    second_service, second_calls = _service(tmp_path)

    second = second_service.execute(plan).manifest

    assert second.status is ExperimentExecutionStatus.COMPLETED
    assert second.reused_count == plan.cardinality
    assert second.completed_count == second.failed_count == 0
    assert second_calls == []
    assert all(record.status is PlannedRunExecutionStatus.REUSED for record in second.records)


def test_builtin_version_one_plan_executes_with_zero_warmup(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan(
        values=(2,),
        warmup=0,
        plugin_version="1",
    )
    service, calls = _service(tmp_path)

    execution = service.execute(plan).manifest

    assert execution.status is ExperimentExecutionStatus.COMPLETED
    assert len(calls) == plan.cardinality
    assert all(spec.plugin.version == "1" for spec in plan.run_specs)
    assert all(record.status is PlannedRunExecutionStatus.COMPLETED for record in execution.records)


def test_versioned_builtin_identity_changes_run_ids_and_prevents_reuse(
    tmp_path: Path,
) -> None:
    version_one, snapshot_one, _manifest_one = _plan(
        values=(2,),
        warmup=0,
        plugin_version="1",
    )
    version_two, snapshot_two, _manifest_two = _plan(
        values=(2,),
        warmup=0,
        plugin_version="2",
    )
    assert snapshot_one == snapshot_two
    expected_one = tuple(
        build_run_id(spec.backtest_config, snapshot_one).value for spec in version_one.run_specs
    )
    expected_two = tuple(
        build_run_id(spec.backtest_config, snapshot_two).value for spec in version_two.run_specs
    )

    first_service, first_calls = _service(tmp_path)
    first = first_service.execute(version_one).manifest
    second_service, second_calls = _service(tmp_path)
    second = second_service.execute(version_two).manifest

    assert version_one.experiment_id != version_two.experiment_id
    assert set(expected_one).isdisjoint(expected_two)
    assert tuple(record.run_id for record in first.records) == expected_one
    assert tuple(record.run_id for record in second.records) == expected_two
    assert len(first_calls) == len(second_calls) == version_one.cardinality
    assert second.reused_count == 0
    assert all(record.status is PlannedRunExecutionStatus.COMPLETED for record in second.records)


def test_custom_same_version_different_lifecycle_never_reuses_artifacts(
    tmp_path: Path,
) -> None:
    lifecycle_one, registry_one = _custom_lifecycle_plan(1)
    lifecycle_two, registry_two = _custom_lifecycle_plan(2)
    snapshot, _manifest = _contracts()

    first_service, first_calls = _service(tmp_path, registry=registry_one)
    first = first_service.execute(lifecycle_one).manifest
    second_service, second_calls = _service(tmp_path, registry=registry_two)
    second = second_service.execute(lifecycle_two).manifest

    first_run_ids = tuple(record.run_id for record in first.records)
    second_run_ids = tuple(record.run_id for record in second.records)
    assert lifecycle_one.plugin.name == lifecycle_two.plugin.name == "lifecycle-planning"
    assert lifecycle_one.plugin.version == lifecycle_two.plugin.version == "1"
    assert lifecycle_one.plugin.lifecycle_version == 1
    assert lifecycle_two.plugin.lifecycle_version == 2
    assert set(first_run_ids).isdisjoint(second_run_ids)
    assert len(first_calls) == len(second_calls) == lifecycle_one.cardinality
    assert second.reused_count == 0
    assert all(record.status is PlannedRunExecutionStatus.COMPLETED for record in second.records)

    rows = _candles(*(str(100 + index) for index in range(12)))
    snapshot_factory = lambda _data_dir: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    verifier = BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory)
    for record in first.records:
        assert record.run_id is not None
        assert verifier.verify(record.run_id).strategy_lifecycle_version == 1
    for record in second.records:
        assert record.run_id is not None
        assert verifier.verify(record.run_id).strategy_lifecycle_version == 2


def test_one_failure_is_bounded_and_does_not_stop_later_specs(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    service, calls = _service(tmp_path, fail_calls={1})

    execution = service.execute(plan).manifest

    assert len(calls) == plan.cardinality
    assert execution.status is ExperimentExecutionStatus.PARTIALLY_FAILED
    assert execution.failed_count == 1
    failed = execution.records[1]
    assert failed.status is PlannedRunExecutionStatus.FAILED
    assert failed.error is not None
    assert failed.error.code == "unexpected_execution_error"
    assert "sensitive" not in failed.error.message
    assert execution.records[2].status is PlannedRunExecutionStatus.COMPLETED


def test_all_failures_produce_failed_aggregate(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    service, _calls = _service(tmp_path, fail_calls=set(range(plan.cardinality)))

    execution = service.execute(plan).manifest

    assert execution.status is ExperimentExecutionStatus.FAILED
    assert execution.failed_count == plan.cardinality


def test_limit_rejects_before_loading_or_running_specs(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    service, calls = _service(tmp_path, max_specs=plan.cardinality - 1)

    with pytest.raises(ExperimentExecutionLimitExceededError):
        service.execute(plan)
    assert calls == []


def test_invalid_plan_rejected_before_engine(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    service, calls = _service(tmp_path)
    object.__setattr__(plan, "checksum", "0" * 64)

    with pytest.raises(InvalidExperimentExecutionPlanError):
        service.execute(plan)
    assert calls == []


def test_malformed_plan_shape_is_rejected_before_len_or_external_calls(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    contract_calls: list[int] = []
    service, engine_calls = _service(tmp_path, contract_calls=contract_calls)
    object.__setattr__(plan, "run_specs", object())

    with pytest.raises(InvalidExperimentExecutionPlanError):
        service.execute(plan)

    assert contract_calls == []
    assert engine_calls == []


def test_manifest_preflight_rejects_before_contract_engine_and_publication(
    tmp_path: Path,
) -> None:
    plan, _snapshot, _manifest = _plan()
    maximum = maximum_execution_manifest_size(plan, f"backtests/{'f' * 64}")
    contract_calls: list[int] = []
    service, engine_calls = _service(
        tmp_path,
        max_manifest_bytes=maximum - 1,
        contract_calls=contract_calls,
    )

    with pytest.raises(ExperimentExecutionManifestLimitExceededError):
        service.execute(plan)

    assert contract_calls == []
    assert engine_calls == []
    assert not (tmp_path / "market" / "optimization").exists()


def test_manifest_preflight_accepts_exact_conservative_bound(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    maximum = maximum_execution_manifest_size(plan, f"backtests/{'f' * 64}")
    service, calls = _service(tmp_path, max_manifest_bytes=maximum)

    publication = service.execute(plan)

    assert publication.manifest.total_count == plan.cardinality
    assert len(calls) == plan.cardinality


def test_corrupt_existing_result_fails_that_spec_without_overwrite(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    first_service, _calls = _service(tmp_path)
    first = first_service.execute(plan).manifest
    target = tmp_path / "market" / (first.records[0].artifact_path or "") / "config.json"
    original = target.read_bytes()
    target.write_bytes(original + b"corrupt")
    second_service, second_calls = _service(tmp_path)

    second = second_service.execute(plan).manifest

    assert second.records[0].status is PlannedRunExecutionStatus.FAILED
    assert second.records[0].error is not None
    assert second.records[0].error.code == "result_corrupt"
    assert target.read_bytes() == original + b"corrupt"
    assert second_calls == []


def test_execution_document_round_trip_is_canonical_and_clock_free(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan(values=(2,))
    service, _calls = _service(tmp_path)
    execution = service.execute(plan).manifest

    document = experiment_execution_to_document(execution)
    decoded = decode_experiment_execution_document(document)

    assert decoded == execution
    assert canonical_experiment_execution_document_bytes(decoded) == (
        canonical_experiment_execution_document_bytes(execution)
    )
    assert b"created_at" not in canonical_experiment_execution_document_bytes(execution)


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("checksum", "0" * 64, ExperimentExecutionChecksumError),
        (
            "experiment_execution_id",
            "0" * 64,
            IncompatibleExperimentExecutionDocumentError,
        ),
    ],
)
def test_execution_document_rejects_tampered_envelope(
    tmp_path: Path, field: str, value: str, error_type: type[Exception]
) -> None:
    plan, _snapshot, _manifest = _plan()
    service, _calls = _service(tmp_path)
    document = deepcopy(experiment_execution_to_document(service.execute(plan).manifest))
    document[field] = value

    with pytest.raises(error_type):
        decode_experiment_execution_document(document)


def test_execution_document_rejects_unknown_nested_fields(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    service, _calls = _service(tmp_path)
    document = deepcopy(experiment_execution_to_document(service.execute(plan).manifest))
    payload = document["execution_manifest"]
    assert isinstance(payload, dict)
    payload["unexpected"] = True

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        decode_experiment_execution_document(document)


def test_repository_is_idempotent_and_uses_committed_publication(tmp_path: Path) -> None:
    plan, _snapshot, _manifest = _plan()
    service, _calls = _service(tmp_path)
    first = service.execute(plan)
    repository = ExperimentExecutionRepository(tmp_path)

    second = repository.publish(first.manifest)
    publication_path = tmp_path / "market" / first.relative_path / "publication.json"

    assert second.reused is True
    assert second.manifest == first.manifest
    assert json.loads(publication_path.read_text("utf-8"))["state"] == "COMMITTED"
    assert {path.name for path in publication_path.parent.iterdir()} == {
        "manifest.json",
        "publication.json",
    }


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PlannedRunExecutionStatus.PENDING, PlannedRunExecutionStatus.COMPLETED),
        (PlannedRunExecutionStatus.RUNNING, PlannedRunExecutionStatus.PENDING),
        (PlannedRunExecutionStatus.COMPLETED, PlannedRunExecutionStatus.RUNNING),
        (PlannedRunExecutionStatus.FAILED, PlannedRunExecutionStatus.RUNNING),
        (PlannedRunExecutionStatus.REUSED, PlannedRunExecutionStatus.RUNNING),
    ],
)
def test_invalid_execution_state_transitions_are_rejected(
    current: PlannedRunExecutionStatus, target: PlannedRunExecutionStatus
) -> None:
    with pytest.raises(InvalidExperimentExecutionTransitionError):
        validate_execution_transition(current, target)


@pytest.mark.parametrize("current", [None, [], "PENDING", 1])
def test_malformed_execution_transition_types_are_stable(current: object) -> None:
    with pytest.raises(InvalidExperimentExecutionTransitionError):
        validate_execution_transition(
            current,  # type: ignore[arg-type]
            PlannedRunExecutionStatus.RUNNING,
        )


@pytest.mark.parametrize("payload", [[], None])
def test_repository_read_rejects_non_object_json(
    tmp_path: Path,
    payload: object,
) -> None:
    plan, _snapshot, _manifest = _plan()
    service, _calls = _service(tmp_path)
    publication = service.execute(plan)
    root = tmp_path / "market" / publication.relative_path
    (root / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        ExperimentExecutionRepository(tmp_path).read(
            plan.experiment_id,
            publication.manifest.experiment_execution_id,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("segment_index", 3),
        ("purpose", "TRAINING"),
        ("verified", 1),
        ("artifact_path", ""),
    ],
)
def test_hostile_record_mutation_is_rejected_stably(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    plan, _snapshot, _manifest = _plan()
    service, _calls = _service(tmp_path)
    execution = service.execute(plan).manifest
    object.__setattr__(execution.records[0], field, value)

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        validate_experiment_execution_manifest(execution)


@pytest.mark.parametrize(("field", "value"), [("code", 1), ("message", "bad\nmessage")])
def test_hostile_failure_mutation_is_rejected_stably(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    plan, _snapshot, _manifest = _plan()
    service, _calls = _service(tmp_path, fail_calls=set(range(plan.cardinality)))
    execution = service.execute(plan).manifest
    error = execution.records[0].error
    assert error is not None
    object.__setattr__(error, field, value)

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        validate_experiment_execution_manifest(execution)


def test_manifest_requires_canonical_three_record_groups() -> None:
    plan, _snapshot, _manifest = _plan()
    spec = plan.run_specs[0]

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        build_planned_run_execution(
            run_spec_id=spec.run_spec_id,
            experiment_id=spec.experiment_id,
            global_index=spec.global_index,
            combination_index=1,
            combination_id=spec.combination.combination_id,
            segment_index=spec.segment.index,
            segment_id=spec.segment.segment_id,
            purpose=spec.purpose,
            status=PlannedRunExecutionStatus.FAILED,
            error=ExperimentExecutionFailure("failed", "bounded"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("purpose", object()),
        ("purpose", "TRAINING"),
        ("status", object()),
        ("status", "FAILED"),
        ("status", "COMPLETED"),
        ("error", object()),
        ("reused", 1),
        ("verified", 1),
        ("global_index", True),
        ("global_index", -1),
        ("global_index", 1),
        ("combination_index", True),
        ("combination_index", "0"),
        ("segment_index", 3),
        ("segment_index", []),
    ],
)
def test_record_factory_rejects_hostile_types_before_payload_access(
    field: str,
    value: object,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    spec = plan.run_specs[0]
    arguments: dict[str, object] = {
        "run_spec_id": spec.run_spec_id,
        "experiment_id": spec.experiment_id,
        "global_index": spec.global_index,
        "combination_index": spec.combination.index,
        "combination_id": spec.combination.combination_id,
        "segment_index": spec.segment.index,
        "segment_id": spec.segment.segment_id,
        "purpose": spec.purpose,
        "status": PlannedRunExecutionStatus.FAILED,
        "error": ExperimentExecutionFailure("failed", "bounded"),
    }
    arguments[field] = value

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        build_planned_run_execution(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("experiment_id", object()),
        ("plan_checksum", object()),
        ("plan_schema_version", True),
        ("plan_schema_version", "1"),
        ("ordering_policy", object()),
        ("ordering_policy", "COMBINATION_THEN_SEGMENT"),
        ("records", []),
        ("records", object()),
        ("records", (object(),)),
    ],
)
def test_manifest_factory_rejects_hostile_types_before_payload_access(
    field: str,
    value: object,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    arguments: dict[str, object] = {
        "experiment_id": plan.experiment_id,
        "plan_checksum": plan.checksum,
        "plan_schema_version": plan.schema_version,
        "ordering_policy": plan.ordering_policy,
        "records": execution.records,
    }
    arguments[field] = value

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        build_experiment_execution_manifest(**arguments)  # type: ignore[arg-type]


def test_failure_payload_revalidates_its_public_input() -> None:
    failure = ExperimentExecutionFailure("failed", "bounded")
    object.__setattr__(failure, "message", object())

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        failure_payload(failure)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("failure_policy", object()),
        ("failure_policy", "CONTINUE_AFTER_FAILURE"),
        ("warmup_policy", object()),
        ("warmup_policy", "WARMUP_OBSERVATION_ONLY"),
        ("ordering_policy", object()),
        ("status", object()),
        ("status", "FAILED"),
        ("records", []),
    ],
)
def test_manifest_payload_helper_rejects_hostile_types_before_enum_access(
    field: str,
    value: object,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    arguments: dict[str, object] = {
        "experiment_id": execution.experiment_id,
        "plan_checksum": execution.plan_checksum,
        "plan_schema_version": execution.plan_schema_version,
        "failure_policy": ExperimentFailurePolicy.CONTINUE_AFTER_FAILURE,
        "warmup_policy": ExperimentWarmupPolicy.WARMUP_OBSERVATION_ONLY,
        "ordering_policy": execution.ordering_policy,
        "total_count": execution.total_count,
        "completed_count": execution.completed_count,
        "reused_count": execution.reused_count,
        "failed_count": execution.failed_count,
        "status": execution.status,
        "records": execution.records,
    }
    arguments[field] = value

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        experiment_execution_values_payload(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("purpose", object()),
        ("purpose", "TRAINING"),
        ("status", object()),
        ("status", "FAILED"),
        ("status", "COMPLETED"),
        ("error", object()),
        ("reused", 1),
        ("verified", 1),
        ("global_index", True),
        ("global_index", -1),
        ("global_index", 1),
        ("combination_index", "0"),
        ("segment_index", []),
    ],
)
def test_hostile_mutation_blocks_rebuild_serialization_and_publication_without_writes(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    first = execution.records[0]
    if field == "error":
        object.__setattr__(first, "error", value)
    else:
        object.__setattr__(first, field, value)

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        _rebuild_manifest(execution, execution.records)
    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        experiment_execution_to_document(execution)
    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        ExperimentExecutionRepository(tmp_path).publish(execution)

    assert not (tmp_path / "market" / "optimization").exists()
    assert not (tmp_path / "market" / "backtests").exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ordering_policy", object()),
        ("records", []),
        ("records", (object(),)),
    ],
)
def test_hostile_manifest_mutation_blocks_all_boundaries_without_writes(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    object.__setattr__(execution, field, value)

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        build_experiment_execution_manifest(
            experiment_id=execution.experiment_id,
            plan_checksum=execution.plan_checksum,
            plan_schema_version=execution.plan_schema_version,
            ordering_policy=execution.ordering_policy,
            records=execution.records,
        )
    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        experiment_execution_to_document(execution)
    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        ExperimentExecutionRepository(tmp_path).publish(execution)

    assert list(tmp_path.iterdir()) == []


def test_execution_manifest_is_reconciled_against_the_exact_plan(tmp_path: Path) -> None:
    first_plan, snapshot, _manifest = _plan(values=(1,))
    second_plan, other_snapshot, _other_manifest = _plan(values=(2,))
    service, _calls = _service(tmp_path)
    execution = service.execute(first_plan).manifest

    validate_execution_manifest_against_plan(execution, first_plan, snapshot)
    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        validate_execution_manifest_against_plan(execution, second_plan, other_snapshot)


def test_execution_repository_rejects_unsafe_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="safe and relative"):
        ExperimentExecutionRepository(tmp_path, directory=Path("../escape"))


@pytest.mark.parametrize(
    "mutation",
    [
        "records_object",
        "record_object",
        "record_checksum",
        "record_id",
        "record_purpose",
        "record_status",
        "failure",
    ],
)
def test_serializers_validate_the_complete_manifest_before_field_access(
    mutation: str,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    first = execution.records[0]
    if mutation == "records_object":
        object.__setattr__(execution, "records", object())
    elif mutation == "record_object":
        object.__setattr__(execution, "records", (object(),))
    elif mutation == "record_checksum":
        object.__setattr__(first, "checksum", object())
    elif mutation == "record_id":
        object.__setattr__(first, "execution_record_id", object())
    elif mutation == "record_purpose":
        object.__setattr__(first, "purpose", object())
    elif mutation == "record_status":
        object.__setattr__(first, "status", object())
    else:
        assert first.error is not None
        object.__setattr__(first.error, "message", object())

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        experiment_execution_to_document(execution)
    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        canonical_experiment_execution_document_bytes(execution)


def test_repository_rejects_invalid_manifest_before_creating_directories(
    tmp_path: Path,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    object.__setattr__(execution, "records", object())

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        ExperimentExecutionRepository(tmp_path).publish(execution)

    assert not (tmp_path / "market" / "optimization").exists()


def test_staging_corruption_is_detected_before_commit_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    repository = ExperimentExecutionRepository(tmp_path)
    original_write = execution_repository_module._write_bytes

    def corrupt_manifest(path: Path, value: bytes) -> None:
        original_write(path, b"{}" if path.name == "manifest.json" else value)

    monkeypatch.setattr(execution_repository_module, "_write_bytes", corrupt_manifest)
    with pytest.raises(ExperimentExecutionPublicationError):
        repository.publish(execution)

    target = repository.root / plan.experiment_id / execution.experiment_execution_id
    assert not target.exists()
    monkeypatch.setattr(execution_repository_module, "_write_bytes", original_write)

    publication = repository.publish(execution)
    assert publication.manifest == execution
    assert publication.reused is False


def test_post_rename_verification_failure_removes_only_new_target_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    repository = ExperimentExecutionRepository(tmp_path)
    original_read = repository._read_directory

    def fail_final_target(
        root: Path,
        experiment_id: str,
        execution_id: str,
        *,
        state: str,
    ) -> ExperimentExecutionManifest:
        if root.name == execution.experiment_execution_id:
            raise ExperimentExecutionPublicationError("injected final verification failure")
        return original_read(root, experiment_id, execution_id, state=state)

    monkeypatch.setattr(repository, "_read_directory", fail_final_target)
    with pytest.raises(ExperimentExecutionPublicationError):
        repository.publish(execution)

    target = repository.root / plan.experiment_id / execution.experiment_execution_id
    assert not target.exists()
    monkeypatch.setattr(repository, "_read_directory", original_read)
    assert repository.publish(execution).manifest == execution


@pytest.mark.parametrize(
    ("filename", "size"),
    [
        ("manifest.json", MAX_EXECUTION_MANIFEST_BYTES + 1),
        ("publication.json", 4 * 1024 + 1),
    ],
)
def test_repository_read_rejects_oversized_documents_before_decode(
    tmp_path: Path,
    filename: str,
    size: int,
) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)
    repository = ExperimentExecutionRepository(tmp_path)
    publication = repository.publish(execution)
    root = tmp_path / "market" / publication.relative_path
    (root / filename).write_bytes(b"x" * size)

    with pytest.raises(IncompatibleExperimentExecutionDocumentError, match="size limit"):
        repository.read(plan.experiment_id, execution.experiment_execution_id)


def test_record_formula_is_enforced_before_aggregate_validation() -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    spec = plan.run_specs[0]

    with pytest.raises(IncompatibleExperimentExecutionDocumentError, match="global index"):
        build_planned_run_execution(
            run_spec_id=spec.run_spec_id,
            experiment_id=spec.experiment_id,
            global_index=0,
            combination_index=1,
            combination_id=spec.combination.combination_id,
            segment_index=0,
            segment_id=spec.segment.segment_id,
            purpose=spec.purpose,
            status=PlannedRunExecutionStatus.FAILED,
            error=ExperimentExecutionFailure("failed", "bounded"),
        )


@pytest.mark.parametrize(
    "artifact_path",
    [
        f"backtests/{'f' * 64}.json",
        f"backtests/{'e' * 64}",
        f"/backtests/{'f' * 64}",
        f"backtests/../{'f' * 64}",
        f"backtests\\{'f' * 64}",
    ],
)
def test_success_artifact_path_must_end_in_the_exact_run_id(artifact_path: str) -> None:
    plan, _snapshot, _dataset_manifest = _plan(values=(1,))
    spec = plan.run_specs[0]

    with pytest.raises(IncompatibleExperimentExecutionDocumentError):
        build_planned_run_execution(
            run_spec_id=spec.run_spec_id,
            experiment_id=spec.experiment_id,
            global_index=spec.global_index,
            combination_index=spec.combination.index,
            combination_id=spec.combination.combination_id,
            segment_index=spec.segment.index,
            segment_id=spec.segment.segment_id,
            purpose=spec.purpose,
            status=PlannedRunExecutionStatus.COMPLETED,
            run_id="f" * 64,
            logical_result_checksum="e" * 64,
            artifact_path=artifact_path,
            verified=True,
        )


def test_reconciliation_recalculates_success_run_ids_from_plan_and_snapshot(
    tmp_path: Path,
) -> None:
    plan, snapshot, _dataset_manifest = _plan(values=(1,))
    service, _calls = _service(tmp_path)
    execution = service.execute(plan).manifest
    records = list(execution.records)
    wrong_run_id = "f" * 64
    records[0] = _rebuild_success_record(
        records[0],
        run_id=wrong_run_id,
        artifact_path=f"backtests/{wrong_run_id}",
    )
    hostile = _rebuild_manifest(execution, tuple(records))

    with pytest.raises(IncompatibleExperimentExecutionDocumentError, match="run id"):
        validate_execution_manifest_against_plan(hostile, plan, snapshot)


def test_reconciliation_rejects_a_run_id_swapped_between_planned_specs(
    tmp_path: Path,
) -> None:
    plan, snapshot, _dataset_manifest = _plan(values=(1,))
    service, _calls = _service(tmp_path)
    execution = service.execute(plan).manifest
    records = list(execution.records)
    first_run_id = records[0].run_id
    swapped_run_id = records[1].run_id
    assert first_run_id is not None and swapped_run_id is not None
    records[0] = _rebuild_success_record(
        records[0],
        run_id=swapped_run_id,
        artifact_path=f"backtests/{swapped_run_id}",
    )
    records[1] = _rebuild_success_record(
        records[1],
        run_id=first_run_id,
        artifact_path=f"backtests/{first_run_id}",
    )
    hostile = _rebuild_manifest(execution, tuple(records))

    with pytest.raises(IncompatibleExperimentExecutionDocumentError, match="run id"):
        validate_execution_manifest_against_plan(hostile, plan, snapshot)


@pytest.mark.parametrize("field", ["snapshot_id", "checksum"])
def test_reconciliation_rejects_a_different_snapshot_contract(
    tmp_path: Path,
    field: str,
) -> None:
    plan, snapshot, _dataset_manifest = _plan(values=(1,))
    service, _calls = _service(tmp_path)
    execution = service.execute(plan).manifest
    other = (
        replace(snapshot, snapshot_id="f" * 64)
        if field == "snapshot_id"
        else replace(snapshot, checksum="f" * 64)
    )

    with pytest.raises(IncompatibleExperimentExecutionDocumentError, match="snapshot"):
        validate_execution_manifest_against_plan(execution, plan, other)


def test_reconciliation_keeps_failed_records_valid_without_run_id() -> None:
    plan, snapshot, _dataset_manifest = _plan(values=(1,))
    execution = _failed_manifest(plan)

    validate_execution_manifest_against_plan(execution, plan, snapshot)


def test_published_execution_frontier_verifies_every_successful_artifact(
    tmp_path: Path,
) -> None:
    plan, snapshot, _dataset_manifest = _plan(values=(1,))
    service, _calls = _service(tmp_path)
    execution = service.execute(plan).manifest
    rows = _candles(*(str(100 + index) for index in range(12)))
    snapshot_factory = lambda _data_dir: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    store = BacktestArtifactStore(tmp_path, snapshot_factory=snapshot_factory)
    verifier = BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory)

    assert (
        verify_published_execution_manifest(execution, plan, snapshot, store, verifier) == execution
    )

    records = list(execution.records)
    records[0] = _rebuild_success_record(records[0], logical_checksum="f" * 64)
    hostile = _rebuild_manifest(execution, tuple(records))
    with pytest.raises(ExperimentExecutionArtifactVerificationError):
        verify_published_execution_manifest(hostile, plan, snapshot, store, verifier)


def test_published_execution_frontier_binds_artifact_path_to_configured_store(
    tmp_path: Path,
) -> None:
    plan, snapshot, _dataset_manifest = _plan(values=(1,))
    service, _calls = _service(tmp_path)
    execution = service.execute(plan).manifest
    rows = _candles(*(str(100 + index) for index in range(12)))
    snapshot_factory = lambda _data_dir: FakeSnapshotReader(snapshot, rows)  # noqa: E731
    store = BacktestArtifactStore(tmp_path, snapshot_factory=snapshot_factory)
    verifier = BacktestResultVerifier(tmp_path, snapshot_factory=snapshot_factory)
    records = list(execution.records)
    run_id = records[0].run_id
    assert run_id is not None
    records[0] = _rebuild_success_record(records[0], artifact_path=f"other-store/{run_id}")
    hostile = _rebuild_manifest(execution, tuple(records))

    with pytest.raises(ExperimentExecutionArtifactVerificationError):
        verify_published_execution_manifest(hostile, plan, snapshot, store, verifier)


def test_expected_run_id_uses_the_exact_snapshot_reference() -> None:
    plan, snapshot, _dataset_manifest = _plan(values=(1,))
    expected = build_run_id(plan.run_specs[0].backtest_config, snapshot).value
    assert len(expected) == 64
