"""Atomic, deterministic exports for verified backtest comparison reports."""

from __future__ import annotations

import csv
import os
import shutil
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from re import compile as compile_pattern
from uuid import uuid4

from app.backtesting.domain import ArtifactChecksum
from app.backtesting.errors import (
    BacktestComparisonExportConflictError,
    BacktestComparisonExportCorruptError,
    BacktestComparisonExportMissingError,
    BacktestResultCorruptError,
)
from app.backtesting.reports import (
    BacktestComparisonReport,
    ComparisonMetric,
    comparison_report_from_mapping,
)
from app.backtesting.serialization import (
    canonical_checksum,
    canonical_json_bytes,
    canonical_value,
    file_checksum,
    read_json_envelope,
    sha256_bytes,
    write_json_envelope,
)
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager

_EXPORT_SCHEMA_VERSION = 1
_EXPORT_FILES = ("report.json", "report.csv")
_EXPORT_ENTRIES = frozenset((*_EXPORT_FILES, "manifest.json"))
_SHA256 = compile_pattern(r"[0-9a-f]{64}")
_CSV_COLUMNS = (
    "rank",
    "run_id",
    "snapshot_id",
    "dataset_key",
    "dataset_version",
    "engine_version",
    "schema_version",
    "data_start",
    "data_end",
    "strategy_name",
    "strategy_version",
    "initial_capital",
    "final_equity",
    "total_return",
    "net_profit",
    "maximum_drawdown_pct",
    "cagr",
    "annualized_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "number_of_closed_trades",
    "win_rate",
    "profit_factor",
    "turnover",
    "logical_result_checksum",
)


@dataclass(frozen=True, slots=True)
class ComparisonExportManifest:
    """Deterministic metadata binding one export to its report and files."""

    report_id: str
    export_schema_version: int
    report_contract_version: int
    sort_by: str
    descending: bool
    run_count: int
    run_ids: tuple[str, ...]
    logical_result_checksums: tuple[str, ...]
    artifacts: tuple[ArtifactChecksum, ...]

    def __post_init__(self) -> None:
        _require_report_id(self.report_id)
        if self.export_schema_version != _EXPORT_SCHEMA_VERSION:
            raise ValueError("unsupported comparison export schema")
        if self.report_contract_version != 1:
            raise ValueError("unsupported comparison report contract")
        ComparisonMetric(self.sort_by)
        if self.run_count != len(self.run_ids):
            raise ValueError("comparison export run_count is inconsistent")
        if self.run_count != len(self.logical_result_checksums):
            raise ValueError("comparison export checksum count is inconsistent")
        if len(set(self.run_ids)) != self.run_count:
            raise ValueError("comparison export contains duplicate runs")
        if any(_SHA256.fullmatch(value) is None for value in self.run_ids):
            raise ValueError("comparison export run id is invalid")
        if any(_SHA256.fullmatch(value) is None for value in self.logical_result_checksums):
            raise ValueError("comparison export logical checksum is invalid")
        paths = tuple(artifact.relative_path for artifact in self.artifacts)
        if paths != _EXPORT_FILES:
            raise ValueError("comparison export artifact list is invalid")


@dataclass(frozen=True, slots=True)
class ComparisonExportResult:
    """Bounded publication result returned by the local CLI."""

    report_id: str
    relative_path: str
    reused: bool
    run_count: int
    sort_by: str
    descending: bool

    def __post_init__(self) -> None:
        _require_report_id(self.report_id)
        path = Path(self.relative_path)
        if not self.relative_path or path.is_absolute() or ".." in path.parts:
            raise ValueError("comparison export path must be safe and relative")
        if self.run_count < 2:
            raise ValueError("comparison export run count is invalid")


@dataclass(frozen=True, slots=True)
class ComparisonExportVerification:
    """Successful independent verification of one comparison export."""

    report_id: str
    export_schema_version: int
    report_contract_version: int
    run_count: int
    report_checksum: str
    csv_checksum: str

    def __post_init__(self) -> None:
        _require_report_id(self.report_id)
        if self.export_schema_version != _EXPORT_SCHEMA_VERSION:
            raise ValueError("unsupported comparison export schema")
        if self.report_contract_version != 1 or self.run_count < 2:
            raise ValueError("comparison export verification is invalid")
        for checksum in (self.report_checksum, self.csv_checksum):
            if _SHA256.fullmatch(checksum) is None:
                raise ValueError("comparison export checksum is invalid")


