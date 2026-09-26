"""Pure ML contracts: causal boundaries, immutable identity and source reuse."""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from app.indicators.domain import IndicatorDescriptor
from app.market_data.datasets import GapPolicy
from app.market_data.domain import DataRange
from app.ml.documents import encode_document
from app.ml.domain import (
    MLBoundaryPolicy,
    MLCandleDependency,
    MLDatasetMaterialization,
    MLDatasetSpecification,
    MLExcludedObservation,
    MLExclusionReason,
    MLFeatureSetSpecification,
    MLFeatureSpecification,
    MLFeatureVector,
    MLHistoryPolicy,
    MLImplementationReference,
    MLLabel,
    MLMaterializationRow,
    MLMissingPolicy,
    MLObservation,
    MLProvenance,
    MLResetPolicy,
    MLSeedPolicy,
    MLStrategyReference,
    MLTargetSpecification,
    MLTemporalSplit,
    MLValueKind,
    candle_available_at,
    validate_contract,
)
from app.ml.errors import (
    MLContractError,
    MLDuplicateObservationError,
    MLIncompatibleReferenceError,
    MLTemporalLeakageError,
    MLUnsupportedSchemaError,
)
from app.optimization.temporal_domain import TemporalSegmentRole
from app.optimization.temporal_segmentation import TemporalSegmentationService
from app.strategies.definitions import StoredStrategyParameter, strategy_parameter_checksum
from app.strategies.domain import StrategyParameterKind
from tests.test_optimization_temporal_segmentation import _contracts

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def instant(hour: int) -> datetime:
    return START + HOUR * hour


def implementation() -> MLImplementationReference:
    return MLImplementationReference("adt-test-build", "1", "a" * 64)


def dataset(*, supervised: bool = True, warmup: int = 0) -> MLDatasetSpecification:
    snapshot, manifest = _contracts()
    temporal = TemporalSegmentationService().create(
        snapshot,
        manifest,
        DataRange(instant(1), instant(11)),
        train_candles=4,
        validation_candles=3,
        test_candles=3,
        warmup_candles=1,
    )
    recipe = MLFeatureSpecification(
        name="close_ema",
        kind=MLValueKind.DECIMAL,
        unit="quote/base",
        meaning="EMA of trailing closed prices",
        algorithm=IndicatorDescriptor("ema", "1", (("period", 3),)),
        source_fields=("close",),
        history_policy=MLHistoryPolicy.TRAILING_WINDOW,
        lookback_candles=3,
        warmup_candles=warmup,
        seed_policy=MLSeedPolicy.ARITHMETIC_MEAN,
        reset_policy=MLResetPolicy.PER_OBSERVATION,
        missing_policy=MLMissingPolicy.REJECT,
    )
    return MLDatasetSpecification(
        MLProvenance(
            temporal.snapshot,
            DataRange(instant(1), instant(11)),
            DataRange(instant(0), instant(11)),
            implementation(),
        ),
        MLFeatureSetSpecification("1h", (recipe,), implementation()),
        MLTargetSpecification(
            "next_return",
            IndicatorDescriptor("forward_close_return", "1"),
            MLValueKind.DECIMAL,
            "ratio",
            "Return over the next complete candle",
            1,
            implementation(),
        )
        if supervised
        else None,
    )


def observation(spec: MLDatasetSpecification | None = None, *, hour: int = 2) -> MLObservation:
    return MLObservation(spec or dataset(), instant(hour), instant(hour + 1))


def vector(obs: MLObservation | None = None) -> MLFeatureVector:
    obs = obs or observation()
    spec = obs.dataset
    duration = HOUR
    dependencies = []
    values = []
    for recipe in spec.feature_set.features:
        end = obs.observation_time
        start = spec.provenance.context_range.start
        if recipe.history_policy is MLHistoryPolicy.TRAILING_WINDOW:
            start = max(start, end - duration * recipe.lookback_candles)
        dependency = MLCandleDependency(spec.provenance.source, DataRange(start, end))
        dependencies.append(dependency)
        values.append(
            None if (end - start) // duration <= recipe.warmup_candles else Decimal("101.25")
        )
    return MLFeatureVector(obs, spec.feature_set, tuple(values), tuple(dependencies))


