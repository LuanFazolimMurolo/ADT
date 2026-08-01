"""Phase 4-03 pure reproducible experiment-planning tests."""

from __future__ import annotations

import os
import random
import time
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    RiskLimits,
    SlippageModel,
    StrategyDescriptor,
    StrategyParameters,
)
from app.backtesting.strategy import NoOpStrategy
from app.market_data.datasets import (
    DatasetIdentity,
    DatasetKind,
    DatasetLineage,
    DatasetManifest,
    DatasetSnapshot,
    DatasetState,
    GapPolicy,
    PartitionSummary,
)
from app.market_data.domain import DataRange, Exchange, MarketType
from app.market_data.snapshots import build_snapshot_id, expected_snapshot_partitions
from app.optimization import (
    ABSOLUTE_MAX_RUN_SPECS,
    DEFAULT_MAX_RUN_SPECS,
    DuplicatePlannedRunSpecError,
    EmptyParameterSearchSpaceError,
    ExperimentBacktestConfiguration,
    ExperimentChecksumError,
    ExperimentHoldoutPolicyError,
    ExperimentIdentifierError,
    ExperimentPlan,
    ExperimentPlanningError,
    ExperimentPlanningService,
    ExperimentPluginReference,
    ExperimentRunIndexError,
    ExperimentRunOrderError,
    ExperimentRunPurpose,
    IncompatibleExperimentDocumentError,
    IncompatibleExperimentPluginError,
    IncompatibleExperimentSearchSpaceError,
    IncompatibleExperimentSnapshotError,
    IncompatibleExperimentTemporalPlanError,
    InvalidExperimentBacktestConfigurationError,
    InvalidExperimentCardinalityError,
    InvalidExperimentRunPurposeError,
    InvalidRunSpecLimitError,
    InvalidSearchCombinationError,
    ParameterSearchService,
    ParameterSearchSpace,
    PlannedRunSpecIdentifierError,
    RunSpecLimitExceededError,
    TemporalSegmentationPlan,
    TemporalSegmentationService,
    TemporalSegmentRole,
    UnsupportedExperimentSchemaError,
    calculate_run_spec_cardinality,
    canonical_experiment_document_bytes,
)
from app.optimization.canonical import document_checksum
from app.strategies.domain import (
    StrategyParameterKind,
    StrategyParameterSpec,
    StrategyPluginDescriptor,
)
from app.strategies.errors import StrategyParameterValidationError
from app.strategies.registry import StrategyPluginRegistry

START = datetime(2026, 1, 1, tzinfo=UTC)


def _contracts(
    *,
    checksum: str = "c" * 64,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    snapshot_candles: int = 12,
) -> tuple[DatasetSnapshot, DatasetManifest]:
    duration = timedelta(hours=1) if timeframe == "1h" else timedelta(minutes=5)
    identity = DatasetIdentity(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol=symbol,
        timeframe=timeframe,
        kind=DatasetKind.DERIVED,
        source="binance-public",
        construction_policy="canonical_ohlcv:1:STRICT:crypto_24_7",
        schema_version=1,
    )
    lineage = DatasetLineage(
        source_dataset_key=f"raw:binance:spot:{symbol}:1m",
        source_dataset_version="a" * 64,
        source_checksum="b" * 64,
        source_timeframe="1m",
        target_timeframe=timeframe,
        algorithm="canonical_ohlcv",
        algorithm_version="1",
        gap_policy=GapPolicy.STRICT,
        open_candle_policy="REJECT",
        calendar="crypto_24_7",
        materialized_at=START.isoformat(),
    )
    manifest_candles = 14
    manifest_end = START + duration * manifest_candles
    partition = PartitionSummary(
        relative_path="derived/year=2026/month=01/candles.parquet",
        year=2026,
        month=1,
        candle_count=manifest_candles,
        first_open_time=START.isoformat(),
        last_open_time=(manifest_end - duration).isoformat(),
        checksum="e" * 64,
    )
    manifest = DatasetManifest(
        identity=identity,
        schema_version=1,
        source_dataset_key=lineage.source_dataset_key,
        source_dataset_version=lineage.source_dataset_version,
        source_checksum=lineage.source_checksum,
        target_dataset_key=identity.key,
        target_version="d" * 64,
        target_checksum=checksum,
        source_timeframe="1m",
        target_timeframe=timeframe,
        gap_policy=GapPolicy.STRICT,
        calendar="crypto_24_7",
        first_open_time=START.isoformat(),
        last_open_time=(manifest_end - duration).isoformat(),
        candle_count=manifest_candles,
        partitions=(partition,),
        source_partitions=(),
        algorithm="canonical_ohlcv",
        algorithm_version="1",
        created_at=START.isoformat(),
        updated_at=START.isoformat(),
        state=DatasetState.COMPLETE,
        lineage=lineage,
    )
    snapshot_range = DataRange(START, START + duration * snapshot_candles)
    snapshot = DatasetSnapshot(
        snapshot_id=build_snapshot_id(manifest, snapshot_range),
        dataset_key=identity.key,
        dataset_version=manifest.target_version,
        checksum=checksum,
        data_range=snapshot_range,
        partitions=expected_snapshot_partitions(manifest, snapshot_range),
        manifest_path="dataset-manifest.json",
        created_at=START.isoformat(),
    )
    return snapshot, manifest


