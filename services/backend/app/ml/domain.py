"""Immutable schema-v1 candle dataset contracts, with no I/O or calculators.

Dependency ranges describe claimed complete STRICT candle coverage. Their
availability is derived, not supplied. Authenticating bytes and reproducing a
calculation remain responsibilities of a future trusted materializer.
"""

from __future__ import annotations

import re
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, TypeAlias

from app.domain.errors import DomainError
from app.indicators.domain import IndicatorDescriptor
from app.market_data.domain import DataRange, Timeframe, require_utc
from app.market_data.timeframes import get_timeframe
from app.ml.errors import (
    InvalidMLContractError,
    MLContractError,
    MLDuplicateObservationError,
    MLIncompatibleReferenceError,
    MLTemporalLeakageError,
    MLUnsupportedSchemaError,
)
from app.optimization.canonical import decimal_text, deterministic_id, document_checksum
from app.optimization.temporal_domain import (
    CANONICAL_TEMPORAL_ROLES,
    TemporalSegmentRole,
    TemporalSnapshotReference,
    validate_temporal_snapshot_reference,
)
from app.strategies.definitions import StrategyParameterDocument, strategy_parameter_checksum

MAX_FEATURES = 128
MAX_ROWS = 1_000
MAX_CANDLES = 10_000_000
MAX_TEXT = 256
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_FIELDS = frozenset(
    {"open", "high", "low", "close", "volume", "quote_volume", "trade_count"}
)
MLScalar: TypeAlias = Decimal | int | bool | str | None


class MLValueKind(StrEnum):
    DECIMAL = "DECIMAL"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    CATEGORY = "CATEGORY"


class MLHistoryPolicy(StrEnum):
    TRAILING_WINDOW = "TRAILING_WINDOW"
    FROM_CONTEXT_START = "FROM_CONTEXT_START"


class MLSeedPolicy(StrEnum):
    NONE = "NONE"
    FIRST_VALUE = "FIRST_VALUE"
    ARITHMETIC_MEAN = "ARITHMETIC_MEAN"


class MLResetPolicy(StrEnum):
    PER_OBSERVATION = "PER_OBSERVATION"
    CONTEXT_START = "CONTEXT_START"


class MLMissingPolicy(StrEnum):
    REJECT = "REJECT"
    PRESERVE_NONE = "PRESERVE_NONE"


class MLPreprocessing(StrEnum):
    NONE = "NONE"  # No fitted/learned transform contract exists in v1.


class MLBoundaryPolicy(StrEnum):
    REJECT_CROSSING = "REJECT_CROSSING"


class MLExclusionReason(StrEnum):
    TARGET_CROSSES_BOUNDARY = "TARGET_CROSSES_BOUNDARY"
    TARGET_AFTER_TRAINING_CUTOFF = "TARGET_AFTER_TRAINING_CUTOFF"
    TARGET_OUTSIDE_SOURCE = "TARGET_OUTSIDE_SOURCE"
    FEATURE_WARMUP = "FEATURE_WARMUP"


def _require(condition: bool, message: str = "invalid ML contract") -> None:
    if not condition:
        raise InvalidMLContractError(message)


def _same(condition: bool) -> None:
    if not condition:
        raise MLIncompatibleReferenceError()


def _temporal(condition: bool) -> None:
    if not condition:
        raise MLTemporalLeakageError()


def _text(value: object, *, token: bool = False) -> None:
    _require(type(value) is str)
    assert isinstance(value, str)
    _require(bool(value) and value == value.strip() and len(value) <= MAX_TEXT)
    _require(all(ord(char) >= 32 for char in value))
    if token:
        _require(_TOKEN.fullmatch(value) is not None)


def _hash(value: object) -> None:
    _require(type(value) is str and _SHA256.fullmatch(value) is not None)


def _count(value: object, *, minimum: int = 0, maximum: int = MAX_CANDLES) -> None:
    _require(type(value) is int and minimum <= value <= maximum)


def _tuple(value: object, *, minimum: int = 0, maximum: int = MAX_FEATURES) -> None:
    _require(type(value) is tuple and minimum <= len(value) <= maximum)


def _utc(value: datetime) -> None:
    _require(type(value) is datetime)
    try:
        require_utc(value, field_name="ML timestamp")
    except DomainError as error:
        raise InvalidMLContractError() from error


