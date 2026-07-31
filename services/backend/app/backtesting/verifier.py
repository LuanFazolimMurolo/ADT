"""Independent verification of published deterministic backtest results."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import pyarrow.parquet as pq

from app.backtesting.artifacts import (
    build_logical_result_checksum,
    build_run_id_from_values,
)
from app.backtesting.domain import (
    BacktestRunId,
    ClosedTrade,
    EquityPoint,
    Fill,
    FillLiquidity,
    FillReason,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    SimulatedOrder,
    TimeInForce,
)
from app.backtesting.engine import BacktestExecutionResult
from app.backtesting.errors import BacktestResultCorruptError
from app.backtesting.ledger import LedgerEntry, LedgerEntryType, verify_ledger
from app.backtesting.metrics import calculate_metrics, derive_closed_trades
from app.backtesting.portfolio import apply_fill, initialize_portfolio, mark_to_market
from app.backtesting.serialization import (
    canonical_checksum,
    canonical_value,
    file_checksum,
    read_json_envelope,
)
from app.market_data.datasets import DatasetSnapshot
from app.market_data.filesystem import ensure_safe_path, market_root
from app.market_data.locks import DatasetLockManager

_EXPECTED_ARTIFACTS = {
    "config.json",
    "result.json",
    "orders.jsonl",
    "fills.jsonl",
    "ledger.jsonl",
    "equity.parquet",
    "trades.jsonl",
}


class SnapshotVerifier(Protocol):
    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot: ...

    def verify_unchanged(self) -> DatasetSnapshot: ...


SnapshotFactory = Callable[[Path], SnapshotVerifier]


@dataclass(frozen=True, slots=True)
class BacktestVerification:
    run_id: BacktestRunId
    logical_result_checksum: str
    artifact_count: int
    order_count: int
    fill_count: int
    ledger_count: int
    trade_count: int
    candle_count: int


class BacktestResultVerifier:
    """Verify a run without executing the strategy again."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("backtests"),
        lock_timeout_seconds: float = 30,
        lock_stale_after_seconds: float = 300,
        acquire_lock: bool = True,
        snapshot_factory: SnapshotFactory | None = None,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise ValueError("backtest directory must be safe and relative")
        self._data_dir = data_dir
        self._market = market_root(data_dir)
        self._root = ensure_safe_path(self._market, self._market / directory)
        self._acquire_lock = acquire_lock
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
        )
        self._snapshot_factory = snapshot_factory or _default_snapshot_factory

    def verify(self, run_id: str) -> BacktestVerification:
        typed_run_id = BacktestRunId(run_id)
        if self._acquire_lock:
            with self._locks.acquire(f"backtest:{run_id}"):
                return self._verify_unlocked(typed_run_id)
        return self._verify_unlocked(typed_run_id)

    def _verify_unlocked(self, run_id: BacktestRunId) -> BacktestVerification:
        root = ensure_safe_path(self._market, self._root / run_id.value)
        manifest = _read_envelope(root / "manifest.json", "manifest")
        if _dict(manifest, "run_id").get("value") != run_id.value:
            raise BacktestResultCorruptError("O run_id do manifest diverge do diretório.")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            raise BacktestResultCorruptError("A lista de artefatos é inválida.")
        declared = [item.get("relative_path") for item in artifacts if isinstance(item, dict)]
        if (
            len(declared) != len(_EXPECTED_ARTIFACTS)
            or len(set(declared)) != len(declared)
            or set(declared) != _EXPECTED_ARTIFACTS
        ):
            raise BacktestResultCorruptError("O conjunto de artefatos do backtest é inválido.")
        try:
            actual_entries = {path.name for path in root.iterdir()}
        except OSError:
            raise BacktestResultCorruptError("O diretório do backtest é inválido.") from None
        if actual_entries != _EXPECTED_ARTIFACTS | {"manifest.json"}:
            raise BacktestResultCorruptError(
                "O diretório do backtest contém artefatos inesperados."
            )
        for item in artifacts:
            if not isinstance(item, dict):
                raise BacktestResultCorruptError()
            relative = item.get("relative_path")
            if not isinstance(relative, str):
                raise BacktestResultCorruptError()
            path = ensure_safe_path(self._market, root / relative)
            try:
                size = path.stat().st_size
            except OSError:
                raise BacktestResultCorruptError("Um artefato declarado está ausente.") from None
            if size != item.get("size_bytes") or file_checksum(path) != item.get("checksum"):
                raise BacktestResultCorruptError("O checksum de um artefato diverge.")

        config_raw = _read_envelope(root / "config.json", "config")
        result_raw = _read_envelope(root / "result.json", "result")
        self._verify_manifest_config(manifest, config_raw)
        if result_raw.get("run_id") != run_id.value:
            raise BacktestResultCorruptError("O run_id do resultado diverge do diretório.")
        snapshot_value = {
            "snapshot_id": manifest.get("snapshot_id"),
            "dataset_key": manifest.get("dataset_key"),
            "dataset_version": manifest.get("dataset_version"),
            "checksum": manifest.get("dataset_checksum"),
            "data_range": manifest.get("snapshot_data_range"),
        }
        if build_run_id_from_values(config_raw, snapshot_value) != run_id:
            raise BacktestResultCorruptError("A identidade lógica do run diverge.")
        verified_snapshot = self._verify_snapshot(manifest)

        orders = tuple(_decode_order(item) for item in _read_jsonl(root / "orders.jsonl"))
        fills = tuple(_decode_fill(item) for item in _read_jsonl(root / "fills.jsonl"))
        ledger = tuple(_decode_ledger(item) for item in _read_jsonl(root / "ledger.jsonl"))
        trades = tuple(_decode_trade(item) for item in _read_jsonl(root / "trades.jsonl"))
        equity = _read_equity(root / "equity.parquet")
        _verify_sequences(orders, fills)
        ledger_verification = verify_ledger(ledger)
        if not equity:
            raise BacktestResultCorruptError("A curva de patrimônio está vazia.")
        final_portfolio = _decode_portfolio(_dict(result_raw, "final_portfolio"))
        if (
            final_portfolio.quote_cash != ledger_verification.final_quote_balance
            or final_portfolio.base_quantity != ledger_verification.final_base_balance
        ):
            raise BacktestResultCorruptError("O portfolio final diverge do ledger.")
        initial_capital = _decimal(manifest.get("initial_capital"))
        reconstructed_portfolio = initialize_portfolio(initial_capital)
        fills_by_candle: dict[int, list[Fill]] = {}
        for fill in fills:
            fills_by_candle.setdefault(fill.candle_index, []).append(fill)
        for point in equity:
            for fill in fills_by_candle.get(point.candle_index, []):
                reconstructed_portfolio = apply_fill(reconstructed_portfolio, fill).after
            reconstructed_portfolio = mark_to_market(reconstructed_portfolio, point.close_price)
            snapshot = reconstructed_portfolio.snapshot()
            if (
                snapshot.quote_cash != point.quote_cash
                or snapshot.base_quantity != point.base_quantity
                or snapshot.equity != point.equity
                or snapshot.peak_equity != point.peak_equity
                or snapshot.drawdown != point.drawdown
                or snapshot.drawdown_pct != point.drawdown_pct
            ):
                raise BacktestResultCorruptError("A curva de patrimônio não é reconstruível.")
        if reconstructed_portfolio.snapshot() != final_portfolio:
            raise BacktestResultCorruptError("O portfolio final não é reconstruível.")

        expected_trades = derive_closed_trades(fills)
        if trades != expected_trades:
            raise BacktestResultCorruptError("Os trades divergem dos fills.")
        execution = BacktestExecutionResult(
            snapshot=verified_snapshot,
            candles_processed=_int(result_raw.get("candles_processed")),
            orders=orders,
            fills=fills,
            ledger=ledger,
            equity_curve=equity,
            final_portfolio=final_portfolio,
            risk_halt=_bool(result_raw.get("risk_halt")),
        )
        metrics = calculate_metrics(execution, initial_equity=initial_capital, trades=trades)
        if canonical_value(metrics) != result_raw.get("metrics"):
            raise BacktestResultCorruptError("As métricas publicadas não são reproduzíveis.")
        logical_checksum = build_logical_result_checksum(
            run_id=run_id,
            execution=execution,
            trades=trades,
            metrics=metrics,
        )
        if logical_checksum != manifest.get(
            "logical_result_checksum"
        ) or logical_checksum != result_raw.get("logical_result_checksum"):
            raise BacktestResultCorruptError("O checksum lógico do resultado diverge.")
        if _int(manifest.get("candle_count")) != execution.candles_processed:
            raise BacktestResultCorruptError("A contagem de candles do manifest diverge.")
        if _int(manifest.get("order_count")) != len(orders):
            raise BacktestResultCorruptError("A contagem de ordens do manifest diverge.")
        if _int(manifest.get("fill_count")) != len(fills):
            raise BacktestResultCorruptError("A contagem de fills do manifest diverge.")
        if _int(manifest.get("trade_count")) != len(trades):
            raise BacktestResultCorruptError("A contagem de trades do manifest diverge.")
        return BacktestVerification(
            run_id=run_id,
            logical_result_checksum=logical_checksum,
            artifact_count=len(artifacts),
            order_count=len(orders),
            fill_count=len(fills),
            ledger_count=len(ledger),
            trade_count=len(trades),
            candle_count=execution.candles_processed,
        )

    @staticmethod
    def _verify_manifest_config(
        manifest: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        if manifest.get("status") != "COMPLETE":
            raise BacktestResultCorruptError("O manifest publicado não está COMPLETE.")
        for field in (
            "engine_version",
            "schema_version",
            "snapshot_id",
            "data_range",
            "strategy",
            "initial_capital",
            "execution",
            "risk_limits",
        ):
            if manifest.get(field) != config.get(field):
                raise BacktestResultCorruptError(
                    "O manifest diverge da configuração lógica do backtest."
                )
        strategy = _dict(config, "strategy")
        parameters = strategy.get("parameters")
        if canonical_checksum(parameters) != manifest.get("strategy_parameters_checksum"):
            raise BacktestResultCorruptError("O checksum dos parâmetros da estratégia diverge.")
        try:
            created_at = datetime.fromisoformat(_str(manifest.get("created_at")))
            completed_at = datetime.fromisoformat(_str(manifest.get("completed_at")))
        except ValueError:
            raise BacktestResultCorruptError("Os timestamps do manifest são inválidos.") from None
        created_offset = created_at.utcoffset()
        completed_offset = completed_at.utcoffset()
        if (
            created_at.tzinfo is None
            or completed_at.tzinfo is None
            or created_offset is None
            or completed_offset is None
            or created_offset.total_seconds() != 0
            or completed_offset.total_seconds() != 0
            or completed_at < created_at
        ):
            raise BacktestResultCorruptError("Os timestamps do manifest são inválidos.")

    def _verify_snapshot(self, manifest: dict[str, Any]) -> DatasetSnapshot:
        reader = self._snapshot_factory(self._data_dir)
        try:
            snapshot = reader.open_snapshot(_str(manifest.get("snapshot_id")))
            verified = reader.verify_unchanged()
        except Exception:
            raise BacktestResultCorruptError(
                "O snapshot associado ao backtest é inválido."
            ) from None
        expected_range = _dict(manifest, "snapshot_data_range")
        expected_values = (
            _str(manifest.get("snapshot_id")),
            _str(manifest.get("dataset_key")),
            _str(manifest.get("dataset_version")),
            _str(manifest.get("dataset_checksum")),
            datetime.fromisoformat(_str(expected_range.get("start"))),
            datetime.fromisoformat(_str(expected_range.get("end"))),
        )
        for candidate in (snapshot, verified):
            actual_values = (
                candidate.snapshot_id,
                candidate.dataset_key,
                candidate.dataset_version,
                candidate.checksum,
                candidate.data_range.start,
                candidate.data_range.end,
            )
            if actual_values != expected_values:
                raise BacktestResultCorruptError("O snapshot associado ao run diverge.")
        return verified


def _default_snapshot_factory(data_dir: Path) -> SnapshotVerifier:
    from app.market_data.snapshots import MarketDatasetReader

    return MarketDatasetReader(data_dir)


def _read_envelope(path: Path, key: str) -> dict[str, Any]:
    try:
        return read_json_envelope(path, key)
    except ValueError:
        raise BacktestResultCorruptError("Um envelope JSON do backtest é inválido.") from None


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            for raw in stream:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError
                yield value
    except (OSError, ValueError, TypeError):
        raise BacktestResultCorruptError("Um artefato JSONL é inválido.") from None


def _read_equity(path: Path) -> tuple[EquityPoint, ...]:
    try:
        rows = pq.read_table(path).to_pylist()
        return tuple(
            EquityPoint(
                candle_index=_int(row.get("candle_index")),
                event_time=datetime.fromisoformat(_str(row.get("event_time"))),
                close_price=_decimal(row.get("close_price")),
                quote_cash=_decimal(row.get("quote_cash")),
                base_quantity=_decimal(row.get("base_quantity")),
                equity=_decimal(row.get("equity")),
                peak_equity=_decimal(row.get("peak_equity")),
                drawdown=_decimal(row.get("drawdown")),
                drawdown_pct=_decimal(row.get("drawdown_pct")),
            )
            for row in rows
        )
    except Exception:
        raise BacktestResultCorruptError("O artefato de equity é inválido.") from None


def _decode_order(raw: dict[str, Any]) -> SimulatedOrder:
    intent_raw = _dict(raw, "intent")
    return SimulatedOrder(
        order_id=_str(raw.get("order_id")),
        created_sequence=_int(raw.get("created_sequence")),
        created_at=datetime.fromisoformat(_str(raw.get("created_at"))),
        created_candle_index=_int(raw.get("created_candle_index")),
        eligible_candle_index=_int(raw.get("eligible_candle_index")),
        intent=OrderIntent(
            side=OrderSide(_str(intent_raw.get("side"))),
            order_type=OrderType(_str(intent_raw.get("order_type"))),
            quantity=_decimal(intent_raw.get("quantity")),
            time_in_force=TimeInForce(_str(intent_raw.get("time_in_force"))),
            limit_price=_optional_decimal(intent_raw.get("limit_price")),
            stop_price=_optional_decimal(intent_raw.get("stop_price")),
            client_tag=_optional_str(intent_raw.get("client_tag")),
        ),
        status=OrderStatus(_str(raw.get("status"))),
        opened_at=_optional_datetime(raw.get("opened_at")),
        terminal_at=_optional_datetime(raw.get("terminal_at")),
        rejection_code=_optional_str(raw.get("rejection_code")),
    )


def _decode_fill(raw: dict[str, Any]) -> Fill:
    return Fill(
        fill_id=_str(raw.get("fill_id")),
        order_id=_str(raw.get("order_id")),
        reason=FillReason(_str(raw.get("reason"))),
        liquidity=FillLiquidity(_str(raw.get("liquidity"))),
        side=OrderSide(_str(raw.get("side"))),
        quantity=_decimal(raw.get("quantity")),
        base_price=_decimal(raw.get("base_price")),
        execution_price=_decimal(raw.get("execution_price")),
        notional=_decimal(raw.get("notional")),
        fee=_decimal(raw.get("fee")),
        slippage_cost=_decimal(raw.get("slippage_cost")),
        event_time=datetime.fromisoformat(_str(raw.get("event_time"))),
        candle_index=_int(raw.get("candle_index")),
    )


def _decode_ledger(raw: dict[str, Any]) -> LedgerEntry:
    return LedgerEntry(
        sequence=_int(raw.get("sequence")),
        event_time=datetime.fromisoformat(_str(raw.get("event_time"))),
        candle_index=_int(raw.get("candle_index")),
        entry_type=LedgerEntryType(_str(raw.get("entry_type"))),
        quote_delta=_decimal(raw.get("quote_delta")),
        base_delta=_decimal(raw.get("base_delta")),
        fee=_decimal(raw.get("fee")),
        realized_pnl=_decimal(raw.get("realized_pnl")),
        quote_balance=_decimal(raw.get("quote_balance")),
        base_balance=_decimal(raw.get("base_balance")),
        previous_hash=_str(raw.get("previous_hash")),
        entry_hash=_str(raw.get("entry_hash")),
        order_id=_optional_str(raw.get("order_id")),
        fill_id=_optional_str(raw.get("fill_id")),
        notional=_decimal(raw.get("notional")),
        reference_price=_optional_decimal(raw.get("reference_price")),
    )


def _decode_trade(raw: dict[str, Any]) -> ClosedTrade:
    return ClosedTrade(
        entry_time=datetime.fromisoformat(_str(raw.get("entry_time"))),
        exit_time=datetime.fromisoformat(_str(raw.get("exit_time"))),
        quantity=_decimal(raw.get("quantity")),
        average_entry=_decimal(raw.get("average_entry")),
        average_exit=_decimal(raw.get("average_exit")),
        gross_pnl=_decimal(raw.get("gross_pnl")),
        fees=_decimal(raw.get("fees")),
        net_pnl=_decimal(raw.get("net_pnl")),
        return_pct=_optional_decimal(raw.get("return_pct")),
        bars_held=_int(raw.get("bars_held")),
        entry_fill_ids=tuple(_str(value) for value in _list(raw, "entry_fill_ids")),
        exit_fill_ids=tuple(_str(value) for value in _list(raw, "exit_fill_ids")),
    )


def _decode_portfolio(raw: dict[str, Any]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        quote_cash=_decimal(raw.get("quote_cash")),
        base_quantity=_decimal(raw.get("base_quantity")),
        average_entry_price=_decimal(raw.get("average_entry_price")),
        realized_pnl=_decimal(raw.get("realized_pnl")),
        unrealized_pnl=_decimal(raw.get("unrealized_pnl")),
        total_fees=_decimal(raw.get("total_fees")),
        total_slippage_cost=_decimal(raw.get("total_slippage_cost")),
        equity=_decimal(raw.get("equity")),
        peak_equity=_decimal(raw.get("peak_equity")),
        drawdown=_decimal(raw.get("drawdown")),
        cost_basis=_decimal(raw.get("cost_basis")),
        drawdown_pct=_decimal(raw.get("drawdown_pct")),
    )


def _verify_sequences(orders: tuple[SimulatedOrder, ...], fills: tuple[Fill, ...]) -> None:
    if [order.created_sequence for order in orders] != list(range(1, len(orders) + 1)):
        raise BacktestResultCorruptError("A sequência das ordens é inválida.")
    if any(
        order.order_id != f"O{position:012d}" for position, order in enumerate(orders, start=1)
    ) or any(fill.fill_id != f"F{position:012d}" for position, fill in enumerate(fills, start=1)):
        raise BacktestResultCorruptError("Os IDs sequenciais do backtest são inválidos.")
    order_by_id = {order.order_id: order for order in orders}
    if len(order_by_id) != len(orders) or len({fill.fill_id for fill in fills}) != len(fills):
        raise BacktestResultCorruptError("Ordens ou fills possuem IDs duplicados.")
    fills_by_order: dict[str, int] = {}
    for fill in fills:
        order = order_by_id.get(fill.order_id)
        if order is None:
            raise BacktestResultCorruptError("Um fill não pertence a uma ordem.")
        if (
            fill.side is not order.intent.side
            or fill.quantity != order.intent.quantity
            or fill.candle_index < order.eligible_candle_index
            or order.terminal_at != fill.event_time
        ):
            raise BacktestResultCorruptError("Um fill diverge da ordem correspondente.")
        fills_by_order[fill.order_id] = fills_by_order.get(fill.order_id, 0) + 1
    for order in orders:
        count = fills_by_order.get(order.order_id, 0)
        if order.status is OrderStatus.FILLED and count != 1:
            raise BacktestResultCorruptError("Uma ordem FILLED não possui exatamente um fill.")
        if order.status is not OrderStatus.FILLED and count:
            raise BacktestResultCorruptError("Uma ordem não preenchida possui fill.")


def _dict(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise BacktestResultCorruptError()
    return value


def _list(raw: dict[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise BacktestResultCorruptError()
    return value


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise BacktestResultCorruptError()
    return value


def _optional_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise BacktestResultCorruptError()
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise BacktestResultCorruptError()
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise BacktestResultCorruptError()
    try:
        result = Decimal(value)
    except Exception:
        raise BacktestResultCorruptError() from None
    if not result.is_finite():
        raise BacktestResultCorruptError()
    return result


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(_str(value))
