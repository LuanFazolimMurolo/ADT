"""Deterministic, bounded comparison contracts for verified backtest runs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.backtesting.domain import BacktestRunId
from app.backtesting.errors import BacktestResultCorruptError
from app.market_data.domain import require_utc

_MAX_COMPARISON_RUNS = 100
_MIN_COMPARISON_RUNS = 2
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REPORT_FIELDS = frozenset(
    {
        "contract_version",
        "sort_by",
        "descending",
        "run_count",
        "same_snapshot",
        "same_data_range",
        "same_initial_capital",
        "entries",
    }
)
_ENTRY_FIELDS = frozenset(
    {
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
    }
)


class ComparisonMetric(StrEnum):
    """Stable metric names accepted by the comparison contract and CLI."""

    TOTAL_RETURN = "total_return"
    CAGR = "cagr"
    SHARPE_RATIO = "sharpe_ratio"
    SORTINO_RATIO = "sortino_ratio"
    MAXIMUM_DRAWDOWN_PCT = "maximum_drawdown_pct"
    NET_PROFIT = "net_profit"
    PROFIT_FACTOR = "profit_factor"


@dataclass(frozen=True, slots=True)
class BacktestComparisonEntry:
    """One verified run projected to a bounded visualization-safe schema."""

    run_id: str
    snapshot_id: str
    dataset_key: str
    dataset_version: str
    engine_version: str
    schema_version: int
    data_start: datetime
    data_end: datetime
    strategy_name: str
    strategy_version: str
    initial_capital: Decimal
    final_equity: Decimal
    total_return: Decimal
    net_profit: Decimal
    maximum_drawdown_pct: Decimal
    cagr: Decimal | None
    annualized_volatility: Decimal | None
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    number_of_closed_trades: int
    win_rate: Decimal | None
    profit_factor: Decimal | None
    turnover: Decimal
    logical_result_checksum: str

    def __post_init__(self) -> None:
        BacktestRunId(self.run_id)
        if (
            _SHA256.fullmatch(self.snapshot_id) is None
            or _SHA256.fullmatch(self.dataset_version) is None
            or _SHA256.fullmatch(self.logical_result_checksum) is None
        ):
            raise ValueError("comparison identity checksums are invalid")
        object.__setattr__(
            self,
            "data_start",
            require_utc(self.data_start, field_name="data_start"),
        )
        object.__setattr__(
            self,
            "data_end",
            require_utc(self.data_end, field_name="data_end"),
        )
        if self.data_end <= self.data_start:
            raise ValueError("comparison data range must be positive")
        if self.schema_version < 1:
            raise ValueError("comparison schema_version must be positive")
        if self.number_of_closed_trades < 0:
            raise ValueError("comparison trade count must be nonnegative")
        for value in (
            self.initial_capital,
            self.final_equity,
            self.total_return,
            self.net_profit,
            self.maximum_drawdown_pct,
            self.turnover,
        ):
            if not value.is_finite():
                raise ValueError("comparison metrics must be finite")
        for optional_metric in (
            self.cagr,
            self.annualized_volatility,
            self.sharpe_ratio,
            self.sortino_ratio,
            self.win_rate,
            self.profit_factor,
        ):
            if optional_metric is not None and not optional_metric.is_finite():
                raise ValueError("optional comparison metrics must be finite")

    def metric(self, metric: ComparisonMetric) -> Decimal | None:
        """Return the typed value used for deterministic ordering."""
        if metric is ComparisonMetric.TOTAL_RETURN:
            return self.total_return
        if metric is ComparisonMetric.CAGR:
            return self.cagr
        if metric is ComparisonMetric.SHARPE_RATIO:
            return self.sharpe_ratio
        if metric is ComparisonMetric.SORTINO_RATIO:
            return self.sortino_ratio
        if metric is ComparisonMetric.MAXIMUM_DRAWDOWN_PCT:
            return self.maximum_drawdown_pct
        if metric is ComparisonMetric.NET_PROFIT:
            return self.net_profit
        if metric is ComparisonMetric.PROFIT_FACTOR:
            return self.profit_factor
        raise AssertionError("unsupported comparison metric")


@dataclass(frozen=True, slots=True)
class BacktestComparisonReport:
    """Versioned deterministic report over a bounded set of verified runs."""

    contract_version: int
    sort_by: ComparisonMetric
    descending: bool
    run_count: int
    same_snapshot: bool
    same_data_range: bool
    same_initial_capital: bool
    entries: tuple[BacktestComparisonEntry, ...]

    def __post_init__(self) -> None:
        if self.contract_version != 1:
            raise ValueError("unsupported comparison contract version")
        if self.run_count != len(self.entries):
            raise ValueError("comparison run_count is inconsistent")
        if not _MIN_COMPARISON_RUNS <= self.run_count <= _MAX_COMPARISON_RUNS:
            raise ValueError("comparison run count is outside the bounded contract")
        run_ids = [entry.run_id for entry in self.entries]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("comparison report contains duplicate runs")
        if self.entries != _order_entries(
            self.entries,
            sort_by=self.sort_by,
            descending=self.descending,
        ):
            raise ValueError("comparison report ordering is inconsistent")
        if self.same_snapshot is not (len({entry.snapshot_id for entry in self.entries}) == 1):
            raise ValueError("comparison snapshot scope is inconsistent")
        if self.same_data_range is not (
            len({(entry.data_start, entry.data_end) for entry in self.entries}) == 1
        ):
            raise ValueError("comparison data-range scope is inconsistent")
        if self.same_initial_capital is not (
            len({entry.initial_capital for entry in self.entries}) == 1
        ):
            raise ValueError("comparison capital scope is inconsistent")


def normalize_comparison_run_ids(run_ids: Sequence[str]) -> tuple[str, ...]:
    """Validate a bounded, duplicate-free sequence before reading artifacts."""
    normalized = tuple(BacktestRunId(run_id).value for run_id in run_ids)
    if not _MIN_COMPARISON_RUNS <= len(normalized) <= _MAX_COMPARISON_RUNS:
        raise ValueError("comparison requires between 2 and 100 runs")
    if len(set(normalized)) != len(normalized):
        raise ValueError("comparison run ids must be unique")
    return normalized


def build_comparison_report(
    summaries: Sequence[Mapping[str, object]],
    *,
    sort_by: ComparisonMetric = ComparisonMetric.TOTAL_RETURN,
    descending: bool = True,
) -> BacktestComparisonReport:
    """Build one stable report from summaries returned only after verification."""
    if not _MIN_COMPARISON_RUNS <= len(summaries) <= _MAX_COMPARISON_RUNS:
        raise ValueError("comparison requires between 2 and 100 summaries")
    entries = tuple(comparison_entry_from_summary(summary) for summary in summaries)
    run_ids = [entry.run_id for entry in entries]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("comparison summaries must reference unique runs")

    ordered = _order_entries(
        entries,
        sort_by=sort_by,
        descending=descending,
    )
    return BacktestComparisonReport(
        contract_version=1,
        sort_by=sort_by,
        descending=descending,
        run_count=len(ordered),
        same_snapshot=len({entry.snapshot_id for entry in ordered}) == 1,
        same_data_range=(len({(entry.data_start, entry.data_end) for entry in ordered}) == 1),
        same_initial_capital=(len({entry.initial_capital for entry in ordered}) == 1),
        entries=ordered,
    )


def _order_entries(
    entries: Sequence[BacktestComparisonEntry],
    *,
    sort_by: ComparisonMetric,
    descending: bool,
) -> tuple[BacktestComparisonEntry, ...]:
    defined = sorted(
        (entry for entry in entries if entry.metric(sort_by) is not None),
        key=lambda entry: entry.run_id,
    )
    defined.sort(
        key=lambda entry: _required_metric(entry, sort_by),
        reverse=descending,
    )
    undefined = sorted(
        (entry for entry in entries if entry.metric(sort_by) is None),
        key=lambda entry: entry.run_id,
    )
    return tuple(defined + undefined)


def _required_metric(entry: BacktestComparisonEntry, metric: ComparisonMetric) -> Decimal:
    value = entry.metric(metric)
    if value is None:  # pragma: no cover - filtered by caller
        raise TypeError("comparison metric is undefined")
    return value


def comparison_entry_from_summary(
    summary: Mapping[str, object],
) -> BacktestComparisonEntry:
    """Project one verified summary to the stable comparison-entry contract."""
    return _entry_from_summary(summary)


def _entry_from_summary(summary: Mapping[str, object]) -> BacktestComparisonEntry:
    try:
        if _string(summary.get("status")) != "COMPLETE":
            raise ValueError
        data_range = _mapping(summary.get("evaluation_range", summary.get("data_range")))
        strategy = _mapping(summary.get("strategy"))
        metrics = _mapping(summary.get("metrics"))
        return BacktestComparisonEntry(
            run_id=BacktestRunId(_string(summary.get("run_id"))).value,
            snapshot_id=_string(summary.get("snapshot_id")),
            dataset_key=_string(summary.get("dataset_key")),
            dataset_version=_string(summary.get("dataset_version")),
            engine_version=_string(summary.get("engine_version")),
            schema_version=_integer(summary.get("schema_version")),
            data_start=datetime.fromisoformat(_string(data_range.get("start"))),
            data_end=datetime.fromisoformat(_string(data_range.get("end"))),
            strategy_name=_string(strategy.get("name")),
            strategy_version=_string(strategy.get("version")),
            initial_capital=_decimal(summary.get("initial_capital")),
            final_equity=_decimal(metrics.get("final_equity")),
            total_return=_decimal(metrics.get("total_return")),
            net_profit=_decimal(metrics.get("net_profit")),
            maximum_drawdown_pct=_decimal(metrics.get("maximum_drawdown_pct")),
            cagr=_optional_decimal(metrics.get("cagr")),
            annualized_volatility=_optional_decimal(metrics.get("annualized_volatility")),
            sharpe_ratio=_optional_decimal(metrics.get("sharpe_ratio")),
            sortino_ratio=_optional_decimal(metrics.get("sortino_ratio")),
            number_of_closed_trades=_integer(metrics.get("number_of_closed_trades")),
            win_rate=_optional_decimal(metrics.get("win_rate")),
            profit_factor=_optional_decimal(metrics.get("profit_factor")),
            turnover=_decimal(metrics.get("turnover")),
            logical_result_checksum=_string(summary.get("logical_result_checksum")),
        )
    except (InvalidOperation, TypeError, ValueError):
        raise BacktestResultCorruptError(
            "Um resumo verificado não respeita o contrato comparativo."
        ) from None


def comparison_report_from_mapping(
    value: Mapping[str, object],
) -> BacktestComparisonReport:
    """Rebuild and validate one canonical serialized comparison report."""
    try:
        if frozenset(value) != _REPORT_FIELDS:
            raise ValueError
        entries_value = value.get("entries")
        if not isinstance(entries_value, list):
            raise TypeError
        entries = tuple(_entry_from_mapping(_mapping(item)) for item in entries_value)
        return BacktestComparisonReport(
            contract_version=_integer(value.get("contract_version")),
            sort_by=ComparisonMetric(_string(value.get("sort_by"))),
            descending=_boolean(value.get("descending")),
            run_count=_integer(value.get("run_count")),
            same_snapshot=_boolean(value.get("same_snapshot")),
            same_data_range=_boolean(value.get("same_data_range")),
            same_initial_capital=_boolean(value.get("same_initial_capital")),
            entries=entries,
        )
    except (InvalidOperation, TypeError, ValueError):
        raise BacktestResultCorruptError(
            "O relatório comparativo serializado é inválido."
        ) from None


def _entry_from_mapping(value: Mapping[str, object]) -> BacktestComparisonEntry:
    if frozenset(value) != _ENTRY_FIELDS:
        raise ValueError("comparison entry fields are invalid")
    return BacktestComparisonEntry(
        run_id=_string(value.get("run_id")),
        snapshot_id=_string(value.get("snapshot_id")),
        dataset_key=_string(value.get("dataset_key")),
        dataset_version=_string(value.get("dataset_version")),
        engine_version=_string(value.get("engine_version")),
        schema_version=_integer(value.get("schema_version")),
        data_start=datetime.fromisoformat(_string(value.get("data_start"))),
        data_end=datetime.fromisoformat(_string(value.get("data_end"))),
        strategy_name=_string(value.get("strategy_name")),
        strategy_version=_string(value.get("strategy_version")),
        initial_capital=_decimal(value.get("initial_capital")),
        final_equity=_decimal(value.get("final_equity")),
        total_return=_decimal(value.get("total_return")),
        net_profit=_decimal(value.get("net_profit")),
        maximum_drawdown_pct=_decimal(value.get("maximum_drawdown_pct")),
        cagr=_optional_decimal(value.get("cagr")),
        annualized_volatility=_optional_decimal(value.get("annualized_volatility")),
        sharpe_ratio=_optional_decimal(value.get("sharpe_ratio")),
        sortino_ratio=_optional_decimal(value.get("sortino_ratio")),
        number_of_closed_trades=_integer(value.get("number_of_closed_trades")),
        win_rate=_optional_decimal(value.get("win_rate")),
        profit_factor=_optional_decimal(value.get("profit_factor")),
        turnover=_decimal(value.get("turnover")),
        logical_result_checksum=_string(value.get("logical_result_checksum")),
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


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise TypeError
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError
    return result


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)