def _timeframe(code: str) -> Timeframe:
    _text(code, token=True)
    try:
        return get_timeframe(code)
    except DomainError as error:
        raise InvalidMLContractError() from error


def _range(value: DataRange, timeframe: str) -> None:
    _require(type(value) is DataRange)
    _utc(value.start)
    _utc(value.end)
    cadence = _timeframe(timeframe)
    _require(value.start < value.end)
    _require(cadence.validate_open_time(value.start) and cadence.validate_open_time(value.end))
    _count((value.end - value.start) // cadence.duration, minimum=1)


def _inside(inner: DataRange, outer: DataRange) -> bool:
    return outer.start <= inner.start < inner.end <= outer.end


def _scalar(value: MLScalar, kind: MLValueKind, *, unavailable: bool = False) -> None:
    _require(type(kind) is MLValueKind)
    if value is None:
        _require(unavailable, "unavailable value is not permitted")
    elif kind is MLValueKind.DECIMAL:
        _require(type(value) is Decimal)
        assert isinstance(value, Decimal)
        try:
            decimal_text(value)
        except DomainError as error:
            raise InvalidMLContractError() from error
    elif kind is MLValueKind.INTEGER:
        _require(type(value) is int and abs(value) < 10**128)
    elif kind is MLValueKind.BOOLEAN:
        _require(type(value) is bool)
    else:
        _text(value)


def _algorithm(descriptor: IndicatorDescriptor) -> None:
    _require(type(descriptor) is IndicatorDescriptor)
    _tuple(descriptor.parameters)
    _text(descriptor.name, token=True)
    _text(descriptor.version, token=True)
    try:
        _require(
            IndicatorDescriptor(
                descriptor.name,
                descriptor.version,
                descriptor.parameters,
                descriptor.schema_version,
            )
            == descriptor
        )
        for name, value in descriptor.parameters:
            _text(name, token=True)
            if value is not None:
                kind = {
                    Decimal: MLValueKind.DECIMAL,
                    int: MLValueKind.INTEGER,
                    bool: MLValueKind.BOOLEAN,
                    str: MLValueKind.CATEGORY,
                }.get(type(value))
                _require(kind is not None)
                assert kind is not None
                _scalar(value, kind)
    except (DomainError, TypeError, ValueError) as error:
        raise InvalidMLContractError() from error


@dataclass(frozen=True, slots=True, kw_only=True)
class MLContract:
    """Content identity excludes only explicitly nonlogical audit timestamps."""

    schema_version: int = 1
    namespace: ClassVar[str] = "adt-ml"

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise MLUnsupportedSchemaError()

    @property
    def checksum(self) -> str:
        from app.ml.documents import logical_payload

        return document_checksum(logical_payload(self))

    @property
    def identity(self) -> str:
        from app.ml.documents import logical_payload

        return deterministic_id(f"{self.namespace}-{type(self).__name__}-v1", logical_payload(self))


@dataclass(frozen=True, slots=True)
class MLImplementationReference(MLContract):
    """Supplied immutable build/recipe reference, never resolved by these contracts."""

    name: str
    version: str
    content_checksum: str

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _text(self.name, token=True)
        _text(self.version, token=True)
        _hash(self.content_checksum)


@dataclass(frozen=True, slots=True)
class MLStrategyReference(MLContract):
    plugin_name: str
    plugin_version: str
    plugin_schema_version: int
    lifecycle_version: int
    parameters: StrategyParameterDocument
    parameters_checksum: str
    configuration_checksum: str
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _text(self.plugin_name, token=True)
        _text(self.plugin_version, token=True)
        _require(type(self.plugin_schema_version) is int and self.plugin_schema_version == 1)
        _require(type(self.lifecycle_version) is int and self.lifecycle_version in (1, 2))
        _tuple(self.parameters)
        _hash(self.parameters_checksum)
        _hash(self.configuration_checksum)
        _nested(self.implementation, MLImplementationReference)
        try:
            expected = strategy_parameter_checksum(self.parameters)
        except (DomainError, TypeError, ValueError, AttributeError) as error:
            raise InvalidMLContractError() from error
        _same(expected == self.parameters_checksum)
        _require(
            tuple(item.name for item in self.parameters)
            == tuple(sorted(item.name for item in self.parameters))
        )


@dataclass(frozen=True, slots=True)
class MLProvenance(MLContract):
    source: TemporalSnapshotReference
    selected_range: DataRange
    context_range: DataRange
    implementation: MLImplementationReference
    strategy: MLStrategyReference | None = None
    algorithms: tuple[IndicatorDescriptor, ...] = ()

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _source(self.source)
        _range(self.selected_range, self.source.timeframe)
        _range(self.context_range, self.source.timeframe)
        _require(_inside(self.selected_range, self.context_range))
        _require(_inside(self.context_range, self.source.available_coverage.data_range))
        _nested(self.implementation, MLImplementationReference)
        if self.strategy is not None:
            _nested(self.strategy, MLStrategyReference)
        _tuple(self.algorithms)
        for algorithm in self.algorithms:
            _algorithm(algorithm)


@dataclass(frozen=True, slots=True)
class MLFeatureSpecification(MLContract):
    """One output recipe; order lives in the enclosing feature set."""

    name: str
    kind: MLValueKind
    unit: str
    meaning: str
    algorithm: IndicatorDescriptor
    source_fields: tuple[str, ...]
    history_policy: MLHistoryPolicy
    lookback_candles: int
    warmup_candles: int
    seed_policy: MLSeedPolicy
    reset_policy: MLResetPolicy
    missing_policy: MLMissingPolicy
    preprocessing: MLPreprocessing = MLPreprocessing.NONE

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _text(self.name, token=True)
        _text(self.unit)
        _text(self.meaning)
        _require(type(self.kind) is MLValueKind)
        _require(type(self.history_policy) is MLHistoryPolicy)
        _require(type(self.seed_policy) is MLSeedPolicy)
        _require(type(self.reset_policy) is MLResetPolicy)
        _require(type(self.missing_policy) is MLMissingPolicy)
        _require(self.preprocessing is MLPreprocessing.NONE, "fitted preprocessing is unsupported")
        _algorithm(self.algorithm)
        _tuple(self.source_fields, minimum=1, maximum=len(_SOURCE_FIELDS))
        _require(all(type(item) is str and item in _SOURCE_FIELDS for item in self.source_fields))
        _require(len(set(self.source_fields)) == len(self.source_fields))
        _count(self.lookback_candles, minimum=1)
        _count(self.warmup_candles)
        if self.history_policy is MLHistoryPolicy.TRAILING_WINDOW:
            _require(self.reset_policy is MLResetPolicy.PER_OBSERVATION)
            _require(self.warmup_candles < self.lookback_candles)
        else:
            _require(self.reset_policy is MLResetPolicy.CONTEXT_START)


@dataclass(frozen=True, slots=True)
class MLFeatureSetSpecification(MLContract):
    timeframe: str
    features: tuple[MLFeatureSpecification, ...]
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _timeframe(self.timeframe)
        _tuple(self.features, minimum=1)
        for item in self.features:
            _nested(item, MLFeatureSpecification)
        _require(len({item.name for item in self.features}) == len(self.features))
        _nested(self.implementation, MLImplementationReference)


@dataclass(frozen=True, slots=True)
class MLTargetSpecification(MLContract):
    name: str
    algorithm: IndicatorDescriptor
    kind: MLValueKind
    unit: str
    meaning: str
    horizon_candles: int
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _text(self.name, token=True)
        _text(self.unit)
        _text(self.meaning)
        _count(self.horizon_candles, minimum=1)
        _require(type(self.kind) is MLValueKind)
        _algorithm(self.algorithm)
        _nested(self.implementation, MLImplementationReference)


@dataclass(frozen=True, slots=True)
class MLDatasetSpecification(MLContract):
    provenance: MLProvenance
    feature_set: MLFeatureSetSpecification
    target: MLTargetSpecification | None = None

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.provenance, MLProvenance)
        _nested(self.feature_set, MLFeatureSetSpecification)
        _same(self.feature_set.timeframe == self.provenance.source.timeframe)
        if self.target is not None:
            _nested(self.target, MLTargetSpecification)