def label(obs: MLObservation | None = None) -> MLLabel:
    obs = obs or observation()
    target = obs.dataset.target
    assert target is not None
    future = MLCandleDependency(
        obs.dataset.provenance.source,
        DataRange(obs.observation_time, obs.observation_time + HOUR * target.horizon_candles),
    )
    return MLLabel(obs, target, future, Decimal("0.125"))


def split(spec: MLDatasetSpecification | None = None) -> MLTemporalSplit:
    return MLTemporalSplit(
        spec or dataset(),
        DataRange(instant(1), instant(5)),
        DataRange(instant(5), instant(8)),
        DataRange(instant(8), instant(11)),
        instant(5),
        True,
    )


def materialization(*, supervised: bool = True) -> MLDatasetMaterialization:
    spec = dataset(supervised=supervised)
    partition = split(spec)
    rows = tuple(
        MLMaterializationRow(
            vector(obs), label(obs) if supervised else None, partition.role_for(obs)
        )
        for obs in (observation(spec, hour=2), observation(spec, hour=5))
    )
    excluded = MLExcludedObservation(
        observation(spec, hour=4), MLExclusionReason.TARGET_CROSSES_BOUNDARY
    )
    return MLDatasetMaterialization(
        spec,
        partition,
        rows,
        (excluded,) if supervised else (),
        implementation(),
        built_at=instant(20),
    )


def strategy() -> MLStrategyReference:
    parameters = (StoredStrategyParameter("period", StrategyParameterKind.INTEGER, 3),)
    return MLStrategyReference(
        "example",
        "1",
        1,
        2,
        parameters,
        strategy_parameter_checksum(parameters),
        "b" * 64,
        implementation(),
    )


def test_identity_is_deterministic_across_processes_and_decimal_contexts() -> None:
    with localcontext() as context:
        context.prec = 6
        low = materialization().identity
    with localcontext() as context:
        context.prec = 50
        assert materialization().identity == low
    script = (
        "from tests.test_ml_dataset_contracts import materialization; "
        "print(materialization().identity)"
    )
    assert subprocess.check_output([sys.executable, "-B", "-c", script], text=True).strip() == low


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "ema_other"),
        ("unit", "percent"),
        ("meaning", "Other output"),
        ("lookback_candles", 4),
        ("warmup_candles", 1),
        ("seed_policy", MLSeedPolicy.FIRST_VALUE),
        ("missing_policy", MLMissingPolicy.PRESERVE_NONE),
        ("source_fields", ("high",)),
        ("algorithm", IndicatorDescriptor("ema", "2", (("period", 3),))),
        ("algorithm", IndicatorDescriptor("ema", "1", (("period", 4),))),
    ],
)
def test_feature_identity_binds_recipe_semantics(field: str, value: object) -> None:
    original = dataset().feature_set.features[0]
    assert replace(original, **{field: value}).identity != original.identity


def test_history_reset_and_implementation_change_identity() -> None:
    spec = dataset()
    feature = spec.feature_set.features[0]
    alternate = replace(
        feature,
        history_policy=MLHistoryPolicy.FROM_CONTEXT_START,
        reset_policy=MLResetPolicy.CONTEXT_START,
    )
    assert feature.identity != alternate.identity
    assert (
        replace(
            spec.feature_set, implementation=replace(implementation(), content_checksum="b" * 64)
        ).identity
        != spec.feature_set.identity
    )


@pytest.mark.parametrize("value", [0, -1, True, 1.0])
def test_target_requires_positive_exact_horizon(value: object) -> None:
    with pytest.raises(MLContractError):
        replace(dataset().target, horizon_candles=value)


@pytest.mark.parametrize("value", [None, "latest", "a" * 63, "A" * 64])
def test_implementation_requires_content_reference(value: object) -> None:
    with pytest.raises(MLContractError):
        replace(implementation(), content_checksum=value)


@pytest.mark.parametrize("value", ["FITTED", "LEARNED", "STANDARD_SCALER", True, None])
def test_fitted_preprocessing_is_rejected(value: object) -> None:
    with pytest.raises(MLContractError):
        replace(dataset().feature_set.features[0], preprocessing=value)


