"""Canonical ML documents reject corrupt bytes and re-signed semantic attacks."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from app.ml.documents import (
    MAX_DOCUMENT_BYTES,
    decode_document,
    encode_document,
    from_document,
    to_document,
)
from app.ml.domain import MLContract, MLExcludedObservation, MLExclusionReason
from app.ml.errors import MLContractError, MLCorruptDocumentError, MLUnsupportedSchemaError
from app.optimization.canonical import canonical_json_bytes, deterministic_id, document_checksum
from tests.test_ml_dataset_contracts import (
    dataset,
    implementation,
    instant,
    label,
    materialization,
    observation,
    split,
    strategy,
    vector,
)


def contracts() -> tuple[MLContract, ...]:
    spec = dataset()
    return (
        implementation(),
        strategy(),
        spec.provenance,
        spec.feature_set.features[0],
        spec.feature_set,
        spec.target,
        spec,
        observation(),
        vector().dependencies[0],
        vector(),
        label(),
        split(),
        materialization().rows[0],
        MLExcludedObservation(observation(hour=4), MLExclusionReason.TARGET_CROSSES_BOUNDARY),
        materialization(),
    )


def resign(document: dict[str, object]) -> bytes:
    """Attacker can hash; semantic validity must still be independently checked."""
    payload = copy.deepcopy(document["payload"])
    if payload["type"] == "MLDatasetMaterialization":
        del payload["fields"]["built_at"]
    document["logical_checksum"] = document_checksum(payload)
    document["identity"] = deterministic_id(f"adt-ml-{payload['type']}-v1", payload)
    document["document_checksum"] = document_checksum(
        {key: value for key, value in document.items() if key != "document_checksum"}
    )
    return canonical_json_bytes(document)


@pytest.mark.parametrize("index", range(15))
def test_all_public_value_objects_round_trip(index: int) -> None:
    original = contracts()[index]
    data = encode_document(original)
    rebuilt = decode_document(data)
    assert rebuilt == original
    assert encode_document(rebuilt) == data
    assert from_document(to_document(original)) == original
    assert rebuilt.identity == original.identity
    assert rebuilt.checksum == original.checksum


@pytest.mark.parametrize("index", range(15))
def test_unknown_fields_rejected_at_every_contract(index: int) -> None:
    document = to_document(contracts()[index])
    document["payload"]["fields"]["verified"] = True
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


@pytest.mark.parametrize("index", range(15))
def test_missing_required_schema_at_every_contract(index: int) -> None:
    document = to_document(contracts()[index])
    del document["payload"]["fields"]["schema_version"]
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


@pytest.mark.parametrize("index", range(15))
def test_unsupported_schema_at_every_contract(index: int) -> None:
    document = to_document(contracts()[index])
    document["payload"]["fields"]["schema_version"] = 2
    with pytest.raises(MLUnsupportedSchemaError):
        decode_document(resign(document))


@pytest.mark.parametrize("field", ["identity", "logical_checksum", "document_checksum"])
def test_checksum_tampering_rejected(field: str) -> None:
    document = to_document(vector())
    document[field] = "0" * 64
    with pytest.raises(MLCorruptDocumentError):
        decode_document(canonical_json_bytes(document))


def test_decimal_is_exact_typed_and_context_independent() -> None:
    a = replace(label(), value=Decimal("0.125000"))
    b = replace(a, value=Decimal("0.125"))
    assert encode_document(a) == encode_document(b)
    assert to_document(a)["payload"]["fields"]["value"] == {"decimal": "0.125"}


@pytest.mark.parametrize("value", ["0.1250", "1e-3", "-0", "NaN", "Infinity", "1e999999999"])
def test_noncanonical_decimal_rejected(value: str) -> None:
    document = to_document(label())
    document["payload"]["fields"]["value"] = {"decimal": value}
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


@pytest.mark.parametrize("value", [0.125, 1, True, "0.125", None])
def test_decimal_type_substitution_rejected(value: object) -> None:
    document = to_document(label())
    document["payload"]["fields"]["value"] = value
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


@pytest.mark.parametrize(
    "timestamp", ["2026-01-01T03:00:00", "2026-01-01T03:00:00Z", "2026-01-01T04:00:00+01:00"]
)
def test_noncanonical_or_non_utc_timestamp_rejected(timestamp: str) -> None:
    document = to_document(observation())
    document["payload"]["fields"]["observation_time"] = {"utc": timestamp}
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


def test_duplicate_keys_rejected_even_when_values_equal() -> None:
    data = encode_document(implementation())
    data = data.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1)
    with pytest.raises(MLCorruptDocumentError):
        decode_document(data)


@pytest.mark.parametrize(
    "data", [b"NaN", b"Infinity", b"-Infinity", b"1.0", b"[]", b"null", b"{}", b"\xff", b"[" * 2000]
)
def test_invalid_json_shapes_numbers_and_depth(data: bytes) -> None:
    with pytest.raises(MLCorruptDocumentError):
        decode_document(data)


def test_bounded_document_strings_lists_and_integers() -> None:
    with pytest.raises(MLCorruptDocumentError):
        decode_document(b" " * (MAX_DOCUMENT_BYTES + 1))
    for value in ("x" * 513, [0] * 1001, 10**128):
        document = to_document(implementation())
        document["payload"]["fields"]["name"] = value
        with pytest.raises(MLCorruptDocumentError):
            decode_document(resign(document))


def test_materialization_audit_time_authenticated_but_not_logical_identity() -> None:
    first = materialization()
    second = replace(first, built_at=instant(99))
    a, b = to_document(first), to_document(second)
    assert a["identity"] == b["identity"]
    assert a["logical_checksum"] == b["logical_checksum"]
    assert a["document_checksum"] != b["document_checksum"]
    assert decode_document(encode_document(second)) == second


@pytest.mark.parametrize("field", ["observation_count", "label_count", "exclusion_count"])
def test_resigned_false_manifest_counts_rejected(field: str) -> None:
    document = to_document(materialization())
    document["payload"]["fields"][field] += 1
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


def test_resigned_backdated_dependency_and_feature_time_rejected() -> None:
    for contract, field in (
        (vector().dependencies[0], "information_time"),
        (vector(), "feature_information_time"),
        (label(), "target_information_time"),
    ):
        document = to_document(contract)
        document["payload"]["fields"][field] = {"utc": instant(1).isoformat()}
        with pytest.raises(MLCorruptDocumentError):
            decode_document(resign(document))


def test_resigned_future_feature_even_with_consistent_derived_time_rejected() -> None:
    document = to_document(vector())
    raw = document["payload"]["fields"]
    dependency = raw["dependencies"][0]["fields"]
    dependency["data_range"]["fields"]["start"] = {"utc": instant(1).isoformat()}
    dependency["data_range"]["fields"]["end"] = {"utc": instant(4).isoformat()}
    dependency["information_time"] = {"utc": instant(4).isoformat()}
    raw["feature_information_time"] = {"utc": instant(4).isoformat()}
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


def test_resigned_cutoff_violation_at_manifest_boundary_rejected() -> None:
    document = to_document(materialization())
    document["payload"]["fields"]["split"]["fields"]["training_cutoff"] = {
        "utc": instant(3).isoformat()
    }
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


def test_resigned_overlap_and_role_reassignment_rejected() -> None:
    document = to_document(split())
    document["payload"]["fields"]["validation"]["fields"]["start"] = {"utc": instant(4).isoformat()}
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))
    document = to_document(materialization())
    document["payload"]["fields"]["rows"][0]["fields"]["role"]["value"] = "TEST"
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


def test_resigned_duplicate_membership_rejected() -> None:
    document = to_document(materialization())
    raw = document["payload"]["fields"]
    raw["rows"].append(copy.deepcopy(raw["rows"][0]))
    raw["observation_count"] += 1
    raw["label_count"] += 1
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


def test_resigned_cross_reference_and_value_order_changes_rejected() -> None:
    document = to_document(vector())
    document["payload"]["fields"]["feature_set"]["fields"]["features"][0]["fields"]["name"] = (
        "changed"
    )
    with pytest.raises(MLCorruptDocumentError):
        decode_document(resign(document))


def test_no_dynamic_import_or_unknown_type_tags() -> None:
    document = to_document(implementation())
    document["payload"]["type"] = "app.paper_trading.service.PaperTradingService"
    with pytest.raises(MLContractError):
        decode_document(resign(document))


def test_mutating_document_does_not_mutate_original() -> None:
    original = vector()
    before = encode_document(original)
    document = to_document(original)
    document["payload"]["fields"]["values"][0] = {"decimal": "999"}
    assert encode_document(original) == before


def test_mapping_key_order_is_irrelevant_but_bytes_are_canonical() -> None:
    original = vector()
    document = to_document(original)
    reordered = dict(reversed(list(document.items())))
    assert from_document(reordered) == original
    with pytest.raises(MLCorruptDocumentError):
        decode_document(json.dumps(document, indent=2).encode())