@dataclass(frozen=True, slots=True)
class MLObservation(MLContract):
    dataset: MLDatasetSpecification
    candle_open_time: datetime
    observation_time: datetime
    candidate: MLStrategyReference | None = None

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.dataset, MLDatasetSpecification)
        _utc(self.candle_open_time)
        _utc(self.observation_time)
        cadence = _timeframe(self.dataset.feature_set.timeframe)
        _require(cadence.validate_open_time(self.candle_open_time))
        _temporal(self.observation_time == candle_available_at(self.candle_open_time, cadence.code))
        selected = self.dataset.provenance.selected_range
        _require(selected.start <= self.candle_open_time < selected.end)
        if self.candidate is not None:
            _nested(self.candidate, MLStrategyReference)
        if self.dataset.provenance.strategy is not None:
            _same(self.candidate == self.dataset.provenance.strategy)


def candle_available_at(open_time: datetime, timeframe: str) -> datetime:
    """Earliest v1 event-time availability; never accepts a stored close stamp."""
    _utc(open_time)
    cadence = _timeframe(timeframe)
    _require(cadence.validate_open_time(open_time))
    try:
        return open_time + cadence.duration
    except OverflowError as error:
        raise InvalidMLContractError() from error


@dataclass(frozen=True, slots=True)
class MLCandleDependency(MLContract):
    source: TemporalSnapshotReference
    data_range: DataRange
    information_time: datetime = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _source(self.source)
        _range(self.data_range, self.source.timeframe)
        _require(_inside(self.data_range, self.source.available_coverage.data_range))
        # Last included open is end-duration; its availability is exactly end.
        object.__setattr__(self, "information_time", self.data_range.end)


