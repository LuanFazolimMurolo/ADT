"""Canonical, lossless serialization helpers for backtest artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite Decimal cannot be serialized")
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def canonical_value(value: object) -> object:
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return {
            "days": value.days,
            "seconds": value.seconds,
            "microseconds": value.microseconds,
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {
            str(key): canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_checksum(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_envelope(path: Path, key: str, value: object) -> None:
    payload = canonical_value(value)
    encoded = canonical_json_bytes(payload)
    envelope: dict[str, Any] = {
        key: payload,
        "checksum": sha256_bytes(encoded),
    }
    path.write_bytes(canonical_json_bytes(envelope))


def read_json_envelope(path: Path, key: str) -> dict[str, Any]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        payload = envelope[key]
        checksum = envelope["checksum"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("invalid JSON envelope") from None
    if not isinstance(payload, dict) or not isinstance(checksum, str):
        raise ValueError("invalid JSON envelope")
    if sha256_bytes(canonical_json_bytes(payload)) != checksum:
        raise ValueError("JSON envelope checksum mismatch")
    return payload
