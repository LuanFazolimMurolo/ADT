"""Deterministic asset/timeframe/regime intelligence for Phase 8-13."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.indicators.regime import MarketRegimeKind, TrendDirection
from app.market_data.datasets import DatasetIdentity
from app.ml.domain import (
    MLContract,
    MLImplementationReference,
    _nested,
    _same,
)
from app.ml.regimes import MLMarketRegimeClassification
from app.optimization.temporal_domain import TemporalSnapshotReference


@dataclass(frozen=True, slots=True)
class MLMarketContextIntelligence(MLContract):
    """Exact market context derived from one canonical regime classification."""

    classification: MLMarketRegimeClassification
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.classification, MLMarketRegimeClassification)
        _nested(self.implementation, MLImplementationReference)
        _same(self.classification.specification.implementation == self.implementation)

    @property
    def source(self) -> TemporalSnapshotReference:
        return self.classification.observation.dataset.provenance.source

    @property
    def dataset_identity(self) -> DatasetIdentity:
        return self.source.identity

    @property
    def symbol(self) -> str:
        return self.dataset_identity.symbol

    @property
    def timeframe(self) -> str:
        return self.dataset_identity.timeframe

    @property
    def information_time(self) -> datetime:
        return self.classification.observation.observation_time

    @property
    def regime(self) -> MarketRegimeKind:
        return self.classification.regime

    @property
    def trend_direction(self) -> TrendDirection:
        return self.classification.trend_direction


def market_context_intelligence(
    classification: MLMarketRegimeClassification,
) -> MLMarketContextIntelligence:
    """Project canonical regime evidence into exact market-context intelligence."""

    _nested(classification, MLMarketRegimeClassification)

    return MLMarketContextIntelligence(
        classification=classification,
        implementation=classification.specification.implementation,
    )
