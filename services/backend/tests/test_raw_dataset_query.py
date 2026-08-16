"""Deterministic remote-free tests for persisted RAW dataset inspection."""

from __future__ import annotations

import fcntl
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from app.market_data.catalog import (
    DatasetMetadata,
    IngestionRunRecord,
    JsonMarketDataCatalog,
)
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.market_data.errors import (
    InvalidDatasetIdError,
    InvalidRawDatasetQueryError,
    MarketDataCatalogBusyError,
    MarketDataInconsistencyError,
    RawDatasetNotFoundError,
)
from app.market_data.integrity import (
    LEGACY_RAW_DATASET_VERSION_ALGORITHM,
    RAW_DATASET_VERSION_ALGORITHM,
    RawPartitionIntegrityEntry,
    build_raw_partition_integrity_manifest,
)
from app.market_data.operations import (
    MarketDatasetSelector,
    encode_dataset_id,
)
from app.market_data.raw_dataset_query import (
    LocalRawDatasetReadService,
    RawDatasetPageQuery,
)
from app.market_data.storage import compose_raw_dataset_version
from app.market_data.timeframes import get_timeframe

UPDATED_AT: Final = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _selector(symbol: str, timeframe: str) -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair.parse(symbol),
        timeframe=get_timeframe(timeframe),
    )


def _raw_metadata(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    first_open_time: str | None = "2026-08-01T00:00:00+00:00",
    last_open_time: str | None = "2026-08-01T02:00:00+00:00",
    candle_count: int = 3,
    with_integrity: bool = True,
) -> DatasetMetadata:
    selector = _selector(symbol, timeframe)

    if with_integrity:
        entry = RawPartitionIntegrityEntry(
            relative_path=(
                f"exchange={selector.exchange.value}/"
                f"market={selector.market_type.value}/"
                f"base={selector.pair.base}/"
                f"quote={selector.pair.quote}/"
                f"timeframe={selector.timeframe.code}/"
                "year=2026/month=08/candles.parquet"
            ),
            checksum="a" * 64,
        )
        entries = (entry,)
        version = compose_raw_dataset_version(
            (item.relative_path, item.checksum) for item in entries
        )
        manifest = build_raw_partition_integrity_manifest(version, entries)
        version_algorithm = RAW_DATASET_VERSION_ALGORITHM
    else:
        version = "b" * 64
        manifest = None
        version_algorithm = LEGACY_RAW_DATASET_VERSION_ALGORITHM

    return DatasetMetadata(
        key=selector.canonical_key,
        exchange=selector.exchange.value,
        market_type=selector.market_type.value,
        symbol=selector.pair.symbol,
        native_symbol=f"{selector.pair.base}{selector.pair.quote}",
        timeframe=selector.timeframe.code,
        location=(
            "/srv/ADT_DATA_DIR/do-not-expose/"
            f"{selector.pair.base}-{selector.pair.quote}-{selector.timeframe.code}"
        ),
        first_open_time=first_open_time,
        last_open_time=last_open_time,
        candle_count=candle_count,
        version=version,
        updated_at=UPDATED_AT.isoformat(),
        version_algorithm=version_algorithm,
        partition_integrity=manifest,
    )


class FakeRawCatalog:
    """Read-only fake; mutation, scanning and network methods do not exist."""

    def __init__(self, datasets: tuple[DatasetMetadata, ...]) -> None:
        self.datasets = {item.key: item for item in datasets}
        self.list_calls = 0
        self.get_calls: list[str] = []

    def list_datasets_snapshot(
        self,
        *,
        timeout_seconds: float,
    ) -> tuple[DatasetMetadata, ...]:
        self.list_calls += 1
        # Reverse deliberately so the service must own canonical ordering.
        return tuple(reversed(tuple(self.datasets.values())))

    def get_dataset_snapshot(
        self,
        key: str,
        *,
        timeout_seconds: float,
    ) -> DatasetMetadata | None:
        self.get_calls.append(key)
        return self.datasets.get(key)