def build_comparison_report_id(report: BacktestComparisonReport) -> str:
    """Derive one content-addressed ID from the complete canonical report."""
    return canonical_checksum(
        {
            "export_schema_version": _EXPORT_SCHEMA_VERSION,
            "report": report,
        }
    )


def render_comparison_csv(report: BacktestComparisonReport) -> bytes:
    """Render one stable UTF-8 CSV projection with blank optional metrics."""
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for rank, entry in enumerate(report.entries, start=1):
        value = canonical_value(entry)
        if not isinstance(value, dict):  # pragma: no cover - dataclass contract
            raise TypeError("comparison entry serialization is invalid")
        writer.writerow(
            {
                "rank": rank,
                **{
                    column: "" if value.get(column) is None else value.get(column)
                    for column in _CSV_COLUMNS
                    if column != "rank"
                },
            }
        )
    return stream.getvalue().encode("utf-8")


class ComparisonReportExportStore:
    """Publish immutable comparison exports under one content-addressed lock."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("backtest-reports"),
        lock_timeout_seconds: float = 30,
        lock_stale_after_seconds: float = 300,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("comparison export directory must be safe and relative")
        self._data_dir = data_dir
        self._market = market_root(data_dir)
        self._root = ensure_safe_path(self._market, self._market / directory)
        self._directory = directory
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
        )

    def publish(self, report: BacktestComparisonReport) -> ComparisonExportResult:
        report_id = build_comparison_report_id(report)
        self._root.mkdir(parents=True, exist_ok=True)
        fsync_directory(self._market)
        with self._locks.acquire(f"backtest-report:{report_id}"):
            target = ensure_safe_path(self._market, self._root / report_id)
            if target.exists():
                verification = ComparisonReportExportVerifier(
                    self._data_dir,
                    directory=self._directory,
                ).verify(report_id)
                if verification.report_id != report_id:
                    raise BacktestComparisonExportConflictError()
                return self._result(report, report_id, reused=True)

            staging = ensure_safe_path(
                self._market,
                self._root / f".{report_id}.tmp-{os.getpid()}-{uuid4().hex}",
            )
            staging.mkdir(parents=False, exist_ok=False)
            try:
                write_json_envelope(
                    staging / "report.json",
                    "comparison_report",
                    report,
                )
                (staging / "report.csv").write_bytes(render_comparison_csv(report))
                for name in _EXPORT_FILES:
                    _fsync_file(staging / name)
                artifacts = tuple(
                    ArtifactChecksum(
                        relative_path=name,
                        checksum=file_checksum(staging / name),
                        size_bytes=(staging / name).stat().st_size,
                    )
                    for name in _EXPORT_FILES
                )
                manifest = ComparisonExportManifest(
                    report_id=report_id,
                    export_schema_version=_EXPORT_SCHEMA_VERSION,
                    report_contract_version=report.contract_version,
                    sort_by=report.sort_by.value,
                    descending=report.descending,
                    run_count=report.run_count,
                    run_ids=tuple(entry.run_id for entry in report.entries),
                    logical_result_checksums=tuple(
                        entry.logical_result_checksum for entry in report.entries
                    ),
                    artifacts=artifacts,
                )
                write_json_envelope(
                    staging / "manifest.json",
                    "comparison_export_manifest",
                    manifest,
                )
                _fsync_file(staging / "manifest.json")
                fsync_directory(staging)
                os.replace(staging, target)
                fsync_directory(self._root)
            except Exception:
                _remove_tree(staging)
                raise
        return self._result(report, report_id, reused=False)

    def _result(
        self,
        report: BacktestComparisonReport,
        report_id: str,
        *,
        reused: bool,
    ) -> ComparisonExportResult:
        return ComparisonExportResult(
            report_id=report_id,
            relative_path=(self._directory / report_id).as_posix(),
            reused=reused,
            run_count=report.run_count,
            sort_by=report.sort_by.value,
            descending=report.descending,
        )


class ComparisonReportExportVerifier:
    """Independently verify exact files, checksums, schema and report identity."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("backtest-reports"),
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("comparison export directory must be safe and relative")
        self._market = market_root(data_dir)
        self._root = ensure_safe_path(self._market, self._market / directory)

    def verify(self, report_id: str) -> ComparisonExportVerification:
        _require_report_id(report_id)
        root = ensure_safe_path(self._market, self._root / report_id)
        if not root.is_dir():
            raise BacktestComparisonExportMissingError()
        try:
            actual_entries = frozenset(path.name for path in root.iterdir())
            if actual_entries != _EXPORT_ENTRIES:
                raise ValueError
            manifest_value = read_json_envelope(
                root / "manifest.json",
                "comparison_export_manifest",
            )
            report_value = read_json_envelope(
                root / "report.json",
                "comparison_report",
            )
            report = comparison_report_from_mapping(report_value)
            manifest = _manifest_from_mapping(manifest_value)
            if (root / "report.json").read_bytes() != _json_envelope_bytes(
                "comparison_report", report
            ):
                raise ValueError
            if (root / "manifest.json").read_bytes() != _json_envelope_bytes(
                "comparison_export_manifest", manifest
            ):
                raise ValueError
            if manifest.report_id != report_id:
                raise ValueError
            if build_comparison_report_id(report) != report_id:
                raise ValueError
            if manifest.report_contract_version != report.contract_version:
                raise ValueError
            if manifest.sort_by != report.sort_by.value:
                raise ValueError
            if manifest.descending is not report.descending:
                raise ValueError
            if manifest.run_count != report.run_count:
                raise ValueError
            if manifest.run_ids != tuple(entry.run_id for entry in report.entries):
                raise ValueError
            if manifest.logical_result_checksums != tuple(
                entry.logical_result_checksum for entry in report.entries
            ):
                raise ValueError
            for artifact in manifest.artifacts:
                path = ensure_safe_path(self._market, root / artifact.relative_path)
                if not path.is_file():
                    raise ValueError
                if path.stat().st_size != artifact.size_bytes:
                    raise ValueError
                if file_checksum(path) != artifact.checksum:
                    raise ValueError
            if (root / "report.csv").read_bytes() != render_comparison_csv(report):
                raise ValueError
            return ComparisonExportVerification(
                report_id=report_id,
                export_schema_version=manifest.export_schema_version,
                report_contract_version=manifest.report_contract_version,
                run_count=manifest.run_count,
                report_checksum=file_checksum(root / "report.json"),
                csv_checksum=file_checksum(root / "report.csv"),
            )
        except (
            BacktestComparisonExportCorruptError,
            BacktestResultCorruptError,
            OSError,
            TypeError,
            ValueError,
        ):
            raise BacktestComparisonExportCorruptError() from None


