"""Atomic PREPARED/COMMITTED repository for walk-forward execution manifests."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager
from app.optimization.canonical import canonical_json_bytes
from app.optimization.errors import (
    IncompatibleWalkForwardDocumentError,
    WalkForwardError,
    WalkForwardPublicationError,
)
from app.optimization.walk_forward_documents import (
    canonical_walk_forward_execution_bytes,
    decode_walk_forward_execution_document,
)
from app.optimization.walk_forward_domain import (
    MAX_WALK_FORWARD_MANIFEST_BYTES,
    WalkForwardExecutionManifest,
    validate_walk_forward_execution_manifest,
)

_FINAL_ENTRIES = {"manifest.json", "publication.json"}
_MAX_PUBLICATION_BYTES = 4 * 1024


@dataclass(frozen=True, slots=True)
class WalkForwardExecutionPublication:
    manifest: WalkForwardExecutionManifest
    relative_path: Path
    reused: bool


class WalkForwardRepository:
    """Publish compact immutable references below the configured market root."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("optimization/walk-forward"),
        lock_timeout_seconds: float = 30,
        lock_stale_after_seconds: float = 300,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("walk-forward repository directory must be safe and relative")
        self._market = market_root(data_dir)
        self._root = ensure_safe_path(self._market, self._market / directory)
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
        )

    @property
    def root(self) -> Path:
        return self._root

    def publish(
        self,
        manifest: WalkForwardExecutionManifest,
        *,
        semantic_validator: Callable[
            [WalkForwardExecutionManifest], WalkForwardExecutionManifest
        ]
        | None = None,
    ) -> WalkForwardExecutionPublication:
        validate_walk_forward_execution_manifest(manifest)
        if semantic_validator is None:
            raise WalkForwardPublicationError(
                "walk-forward semantic validation is required before publication"
            )
        _validate_semantics(manifest, semantic_validator)
        encoded = canonical_walk_forward_execution_bytes(manifest)
        if len(encoded) > MAX_WALK_FORWARD_MANIFEST_BYTES:
            raise WalkForwardPublicationError("walk-forward manifest exceeds its byte limit")
        plan_root = ensure_safe_path(self._market, self._root / manifest.walk_forward_plan_id)
        target = ensure_safe_path(
            self._market,
            plan_root / manifest.walk_forward_execution_id,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        plan_root.mkdir(parents=True, exist_ok=True)
        fsync_directory(self._market)
        with self._locks.acquire(f"walk-forward:{manifest.walk_forward_plan_id}"):
            if target.exists():
                try:
                    existing = self._read_directory(target, manifest, "COMMITTED")
                    _validate_semantics(existing, semantic_validator)
                except (IncompatibleWalkForwardDocumentError, WalkForwardError):
                    try:
                        if target.is_dir() and not target.is_symlink():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                        fsync_directory(plan_root)
                    except OSError as error:
                        raise WalkForwardPublicationError(
                            "corrupt walk-forward target cannot be recovered"
                        ) from error
                else:
                    if existing != manifest:
                        raise WalkForwardPublicationError(
                            "published walk-forward identity contains different content"
                        )
                    return WalkForwardExecutionPublication(
                        existing,
                        target.relative_to(self._market),
                        True,
                    )
            staging = ensure_safe_path(
                self._market,
                plan_root
                / f".{manifest.walk_forward_execution_id}.tmp-{os.getpid()}-{uuid4().hex}",
            )
            renamed = False
            try:
                staging.mkdir(parents=False, exist_ok=False)
                _write(staging / "publication.json", _publication_bytes(manifest, "PREPARED"))
                _write(staging / "manifest.json", encoded)
                fsync_directory(staging)
                prepared = self._read_directory(staging, manifest, "PREPARED")
                _validate_semantics(prepared, semantic_validator)
                if prepared != manifest:
                    raise WalkForwardPublicationError("staged walk-forward verification failed")
                _write(staging / "publication.json", _publication_bytes(manifest, "COMMITTED"))
                fsync_directory(staging)
                committed = self._read_directory(staging, manifest, "COMMITTED")
                _validate_semantics(committed, semantic_validator)
                if committed != manifest:
                    raise WalkForwardPublicationError("committed staging verification failed")
                os.replace(staging, target)
                renamed = True
                fsync_directory(plan_root)
                verified = self._read_directory(target, manifest, "COMMITTED")
                _validate_semantics(verified, semantic_validator)
                if verified != manifest:
                    raise WalkForwardPublicationError("post-publication verification failed")
            except Exception as error:
                if staging.exists():
                    shutil.rmtree(staging)
                if renamed and target.exists():
                    shutil.rmtree(target)
                    fsync_directory(plan_root)
                if isinstance(error, WalkForwardPublicationError):
                    raise
                raise WalkForwardPublicationError() from error
        return WalkForwardExecutionPublication(
            verified,
            target.relative_to(self._market),
            False,
        )

    def read(
        self,
        walk_forward_plan_id: str,
        walk_forward_execution_id: str,
    ) -> WalkForwardExecutionManifest:
        root = ensure_safe_path(
            self._market,
            self._root / walk_forward_plan_id / walk_forward_execution_id,
        )
        expected = _ExpectedIdentity(walk_forward_plan_id, walk_forward_execution_id)
        return self._read_directory(root, expected, "COMMITTED")

    def _read_directory(
        self,
        root: Path,
        expected: WalkForwardExecutionManifest | _ExpectedIdentity,
        state: str,
    ) -> WalkForwardExecutionManifest:
        try:
            entries = {path.name for path in root.iterdir()}
            if entries != _FINAL_ENTRIES:
                raise IncompatibleWalkForwardDocumentError(
                    "walk-forward directory entries are incompatible"
                )
            publication = _read_json(root / "publication.json", _MAX_PUBLICATION_BYTES)
            envelope = _read_json(root / "manifest.json", MAX_WALK_FORWARD_MANIFEST_BYTES)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise IncompatibleWalkForwardDocumentError(
                "published walk-forward cannot be read"
            ) from None
        if not isinstance(publication, dict) or not isinstance(envelope, dict):
            raise IncompatibleWalkForwardDocumentError(
                "published walk-forward documents must be objects"
            )
        checksum = envelope.get("checksum")
        if publication != _publication_values(
            expected.walk_forward_plan_id,
            expected.walk_forward_execution_id,
            checksum,
            state,
        ):
            raise IncompatibleWalkForwardDocumentError(
                "walk-forward publication record is incompatible"
            )
        manifest = decode_walk_forward_execution_document(envelope)
        if (
            manifest.walk_forward_plan_id != expected.walk_forward_plan_id
            or manifest.walk_forward_execution_id != expected.walk_forward_execution_id
        ):
            raise IncompatibleWalkForwardDocumentError(
                "walk-forward path diverges from its manifest"
            )
        return manifest


@dataclass(frozen=True, slots=True)
class _ExpectedIdentity:
    walk_forward_plan_id: str
    walk_forward_execution_id: str


def _validate_semantics(
    manifest: WalkForwardExecutionManifest,
    validator: Callable[[WalkForwardExecutionManifest], WalkForwardExecutionManifest],
) -> None:
    try:
        validated = validator(manifest)
    except WalkForwardError:
        raise
    except Exception as error:
        raise WalkForwardPublicationError(
            "walk-forward semantic validation failed"
        ) from error
    if validated != manifest:
        raise WalkForwardPublicationError(
            "walk-forward semantic validator returned incompatible content"
        )


def _publication_bytes(manifest: WalkForwardExecutionManifest, state: str) -> bytes:
    return canonical_json_bytes(
        _publication_values(
            manifest.walk_forward_plan_id,
            manifest.walk_forward_execution_id,
            manifest.checksum,
            state,
        )
    )


def _publication_values(
    plan_id: str,
    execution_id: str,
    checksum: object,
    state: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "state": state,
        "walk_forward_plan_id": plan_id,
        "walk_forward_execution_id": execution_id,
        "manifest_checksum": checksum,
    }


def _write(path: Path, value: bytes) -> None:
    try:
        with path.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise WalkForwardPublicationError() from error


def _read_json(path: Path, maximum: int) -> object:
    if path.stat().st_size > maximum:
        raise IncompatibleWalkForwardDocumentError(
            "published walk-forward document exceeds its byte limit"
        )
    with path.open("rb") as handle:
        encoded = handle.read(maximum + 1)
    if len(encoded) > maximum:
        raise IncompatibleWalkForwardDocumentError(
            "published walk-forward document exceeds its byte limit"
        )
    return json.loads(encoded.decode("utf-8"))