def test_list_is_bounded_sorted_and_paginated() -> None:
    catalog = FakeRawCatalog(
        (
            _raw_metadata(symbol="BTC/USDT", timeframe="1h"),
            _raw_metadata(
                symbol="ETH/USDT",
                timeframe="5m",
                first_open_time="2026-08-01T00:00:00+00:00",
                last_open_time="2026-08-01T00:10:00+00:00",
            ),
        )
    )
    service = LocalRawDatasetReadService(catalog)

    page = service.list(RawDatasetPageQuery(page=1, page_size=1))

    assert page.page == 1
    assert page.page_size == 1
    assert page.total == 2
    assert page.total_pages == 2
    assert len(page.items) == 1
    assert page.items[0].dataset.pair == TradingPair("BTC", "USDT")
    assert catalog.list_calls == 1

    second = service.list(RawDatasetPageQuery(page=2, page_size=1))
    assert len(second.items) == 1
    assert second.items[0].dataset.pair == TradingPair("ETH", "USDT")


def test_list_canonicalizes_filters_without_adapter_access() -> None:
    catalog = FakeRawCatalog(
        (
            _raw_metadata(symbol="BTC/USDT", timeframe="1h"),
            _raw_metadata(
                symbol="ETH/USDT",
                timeframe="5m",
                first_open_time="2026-08-01T00:00:00+00:00",
                last_open_time="2026-08-01T00:10:00+00:00",
            ),
        )
    )
    service = LocalRawDatasetReadService(catalog)

    page = service.list(
        RawDatasetPageQuery(
            symbol="eth/usdt",
            timeframe="5m",
        )
    )

    assert page.total == 1
    assert page.items[0].dataset.pair == TradingPair("ETH", "USDT")
    assert page.items[0].dataset.timeframe.code == "5m"


def test_get_projects_half_open_coverage_and_sanitized_integrity() -> None:
    metadata = _raw_metadata()
    catalog = FakeRawCatalog((metadata,))
    service = LocalRawDatasetReadService(catalog)
    selector = _selector("BTC/USDT", "1h")

    snapshot = service.get(encode_dataset_id(selector))

    assert snapshot.dataset == selector
    assert snapshot.first_open_time == datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    assert snapshot.last_open_time == datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
    assert snapshot.coverage_start == datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    assert snapshot.coverage_end == datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
    assert snapshot.candle_count == 3
    assert snapshot.version == metadata.version
    assert snapshot.version_algorithm == RAW_DATASET_VERSION_ALGORITHM
    assert snapshot.integrity.present is True
    assert snapshot.integrity.schema_version == 1
    assert snapshot.integrity.checksum_algorithm == RAW_DATASET_VERSION_ALGORITHM
    assert snapshot.integrity.partition_count == 1

    # The domain projection itself contains no filesystem/location fields.
    assert not hasattr(snapshot, "location")
    assert not hasattr(snapshot.integrity, "relative_path")


def test_legacy_dataset_without_integrity_is_reported_without_fabrication() -> None:
    metadata = _raw_metadata(with_integrity=False)
    service = LocalRawDatasetReadService(FakeRawCatalog((metadata,)))

    snapshot = service.get(encode_dataset_id(_selector("BTC/USDT", "1h")))

    assert snapshot.integrity.present is False
    assert snapshot.integrity.schema_version is None
    assert snapshot.integrity.checksum_algorithm is None
    assert snapshot.integrity.partition_count == 0
    assert snapshot.version_algorithm == LEGACY_RAW_DATASET_VERSION_ALGORITHM


def test_invalid_dataset_id_is_rejected_before_catalog_lookup() -> None:
    catalog = FakeRawCatalog((_raw_metadata(),))
    service = LocalRawDatasetReadService(catalog)

    with pytest.raises(InvalidDatasetIdError):
        service.get("abc")

    assert catalog.get_calls == []


def test_missing_dataset_returns_specific_not_found() -> None:
    catalog = FakeRawCatalog(())
    service = LocalRawDatasetReadService(catalog)

    with pytest.raises(RawDatasetNotFoundError):
        service.get(encode_dataset_id(_selector("BTC/USDT", "1h")))


