"""Deterministic asset-level performance aggregation for verified backtest runs."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.backtesting.domain import BacktestRunId
from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.reports import (
    BacktestComparisonEntry,
    comparison_entry_from_summary,
)
from app.backtesting.serialization import canonical_json_bytes
from app.market_data.domain import require_utc

_MAX_TRACKED_RUNS = 100
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LOWER_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_ASSET_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_TIMEFRAME = re.compile(r"[1-9][0-9]*(?:m|h|d|w|M)")
_SAFE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_REPORT_FIELDS = frozenset(
    {
        "contract_version",
        "report_id",
        "run_count",
        "asset_count",
        "assets",
    }
)
_ASSET_FIELDS = frozenset({"exchange", "market_type", "symbol"})
_GROUP_FIELDS = frozenset(
    {
        "asset",
        "run_count",
        "timeframe_count",
        "strategy_count",
        "data_start",
        "data_end",
        "total_initial_capital",
        "total_final_equity",
        "total_net_profit",
        "capital_weighted_return",
        "profitable_runs",
        "losing_runs",
        "flat_runs",
        "number_of_closed_trades",
        "maximum_drawdown_pct",
        "best_run_id",
        "best_total_return",
        "worst_run_id",
        "worst_total_return",
        "runs",
    }
)
_RUN_FIELDS = frozenset(
    {
        "run_id",
        "asset",
        "dataset_kind",
        "timeframe",
        "strategy_name",
        "strategy_version",
        "data_start",
        "data_end",
        "initial_capital",
        "final_equity",
        "total_return",
        "net_profit",
        "maximum_drawdown_pct",
        "number_of_closed_trades",
        "logical_result_checksum",
    }
)


@dataclass(frozen=True, slots=True, order=True)
class AssetIdentity:
    """Canonical exchange/market/pair identity independent from timeframe."""

    exchange: str
    market_type: str
    symbol: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.exchange, str)
            or _LOWER_TOKEN.fullmatch(self.exchange) is None
            or not isinstance(self.market_type, str)
            or _LOWER_TOKEN.fullmatch(self.market_type) is None
        ):
            raise ValueError("asset exchange and market type must be canonical lowercase tokens")
        _validate_symbol(self.symbol)


@dataclass(frozen=True, slots=True)
class AssetPerformanceRun:
    """One verified backtest run projected to an asset-performance contract."""

    run_id: str
    asset: AssetIdentity
    dataset_kind: str
    timeframe: str
    strategy_name: str
    strategy_version: str
    data_start: datetime
    data_end: datetime
    initial_capital: Decimal
    final_equity: Decimal
    total_return: Decimal
    net_profit: Decimal
    maximum_drawdown_pct: Decimal
    number_of_closed_trades: int
    logical_result_checksum: str

    def __post_init__(self) -> None:
        BacktestRunId(self.run_id)
        _revalidate_asset(self.asset)
        if self.dataset_kind not in {"raw", "derived"}:
            raise ValueError("asset performance dataset kind is unsupported")
        if not isinstance(self.timeframe, str) or _TIMEFRAME.fullmatch(self.timeframe) is None:
            raise ValueError("asset performance timeframe is invalid")
        for text_value in (self.strategy_name, self.strategy_version):
            if not isinstance(text_value, str) or _SAFE_TEXT.fullmatch(text_value) is None:
                raise ValueError("asset performance strategy identity is invalid")
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
            raise ValueError("asset performance data range must be positive")
        for metric_value in (
            self.initial_capital,
            self.final_equity,
            self.total_return,
            self.net_profit,
            self.maximum_drawdown_pct,
        ):
            if not isinstance(metric_value, Decimal) or not metric_value.is_finite():
                raise ValueError("asset performance metrics must be finite Decimals")
        if self.initial_capital <= 0 or self.final_equity < 0:
            raise ValueError("asset performance equity values are invalid")
        if self.maximum_drawdown_pct < 0:
            raise ValueError("asset performance drawdown must be nonnegative")
        if self.final_equity != self.initial_capital + self.net_profit:
            raise ValueError("asset performance net profit is inconsistent")
        expected_return = self.net_profit / self.initial_capital * Decimal("100")
        if self.total_return != expected_return:
            raise ValueError("asset performance total return is inconsistent")
        if type(self.number_of_closed_trades) is not int or self.number_of_closed_trades < 0:
            raise ValueError("asset performance trade count must be nonnegative")
        if (
            not isinstance(self.logical_result_checksum, str)
            or _SHA256.fullmatch(self.logical_result_checksum) is None
        ):
            raise ValueError("asset performance logical checksum is invalid")


@dataclass(frozen=True, slots=True)
class _GroupValues:
    run_count: int
    timeframe_count: int
    strategy_count: int
    data_start: datetime
    data_end: datetime
    total_initial_capital: Decimal
    total_final_equity: Decimal
    total_net_profit: Decimal
    capital_weighted_return: Decimal
    profitable_runs: int
    losing_runs: int
    flat_runs: int
    number_of_closed_trades: int
    maximum_drawdown_pct: Decimal
    best_run_id: str
    best_total_return: Decimal
    worst_run_id: str
    worst_total_return: Decimal


@dataclass(frozen=True, slots=True)
class AssetPerformanceGroup:
    """One complete deterministic aggregation for a canonical asset."""

    asset: AssetIdentity
    run_count: int
    timeframe_count: int
    strategy_count: int
    data_start: datetime
    data_end: datetime
    total_initial_capital: Decimal
    total_final_equity: Decimal
    total_net_profit: Decimal
    capital_weighted_return: Decimal
    profitable_runs: int
    losing_runs: int
    flat_runs: int
    number_of_closed_trades: int
    maximum_drawdown_pct: Decimal
    best_run_id: str
    best_total_return: Decimal
    worst_run_id: str
    worst_total_return: Decimal
    runs: tuple[AssetPerformanceRun, ...]

    def __post_init__(self) -> None:
        _revalidate_asset(self.asset)
        for count in (
            self.run_count,
            self.timeframe_count,
            self.strategy_count,
            self.profitable_runs,
            self.losing_runs,
            self.flat_runs,
            self.number_of_closed_trades,
        ):
            if type(count) is not int:
                raise ValueError("asset performance counts must be integers")
        if not isinstance(self.runs, tuple) or not self.runs:
            raise ValueError("asset performance group requires runs")
        for run in self.runs:
            _revalidate_run(run)
        if self.runs != tuple(sorted(self.runs, key=lambda run: run.run_id)):
            raise ValueError("asset performance runs must use canonical run-id order")
        if any(run.asset != self.asset for run in self.runs):
            raise ValueError("asset performance group mixes assets")
        expected = _group_values(self.runs)
        actual = _GroupValues(
            run_count=self.run_count,
            timeframe_count=self.timeframe_count,
            strategy_count=self.strategy_count,
            data_start=self.data_start,
            data_end=self.data_end,
            total_initial_capital=self.total_initial_capital,
            total_final_equity=self.total_final_equity,
            total_net_profit=self.total_net_profit,
            capital_weighted_return=self.capital_weighted_return,
            profitable_runs=self.profitable_runs,
            losing_runs=self.losing_runs,
            flat_runs=self.flat_runs,
            number_of_closed_trades=self.number_of_closed_trades,
            maximum_drawdown_pct=self.maximum_drawdown_pct,
            best_run_id=self.best_run_id,
            best_total_return=self.best_total_return,
            worst_run_id=self.worst_run_id,
            worst_total_return=self.worst_total_return,
        )
        if actual != expected:
            raise ValueError("asset performance aggregation is inconsistent")


@dataclass(frozen=True, slots=True)
class AssetPerformanceReport:
    """Versioned deterministic aggregation over a bounded set of verified runs."""

    contract_version: int
    report_id: str
    run_count: int
    asset_count: int
    assets: tuple[AssetPerformanceGroup, ...]

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("unsupported asset performance contract version")
        if type(self.run_count) is not int or type(self.asset_count) is not int:
            raise ValueError("asset performance report counts must be integers")
        if not isinstance(self.assets, tuple) or not self.assets:
            raise ValueError("asset performance report requires assets")
        for group in self.assets:
            _revalidate_group(group)
        expected_order = tuple(sorted(self.assets, key=lambda group: group.asset))
        if self.assets != expected_order:
            raise ValueError("asset performance assets must use canonical order")
        if len({group.asset for group in self.assets}) != len(self.assets):
            raise ValueError("asset performance report contains duplicate assets")
        expected_run_count = sum(group.run_count for group in self.assets)
        if (
            self.run_count != expected_run_count
            or not 1 <= self.run_count <= _MAX_TRACKED_RUNS
            or self.asset_count != len(self.assets)
        ):
            raise ValueError("asset performance report counts are inconsistent")
        run_ids = [run.run_id for group in self.assets for run in group.runs]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("asset performance report contains duplicate runs")
        if self.report_id != _report_id(self.assets):
            raise ValueError("asset performance report identity is inconsistent")


def asset_performance_run(entry: BacktestComparisonEntry) -> AssetPerformanceRun:
    """Project one already verified comparison entry to an asset-bound record."""

    _revalidate_entry(entry)
    asset, dataset_kind, timeframe = _parse_dataset_key(entry.dataset_key)
    return AssetPerformanceRun(
        run_id=entry.run_id,
        asset=asset,
        dataset_kind=dataset_kind,
        timeframe=timeframe,
        strategy_name=entry.strategy_name,
        strategy_version=entry.strategy_version,
        data_start=entry.data_start,
        data_end=entry.data_end,
        initial_capital=entry.initial_capital,
        final_equity=entry.final_equity,
        total_return=entry.total_return,
        net_profit=entry.net_profit,
        maximum_drawdown_pct=entry.maximum_drawdown_pct,
        number_of_closed_trades=entry.number_of_closed_trades,
        logical_result_checksum=entry.logical_result_checksum,
    )


def normalize_asset_performance_run_ids(
    run_ids: Sequence[str],
) -> tuple[str, ...]:
    """Validate, deduplicate and canonically order a bounded run-id set."""

    if not 1 <= len(run_ids) <= _MAX_TRACKED_RUNS:
        raise ValueError("asset performance requires between 1 and 100 run ids")
    normalized = tuple(BacktestRunId(run_id).value for run_id in run_ids)
    if len(set(normalized)) != len(normalized):
        raise ValueError("asset performance run ids must be unique")
    return tuple(sorted(normalized))


def build_asset_performance_report(
    entries: Sequence[BacktestComparisonEntry],
) -> AssetPerformanceReport:
    """Group verified run projections by canonical asset without statistical mixing."""

    if not 1 <= len(entries) <= _MAX_TRACKED_RUNS:
        raise ValueError("asset performance requires between 1 and 100 runs")
    runs = tuple(asset_performance_run(entry) for entry in entries)
    run_ids = [run.run_id for run in runs]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("asset performance run ids must be unique")

    grouped: defaultdict[AssetIdentity, list[AssetPerformanceRun]] = defaultdict(list)
    for run in runs:
        grouped[run.asset].append(run)
    assets = tuple(
        _build_group(asset, tuple(sorted(asset_runs, key=lambda run: run.run_id)))
        for asset, asset_runs in sorted(grouped.items(), key=lambda item: item[0])
    )
    return AssetPerformanceReport(
        contract_version=1,
        report_id=_report_id(assets),
        run_count=len(runs),
        asset_count=len(assets),
        assets=assets,
    )


def build_asset_performance_report_from_summaries(
    summaries: Sequence[Mapping[str, object]],
) -> AssetPerformanceReport:
    """Build an asset report from summaries obtained after full run verification."""

    if not 1 <= len(summaries) <= _MAX_TRACKED_RUNS:
        raise ValueError("asset performance requires between 1 and 100 summaries")
    return build_asset_performance_report(
        tuple(comparison_entry_from_summary(summary) for summary in summaries)
    )


def asset_performance_report_from_mapping(
    value: Mapping[str, object],
) -> AssetPerformanceReport:
    """Rebuild and validate one canonical serialized asset-performance report."""

    try:
        if frozenset(value) != _REPORT_FIELDS:
            raise ValueError
        assets_value = value.get("assets")
        if not isinstance(assets_value, list):
            raise TypeError
        return AssetPerformanceReport(
            contract_version=_integer(value.get("contract_version")),
            report_id=_string(value.get("report_id")),
            run_count=_integer(value.get("run_count")),
            asset_count=_integer(value.get("asset_count")),
            assets=tuple(_group_from_mapping(_mapping(item)) for item in assets_value),
        )
    except (InvalidOperation, TypeError, ValueError):
        raise BacktestResultCorruptError(
            "O relatório de performance por ativo é inválido."
        ) from None


def _build_group(
    asset: AssetIdentity,
    runs: tuple[AssetPerformanceRun, ...],
) -> AssetPerformanceGroup:
    values = _group_values(runs)
    return AssetPerformanceGroup(
        asset=asset,
        run_count=values.run_count,
        timeframe_count=values.timeframe_count,
        strategy_count=values.strategy_count,
        data_start=values.data_start,
        data_end=values.data_end,
        total_initial_capital=values.total_initial_capital,
        total_final_equity=values.total_final_equity,
        total_net_profit=values.total_net_profit,
        capital_weighted_return=values.capital_weighted_return,
        profitable_runs=values.profitable_runs,
        losing_runs=values.losing_runs,
        flat_runs=values.flat_runs,
        number_of_closed_trades=values.number_of_closed_trades,
        maximum_drawdown_pct=values.maximum_drawdown_pct,
        best_run_id=values.best_run_id,
        best_total_return=values.best_total_return,
        worst_run_id=values.worst_run_id,
        worst_total_return=values.worst_total_return,
        runs=runs,
    )


def _group_values(runs: tuple[AssetPerformanceRun, ...]) -> _GroupValues:
    total_initial = sum((run.initial_capital for run in runs), Decimal("0"))
    total_final = sum((run.final_equity for run in runs), Decimal("0"))
    total_net = sum((run.net_profit for run in runs), Decimal("0"))
    best = min(runs, key=lambda run: (-run.total_return, run.run_id))
    worst = min(runs, key=lambda run: (run.total_return, run.run_id))
    return _GroupValues(
        run_count=len(runs),
        timeframe_count=len({run.timeframe for run in runs}),
        strategy_count=len({(run.strategy_name, run.strategy_version) for run in runs}),
        data_start=min(run.data_start for run in runs),
        data_end=max(run.data_end for run in runs),
        total_initial_capital=total_initial,
        total_final_equity=total_final,
        total_net_profit=total_net,
        capital_weighted_return=total_net / total_initial * Decimal("100"),
        profitable_runs=sum(run.net_profit > 0 for run in runs),
        losing_runs=sum(run.net_profit < 0 for run in runs),
        flat_runs=sum(run.net_profit == 0 for run in runs),
        number_of_closed_trades=sum(run.number_of_closed_trades for run in runs),
        maximum_drawdown_pct=max(run.maximum_drawdown_pct for run in runs),
        best_run_id=best.run_id,
        best_total_return=best.total_return,
        worst_run_id=worst.run_id,
        worst_total_return=worst.total_return,
    )


def _report_id(assets: tuple[AssetPerformanceGroup, ...]) -> str:
    payload = {
        "contract_version": 1,
        "assets": assets,
    }
    return hashlib.sha256(
        b"adt-asset-performance-v1\x00" + canonical_json_bytes(payload)
    ).hexdigest()


def _parse_dataset_key(dataset_key: str) -> tuple[AssetIdentity, str, str]:
    if not isinstance(dataset_key, str):
        raise ValueError("asset performance dataset key is invalid")
    parts = dataset_key.split(":")
    if len(parts) != 5:
        raise ValueError("asset performance dataset key is invalid")
    dataset_kind, exchange, market_type, symbol, timeframe = parts
    asset = AssetIdentity(exchange=exchange, market_type=market_type, symbol=symbol)
    if dataset_kind not in {"raw", "derived"} or _TIMEFRAME.fullmatch(timeframe) is None:
        raise ValueError("asset performance dataset key is invalid")
    return asset, dataset_kind, timeframe


def _validate_symbol(symbol: object) -> None:
    if not isinstance(symbol, str) or symbol.count("/") != 1:
        raise ValueError("asset symbol must be one canonical BASE/QUOTE pair")
    base, quote = symbol.split("/", 1)
    if (
        _ASSET_TOKEN.fullmatch(base) is None
        or _ASSET_TOKEN.fullmatch(quote) is None
        or base == quote
    ):
        raise ValueError("asset symbol must be one canonical BASE/QUOTE pair")


def _revalidate_asset(asset: object) -> None:
    if type(asset) is not AssetIdentity:
        raise ValueError("asset identity contract is invalid")
    AssetIdentity.__post_init__(asset)


def _revalidate_entry(entry: object) -> None:
    if type(entry) is not BacktestComparisonEntry:
        raise ValueError("asset performance entry contract is invalid")
    candidate = copy(entry)
    BacktestComparisonEntry.__post_init__(candidate)
    if candidate != entry:
        raise ValueError("asset performance entry is not canonical")


def _revalidate_run(run: object) -> None:
    if type(run) is not AssetPerformanceRun:
        raise ValueError("asset performance run contract is invalid")
    candidate = copy(run)
    AssetPerformanceRun.__post_init__(candidate)
    if candidate != run:
        raise ValueError("asset performance run is not canonical")


def _revalidate_group(group: object) -> None:
    if type(group) is not AssetPerformanceGroup:
        raise ValueError("asset performance group contract is invalid")
    candidate = copy(group)
    AssetPerformanceGroup.__post_init__(candidate)
    if candidate != group:
        raise ValueError("asset performance group is not canonical")


def _group_from_mapping(value: Mapping[str, object]) -> AssetPerformanceGroup:
    if frozenset(value) != _GROUP_FIELDS:
        raise ValueError
    runs_value = value.get("runs")
    if not isinstance(runs_value, list):
        raise TypeError
    return AssetPerformanceGroup(
        asset=_asset_from_mapping(_mapping(value.get("asset"))),
        run_count=_integer(value.get("run_count")),
        timeframe_count=_integer(value.get("timeframe_count")),
        strategy_count=_integer(value.get("strategy_count")),
        data_start=datetime.fromisoformat(_string(value.get("data_start"))),
        data_end=datetime.fromisoformat(_string(value.get("data_end"))),
        total_initial_capital=_decimal(value.get("total_initial_capital")),
        total_final_equity=_decimal(value.get("total_final_equity")),
        total_net_profit=_decimal(value.get("total_net_profit")),
        capital_weighted_return=_decimal(value.get("capital_weighted_return")),
        profitable_runs=_integer(value.get("profitable_runs")),
        losing_runs=_integer(value.get("losing_runs")),
        flat_runs=_integer(value.get("flat_runs")),
        number_of_closed_trades=_integer(value.get("number_of_closed_trades")),
        maximum_drawdown_pct=_decimal(value.get("maximum_drawdown_pct")),
        best_run_id=_string(value.get("best_run_id")),
        best_total_return=_decimal(value.get("best_total_return")),
        worst_run_id=_string(value.get("worst_run_id")),
        worst_total_return=_decimal(value.get("worst_total_return")),
        runs=tuple(_run_from_mapping(_mapping(item)) for item in runs_value),
    )


def _run_from_mapping(value: Mapping[str, object]) -> AssetPerformanceRun:
    if frozenset(value) != _RUN_FIELDS:
        raise ValueError
    return AssetPerformanceRun(
        run_id=_string(value.get("run_id")),
        asset=_asset_from_mapping(_mapping(value.get("asset"))),
        dataset_kind=_string(value.get("dataset_kind")),
        timeframe=_string(value.get("timeframe")),
        strategy_name=_string(value.get("strategy_name")),
        strategy_version=_string(value.get("strategy_version")),
        data_start=datetime.fromisoformat(_string(value.get("data_start"))),
        data_end=datetime.fromisoformat(_string(value.get("data_end"))),
        initial_capital=_decimal(value.get("initial_capital")),
        final_equity=_decimal(value.get("final_equity")),
        total_return=_decimal(value.get("total_return")),
        net_profit=_decimal(value.get("net_profit")),
        maximum_drawdown_pct=_decimal(value.get("maximum_drawdown_pct")),
        number_of_closed_trades=_integer(value.get("number_of_closed_trades")),
        logical_result_checksum=_string(value.get("logical_result_checksum")),
    )


def _asset_from_mapping(value: Mapping[str, object]) -> AssetIdentity:
    if frozenset(value) != _ASSET_FIELDS:
        raise ValueError
    return AssetIdentity(
        exchange=_string(value.get("exchange")),
        market_type=_string(value.get("market_type")),
        symbol=_string(value.get("symbol")),
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
    if type(value) is not int:
        raise TypeError
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise TypeError
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError
    return result