@pytest.mark.parametrize("value", [0, 2, True, "1"])
def test_schema_is_exact(value: object) -> None:
    with pytest.raises(MLUnsupportedSchemaError):
        replace(implementation(), schema_version=value)


@pytest.mark.parametrize(
    "timestamp", [START.replace(tzinfo=None), START.astimezone(timezone(HOUR))]
)
def test_non_utc_instants_rejected(timestamp: datetime) -> None:
    with pytest.raises(MLContractError):
        replace(observation(), observation_time=timestamp)


def test_misaligned_range_and_anchor_rejected() -> None:
    with pytest.raises(MLContractError):
        replace(
            dataset().provenance,
            selected_range=DataRange(instant(1) + timedelta(seconds=1), instant(11)),
        )
    with pytest.raises(MLContractError):
        replace(observation(), candle_open_time=instant(2) + timedelta(milliseconds=1))


def test_candle_close_stamp_does_not_prove_availability() -> None:
    assert candle_available_at(instant(2), "1h") == instant(3)
    with pytest.raises(MLTemporalLeakageError):
        replace(observation(), observation_time=instant(3) - timedelta(milliseconds=1))
    with pytest.raises(MLTemporalLeakageError):
        replace(observation(), observation_time=instant(2))


def test_future_dependency_cannot_be_backdated() -> None:
    current = vector()
    dependency = replace(current.dependencies[0], data_range=DataRange(instant(1), instant(4)))
    assert dependency.information_time == instant(4)
    with pytest.raises(MLTemporalLeakageError):
        replace(current, dependencies=(dependency,))
    with pytest.raises(TypeError):
        MLCandleDependency(dependency.source, dependency.data_range, information_time=instant(2))


def test_historical_dependency_cutoff_is_maximum_and_prefix_local() -> None:
    spec = dataset()
    features = (
        spec.feature_set.features[0],
        replace(spec.feature_set.features[0], name="earlier_ema"),
    )
    spec = replace(spec, feature_set=replace(spec.feature_set, features=features))
    obs = observation(spec)
    current = MLCandleDependency(spec.provenance.source, DataRange(instant(0), instant(3)))
    earlier = replace(current, data_range=DataRange(instant(0), instant(2)))
    bounded = MLFeatureVector(obs, spec.feature_set, (Decimal(1), Decimal(2)), (current, earlier))
    assert bounded.feature_information_time == instant(3)
    # The contract needs only source coverage claims, not future candle values.
    assert bounded.dependencies[1].information_time == instant(2)


def test_dependency_must_match_declared_history_seed_context() -> None:
    current = vector()
    with pytest.raises(MLIncompatibleReferenceError):
        replace(
            current,
            dependencies=(
                replace(current.dependencies[0], data_range=DataRange(instant(1), instant(3))),
            ),
        )


def test_warmup_is_none_never_zero() -> None:
    current = vector(observation(dataset(warmup=2), hour=1))
    assert current.values == (None,)
    with pytest.raises(MLContractError):
        replace(current, values=(Decimal(0),))
    with pytest.raises(MLContractError):
        replace(vector(), values=(None,))


