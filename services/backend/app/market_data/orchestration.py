"""Sequential Phase 2B job execution over the transactional Phase 2A service."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Protocol
from uuid import UUID

from app.market_data.catalog import ChunkCommitReceipt, ChunkOperationContext
from app.market_data.domain import DataRange, Instrument, TradingPair
from app.market_data.errors import MarketDataError, MarketDataInconsistencyError
from app.market_data.jobs import MarketJobCatalog, MarketJobRecord
from app.market_data.locks import DatasetLease, DatasetLockManager
from app.market_data.planning import (
    BackfillChunk,
    BackfillPlan,
    BackfillResult,
    MarketJobStatus,
    MarketJobType,
    expected_candle_count,
)
from app.market_data.services import HistoricalMarketDataService
from app.market_data.timeframes import get_timeframe

logger = logging.getLogger(__name__)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BackfillControl(StrEnum):
    """Cooperative action requested at a safe chunk boundary."""

    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    CANCEL = "CANCEL"


class BackfillExecutionObserver(Protocol):
    """Observe durable execution without coupling the executor to PostgreSQL."""

    async def before_chunk(
        self,
        record: MarketJobRecord,
        chunk: BackfillChunk,
    ) -> BackfillControl: ...

    async def after_checkpoint(
        self,
        record: MarketJobRecord,
        chunk: BackfillChunk,
    ) -> None: ...


class BackfillExecutor:
    """Execute and checkpoint one bounded chunk at a time."""

    def __init__(
        self,
        *,
        history: HistoricalMarketDataService,
        jobs: MarketJobCatalog,
        data_dir: Path,
        lock_timeout_seconds: float,
        lock_stale_after_seconds: float,
    ) -> None:
        self._history = history
        self._jobs = jobs
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
        )

    async def run(
        self,
        plan: BackfillPlan,
        pair: TradingPair,
        *,
        dry_run: bool = False,
        observer: BackfillExecutionObserver | None = None,
    ) -> BackfillResult:
        if not plan.chunks:
            raise MarketDataInconsistencyError("Um job executável deve possuir chunks.")
        if dry_run:
            return BackfillResult(
                job_id=plan.job_id,
                status=MarketJobStatus.PLANNED,
                chunks_completed=0,
                total_chunks=len(plan.chunks),
                fetched_count=0,
                stored_count=0,
                duplicate_count=0,
                request_count=0,
            )
        record = self._jobs.create(plan)
        if record.status is MarketJobStatus.COMPLETED:
            return _result(record, 0)
        return await self._execute(
            record,
            plan,
            pair,
            observer=observer,
        )

    async def resume(
        self,
        job_id: str,
        pair: TradingPair,
        *,
        observer: BackfillExecutionObserver | None = None,
    ) -> BackfillResult:
        record = self._jobs.get(job_id)
        if record.status not in {MarketJobStatus.PAUSED, MarketJobStatus.FAILED}:
            raise MarketDataInconsistencyError("Somente job pausado ou falho pode ser retomado.")
        plan = _record_plan(record)
        if record.dataset_key != _dataset_key(pair, plan.timeframe.code):
            raise MarketDataInconsistencyError("O símbolo informado diverge do job persistido.")
        return await self._execute(
            record,
            plan,
            pair,
            observer=observer,
        )

    async def _execute(
        self,
        record: MarketJobRecord,
        plan: BackfillPlan,
        pair: TradingPair,
        *,
        observer: BackfillExecutionObserver | None,
    ) -> BackfillResult:
        requests = 0
        if record.dataset_key != _dataset_key(pair, plan.timeframe.code):
            raise MarketDataInconsistencyError("O símbolo informado diverge do plano.")
        with self._locks.acquire(record.dataset_key) as lease:
            instrument = _instrument(pair)
            with self._history.dataset_lease(instrument, plan.timeframe, lease=lease):
                pass
            record = self._jobs.start(record.job_id)
            logger.info(
                "Market-data job started",
                extra={"job_id": record.job_id, "dataset_key": record.dataset_key},
            )
            try:
                for chunk in plan.chunks[record.next_chunk_index :]:
                    current = self._jobs.get(record.job_id)
                    if current.status in {
                        MarketJobStatus.PAUSED,
                        MarketJobStatus.CANCELLED,
                    }:
                        logger.info(
                            "Market-data job interrupted cooperatively",
                            extra={
                                "job_id": current.job_id,
                                "dataset_key": current.dataset_key,
                                "job_status": current.status.value,
                            },
                        )
                        return _result(current, requests)

                    if observer is not None:
                        control = await observer.before_chunk(current, chunk)
                        if not isinstance(control, BackfillControl):
                            raise MarketDataInconsistencyError(
                                "O observer retornou controle de execução inválido."
                            )

                        if control is BackfillControl.PAUSE:
                            current = self._jobs.pause(record.job_id)
                            logger.info(
                                "Market-data job paused by execution observer",
                                extra={
                                    "job_id": current.job_id,
                                    "dataset_key": current.dataset_key,
                                    "chunk_index": chunk.index,
                                },
                            )
                            return _result(current, requests)

                        if control is BackfillControl.CANCEL:
                            current = self._jobs.cancel(record.job_id)
                            logger.info(
                                "Market-data job cancelled by execution observer",
                                extra={
                                    "job_id": current.job_id,
                                    "dataset_key": current.dataset_key,
                                    "chunk_index": chunk.index,
                                },
                            )
                            return _result(current, requests)
                    existing = self._history.verify(
                        instrument,
                        plan.timeframe,
                        chunk.data_range,
                        lease=lease,
                    )
                    if (
                        existing.is_valid
                        and existing.expected_count is not None
                        and existing.checked_count == existing.expected_count
                    ):
                        receipt = self._history.get_chunk_receipt(record.job_id, chunk.index)
                        if receipt is not None:
                            self._validate_receipt(receipt, record, chunk)
                            if (
                                plan.job_type is MarketJobType.GAP_REPAIR
                                and chunk.index == len(plan.chunks) - 1
                            ):
                                self._verify_repair(instrument, plan, lease)
                            record = self._advance_with_receipt(record, receipt)
                            if observer is not None:
                                await observer.after_checkpoint(record, chunk)
                            logger.info(
                                "Market-data confirmed chunk recovered from receipt",
                                extra={
                                    "job_id": record.job_id,
                                    "dataset_key": record.dataset_key,
                                    "chunk_index": chunk.index,
                                },
                            )
                            continue
                    started = monotonic()
                    logger.info(
                        "Market-data chunk started",
                        extra={
                            "job_id": record.job_id,
                            "dataset_key": record.dataset_key,
                            "chunk_index": chunk.index,
                        },
                    )
                    ingestion = await self._history.ingest(
                        pair,
                        plan.timeframe,
                        chunk.data_range,
                        lease=lease,
                        operation=ChunkOperationContext(
                            job_id=record.job_id,
                            chunk_index=chunk.index,
                            data_range=chunk.data_range,
                        ),
                    )
                    requests += ingestion.request_count
                    receipt = self._history.get_chunk_receipt(record.job_id, chunk.index)
                    if receipt is None:
                        raise MarketDataInconsistencyError(
                            "O recibo durável do chunk não foi encontrado."
                        )
                    self._validate_receipt(receipt, record, chunk)
                    if (
                        plan.job_type is MarketJobType.GAP_REPAIR
                        and chunk.index == len(plan.chunks) - 1
                    ):
                        self._verify_repair(instrument, plan, lease)
                    record = self._checkpoint_confirmed_chunk(
                        record,
                        chunk,
                        fetched=receipt.fetched_count,
                        stored=receipt.stored_count,
                        duplicates=receipt.duplicate_count,
                        requests=receipt.request_count,
                    )
                    if observer is not None:
                        await observer.after_checkpoint(record, chunk)
                    logger.info(
                        "Market-data chunk completed",
                        extra={
                            "job_id": record.job_id,
                            "dataset_key": record.dataset_key,
                            "chunk_index": chunk.index,
                            "request_count": ingestion.request_count,
                            "fetched_count": ingestion.fetched_count,
                            "stored_count": ingestion.stored_count,
                            "duration_ms": int((monotonic() - started) * 1_000),
                        },
                    )
                    logger.info(
                        "Market-data job progress",
                        extra={
                            "job_id": record.job_id,
                            "dataset_key": record.dataset_key,
                            "chunks_completed": record.chunks_completed,
                            "total_chunks": len(record.chunk_ranges),
                        },
                    )
            except Exception as error:
                code = error.code if isinstance(error, MarketDataError) else "job_failed"
                self._jobs.fail(record.job_id, code)
                logger.warning(
                    "Market-data job failed",
                    extra={
                        "job_id": record.job_id,
                        "dataset_key": record.dataset_key,
                        "failure_code": code,
                    },
                )
                raise
        final = self._jobs.get(record.job_id)
        logger.info(
            "Market-data job completed",
            extra={"job_id": final.job_id, "dataset_key": final.dataset_key},
        )
        return _result(final, requests)

    def _validate_receipt(
        self,
        receipt: ChunkCommitReceipt,
        record: MarketJobRecord,
        chunk: BackfillChunk,
    ) -> None:
        try:
            UUID(receipt.job_id)
            committed_at = datetime.fromisoformat(receipt.committed_at)
        except ValueError:
            raise MarketDataInconsistencyError("O recibo durável do chunk é inválido.") from None
        if (
            receipt.job_id != record.job_id
            or receipt.chunk_index != chunk.index
            or receipt.dataset_key != record.dataset_key
            or receipt.start != chunk.data_range.start.isoformat()
            or receipt.end != chunk.data_range.end.isoformat()
            or min(
                receipt.fetched_count,
                receipt.stored_count,
                receipt.duplicate_count,
                receipt.request_count,
            )
            < 0
            or receipt.stored_count + receipt.duplicate_count > receipt.fetched_count
            or not _SHA256_PATTERN.fullmatch(receipt.version)
            or not _SHA256_PATTERN.fullmatch(receipt.checksum)
            or committed_at.tzinfo is None
        ):
            raise MarketDataInconsistencyError("O recibo durável do chunk diverge do job.")

    def _advance_with_receipt(
        self,
        record: MarketJobRecord,
        receipt: ChunkCommitReceipt,
    ) -> MarketJobRecord:
        return self._jobs.advance(
            record.job_id,
            chunk_index=receipt.chunk_index,
            fetched=receipt.fetched_count,
            stored=receipt.stored_count,
            duplicates=receipt.duplicate_count,
            requests=receipt.request_count,
        )

    def _verify_repair(
        self,
        instrument: Instrument,
        plan: BackfillPlan,
        lease: DatasetLease,
    ) -> None:
        report = self._history.verify(
            instrument,
            plan.timeframe,
            plan.data_range,
            lease=lease,
        )
        if (
            not report.is_valid
            or report.expected_count is None
            or report.checked_count != report.expected_count
        ):
            raise MarketDataInconsistencyError("O gap persiste após nova consulta à fonte.")

    def _checkpoint_confirmed_chunk(
        self,
        record: MarketJobRecord,
        chunk: BackfillChunk,
        *,
        fetched: int,
        stored: int,
        duplicates: int,
        requests: int,
    ) -> MarketJobRecord:
        """Retry a transient checkpoint failure after the candle commit."""
        try:
            return self._jobs.advance(
                record.job_id,
                chunk_index=chunk.index,
                fetched=fetched,
                stored=stored,
                duplicates=duplicates,
                requests=requests,
            )
        except Exception:
            current = self._jobs.get(record.job_id)
            if current.next_chunk_index == chunk.index + 1:
                return current
            logger.info(
                "Market-data checkpoint retry",
                extra={
                    "job_id": record.job_id,
                    "dataset_key": record.dataset_key,
                    "chunk_index": chunk.index,
                },
            )
            return self._jobs.advance(
                record.job_id,
                chunk_index=chunk.index,
                fetched=fetched,
                stored=stored,
                duplicates=duplicates,
                requests=requests,
            )


def _record_plan(record: MarketJobRecord) -> BackfillPlan:
    timeframe = get_timeframe(record.timeframe)
    chunks = tuple(
        BackfillChunk(
            index=index,
            data_range=DataRange(datetime.fromisoformat(start), datetime.fromisoformat(end)),
            expected_candles=expected_candle_count(
                DataRange(datetime.fromisoformat(start), datetime.fromisoformat(end)),
                timeframe,
            ),
        )
        for index, (start, end) in enumerate(record.chunk_ranges)
    )
    return BackfillPlan(
        job_id=record.job_id,
        dataset_key=record.dataset_key,
        timeframe=timeframe,
        data_range=DataRange(
            datetime.fromisoformat(record.start),
            datetime.fromisoformat(record.end),
        ),
        chunks=chunks,
        expected_candles=record.candles_expected,
        chunk_candles=record.chunk_candles,
        job_type=record.job_type,
    )


def _result(record: MarketJobRecord, requests: int) -> BackfillResult:
    return BackfillResult(
        job_id=record.job_id,
        status=record.status,
        chunks_completed=record.chunks_completed,
        total_chunks=len(record.chunk_ranges),
        fetched_count=record.candles_fetched,
        stored_count=record.candles_stored,
        duplicate_count=record.duplicates,
        request_count=max(requests, record.request_count),
    )


def _instrument(pair: TradingPair) -> Instrument:
    from app.market_data.domain import Exchange, MarketType

    return Instrument(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=pair,
        native_symbol=f"{pair.base}{pair.quote}",
        active=True,
    )


def _dataset_key(pair: TradingPair, timeframe: str) -> str:
    return f"binance:spot:{pair.symbol}:{timeframe}"
