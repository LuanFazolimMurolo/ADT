"""Canonical JSONL and CSV exports for verified paper-trading journal queries."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.backtesting.serialization import canonical_json_bytes, decimal_text
from app.paper_trading.errors import InvalidPaperSessionError
from app.paper_trading.journal import PaperTradeExecution
from app.paper_trading.journal_query import (
    PaperTradeJournalFilter,
    PaperTradeJournalReadService,
    PaperTradeRecord,
)
from app.paper_trading.persisted_state import PaperPersistedStateVerifier
from app.paper_trading.repository import PaperTradingRepository

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "adt-paper-trade-journal-export-v1"
_CSV_COLUMNS = (
    "session_id",
    "trade_id",
    "sequence",
    "status",
    "symbol",
    "base_asset",
    "quote_asset",
    "timeframe",
    "strategy_name",
    "strategy_version",
    "strategy_parameters_json",
    "opened_at",
    "last_entry_at",
    "first_exit_at",
    "closed_at",
    "opened_quantity",
    "closed_quantity",
    "remaining_quantity",
    "average_entry_price",
    "average_exit_price",
    "entry_notional",
    "exit_notional",
    "entry_fees",
    "exit_fees",
    "total_fees",
    "entry_slippage_cost",
    "exit_slippage_cost",
    "total_slippage_cost",
    "entry_cost_basis",
    "released_cost_basis",
    "remaining_cost_basis",
    "realized_pnl",
    "unrealized_pnl",
    "net_pnl",
    "mark_price",
    "entry_count",
    "exit_count",
    "entry_fill_ids",
    "exit_fill_ids",
    "entry_order_ids",
    "exit_order_ids",
    "entry_client_tags",
    "exit_client_tags",
    "entry_fill_reasons",
    "exit_fill_reasons",
    "state_id",
    "state_checksum",
    "last_candle_open_time",
    "replayed_at",
)


class PaperTradeExportFormat(StrEnum):
    JSONL = "jsonl"
    CSV = "csv"


@dataclass(frozen=True, slots=True)
class PaperTradeExport:
    """In-memory deterministic export with independently verifiable identity."""

    format: PaperTradeExportFormat
    query_checksum: str
    content_checksum: str
    filename: str
    media_type: str
    rows_count: int
    content: bytes

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.format, PaperTradeExportFormat):
                raise ValueError("format must be canonical")
            for value, field_name in (
                (self.query_checksum, "query_checksum"),
                (self.content_checksum, "content_checksum"),
            ):
                if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                    raise ValueError(f"{field_name} must be one lowercase SHA-256 digest")
            if type(self.rows_count) is not int or self.rows_count < 0:
                raise ValueError("rows_count must be nonnegative")
            if not isinstance(self.content, bytes) or not self.content.endswith(b"\n"):
                raise ValueError("content must be newline-terminated bytes")
            if hashlib.sha256(self.content).hexdigest() != self.content_checksum:
                raise ValueError("content checksum is inconsistent")
            extension, media_type = _format_metadata(self.format)
            expected_filename = f"paper-trade-journal-{self.query_checksum[:16]}.{extension}"
            if self.filename != expected_filename or self.media_type != media_type:
                raise ValueError("export metadata is inconsistent")
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


class PaperTradeJournalExportService:
    """Export all matching trades through the same verified bounded read path."""

    def __init__(
        self,
        repository: PaperTradingRepository,
        state_verifier: PaperPersistedStateVerifier,
    ) -> None:
        if not isinstance(repository, PaperTradingRepository) or not isinstance(
            state_verifier, PaperPersistedStateVerifier
        ):
            raise InvalidPaperSessionError("O repositório de exportação é inválido.")
        self._reader = PaperTradeJournalReadService(repository, state_verifier)

    def export(
        self,
        filters: PaperTradeJournalFilter,
        *,
        format: PaperTradeExportFormat,
    ) -> PaperTradeExport:
        if not isinstance(format, PaperTradeExportFormat):
            raise InvalidPaperSessionError("O formato de exportação é inválido.")
        records = self._reader.records_for_export(filters)
        return build_paper_trade_export(filters, records, format=format)


def build_paper_trade_export(
    filters: PaperTradeJournalFilter,
    records: tuple[PaperTradeRecord, ...],
    *,
    format: PaperTradeExportFormat,
) -> PaperTradeExport:
    """Serialize one canonical, already ordered journal selection."""
    if not isinstance(filters, PaperTradeJournalFilter):
        raise InvalidPaperSessionError("Os filtros de exportação são inválidos.")
    PaperTradeJournalFilter.__post_init__(filters)
    if not isinstance(records, tuple) or any(
        not isinstance(record, PaperTradeRecord) for record in records
    ):
        raise InvalidPaperSessionError("Os registros de exportação são inválidos.")
    if len(records) > 10_000:
        raise InvalidPaperSessionError(
            "A exportação do journal excede o limite de 10000 operações."
        )
    for record in records:
        PaperTradeRecord.__post_init__(record)
    ordered = tuple(sorted(records, key=_record_key, reverse=True))
    trade_ids = tuple(record.trade.trade_id for record in ordered)
    if len(set(trade_ids)) != len(trade_ids):
        raise InvalidPaperSessionError("A exportação contém operações duplicadas.")
    query_checksum = hashlib.sha256(
        canonical_json_bytes({"schema": _SCHEMA, "filters": filters})
    ).hexdigest()
    if format is PaperTradeExportFormat.JSONL:
        content = _jsonl(filters, ordered, query_checksum)
    elif format is PaperTradeExportFormat.CSV:
        content = _csv(ordered)
    else:  # pragma: no cover - guarded by the canonical enum
        raise InvalidPaperSessionError("O formato de exportação é inválido.")
    extension, media_type = _format_metadata(format)
    return PaperTradeExport(
        format=format,
        query_checksum=query_checksum,
        content_checksum=hashlib.sha256(content).hexdigest(),
        filename=f"paper-trade-journal-{query_checksum[:16]}.{extension}",
        media_type=media_type,
        rows_count=len(records),
        content=content,
    )


def _jsonl(
    filters: PaperTradeJournalFilter,
    records: tuple[PaperTradeRecord, ...],
    query_checksum: str,
) -> bytes:
    lines = [
        canonical_json_bytes(
            {
                "kind": "manifest",
                "schema": _SCHEMA,
                "query_checksum": query_checksum,
                "filters": filters,
                "rows_count": len(records),
            }
        )
    ]
    lines.extend(
        canonical_json_bytes(
            {
                "kind": "trade",
                "schema": _SCHEMA,
                "record": record,
            }
        )
        for record in records
    )
    return b"\n".join(lines) + b"\n"


def _csv(records: tuple[PaperTradeRecord, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=_CSV_COLUMNS,
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    for record in records:
        writer.writerow(_csv_row(record))
    return stream.getvalue().encode("utf-8")


def _csv_row(record: PaperTradeRecord) -> dict[str, str | int]:
    trade = record.trade
    entries = trade.entry_executions
    exits = trade.exit_executions
    return {
        "session_id": record.session_id,
        "trade_id": trade.trade_id,
        "sequence": trade.sequence,
        "status": trade.status.value,
        "symbol": record.pair.symbol,
        "base_asset": record.pair.base,
        "quote_asset": record.pair.quote,
        "timeframe": record.timeframe.code,
        "strategy_name": record.strategy.name,
        "strategy_version": record.strategy.version,
        "strategy_parameters_json": canonical_json_bytes(record.strategy.parameters).decode(
            "utf-8"
        ),
        "opened_at": trade.opened_at.isoformat(),
        "last_entry_at": trade.last_entry_at.isoformat(),
        "first_exit_at": _datetime_text(trade.first_exit_at),
        "closed_at": _datetime_text(trade.closed_at),
        "opened_quantity": decimal_text(trade.opened_quantity),
        "closed_quantity": decimal_text(trade.closed_quantity),
        "remaining_quantity": decimal_text(trade.remaining_quantity),
        "average_entry_price": decimal_text(trade.average_entry_price),
        "average_exit_price": _decimal_text(trade.average_exit_price),
        "entry_notional": decimal_text(trade.entry_notional),
        "exit_notional": decimal_text(trade.exit_notional),
        "entry_fees": decimal_text(trade.entry_fees),
        "exit_fees": decimal_text(trade.exit_fees),
        "total_fees": decimal_text(trade.total_fees),
        "entry_slippage_cost": decimal_text(trade.entry_slippage_cost),
        "exit_slippage_cost": decimal_text(trade.exit_slippage_cost),
        "total_slippage_cost": decimal_text(trade.total_slippage_cost),
        "entry_cost_basis": decimal_text(trade.entry_cost_basis),
        "released_cost_basis": decimal_text(trade.released_cost_basis),
        "remaining_cost_basis": decimal_text(trade.remaining_cost_basis),
        "realized_pnl": decimal_text(trade.realized_pnl),
        "unrealized_pnl": decimal_text(trade.unrealized_pnl),
        "net_pnl": decimal_text(trade.net_pnl),
        "mark_price": _decimal_text(trade.mark_price),
        "entry_count": len(entries),
        "exit_count": len(exits),
        "entry_fill_ids": _joined(entries, "fill_id"),
        "exit_fill_ids": _joined(exits, "fill_id"),
        "entry_order_ids": _joined(entries, "order_id"),
        "exit_order_ids": _joined(exits, "order_id"),
        "entry_client_tags": _joined(entries, "client_tag"),
        "exit_client_tags": _joined(exits, "client_tag"),
        "entry_fill_reasons": "|".join(item.fill_reason.value for item in entries),
        "exit_fill_reasons": "|".join(item.fill_reason.value for item in exits),
        "state_id": record.state_id,
        "state_checksum": record.state_checksum,
        "last_candle_open_time": record.last_candle_open_time.isoformat(),
        "replayed_at": record.replayed_at.isoformat(),
    }


def _joined(
    executions: tuple[PaperTradeExecution, ...],
    field_name: str,
) -> str:
    values: list[str] = []
    for execution in executions:
        value = getattr(execution, field_name)
        values.append("" if value is None else str(value))
    return "|".join(values)


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return decimal_text(value)


def _datetime_text(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _record_key(record: PaperTradeRecord) -> tuple[datetime, str, int, str]:
    return (
        record.trade.opened_at,
        record.session_id,
        record.trade.sequence,
        record.trade.trade_id,
    )


def _format_metadata(format: PaperTradeExportFormat) -> tuple[str, str]:
    if format is PaperTradeExportFormat.JSONL:
        return "jsonl", "application/x-ndjson"
    if format is PaperTradeExportFormat.CSV:
        return "csv", "text/csv; charset=utf-8"
    raise ValueError("unsupported export format")