def _temporal(
    snapshot: DatasetSnapshot,
    manifest: DatasetManifest,
    *,
    warmup: int = 1,
) -> TemporalSegmentationPlan:
    duration = timedelta(hours=1) if manifest.target_timeframe == "1h" else timedelta(minutes=5)
    return TemporalSegmentationService().create(
        snapshot,
        manifest,
        DataRange(START + duration, START + duration * 10),
        train_candles=5,
        validation_candles=2,
        test_candles=2,
        warmup_candles=warmup,
    )


def _space(*, values: tuple[int, ...] = (2, 3), quantity: str = "0.1") -> ParameterSearchSpace:
    return ParameterSearchService().create(
        "ema-cross-example",
        "1",
        {"fast_period": list(values)},
        fixed_parameters={"slow_period": 5, "quantity": Decimal(quantity)},
    )


def _configuration(
    *,
    initial_capital: object = Decimal("10000"),
    maker_fee: Decimal = Decimal("1"),
    engine_version: object = "3b-1",
    schema_version: object = 2,
) -> ExperimentBacktestConfiguration:
    return ExperimentBacktestConfiguration(
        initial_capital=initial_capital,  # type: ignore[arg-type]
        execution=ExecutionAssumptions(
            FeeModel(maker_fee, Decimal("2")),
            SlippageModel(fixed_bps=Decimal("1")),
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        risk_limits=RiskLimits(max_open_orders=10, max_total_orders=100),
        history_window=10,
        max_candles=100,
        max_orders=100,
        max_events=1_000,
        engine_version=engine_version,  # type: ignore[arg-type]
        schema_version=schema_version,  # type: ignore[arg-type]
    )


def _plan(
    *,
    values: tuple[int, ...] = (2, 3),
    checksum: str = "c" * 64,
    warmup: int = 1,
    configuration: ExperimentBacktestConfiguration | None = None,
    max_run_specs: int = DEFAULT_MAX_RUN_SPECS,
) -> tuple[ExperimentPlan, DatasetSnapshot, DatasetManifest]:
    snapshot, manifest = _contracts(checksum=checksum)
    temporal = _temporal(snapshot, manifest, warmup=warmup)
    space = _space(values=values)
    plan = ExperimentPlanningService().create(
        snapshot,
        manifest,
        temporal,
        space,
        plugin_name="ema-cross-example",
        plugin_version="1",
        backtest_configuration=configuration or _configuration(),
        max_run_specs=max_run_specs,
    )
    return plan, snapshot, manifest


def _document() -> tuple[
    ExperimentPlan,
    DatasetSnapshot,
    DatasetManifest,
    dict[str, object],
]:
    plan, snapshot, manifest = _plan()
    document = ExperimentPlanningService().to_document(plan, snapshot, manifest)
    return plan, snapshot, manifest, document


def _rehash(document: dict[str, object]) -> None:
    document["checksum"] = document_checksum(document["experiment_plan"])


def _payload(document: dict[str, object]) -> dict[str, object]:
    value = document["experiment_plan"]
    assert isinstance(value, dict)
    return value


def test_valid_experiment_has_three_ordered_specs_per_combination() -> None:
    plan, _snapshot, _manifest = _plan(values=(2, 3, 4))

    assert plan.cardinality == 9
    assert len(plan.run_specs) == 9
    assert [item.global_index for item in plan.run_specs] == list(range(9))
    assert [(item.combination.index, item.segment.index) for item in plan.run_specs] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
        (2, 0),
        (2, 1),
        (2, 2),
    ]
    assert all(len(plan.run_specs[index : index + 3]) == 3 for index in range(0, 9, 3))


