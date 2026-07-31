"""Application services composing adapters, quality, Parquet and catalog."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from app.market_data.adapters import MarketDataAdapter
from app.market_data.catalog import (
    CatalogLease,
    ChunkCommitReceipt,
    ChunkOperationContext,
    DatasetMetadata,
    IngestionRunRecord,
    JsonMarketDataCatalog,
    dataset_key,
)
from app.market_data.domain import (
    Candle,
    DataQualityReport,
    DataRange,
    IngestionResult,
    Instrument,
    Timeframe,
    TradingPair,
)
from app.market_data.errors import (
    InvalidDataRangeError,
    MarketDataError,
    MarketDataInconsistencyError,
)
from app.market_data.locks import DatasetLease, DatasetLockManager
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.storage import ParquetCandleStore, ParquetUpsertPlan
from app.market_data.transaction import MarketDataTransactionCoordinator

Clock = Callable[[], datetime]


class InstrumentCatalogService:
    """Expose normalized instrument discovery."""

    def __init__(self, adapter: MarketDataAdapter) -> None:
        self._adapter = adapter

    async def list(self) -> tuple[Instrument, ...]:
        return await self._adapter.list_instruments()

    async def get(self, pair: TradingPair) -> Instrument:
        return await self._adapter.get_instrument(pair)


class HistoricalMarketDataService:
    """Idempotent adapter-to-Parquet ingestion with a durable local transaction."""

    def __init__(
        self,
        *,
        adapter: MarketDataAdapter,
        store: ParquetCandleStore,
        catalog: JsonMarketDataCatalog,
        validator: MarketDataQualityValidator,
        max_fetch_candles: int,
        coordinator: MarketDataTransactionCoordinator | None = None,
        clock: Clock | None = None,
        lock_manager: DatasetLockManager | None = None,
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._catalog = catalog
        self._validator = validator
        self._max_fetch_candles = max_fetch_candles
        self._coordinator = coordinator or MarketDataTransactionCoordinator(store, catalog)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock_manager = lock_manager or DatasetLockManager(
            store.root.parent,
            timeout_seconds=10,
            stale_after_seconds=3_600,
        )

    @property
    def catalog(self) -> JsonMarketDataCatalog:
        """Expose the local catalog to read-only Phase 2C composition."""
        return self._catalog

    async def ingest(
        self,
        pair: TradingPair,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        dry_run: bool = False,
        lease: DatasetLease | None = None,
        operation: ChunkOperationContext | None = None,
    ) -> IngestionResult:
        """Fetch, validate, deduplicate, persist and catalog one bounded interval."""
        if not timeframe.validate_open_time(data_range.start) or not timeframe.validate_open_time(
            data_range.end
        ):
            raise InvalidDataRangeError("O intervalo deve estar alinhado ao timeframe.")
        interval = data_range.end - data_range.start
        expected_count = interval // timeframe.duration
        if data_range.start + expected_count * timeframe.duration != data_range.end:
            raise InvalidDataRangeError("O intervalo deve cobrir candles inteiros.")
        if expected_count > self._max_fetch_candles:
            raise InvalidDataRangeError(
                "O intervalo excede o limite seguro de candles e deve ser dividido."
            )
        key = (
            f"{self._adapter.exchange.value}:{self._adapter.market_type.value}:"
            f"{pair.symbol}:{timeframe.code}"
        )
        if operation is not None and operation.data_range != data_range:
            raise MarketDataInconsistencyError("O contexto do chunk diverge do intervalo.")
        if operation is not None:
            try:
                UUID(operation.job_id)
            except ValueError:
                raise MarketDataInconsistencyError("O job do contexto é inválido.") from None
            if operation.chunk_index < 0:
                raise MarketDataInconsistencyError("O índice do chunk é inválido.")
        if not dry_run and lease is None:
            with self._lock_manager.acquire(key) as acquired:
                self._coordinator.recover_dataset(key, acquired)
                instrument = await self._adapter.get_instrument(pair)
                if dataset_key(instrument, timeframe) != key:
                    raise MarketDataInconsistencyError("A identidade do adapter divergiu.")
                return await self._ingest(
                    instrument,
                    timeframe,
                    data_range,
                    key,
                    lease=acquired,
                    operation=operation,
                )
        if not dry_run:
            assert lease is not None
            self._lock_manager.validate(lease, key)
            self._coordinator.recover_dataset(key, lease)
        instrument = await self._adapter.get_instrument(pair)
        if dataset_key(instrument, timeframe) != key:
            raise MarketDataInconsistencyError("A identidade do adapter divergiu.")
        return await self._ingest(
            instrument,
            timeframe,
            data_range,
            key,
            lease=lease,
            operation=operation,
            dry_run=dry_run,
        )

    async def _ingest(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
        key: str,
        *,
        lease: DatasetLease | None,
        operation: ChunkOperationContext | None,
        dry_run: bool = False,
    ) -> IngestionResult:
        persisted_run = None if dry_run else self._catalog.start_run(key)
        run_id = str(uuid4()) if persisted_run is None else persisted_run.run_id
        try:
            batch = await self._adapter.fetch_candles(
                instrument,
                timeframe,
                data_range,
                max_candles=self._max_fetch_candles,
            )
            quality = self._validator.validate(
                batch.candles,
                timeframe=timeframe,
                expected_range=data_range,
            )
            if not quality.is_valid:
                raise MarketDataInconsistencyError(details={"run_id": run_id})
            ordered = tuple(sorted(batch.candles, key=lambda candle: candle.open_time))
            if dry_run:
                return _result(run_id, batch.source_request_count, ordered, quality, 0, 0, True)

            assert persisted_run is not None
            closed = tuple(candle for candle in ordered if candle.is_closed)
            transaction_id = uuid4().hex
            plan = self._store.plan_upsert(closed, transaction_id=transaction_id)
            if not closed:
                first, last, count = self._store.first_last_count(
                    instrument.exchange,
                    instrument.market_type,
                    instrument.pair,
                    timeframe,
                )
                plan = ParquetUpsertPlan(
                    transaction_id=transaction_id,
                    partitions=(),
                    stored_count=0,
                    duplicate_count=0,
                    first_open_time=first,
                    last_open_time=last,
                    candle_count=count,
                    checksum=sha256(b"").hexdigest(),
                )
            committed_at = self._clock().astimezone(UTC).isoformat()
            with self._catalog.acquire_lease() as catalog_lease:
                previous = self._catalog.get_dataset(key, lease=catalog_lease)
                version = self._intended_version(previous, plan)
                metadata = self._dataset_metadata(
                    instrument,
                    timeframe,
                    first=plan.first_open_time,
                    last=plan.last_open_time,
                    count=plan.candle_count,
                    version=version,
                    catalog_lease=catalog_lease,
                )
                receipt = (
                    ChunkCommitReceipt(
                        job_id=operation.job_id,
                        chunk_index=operation.chunk_index,
                        dataset_key=key,
                        start=data_range.start.isoformat(),
                        end=data_range.end.isoformat(),
                        fetched_count=len(ordered),
                        stored_count=plan.stored_count,
                        duplicate_count=plan.duplicate_count,
                        request_count=batch.source_request_count,
                        version=version,
                        checksum=plan.checksum,
                        committed_at=committed_at,
                    )
                    if operation is not None
                    else None
                )
                catalog_plan = self._catalog.prepare_completion(
                    _completed_run(
                        run_id,
                        key,
                        len(ordered),
                        plan.stored_count,
                        started_at=persisted_run.started_at,
                        finished_at=committed_at,
                    ),
                    metadata,
                    transaction_id=transaction_id,
                    lease=catalog_lease,
                    receipt=receipt,
                )
                self._coordinator.execute(
                    plan,
                    catalog_plan,
                    intended_version=version,
                    catalog_lease=catalog_lease,
                )
            return _result(
                run_id,
                batch.source_request_count,
                ordered,
                quality,
                plan.stored_count,
                plan.duplicate_count,
                False,
            )
        except Exception as error:
            error_code = error.code if isinstance(error, MarketDataError) else "ingestion_failed"
            if not dry_run:
                try:
                    self._catalog.fail_run(run_id, key, error_code)
                except MarketDataError:
                    pass
            raise

    def get_chunk_receipt(self, job_id: str, chunk_index: int) -> ChunkCommitReceipt | None:
        return self._catalog.get_chunk_receipt(job_id, chunk_index)

    def inspect(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        *,
        lease: DatasetLease | None = None,
    ) -> DatasetMetadata:
        """Return current local dataset boundaries and count."""
        with self.dataset_lease(instrument, timeframe, lease=lease):
            return self._dataset_metadata(instrument, timeframe)

    def verify(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        lease: DatasetLease | None = None,
    ) -> DataQualityReport:
        """Read a bounded interval and run the same canonical quality checks."""
        with self.dataset_lease(instrument, timeframe, lease=lease):
            candles = self._store.read(
                instrument.exchange,
                instrument.market_type,
                instrument.pair,
                timeframe,
                data_range,
            )
            return self._validator.validate(
                candles,
                timeframe=timeframe,
                expected_range=data_range,
            )

    @contextmanager
    def dataset_lease(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        *,
        lease: DatasetLease | None = None,
    ) -> Iterator[DatasetLease]:
        """Hold a recovered, exact dataset lease for a complete read operation."""
        key = dataset_key(instrument, timeframe)
        if lease is not None:
            self._lock_manager.validate(lease, key)
            self._coordinator.recover_dataset(key, lease)
            yield lease
            return
        with self._lock_manager.acquire(key) as acquired:
            self._coordinator.recover_dataset(key, acquired)
            yield acquired

    def _dataset_metadata(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        *,
        first: datetime | None = None,
        last: datetime | None = None,
        count: int | None = None,
        version: str | None = None,
        catalog_lease: CatalogLease | None = None,
    ) -> DatasetMetadata:
        if count is None:
            first, last, count = self._store.first_last_count(
                instrument.exchange,
                instrument.market_type,
                instrument.pair,
                timeframe,
            )
        location_path = self._store.dataset_root(
            instrument.exchange,
            instrument.market_type,
            instrument.pair,
            timeframe,
        )
        logical_location = Path("market") / location_path.relative_to(self._store.root)
        existing = self._catalog.get_dataset(
            dataset_key(instrument, timeframe),
            lease=catalog_lease,
        )
        version_payload = f"{first}|{last}|{count}".encode()
        return DatasetMetadata(
            key=dataset_key(instrument, timeframe),
            exchange=instrument.exchange.value,
            market_type=instrument.market_type.value,
            symbol=instrument.symbol,
            native_symbol=instrument.native_symbol,
            timeframe=timeframe.code,
            location=logical_location.as_posix(),
            first_open_time=first.isoformat() if first else None,
            last_open_time=last.isoformat() if last else None,
            candle_count=count,
            version=version
            or (existing.version if existing else sha256(version_payload).hexdigest()),
            updated_at=self._clock().astimezone(UTC).isoformat(),
        )

    @staticmethod
    def _intended_version(
        previous: DatasetMetadata | None,
        plan: ParquetUpsertPlan,
    ) -> str:
        if previous is not None and plan.stored_count == 0:
            return previous.version
        return plan.checksum


def default_local_services(
    data_dir: Path,
    adapter: MarketDataAdapter,
    *,
    max_fetch_candles: int,
    clock: Clock | None = None,
    lock_timeout_seconds: float = 10,
    lock_stale_after_seconds: float = 3_600,
) -> tuple[InstrumentCatalogService, HistoricalMarketDataService]:
    """Build the local Phase 2A service graph."""
    store = ParquetCandleStore(data_dir)
    effective_clock = clock or (lambda: datetime.now(UTC))
    catalog = JsonMarketDataCatalog(data_dir, clock=effective_clock)
    lock_manager = DatasetLockManager(
        data_dir,
        timeout_seconds=lock_timeout_seconds,
        stale_after_seconds=lock_stale_after_seconds,
        clock=effective_clock,
    )
    coordinator = MarketDataTransactionCoordinator(
        store,
        catalog,
        lock_manager=lock_manager,
    )
    coordinator.recover()
    return (
        InstrumentCatalogService(adapter),
        HistoricalMarketDataService(
            adapter=adapter,
            store=store,
            catalog=catalog,
            validator=MarketDataQualityValidator(clock=effective_clock),
            max_fetch_candles=max_fetch_candles,
            coordinator=coordinator,
            clock=effective_clock,
            lock_manager=lock_manager,
        ),
    )


def _completed_run(
    run_id: str,
    key: str,
    fetched_count: int,
    stored_count: int,
    *,
    started_at: str,
    finished_at: str,
) -> IngestionRunRecord:
    return IngestionRunRecord(
        run_id=run_id,
        dataset_key=key,
        status="COMPLETED",
        started_at=started_at,
        finished_at=finished_at,
        fetched_count=fetched_count,
        stored_count=stored_count,
        error_code=None,
    )


def _result(
    run_id: str,
    request_count: int,
    candles: tuple[Candle, ...],
    quality: DataQualityReport,
    stored_count: int,
    duplicate_count: int,
    dry_run: bool,
) -> IngestionResult:
    first = candles[0].open_time if candles else None
    last = candles[-1].open_time if candles else None
    return IngestionResult(
        run_id=run_id,
        fetched_count=len(candles),
        stored_count=stored_count,
        duplicate_count=duplicate_count,
        request_count=request_count,
        first_open_time=first,
        last_open_time=last,
        quality=quality,
        dry_run=dry_run,
    )