@pytest.mark.parametrize(
    "value",
    [1.25, float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity"), 1, True, "1.25"],
)
def test_decimal_feature_rejects_substitutions(value: object) -> None:
    with pytest.raises(MLContractError):
        replace(vector(), values=(value,))


@pytest.mark.parametrize(
    "kind,value",
    [(MLValueKind.INTEGER, 1), (MLValueKind.BOOLEAN, True), (MLValueKind.CATEGORY, "trend")],
)
def test_supported_typed_features(kind: MLValueKind, value: object) -> None:
    spec = dataset()
    feature_set = replace(
        spec.feature_set, features=(replace(spec.feature_set.features[0], kind=kind),)
    )
    spec = replace(spec, feature_set=feature_set)
    obs = observation(spec)
    dependency = MLCandleDependency(spec.provenance.source, DataRange(instant(0), instant(3)))
    assert MLFeatureVector(obs, feature_set, (value,), (dependency,)).values == (value,)


def test_feature_order_is_semantic_and_cross_reference_exact() -> None:
    first = dataset().feature_set.features[0]
    second = replace(first, name="second")
    a = replace(dataset().feature_set, features=(first, second))
    b = replace(a, features=(second, first))
    assert a.identity != b.identity
    with pytest.raises(MLIncompatibleReferenceError):
        replace(vector(observation(replace(dataset(), feature_set=a))), feature_set=b)
    with pytest.raises(MLContractError):
        replace(a, features=(first, first))


def test_label_separation_and_observation_identity() -> None:
    original = label()
    changed = replace(original, value=Decimal("-0.5"))
    assert original.identity != changed.identity
    assert original.observation.identity == changed.observation.identity
    assert not hasattr(vector(), "label") and not hasattr(vector(), "target")
    with pytest.raises(MLContractError):
        replace(vector(), values=(original,))


@pytest.mark.parametrize("start,end", [(2, 3), (1, 2), (3, 5)])
def test_label_future_evidence_must_match_exact_horizon(start: int, end: int) -> None:
    original = label()
    with pytest.raises(MLTemporalLeakageError):
        replace(
            original,
            future=replace(original.future, data_range=DataRange(instant(start), instant(end))),
        )


def test_training_cutoff_inclusive_maturity() -> None:
    current = label()
    current.validate_training_cutoff(instant(4))
    with pytest.raises(MLTemporalLeakageError):
        current.validate_training_cutoff(instant(4) - timedelta(microseconds=1))
    with pytest.raises(MLTemporalLeakageError):
        replace(split(), training_cutoff=instant(6))
    partition = replace(split(), training_cutoff=instant(3))
    with pytest.raises(MLTemporalLeakageError):
        partition.validate_row(vector(), current, TemporalSegmentRole.TRAIN)


@pytest.mark.parametrize(
    "hour,role",
    [
        (4, TemporalSegmentRole.TRAIN),
        (7, TemporalSegmentRole.VALIDATION),
        (10, TemporalSegmentRole.TEST),
    ],
)
def test_labels_cannot_cross_role_or_fold_end(hour: int, role: TemporalSegmentRole) -> None:
    obs = observation(hour=hour)
    assert split().role_for(obs) is role
    with pytest.raises(MLTemporalLeakageError):
        split().validate_row(vector(obs), label(obs), role)


def test_past_feature_context_is_separately_controlled() -> None:
    obs = observation(hour=5)
    split().validate_row(vector(obs), label(obs), TemporalSegmentRole.VALIDATION)
    with pytest.raises(MLTemporalLeakageError):
        replace(split(), allow_retrospective_context=False).validate_row(
            vector(obs), label(obs), TemporalSegmentRole.VALIDATION
        )


@pytest.mark.parametrize("start", [4, 6])
def test_overlapping_or_gapped_scored_ranges_rejected(start: int) -> None:
    with pytest.raises(MLContractError):
        replace(split(), validation=DataRange(instant(start), instant(8)))


def test_source_contract_reuse_rejects_raw_or_non_strict_identity() -> None:
    original = dataset().provenance
    source = original.source
    object.__setattr__(source, "gap_policy", GapPolicy.SKIP_INCOMPLETE)
    with pytest.raises(MLContractError):
        replace(original, source=source)


def test_source_and_selected_coverage_are_identity_bearing() -> None:
    original = dataset().provenance
    assert (
        replace(original, selected_range=DataRange(instant(2), instant(11))).checksum
        != original.checksum
    )
    assert (
        replace(original, implementation=replace(implementation(), version="2")).checksum
        != original.checksum
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("plugin_name", "other"),
        ("plugin_version", "2"),
        ("lifecycle_version", 1),
        ("configuration_checksum", "c" * 64),
    ],
)
def test_strategy_plugin_configuration_identity_is_not_parameter_identity(
    field: str, value: object
) -> None:
    original = strategy()
    changed = replace(original, **{field: value})
    assert changed.identity != original.identity
    assert changed.parameters_checksum == original.parameters_checksum


def test_strategy_parameter_checksum_and_frozen_binding() -> None:
    original = strategy()
    with pytest.raises(MLContractError):
        replace(original, parameters_checksum="d" * 64)
    obs = observation()
    assert replace(obs, candidate=original).identity != obs.identity
    spec = replace(obs.dataset, provenance=replace(obs.dataset.provenance, strategy=original))
    with pytest.raises(MLIncompatibleReferenceError):
        observation(spec)
    assert replace(obs, dataset=spec, candidate=original).candidate == original