@dataclass(frozen=True, slots=True)
class MLFeatureVector(MLContract):
    observation: MLObservation
    feature_set: MLFeatureSetSpecification
    values: tuple[MLScalar, ...]
    dependencies: tuple[MLCandleDependency, ...]
    feature_information_time: datetime = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.observation, MLObservation)
        _nested(self.feature_set, MLFeatureSetSpecification)
        _same(self.feature_set == self.observation.dataset.feature_set)
        _tuple(self.values, minimum=1)
        _tuple(self.dependencies, minimum=1)
        _same(len(self.values) == len(self.dependencies) == len(self.feature_set.features))
        provenance = self.observation.dataset.provenance
        duration = _timeframe(self.feature_set.timeframe).duration
        for recipe, value, dependency in zip(
            self.feature_set.features, self.values, self.dependencies, strict=True
        ):
            _nested(dependency, MLCandleDependency)
            _same(dependency.source == provenance.source)
            _temporal(dependency.information_time <= self.observation.observation_time)
            _require(_inside(dependency.data_range, provenance.context_range))
            available = (dependency.data_range.end - provenance.context_range.start) // duration
            count = (dependency.data_range.end - dependency.data_range.start) // duration
            expected_count = (
                min(available, recipe.lookback_candles)
                if recipe.history_policy is MLHistoryPolicy.TRAILING_WINDOW
                else available
            )
            _same(count == expected_count)
            warming_up = count <= recipe.warmup_candles
            if warming_up:
                _require(value is None, "warmup must remain unavailable, never zero")
            _scalar(
                value,
                recipe.kind,
                unavailable=warming_up or recipe.missing_policy is MLMissingPolicy.PRESERVE_NONE,
            )
        object.__setattr__(
            self,
            "feature_information_time",
            max(item.information_time for item in self.dependencies),
        )


@dataclass(frozen=True, slots=True)
class MLLabel(MLContract):
    observation: MLObservation
    target: MLTargetSpecification
    future: MLCandleDependency
    value: MLScalar
    target_information_time: datetime = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.observation, MLObservation)
        _nested(self.target, MLTargetSpecification)
        _nested(self.future, MLCandleDependency)
        _same(self.target == self.observation.dataset.target)
        _same(self.future.source == self.observation.dataset.provenance.source)
        _temporal(self.future.data_range.start == self.observation.observation_time)
        _temporal(self.future.data_range.end == target_maturity(self.observation))
        _temporal(self.future.information_time > self.observation.observation_time)
        _scalar(self.value, self.target.kind)
        object.__setattr__(self, "target_information_time", self.future.information_time)

    def validate_training_cutoff(self, cutoff: datetime) -> None:
        validate_contract(self)
        _utc(cutoff)
        _temporal(self.target_information_time <= cutoff)


