"""Deterministic model registry, versioning and lineage contracts for Phase 8-08."""

from __future__ import annotations

from dataclasses import dataclass

from app.ml.domain import (
    MLContract,
    MLImplementationReference,
    _nested,
    _require,
    _same,
)
from app.ml.errors import MLContractError
from app.ml.evaluation import MLRegressionEvaluationEvidence
from app.ml.predictions import MLModelReference
from app.ml.training import MLTrainingAlgorithm, MLTrainingResult
from app.optimization.canonical import deterministic_id
from app.optimization.temporal_domain import TemporalSegmentRole


class MLRegistryError(MLContractError):
    code = "ml_registry_error"
    default_message = "O registro ML falhou."


class MLRegistryInputError(MLRegistryError):
    code = "ml_registry_input_error"
    default_message = "A entrada do registro ML é incompatível."


@dataclass(frozen=True, slots=True)
class MLModelLineage(MLContract):
    """Immutable provenance binding one registered model to its exact training origin."""

    training_result_checksum: str
    training_specification_checksum: str
    training_input_checksum: str
    dataset_identity: str
    split_identity: str
    feature_set_identity: str
    target_identity: str
    fitted_model_checksum: str
    algorithm: MLTrainingAlgorithm
    implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        for checksum in (
            self.training_result_checksum,
            self.training_specification_checksum,
            self.training_input_checksum,
            self.fitted_model_checksum,
        ):
            _hash(checksum)

        for identity in (
            self.dataset_identity,
            self.split_identity,
            self.feature_set_identity,
            self.target_identity,
        ):
            _hash(identity)

        _require(type(self.algorithm) is MLTrainingAlgorithm)
        _nested(self.implementation, MLImplementationReference)


@dataclass(frozen=True, slots=True)
class MLEvaluationEvidenceReference(MLContract):
    """Compact immutable reference to one evaluation evidence artifact."""

    role: TemporalSegmentRole
    evidence_checksum: str
    evaluation_input_checksum: str
    sample_count: int

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        if self.role not in (
            TemporalSegmentRole.VALIDATION,
            TemporalSegmentRole.TEST,
        ):
            raise MLRegistryInputError("registry evaluation reference must use VALIDATION or TEST")

        _hash(self.evidence_checksum)
        _hash(self.evaluation_input_checksum)
        _require(type(self.sample_count) is int and self.sample_count >= 1)


@dataclass(frozen=True, slots=True)
class MLModelRegistryRecord(MLContract):
    """Immutable content-addressed registry record for one fitted model version."""

    model_reference: MLModelReference
    lineage: MLModelLineage
    evaluation_evidence: tuple[MLEvaluationEvidenceReference, ...]
    registry_implementation: MLImplementationReference

    def __post_init__(self) -> None:
        MLContract.__post_init__(self)

        _nested(self.model_reference, MLModelReference)
        _nested(self.lineage, MLModelLineage)
        _nested(self.registry_implementation, MLImplementationReference)

        if type(self.evaluation_evidence) is not tuple:
            raise MLRegistryInputError("evaluation evidence references must use a tuple")

        if len(self.evaluation_evidence) > 2:
            raise MLRegistryInputError("registry v1 supports at most VALIDATION and TEST evidence")

        for reference in self.evaluation_evidence:
            _nested(reference, MLEvaluationEvidenceReference)

        roles = tuple(reference.role for reference in self.evaluation_evidence)

        if len(set(roles)) != len(roles):
            raise MLRegistryInputError(
                "registry v1 permits at most one evidence reference per role"
            )

        expected_order = tuple(
            sorted(
                self.evaluation_evidence,
                key=lambda reference: _role_order(reference.role),
            )
        )
        _same(self.evaluation_evidence == expected_order)

        _same(self.model_reference.artifact_checksum == self.lineage.fitted_model_checksum)
        _same(self.model_reference.implementation == self.lineage.implementation)

        expected_model_id = _model_family_id(
            algorithm=self.lineage.algorithm,
            feature_set_identity=self.lineage.feature_set_identity,
            target_identity=self.lineage.target_identity,
            implementation=self.lineage.implementation,
        )
        _same(self.model_reference.model_id == expected_model_id)

        expected_model_version = _model_version_id(
            model_id=expected_model_id,
            fitted_model_checksum=self.lineage.fitted_model_checksum,
            training_input_checksum=self.lineage.training_input_checksum,
        )
        _same(self.model_reference.model_version == expected_model_version)

    @property
    def model_id(self) -> str:
        return self.model_reference.model_id

    @property
    def version(self) -> str:
        return self.model_reference.model_version