@pytest.mark.parametrize(
    ("page", "page_size"),
    [
        (0, 25),
        (1, 0),
        (100_001, 25),
        (1, 101),
    ],
)
def test_query_rejects_out_of_bounds_pagination(
    page: int,
    page_size: int,
) -> None:
    with pytest.raises(InvalidRawDatasetQueryError):
        RawDatasetPageQuery(page=page, page_size=page_size)


def test_query_rejects_invalid_symbol_and_timeframe() -> None:
    with pytest.raises(InvalidRawDatasetQueryError):
        RawDatasetPageQuery(symbol="BTC")

    with pytest.raises(InvalidRawDatasetQueryError):
        RawDatasetPageQuery(timeframe="99x")


def test_catalog_identity_mismatch_is_rejected() -> None:
    metadata = _raw_metadata()
    tampered = DatasetMetadata(
        key="binance:spot:ETH/USDT:1h",
        exchange=metadata.exchange,
        market_type=metadata.market_type,
        symbol=metadata.symbol,
        native_symbol=metadata.native_symbol,
        timeframe=metadata.timeframe,
        location=metadata.location,
        first_open_time=metadata.first_open_time,
        last_open_time=metadata.last_open_time,
        candle_count=metadata.candle_count,
        version=metadata.version,
        updated_at=metadata.updated_at,
        version_algorithm=metadata.version_algorithm,
        partition_integrity=metadata.partition_integrity,
    )
    service = LocalRawDatasetReadService(FakeRawCatalog((tampered,)))

    with pytest.raises(MarketDataInconsistencyError):
        service.list(RawDatasetPageQuery())


def test_invalid_temporal_coverage_is_rejected() -> None:
    metadata = _raw_metadata(
        first_open_time="2026-08-01T00:30:00+00:00",
        last_open_time="2026-08-01T02:00:00+00:00",
    )
    service = LocalRawDatasetReadService(FakeRawCatalog((metadata,)))

    with pytest.raises(MarketDataInconsistencyError):
        service.list(RawDatasetPageQuery())


def test_catalog_snapshot_read_times_out_behind_writer_lock(
    tmp_path: Path,
) -> None:
    catalog = JsonMarketDataCatalog(tmp_path)
    catalog.path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = catalog.path.parent / ".catalog.lock"

    with lock_path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(
            stream.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
        try:
            with pytest.raises(MarketDataCatalogBusyError):
                catalog.list_datasets_snapshot(
                    timeout_seconds=0.02,
                )
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def test_real_catalog_snapshot_success_path_is_readable_by_service(
    tmp_path: Path,
) -> None:
    catalog = JsonMarketDataCatalog(
        tmp_path,
        clock=lambda: UPDATED_AT,
    )
    metadata = _raw_metadata()

    with catalog.acquire_lease() as lease:
        started = catalog.start_run(
            metadata.key,
            lease=lease,
        )
        completed = IngestionRunRecord(
            run_id=started.run_id,
            dataset_key=started.dataset_key,
            status="COMPLETED",
            started_at=started.started_at,
            finished_at=UPDATED_AT.isoformat(),
            fetched_count=metadata.candle_count,
            stored_count=metadata.candle_count,
            error_code=None,
        )
        plan = catalog.prepare_completion(
            completed,
            metadata,
            transaction_id="00000000-0000-4000-8000-000000000703",
            lease=lease,
        )
        catalog.write_prepared(plan, lease=lease)
        catalog.promote(plan, lease=lease)

    service = LocalRawDatasetReadService(
        catalog,
        lock_timeout_seconds=0.1,
    )

    page = service.list(
        RawDatasetPageQuery(
            page=1,
            page_size=25,
        )
    )

    assert page.total == 1
    assert page.total_pages == 1
    assert page.items == (service.get(encode_dataset_id(_selector("BTC/USDT", "1h"))),)
    assert page.items[0].version == metadata.version
    assert page.items[0].candle_count == 3
    assert page.items[0].integrity.present is True

    # Snapshot reads must leave the persisted catalog bytes untouched.
    before = catalog.path.read_bytes()

    catalog.list_datasets_snapshot(
        timeout_seconds=0.1,
    )
    catalog.get_dataset_snapshot(
        metadata.key,
        timeout_seconds=0.1,
    )

    assert catalog.path.read_bytes() == before
