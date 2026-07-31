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
from app.market_data.http import PublicMarketHttpClient
from app.market_data.services import default_local_services
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

        assert pair is not None
        timeframe = get_timeframe(args.timeframe)
        if args.command == "fetch":
            result = await history_service.ingest(
                pair,
                timeframe,
                DataRange(args.start, args.end),
                dry_run=args.dry_run,
            )
            _print(
                {
                    "run_id": result.run_id,
                    "fetched": result.fetched_count,
                    "stored": result.stored_count,
                    "duplicates": result.duplicate_count,
                    "requests": result.request_count,
                    "quality_valid": result.quality.is_valid,
                    "dry_run": result.dry_run,
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


if __name__ == "__main__":
    raise SystemExit(main())
