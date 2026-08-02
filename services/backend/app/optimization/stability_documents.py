"""Strict canonical codec for Phase 4-06 stability reports."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from app.backtesting.reports import ComparisonMetric
from app.optimization.canonical import canonical_json_bytes, decimal_text
from app.optimization.errors import (
    IncompatibleStabilityDocumentError,
    StabilityAnalysisError,
)
from app.optimization.stability_domain import (
    ExactRatio,
    MetricDistribution,
    OverfittingAssessment,
    ParameterStabilityAssessment,
    StabilityAnalysisKind,
    StabilityAnalysisPolicy,
    StabilityAssessment,
    StabilityControlName,
    StabilityControlResult,
    StabilityFoldObservation,
    StabilityReport,
    stability_report_payload,
    validate_stability_report,
)
from app.optimization.walk_forward_domain import (
    WalkForwardFoldStatus,
    WalkForwardSelectionDirection,
)


def stability_report_to_document(report: StabilityReport) -> dict[str, object]:
    validate_stability_report(report)
    return {
        "stability_report": stability_report_payload(report),
        "checksum": report.checksum,
        "stability_report_id": report.stability_report_id,
    }


def canonical_stability_report_bytes(report: StabilityReport) -> bytes:
    return canonical_json_bytes(stability_report_to_document(report))


def decode_stability_report_document(envelope: Mapping[str, object]) -> StabilityReport:
    root = _mapping(envelope, "stability report envelope")
    _exact(root, {"stability_report", "checksum", "stability_report_id"})
    payload = _mapping(root["stability_report"], "stability report")
    _exact(
        payload,
        {
            "schema_version",
            "walk_forward_plan_id",
            "plan_checksum",
            "walk_forward_execution_id",
            "execution_checksum",
            "policy",
            "observations",
            "fold_count",
            "completed_count",
            "failed_count",
            "completion_ratio",
            "test_not_worse_count",
            "test_not_worse_ratio",
            "parameter_transition_count",
            "parameter_switch_count",
            "parameter_turnover_ratio",
            "validation_distribution",
            "test_distribution",
            "degradation_distribution",
            "controls",
            "overfitting_assessment",
            "parameter_stability_assessment",
            "assessment",
        },
    )
    try:
        report = StabilityReport(
            walk_forward_plan_id=_text(payload["walk_forward_plan_id"]),
            plan_checksum=_text(payload["plan_checksum"]),
            walk_forward_execution_id=_text(payload["walk_forward_execution_id"]),
            execution_checksum=_text(payload["execution_checksum"]),
            policy=_decode_policy(payload["policy"]),
            observations=tuple(
                _decode_observation(item) for item in _list(payload["observations"])
            ),
            fold_count=_integer(payload["fold_count"]),
            completed_count=_integer(payload["completed_count"]),
            failed_count=_integer(payload["failed_count"]),
            completion_ratio=_decode_ratio(payload["completion_ratio"]),
            test_not_worse_count=_integer(payload["test_not_worse_count"]),
            test_not_worse_ratio=_decode_ratio(payload["test_not_worse_ratio"]),
            parameter_transition_count=_integer(payload["parameter_transition_count"]),
            parameter_switch_count=_integer(payload["parameter_switch_count"]),
            parameter_turnover_ratio=_decode_ratio(payload["parameter_turnover_ratio"]),
            validation_distribution=_decode_optional_distribution(
                payload["validation_distribution"]
            ),
            test_distribution=_decode_optional_distribution(payload["test_distribution"]),
            degradation_distribution=_decode_optional_distribution(
                payload["degradation_distribution"]
            ),
            controls=tuple(_decode_control(item) for item in _list(payload["controls"])),
            overfitting_assessment=OverfittingAssessment(
                _text(payload["overfitting_assessment"])
            ),
            parameter_stability_assessment=ParameterStabilityAssessment(
                _text(payload["parameter_stability_assessment"])
            ),
            assessment=StabilityAssessment(_text(payload["assessment"])),
            checksum=_text(root["checksum"]),
            stability_report_id=_text(root["stability_report_id"]),
            schema_version=_integer(payload["schema_version"]),
        )
    except (StabilityAnalysisError, ValueError, TypeError) as error:
        raise IncompatibleStabilityDocumentError(
            "stability report values are invalid"
        ) from error
    if stability_report_payload(report) != dict(payload):
        raise IncompatibleStabilityDocumentError("stability report is not canonical")
    return report


def _decode_policy(raw: object) -> StabilityAnalysisPolicy:
    value = _mapping(raw, "stability policy")
    _exact(
        value,
        {
            "schema_version",
            "kind",
            "metric",
            "direction",
            "minimum_completed_folds",
            "minimum_completion_ratio",
            "minimum_test_not_worse_ratio",
            "maximum_median_degradation",
            "maximum_worst_degradation",
            "maximum_parameter_turnover_ratio",
        },
    )
    return StabilityAnalysisPolicy(
        metric=ComparisonMetric(_text(value["metric"])),
        direction=WalkForwardSelectionDirection(_text(value["direction"])),
        minimum_completed_folds=_integer(value["minimum_completed_folds"]),
        minimum_completion_ratio=_decimal(value["minimum_completion_ratio"]),
        minimum_test_not_worse_ratio=_decimal(value["minimum_test_not_worse_ratio"]),
        maximum_median_degradation=_decimal(value["maximum_median_degradation"]),
        maximum_worst_degradation=_decimal(value["maximum_worst_degradation"]),
        maximum_parameter_turnover_ratio=_decimal(
            value["maximum_parameter_turnover_ratio"]
        ),
        kind=StabilityAnalysisKind(_text(value["kind"])),
        schema_version=_integer(value["schema_version"]),
    )


def _decode_observation(raw: object) -> StabilityFoldObservation:
    value = _mapping(raw, "stability observation envelope")
    _exact(value, {"observation", "checksum", "observation_id"})
    payload = _mapping(value["observation"], "stability observation")
    _exact(
        payload,
        {
            "fold_id",
            "fold_index",
            "source_status",
            "selection_id",
            "combination_index",
            "combination_id",
            "parameter_set_id",
            "validation_score",
            "test_score",
            "degradation",
            "test_not_worse",
            "parameter_changed_from_previous_completed",
        },
    )
    return StabilityFoldObservation(
        fold_id=_text(payload["fold_id"]),
        fold_index=_integer(payload["fold_index"]),
        source_status=WalkForwardFoldStatus(_text(payload["source_status"])),
        selection_id=_optional_text(payload["selection_id"]),
        combination_index=_optional_integer(payload["combination_index"]),
        combination_id=_optional_text(payload["combination_id"]),
        parameter_set_id=_optional_text(payload["parameter_set_id"]),
        validation_score=_optional_decimal(payload["validation_score"]),
        test_score=_optional_decimal(payload["test_score"]),
        degradation=_optional_decimal(payload["degradation"]),
        test_not_worse=_optional_boolean(payload["test_not_worse"]),
        parameter_changed_from_previous_completed=_optional_boolean(
            payload["parameter_changed_from_previous_completed"]
        ),
        checksum=_text(value["checksum"]),
        observation_id=_text(value["observation_id"]),
    )


def _decode_ratio(raw: object) -> ExactRatio:
    value = _mapping(raw, "exact ratio")
    _exact(value, {"numerator", "denominator"})
    return ExactRatio(_integer(value["numerator"]), _integer(value["denominator"]))


def _decode_optional_distribution(raw: object) -> MetricDistribution | None:
    if raw is None:
        return None
    value = _mapping(raw, "metric distribution")
    _exact(value, {"count", "minimum", "median", "maximum"})
    return MetricDistribution(
        _integer(value["count"]),
        _decimal(value["minimum"]),
        _decimal(value["median"]),
        _decimal(value["maximum"]),
    )


def _decode_control(raw: object) -> StabilityControlResult:
    value = _mapping(raw, "stability control")
    _exact(value, {"name", "passed"})
    return StabilityControlResult(
        StabilityControlName(_text(value["name"])),
        _boolean(value["passed"]),
    )


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
        raise IncompatibleStabilityDocumentError(f"{label} must be an object")
    return raw


def _list(raw: object) -> list[object]:
    if not isinstance(raw, list):
        raise IncompatibleStabilityDocumentError("stability sequence must be a list")
    return raw


def _exact(raw: Mapping[str, object], expected: set[str]) -> None:
    if set(raw) != expected:
        raise IncompatibleStabilityDocumentError("stability document fields are incompatible")


def _text(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        raise IncompatibleStabilityDocumentError("stability text is invalid")
    return raw


def _optional_text(raw: object) -> str | None:
    return None if raw is None else _text(raw)


def _integer(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise IncompatibleStabilityDocumentError("stability integer is invalid")
    return raw


def _optional_integer(raw: object) -> int | None:
    return None if raw is None else _integer(raw)


def _boolean(raw: object) -> bool:
    if not isinstance(raw, bool):
        raise IncompatibleStabilityDocumentError("stability boolean is invalid")
    return raw


def _optional_boolean(raw: object) -> bool | None:
    return None if raw is None else _boolean(raw)


def _decimal(raw: object) -> Decimal:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise IncompatibleStabilityDocumentError("stability decimal is invalid")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise IncompatibleStabilityDocumentError("stability decimal is invalid") from None
    if not value.is_finite() or decimal_text(value) != raw:
        raise IncompatibleStabilityDocumentError("stability decimal is not canonical")
    return value


def _optional_decimal(raw: object) -> Decimal | None:
    return None if raw is None else _decimal(raw)