def test_one_combination_preserves_parameters_context_and_phase3a_config() -> None:
    plan, snapshot, _manifest = _plan(values=(2,))

    assert plan.cardinality == 3
    for spec in plan.run_specs:
        assert spec.snapshot.snapshot_id == snapshot.snapshot_id
        assert spec.context_range == spec.backtest_config.data_range
        assert spec.evaluation_range == spec.segment.evaluation.data_range
        assert spec.combination.parameters == spec.backtest_config.strategy.parameters
        assert spec.combination.parameter_document


def test_holdout_policy_is_explicit_and_only_validation_is_selection_eligible() -> None:
    plan, _snapshot, _manifest = _plan(values=(2,))
    train, validation, test = plan.run_specs

    assert train.purpose is ExperimentRunPurpose.TRAINING
    assert not train.eligible_for_model_selection
    assert validation.purpose is ExperimentRunPurpose.MODEL_SELECTION
    assert validation.eligible_for_model_selection
    assert test.purpose is ExperimentRunPurpose.FINAL_HOLDOUT
    assert test.segment.role is TemporalSegmentRole.TEST
    assert not test.eligible_for_model_selection


@pytest.mark.parametrize(
    "attribute,value,error",
    [
        ("snapshot_id", "0" * 64, IncompatibleExperimentSnapshotError),
        ("checksum", "0" * 64, IncompatibleExperimentSnapshotError),
        ("dataset_key", "derived:other", IncompatibleExperimentSnapshotError),
        ("dataset_version", "0" * 64, IncompatibleExperimentSnapshotError),
    ],
)
def test_snapshot_identity_divergence_is_rejected(
    attribute: str,
    value: str,
    error: type[Exception],
) -> None:
    snapshot, manifest = _contracts()
    if attribute == "snapshot_id":
        changed = replace(snapshot, snapshot_id=value)
    elif attribute == "checksum":
        changed = replace(snapshot, checksum=value)
    elif attribute == "dataset_key":
        changed = replace(snapshot, dataset_key=value)
    else:
        changed = replace(snapshot, dataset_version=value)

    with pytest.raises(error):
        ExperimentPlanningService().create(
            changed,
            manifest,
            _temporal(snapshot, manifest),
            _space(),
            plugin_name="ema-cross-example",
            plugin_version="1",
            backtest_configuration=_configuration(),
        )


@pytest.mark.parametrize("kind", ["timeframe", "instrument", "coverage"])
def test_snapshot_manifest_semantic_divergence_is_rejected(kind: str) -> None:
    snapshot, manifest = _contracts()
    changed_snapshot = snapshot
    changed_manifest = manifest
    if kind == "timeframe":
        changed_manifest = replace(manifest, target_timeframe="5m")
    elif kind == "instrument":
        changed_manifest = replace(
            manifest,
            identity=replace(manifest.identity, symbol="ETH/USDT"),
        )
    else:
        changed_snapshot = replace(
            snapshot,
            data_range=DataRange(START, START + timedelta(hours=13)),
        )

    with pytest.raises(IncompatibleExperimentSnapshotError):
        ExperimentPlanningService().create(
            changed_snapshot,
            changed_manifest,
            _temporal(snapshot, manifest),
            _space(),
            plugin_name="ema-cross-example",
            plugin_version="1",
            backtest_configuration=_configuration(),
        )


def test_temporal_plan_low_level_tampering_is_rejected() -> None:
    snapshot, manifest = _contracts()
    temporal = _temporal(snapshot, manifest)
    object.__setattr__(temporal, "checksum", "0" * 64)

    with pytest.raises(IncompatibleExperimentTemporalPlanError):
        ExperimentPlanningService().create(
            snapshot,
            manifest,
            temporal,
            _space(),
            plugin_name="ema-cross-example",
            plugin_version="1",
            backtest_configuration=_configuration(),
        )


def test_search_space_low_level_tampering_is_rejected_before_factory() -> None:
    snapshot, manifest = _contracts()
    space = _space()
    object.__setattr__(space, "checksum", "0" * 64)

    with pytest.raises(IncompatibleExperimentSearchSpaceError):
        ExperimentPlanningService().create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            space,
            plugin_name="ema-cross-example",
            plugin_version="1",
            backtest_configuration=_configuration(),
        )


