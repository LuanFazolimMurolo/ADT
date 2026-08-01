"""Bounded explicit batch-comparison contracts without parameter search."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.backtesting.reports import (
    BacktestComparisonReport,
    ComparisonMetric,
    normalize_comparison_run_ids,
)
from app.backtesting.serialization import canonical_checksum

MIN_BATCH_GROUPS = 1
MAX_BATCH_GROUPS = 20
MAX_BATCH_RUN_REFERENCES = 500
MAX_BATCH_UNIQUE_RUNS = 100
MAX_BATCH_REQUEST_BYTES = 1_048_576
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ComparisonBatchGroup:
    """One explicit comparison group; no grids or inferred parameters."""

    name: str
    run_ids: tuple[str, ...]
    sort_by: ComparisonMetric = ComparisonMetric.TOTAL_RETURN
    descending: bool = True

    def __post_init__(self) -> None:
        if _NAME.fullmatch(self.name) is None:
            raise ValueError("comparison batch group name is invalid")
        object.__setattr__(self, "run_ids", normalize_comparison_run_ids(self.run_ids))


@dataclass(frozen=True, slots=True)
class ComparisonBatchRequest:
    """Versioned bounded request loaded from an explicit local JSON file."""

    contract_version: int
    groups: tuple[ComparisonBatchGroup, ...]

    def __post_init__(self) -> None:
        if self.contract_version != 1:
            raise ValueError("unsupported comparison batch contract version")
        if not MIN_BATCH_GROUPS <= len(self.groups) <= MAX_BATCH_GROUPS:
            raise ValueError("comparison batch requires between 1 and 20 groups")
        names = tuple(group.name for group in self.groups)
        if len(set(names)) != len(names):
            raise ValueError("comparison batch group names must be unique")
        references = tuple(run_id for group in self.groups for run_id in group.run_ids)
        if len(references) > MAX_BATCH_RUN_REFERENCES:
            raise ValueError("comparison batch exceeds the run-reference limit")
        if len(set(references)) > MAX_BATCH_UNIQUE_RUNS:
            raise ValueError("comparison batch exceeds the unique-run limit")

    @property
    def unique_run_ids(self) -> tuple[str, ...]:
        """Return first-seen unique run IDs for verify-once orchestration."""
        return tuple(dict.fromkeys(run_id for group in self.groups for run_id in group.run_ids))


@dataclass(frozen=True, slots=True)
class ComparisonBatchGroupResult:
    """One named deterministic report inside a batch response."""

    name: str
    report: BacktestComparisonReport

    def __post_init__(self) -> None:
        if _NAME.fullmatch(self.name) is None:
            raise ValueError("comparison batch result name is invalid")


@dataclass(frozen=True, slots=True)
class BacktestComparisonBatch:
    """Bounded visualization-safe response over multiple explicit comparisons."""

    contract_version: int
    batch_id: str
    group_count: int
    unique_run_count: int
    groups: tuple[ComparisonBatchGroupResult, ...]

    def __post_init__(self) -> None:
        if self.contract_version != 1:
            raise ValueError("unsupported comparison batch result version")
        if _SHA256.fullmatch(self.batch_id) is None:
            raise ValueError("comparison batch id is invalid")
        if self.group_count != len(self.groups):
            raise ValueError("comparison batch group_count is inconsistent")
        if not MIN_BATCH_GROUPS <= self.group_count <= MAX_BATCH_GROUPS:
            raise ValueError("comparison batch result is outside the group limit")
        run_ids = {entry.run_id for group in self.groups for entry in group.report.entries}
        if self.unique_run_count != len(run_ids):
            raise ValueError("comparison batch unique_run_count is inconsistent")


def comparison_batch_request_from_mapping(
    value: Mapping[str, object],
) -> ComparisonBatchRequest:
    """Decode a strict JSON mapping and reject silent unknown fields."""
    if set(value) != {"contract_version", "groups"}:
        raise ValueError("comparison batch request fields are invalid")
    groups_value = value.get("groups")
    if not isinstance(groups_value, list):
        raise ValueError("comparison batch groups must be a list")
    return ComparisonBatchRequest(
        contract_version=_integer(value.get("contract_version")),
        groups=tuple(_group_from_mapping(_mapping(item)) for item in groups_value),
    )


def load_comparison_batch_request(path: Path) -> ComparisonBatchRequest:
    """Load one bounded regular JSON file without performing network I/O."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_BATCH_REQUEST_BYTES:
            raise ValueError
        raw = json.loads(path.read_text(encoding="utf-8"))
        return comparison_batch_request_from_mapping(_mapping(raw))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("comparison batch request file is invalid") from None


def build_comparison_batch(
    request: ComparisonBatchRequest,
    reports: Sequence[BacktestComparisonReport],
) -> BacktestComparisonBatch:
    """Bind named reports to their request and derive a deterministic ID."""
    if len(reports) != len(request.groups):
        raise ValueError("comparison batch report count is inconsistent")
    groups: list[ComparisonBatchGroupResult] = []
    for group, report in zip(request.groups, reports, strict=True):
        if report.sort_by is not group.sort_by or report.descending is not group.descending:
            raise ValueError("comparison batch report ordering is inconsistent")
        if {entry.run_id for entry in report.entries} != set(group.run_ids):
            raise ValueError("comparison batch report runs are inconsistent")
        groups.append(ComparisonBatchGroupResult(group.name, report))
    group_results = tuple(groups)
    batch_id = canonical_checksum(
        {
            "contract_version": 1,
            "groups": group_results,
        }
    )
    return BacktestComparisonBatch(
        contract_version=1,
        batch_id=batch_id,
        group_count=len(group_results),
        unique_run_count=len(request.unique_run_ids),
        groups=group_results,
    )


def _group_from_mapping(value: Mapping[str, object]) -> ComparisonBatchGroup:
    allowed = {"name", "run_ids", "sort_by", "descending"}
    if not set(value).issubset(allowed) or not {"name", "run_ids"}.issubset(value):
        raise ValueError("comparison batch group fields are invalid")
    run_ids_value = value.get("run_ids")
    if not isinstance(run_ids_value, list):
        raise ValueError("comparison batch run_ids must be a list")
    return ComparisonBatchGroup(
        name=_string(value.get("name")),
        run_ids=tuple(_string(item) for item in run_ids_value),
        sort_by=ComparisonMetric(
            _string(value.get("sort_by", ComparisonMetric.TOTAL_RETURN.value))
        ),
        descending=_boolean(value.get("descending", True)),
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError
    return value


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
