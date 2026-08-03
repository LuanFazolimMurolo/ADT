"""Local RAW candle source for deterministic paper replay."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.market_data.catalog import JsonMarketDataCatalog, dataset_key
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import (
    Candle,
    DataRange,
    Exchange,
    Instrument,
    MarketType,
    require_utc,
)
from app.market_data.errors import MarketDataInconsistencyError
from app.market_data.locks import DatasetLockManager
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.storage import ParquetCandleStore, canonical_candle_bytes
from app.market_data.transaction import MarketDataTransactionCoordinator
from app.paper_trading.domain import PaperCandleBatch, PaperSessionConfig, paper_session_id
from app.paper_trading.errors import PaperSessionDataUnavailableError


class PaperCandleSource(Protocol):
    def load(
        self,
        config: PaperSessionConfig,
        *,
        end: datetime | None = None,
    ) -> PaperCandleBatch: ...


@dataclass(slots=True)
class LocalRawPaperCandleSource:
    data_dir: Path
    lock_timeout_seconds: float = 10
    lock_stale_after_seconds: float = 3_600

    def load(
        self,
        config: PaperSessionConfig,
        *,
        end: datetime | None = None,
    ) -> PaperCandleBatch:
        paper_session_id(config)
        selected_end_override = None if end is None else require_utc(end, field_name="end")
        if selected_end_override is not None and not config.timeframe.validate_open_time(
            selected_end_override
        ):
            raise PaperSessionDataUnavailableError(
                "O fim solicitado não está alinhado ao timeframe."
            )
        store = ParquetCandleStore(self.data_dir)
        catalog = JsonMarketDataCatalog(self.data_dir)
        lock_manager = DatasetLockManager(
            self.data_dir,
            timeout_seconds=self.lock_timeout_seconds,
            stale_after_seconds=self.lock_stale_after_seconds,
        )
        coordinator = MarketDataTransactionCoordinator(
            store,
            catalog,
            lock_manager=lock_manager,
        )
        instrument = Instrument(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            pair=config.pair,
            native_symbol=f"{config.pair.base}{config.pair.quote}",
            active=True,
        )
        key = dataset_key(instrument, config.timeframe)
        with lock_manager.acquire(key) as lease:
            coordinator.recover_dataset(key, lease)
            first, last, count = store.first_last_count(
                instrument.exchange,
                instrument.market_type,
                instrument.pair,
                config.timeframe,
            )
            if first is None or last is None or count < 1:
                raise PaperSessionDataUnavailableError()
            available_end = last + config.timeframe.duration
            selected_end = available_end if selected_end_override is None else selected_end_override
            if selected_end > available_end or selected_end <= config.start_at:
                raise PaperSessionDataUnavailableError()
            data_range = DataRange(config.context_start, selected_end)
            expected = (data_range.end - data_range.start) // config.timeframe.duration
            if expected > config.max_candles:
                raise PaperSessionDataUnavailableError(
                    "A sessão excede o limite seguro de candles para replay."
                )
            candles = store.read(
                instrument.exchange,
                instrument.market_type,
                instrument.pair,
                config.timeframe,
                data_range,
            )
            quality = MarketDataQualityValidator().validate(
                candles,
                timeframe=config.timeframe,
                expected_range=data_range,
            )
            if not quality.is_valid or len(candles) != expected:
                raise PaperSessionDataUnavailableError(
                    "O dataset local não cobre integralmente o replay solicitado."
                )
            dataset_version = store.logical_version(
                instrument.exchange,
                instrument.market_type,
                instrument.pair,
                config.timeframe,
            )
        digest = hashlib.sha256()
        for candle in candles:
            digest.update(canonical_candle_bytes(candle))
        return PaperCandleBatch(
            data_range=data_range,
            dataset_version=dataset_version,
            source_checksum=digest.hexdigest(),
            candles=tuple(candles),
        )


class InMemoryPaperSnapshotReader:
    """Adapter exposing one frozen candle batch to the existing backtest engine."""

    def __init__(self, snapshot: DatasetSnapshot, candles: tuple[Candle, ...]) -> None:
        if not isinstance(snapshot, DatasetSnapshot):
            raise MarketDataInconsistencyError("O snapshot sintético é inválido.")
        self._snapshot = snapshot
        self._candles = candles

    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        if snapshot_id != self._snapshot.snapshot_id:
            raise MarketDataInconsistencyError("O snapshot sintético diverge da sessão.")
        return self._snapshot

    def iter_candles(self, data_range: DataRange | None = None) -> Iterator[Candle]:
        selected = self._snapshot.data_range if data_range is None else data_range
        yield from (
            candle for candle in self._candles if selected.start <= candle.open_time < selected.end
        )

    def verify_unchanged(self) -> DatasetSnapshot:
        return self._snapshot