@pytest.mark.parametrize(
    "name,version",
    [("no-op", "1"), ("ema-cross-example", "2")],
)
def test_selected_plugin_identity_must_equal_search_space(name: str, version: str) -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(IncompatibleExperimentPluginError):
        ExperimentPlanningService().create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            _space(),
            plugin_name=name,
            plugin_version=version,
            backtest_configuration=_configuration(),
        )


@pytest.mark.parametrize("attribute", ["plugin_schema_version", "plugin_lifecycle_version"])
def test_registered_plugin_versions_must_equal_space(attribute: str) -> None:
    snapshot, manifest = _contracts()
    space = _space()
    object.__setattr__(space, attribute, 2)
    with pytest.raises(IncompatibleExperimentPluginError):
        ExperimentPlanningService().create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            space,
            plugin_name="ema-cross-example",
            plugin_version="1",
            backtest_configuration=_configuration(),
        )


@dataclass(slots=True)
class _ChangingPlugin:
    reject: bool = False
    build_calls: int = 0
    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        name="changing",
        version="1",
        description="Test-only changing factory.",
        parameters=(StrategyParameterSpec("period", StrategyParameterKind.INTEGER),),
    )

    def build(self, parameters: StrategyParameters) -> NoOpStrategy:
        self.build_calls += 1
        if self.reject:
            raise StrategyParameterValidationError("factory changed")
        return NoOpStrategy(StrategyDescriptor("changing", "1", parameters))


def test_real_factory_remains_final_parameter_boundary() -> None:
    plugin = _ChangingPlugin()
    registry = StrategyPluginRegistry((plugin,))
    space = ParameterSearchService(registry, available_indicators=()).create(
        "changing", "1", {"period": [1]}
    )
    plugin.reject = True
    snapshot, manifest = _contracts()

    with pytest.raises(InvalidSearchCombinationError):
        ExperimentPlanningService(registry, available_indicators=()).create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            space,
            plugin_name="changing",
            plugin_version="1",
            backtest_configuration=_configuration(),
        )


@pytest.mark.parametrize("capital", [Decimal("0"), Decimal("-1"), Decimal("NaN"), 1.0])
def test_invalid_capital_and_float_are_rejected(capital: object) -> None:
    with pytest.raises(InvalidExperimentBacktestConfigurationError):
        _configuration(initial_capital=capital)


def test_engine_version_rejects_noncanonical_whitespace() -> None:
    with pytest.raises(InvalidExperimentBacktestConfigurationError):
        _configuration(engine_version=" 3b-1 ")


@pytest.mark.parametrize("schema_version", [True, False, 0, 3])
def test_invalid_backtest_schema_versions_are_rejected(schema_version: object) -> None:
    with pytest.raises(InvalidExperimentBacktestConfigurationError):
        _configuration(schema_version=schema_version)


@pytest.mark.parametrize("schema_version", [1, 2])
def test_every_supported_backtest_schema_round_trips_exactly(schema_version: int) -> None:
    configuration = _configuration(schema_version=schema_version)
    plan, snapshot, manifest = _plan(configuration=configuration)
    reconstructed = ExperimentPlanningService().from_document(
        ExperimentPlanningService().to_document(plan, snapshot, manifest),
        snapshot=snapshot,
        manifest=manifest,
    )

    assert reconstructed == plan
    assert reconstructed.backtest_configuration == configuration
    assert {spec.backtest_config.schema_version for spec in reconstructed.run_specs} == {
        schema_version
    }
    assert {spec.backtest_config.engine_version for spec in reconstructed.run_specs} == {"3b-1"}


@pytest.mark.parametrize(
    "name,version",
    [(" ema-cross-example ", "1"), ("ema-cross-example", " 1 ")],
)
def test_plugin_reference_rejects_noncanonical_identity(name: str, version: str) -> None:
    with pytest.raises(IncompatibleExperimentPluginError):
        ExperimentPluginReference(name, version, 1, 1)


def test_plugin_reference_round_trips_exactly() -> None:
    plan, snapshot, manifest, document = _document()
    reconstructed = ExperimentPlanningService().from_document(
        document, snapshot=snapshot, manifest=manifest
    )

    assert reconstructed.plugin == plan.plugin