def build_model_registry_record(
    training_result: MLTrainingResult,
    *,
    evaluation_evidence: tuple[MLRegressionEvaluationEvidence, ...] = (),
    registry_implementation: MLImplementationReference,
) -> MLModelRegistryRecord:
    """Build one deterministic registry record without persistence or deployment authority."""

    _nested(training_result, MLTrainingResult)
    _nested(registry_implementation, MLImplementationReference)

    model = training_result.fitted_model
    training_specification = training_result.training_specification
    dataset = training_specification.dataset
    split = training_specification.materialization.split
    target = dataset.target

    if target is None:
        raise MLRegistryInputError("model registry requires a supervised training result")

    model_id = _model_family_id(
        algorithm=model.algorithm,
        feature_set_identity=model.feature_set.identity,
        target_identity=model.target.identity,
        implementation=model.implementation,
    )

    version = _model_version_id(
        model_id=model_id,
        fitted_model_checksum=model.checksum,
        training_input_checksum=training_result.training_input_checksum,
    )

    model_reference = MLModelReference(
        model_id=model_id,
        model_version=version,
        artifact_checksum=model.checksum,
        implementation=model.implementation,
    )

    lineage = MLModelLineage(
        training_result_checksum=training_result.checksum,
        training_specification_checksum=training_specification.checksum,
        training_input_checksum=training_result.training_input_checksum,
        dataset_identity=dataset.identity,
        split_identity=split.identity,
        feature_set_identity=model.feature_set.identity,
        target_identity=model.target.identity,
        fitted_model_checksum=model.checksum,
        algorithm=model.algorithm,
        implementation=model.implementation,
    )

    references = tuple(
        sorted(
            (
                _evaluation_reference(
                    evidence,
                    training_result,
                )
                for evidence in evaluation_evidence
            ),
            key=lambda reference: _role_order(reference.role),
        )
    )

    return MLModelRegistryRecord(
        model_reference=model_reference,
        lineage=lineage,
        evaluation_evidence=references,
        registry_implementation=registry_implementation,
    )


def _model_family_id(
    *,
    algorithm: MLTrainingAlgorithm,
    feature_set_identity: str,
    target_identity: str,
    implementation: MLImplementationReference,
) -> str:
    return deterministic_id(
        "adt-ml-model-family-v1",
        {
            "algorithm": algorithm.value,
            "feature_set": feature_set_identity,
            "target": target_identity,
            "implementation": implementation.identity,
        },
    )


def _model_version_id(
    *,
    model_id: str,
    fitted_model_checksum: str,
    training_input_checksum: str,
) -> str:
    return deterministic_id(
        "adt-ml-model-version-v1",
        {
            "model_id": model_id,
            "fitted_model_checksum": fitted_model_checksum,
            "training_input_checksum": training_input_checksum,
        },
    )


def _evaluation_reference(
    evidence: MLRegressionEvaluationEvidence,
    training_result: MLTrainingResult,
) -> MLEvaluationEvidenceReference:
    _nested(evidence, MLRegressionEvaluationEvidence)

    _same(evidence.specification.training_result == training_result)

    return MLEvaluationEvidenceReference(
        role=evidence.specification.role,
        evidence_checksum=evidence.checksum,
        evaluation_input_checksum=evidence.evaluation_input_checksum,
        sample_count=evidence.sample_count,
    )


def _role_order(role: TemporalSegmentRole) -> int:
    if role is TemporalSegmentRole.VALIDATION:
        return 0
    if role is TemporalSegmentRole.TEST:
        return 1
    raise MLRegistryInputError("registry evaluation role is unsupported")


def _hash(value: object) -> None:
    _require(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
