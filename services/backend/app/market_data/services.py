"""Application services composing adapters, quality, Parquet and catalog."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.market_data.adapters import MarketDataAdapter
from app.market_data.catalog import (
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
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._catalog = catalog
        self._validator = validator
        self._max_fetch_candles = max_fetch_candles
        self._coordinator = coordinator or MarketDataTransactionCoordinator(store, catalog)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def ingest(
        self,
        pair: TradingPair,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        dry_run: bool = False,
    ) -> IngestionResult:
        """Fetch, validate, deduplicate, persist and catalog one bounded interval."""
        if not timeframe.validate_open_time(data_range.start) or not timeframe.validate_open_time(
            data_range.end
        ):
            raise InvalidDataRangeError("O intervalo deve estar alinhado ao timeframe.")
        expected_count = int((data_range.end - data_range.start) / timeframe.duration)
        if expected_count > self._max_fetch_candles:
            raise InvalidDataRangeError(
                "O intervalo excede o limite seguro de candles e deve ser dividido."
            )
        instrument = await self._adapter.get_instrument(pair)
        key = dataset_key(instrument, timeframe)
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
            previous = self._catalog.get_dataset(key)
            version = self._intended_version(previous, plan)
            metadata = self._dataset_metadata(
                instrument,
                timeframe,
                first=plan.first_open_time,
                last=plan.last_open_time,
                count=plan.candle_count,
                version=version,
            )
            catalog_plan = self._catalog.prepare_completion(
                _completed_run(
                    run_id,
                    key,
                    len(ordered),
                    plan.stored_count,
                    started_at=persisted_run.started_at,
                    finished_at=self._clock().astimezone(UTC).isoformat(),
                ),
                metadata,
                transaction_id=transaction_id,
            )
            self._coordinator.execute(plan, catalog_plan, intended_version=version)
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

    def inspect(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> DatasetMetadata:
        """Return current local dataset boundaries and count."""
        return self._dataset_metadata(instrument, timeframe)

    def verify(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
    ) -> DataQualityReport:
        """Read a bounded interval and run the same canonical quality checks."""
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

    def _dataset_metadata(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        *,
        first: datetime | None = None,
        last: datetime | None = None,
        count: int | None = None,
        version: str | None = None,
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
        existing = self._catalog.get_dataset(dataset_key(instrument, timeframe))
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
        prior = previous.version if previous is not None else ""
        payload = (
            f"{prior}|{plan.checksum}|{plan.first_open_time}|"
            f"{plan.last_open_time}|{plan.candle_count}"
        ).encode()
        return sha256(payload).hexdigest()


def default_local_services(
    data_dir: Path,
    adapter: MarketDataAdapter,
    *,
    max_fetch_candles: int,
    clock: Clock | None = None,
) -> tuple[InstrumentCatalogService, HistoricalMarketDataService]:
    """Build the local Phase 2A service graph."""
    store = ParquetCandleStore(data_dir)
    effective_clock = clock or (lambda: datetime.now(UTC))
    catalog = JsonMarketDataCatalog(data_dir, clock=effective_clock)
    coordinator = MarketDataTransactionCoordinator(store, catalog)
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
