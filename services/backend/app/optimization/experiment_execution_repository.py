"""Atomic PREPARED/COMMITTED publication of experiment execution manifests."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager
from app.optimization.canonical import canonical_json_bytes
from app.optimization.errors import (
    ExperimentExecutionPublicationError,
    IncompatibleExperimentExecutionDocumentError,
)
from app.optimization.experiment_execution_documents import (
    canonical_experiment_execution_document_bytes,
    decode_experiment_execution_document,
)
from app.optimization.experiment_execution_domain import (
    MAX_EXECUTION_MANIFEST_BYTES,
    ExperimentExecutionManifest,
    validate_experiment_execution_manifest,
)

_FINAL_ENTRIES = {"manifest.json", "publication.json"}
_MAX_PUBLICATION_RECORD_BYTES = 4 * 1024


@dataclass(frozen=True, slots=True)
class ExperimentExecutionPublication:
    manifest: ExperimentExecutionManifest
    relative_path: Path
    reused: bool


class ExperimentExecutionRepository:
    """Publish immutable manifests below the configured ADT market root."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("optimization/experiments"),
        lock_timeout_seconds: float = 30,
        lock_stale_after_seconds: float = 300,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("experiment execution directory must be safe and relative")
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

    def publish(self, manifest: ExperimentExecutionManifest) -> ExperimentExecutionPublication:
        # Validation must precede serialization, path derivation, locks and all filesystem writes.
        validate_experiment_execution_manifest(manifest)
        encoded = canonical_experiment_execution_document_bytes(manifest)
        if len(encoded) > MAX_EXECUTION_MANIFEST_BYTES:
            raise ExperimentExecutionPublicationError("execution manifest is too large")
        experiment_root = ensure_safe_path(self._market, self._root / manifest.experiment_id)
        target = ensure_safe_path(self._market, experiment_root / manifest.experiment_execution_id)
        self._root.mkdir(parents=True, exist_ok=True)
        experiment_root.mkdir(parents=True, exist_ok=True)
        fsync_directory(self._market)
        with self._locks.acquire(f"experiment-execution:{manifest.experiment_id}"):
            if target.exists():
                existing = self.read(manifest.experiment_id, manifest.experiment_execution_id)
                if existing != manifest:
                    raise ExperimentExecutionPublicationError(
                        "published execution identity contains different content"
                    )
                return ExperimentExecutionPublication(
                    manifest=existing,
                    relative_path=target.relative_to(self._market),
                    reused=True,
                )
            staging = ensure_safe_path(
                self._market,
                experiment_root
                / f".{manifest.experiment_execution_id}.tmp-{os.getpid()}-{uuid4().hex}",
            )
            renamed = False
            try:
                staging.mkdir(parents=False, exist_ok=False)
                _write_bytes(
                    staging / "publication.json",
                    canonical_json_bytes(_publication_record(manifest, "PREPARED")),
                )
                _write_bytes(staging / "manifest.json", encoded)
                fsync_directory(staging)
                prepared = self._read_directory(
                    staging,
                    manifest.experiment_id,
                    manifest.experiment_execution_id,
                    state="PREPARED",
                )
                if prepared != manifest:
                    raise ExperimentExecutionPublicationError(
                        "staged execution verification failed"
                    )
                _write_bytes(
                    staging / "publication.json",
                    canonical_json_bytes(_publication_record(manifest, "COMMITTED")),
                )
                fsync_directory(staging)
                committed = self._read_directory(
                    staging,
                    manifest.experiment_id,
                    manifest.experiment_execution_id,
                    state="COMMITTED",
                )
                if committed != manifest:
                    raise ExperimentExecutionPublicationError(
                        "committed staging verification failed"
                    )
                os.replace(staging, target)
                renamed = True
                fsync_directory(experiment_root)
                verified = self._read_directory(
                    target,
                    manifest.experiment_id,
                    manifest.experiment_execution_id,
                    state="COMMITTED",
                )
                if verified != manifest:
                    raise ExperimentExecutionPublicationError(
                        "post-publication verification failed"
                    )
            except Exception as error:
                if staging.exists():
                    shutil.rmtree(staging)
                if renamed and target.exists():
                    shutil.rmtree(target)
                    fsync_directory(experiment_root)
                if isinstance(error, ExperimentExecutionPublicationError):
                    raise
                raise ExperimentExecutionPublicationError() from error
        return ExperimentExecutionPublication(
            manifest=verified,
            relative_path=target.relative_to(self._market),
            reused=False,
        )

    def read(self, experiment_id: str, experiment_execution_id: str) -> ExperimentExecutionManifest:
        root = ensure_safe_path(self._market, self._root / experiment_id / experiment_execution_id)
        return self._read_directory(
            root,
            experiment_id,
            experiment_execution_id,
            state="COMMITTED",
        )

    def _read_directory(
        self,
        root: Path,
        experiment_id: str,
        experiment_execution_id: str,
        *,
        state: str,
    ) -> ExperimentExecutionManifest:
        try:
            entries: set[str] = set()
            for position, path in enumerate(root.iterdir()):
                if position >= len(_FINAL_ENTRIES):
                    raise IncompatibleExperimentExecutionDocumentError(
                        "execution directory entries are incompatible"
                    )
                entries.add(path.name)
            if entries != _FINAL_ENTRIES:
                raise IncompatibleExperimentExecutionDocumentError(
                    "execution directory entries are incompatible"
                )
            publication = _read_bounded_json(
                root / "publication.json", _MAX_PUBLICATION_RECORD_BYTES
            )
            envelope = _read_bounded_json(root / "manifest.json", MAX_EXECUTION_MANIFEST_BYTES)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise IncompatibleExperimentExecutionDocumentError(
                "published execution cannot be read"
            ) from None
        if not isinstance(publication, dict) or not isinstance(envelope, dict):
            raise IncompatibleExperimentExecutionDocumentError(
                "published execution documents must be objects"
            )
        if publication != _publication_record_values(
            experiment_id, experiment_execution_id, envelope.get("checksum"), state
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "execution publication record is incompatible"
            )
        manifest = decode_experiment_execution_document(envelope)
        if (
            manifest.experiment_id != experiment_id
            or manifest.experiment_execution_id != experiment_execution_id
        ):
            raise IncompatibleExperimentExecutionDocumentError(
                "execution path diverges from its manifest"
            )
        return manifest


def _publication_record(manifest: ExperimentExecutionManifest, state: str) -> dict[str, object]:
    return _publication_record_values(
        manifest.experiment_id,
        manifest.experiment_execution_id,
        manifest.checksum,
        state,
    )


def _publication_record_values(
    experiment_id: str,
    execution_id: str,
    checksum: object,
    state: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "state": state,
        "experiment_id": experiment_id,
        "experiment_execution_id": execution_id,
        "manifest_checksum": checksum,
    }


def _write_bytes(path: Path, value: bytes) -> None:
    try:
        with path.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise ExperimentExecutionPublicationError() from error


def _read_bounded_json(path: Path, maximum_bytes: int) -> object:
    size = path.stat().st_size
    if size > maximum_bytes:
        raise IncompatibleExperimentExecutionDocumentError(
            "published execution document exceeds its size limit"
        )
    with path.open("rb") as handle:
        encoded = handle.read(maximum_bytes + 1)
    if len(encoded) > maximum_bytes:
        raise IncompatibleExperimentExecutionDocumentError(
            "published execution document exceeds its size limit"
        )
    return json.loads(encoded.decode("utf-8"))