def target_maturity(observation: MLObservation) -> datetime:
    _nested(observation, MLObservation)
    target = observation.dataset.target
    _require(target is not None, "observation has no target specification")
    assert target is not None
    try:
        return (
            observation.observation_time
            + _timeframe(observation.dataset.feature_set.timeframe).duration
            * target.horizon_candles
        )
    except OverflowError as error:
        raise InvalidMLContractError() from error


@dataclass(frozen=True, slots=True)
class MLTemporalSplit(MLContract):
    dataset: MLDatasetSpecification
    train: DataRange
    validation: DataRange
    test: DataRange
    training_cutoff: datetime
    allow_retrospective_context: bool
    boundary_policy: MLBoundaryPolicy = MLBoundaryPolicy.REJECT_CROSSING

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.dataset, MLDatasetSpecification)
        for coverage in (self.train, self.validation, self.test):
            _range(coverage, self.dataset.feature_set.timeframe)
        _require(self.train.end == self.validation.start and self.validation.end == self.test.start)
        selected = self.dataset.provenance.selected_range
        _same(self.train.start == selected.start and self.test.end == selected.end)
        _utc(self.training_cutoff)
        _temporal(self.train.start <= self.training_cutoff <= self.train.end)
        _require(type(self.allow_retrospective_context) is bool)
        _require(self.boundary_policy is MLBoundaryPolicy.REJECT_CROSSING)

    def coverage_for(self, role: TemporalSegmentRole) -> DataRange:
        _require(type(role) is TemporalSegmentRole)
        return (self.train, self.validation, self.test)[CANONICAL_TEMPORAL_ROLES.index(role)]

    def role_for(self, observation: MLObservation) -> TemporalSegmentRole:
        _same(observation.dataset == self.dataset)
        for role in CANONICAL_TEMPORAL_ROLES:
            coverage = self.coverage_for(role)
            if coverage.start <= observation.candle_open_time < coverage.end:
                return role
        raise MLIncompatibleReferenceError()

    def validate_row(
        self, vector: MLFeatureVector, label: MLLabel | None, role: TemporalSegmentRole
    ) -> None:
        validate_contract(self)
        _nested(vector, MLFeatureVector)
        _same(role is self.role_for(vector.observation))
        coverage = self.coverage_for(role)
        if not self.allow_retrospective_context:
            _temporal(all(item.data_range.start >= coverage.start for item in vector.dependencies))
        if self.dataset.target is None:
            _same(label is None)
        else:
            _require(label is not None, "supervised membership requires a mature label")
            assert label is not None
            _nested(label, MLLabel)
            _same(label.observation == vector.observation)
            _temporal(_inside(label.future.data_range, coverage))
            if role is TemporalSegmentRole.TRAIN:
                label.validate_training_cutoff(self.training_cutoff)


@dataclass(frozen=True, slots=True)
class MLMaterializationRow(MLContract):
    vector: MLFeatureVector
    label: MLLabel | None
    role: TemporalSegmentRole
    observation_id: str = field(init=False)
    vector_checksum: str = field(init=False)
    label_checksum: str | None = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.vector, MLFeatureVector)
        _require(type(self.role) is TemporalSegmentRole)
        if self.label is not None:
            _nested(self.label, MLLabel)
            _same(self.label.observation == self.vector.observation)
        object.__setattr__(self, "observation_id", self.vector.observation.identity)
        object.__setattr__(self, "vector_checksum", self.vector.checksum)
        object.__setattr__(
            self, "label_checksum", None if self.label is None else self.label.checksum
        )


@dataclass(frozen=True, slots=True)
class MLExcludedObservation(MLContract):
    observation: MLObservation
    reason: MLExclusionReason

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.observation, MLObservation)
        _require(type(self.reason) is MLExclusionReason)