@pytest.mark.parametrize("model", ["fee", "slippage"])
def test_mutated_fee_and_slippage_models_are_rejected(model: str) -> None:
    configuration = _configuration()
    if model == "fee":
        object.__setattr__(configuration.execution.fees, "maker_fee_bps", 1.0)
    else:
        object.__setattr__(configuration.execution.slippage, "kind", "UNKNOWN")

    snapshot, manifest = _contracts()
    with pytest.raises(InvalidExperimentBacktestConfigurationError):
        ExperimentPlanningService().create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            _space(),
            plugin_name="ema-cross-example",
            plugin_version="1",
            backtest_configuration=configuration,
        )


def test_default_and_absolute_run_spec_limits_are_phase401_compatible() -> None:
    assert DEFAULT_MAX_RUN_SPECS == 3_000
    assert ABSOLUTE_MAX_RUN_SPECS == 30_000
    assert calculate_run_spec_cardinality(10_000, ABSOLUTE_MAX_RUN_SPECS) == 30_000


@pytest.mark.parametrize("limit", [0, -1, ABSOLUTE_MAX_RUN_SPECS + 1, True])
def test_invalid_requested_run_spec_limits_are_rejected(limit: int) -> None:
    snapshot, manifest = _contracts()
    with pytest.raises(InvalidRunSpecLimitError):
        ExperimentPlanningService().create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            _space(values=(2,)),
            plugin_name="ema-cross-example",
            plugin_version="1",
            backtest_configuration=_configuration(),
            max_run_specs=limit,
        )


def test_cardinality_limit_is_checked_before_expansion_factory_calls() -> None:
    plugin = _ChangingPlugin()
    registry = StrategyPluginRegistry((plugin,))
    space = ParameterSearchService(registry, available_indicators=()).create(
        "changing", "1", {"period": [1, 2]}
    )
    plugin.build_calls = 0
    snapshot, manifest = _contracts()

    with pytest.raises(RunSpecLimitExceededError):
        ExperimentPlanningService(registry, available_indicators=()).create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            space,
            plugin_name="changing",
            plugin_version="1",
            backtest_configuration=_configuration(),
            max_run_specs=3,
        )
    assert plugin.build_calls == 0


def test_absolute_limit_is_rejected_before_expansion_factory_calls() -> None:
    plugin = _ChangingPlugin()
    registry = StrategyPluginRegistry((plugin,))
    space = ParameterSearchService(registry, available_indicators=()).create(
        "changing", "1", {"period": [1]}
    )
    plugin.build_calls = 0
    snapshot, manifest = _contracts()

    with pytest.raises(InvalidRunSpecLimitError):
        ExperimentPlanningService(registry, available_indicators=()).create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            space,
            plugin_name="changing",
            plugin_version="1",
            backtest_configuration=_configuration(),
            max_run_specs=ABSOLUTE_MAX_RUN_SPECS + 1,
        )
    assert plugin.build_calls == 0


def test_invalid_configuration_is_rejected_before_expansion_factory_calls() -> None:
    plugin = _ChangingPlugin()
    registry = StrategyPluginRegistry((plugin,))
    space = ParameterSearchService(registry, available_indicators=()).create(
        "changing", "1", {"period": [1]}
    )
    plugin.build_calls = 0
    configuration = _configuration()
    object.__setattr__(configuration, "engine_version", " 3b-1 ")
    snapshot, manifest = _contracts()

    with pytest.raises(InvalidExperimentBacktestConfigurationError):
        ExperimentPlanningService(registry, available_indicators=()).create(
            snapshot,
            manifest,
            _temporal(snapshot, manifest),
            space,
            plugin_name="changing",
            plugin_version="1",
            backtest_configuration=configuration,
        )
    assert plugin.build_calls == 0


def test_experiment_and_run_spec_ids_and_checksums_are_stable_and_distinct() -> None:
    first, _snapshot, _manifest = _plan()
    second, _snapshot, _manifest = _plan()

    assert first.experiment_id == second.experiment_id
    assert first.checksum == second.checksum
    assert [item.run_spec_id for item in first.run_specs] == [
        item.run_spec_id for item in second.run_specs
    ]
    assert len({item.run_spec_id for item in first.run_specs}) == first.cardinality


