"""Atomic PREPARED/COMMITTED repository for Phase 4-06 stability reports."""

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
    IncompatibleStabilityDocumentError,
    StabilityAnalysisError,
    StabilityPublicationError,
)
from app.optimization.stability_documents import (
    canonical_stability_report_bytes,
    decode_stability_report_document,
)
from app.optimization.stability_domain import (
    MAX_STABILITY_REPORT_BYTES,
    StabilityReport,
    validate_stability_report,
)

_FINAL_ENTRIES = {"publication.json", "report.json"}
_MAX_PUBLICATION_BYTES = 4 * 1024


@dataclass(frozen=True, slots=True)
class StabilityReportPublication:
    report: StabilityReport
    relative_path: Path
    reused: bool


class StabilityReportRepository:
    """Publish compact immutable stability reports below the market root."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("optimization/stability"),
        lock_timeout_seconds: float = 30,
        lock_stale_after_seconds: float = 300,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("stability repository directory must be safe and relative")
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
        report: StabilityReport,
        *,
        semantic_validator: Callable[[StabilityReport], StabilityReport] | None = None,
    ) -> StabilityReportPublication:
        validate_stability_report(report)
        if semantic_validator is None:
            raise StabilityPublicationError(
                "stability semantic validation is required before publication"
            )
        _validate_semantics(report, semantic_validator)
        encoded = canonical_stability_report_bytes(report)
        if len(encoded) > MAX_STABILITY_REPORT_BYTES:
            raise StabilityPublicationError("stability report exceeds its byte limit")
        execution_root = ensure_safe_path(
            self._market,
            self._root / report.walk_forward_execution_id,
        )
        target = ensure_safe_path(
            self._market,
            execution_root / report.stability_report_id,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        execution_root.mkdir(parents=True, exist_ok=True)
        fsync_directory(self._market)
        with self._locks.acquire(f"stability:{report.walk_forward_execution_id}"):
            if target.exists():
                try:
                    existing = self._read_directory(target, report, "COMMITTED")
                    _validate_semantics(existing, semantic_validator)
                except (IncompatibleStabilityDocumentError, StabilityAnalysisError):
                    try:
                        if target.is_dir() and not target.is_symlink():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                        fsync_directory(execution_root)
                    except OSError as error:
                        raise StabilityPublicationError(
                            "corrupt stability target cannot be recovered"
                        ) from error
                else:
                    if existing != report:
                        raise StabilityPublicationError(
                            "published stability identity contains different content"
                        )
                    return StabilityReportPublication(
                        existing,
                        target.relative_to(self._market),
                        True,
                    )
            staging = ensure_safe_path(
                self._market,
                execution_root
                / f".{report.stability_report_id}.tmp-{os.getpid()}-{uuid4().hex}",
            )
            renamed = False
            try:
                staging.mkdir(parents=False, exist_ok=False)
                _write(staging / "publication.json", _publication_bytes(report, "PREPARED"))
                _write(staging / "report.json", encoded)
                fsync_directory(staging)
                prepared = self._read_directory(staging, report, "PREPARED")
                _validate_semantics(prepared, semantic_validator)
                if prepared != report:
                    raise StabilityPublicationError("staged stability verification failed")
                _write(staging / "publication.json", _publication_bytes(report, "COMMITTED"))
                fsync_directory(staging)
                committed = self._read_directory(staging, report, "COMMITTED")
                _validate_semantics(committed, semantic_validator)
                if committed != report:
                    raise StabilityPublicationError("committed stability verification failed")
                os.replace(staging, target)
                renamed = True
                fsync_directory(execution_root)
                verified = self._read_directory(target, report, "COMMITTED")
                _validate_semantics(verified, semantic_validator)
                if verified != report:
                    raise StabilityPublicationError("post-publication verification failed")
            except Exception as error:
                if staging.exists():
                    shutil.rmtree(staging)
                if renamed and target.exists():
                    shutil.rmtree(target)
                    fsync_directory(execution_root)
                if isinstance(error, StabilityPublicationError):
                    raise
                raise StabilityPublicationError() from error
        return StabilityReportPublication(
            verified,
            target.relative_to(self._market),
            False,
        )

    def read(self, walk_forward_execution_id: str, report_id: str) -> StabilityReport:
        root = ensure_safe_path(
            self._market,
            self._root / walk_forward_execution_id / report_id,
        )
        return self._read_directory(
            root,
            _ExpectedIdentity(walk_forward_execution_id, report_id),
            "COMMITTED",
        )

    def _read_directory(
        self,
        root: Path,
        expected: StabilityReport | _ExpectedIdentity,
        state: str,
    ) -> StabilityReport:
        try:
            entries = {path.name for path in root.iterdir()}
            if entries != _FINAL_ENTRIES:
                raise IncompatibleStabilityDocumentError(
                    "stability directory entries are incompatible"
                )
            publication = _read_json(root / "publication.json", _MAX_PUBLICATION_BYTES)
            envelope = _read_json(root / "report.json", MAX_STABILITY_REPORT_BYTES)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise IncompatibleStabilityDocumentError(
                "published stability report cannot be read"
            ) from None
        if not isinstance(publication, dict) or not isinstance(envelope, dict):
            raise IncompatibleStabilityDocumentError(
                "published stability documents must be objects"
            )
        checksum = envelope.get("checksum")
        if publication != _publication_values(
            expected.walk_forward_execution_id,
            expected.stability_report_id,
            checksum,
            state,
        ):
            raise IncompatibleStabilityDocumentError(
                "stability publication record is incompatible"
            )
        report = decode_stability_report_document(envelope)
        if (
            report.walk_forward_execution_id != expected.walk_forward_execution_id
            or report.stability_report_id != expected.stability_report_id
        ):
            raise IncompatibleStabilityDocumentError(
                "stability publication path diverges from its report"
            )
        return report


@dataclass(frozen=True, slots=True)
class _ExpectedIdentity:
    walk_forward_execution_id: str
    stability_report_id: str


def _validate_semantics(
    report: StabilityReport,
    validator: Callable[[StabilityReport], StabilityReport],
) -> None:
    expected_report_id = report.stability_report_id
    expected_checksum = report.checksum
    try:
        validated = validator(report)
        validate_stability_report(validated)
    except StabilityAnalysisError as error:
        raise StabilityPublicationError("stability semantic validation failed") from error
    except Exception as error:
        raise StabilityPublicationError("stability semantic validation failed") from error
    if validated != report:
        raise StabilityPublicationError(
            "stability semantic validator returned incompatible content"
        )
    if (
        validated.stability_report_id != expected_report_id
        or validated.checksum != expected_checksum
    ):
        raise StabilityPublicationError(
            "stability report changed during semantic validation"
        )


def _publication_bytes(report: StabilityReport, state: str) -> bytes:
    return canonical_json_bytes(
        _publication_values(
            report.walk_forward_execution_id,
            report.stability_report_id,
            report.checksum,
            state,
        )
    )


def _publication_values(
    execution_id: str,
    report_id: str,
    checksum: object,
    state: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "state": state,
        "walk_forward_execution_id": execution_id,
        "stability_report_id": report_id,
        "report_checksum": checksum,
    }


def _write(path: Path, value: bytes) -> None:
    try:
        with path.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise StabilityPublicationError() from error


def _read_json(path: Path, maximum: int) -> object:
    if path.stat().st_size > maximum:
        raise IncompatibleStabilityDocumentError(
            "published stability document exceeds its byte limit"
        )
    with path.open("rb") as handle:
        encoded = handle.read(maximum + 1)
    if len(encoded) > maximum:
        raise IncompatibleStabilityDocumentError(
            "published stability document exceeds its byte limit"
        )
    return json.loads(encoded.decode("utf-8"))
