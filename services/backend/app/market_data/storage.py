"""Planned, exact and atomic monthly Parquet storage."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from app.market_data.domain import (
    Candle,
    DataRange,
    Exchange,
    MarketType,
    Timeframe,
    TradingPair,
    datetime_to_epoch_milliseconds,
)
from app.market_data.errors import MarketDataInconsistencyError, MarketDataStorageError
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.integrity import (
    LEGACY_RAW_DATASET_VERSION_ALGORITHM as _LEGACY_RAW_DATASET_VERSION_ALGORITHM,
)
from app.market_data.integrity import (
    RAW_DATASET_VERSION_ALGORITHM as _RAW_DATASET_VERSION_ALGORITHM,
)
from app.market_data.integrity import RawPartitionIntegrityEntry

RAW_DATASET_VERSION_ALGORITHM = _RAW_DATASET_VERSION_ALGORITHM
LEGACY_RAW_DATASET_VERSION_ALGORITHM = _LEGACY_RAW_DATASET_VERSION_ALGORITHM

DECIMAL_TYPE = pa.decimal128(38, 18)
PARQUET_SCHEMA = pa.schema(
    [
        pa.field("exchange", pa.string(), nullable=False),
        pa.field("market_type", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timeframe", pa.string(), nullable=False),
        pa.field("open_time", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("close_time", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("open", DECIMAL_TYPE, nullable=False),
        pa.field("high", DECIMAL_TYPE, nullable=False),
        pa.field("low", DECIMAL_TYPE, nullable=False),
        pa.field("close", DECIMAL_TYPE, nullable=False),
        pa.field("volume", DECIMAL_TYPE, nullable=False),
        pa.field("quote_volume", DECIMAL_TYPE, nullable=True),
        pa.field("trade_count", pa.int64(), nullable=True),
        pa.field("is_closed", pa.bool_(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True, slots=True)
class PlannedPartition:
    """One fully validated monthly replacement."""

    target: Path
    temporary: Path
    backup: Path
    had_original: bool
    candles: tuple[Candle, ...]


@dataclass(frozen=True, slots=True)
class ParquetUpsertPlan:
    """Read-only result of validating and merging an upsert."""

    transaction_id: str
    partitions: tuple[PlannedPartition, ...]
    stored_count: int
    duplicate_count: int
    first_open_time: datetime | None
    last_open_time: datetime | None
    candle_count: int
    checksum: str
    version_algorithm: str = RAW_DATASET_VERSION_ALGORITHM
    partition_integrity_entries: tuple[RawPartitionIntegrityEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class RawPartitionIntegritySnapshot:
    """One-pass offline projection of every committed RAW partition."""

    entries: tuple[RawPartitionIntegrityEntry, ...]
    current_version: str
    legacy_version: str
    first_open_time: datetime | None
    last_open_time: datetime | None
    candle_count: int


@dataclass(frozen=True, slots=True)
class PartitionChange:
    """One promoted partition retained until transaction cleanup."""

    target: Path
    backup: Path
    had_original: bool


@dataclass(slots=True)
class ParquetWriteReceipt:
    """Compatibility wrapper for a compensatable local upsert."""

    changes: tuple[PartitionChange, ...]
    stored_count: int
    duplicate_count: int

    def commit(self) -> None:
        for change in self.changes:
            change.backup.unlink(missing_ok=True)
            fsync_directory(change.target.parent)

    def rollback(self) -> None:
        for change in reversed(self.changes):
            _rollback_artifact(change.target, change.backup, change.had_original)


class ParquetCandleStore:
    """Read and plan bounded monthly Parquet partitions under ADT_DATA_DIR."""

    def __init__(self, data_dir: Path) -> None:
        self._root = market_root(data_dir)

    @property
    def root(self) -> Path:
        return self._root

    def dataset_root(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
    ) -> Path:
        """Build a collision-free reversible path and verify containment."""
        candidate = (
            self._root
            / f"exchange={quote(exchange.value, safe='')}"
            / f"market={quote(market_type.value, safe='')}"
            / f"base={quote(pair.base, safe='')}"
            / f"quote={quote(pair.quote, safe='')}"
            / f"timeframe={quote(timeframe.code, safe='')}"
        )
        return ensure_safe_path(self._root, candidate)

    def logical_version(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
    ) -> str:
        """Calculate the composable logical version of the persisted RAW dataset."""
        dataset = self.dataset_root(exchange, market_type, pair, timeframe)
        partitions: list[tuple[str, str]] = []
        if dataset.exists():
            for path in sorted(dataset.glob("year=*/month=*/candles.parquet")):
                rows = self._read_file(
                    path,
                    timeframe=timeframe,
                    expected_exchange=exchange,
                    expected_market_type=market_type,
                    expected_pair=pair,
                )
                partitions.append(
                    (
                        path.relative_to(self._root).as_posix(),
                        raw_partition_logical_checksum(rows),
                    )
                )
        return compose_raw_dataset_version(partitions)

    def plan_upsert(
        self,
        candles: tuple[Candle, ...],
        *,
        transaction_id: str,
    ) -> ParquetUpsertPlan:
        """Validate every value and reject all conflicting duplicate keys."""
        if not candles:
            first, last, count = None, None, 0
            return ParquetUpsertPlan(
                transaction_id,
                (),
                0,
                0,
                first,
                last,
                count,
                compose_raw_dataset_version(()),
                partition_integrity_entries=(),
            )

        identity = _candle_identity(candles[0])
        unique_incoming: dict[tuple[Exchange, str, str, datetime], Candle] = {}
        duplicate_count = 0
        for candle in candles:
            validate_candle_serialization(candle)
            if _candle_identity(candle) != identity:
                raise MarketDataInconsistencyError("O lote mistura identidades de dataset.")
            existing_in_batch = unique_incoming.get(candle.key)
            if existing_in_batch is None:
                unique_incoming[candle.key] = candle
            elif existing_in_batch == candle:
                duplicate_count += 1
            else:
                raise MarketDataInconsistencyError(
                    "O lote contém candles conflitantes para a mesma chave."
                )

        grouped: dict[tuple[int, int], list[Candle]] = defaultdict(list)
        for candle in unique_incoming.values():
            grouped[(candle.open_time.year, candle.open_time.month)].append(candle)

        planned: list[PlannedPartition] = []
        stored_count = 0
        pair = TradingPair.parse(candles[0].symbol)
        for (year, month), incoming in sorted(grouped.items()):
            target = self._partition_target(
                candles[0].exchange,
                candles[0].market_type,
                pair,
                candles[0].timeframe,
                year,
                month,
            )
            existing_rows = self._read_file(
                target,
                timeframe=candles[0].timeframe,
                expected_exchange=candles[0].exchange,
                expected_market_type=candles[0].market_type,
                expected_pair=pair,
            )
            merged: dict[tuple[Exchange, str, str, datetime], Candle] = {}
            for item in existing_rows:
                if item.key in merged:
                    raise MarketDataInconsistencyError(
                        "A partição existente contém uma chave duplicada."
                    )
                merged[item.key] = item
            new_rows = 0
            for candle in incoming:
                persisted = merged.get(candle.key)
                if persisted is None:
                    merged[candle.key] = candle
                    new_rows += 1
                elif persisted == candle:
                    duplicate_count += 1
                else:
                    raise MarketDataInconsistencyError(
                        "O dataset já possui conteúdo diferente para a mesma chave."
                    )
            if new_rows == 0:
                continue
            ordered = tuple(sorted(merged.values(), key=lambda item: item.open_time))
            temporary = target.with_name(f".{target.name}.tmp-{transaction_id}")
            backup = target.with_name(f".{target.name}.bak-{transaction_id}")
            planned.append(
                PlannedPartition(
                    target=ensure_safe_path(self._root, target),
                    temporary=ensure_safe_path(self._root, temporary),
                    backup=ensure_safe_path(self._root, backup),
                    had_original=target.exists(),
                    candles=ordered,
                )
            )
            stored_count += new_rows

        future = self._future_dataset_integrity(
            candles[0].exchange,
            candles[0].market_type,
            pair,
            candles[0].timeframe,
            unique_incoming,
        )
        return ParquetUpsertPlan(
            transaction_id=transaction_id,
            partitions=tuple(planned),
            stored_count=stored_count,
            duplicate_count=duplicate_count,
            first_open_time=future.first_open_time,
            last_open_time=future.last_open_time,
            candle_count=future.candle_count,
            checksum=future.current_version,
            partition_integrity_entries=future.entries,
        )

    def _future_dataset_integrity(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
        incoming: dict[tuple[Exchange, str, str, datetime], Candle],
    ) -> RawPartitionIntegritySnapshot:
        logical: dict[tuple[Exchange, str, str, datetime], Candle] = {}
        dataset = self.dataset_root(exchange, market_type, pair, timeframe)
        if dataset.exists():
            for path in sorted(dataset.glob("year=*/month=*/candles.parquet")):
                for candle in self._read_file(
                    path,
                    timeframe=timeframe,
                    expected_exchange=exchange,
                    expected_market_type=market_type,
                    expected_pair=pair,
                ):
                    if candle.key in logical:
                        raise MarketDataInconsistencyError(
                            "O dataset contém chave duplicada entre partições."
                        )
                    logical[candle.key] = candle
        logical.update(incoming)
        grouped: dict[tuple[int, int], list[Candle]] = defaultdict(list)
        for candle in logical.values():
            grouped[(candle.open_time.year, candle.open_time.month)].append(candle)
        entries: list[RawPartitionIntegrityEntry] = []
        legacy = hashlib.sha256()
        first: datetime | None = None
        last: datetime | None = None
        count = 0
        for (year, month), rows in sorted(grouped.items()):
            ordered = tuple(sorted(rows, key=lambda item: item.open_time))
            target = self._partition_target(
                exchange,
                market_type,
                pair,
                timeframe,
                year,
                month,
            )
            entries.append(
                RawPartitionIntegrityEntry(
                    relative_path=target.relative_to(self._root).as_posix(),
                    checksum=raw_partition_logical_checksum(ordered),
                )
            )
            for candle in ordered:
                legacy.update(_canonical_candle_bytes(candle))
                first = candle.open_time if first is None else min(first, candle.open_time)
                last = candle.open_time if last is None else max(last, candle.open_time)
                count += 1
        canonical_entries = tuple(entries)
        return RawPartitionIntegritySnapshot(
            entries=canonical_entries,
            current_version=compose_raw_dataset_version(
                (entry.relative_path, entry.checksum) for entry in canonical_entries
            ),
            legacy_version=legacy.hexdigest(),
            first_open_time=first,
            last_open_time=last,
            candle_count=count,
        )

    def partition_integrity_snapshot(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
    ) -> RawPartitionIntegritySnapshot:
        """Read every RAW partition once for an explicit offline integrity operation."""
        entries: list[RawPartitionIntegrityEntry] = []
        legacy = hashlib.sha256()
        first: datetime | None = None
        last: datetime | None = None
        count = 0
        for path in self.partition_paths(exchange, market_type, pair, timeframe):
            rows = self._read_file(
                path,
                timeframe=timeframe,
                expected_exchange=exchange,
                expected_market_type=market_type,
                expected_pair=pair,
            )
            entries.append(
                RawPartitionIntegrityEntry(
                    relative_path=path.relative_to(self._root).as_posix(),
                    checksum=raw_partition_logical_checksum(rows),
                )
            )
            for candle in rows:
                legacy.update(_canonical_candle_bytes(candle))
                first = candle.open_time if first is None else min(first, candle.open_time)
                last = candle.open_time if last is None else max(last, candle.open_time)
                count += 1
        canonical_entries = tuple(entries)
        return RawPartitionIntegritySnapshot(
            entries=canonical_entries,
            current_version=compose_raw_dataset_version(
                (entry.relative_path, entry.checksum) for entry in canonical_entries
            ),
            legacy_version=legacy.hexdigest(),
            first_open_time=first,
            last_open_time=last,
            candle_count=count,
        )

    def prepare_files(self, plan: ParquetUpsertPlan) -> None:
        """Write and fsync every planned temporary Parquet file."""
        prepared: list[Path] = []
        try:
            for partition in plan.partitions:
                self.prepare_partition(partition)
                prepared.append(partition.temporary)
        except Exception as error:
            for path in prepared:
                path.unlink(missing_ok=True)
            if isinstance(error, (MarketDataStorageError, MarketDataInconsistencyError)):
                raise
            raise MarketDataStorageError() from error

    def prepare_partition(self, partition: PlannedPartition) -> None:
        """Write and fsync one temporary partition without promoting it."""
        ensure_safe_path(self._root, partition.target.parent)
        partition.target.parent.mkdir(parents=True, exist_ok=True)
        ensure_safe_path(self._root, partition.target.parent)
        table = _candles_to_table(partition.candles)
        try:
            pq.write_table(table, partition.temporary, compression="zstd")
            with partition.temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            if not pq.ParquetFile(partition.temporary).schema_arrow.equals(PARQUET_SCHEMA):
                raise MarketDataStorageError("O arquivo preparado possui schema inválido.")
            fsync_directory(partition.target.parent)
        except Exception as error:
            partition.temporary.unlink(missing_ok=True)
            if isinstance(error, (MarketDataStorageError, MarketDataInconsistencyError)):
                raise
            raise MarketDataStorageError() from error

    def promote_partition(self, partition: PlannedPartition) -> PartitionChange:
        """Promote one prepared file and restore its original on every error."""
        _replace_partition(
            partition.target,
            partition.temporary,
            partition.backup,
            partition.had_original,
        )
        return PartitionChange(partition.target, partition.backup, partition.had_original)

    def upsert(self, candles: tuple[Candle, ...]) -> ParquetWriteReceipt:
        """Perform a local compensatable upsert; service ingestion uses the journal."""
        plan = self.plan_upsert(candles, transaction_id=uuid4().hex)
        self.prepare_files(plan)
        changes: list[PartitionChange] = []
        try:
            for partition in plan.partitions:
                changes.append(self.promote_partition(partition))
        except Exception:
            ParquetWriteReceipt(tuple(changes), plan.stored_count, plan.duplicate_count).rollback()
            raise
        return ParquetWriteReceipt(
            tuple(changes),
            stored_count=plan.stored_count,
            duplicate_count=plan.duplicate_count,
        )

    def read(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
        data_range: DataRange,
    ) -> tuple[Candle, ...]:
        """Read only intersecting partitions and enforce path/row identity."""
        candles: list[Candle] = []
        for path in self._partition_paths(
            exchange, market_type, pair, timeframe, data_range.start, data_range.end
        ):
            candles.extend(
                self._read_file(
                    path,
                    timeframe=timeframe,
                    expected_exchange=exchange,
                    expected_market_type=market_type,
                    expected_pair=pair,
                )
            )
        return tuple(
            candle
            for candle in sorted(candles, key=lambda item: item.open_time)
            if data_range.start <= candle.open_time < data_range.end
        )

    def read_verified(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
        data_range: DataRange,
        expected_checksums: dict[str, str],
    ) -> tuple[Candle, ...]:
        """Read, authenticate and return the same rows from intersecting partitions."""
        candles: list[Candle] = []
        for path in self._partition_paths(
            exchange, market_type, pair, timeframe, data_range.start, data_range.end
        ):
            relative = path.relative_to(self._root).as_posix()
            expected = expected_checksums.get(relative)
            if expected is None:
                raise MarketDataInconsistencyError(
                    "A partição RAW solicitada não possui prova de integridade."
                )
            rows = self._read_file(
                path,
                timeframe=timeframe,
                expected_exchange=exchange,
                expected_market_type=market_type,
                expected_pair=pair,
            )
            if raw_partition_logical_checksum(rows) != expected:
                raise MarketDataInconsistencyError(
                    "O conteúdo da partição RAW diverge do manifesto catalogado."
                )
            candles.extend(rows)
        return tuple(
            candle
            for candle in sorted(candles, key=lambda item: item.open_time)
            if data_range.start <= candle.open_time < data_range.end
        )

    def first_last_count(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
    ) -> tuple[datetime | None, datetime | None, int]:
        """Discover boundaries while validating every partition identity."""
        dataset = self.dataset_root(exchange, market_type, pair, timeframe)
        first: datetime | None = None
        last: datetime | None = None
        count = 0
        if not dataset.exists():
            return first, last, count
        for path in sorted(dataset.glob("year=*/month=*/candles.parquet")):
            ensure_safe_path(self._root, path)
            rows = self._read_file(
                path,
                timeframe=timeframe,
                expected_exchange=exchange,
                expected_market_type=market_type,
                expected_pair=pair,
            )
            if not rows:
                continue
            first = min(first, rows[0].open_time) if first is not None else rows[0].open_time
            last = max(last, rows[-1].open_time) if last is not None else rows[-1].open_time
            count += len(rows)
        return first, last, count

    def verify_schema(self, path: Path) -> bool:
        safe_path = ensure_safe_path(self._root, path)
        return safe_path.is_file() and pq.ParquetFile(safe_path).schema_arrow.equals(PARQUET_SCHEMA)

    def partition_paths(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
        data_range: DataRange | None = None,
    ) -> tuple[Path, ...]:
        """List safe monthly partitions without loading their rows."""
        if data_range is not None:
            return self._partition_paths(
                exchange,
                market_type,
                pair,
                timeframe,
                data_range.start,
                data_range.end,
            )
        dataset = self.dataset_root(exchange, market_type, pair, timeframe)
        if not dataset.exists():
            return ()
        return tuple(
            ensure_safe_path(self._root, path)
            for path in sorted(dataset.glob("year=*/month=*/candles.parquet"))
        )

    def read_partition(
        self,
        path: Path,
        *,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
    ) -> tuple[Candle, ...]:
        """Read and fully validate one explicit partition."""
        return self._read_file(
            path,
            timeframe=timeframe,
            expected_exchange=exchange,
            expected_market_type=market_type,
            expected_pair=pair,
        )

    def _partition_target(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
        year: int,
        month: int,
    ) -> Path:
        candidate = (
            self.dataset_root(exchange, market_type, pair, timeframe)
            / f"year={year:04d}"
            / f"month={month:02d}"
            / "candles.parquet"
        )
        return ensure_safe_path(self._root, candidate)

    def _read_file(
        self,
        path: Path,
        *,
        timeframe: Timeframe,
        expected_exchange: Exchange,
        expected_market_type: MarketType,
        expected_pair: TradingPair,
    ) -> tuple[Candle, ...]:
        safe_path = ensure_safe_path(self._root, path)
        if not safe_path.exists():
            return ()
        table = pq.ParquetFile(safe_path).read()
        if not table.schema.equals(PARQUET_SCHEMA):
            raise MarketDataStorageError("O arquivo Parquet possui schema divergente.")
        partition_year, partition_month = _partition_year_month(safe_path)
        rows: list[Candle] = []
        seen_keys: set[tuple[Exchange, str, str, datetime]] = set()
        previous_open_time: datetime | None = None
        for raw in table.to_pylist():
            if (
                raw.get("exchange") != expected_exchange.value
                or raw.get("market_type") != expected_market_type.value
                or raw.get("symbol") != expected_pair.symbol
                or raw.get("timeframe") != timeframe.code
            ):
                raise MarketDataInconsistencyError(
                    "A identidade interna do Parquet diverge do caminho solicitado."
                )
            candle = _row_to_candle(raw, timeframe)
            validate_candle_serialization(candle)
            if candle.key in seen_keys:
                raise MarketDataInconsistencyError("A partição Parquet contém uma chave duplicada.")
            if previous_open_time is not None and candle.open_time <= previous_open_time:
                raise MarketDataInconsistencyError(
                    "A partição Parquet não está em ordem estritamente crescente."
                )
            if candle.open_time.year != partition_year or candle.open_time.month != partition_month:
                raise MarketDataInconsistencyError(
                    "Um candle não pertence ao ano e mês da partição."
                )
            seen_keys.add(candle.key)
            previous_open_time = candle.open_time
            rows.append(candle)
        return tuple(rows)

    def _partition_paths(
        self,
        exchange: Exchange,
        market_type: MarketType,
        pair: TradingPair,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[Path, ...]:
        dataset = self.dataset_root(exchange, market_type, pair, timeframe)
        cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
        paths: list[Path] = []
        while cursor < end:
            path = (
                dataset
                / f"year={cursor.year:04d}"
                / f"month={cursor.month:02d}"
                / "candles.parquet"
            )
            path = ensure_safe_path(self._root, path)
            if path.exists():
                paths.append(path)
            cursor = _next_month(cursor)
        return tuple(paths)


def validate_candle_serialization(candle: Candle, *, require_closed: bool = True) -> None:
    """Reject values PyArrow would otherwise rescale or truncate."""
    if require_closed and not candle.is_closed:
        raise MarketDataInconsistencyError("Candles abertos não podem ser persistidos.")
    for value in (
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.volume,
        candle.quote_volume,
    ):
        if value is not None:
            _validate_decimal128_38_18(value)
    datetime_to_epoch_milliseconds(candle.open_time, field_name="open_time")
    datetime_to_epoch_milliseconds(candle.close_time, field_name="close_time")


def canonical_candle_bytes(candle: Candle) -> bytes:
    """Expose the stable logical encoding used by dataset checksums."""
    return _canonical_candle_bytes(candle)


def raw_partition_logical_checksum(candles: Iterable[Candle]) -> str:
    """Hash one canonical partition independently from its Parquet encoding."""
    digest = hashlib.sha256()
    previous: datetime | None = None
    for candle in sorted(candles, key=lambda item: item.open_time):
        if previous is not None and candle.open_time <= previous:
            raise MarketDataInconsistencyError(
                "A partição RAW possui duplicata ou ordem lógica inválida."
            )
        digest.update(_canonical_candle_bytes(candle))
        previous = candle.open_time
    return digest.hexdigest()


def compose_raw_dataset_version(partitions: Iterable[tuple[str, str]]) -> str:
    """Compose ordered canonical partition hashes into the RAW logical version."""
    entries = tuple(partitions)
    relative_paths = [relative for relative, _checksum in entries]
    if len(relative_paths) != len(set(relative_paths)):
        raise MarketDataInconsistencyError("A versão RAW contém partições duplicadas.")
    digest = hashlib.sha256()
    for relative, checksum in sorted(entries):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(checksum.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_decimal128_38_18(value: Decimal) -> None:
    if not value.is_finite():
        raise MarketDataInconsistencyError("Decimal não finito não pode ser persistido.")
    sign, raw_digits, exponent = value.as_tuple()
    del sign
    if not isinstance(exponent, int):
        raise MarketDataInconsistencyError("Decimal não finito não pode ser persistido.")
    digits = list(raw_digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent < -18:
        raise MarketDataInconsistencyError("Decimal excede a escala 18 sem representação exata.")
    if all(digit == 0 for digit in digits):
        return
    scaled_digits = len(digits) + exponent + 18
    if scaled_digits > 38:
        raise MarketDataInconsistencyError("Decimal excede a precisão decimal128(38,18).")


def _replace_partition(
    target: Path,
    temporary: Path,
    backup: Path,
    had_original: bool,
) -> None:
    """Replace one target and restore backup even after the new target exists."""
    try:
        if had_original:
            os.replace(target, backup)
            fsync_directory(target.parent)
        os.replace(temporary, target)
        fsync_directory(target.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        if backup.exists():
            target.unlink(missing_ok=True)
            os.replace(backup, target)
            fsync_directory(target.parent)
        elif not had_original:
            target.unlink(missing_ok=True)
            fsync_directory(target.parent)
        raise


def _rollback_artifact(target: Path, backup: Path, had_original: bool) -> None:
    if backup.exists():
        target.unlink(missing_ok=True)
        os.replace(backup, target)
    elif not had_original:
        target.unlink(missing_ok=True)
    if target.parent.exists():
        fsync_directory(target.parent)


def _candles_to_table(candles: tuple[Candle, ...]) -> pa.Table:
    for candle in candles:
        validate_candle_serialization(candle)
    rows = [
        {
            "exchange": candle.exchange.value,
            "market_type": candle.market_type.value,
            "symbol": candle.symbol,
            "timeframe": candle.timeframe.code,
            "open_time": candle.open_time,
            "close_time": candle.close_time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "quote_volume": candle.quote_volume,
            "trade_count": candle.trade_count,
            "is_closed": candle.is_closed,
            "source": candle.source,
        }
        for candle in candles
    ]
    return pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA)


def _row_to_candle(row: dict[str, object], timeframe: Timeframe) -> Candle:
    return Candle(
        exchange=Exchange(str(row["exchange"])),
        market_type=MarketType(str(row["market_type"])),
        symbol=str(row["symbol"]),
        timeframe=timeframe,
        open_time=_require_datetime(row["open_time"]),
        close_time=_require_datetime(row["close_time"]),
        open=_require_decimal(row["open"]),
        high=_require_decimal(row["high"]),
        low=_require_decimal(row["low"]),
        close=_require_decimal(row["close"]),
        volume=_require_decimal(row["volume"]),
        quote_volume=(
            _require_decimal(row["quote_volume"]) if row["quote_volume"] is not None else None
        ),
        trade_count=_require_int(row["trade_count"]) if row["trade_count"] is not None else None,
        is_closed=_require_bool(row["is_closed"]),
        source=str(row["source"]),
    )


def _canonical_candle_bytes(candle: Candle) -> bytes:
    values = (
        candle.exchange.value,
        candle.market_type.value,
        candle.symbol,
        candle.timeframe.code,
        candle.open_time.isoformat(),
        candle.close_time.isoformat(),
        format(candle.open, ".18f"),
        format(candle.high, ".18f"),
        format(candle.low, ".18f"),
        format(candle.close, ".18f"),
        format(candle.volume, ".18f"),
        format(candle.quote_volume, ".18f") if candle.quote_volume is not None else "",
        str(candle.trade_count) if candle.trade_count is not None else "",
        "1" if candle.is_closed else "0",
        candle.source,
    )
    return ("\x1f".join(values) + "\n").encode()


def _candle_identity(candle: Candle) -> tuple[Exchange, MarketType, str, str]:
    return candle.exchange, candle.market_type, candle.symbol, candle.timeframe.code


def _require_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise MarketDataStorageError()
    normalized = value.astimezone(UTC)
    datetime_to_epoch_milliseconds(normalized, field_name="timestamp")
    return normalized


def _require_decimal(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise MarketDataStorageError()
    _validate_decimal128_38_18(value)
    return value


def _require_int(value: object) -> int:
    if not isinstance(value, int):
        raise MarketDataStorageError()
    return value


def _require_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise MarketDataStorageError()
    return value


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=UTC)
    return datetime(value.year, value.month + 1, 1, tzinfo=UTC)


def _partition_year_month(path: Path) -> tuple[int, int]:
    try:
        year_name = path.parent.parent.name
        month_name = path.parent.name
        if not year_name.startswith("year=") or not month_name.startswith("month="):
            raise ValueError
        year = int(year_name.removeprefix("year="))
        month = int(month_name.removeprefix("month="))
    except ValueError:
        raise MarketDataStorageError("O caminho da partição Parquet é inválido.") from None
    if year < 1 or not 1 <= month <= 12:
        raise MarketDataStorageError("O caminho da partição Parquet é inválido.")
    return year, month
