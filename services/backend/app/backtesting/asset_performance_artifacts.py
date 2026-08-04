"""Atomic immutable publication of deterministic asset-performance reports."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from app.backtesting.asset_performance import (
    AssetPerformanceReport,
    asset_performance_report_from_mapping,
)
from app.backtesting.errors import (
    BacktestResultConflictError,
    BacktestResultCorruptError,
)
from app.backtesting.serialization import (
    canonical_checksum,
    canonical_json_bytes,
    canonical_value,
    sha256_bytes,
)
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager

_REPORT_ID = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_NAMES = frozenset({"manifest.json", "report.json"})
_MANIFEST_FIELDS = frozenset(
    {
        "contract_version",
        "report_id",
        "report_checksum",
        "run_count",
        "asset_count",
        "source_runs",
        "created_at",
    }
)
_SOURCE_RUN_FIELDS = frozenset({"run_id", "logical_result_checksum"})
_MAX_REPORT_ENVELOPE_BYTES = 1024 * 1024
_MAX_MANIFEST_ENVELOPE_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True, order=True)
class AssetPerformanceSourceRun:
    """One immutable source binding recorded in an exported report manifest."""

    run_id: str
    logical_result_checksum: str

    def __post_init__(self) -> None:
        _require_sha256(self.run_id, field_name="source run id")
        _require_sha256(
            self.logical_result_checksum,
            field_name="source logical result checksum",
        )


@dataclass(frozen=True, slots=True)
class AssetPerformanceExportManifest:
    """Versioned manifest binding one report to all verified source runs."""

    contract_version: int
    report_id: str
    report_checksum: str
    run_count: int
    asset_count: int
    source_runs: tuple[AssetPerformanceSourceRun, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("unsupported asset performance export contract version")
        _require_sha256(self.report_id, field_name="asset performance report id")
        _require_sha256(
            self.report_checksum,
            field_name="asset performance report checksum",
        )
        if type(self.run_count) is not int or type(self.asset_count) is not int:
            raise ValueError("asset performance export counts must be integers")
        if not isinstance(self.source_runs, tuple) or not self.source_runs:
            raise ValueError("asset performance export requires source runs")
        for source in self.source_runs:
            AssetPerformanceSourceRun.__post_init__(source)
        if self.source_runs != tuple(sorted(self.source_runs)):
            raise ValueError("asset performance source runs must use canonical order")
        if len({source.run_id for source in self.source_runs}) != len(self.source_runs):
            raise ValueError("asset performance source runs must be unique")
        if self.run_count != len(self.source_runs) or self.asset_count < 1:
            raise ValueError("asset performance export counts are inconsistent")
        offset = self.created_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("asset performance export timestamp must be UTC")


@dataclass(frozen=True, slots=True)
class AssetPerformanceExportResult:
    """Bounded publication result returned by the local CLI."""

    report_id: str
    relative_path: str
    reused: bool
    run_count: int
    asset_count: int


@dataclass(frozen=True, slots=True)
class AssetPerformanceVerification:
    """Successful independent verification of one exported report."""

    report_id: str
    relative_path: str
    report_checksum: str
    run_count: int
    asset_count: int
    source_run_count: int
    verified: bool = True


@dataclass(frozen=True, slots=True)
class _LoadedExport:
    report: AssetPerformanceReport
    manifest: AssetPerformanceExportManifest


class AssetPerformanceReportStore:
    """Publish one content-addressed report atomically and never overwrite it."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("asset-performance-reports"),
        lock_timeout_seconds: float = 30,
        lock_stale_after_seconds: float = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("asset performance directory must be safe and relative")
        self._data_dir = data_dir
        self._market = market_root(data_dir)
        self._root = ensure_safe_path(self._market, self._market / directory)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
            clock=self._clock,
        )

    @property
    def root(self) -> Path:
        return self._root

    def publish(self, report: AssetPerformanceReport) -> AssetPerformanceExportResult:
        report = _revalidate_report(report)
        self._root.mkdir(parents=True, exist_ok=True)
        fsync_directory(self._market)
        with self._locks.acquire(f"asset-performance:{report.report_id}"):
            target = ensure_safe_path(self._market, self._root / report.report_id)
            if target.exists():
                existing = AssetPerformanceReportVerifier(
                    self._data_dir,
                    directory=self._root.relative_to(self._market),
                ).inspect(report.report_id)
                if existing != report:
                    raise BacktestResultConflictError()
                return self._result(report, reused=True)

            staging = ensure_safe_path(
                self._market,
                self._root / f".{report.report_id}.tmp-{os.getpid()}-{uuid4().hex}",
            )
            staging.mkdir(parents=False, exist_ok=False)
            try:
                self._write_export(staging, report)
                fsync_directory(staging)
                os.replace(staging, target)
                fsync_directory(self._root)
            except Exception:
                _remove_tree(staging)
                raise
        return self._result(report, reused=False)

    def _write_export(self, staging: Path, report: AssetPerformanceReport) -> None:
        report_checksum = canonical_checksum(report)
        manifest = AssetPerformanceExportManifest(
            contract_version=1,
            report_id=report.report_id,
            report_checksum=report_checksum,
            run_count=report.run_count,
            asset_count=report.asset_count,
            source_runs=_source_runs(report),
            created_at=self._clock().astimezone(UTC),
        )
        (staging / "report.json").write_bytes(_exact_envelope_bytes("report", report))
        (staging / "manifest.json").write_bytes(_exact_envelope_bytes("manifest", manifest))
        _fsync_file(staging / "report.json")
        _fsync_file(staging / "manifest.json")

    def _result(
        self,
        report: AssetPerformanceReport,
        *,
        reused: bool,
    ) -> AssetPerformanceExportResult:
        return AssetPerformanceExportResult(
            report_id=report.report_id,
            relative_path=(self._root / report.report_id).relative_to(self._market).as_posix(),
            reused=reused,
            run_count=report.run_count,
            asset_count=report.asset_count,
        )


