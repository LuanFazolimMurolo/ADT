"""Local operational CLI for ADT backend tasks."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import TextIO

import httpx

from app.core.config import Settings, settings
from app.domain.errors import DomainError
from app.market_data.binance import BINANCE_MARKET_DATA_BASE_URL, BinanceSpotAdapter
from app.market_data.domain import DataRange, Exchange, Instrument, MarketType, TradingPair
from app.market_data.errors import MarketDataInconsistencyError
from app.market_data.http import PublicMarketHttpClient
from app.market_data.jobs import MarketJobCatalog
from app.market_data.orchestration import BackfillExecutor
from app.market_data.planning import BackfillPlan, BackfillResult, MarketDataPlanner
from app.market_data.services import default_local_services
from app.market_data.storage import ParquetCandleStore
from app.market_data.timeframes import TIMEFRAMES, get_timeframe

EXIT_OK = 0
EXIT_INVALID_ARGUMENTS = 2
EXIT_DOMAIN_FAILURE = 3
EXIT_UNEXPECTED_FAILURE = 4


def build_parser() -> argparse.ArgumentParser:
    """Build the stable command surface without performing I/O."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    root = parser.add_subparsers(dest="group", required=True)
    market = root.add_parser("market-data", help="Operate local historical market data.")
    commands = market.add_subparsers(dest="command", required=True)

    instruments = commands.add_parser("instruments", help="List Binance Spot instruments.")
    _add_market_identity(instruments, include_symbol=False)

    fetch = commands.add_parser("fetch", help="Fetch and optionally persist candles.")
    _add_market_identity(fetch)
    _add_time_range(fetch)
    fetch.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)
    fetch.add_argument("--dry-run", action="store_true")

    inspect = commands.add_parser("inspect", help="Summarize one local dataset.")
    _add_market_identity(inspect)
    inspect.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)

    verify = commands.add_parser("verify", help="Verify one local dataset interval.")
    _add_market_identity(verify)
    _add_time_range(verify)
    verify.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)

    backfill = commands.add_parser("backfill", help="Plan or execute bounded backfills.")
    backfill_commands = backfill.add_subparsers(dest="backfill_command", required=True)
    for name in ("plan", "run"):
        command = backfill_commands.add_parser(name)
        _add_market_identity(command)
        _add_time_range(command)
        command.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)
        if name == "run":
            command.add_argument("--yes", action="store_true")
            command.add_argument("--dry-run", action="store_true")
    resume = backfill_commands.add_parser("resume")
    resume.add_argument("--job-id", required=True)
    resume.add_argument("--symbol", required=True)
    for name in ("status", "pause", "cancel"):
        lifecycle = backfill_commands.add_parser(name)
        lifecycle.add_argument("--job-id", required=True)

    update = commands.add_parser("update", help="Incrementally update one dataset.")
    _add_market_identity(update)
    update.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)
    update.add_argument("--start", type=_utc_datetime)
    update.add_argument("--dry-run", action="store_true")
    update.add_argument("--yes", action="store_true")

    gaps = commands.add_parser("gaps", help="Discover local missing intervals.")
    _add_market_identity(gaps)
    _add_time_range(gaps)
    gaps.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)

    repair = commands.add_parser("repair", help="Explicitly refetch missing intervals.")
    _add_market_identity(repair)
    _add_time_range(repair)
    repair.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)
    repair.add_argument("--dry-run", action="store_true")
    repair.add_argument("--yes", action="store_true")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    app_settings: Settings = settings,
    transport: httpx.AsyncBaseTransport | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Parse, execute and return a predictable process exit code."""
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(
            _run_market_command(
                args,
                app_settings=app_settings,
                transport=transport,
                stdout=stdout,
            )
        )
    except DomainError as error:
        print(json.dumps({"error": error.code, "message": error.message}), file=stderr)
        return EXIT_DOMAIN_FAILURE
    except (OSError, ValueError):
        print(
            json.dumps(
                {
                    "error": "operation_failed",
                    "message": "A operação local não pôde ser concluída.",
                }
            ),
            file=stderr,
        )
        return EXIT_UNEXPECTED_FAILURE


async def _run_market_command(
    args: argparse.Namespace,
    *,
    app_settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
    stdout: TextIO,
) -> int:
    pair = TradingPair.parse(args.symbol) if hasattr(args, "symbol") else None

    def clock() -> datetime:
        return datetime.now(UTC)

    client = PublicMarketHttpClient(
        base_url=BINANCE_MARKET_DATA_BASE_URL,
        user_agent=app_settings.market_user_agent,
        timeout_seconds=app_settings.market_http_timeout,
        max_connections=app_settings.market_http_max_connections,
        retries=app_settings.market_http_retries,
        max_retry_after_seconds=app_settings.market_http_max_retry_after,
        transport=transport,
    )
    async with client:
        adapter = BinanceSpotAdapter(
            client,
            allow_open_candles=app_settings.market_allow_open_candles,
            now=clock,
        )
        catalog_service, history_service = default_local_services(
            app_settings.data_dir,
            adapter,
            max_fetch_candles=app_settings.market_max_fetch_candles,
            clock=clock,
            lock_timeout_seconds=app_settings.market_job_lock_timeout,
            lock_stale_after_seconds=app_settings.market_job_stale_after,
        )
        store = ParquetCandleStore(app_settings.data_dir)
        jobs = MarketJobCatalog(
            app_settings.data_dir,
            clock=clock,
            stale_after_seconds=app_settings.market_job_stale_after,
        )
        if args.command in {"backfill", "update", "repair"}:
            jobs.recover_abandoned()
        planner = MarketDataPlanner(
            adapter_request_limit=adapter.limits.max_candles_per_request,
            max_fetch_candles=app_settings.market_max_fetch_candles,
            chunk_candles=app_settings.market_backfill_chunk_candles,
            max_total_candles=app_settings.market_backfill_max_total_candles,
            max_chunks=app_settings.market_job_max_chunks,
            clock=clock,
        )
        executor = BackfillExecutor(
            history=history_service,
            jobs=jobs,
            data_dir=app_settings.data_dir,
            lock_timeout_seconds=app_settings.market_job_lock_timeout,
            lock_stale_after_seconds=app_settings.market_job_stale_after,
        )
        if args.command == "instruments":
            instruments = await catalog_service.list()
            _print(
                {
                    "count": len(instruments),
                    "items": [
                        {
                            "symbol": item.symbol,
                            "native_symbol": item.native_symbol,
                            "active": item.active,
                        }
                        for item in instruments[:20]
                    ],
                    "truncated": len(instruments) > 20,
                },
                stdout,
            )
            return EXIT_OK

        if args.command == "backfill" and args.backfill_command in {
            "status",
            "pause",
            "cancel",
        }:
            if args.backfill_command == "pause":
                jobs.pause(args.job_id)
            if args.backfill_command == "cancel":
                jobs.cancel(args.job_id)
            _print_job(jobs, args.job_id, stdout)
            return EXIT_OK
        if args.command == "backfill" and args.backfill_command == "resume":
            assert pair is not None
            resume_result = await executor.resume(args.job_id, pair)
            _print_job_result(resume_result, stdout)
            return EXIT_OK

        assert pair is not None
        timeframe = get_timeframe(args.timeframe)
        if args.command == "fetch":
            ingestion_result = await history_service.ingest(
                pair,
                timeframe,
                DataRange(args.start, args.end),
                dry_run=args.dry_run,
            )
            _print(
                {
                    "run_id": ingestion_result.run_id,
                    "fetched": ingestion_result.fetched_count,
                    "stored": ingestion_result.stored_count,
                    "duplicates": ingestion_result.duplicate_count,
                    "requests": ingestion_result.request_count,
                    "quality_valid": ingestion_result.quality.is_valid,
                    "dry_run": ingestion_result.dry_run,
                },
                stdout,
            )
            return EXIT_OK

        instrument = _local_instrument(pair)
        if args.command == "inspect":
            metadata = history_service.inspect(instrument, timeframe)
            _print(
                {
                    "symbol": metadata.symbol,
                    "timeframe": metadata.timeframe,
                    "count": metadata.candle_count,
                    "first_open_time": metadata.first_open_time,
                    "last_open_time": metadata.last_open_time,
                    "location": metadata.location,
                    "version": metadata.version,
                },
                stdout,
            )
            return EXIT_OK

        if args.command == "verify":
            report = history_service.verify(
                instrument,
                timeframe,
                DataRange(args.start, args.end),
            )
            _print(
                {
                    "valid": report.is_valid,
                    "checked": report.checked_count,
                    "expected": report.expected_count,
                    "issues": [
                        {"code": issue.code, "severity": issue.severity.value}
                        for issue in report.issues[:50]
                    ],
                    "truncated": len(report.issues) > 50,
                },
                stdout,
            )
            return EXIT_OK if report.is_valid else EXIT_DOMAIN_FAILURE

        instrument = _local_instrument(pair)
        key = (
            f"{instrument.exchange.value}:{instrument.market_type.value}:"
            f"{instrument.symbol}:{timeframe.code}"
        )
        if args.command == "backfill":
            plan = planner.backfill(key, timeframe, DataRange(args.start, args.end))
            if args.backfill_command == "plan" or args.dry_run:
                _print_plan(plan, stdout)
                return EXIT_OK
            _require_confirmation(args.yes, len(plan.chunks))
            backfill_result = await executor.run(plan, pair)
            _print_job_result(backfill_result, stdout)
            return EXIT_OK
        if args.command == "update":
            with history_service.dataset_lease(instrument, timeframe):
                incremental = planner.incremental(
                    store,
                    instrument,
                    timeframe,
                    now=clock(),
                    overlap_candles=app_settings.market_incremental_overlap_candles,
                    start=args.start,
                )
            if incremental.backfill is None:
                _print({"action": "NOOP"}, stdout)
                return EXIT_OK
            if args.dry_run:
                _print_plan(incremental.backfill, stdout)
                return EXIT_OK
            _require_confirmation(args.yes, len(incremental.backfill.chunks))
            update_result = await executor.run(incremental.backfill, pair)
            _print_job_result(update_result, stdout)
            return EXIT_OK
        if args.command in {"gaps", "repair"}:
            with history_service.dataset_lease(instrument, timeframe):
                repair_plan = planner.gaps(
                    store,
                    instrument,
                    timeframe,
                    DataRange(args.start, args.end),
                )
            if args.command == "gaps" or args.dry_run:
                missing_candles = (
                    repair_plan.backfill.expected_candles if repair_plan.backfill is not None else 0
                )
                _print(
                    {
                        "gap_count": len(repair_plan.gap_ranges),
                        "missing_candles": missing_candles,
                        "ranges": [
                            {"start": gap.start.isoformat(), "end": gap.end.isoformat()}
                            for gap in repair_plan.gap_ranges[:50]
                        ],
                        "truncated": len(repair_plan.gap_ranges) > 50,
                    },
                    stdout,
                )
                return EXIT_OK
            if not repair_plan.gap_ranges:
                _print({"action": "NOOP", "gap_count": 0}, stdout)
                return EXIT_OK
            assert repair_plan.backfill is not None
            _require_confirmation(args.yes, len(repair_plan.backfill.chunks))
            repair_result = await executor.run(repair_plan.backfill, pair)
            _print_job_result(repair_result, stdout)
            return EXIT_OK

    raise ValueError("unknown command")


def _add_market_identity(
    parser: argparse.ArgumentParser,
    *,
    include_symbol: bool = True,
) -> None:
    parser.add_argument("--exchange", choices=["binance"], default="binance")
    parser.add_argument("--market", choices=["spot"], default="spot")
    if include_symbol:
        parser.add_argument("--symbol", required=True, help="Canonical BASE/QUOTE symbol.")


def _add_time_range(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", required=True, type=_utc_datetime)
    parser.add_argument("--end", required=True, type=_utc_datetime)


def _utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("use an ISO-8601 UTC datetime") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("datetime must be timezone-aware UTC")
    return parsed.astimezone(UTC)


def _local_instrument(pair: TradingPair) -> Instrument:
    return Instrument(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=pair,
        native_symbol=f"{pair.base}{pair.quote}",
        active=True,
    )


def _print(payload: dict[str, object], stdout: TextIO) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)


def _print_plan(plan: BackfillPlan, stdout: TextIO) -> None:
    _print(
        {
            "job_id": plan.job_id,
            "job_type": plan.job_type.value,
            "chunks": len(plan.chunks),
            "expected_candles": plan.expected_candles,
            "start": plan.data_range.start.isoformat(),
            "end": plan.data_range.end.isoformat(),
        },
        stdout,
    )


def _print_job_result(result: BackfillResult, stdout: TextIO) -> None:
    _print(
        {
            "job_id": result.job_id,
            "status": result.status.value,
            "chunks_completed": result.chunks_completed,
            "total_chunks": result.total_chunks,
            "fetched": result.fetched_count,
            "stored": result.stored_count,
            "duplicates": result.duplicate_count,
            "requests": result.request_count,
        },
        stdout,
    )


def _print_job(jobs: MarketJobCatalog, job_id: str, stdout: TextIO) -> None:
    progress = jobs.progress(job_id)
    _print(
        {
            "job_id": progress.job_id,
            "status": progress.status.value,
            "chunks_completed": progress.chunks_completed,
            "total_chunks": progress.total_chunks,
            "next_start": progress.next_start.isoformat(),
            "fetched": progress.fetched_count,
            "stored": progress.stored_count,
            "duplicates": progress.duplicate_count,
        },
        stdout,
    )


def _require_confirmation(confirmed: bool, chunk_count: int) -> None:
    if chunk_count > 1 and not confirmed:
        raise MarketDataInconsistencyError("Backfill grande exige confirmação explícita --yes.")


if __name__ == "__main__":
    raise SystemExit(main())
