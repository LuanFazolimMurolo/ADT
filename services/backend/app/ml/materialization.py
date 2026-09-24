"""Deterministic materialization of ML rows from authenticated snapshot candles."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.market_data.timeframes import get_timeframe
from app.ml.domain import (
    MLCandleDependency,
    MLDatasetMaterialization,
    MLDatasetSpecification,
    MLExcludedObservation,
    MLExclusionReason,
    MLFeatureVector,
    MLImplementationReference,
    MLLabel,
    MLMaterializationRow,
    MLObservation,
    MLScalar,
    MLTemporalSplit,
    candle_available_at,
    target_maturity,
)
from app.ml.features import compute_feature
from app.ml.targets import compute_target
from app.optimization.temporal_domain import TemporalSegmentRole


class MLDatasetMaterializationService:
    """Materialize one deterministic ML dataset from an authenticated snapshot."""

    def __init__(
        self,
        *,
        reader_factory: Callable[..., Any] | None = None,
    ) -> None:
        if reader_factory is None:
            raise ValueError("reader_factory is required")
        self._reader_factory = reader_factory

    def build(
        self,
        specification: MLDatasetSpecification,
        split: MLTemporalSplit,
        implementation_reference: MLImplementationReference,
    ) -> MLDatasetMaterialization:
        reader = self._reader_factory(specification, split, implementation_reference)
        snapshot = reader.open_snapshot(specification.provenance.source.snapshot_id)
        if snapshot.snapshot_id != specification.provenance.source.snapshot_id:
            raise ValueError("snapshot identity mismatch")
        if snapshot.checksum != specification.provenance.source.snapshot_checksum:
            raise ValueError("snapshot checksum mismatch")
        context_candles = tuple(reader.iter_candles(specification.provenance.context_range))
        selected_candles = tuple(reader.iter_candles(specification.provenance.selected_range))
        rows: list[MLMaterializationRow] = []
        exclusions: list[MLExcludedObservation] = []
        for candle in selected_candles:
            observation = MLObservation(
                specification,
                candle.open_time,
                candle_available_at(candle.open_time, specification.feature_set.timeframe),
            )
            role = split.role_for(observation)
            values: list[MLScalar] = []
            dependencies: list[MLCandleDependency] = []
            warmup = False
            for feature in specification.feature_set.features:
                value, dependency = compute_feature(
                    feature,
                    context_candles,
                    observation,
                    specification.provenance.context_range,
                )
                values.append(value)
                dependencies.append(dependency)
                warmup = warmup or (
                    value is None
                    and dependency.data_range.end - specification.provenance.context_range.start
                    <= feature.lookback_candles
                    * get_timeframe(specification.feature_set.timeframe).duration
                )
            if warmup:
                exclusions.append(_excluded(observation, MLExclusionReason.FEATURE_WARMUP))
                continue
            vector = MLFeatureVector(
                observation,
                specification.feature_set,
                tuple(values),
                tuple(dependencies),
            )
            label: MLLabel | None = None
            if specification.target is not None:
                label_value, future = compute_target(
                    specification.target,
                    context_candles,
                    observation,
                )
                label = MLLabel(observation, specification.target, future, label_value)
                maturity = target_maturity(observation)
                if maturity > split.coverage_for(role).end:
                    exclusions.append(
                        _excluded(
                            observation,
                            MLExclusionReason.TARGET_CROSSES_BOUNDARY,
                        )
                    )
                    continue
                if role is TemporalSegmentRole.TRAIN and maturity > split.training_cutoff:
                    exclusions.append(
                        _excluded(
                            observation,
                            MLExclusionReason.TARGET_AFTER_TRAINING_CUTOFF,
                        )
                    )
                    continue
                if maturity > specification.provenance.source.available_coverage.end:
                    exclusions.append(
                        _excluded(
                            observation,
                            MLExclusionReason.TARGET_OUTSIDE_SOURCE,
                        )
                    )
                    continue
            rows.append(MLMaterializationRow(vector, label, role))
        reader.verify_unchanged()
        return MLDatasetMaterialization(
            dataset=specification,
            split=split,
            rows=tuple(rows),
            exclusions=tuple(exclusions),
            implementation=implementation_reference,
            built_at=None,
        )


def _excluded(observation: MLObservation, reason: MLExclusionReason) -> MLExcludedObservation:
    return MLExcludedObservation(observation, reason)