@pytest.mark.parametrize("change", ["snapshot", "temporal", "space", "config", "limit"])
def test_semantic_changes_change_experiment_identity(change: str) -> None:
    baseline, _snapshot, _manifest = _plan()
    if change == "snapshot":
        changed, _snapshot, _manifest = _plan(checksum="f" * 64)
    elif change == "temporal":
        changed, _snapshot, _manifest = _plan(warmup=0)
    elif change == "space":
        changed, _snapshot, _manifest = _plan(values=(2, 4))
    elif change == "config":
        changed, _snapshot, _manifest = _plan(
            configuration=_configuration(initial_capital=Decimal("20000"))
        )
    else:
        changed, _snapshot, _manifest = _plan(max_run_specs=DEFAULT_MAX_RUN_SPECS + 1)

    assert changed.experiment_id != baseline.experiment_id
    assert changed.checksum != baseline.checksum


def test_public_contracts_are_frozen_and_reject_invalid_direct_construction() -> None:
    plan, _snapshot, _manifest = _plan(values=(2,))
    with pytest.raises(FrozenInstanceError):
        plan.cardinality = 9  # type: ignore[misc]
    with pytest.raises(InvalidExperimentRunPurposeError):
        replace(plan.run_specs[0], purpose=ExperimentRunPurpose.FINAL_HOLDOUT)
    with pytest.raises(ExperimentHoldoutPolicyError):
        replace(plan.run_specs[2], eligible_for_model_selection=True)


def test_service_detects_low_level_index_tampering() -> None:
    plan, snapshot, manifest = _plan()
    object.__setattr__(plan.run_specs[1], "global_index", 99)
    with pytest.raises(ExperimentRunIndexError):
        ExperimentPlanningService().validate(plan, snapshot, manifest)


def test_direct_plan_rejects_reordered_and_duplicate_specs() -> None:
    plan, _snapshot, _manifest = _plan()
    with pytest.raises((ExperimentRunIndexError, ExperimentRunOrderError)):
        replace(plan, run_specs=tuple(reversed(plan.run_specs)))
    with pytest.raises(
        (DuplicatePlannedRunSpecError, ExperimentRunIndexError, ExperimentRunOrderError)
    ):
        replace(plan, run_specs=(plan.run_specs[0],) + plan.run_specs[1:-1] + (plan.run_specs[0],))


def test_document_is_canonical_json_and_round_trips() -> None:
    plan, snapshot, manifest, document = _document()
    reconstructed = ExperimentPlanningService().from_document(
        document, snapshot=snapshot, manifest=manifest
    )

    assert reconstructed == plan
    assert canonical_experiment_document_bytes(plan) == canonical_experiment_document_bytes(
        reconstructed
    )
    assert b" " not in canonical_experiment_document_bytes(plan)


def test_document_uses_compact_reconstructable_run_references() -> None:
    _plan_value, _snapshot, _manifest, document = _document()
    specs = _payload(document)["run_specs"]
    assert isinstance(specs, list)
    for envelope in specs:
        assert isinstance(envelope, dict)
        run_spec = envelope["run_spec"]
        assert isinstance(run_spec, dict)
        assert set(run_spec) == {
            "schema_version",
            "global_index",
            "combination_reference",
            "segment_reference",
            "purpose",
            "eligible_for_model_selection",
        }
        assert set(run_spec["combination_reference"]) == {
            "index",
            "combination_id",
            "parameters_checksum",
        }
        assert set(run_spec["segment_reference"]) == {"index", "segment_id", "checksum"}


@pytest.mark.parametrize("mutation", ["missing", "extra", "schema", "enum"])
def test_document_rejects_shape_schema_and_enum_mutations(mutation: str) -> None:
    _plan_value, snapshot, manifest, document = _document()
    payload = _payload(document)
    if mutation == "missing":
        del payload["ordering_policy"]
    elif mutation == "extra":
        payload["unexpected"] = True
    elif mutation == "schema":
        payload["schema_version"] = 999
    else:
        payload["holdout_policy"] = "UNKNOWN"
    _rehash(document)

    error = (
        UnsupportedExperimentSchemaError
        if mutation == "schema"
        else IncompatibleExperimentDocumentError
    )
    with pytest.raises(error):
        ExperimentPlanningService().from_document(document, snapshot=snapshot, manifest=manifest)


