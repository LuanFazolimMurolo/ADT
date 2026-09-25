"""Bounded canonical schema-v1 documents with a closed, non-executable codec.

Only explicitly registered value types are decoded. Type tags are data, never
Python import paths. A logical checksum excludes materialization audit time;
the outer document checksum authenticates every byte of its canonical payload.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, NoReturn, cast

from app.domain.errors import DomainError
from app.indicators.domain import IndicatorDescriptor
from app.market_data.datasets import DatasetIdentity, DatasetKind, GapPolicy
from app.market_data.domain import DataRange, Exchange, MarketType
from app.ml.domain import (
    CONTRACT_TYPES,
    MLBoundaryPolicy,
    MLContract,
    MLDatasetMaterialization,
    MLExclusionReason,
    MLHistoryPolicy,
    MLMissingPolicy,
    MLPreprocessing,
    MLResetPolicy,
    MLSeedPolicy,
    MLValueKind,
    validate_contract,
)
from app.ml.errors import MLCorruptDocumentError, MLUnsupportedSchemaError
from app.ml.predictions import (
    MLModelReference,
    MLPredictionProblemType,
    MLPredictionSpecification,
    MLRegressionPrediction,
)
from app.ml.temporal_safety import MLFitScope
from app.ml.training import (
    MLFittedRegressionModel,
    MLTrainingAlgorithm,
    MLTrainingResult,
    MLTrainingSpecification,
)
from app.optimization.canonical import (
    canonical_json_bytes,
    decimal_text,
    deterministic_id,
    document_checksum,
)
from app.optimization.temporal_domain import (
    TemporalCoverage,
    TemporalSegmentRole,
    TemporalSnapshotReference,
)
from app.strategies.definitions import StoredStrategyParameter
from app.strategies.domain import StrategyParameterKind

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_DEPTH = 40
MAX_DOCUMENT_NODES = 300_000
MAX_COLLECTION_ITEMS = 1_000
MAX_DOCUMENT_STRING = 512

_VALUE_TYPES: tuple[type[Any], ...] = (
    *CONTRACT_TYPES,
    IndicatorDescriptor,
    DataRange,
    DatasetIdentity,
    TemporalCoverage,
    TemporalSnapshotReference,
    StoredStrategyParameter,
    MLModelReference,
    MLPredictionSpecification,
    MLRegressionPrediction,
    MLFitScope,
    MLTrainingSpecification,
    MLFittedRegressionModel,
    MLTrainingResult,
)
_TYPES = {kind.__name__: kind for kind in _VALUE_TYPES}
_ENUM_TYPES = (
    Exchange,
    MarketType,
    DatasetKind,
    GapPolicy,
    TemporalSegmentRole,
    StrategyParameterKind,
    MLValueKind,
    MLHistoryPolicy,
    MLSeedPolicy,
    MLResetPolicy,
    MLMissingPolicy,
    MLPreprocessing,
    MLBoundaryPolicy,
    MLExclusionReason,
    MLPredictionProblemType,
    MLTrainingAlgorithm,
)
_ENUMS = {kind.__name__: kind for kind in _ENUM_TYPES}
_ENVELOPE_FIELDS = {
    "schema_version",
    "payload",
    "logical_checksum",
    "identity",
    "document_checksum",
}


def _fail() -> NoReturn:
    raise MLCorruptDocumentError()


def _encode(value: object, *, logical: bool, depth: int = 0) -> object:
    if depth > MAX_DOCUMENT_DEPTH:
        _fail()
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is Decimal:
        return {"decimal": decimal_text(value)}
    if type(value) is datetime:
        if value.utcoffset() != timedelta(0):
            _fail()
        return {"utc": value.isoformat()}
    if type(value) in _ENUM_TYPES:
        assert isinstance(value, StrEnum)
        return {"enum": type(value).__name__, "value": value.value}
    if type(value) is tuple:
        if len(value) > MAX_COLLECTION_ITEMS:
            _fail()
        return [_encode(item, logical=logical, depth=depth + 1) for item in value]
    if type(value) in _VALUE_TYPES:
        return {
            "type": type(value).__name__,
            "fields": {
                item.name: _encode(getattr(value, item.name), logical=logical, depth=depth + 1)
                for item in fields(cast(Any, value))
                if not (
                    logical and type(value) is MLDatasetMaterialization and item.name == "built_at"
                )
            },
        }
    _fail()


def _check_json_tree(value: object) -> None:
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_DOCUMENT_NODES or depth > MAX_DOCUMENT_DEPTH:
            _fail()
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            if abs(item) >= 10**128:
                _fail()
        elif type(item) is str:
            if len(item) > MAX_DOCUMENT_STRING:
                _fail()
        elif isinstance(item, (list, dict)):
            if len(item) > MAX_COLLECTION_ITEMS:
                _fail()
            if isinstance(item, dict):
                if any(type(key) is not str or len(key) > MAX_DOCUMENT_STRING for key in item):
                    _fail()
                pending.extend((child, depth + 1) for child in item.values())
            else:
                pending.extend((child, depth + 1) for child in item)
        else:
            _fail()


def validate_document_tree(value: MLContract) -> None:
    """Bound and type-check the entire object graph, including external leaves."""
    try:
        encoded = _encode(value, logical=False)
        _check_json_tree(encoded)
        if len(canonical_json_bytes(encoded)) > MAX_DOCUMENT_BYTES:
            _fail()
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise MLCorruptDocumentError() from error


def logical_payload(value: MLContract) -> object:
    validate_contract(value)
    if type(value) is MLRegressionPrediction:
        return {
            "schema_version": value.schema_version,
            "namespace": value.namespace,
            "prediction_specification": logical_payload(value.prediction_specification),
            "model_reference": logical_payload(value.model_reference),
            "observation": logical_payload(value.observation),
            "feature_vector": logical_payload(value.feature_vector),
            "prediction_time": value.prediction_time.isoformat(),
        }
    return _encode(value, logical=True)


def to_document(value: MLContract) -> dict[str, object]:
    """Return a fresh strict envelope; checksums confer no causal authority."""
    logical = logical_payload(value)
    envelope: dict[str, object] = {
        "schema_version": 1,
        "payload": _encode(value, logical=False),
        "logical_checksum": document_checksum(logical),
        "identity": deterministic_id(f"{value.namespace}-{type(value).__name__}-v1", logical),
    }
    envelope["document_checksum"] = document_checksum(envelope)
    if len(canonical_json_bytes(envelope)) > MAX_DOCUMENT_BYTES:
        _fail()
    return envelope


def encode_document(value: MLContract) -> bytes:
    return canonical_json_bytes(to_document(value))


def _decode(value: Any) -> object:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is list:
        return tuple(_decode(item) for item in value)
    if type(value) is not dict:
        _fail()
    if set(value) == {"decimal"}:
        text = value["decimal"]
        if type(text) is not str or len(text) > 128:
            _fail()
        decimal = Decimal(text)
        if decimal_text(decimal) != text:
            _fail()
        return decimal
    if set(value) == {"utc"}:
        text = value["utc"]
        if type(text) is not str:
            _fail()
        timestamp = datetime.fromisoformat(text)
        if timestamp.isoformat() != text or timestamp.utcoffset() != timedelta(0):
            _fail()
        return timestamp
    if set(value) == {"enum", "value"}:
        name, enum_value = value["enum"], value["value"]
        if type(name) is not str or type(enum_value) is not str or name not in _ENUMS:
            _fail()
        return _ENUMS[name](enum_value)
    if set(value) == {"type", "fields"}:
        name, raw_fields = value["type"], value["fields"]
        if type(name) is not str or name not in _TYPES or type(raw_fields) is not dict:
            _fail()
        cls = _TYPES[name]
        specifications = fields(cls)
        if set(raw_fields) != {item.name for item in specifications}:
            _fail()
        decoded = {key: _decode(item) for key, item in raw_fields.items()}
        instance: object = cls(
            **{item.name: decoded[item.name] for item in specifications if item.init}
        )
        # Derived fields, ordering and normalizations must agree exactly.
        if _encode(instance, logical=False) != value:
            _fail()
        return instance
    _fail()


def from_document(envelope: dict[str, object]) -> MLContract:
    """Decode only registered immutable contracts, then revalidate semantics."""
    try:
        _check_json_tree(envelope)
        if type(envelope) is not dict or set(envelope) != _ENVELOPE_FIELDS:
            _fail()
        if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
            raise MLUnsupportedSchemaError()
        if len(canonical_json_bytes(envelope)) > MAX_DOCUMENT_BYTES:
            _fail()
        unsigned = {key: value for key, value in envelope.items() if key != "document_checksum"}
        if document_checksum(unsigned) != envelope["document_checksum"]:
            _fail()
        decoded = _decode(envelope["payload"])
        if type(decoded) not in _VALUE_TYPES:
            _fail()
        if not isinstance(decoded, MLContract):
            _fail()
        if to_document(decoded) != envelope:
            _fail()
        return decoded
    except MLUnsupportedSchemaError:
        raise
    except (
        DomainError,
        TypeError,
        ValueError,
        AttributeError,
        KeyError,
        OverflowError,
        RecursionError,
        InvalidOperation,
    ) as error:
        raise MLCorruptDocumentError() from error


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _reject_number(value: str) -> object:
    del value
    _fail()


def decode_document(data: bytes) -> MLContract:
    """Accept bounded canonical UTF-8 JSON only, rejecting duplicate keys/floats."""
    if type(data) is not bytes or len(data) > MAX_DOCUMENT_BYTES:
        _fail()
    try:
        envelope = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        result = from_document(envelope)
        if encode_document(result) != data:
            _fail()
        return result
    except MLUnsupportedSchemaError:
        raise
    except (TypeError, ValueError, RecursionError) as error:
        raise MLCorruptDocumentError() from error