def _json_envelope_bytes(key: str, value: object) -> bytes:
    payload = canonical_value(value)
    return canonical_json_bytes(
        {
            key: payload,
            "checksum": sha256_bytes(canonical_json_bytes(payload)),
        }
    )


def _manifest_from_mapping(value: dict[str, object]) -> ComparisonExportManifest:
    try:
        artifacts_value = value["artifacts"]
        if not isinstance(artifacts_value, list):
            raise TypeError
        artifacts: list[ArtifactChecksum] = []
        for item in artifacts_value:
            if not isinstance(item, dict):
                raise TypeError
            artifacts.append(
                ArtifactChecksum(
                    relative_path=_string(item.get("relative_path")),
                    checksum=_string(item.get("checksum")),
                    size_bytes=_integer(item.get("size_bytes")),
                )
            )
        return ComparisonExportManifest(
            report_id=_string(value.get("report_id")),
            export_schema_version=_integer(value.get("export_schema_version")),
            report_contract_version=_integer(value.get("report_contract_version")),
            sort_by=_string(value.get("sort_by")),
            descending=_boolean(value.get("descending")),
            run_count=_integer(value.get("run_count")),
            run_ids=_string_tuple(value.get("run_ids")),
            logical_result_checksums=_string_tuple(value.get("logical_result_checksums")),
            artifacts=tuple(artifacts),
        )
    except (KeyError, TypeError, ValueError):
        raise BacktestComparisonExportCorruptError() from None


def _require_report_id(value: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("comparison report id must be one SHA-256 digest")


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError
    return tuple(_string(item) for item in value)


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
        raise BacktestComparisonExportCorruptError(
            "Não foi possível limpar o staging do relatório comparativo."
        ) from error
