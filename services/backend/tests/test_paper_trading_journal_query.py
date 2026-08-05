"""Filtering, pagination, and deterministic export regressions for journals."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import OrderSide
from app.paper_trading.domain import paper_session_id
from app.paper_trading.errors import InvalidPaperSessionError
from app.paper_trading.journal import PaperTradeStatus
from app.paper_trading.journal_export import (
    PaperTradeExportFormat,
    PaperTradeJournalExportService,
)
from app.paper_trading.journal_query import (
    PaperTradeJournalFilter,
    PaperTradeJournalReadService,
)
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading import FakeSource, _candle
from tests.test_paper_trading_journal import (
    _intent,
    _journal_config,
    _service,
)


def _populated_repository(
    tmp_path: Path,
) -> tuple[PaperTradingRepository, str]:
    source = FakeSource(
        tuple(
            _candle(index, close)
            for index, close in enumerate(("100", "110", "120", "130", "140", "150"))
        )
    )
    schedule = (
        (0, (_intent(OrderSide.BUY, "2", "entry-a"),)),
        (1, (_intent(OrderSide.SELL, "2", "close-a"),)),
        (2, (_intent(OrderSide.BUY, "0.5", "entry-b"),)),
    )
    service = _service(tmp_path, source, schedule)
    config = _journal_config()
    service.create(config)
    service.run_once(paper_session_id(config))
    service.create(replace(config, initial_capital=Decimal("20000")))
    return PaperTradingRepository(tmp_path), paper_session_id(config)


def test_journal_query_filters_and_paginates_newest_first(tmp_path: Path) -> None:
    repository, session_id = _populated_repository(tmp_path)
    reader = PaperTradeJournalReadService(repository)
    filters = PaperTradeJournalFilter(
        session_id=session_id,
        base_asset="btc",
        quote_asset="usdt",
        timeframe_code="1m",
        strategy_name="paper-journal-test",
        strategy_version="1",
    )

    first = reader.list_trades(filters, page=1, page_size=1)
    second = reader.list_trades(filters, page=2, page_size=1)

    assert first.total == 2
    assert first.total_pages == 2
    assert first.totals.trades_count == 2
    assert first.totals.closed_trades_count == 1
    assert first.totals.open_trades_count == 1
    assert first.items[0].trade.status is PaperTradeStatus.OPEN
    assert second.items[0].trade.status is PaperTradeStatus.CLOSED
    assert first.items[0].trade.opened_at > second.items[0].trade.opened_at
    assert first.totals.total_net_pnl == (
        first.totals.total_realized_pnl + first.totals.total_unrealized_pnl
    )
    assert filters.base_asset == "BTC"
    assert filters.quote_asset == "USDT"

    opened = reader.list_trades(
        replace(filters, status=PaperTradeStatus.OPEN),
        page=1,
        page_size=20,
    )
    closed = reader.list_trades(
        replace(filters, status=PaperTradeStatus.CLOSED),
        page=1,
        page_size=20,
    )
    assert [item.trade.status for item in opened.items] == [PaperTradeStatus.OPEN]
    assert [item.trade.status for item in closed.items] == [PaperTradeStatus.CLOSED]

    open_time = opened.items[0].trade.opened_at
    assert (
        reader.list_trades(
            replace(filters, opened_from=open_time),
            page=1,
            page_size=20,
        ).total
        == 1
    )
    assert (
        reader.list_trades(
            replace(filters, opened_before=open_time),
            page=1,
            page_size=20,
        ).total
        == 1
    )

    closed_at = closed.items[0].trade.closed_at
    assert closed_at is not None
    assert (
        reader.list_trades(
            replace(filters, closed_from=closed_at),
            page=1,
            page_size=20,
        ).total
        == 1
    )
    assert (
        reader.list_trades(
            replace(filters, closed_before=closed_at),
            page=1,
            page_size=20,
        ).total
        == 0
    )


def test_journal_query_skips_pending_sessions_and_applies_config_filters(
    tmp_path: Path,
) -> None:
    repository, session_id = _populated_repository(tmp_path)
    reader = PaperTradeJournalReadService(repository)

    assert (
        reader.list_trades(
            PaperTradeJournalFilter(),
            page=1,
            page_size=20,
        ).total
        == 2
    )
    assert (
        reader.list_trades(
            PaperTradeJournalFilter(session_id=session_id),
            page=1,
            page_size=20,
        ).total
        == 2
    )
    assert (
        reader.list_trades(
            PaperTradeJournalFilter(base_asset="ETH"),
            page=1,
            page_size=20,
        ).total
        == 0
    )
    assert (
        reader.list_trades(
            PaperTradeJournalFilter(strategy_name="another-strategy"),
            page=1,
            page_size=20,
        ).total
        == 0
    )
    assert (
        reader.list_trades(
            PaperTradeJournalFilter(timeframe_code="5m"),
            page=1,
            page_size=20,
        ).total
        == 0
    )


def test_journal_exports_are_lossless_deterministic_and_query_bound(
    tmp_path: Path,
) -> None:
    repository, session_id = _populated_repository(tmp_path)
    exporter = PaperTradeJournalExportService(repository)
    filters = PaperTradeJournalFilter(session_id=session_id)

    jsonl = exporter.export(filters, format=PaperTradeExportFormat.JSONL)
    repeated = exporter.export(filters, format=PaperTradeExportFormat.JSONL)
    csv_export = exporter.export(filters, format=PaperTradeExportFormat.CSV)

    assert repeated == jsonl
    assert jsonl.rows_count == 2
    assert csv_export.rows_count == 2
    assert jsonl.query_checksum == csv_export.query_checksum
    assert hashlib.sha256(jsonl.content).hexdigest() == jsonl.content_checksum
    assert hashlib.sha256(csv_export.content).hexdigest() == csv_export.content_checksum
    assert jsonl.filename.endswith(".jsonl")
    assert csv_export.filename.endswith(".csv")

    lines = [json.loads(line) for line in jsonl.content.decode("utf-8").splitlines()]
    assert lines[0]["kind"] == "manifest"
    assert lines[0]["rows_count"] == 2
    assert lines[0]["query_checksum"] == jsonl.query_checksum
    assert [line["record"]["trade"]["status"] for line in lines[1:]] == [
        "OPEN",
        "CLOSED",
    ]
    assert isinstance(lines[1]["record"]["trade"]["opened_quantity"], str)

    rows = list(csv.DictReader(io.StringIO(csv_export.content.decode("utf-8"))))
    assert len(rows) == 2
    assert [row["status"] for row in rows] == ["OPEN", "CLOSED"]
    assert rows[0]["entry_client_tags"] == "entry-b"
    assert rows[1]["entry_client_tags"] == "entry-a"
    assert rows[1]["exit_client_tags"] == "close-a"
    assert rows[0]["opened_quantity"] == "0.5"
    assert rows[0]["state_checksum"] == lines[1]["record"]["state_checksum"]

    closed = exporter.export(
        replace(filters, status=PaperTradeStatus.CLOSED),
        format=PaperTradeExportFormat.JSONL,
    )
    assert closed.rows_count == 1
    assert closed.query_checksum != jsonl.query_checksum
    assert closed.content_checksum != jsonl.content_checksum


def test_journal_filter_and_pagination_reject_noncanonical_inputs(
    tmp_path: Path,
) -> None:
    repository, _ = _populated_repository(tmp_path)
    reader = PaperTradeJournalReadService(repository)
    opened = datetime(2026, 8, 1, tzinfo=UTC)

    with pytest.raises(InvalidPaperSessionError):
        PaperTradeJournalFilter(session_id="not-a-session")
    with pytest.raises(InvalidPaperSessionError):
        PaperTradeJournalFilter(base_asset="B/T/C")
    with pytest.raises(InvalidPaperSessionError):
        PaperTradeJournalFilter(opened_from=opened, opened_before=opened)
    with pytest.raises(InvalidPaperSessionError):
        PaperTradeJournalFilter(opened_from=datetime(2026, 8, 1))
    with pytest.raises(InvalidPaperSessionError):
        reader.list_trades(PaperTradeJournalFilter(), page=0, page_size=20)
    with pytest.raises(InvalidPaperSessionError):
        reader.list_trades(PaperTradeJournalFilter(), page=1, page_size=101)

    page = reader.list_trades(
        PaperTradeJournalFilter(
            opened_from=opened - timedelta(minutes=1),
            opened_before=opened + timedelta(days=1),
        ),
        page=100,
        page_size=20,
    )
    assert page.items == ()
    assert page.total == 2
