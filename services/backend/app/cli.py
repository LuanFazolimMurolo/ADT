"""Local operational CLI for ADT backend tasks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import httpx

from app.backtesting.commands import configure_backtest_parser, run_backtest_command
from app.core.config import (
    MarketDataSettings,
    Settings,
    get_market_data_settings,
    get_settings,
)
from app.domain.errors import DomainError
from app.market_data.advanced_quality import (
    AdvancedMarketDataQualityScanner,
    load_quality_baseline,
    save_quality_baseline,
)
from app.market_data.asset_catalog import AssetMarketService
from app.market_data.binance import BINANCE_MARKET_DATA_BASE_URL, BinanceSpotAdapter
from app.market_data.continuous import (
    ContinuousCollectionPolicy,
    ContinuousCollectionRunner,
    ContinuousCollectionService,
    ContinuousCollectionState,
    ContinuousCollectionStateStore,
    ContinuousCollectionTarget,
    collection_target_from_text,
    validate_continuous_collection_targets,
)
from app.market_data.datasets import (
    DatasetIdentity,
    DatasetKind,
    DatasetManifest,
    GapPolicy,
    QualityScanMode,
    QualityScanPlan,
    QualityScanScope,
    ResamplingPlan,
)
from app.market_data.derived import DerivedDatasetService, DerivedDatasetStore
from app.market_data.domain import DataRange, Exchange, Instrument, MarketType, TradingPair
from app.market_data.errors import MarketDataInconsistencyError
from app.market_data.filesystem import ensure_safe_path
from app.market_data.http import PublicMarketHttpClient
from app.market_data.jobs import MarketJobCatalog
from app.market_data.locks import DatasetLockManager
from app.market_data.operation_worker_runtime import (
    MarketOperationWorkerLoopResult,
    run_market_operation_worker_loop,
    run_market_operation_worker_once,
)
from app.market_data.operations import MarketOperationSnapshot
from app.market_data.orchestration import BackfillExecutor
from app.market_data.planning import BackfillPlan, BackfillResult, MarketDataPlanner
from app.market_data.services import default_local_services
from app.market_data.snapshots import DatasetSnapshotService
from app.market_data.storage import ParquetCandleStore
from app.market_data.timeframes import TIMEFRAMES, get_timeframe
from app.paper_trading.commands import (
    configure_paper_trading_parser,
    run_paper_trading_command,
)

EXIT_OK = 0
EXIT_INVALID_ARGUMENTS = 2
EXIT_DOMAIN_FAILURE = 3
EXIT_UNEXPECTED_FAILURE = 4


def build_parser() -> argparse.ArgumentParser:
    """Build the stable command surface without performing I/O."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    root = parser.add_subparsers(dest="group", required=True)
    backtest = root.add_parser("backtest", help="Run deterministic local snapshot backtests.")
    configure_backtest_parser(backtest)
    paper = root.add_parser("paper-trading", help="Run deterministic local paper-trading sessions.")
    configure_paper_trading_parser(paper)

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

    integrity = commands.add_parser(
        "integrity",
        help="Manage authoritative RAW partition integrity metadata.",
    )
    integrity_commands = integrity.add_subparsers(dest="integrity_command", required=True)
    integrity_backfill = integrity_commands.add_parser(
        "backfill",
        help="Backfill one explicitly selected RAW partition manifest.",
    )
    _add_market_identity(integrity_backfill)
    integrity_backfill.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)

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

    collect = commands.add_parser(
        "collect",
        help="Continuously maintain bounded RAW candle datasets.",
    )
    collect_commands = collect.add_subparsers(dest="collect_command", required=True)
    for name in ("run-once", "loop"):
        command = collect_commands.add_parser(name)
        command.add_argument(
            "--target",
            action="append",
            required=True,
            help="Repeatable BASE/QUOTE:TIMEFRAME target.",
        )
        command.add_argument("--bootstrap-candles", type=int)
        command.add_argument("--yes", action="store_true")
        if name == "loop":
            command.add_argument("--interval-seconds", type=int)
            command.add_argument("--max-cycles", type=int)
    collect_commands.add_parser("status")

    worker = commands.add_parser(
        "worker",
        help="Execute queued PostgreSQL-backed market-data operations.",
    )
    worker_commands = worker.add_subparsers(
        dest="worker_command",
        required=True,
    )
    worker_commands.add_parser(
        "run-once",
        help="Claim and process at most one queued market-data operation.",
    )
    worker_loop = worker_commands.add_parser(
        "loop",
        help="Continuously poll queued market-data operations.",
    )
    worker_loop.add_argument(
        "--interval-seconds",
        type=float,
        required=True,
    )
    worker_loop.add_argument("--max-cycles", type=int)

    quality = commands.add_parser("quality", help="Audit persisted datasets.")
    quality_commands = quality.add_subparsers(dest="quality_command", required=True)
    for name in ("scan", "report"):
        command = quality_commands.add_parser(name)
        _add_market_identity(command)
        command.add_argument("--timeframe", choices=tuple(TIMEFRAMES), required=True)
        command.add_argument("--dataset-kind", choices=("RAW", "DERIVED"), default="RAW")
        command.add_argument("--source-timeframe", choices=tuple(TIMEFRAMES))
        command.add_argument(
            "--gap-policy",
            choices=tuple(item.value for item in GapPolicy),
            default="STRICT",
        )
        command.add_argument("--mode", choices=("FULL", "INCREMENTAL"), default="FULL")
        command.add_argument("--scope", choices=("FULL_DATASET", "RANGE"))
        command.add_argument("--baseline", type=Path)
        command.add_argument("--start", type=_utc_datetime)
        command.add_argument("--end", type=_utc_datetime)

    resample = commands.add_parser("resample", help="Build deterministic derived datasets.")
    resample_commands = resample.add_subparsers(dest="resample_command", required=True)
    for name in ("plan", "run", "verify", "rebuild"):
        command = resample_commands.add_parser(name)
        _add_resample_arguments(command)
        if name in {"run", "rebuild"}:
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--yes", action="store_true")

    snapshot = commands.add_parser("snapshot", help="Manage immutable dataset snapshots.")
    snapshot_commands = snapshot.add_subparsers(dest="snapshot_command", required=True)
    create = snapshot_commands.add_parser("create")
    _add_resample_arguments(create)
    create.add_argument("--dry-run", action="store_true")
    for name in ("inspect", "verify"):
        command = snapshot_commands.add_parser(name)
        command.add_argument("--snapshot-id", required=True)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    app_settings: MarketDataSettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Parse, execute and return a predictable process exit code."""
    args = build_parser().parse_args(argv)
    try:
        if args.group == "market-data" and args.command == "worker":
            worker_settings = app_settings if isinstance(app_settings, Settings) else get_settings()
            return asyncio.run(
                _run_market_operation_worker_command(
                    args,
                    app_settings=worker_settings,
                    transport=transport,
                    stdout=stdout,
                )
            )

        resolved_settings = app_settings or get_market_data_settings()
        if args.group == "backtest":
            return run_backtest_command(
                args,
                settings=resolved_settings,
                stdout=stdout,
            )
        if args.group == "paper-trading":
            return run_paper_trading_command(
                args,
                settings=resolved_settings,
                stdout=stdout,
            )
        return asyncio.run(
            _run_market_command(
                args,
                app_settings=resolved_settings,
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


async def _run_market_operation_worker_command(
    args: argparse.Namespace,
    *,
    app_settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
    stdout: TextIO,
) -> int:
    """Execute PostgreSQL-backed market-operation worker commands."""
    if args.worker_command == "run-once":
        operation = await run_market_operation_worker_once(
            app_settings,
            transport=transport,
        )
        _print(
            _market_operation_worker_payload(operation),
            stdout,
        )
        return EXIT_OK

    if args.worker_command == "loop":
        result = await run_market_operation_worker_loop(
            app_settings,
            interval_seconds=args.interval_seconds,
            max_cycles=args.max_cycles,
            transport=transport,
        )
        _print(
            _market_operation_worker_loop_payload(result),
            stdout,
        )
        return EXIT_OK

    raise ValueError("unknown market-operation worker command")


async def _run_market_command(
    args: argparse.Namespace,
    *,
    app_settings: MarketDataSettings,
    transport: httpx.AsyncBaseTransport | None,
    stdout: TextIO,
) -> int:
    pair = TradingPair.parse(args.symbol) if hasattr(args, "symbol") else None
    collection_targets: tuple[ContinuousCollectionTarget, ...] | None = None
    collection_policy: ContinuousCollectionPolicy | None = None
    collection_max_cycles: int | None = None
    if args.command == "collect":
        collection_state_store = ContinuousCollectionStateStore(app_settings.data_dir)
        if args.collect_command == "status":
            current_state = collection_state_store.read()
            _print(
                {"state": None}
                if current_state is None
                else _collection_state_payload(current_state),
                stdout,
            )
            return EXIT_OK
        if not args.yes:
            raise MarketDataInconsistencyError(
                "A coleta contínua exige confirmação explícita --yes."
            )
        bootstrap_candles = (
            args.bootstrap_candles
            if args.bootstrap_candles is not None
            else app_settings.market_continuous_bootstrap_candles
        )
        raw_targets = tuple(
            collection_target_from_text(
                value,
                bootstrap_candles=bootstrap_candles,
            )
            for value in args.target
        )
        collection_targets = validate_continuous_collection_targets(
            tuple(sorted(raw_targets, key=lambda item: item.key)),
            max_targets=app_settings.market_continuous_max_targets,
        )
        interval_seconds = (
            args.interval_seconds
            if args.collect_command == "loop" and args.interval_seconds is not None
            else app_settings.market_continuous_interval_seconds
        )
        collection_policy = ContinuousCollectionPolicy(
            interval_seconds=interval_seconds,
            overlap_candles=app_settings.market_incremental_overlap_candles,
            max_targets=app_settings.market_continuous_max_targets,
        )
        collection_max_cycles = 1 if args.collect_command == "run-once" else args.max_cycles

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
        asset_market_service = AssetMarketService(
            adapter,
            catalog_ttl_seconds=app_settings.market_asset_catalog_ttl_seconds,
            max_instruments=app_settings.market_asset_catalog_max_instruments,
            clock=clock,
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
        collection_state_store = ContinuousCollectionStateStore(app_settings.data_dir)
        locks = DatasetLockManager(
            app_settings.data_dir,
            timeout_seconds=app_settings.market_job_lock_timeout,
            stale_after_seconds=app_settings.market_job_stale_after,
            clock=clock,
        )
        derived_store = DerivedDatasetStore(
            app_settings.data_dir,
            directory=app_settings.market_derived_dir,
            manifest_schema_version=app_settings.market_manifest_schema_version,
        )
        derived_service = DerivedDatasetService(
            raw_store=store,
            raw_catalog=history_service.catalog,
            derived_store=derived_store,
            lock_manager=locks,
            max_source_candles=app_settings.market_resample_max_source_candles,
            max_groups=app_settings.market_resample_max_groups,
            clock=clock,
        )
        snapshot_service = DatasetSnapshotService(
            data_dir=app_settings.data_dir,
            derived_store=derived_store,
            derived_service=derived_service,
            lock_manager=locks,
            max_partitions=app_settings.market_snapshot_max_partitions,
            clock=clock,
        )
        if args.command in {"resample", "snapshot"}:
            derived_service.recover()
        if args.command == "snapshot" and args.snapshot_command in {"inspect", "verify"}:
            snapshot = (
                snapshot_service.verify(args.snapshot_id)
                if args.snapshot_command == "verify"
                else snapshot_service.inspect(args.snapshot_id)
            )
            _print(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "dataset_key": snapshot.dataset_key,
                    "dataset_version": snapshot.dataset_version,
                    "checksum": snapshot.checksum,
                    "partitions": len(snapshot.partitions),
                    "start": snapshot.data_range.start.isoformat(),
                    "end": snapshot.data_range.end.isoformat(),
                },
                stdout,
            )
            return EXIT_OK
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

        if args.command == "collect":
            if collection_targets is None or collection_policy is None:
                raise MarketDataInconsistencyError("A configuração da coleta contínua é inválida.")
            collection_service = ContinuousCollectionService(
                instruments=asset_market_service,
                history=history_service,
                planner=planner,
                executor=executor,
                store=store,
                policy=collection_policy,
                clock=clock,
            )
            runner = ContinuousCollectionRunner(
                service=collection_service,
                state_store=collection_state_store,
                lock_manager=locks,
                clock=clock,
                startup_hook=jobs.recover_abandoned,
            )
            collection_state = await runner.run(
                collection_targets,
                max_cycles=collection_max_cycles,
            )
            _print(_collection_state_payload(collection_state), stdout)
            return EXIT_OK

        assert pair is not None
        instrument = _local_instrument(pair)
        if args.command == "integrity" and args.integrity_command == "backfill":
            integrity_result = history_service.backfill_partition_integrity(
                instrument,
                get_timeframe(args.timeframe),
            )
            _print(
                {
                    "action": integrity_result.action,
                    "dataset_key": integrity_result.metadata.key,
                    "dataset_version": integrity_result.metadata.version,
                    "version_algorithm": integrity_result.metadata.version_algorithm,
                    "partitions": integrity_result.partition_count,
                },
                stdout,
            )
            return EXIT_OK
        if args.command == "quality":
            quality_timeframe = get_timeframe(args.timeframe)
            quality_range = (
                DataRange(args.start, args.end)
                if args.start is not None and args.end is not None
                else None
            )
            if (args.start is None) != (args.end is None):
                raise MarketDataInconsistencyError("--start e --end devem ser usados juntos.")
            identity = DatasetIdentity(
                instrument.exchange,
                instrument.market_type,
                instrument.symbol,
                quality_timeframe.code,
                DatasetKind(args.dataset_kind),
                ("canonical_parquet" if args.dataset_kind == "RAW" else "pending-derived-plan"),
                "source_native" if args.dataset_kind == "RAW" else args.gap_policy,
                app_settings.market_manifest_schema_version,
            )
            derived_quality_plan = None
            if args.dataset_kind == "DERIVED":
                if args.source_timeframe is None or quality_range is None:
                    raise MarketDataInconsistencyError(
                        "Quality DERIVED exige --source-timeframe, --start e --end."
                    )
                derived_quality_plan = derived_service.plan(
                    instrument,
                    args.source_timeframe,
                    args.timeframe,
                    quality_range,
                    gap_policy=GapPolicy(args.gap_policy),
                )
                identity = derived_quality_plan.target
            scope = (
                QualityScanScope(args.scope)
                if args.scope
                else (
                    QualityScanScope.FULL_DATASET
                    if args.dataset_kind == "DERIVED"
                    else (
                        QualityScanScope.RANGE if quality_range else QualityScanScope.FULL_DATASET
                    )
                )
            )
            baseline_path = (
                ensure_safe_path(
                    store.root,
                    args.baseline if args.baseline.is_absolute() else store.root / args.baseline,
                )
                if args.baseline
                else _quality_baseline_path(
                    store.root,
                    identity.key,
                    scope,
                )
            )
            baseline = None
            if args.mode == "INCREMENTAL":
                if not baseline_path.exists():
                    raise MarketDataInconsistencyError(
                        "Quality INCREMENTAL exige baseline FULL existente."
                    )
                baseline = load_quality_baseline(baseline_path, store.root)
            scanner = AdvancedMarketDataQualityScanner(
                store=store,
                catalog=history_service.catalog,
                max_issues=app_settings.market_quality_max_issues,
                clock=clock,
                derived_service=derived_service,
            )
            quality_plan = QualityScanPlan(
                identity,
                QualityScanMode(args.mode),
                quality_range,
                scope=scope,
                baseline=baseline,
                resampling_plan=derived_quality_plan,
            )
            if args.dataset_kind == "RAW":
                with history_service.dataset_lease(instrument, quality_timeframe):
                    scan = scanner.scan(quality_plan)
                    if args.mode == "FULL" and scan.baseline is not None:
                        save_quality_baseline(baseline_path, scan.baseline, store.root)
            else:
                scan = scanner.scan(quality_plan)
            _print(
                {
                    "mode": scan.plan.mode.value,
                    "valid": scan.is_valid,
                    "observed": scan.coverage.observed_count,
                    "expected": scan.coverage.expected_count,
                    "partitions": len(scan.partitions),
                    "checksum": scan.logical_checksum,
                    "scope": scan.effective_scope.value,
                    "baseline_used": scan.baseline_used,
                    "baseline": (
                        baseline_path.relative_to(store.root).as_posix()
                        if scan.baseline is not None
                        else None
                    ),
                    "issues": [
                        {
                            "code": issue.code,
                            "severity": issue.severity,
                            "category": issue.category.value,
                        }
                        for issue in scan.issues
                    ],
                },
                stdout,
            )
            return EXIT_OK if scan.is_valid else EXIT_DOMAIN_FAILURE

        if args.command in {"resample", "snapshot"}:
            derived_plan = derived_service.plan(
                instrument,
                args.source_timeframe,
                args.target_timeframe,
                DataRange(args.start, args.end),
                gap_policy=GapPolicy(args.gap_policy),
            )
            if args.command == "resample" and args.resample_command == "plan":
                _print_resampling_plan(derived_plan, stdout)
                return EXIT_OK
            if args.command == "resample" and args.resample_command == "verify":
                manifest = derived_service.verify(derived_plan)
                _print_manifest(manifest, stdout)
                return EXIT_OK if manifest.state.value == "COMPLETE" else EXIT_DOMAIN_FAILURE
            if args.command == "snapshot":
                if args.dry_run:
                    _print_resampling_plan(derived_plan, stdout)
                    return EXIT_OK
                snapshot = snapshot_service.create(
                    derived_plan,
                    DataRange(args.start, args.end),
                )
                _print(
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "dataset_key": snapshot.dataset_key,
                        "dataset_version": snapshot.dataset_version,
                        "checksum": snapshot.checksum,
                        "partitions": len(snapshot.partitions),
                    },
                    stdout,
                )
                return EXIT_OK
            if derived_plan.expected_groups > 1 and not args.yes and not args.dry_run:
                raise MarketDataInconsistencyError(
                    "Materialização grande exige confirmação explícita --yes."
                )
            result = (
                derived_service.materialize_incremental(derived_plan)
                if args.resample_command == "run" and not args.dry_run
                else derived_service.materialize(derived_plan, dry_run=args.dry_run)
            )
            _print(
                {
                    "action": result.action.value,
                    "dataset_key": derived_plan.target.key,
                    "source_candles": result.source_count,
                    "groups": result.materialized_count,
                    "skipped_groups": len(result.skipped_ranges),
                    "checksum": result.checksum,
                    "dry_run": args.dry_run,
                },
                stdout,
            )
            return EXIT_OK

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


def _collection_state_payload(state: ContinuousCollectionState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "cycle_index": state.cycle_index,
        "cycle_id": state.cycle_id,
        "status": state.status.value,
        "policy": {
            "interval_seconds": state.policy.interval_seconds,
            "overlap_candles": state.policy.overlap_candles,
            "max_targets": state.policy.max_targets,
        },
        "started_at": state.started_at.isoformat(),
        "finished_at": state.finished_at.isoformat(),
        "next_cycle_at": state.next_cycle_at.isoformat(),
        "checksum": state.checksum,
        "results": [
            {
                "target": result.target.key,
                "status": result.status.value,
                "started_at": result.started_at.isoformat(),
                "finished_at": result.finished_at.isoformat(),
                "latest_closed_end": result.latest_closed_end.isoformat(),
                "job_id": result.job_id,
                "fetched": result.fetched_count,
                "stored": result.stored_count,
                "duplicates": result.duplicate_count,
                "requests": result.request_count,
                "error_code": result.error_code,
            }
            for result in state.results
        ],
    }


def _market_operation_worker_loop_payload(
    result: MarketOperationWorkerLoopResult,
) -> dict[str, object]:
    """Serialize only the bounded runner summary."""
    return {
        "status": "COMPLETED",
        "cycles_completed": result.cycles_completed,
        "operations_processed": result.operations_processed,
        "idle_cycles": result.idle_cycles,
        "last_operation_id": (
            str(result.last_operation_id) if result.last_operation_id is not None else None
        ),
        "last_state": (result.last_state.value if result.last_state is not None else None),
    }


def _market_operation_worker_payload(
    operation: MarketOperationSnapshot | None,
) -> dict[str, object]:
    """Serialize only operational identifiers and sanitized durable state."""
    if operation is None:
        return {
            "status": "IDLE",
            "operation": None,
        }

    result: dict[str, object] | None = None
    if operation.result is not None:
        result = {
            "dataset_version": operation.result.dataset_version,
            "dataset_checksum": operation.result.dataset_checksum,
            "completed_at": operation.result.completed_at.isoformat(),
        }

    failure_code = operation.failure.code.value if operation.failure is not None else None

    return {
        "status": "PROCESSED",
        "operation_id": str(operation.operation_id),
        "state": operation.state.value,
        "record_version": operation.record_version,
        "local_job_id": operation.local_job_id,
        "progress": {
            "chunks_planned": operation.progress.chunks_planned,
            "chunks_completed": operation.progress.chunks_completed,
            "chunks_failed": operation.progress.chunks_failed,
            "candles_estimated": operation.progress.candles_estimated,
            "candles_received": operation.progress.candles_received,
            "candles_persisted": operation.progress.candles_persisted,
            "requests_completed": operation.progress.requests_completed,
            "updated_at": operation.progress.updated_at.isoformat(),
        },
        "result": result,
        "failure_code": failure_code,
        "started_at": (
            operation.started_at.isoformat() if operation.started_at is not None else None
        ),
        "finished_at": (
            operation.finished_at.isoformat() if operation.finished_at is not None else None
        ),
    }


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


def _add_resample_arguments(parser: argparse.ArgumentParser) -> None:
    _add_market_identity(parser)
    parser.add_argument("--source-timeframe", choices=tuple(TIMEFRAMES), required=True)
    parser.add_argument("--target-timeframe", choices=tuple(TIMEFRAMES), required=True)
    parser.add_argument(
        "--gap-policy",
        choices=tuple(item.value for item in GapPolicy),
        default="STRICT",
    )
    _add_time_range(parser)


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


def _print_resampling_plan(plan: ResamplingPlan, stdout: TextIO) -> None:
    _print(
        {
            "source_dataset_key": plan.source.key,
            "target_dataset_key": plan.target.key,
            "source_timeframe": plan.source.timeframe,
            "target_timeframe": plan.target.timeframe,
            "source_candles": plan.source_candles,
            "estimated_groups": plan.expected_groups,
            "estimated_partitions": plan.estimated_partitions,
            "gap_policy": plan.gap_policy.value,
        },
        stdout,
    )


def _print_manifest(manifest: DatasetManifest, stdout: TextIO) -> None:
    _print(
        {
            "dataset_key": manifest.target_dataset_key,
            "state": manifest.state.value,
            "version": manifest.target_version,
            "checksum": manifest.target_checksum,
            "source_dataset_key": manifest.source_dataset_key,
            "source_version": manifest.source_dataset_version,
            "source_timeframe": manifest.source_timeframe,
            "target_timeframe": manifest.target_timeframe,
            "count": manifest.candle_count,
            "partitions": len(manifest.partitions),
        },
        stdout,
    )


def _quality_baseline_path(
    market_root: Path,
    identity_key: str,
    scope: QualityScanScope,
) -> Path:
    digest = hashlib.sha256(f"{identity_key}:{scope.value}".encode()).hexdigest()
    return market_root / "quality-baselines" / f"{digest}.json"


def _require_confirmation(confirmed: bool, chunk_count: int) -> None:
    if chunk_count > 1 and not confirmed:
        raise MarketDataInconsistencyError("Backfill grande exige confirmação explícita --yes.")


if __name__ == "__main__":
    raise SystemExit(main())