def test_document_rejects_tampered_checksum_and_experiment_id() -> None:
    _plan_value, snapshot, manifest, document = _document()
    document["checksum"] = "0" * 64
    with pytest.raises(ExperimentChecksumError):
        ExperimentPlanningService().from_document(document, snapshot=snapshot, manifest=manifest)

    _plan_value, snapshot, manifest, document = _document()
    document["experiment_id"] = "0" * 64
    with pytest.raises((ExperimentIdentifierError, PlannedRunSpecIdentifierError)):
        ExperimentPlanningService().from_document(document, snapshot=snapshot, manifest=manifest)


def test_document_rejects_tampered_run_spec_id_order_and_cardinality() -> None:
    _plan_value, snapshot, manifest, document = _document()
    payload = _payload(document)
    specs = payload["run_specs"]
    assert isinstance(specs, list)
    first = specs[0]
    assert isinstance(first, dict)
    first["run_spec_id"] = "0" * 64
    _rehash(document)
    with pytest.raises(PlannedRunSpecIdentifierError):
        ExperimentPlanningService().from_document(document, snapshot=snapshot, manifest=manifest)

    _plan_value, snapshot, manifest, document = _document()
    specs = _payload(document)["run_specs"]
    assert isinstance(specs, list)
    specs.reverse()
    _rehash(document)
    with pytest.raises((ExperimentRunIndexError, ExperimentRunOrderError)):
        ExperimentPlanningService().from_document(document, snapshot=snapshot, manifest=manifest)

    _plan_value, snapshot, manifest, document = _document()
    _payload(document)["cardinality"] = 999
    _rehash(document)
    with pytest.raises(InvalidExperimentCardinalityError):
        ExperimentPlanningService().from_document(document, snapshot=snapshot, manifest=manifest)


def test_document_rejects_noncanonical_timestamp_and_unknown_purpose() -> None:
    _plan_value, snapshot, manifest, document = _document()
    snapshot_payload = _payload(document)["snapshot"]
    assert isinstance(snapshot_payload, dict)
    coverage = snapshot_payload["available_coverage"]
    assert isinstance(coverage, dict)
    coverage["start"] = "2025-12-31T21:00:00-03:00"
    _rehash(document)
    with pytest.raises(IncompatibleExperimentDocumentError):
        ExperimentPlanningService().from_document(document, snapshot=snapshot, manifest=manifest)

    _plan_value, snapshot, manifest, document = _document()
    specs = _payload(document)["run_specs"]
    assert isinstance(specs, list)
    first = specs[0]
    assert isinstance(first, dict)
    spec_payload = first["run_spec"]
    assert isinstance(spec_payload, dict)
    spec_payload["purpose"] = "UNKNOWN"
    first["checksum"] = document_checksum(spec_payload)
    _rehash(document)
    with pytest.raises(IncompatibleExperimentDocumentError):
        ExperimentPlanningService().from_document(document, snapshot=snapshot, manifest=manifest)


def test_mapping_order_timezone_clock_and_randomness_do_not_affect_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, _snapshot, _manifest = _plan()
    original_timezone = os.environ.get("TZ")
    try:
        monkeypatch.setenv("TZ", "Pacific/Honolulu")
        if hasattr(time, "tzset"):
            time.tzset()
        random.Random(987654321).random()
        changed, _snapshot, _manifest = _plan()
    finally:
        if original_timezone is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original_timezone)
        if hasattr(time, "tzset"):
            time.tzset()

    assert changed.experiment_id == baseline.experiment_id
    assert changed.checksum == baseline.checksum