def test_manifest_order_counts_checksums_and_audit_time() -> None:
    original = materialization()
    reverse = replace(original, rows=tuple(reversed(original.rows)))
    assert original == reverse
    assert original.identity == reverse.identity
    assert (original.observation_count, original.label_count, original.exclusion_count) == (2, 2, 1)
    changed_audit = replace(original, built_at=instant(21))
    assert original.identity == changed_audit.identity
    assert original.checksum == changed_audit.checksum
    assert encode_document(original) != encode_document(changed_audit)
    assert original.rows[0].vector_checksum == original.rows[0].vector.checksum
    assert original.rows[0].label_checksum == original.rows[0].label.checksum


def test_manifest_duplicate_observations_even_with_different_labels() -> None:
    original = materialization()
    row = original.rows[0]
    other = replace(row, label=replace(row.label, value=Decimal(9)))
    with pytest.raises(MLDuplicateObservationError):
        replace(original, rows=(row, other))
    with pytest.raises(MLDuplicateObservationError):
        replace(original, exclusions=original.exclusions * 2)
    with pytest.raises(MLDuplicateObservationError):
        replace(
            original,
            exclusions=(
                MLExcludedObservation(row.vector.observation, MLExclusionReason.FEATURE_WARMUP),
            ),
        )


def test_distinct_explicit_candidates_are_distinct_observations() -> None:
    original = materialization()
    obs = replace(original.rows[0].vector.observation, candidate=strategy())
    row = MLMaterializationRow(vector(obs), label(obs), TemporalSegmentRole.TRAIN)
    result = replace(original, rows=(*original.rows, row))
    assert result.observation_count == 3


def test_manifest_validates_supervised_and_unsupervised_membership() -> None:
    assert materialization(supervised=False).label_count == 0
    original = materialization()
    with pytest.raises(MLContractError):
        replace(original, rows=(replace(original.rows[0], label=None),))
    with pytest.raises(MLIncompatibleReferenceError):
        replace(original, rows=(replace(original.rows[0], role=TemporalSegmentRole.TEST),))


def test_false_exclusion_reason_rejected() -> None:
    original = materialization()
    with pytest.raises(MLContractError):
        replace(
            original,
            exclusions=(
                replace(original.exclusions[0], reason=MLExclusionReason.TARGET_OUTSIDE_SOURCE),
            ),
        )


def test_immutable_objects_and_revalidation_of_hostile_mutation() -> None:
    current = vector()
    with pytest.raises(FrozenInstanceError):
        current.values = (Decimal(4),)
    object.__setattr__(current, "feature_information_time", instant(1))
    with pytest.raises(MLContractError):
        validate_contract(current)
    with pytest.raises(MLContractError):
        encode_document(current)


def test_package_has_no_execution_authority_dependencies() -> None:
    root = Path(__file__).parents[1] / "app" / "ml"
    allowed = (
        "app.ml",
        "app.backtesting.reports",
        "app.domain.errors",
        "app.indicators.domain",
        "app.indicators.errors",
        "app.indicators.regime",
        "app.market_data.domain",
        "app.market_data.datasets",
        "app.market_data.timeframes",
        "app.optimization.canonical",
        "app.optimization.temporal_domain",
        "app.strategies.definitions",
        "app.strategies.domain",
    )
    forbidden_roots = {"os", "subprocess", "socket", "httpx", "psycopg", "pathlib"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in modules:
                assert module.split(".")[0] not in forbidden_roots
                if module.startswith("app."):
                    assert any(
                        module == prefix or module.startswith(prefix + ".") for prefix in allowed
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "exec", "eval", "__import__"}


def test_construction_and_codec_do_not_use_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("ML contract attempted external I/O")

    import builtins
    import socket

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    from app.ml.documents import decode_document

    original = materialization()
    assert decode_document(encode_document(original)) == original


def test_unknown_boundary_policy_is_not_silently_accepted() -> None:
    assert split().boundary_policy is MLBoundaryPolicy.REJECT_CROSSING
    with pytest.raises(MLContractError):
        replace(split(), boundary_policy="ALLOW_CROSSING")
