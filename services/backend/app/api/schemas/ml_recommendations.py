"""HTTP projections for immutable ML recommendation evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.ml.domain import MLImplementationReference, MLStrategyReference
from app.ml.recommendations import (
    MLRecommendationEvidenceReference,
    MLStrategyRecommendation,
)
from app.services.ml_recommendations import MLRecommendationPage
from app.strategies.definitions import StoredStrategyParameter


class MLImplementationReferenceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    content_checksum: str

    @classmethod
    def from_domain(
        cls,
        value: MLImplementationReference,
    ) -> MLImplementationReferenceResponse:
        return cls(
            name=value.name,
            version=value.version,
            content_checksum=value.content_checksum,
        )


class MLStrategyParameterResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: str
    value: None | bool | int | str

    @classmethod
    def from_domain(
        cls,
        value: StoredStrategyParameter,
    ) -> MLStrategyParameterResponse:
        return cls(
            name=value.name,
            kind=value.kind.value,
            value=value.value,
        )


class MLStrategyReferenceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: str
    checksum: str
    plugin_name: str
    plugin_version: str
    plugin_schema_version: int
    lifecycle_version: int
    parameters: tuple[MLStrategyParameterResponse, ...]
    parameters_checksum: str
    configuration_checksum: str
    implementation: MLImplementationReferenceResponse

    @classmethod
    def from_domain(
        cls,
        value: MLStrategyReference,
    ) -> MLStrategyReferenceResponse:
        return cls(
            identity=value.identity,
            checksum=value.checksum,
            plugin_name=value.plugin_name,
            plugin_version=value.plugin_version,
            plugin_schema_version=value.plugin_schema_version,
            lifecycle_version=value.lifecycle_version,
            parameters=tuple(
                MLStrategyParameterResponse.from_domain(parameter) for parameter in value.parameters
            ),
            parameters_checksum=value.parameters_checksum,
            configuration_checksum=value.configuration_checksum,
            implementation=MLImplementationReferenceResponse.from_domain(value.implementation),
        )


class MLRecommendationEvidenceReferenceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    evidence_identity: str
    evidence_checksum: str
    information_time: datetime
    strategy_subject_identity: str | None

    @classmethod
    def from_domain(
        cls,
        value: MLRecommendationEvidenceReference,
    ) -> MLRecommendationEvidenceReferenceResponse:
        return cls(
            kind=value.kind.value,
            evidence_identity=value.evidence_identity,
            evidence_checksum=value.evidence_checksum,
            information_time=value.information_time,
            strategy_subject_identity=value.strategy_subject_identity,
        )


class MLRecommendationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    identity: str
    checksum: str
    candidate: MLStrategyReferenceResponse
    evidence: tuple[MLRecommendationEvidenceReferenceResponse, ...]
    rationale_codes: tuple[str, ...]
    recommendation_time: datetime
    implementation: MLImplementationReferenceResponse

    @classmethod
    def from_domain(
        cls,
        value: MLStrategyRecommendation,
    ) -> MLRecommendationResponse:
        return cls(
            identity=value.identity,
            checksum=value.checksum,
            candidate=MLStrategyReferenceResponse.from_domain(value.candidate),
            evidence=tuple(
                MLRecommendationEvidenceReferenceResponse.from_domain(reference)
                for reference in value.evidence
            ),
            rationale_codes=value.rationale_codes,
            recommendation_time=value.recommendation_time,
            implementation=MLImplementationReferenceResponse.from_domain(value.implementation),
        )


class MLRecommendationPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    count: int
    items: tuple[MLRecommendationResponse, ...]

    @classmethod
    def from_domain(
        cls,
        value: MLRecommendationPage,
    ) -> MLRecommendationPageResponse:
        return cls(
            count=value.count,
            items=tuple(
                MLRecommendationResponse.from_domain(recommendation)
                for recommendation in value.items
            ),
        )