class AssetPerformanceReportVerifier:
    """Read and independently verify immutable asset-performance exports."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("asset-performance-reports"),
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("asset performance directory must be safe and relative")
        self._market = market_root(data_dir)
        self._root = ensure_safe_path(self._market, self._market / directory)

    def inspect(self, report_id: str) -> AssetPerformanceReport:
        """Return one canonical report only after full local verification."""

        return self._load(report_id).report

    def verify(self, report_id: str) -> AssetPerformanceVerification:
        """Verify exact files, envelopes, manifest bindings and report identity."""

        loaded = self._load(report_id)
        relative_path = (self._root / loaded.report.report_id).relative_to(self._market)
        return AssetPerformanceVerification(
            report_id=loaded.report.report_id,
            relative_path=relative_path.as_posix(),
            report_checksum=loaded.manifest.report_checksum,
            run_count=loaded.report.run_count,
            asset_count=loaded.report.asset_count,
            source_run_count=len(loaded.manifest.source_runs),
        )

    def _load(self, report_id: str) -> _LoadedExport:
        _require_sha256(report_id, field_name="asset performance report id")
        target = ensure_safe_path(self._market, self._root / report_id)
        try:
            if not target.is_dir() or target.is_symlink():
                raise ValueError
            names = {path.name for path in target.iterdir()}
            if names != _ARTIFACT_NAMES:
                raise ValueError
            for name in _ARTIFACT_NAMES:
                path = target / name
                if not path.is_file() or path.is_symlink():
                    raise ValueError
            report_value, report_bytes = _read_exact_envelope(
                target / "report.json",
                "report",
                maximum_bytes=_MAX_REPORT_ENVELOPE_BYTES,
            )
            manifest_value, manifest_bytes = _read_exact_envelope(
                target / "manifest.json",
                "manifest",
                maximum_bytes=_MAX_MANIFEST_ENVELOPE_BYTES,
            )
            report = asset_performance_report_from_mapping(report_value)
            manifest = _manifest_from_mapping(manifest_value)
            if report_bytes != _exact_envelope_bytes("report", report):
                raise ValueError
            if manifest_bytes != _exact_envelope_bytes("manifest", manifest):
                raise ValueError
            _verify_bindings(report_id, report, manifest)
            return _LoadedExport(report=report, manifest=manifest)
        except BacktestResultCorruptError:
            raise
        except (OSError, TypeError, ValueError):
            raise BacktestResultCorruptError(
                "O export de performance por ativo é inválido."
            ) from None


def _verify_bindings(
    requested_report_id: str,
    report: AssetPerformanceReport,
    manifest: AssetPerformanceExportManifest,
) -> None:
    expected_sources = _source_runs(report)
    if (
        report.report_id != requested_report_id
        or manifest.report_id != requested_report_id
        or manifest.report_checksum != canonical_checksum(report)
        or manifest.run_count != report.run_count
        or manifest.asset_count != report.asset_count
        or manifest.source_runs != expected_sources
    ):
        raise BacktestResultCorruptError(
            "O manifest do relatório de performance por ativo é inconsistente."
        )


def _source_runs(
    report: AssetPerformanceReport,
) -> tuple[AssetPerformanceSourceRun, ...]:
    return tuple(
        sorted(
            AssetPerformanceSourceRun(
                run_id=run.run_id,
                logical_result_checksum=run.logical_result_checksum,
            )
            for group in report.assets
            for run in group.runs
        )
    )


def _revalidate_report(report: AssetPerformanceReport) -> AssetPerformanceReport:
    if not isinstance(report, AssetPerformanceReport):
        raise TypeError("asset performance report is invalid")
    value = canonical_value(report)
    if not isinstance(value, dict):
        raise TypeError("asset performance report serialization is invalid")
    return asset_performance_report_from_mapping(cast(Mapping[str, object], value))


def _manifest_from_mapping(
    value: Mapping[str, object],
) -> AssetPerformanceExportManifest:
    try:
        if frozenset(value) != _MANIFEST_FIELDS:
            raise ValueError
        source_values = value.get("source_runs")
        if not isinstance(source_values, list):
            raise TypeError
        return AssetPerformanceExportManifest(
            contract_version=_integer(value.get("contract_version")),
            report_id=_string(value.get("report_id")),
            report_checksum=_string(value.get("report_checksum")),
            run_count=_integer(value.get("run_count")),
            asset_count=_integer(value.get("asset_count")),
            source_runs=tuple(_source_run_from_mapping(_mapping(item)) for item in source_values),
            created_at=_datetime(value.get("created_at")),
        )
    except (TypeError, ValueError):
        raise BacktestResultCorruptError(
            "O manifest do relatório de performance por ativo é inválido."
        ) from None


def _source_run_from_mapping(
    value: Mapping[str, object],
) -> AssetPerformanceSourceRun:
    if frozenset(value) != _SOURCE_RUN_FIELDS:
        raise ValueError
    return AssetPerformanceSourceRun(
        run_id=_string(value.get("run_id")),
        logical_result_checksum=_string(value.get("logical_result_checksum")),
    )


def _exact_envelope_bytes(key: str, value: object) -> bytes:
    payload = canonical_value(value)
    encoded = canonical_json_bytes(payload)
    return canonical_json_bytes(
        {
            key: payload,
            "checksum": sha256_bytes(encoded),
        }
    )


def _read_exact_envelope(
    path: Path,
    key: str,
    *,
    maximum_bytes: int,
) -> tuple[Mapping[str, object], bytes]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum_bytes + 1)
        if len(raw) > maximum_bytes:
            raise ValueError
        envelope = json.loads(raw.decode("utf-8"))
        if not isinstance(envelope, dict) or frozenset(envelope) != {key, "checksum"}:
            raise ValueError
        payload = envelope[key]
        checksum = envelope["checksum"]
        if not isinstance(payload, dict) or not isinstance(checksum, str):
            raise ValueError
        if sha256_bytes(canonical_json_bytes(payload)) != checksum:
            raise ValueError
        return cast(Mapping[str, object], payload), raw
    except (OSError, KeyError, TypeError, UnicodeDecodeError, ValueError):
        raise BacktestResultCorruptError(
            "O envelope do relatório de performance por ativo é inválido."
        ) from None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError
    return cast(Mapping[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError
    return value


def _datetime(value: object) -> datetime:
    text = _string(value)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError
    return parsed


def _require_sha256(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _REPORT_ID.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise BacktestResultCorruptError(
            "Não foi possível limpar o staging do relatório por ativo."
        ) from error
