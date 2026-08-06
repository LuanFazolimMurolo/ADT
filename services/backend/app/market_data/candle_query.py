"""Bounded local RAW candle queries for authenticated chart clients."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.market_data.catalog import JsonMarketDataCatalog, dataset_key
from app.market_data.domain import (
    Candle,
    DataRange,
    Exchange,
    Instrument,
    MarketType,
    Timeframe,
    TradingPair,
    require_utc,
)
from app.market_data.errors import (
    InvalidDataRangeError,
    MarketCandleDatasetNotFoundError,
    MarketDataInconsistencyError,
)
from app.market_data.locks import DatasetLockManager
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.storage import ParquetCandleStore, canonical_candle_bytes
from app.market_data.transaction import MarketDataTransactionCoordinator

MARKET_CANDLE_PAGE_SCHEMA_VERSION = 1
MARKET_CANDLE_DEFAULT_LIMIT = 1_000
MARKET_CANDLE_MAX_LIMIT = 5_000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class MarketCandlePageQuery:
    """One canonical backward-looking chart-page request."""

    pair: TradingPair
    timeframe: Timeframe
    before: datetime | None = None
    limit: int = MARKET_CANDLE_DEFAULT_LIMIT

    def __post_init__(self) -> None:
        if not isinstance(self.pair, TradingPair):
            raise InvalidDataRangeError("O par solicitado é inválido.")
        if not isinstance(self.timeframe, Timeframe):
            raise InvalidDataRangeError("O timeframe solicitado é inválido.")
        if type(self.limit) is not int or not 1 <= self.limit <= MARKET_CANDLE_MAX_LIMIT:
            raise InvalidDataRangeError("O limite de candles é inválido.")
        if self.before is None:
            return
        if not isinstance(self.before, datetime):
            raise InvalidDataRangeError("O cursor temporal é inválido.")
        try:
            normalized = require_utc(self.before, field_name="before")
        except MarketDataInconsistencyError as error:
            raise InvalidDataRangeError(error.message) from None
        if not self.timeframe.validate_open_time(normalized):
            raise InvalidDataRangeError("O cursor não está alinhado ao timeframe.")
        object.__setattr__(self, "before", normalized)


@dataclass(frozen=True, slots=True)
class MarketCandlePage:
    """One verified bounded projection from the local RAW dataset."""

    schema_version: int
    exchange: Exchange
    market_type: MarketType
    pair: TradingPair
    timeframe: Timeframe
    requested_before: datetime | None
    available_range: DataRange
    data_range: DataRange
    limit: int
    dataset_candle_count: int
    dataset_version: str
    dataset_version_algorithm: str
    content_checksum: str
    has_more_before: bool
    next_before: datetime | None
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        if self.schema_version != MARKET_CANDLE_PAGE_SCHEMA_VERSION:
            raise MarketDataInconsistencyError("A versão da página de candles é inválida.")
        if not isinstance(self.exchange, Exchange) or not isinstance(self.market_type, MarketType):
            raise MarketDataInconsistencyError("A identidade de mercado é inválida.")
        if not isinstance(self.pair, TradingPair) or not isinstance(self.timeframe, Timeframe):
            raise MarketDataInconsistencyError("A identidade do dataset é inválida.")
        if (
            type(self.limit) is not int
            or not 1 <= self.limit <= MARKET_CANDLE_MAX_LIMIT
            or type(self.dataset_candle_count) is not int
            or self.dataset_candle_count < 1
        ):
            raise MarketDataInconsistencyError("Os limites da página são inválidos.")
        if not _SHA256_PATTERN.fullmatch(self.dataset_version):
            raise MarketDataInconsistencyError("A versão lógica do dataset é inválida.")
        if (
            not isinstance(self.dataset_version_algorithm, str)
            or not self.dataset_version_algorithm.strip()
            or len(self.dataset_version_algorithm) > 128
        ):
            raise MarketDataInconsistencyError("O algoritmo de versão do dataset é inválido.")
        if not _SHA256_PATTERN.fullmatch(self.content_checksum):
            raise MarketDataInconsistencyError("O checksum da página é inválido.")
        if not self.candles or len(self.candles) > self.limit:
            raise MarketDataInconsistencyError("A página de candles possui tamanho inválido.")
        if (
            self.available_range.start > self.data_range.start
            or self.data_range.end > self.available_range.end
        ):
            raise MarketDataInconsistencyError("A página está fora da cobertura disponível.")

        expected_count = (self.data_range.end - self.data_range.start) // self.timeframe.duration
        if len(self.candles) != expected_count:
            raise MarketDataInconsistencyError("A página não cobre integralmente o intervalo.")
        if (
            self.candles[0].open_time != self.data_range.start
            or self.timeframe.next_open_time(self.candles[-1].open_time) != self.data_range.end
        ):
            raise MarketDataInconsistencyError("Os limites temporais da página divergem.")

        previous_open: datetime | None = None
        for candle in self.candles:
            identity = (
                candle.exchange,
                candle.market_type,
                candle.symbol,
                candle.timeframe.code,
            )
            expected_identity = (
                self.exchange,
                self.market_type,
                self.pair.symbol,
                self.timeframe.code,
            )
            if identity != expected_identity or not candle.is_closed:
                raise MarketDataInconsistencyError("A página contém candle incompatível.")
            if previous_open is not None and candle.open_time <= previous_open:
                raise MarketDataInconsistencyError("A página não está em ordem crescente.")
            previous_open = candle.open_time

        expected_more = self.available_range.start < self.data_range.start
        if self.has_more_before != expected_more:
            raise MarketDataInconsistencyError("O estado de paginação é inválido.")
        expected_next = self.data_range.start if expected_more else None
        if self.next_before != expected_next:
            raise MarketDataInconsistencyError("O próximo cursor é inválido.")

        expected_checksum = market_candle_page_checksum(
            schema_version=self.schema_version,
            exchange=self.exchange,
            market_type=self.market_type,
            pair=self.pair,
            timeframe=self.timeframe,
            requested_before=self.requested_before,
            available_range=self.available_range,
            data_range=self.data_range,
            limit=self.limit,
            dataset_candle_count=self.dataset_candle_count,
            dataset_version=self.dataset_version,
            dataset_version_algorithm=self.dataset_version_algorithm,
            has_more_before=self.has_more_before,
            next_before=self.next_before,
            candles=self.candles,
        )
        if self.content_checksum != expected_checksum:
            raise MarketDataInconsistencyError("O conteúdo da página diverge do checksum.")


def market_candle_page_checksum(
    *,
    schema_version: int,
    exchange: Exchange,
    market_type: MarketType,
    pair: TradingPair,
    timeframe: Timeframe,
    requested_before: datetime | None,
    available_range: DataRange,
    data_range: DataRange,
    limit: int,
    dataset_candle_count: int,
    dataset_version: str,
    dataset_version_algorithm: str,
    has_more_before: bool,
    next_before: datetime | None,
    candles: tuple[Candle, ...],
) -> str:
    """Hash canonical metadata and logical candle bytes."""

    metadata: dict[str, object] = {
        "schema_version": schema_version,
        "exchange": exchange.value,
        "market_type": market_type.value,
        "symbol": pair.symbol,
        "timeframe": timeframe.code,
        "requested_before": (
            requested_before.isoformat() if requested_before is not None else None
        ),
        "available_start": available_range.start.isoformat(),
        "available_end": available_range.end.isoformat(),
        "range_start": data_range.start.isoformat(),
        "range_end": data_range.end.isoformat(),
        "limit": limit,
        "dataset_candle_count": dataset_candle_count,
        "dataset_version": dataset_version,
        "dataset_version_algorithm": dataset_version_algorithm,
        "has_more_before": has_more_before,
        "next_before": next_before.isoformat() if next_before is not None else None,
        "count": len(candles),
    }
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            metadata,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(b"\n")
    for candle in candles:
        digest.update(canonical_candle_bytes(candle))
    return digest.hexdigest()


@dataclass(slots=True)
class LocalMarketCandleReadService:
    """Read only transactionally cataloged local RAW candles."""

    data_dir: Path
    lock_timeout_seconds: float = 10
    lock_stale_after_seconds: float = 3_600

    def read_page(self, query: MarketCandlePageQuery) -> MarketCandlePage:
        """Return one deterministic backward page without network access."""

        if not isinstance(query, MarketCandlePageQuery):
            raise InvalidDataRangeError()
        MarketCandlePageQuery.__post_init__(query)

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
            pair=query.pair,
            native_symbol=f"{query.pair.base}{query.pair.quote}",
            active=True,
        )
        key = dataset_key(instrument, query.timeframe)

        with lock_manager.acquire(key) as lease:
            coordinator.recover_dataset(key, lease)
            metadata = catalog.get_dataset(key)
            if metadata is None:
                raise MarketCandleDatasetNotFoundError()
            if (
                metadata.key != key
                or metadata.exchange != instrument.exchange.value
                or metadata.market_type != instrument.market_type.value
                or metadata.symbol != query.pair.symbol
                or metadata.timeframe != query.timeframe.code
                or type(metadata.candle_count) is not int
                or metadata.candle_count < 1
                or metadata.first_open_time is None
                or metadata.last_open_time is None
                or not _SHA256_PATTERN.fullmatch(metadata.version)
                or not isinstance(metadata.version_algorithm, str)
                or not metadata.version_algorithm.strip()
                or len(metadata.version_algorithm) > 128
            ):
                raise MarketDataInconsistencyError(
                    "O catálogo do dataset de candles é inconsistente."
                )

            try:
                first = require_utc(
                    datetime.fromisoformat(metadata.first_open_time),
                    field_name="first_open_time",
                )
                last = require_utc(
                    datetime.fromisoformat(metadata.last_open_time),
                    field_name="last_open_time",
                )
            except (TypeError, ValueError, MarketDataInconsistencyError):
                raise MarketDataInconsistencyError(
                    "Os limites catalogados do dataset são inválidos."
                ) from None

            if (
                not query.timeframe.validate_open_time(first)
                or not query.timeframe.validate_open_time(last)
                or last < first
            ):
                raise MarketDataInconsistencyError(
                    "Os limites catalogados não estão alinhados ao timeframe."
                )
            available_end = query.timeframe.next_open_time(last)
            selected_end = available_end if query.before is None else query.before
            if selected_end > available_end or selected_end <= first:
                raise InvalidDataRangeError(
                    "O cursor solicitado está fora da cobertura disponível."
                )

            selected_start = max(
                first,
                selected_end - query.timeframe.duration * query.limit,
            )
            selected_range = DataRange(selected_start, selected_end)
            candles = store.read(
                instrument.exchange,
                instrument.market_type,
                instrument.pair,
                query.timeframe,
                selected_range,
            )
            expected_count = (selected_range.end - selected_range.start) // query.timeframe.duration
            quality = MarketDataQualityValidator().validate(
                candles,
                timeframe=query.timeframe,
                expected_range=selected_range,
                now=available_end,
            )
            if (
                not quality.is_valid
                or len(candles) != expected_count
                or any(not candle.is_closed for candle in candles)
            ):
                raise MarketDataInconsistencyError(
                    "O intervalo RAW solicitado não possui cobertura fechada e íntegra."
                )

            available_range = DataRange(first, available_end)
            has_more_before = first < selected_start
            next_before = selected_start if has_more_before else None
            checksum = market_candle_page_checksum(
                schema_version=MARKET_CANDLE_PAGE_SCHEMA_VERSION,
                exchange=instrument.exchange,
                market_type=instrument.market_type,
                pair=instrument.pair,
                timeframe=query.timeframe,
                requested_before=query.before,
                available_range=available_range,
                data_range=selected_range,
                limit=query.limit,
                dataset_candle_count=metadata.candle_count,
                dataset_version=metadata.version,
                dataset_version_algorithm=metadata.version_algorithm,
                has_more_before=has_more_before,
                next_before=next_before,
                candles=candles,
            )

        return MarketCandlePage(
            schema_version=MARKET_CANDLE_PAGE_SCHEMA_VERSION,
            exchange=instrument.exchange,
            market_type=instrument.market_type,
            pair=instrument.pair,
            timeframe=query.timeframe,
            requested_before=query.before,
            available_range=available_range,
            data_range=selected_range,
            limit=query.limit,
            dataset_candle_count=metadata.candle_count,
            dataset_version=metadata.version,
            dataset_version_algorithm=metadata.version_algorithm,
            content_checksum=checksum,
            has_more_before=has_more_before,
            next_before=next_before,
            candles=candles,
        )
