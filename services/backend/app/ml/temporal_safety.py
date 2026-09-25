"""Minimal deterministic fit-scope boundary for TRAIN-only model fitting.

This contract is intentionally a provenance gate: it defines which data may be
used to fit a future model, but it does not implement fitting, preprocessing,
or runtime execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.ml.domain import (
    MLContract,
    MLFeatureVector,
    MLLabel,
    MLTemporalSplit,
    validate_contract,
)
from app.optimization.temporal_domain import TemporalSegmentRole


@dataclass(frozen=True, slots=True)
class MLFitScope(MLContract):
    """TRAIN-only permission boundary for future model-fitting inputs."""

    split: MLTemporalSplit

    @property
    def dataset(self) -> object:
        return self.split.dataset

    @property
    def role(self) -> TemporalSegmentRole:
        return TemporalSegmentRole.TRAIN

    @property
    def coverage(self) -> object:
        return self.split.train

    @property
    def training_cutoff(self) -> datetime:
        return self.split.training_cutoff

    @property
    def allow_retrospective_context(self) -> bool:
        return self.split.allow_retrospective_context

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)
        from app.ml import domain as ml_domain

        ml_domain._nested(self.split, MLTemporalSplit)
        train_coverage = self.split.coverage_for(TemporalSegmentRole.TRAIN)
        ml_domain._require(
            train_coverage.start <= self.split.training_cutoff <= train_coverage.end,
            "fit scope requires a training cutoff inside the training segment",
        )

    def validate_row(self, vector: MLFeatureVector, label: MLLabel | None = None) -> None:
        validate_contract(self)
        from app.ml import domain as ml_domain

        ml_domain._nested(vector, MLFeatureVector)
        ml_domain._same(self.split.dataset == vector.observation.dataset)
        ml_domain._same(self.split.role_for(vector.observation) is TemporalSegmentRole.TRAIN)
        if self.split.dataset.target is None:
            ml_domain._same(label is None)
        else:
            ml_domain._require(label is not None, "supervised fitting requires a mature label")
            assert label is not None
            ml_domain._nested(label, MLLabel)
            ml_domain._same(label.observation == vector.observation)
        self.split.validate_row(vector, label, TemporalSegmentRole.TRAIN)
        return None