@dataclass(frozen=True, slots=True)
class MLDatasetMaterialization(MLContract):
    dataset: MLDatasetSpecification
    split: MLTemporalSplit
    rows: tuple[MLMaterializationRow, ...]
    exclusions: tuple[MLExcludedObservation, ...]
    implementation: MLImplementationReference
    built_at: datetime | None = None
    observation_count: int = field(init=False)
    label_count: int = field(init=False)
    exclusion_count: int = field(init=False)

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        _nested(self.dataset, MLDatasetSpecification)
        _nested(self.split, MLTemporalSplit)
        _same(self.split.dataset == self.dataset)
        _nested(self.implementation, MLImplementationReference)
        _tuple(self.rows, maximum=MAX_ROWS)
        _tuple(self.exclusions, maximum=MAX_ROWS)
        _count(len(self.rows) + len(self.exclusions), minimum=1, maximum=MAX_ROWS)
        if self.built_at is not None:
            _utc(self.built_at)
        seen: set[str] = set()
        for row in self.rows:
            _nested(row, MLMaterializationRow)
            self.split.validate_row(row.vector, row.label, row.role)
            _unique(row.vector.observation, seen)
        for exclusion in self.exclusions:
            _nested(exclusion, MLExcludedObservation)
            _same(exclusion.observation.dataset == self.dataset)
            _unique(exclusion.observation, seen)
            _validate_exclusion(exclusion, self.split)
        object.__setattr__(
            self,
            "rows",
            tuple(sorted(self.rows, key=lambda row: _observation_order(row.vector.observation))),
        )
        object.__setattr__(
            self,
            "exclusions",
            tuple(sorted(self.exclusions, key=lambda item: _observation_order(item.observation))),
        )
        object.__setattr__(self, "observation_count", len(self.rows))
        object.__setattr__(self, "label_count", sum(row.label is not None for row in self.rows))
        object.__setattr__(self, "exclusion_count", len(self.exclusions))


def _observation_order(observation: MLObservation) -> tuple[datetime, str]:
    return observation.candle_open_time, observation.identity


def _unique(observation: MLObservation, seen: set[str]) -> None:
    identity = observation.identity
    if identity in seen:
        raise MLDuplicateObservationError()
    seen.add(identity)


def _validate_exclusion(exclusion: MLExcludedObservation, split: MLTemporalSplit) -> None:
    observation = exclusion.observation
    role = split.role_for(observation)
    if exclusion.reason is MLExclusionReason.FEATURE_WARMUP:
        count = (
            observation.observation_time - observation.dataset.provenance.context_range.start
        ) // _timeframe(observation.dataset.feature_set.timeframe).duration
        _require(
            any(
                (
                    min(count, feature.lookback_candles)
                    if feature.history_policy is MLHistoryPolicy.TRAILING_WINDOW
                    else count
                )
                <= feature.warmup_candles
                for feature in observation.dataset.feature_set.features
            )
        )
    else:
        maturity = target_maturity(observation)
        if exclusion.reason is MLExclusionReason.TARGET_CROSSES_BOUNDARY:
            _require(maturity > split.coverage_for(role).end)
        elif exclusion.reason is MLExclusionReason.TARGET_AFTER_TRAINING_CUTOFF:
            _require(role is TemporalSegmentRole.TRAIN and maturity > split.training_cutoff)
        else:
            _require(maturity > observation.dataset.provenance.source.available_coverage.end)


def _source(source: TemporalSnapshotReference) -> None:
    _require(type(source) is TemporalSnapshotReference)
    try:
        validate_temporal_snapshot_reference(source)
    except (DomainError, TypeError, ValueError, AttributeError) as error:
        raise InvalidMLContractError() from error


def _nested(value: MLContract, expected: type[MLContract]) -> None:
    _require(type(value) is expected)
    # Reconstruct checks on a copy, rejecting hostile frozen-object mutation.
    candidate = copy(value)
    candidate.__post_init__()
    _require(candidate == value, "noncanonical or mutated nested contract")


def validate_contract(value: MLContract) -> None:
    """Revalidate before encoding/consumption, including derived metadata."""
    try:
        _require(type(value) in CONTRACT_TYPES)
        _nested(value, type(value))
        # Also bound every leaf, including reused external immutable contracts.
        from app.ml.documents import validate_document_tree

        validate_document_tree(value)
    except MLContractError:
        raise
    except (
        DomainError,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,
        RecursionError,
    ) as error:
        raise InvalidMLContractError() from error


CONTRACT_TYPES: tuple[type[MLContract], ...] = (
    MLImplementationReference,
    MLStrategyReference,
    MLProvenance,
    MLFeatureSpecification,
    MLFeatureSetSpecification,
    MLTargetSpecification,
    MLDatasetSpecification,
    MLObservation,
    MLCandleDependency,
    MLFeatureVector,
    MLLabel,
    MLTemporalSplit,
    MLMaterializationRow,
    MLExcludedObservation,
    MLDatasetMaterialization,
)