@pytest.mark.parametrize(
    "mutation",
    [
        "combination_text_index",
        "combination_bool_index",
        "malformed_parameter_entry",
        "non_text_parameter_name",
        "mixed_parameter_names",
        "stored_name",
        "stored_kind",
        "stored_value",
        "plan_search_space",
        "plan_combinations",
        "spec_combination",
        "spec_backtest_config",
        "plugin_name",
        "configuration_engine",
        "configuration_schema",
    ],
)
def test_low_level_nested_corruption_never_leaks_internal_errors(mutation: str) -> None:
    plan, snapshot, manifest = _plan()
    combination = plan.combinations[0]
    if mutation == "combination_text_index":
        object.__setattr__(combination, "index", "invalid")
    elif mutation == "combination_bool_index":
        object.__setattr__(combination, "index", True)
    elif mutation == "malformed_parameter_entry":
        object.__setattr__(combination, "parameters", (("x", 1), ("y",)))
    elif mutation == "non_text_parameter_name":
        object.__setattr__(combination, "parameters", ((object(), 1),))
    elif mutation == "mixed_parameter_names":
        object.__setattr__(combination, "parameters", (("x", 1), (object(), 2)))
    elif mutation == "stored_name":
        object.__setattr__(combination.parameter_document[0], "name", object())
    elif mutation == "stored_kind":
        object.__setattr__(combination.parameter_document[0], "kind", object())
    elif mutation == "stored_value":
        object.__setattr__(combination.parameter_document[0], "value", object())
    elif mutation == "plan_search_space":
        object.__setattr__(plan, "search_space", object())
    elif mutation == "plan_combinations":
        object.__setattr__(plan, "combinations", (object(),))
    elif mutation == "spec_combination":
        object.__setattr__(plan.run_specs[0], "combination", object())
    elif mutation == "spec_backtest_config":
        object.__setattr__(plan.run_specs[0], "backtest_config", object())
    elif mutation == "plugin_name":
        object.__setattr__(plan.plugin, "name", " ema-cross-example ")
    elif mutation == "configuration_engine":
        object.__setattr__(plan.backtest_configuration, "engine_version", " 3b-1 ")
    else:
        object.__setattr__(plan.backtest_configuration, "schema_version", True)

    with pytest.raises(ExperimentPlanningError):
        ExperimentPlanningService().validate(plan, snapshot, manifest)


def test_planning_performs_no_writes_execution_or_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, manifest = _contracts()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("planning crossed an execution or write boundary")

    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr("app.backtesting.engine.DeterministicBacktestEngine.run", forbidden)
    monkeypatch.setattr("app.backtesting.artifacts.BacktestArtifactStore.publish", forbidden)
    plan = ExperimentPlanningService().create(
        snapshot,
        manifest,
        _temporal(snapshot, manifest),
        _space(values=(2,)),
        plugin_name="ema-cross-example",
        plugin_version="1",
        backtest_configuration=_configuration(),
    )

    assert plan.cardinality == 3


def test_ema_plugin_integration_and_noop_incompatibility_are_explicit() -> None:
    plan, _snapshot, _manifest = _plan(values=(2,))
    assert plan.plugin.name == "ema-cross-example"
    assert all(spec.backtest_config.strategy.name == "ema-cross-example" for spec in plan.run_specs)

    with pytest.raises(EmptyParameterSearchSpaceError):
        ParameterSearchService().create("no-op", "1", {})


def test_mapping_input_order_does_not_change_experiment() -> None:
    snapshot, manifest = _contracts()
    temporal = _temporal(snapshot, manifest)
    first_space = ParameterSearchService().create(
        "ema-cross-example",
        "1",
        {"quantity": [Decimal("0.2"), Decimal("0.1")], "fast_period": [3, 2]},
        fixed_parameters={"slow_period": 5},
    )
    second_space = ParameterSearchService().create(
        "ema-cross-example",
        "1",
        {"fast_period": [2, 3], "quantity": [Decimal("0.1"), Decimal("0.2")]},
        fixed_parameters={"slow_period": 5},
    )
    service = ExperimentPlanningService()
    first = service.create(
        snapshot,
        manifest,
        temporal,
        first_space,
        plugin_name="ema-cross-example",
        plugin_version="1",
        backtest_configuration=_configuration(),
    )
    second = service.create(
        snapshot,
        manifest,
        temporal,
        second_space,
        plugin_name="ema-cross-example",
        plugin_version="1",
        backtest_configuration=_configuration(),
    )

    assert first == second


def test_test_holdout_cannot_be_relabelled_even_with_low_level_mutation() -> None:
    plan, snapshot, manifest = _plan(values=(2,))
    test_spec = plan.run_specs[2]
    object.__setattr__(test_spec, "eligible_for_model_selection", True)
    with pytest.raises(ExperimentHoldoutPolicyError):
        ExperimentPlanningService().validate(plan, snapshot, manifest)


def test_document_contains_only_json_scalars_and_never_float() -> None:
    _plan_value, _snapshot, _manifest, document = _document()

    def walk(value: object) -> None:
        assert not isinstance(value, float)
        if isinstance(value, dict):
            assert all(isinstance(key, str) for key in value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        else:
            assert value is None or isinstance(value, (str, int, bool))

    walk(document)
