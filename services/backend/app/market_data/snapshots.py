"""Immutable hard-link snapshots and lazy future-backtest dataset reader."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.market_data.datasets import (
    DatasetManifest,
    DatasetSnapshot,
    DatasetState,
    GapPolicy,
    PartitionSummary,
    ResamplingPlan,
)
from app.market_data.derived import DerivedDatasetService, DerivedDatasetStore
from app.market_data.domain import Candle, DataRange, TradingPair
from app.market_data.errors import MarketDataInconsistencyError, MarketDataStorageError
from app.market_data.filesystem import ensure_safe_path, fsync_directory
from app.market_data.locks import DatasetLockManager
from app.market_data.storage import ParquetCandleStore, canonical_candle_bytes
from app.market_data.timeframes import get_timeframe


class DatasetSnapshotService:
    def __init__(
        self,
        *,
        data_dir: Path,
        derived_store: DerivedDatasetStore,
        derived_service: DerivedDatasetService,
        lock_manager: DatasetLockManager,
        max_partitions: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = ParquetCandleStore(data_dir)
        self._market = self._store.root
        self._root = ensure_safe_path(self._market, self._market / "snapshots")
        self._derived_store = derived_store
        self._derived_service = derived_service
        self._locks = lock_manager
        self._max_partitions = max_partitions
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(self, plan: ResamplingPlan, data_range: DataRange) -> DatasetSnapshot:
        target_timeframe = get_timeframe(plan.target.timeframe)
        if not target_timeframe.validate_open_time(
            data_range.start
        ) or not target_timeframe.validate_open_time(data_range.end):
            raise MarketDataInconsistencyError("O intervalo do snapshot está desalinhado.")
        raw_key = (
            f"{plan.source.exchange.value}:{plan.source.market_type.value}:"
            f"{plan.source.symbol}:{plan.source.timeframe}"
        )
        snapshot_key = (
            "snapshot-plan:"
            + hashlib.sha256(
                (
                    plan.target.key + data_range.start.isoformat() + data_range.end.isoformat()
                ).encode()
            ).hexdigest()
        )
        with self._locks.acquire_many((raw_key, plan.target.key, snapshot_key)) as leases:
            derived_leases = tuple(lease for lease in leases if lease.dataset_key != snapshot_key)
            self._derived_service.recover_derived_dataset(raw_key, plan.target.key, derived_leases)
            manifest = self._derived_service._verify_unlocked(plan)
            if manifest.state is not DatasetState.COMPLETE:
                raise MarketDataInconsistencyError(
                    "Somente dataset derivado COMPLETE pode gerar snapshot."
                )
            snapshot_id = _snapshot_id(manifest, data_range)
            _validate_snapshot_coverage(
                manifest,
                data_range,
                target_timeframe.duration,
            )
            selected = tuple(
                item
                for item in manifest.partitions
                if _partition_intersects(item.year, item.month, data_range)
            )
            if not selected:
                raise MarketDataInconsistencyError("O snapshot não possui partições.")
            if len(selected) > self._max_partitions:
                raise MarketDataInconsistencyError("O snapshot excede o limite de partições.")
            if manifest.gap_policy is GapPolicy.STRICT:
                self._validate_strict_range(manifest, selected, data_range)
            target = ensure_safe_path(self._market, self._root / snapshot_id)
            metadata_path = ensure_safe_path(self._market, target / "snapshot.json")
            if metadata_path.exists():
                existing = _load_snapshot(metadata_path, self._market)
                if (
                    existing.snapshot_id != snapshot_id
                    or existing.dataset_key != manifest.target_dataset_key
                    or existing.dataset_version != manifest.target_version
                    or existing.checksum != manifest.target_checksum
                    or existing.data_range != data_range
                ):
                    raise MarketDataInconsistencyError(
                        "O snapshot idempotente diverge da solicitação."
                    )
                self._verify_existing(existing)
                return existing
            staging = ensure_safe_path(
                self._market,
                self._root / f".snapshot-{snapshot_id}.tmp",
            )
            staging.mkdir(parents=True, exist_ok=False)
            linked: list[str] = []
            try:
                for summary in selected:
                    source = ensure_safe_path(
                        self._market,
                        self._market / summary.relative_path,
                    )
                    destination = ensure_safe_path(
                        self._market,
                        staging
                        / "partitions"
                        / f"year={summary.year:04d}"
                        / f"month={summary.month:02d}"
                        / "candles.parquet",
                    )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.link(source, destination)
                    linked.append(destination.relative_to(staging).as_posix())
                    fsync_directory(destination.parent)
                source_manifest = self._derived_store.manifest_path(plan)
                manifest_copy = ensure_safe_path(
                    self._market,
                    staging / "dataset-manifest.json",
                )
                os.link(source_manifest, manifest_copy)
                snapshot = DatasetSnapshot(
                    snapshot_id=snapshot_id,
                    dataset_key=manifest.target_dataset_key,
                    dataset_version=manifest.target_version,
                    checksum=manifest.target_checksum,
                    data_range=data_range,
                    partitions=tuple(linked),
                    manifest_path="dataset-manifest.json",
                    created_at=self._clock().astimezone(UTC).isoformat(),
                )
                _write_snapshot(staging / "snapshot.json", snapshot)
                self._root.mkdir(parents=True, exist_ok=True)
                os.replace(staging, target)
                fsync_directory(self._root)
                return snapshot
            except Exception:
                _remove_staging_tree(staging)
                raise

    def _verify_existing(self, snapshot: DatasetSnapshot) -> None:
        reader = MarketDatasetReader(self._market.parent)
        reader.open_snapshot(snapshot.snapshot_id)
        candles = tuple(reader.iter_candles())
        if not candles:
            raise MarketDataInconsistencyError("O snapshot idempotente está incompleto.")

    def _validate_strict_range(
        self,
        manifest: DatasetManifest,
        selected: tuple[PartitionSummary, ...],
        data_range: DataRange,
    ) -> None:
        pair = TradingPair.parse(manifest.identity.symbol)
        timeframe = get_timeframe(manifest.target_timeframe)
        expected = data_range.start
        for summary in selected:
            path = ensure_safe_path(self._market, self._market / summary.relative_path)
            for candle in self._store.read_partition(
                path,
                exchange=manifest.identity.exchange,
                market_type=manifest.identity.market_type,
                pair=pair,
                timeframe=timeframe,
            ):
                if not data_range.start <= candle.open_time < data_range.end:
                    continue
                if candle.open_time != expected:
                    raise MarketDataInconsistencyError(
                        "O snapshot STRICT contém intervalo incompleto."
                    )
                expected += timeframe.duration
        if expected != data_range.end:
            raise MarketDataInconsistencyError("O snapshot STRICT contém intervalo incompleto.")

    def inspect(self, snapshot_id: str) -> DatasetSnapshot:
        return _load_snapshot(
            ensure_safe_path(self._market, self._root / snapshot_id / "snapshot.json"),
            self._market,
        )

    def verify(self, snapshot_id: str) -> DatasetSnapshot:
        snapshot = self.inspect(snapshot_id)
        reader = MarketDatasetReader(self._market.parent)
        reader.open_snapshot(snapshot_id)
        tuple(reader.iter_candles())
        return snapshot


class MarketDatasetReader:
    """Lazy immutable-snapshot reader; no backtest logic lives here."""

    def __init__(self, data_dir: Path) -> None:
        self._store = ParquetCandleStore(data_dir)
        self._root = ensure_safe_path(self._store.root, self._store.root / "snapshots")
        self._snapshot: DatasetSnapshot | None = None
        self._metadata_path: Path | None = None
        self._metadata_checksum: str | None = None
        self._manifest_path: Path | None = None
        self._manifest_checksum: str | None = None
        self._manifest: DatasetManifest | None = None

    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        metadata = ensure_safe_path(
            self._store.root,
            self._root / snapshot_id / "snapshot.json",
        )
        snapshot = _load_snapshot(metadata, self._store.root)
        manifest_path = ensure_safe_path(
            self._store.root,
            metadata.parent / snapshot.manifest_path,
        )
        from app.market_data.derived import _decode_manifest

        try:
            envelope = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_manifest = envelope["manifest"]
            manifest_checksum = envelope["checksum"]
            if (
                not isinstance(manifest_checksum, str)
                or hashlib.sha256(_canonical_json(raw_manifest)).hexdigest() != manifest_checksum
            ):
                raise ValueError
            manifest = _decode_manifest(raw_manifest)
        except (OSError, ValueError, KeyError, TypeError):
            raise MarketDataStorageError("O manifest do snapshot é inválido.") from None
        if (
            snapshot.snapshot_id != snapshot_id
            or snapshot.manifest_path != "dataset-manifest.json"
            or snapshot.snapshot_id != _snapshot_id(manifest, snapshot.data_range)
            or manifest.state is not DatasetState.COMPLETE
            or manifest.target_dataset_key != snapshot.dataset_key
            or manifest.target_version != snapshot.dataset_version
            or manifest.target_checksum != snapshot.checksum
        ):
            raise MarketDataInconsistencyError("O snapshot diverge do manifest.")
        expected_partitions = tuple(
            f"partitions/year={item.year:04d}/month={item.month:02d}/candles.parquet"
            for item in manifest.partitions
            if _partition_intersects(item.year, item.month, snapshot.data_range)
        )
        if snapshot.partitions != expected_partitions:
            raise MarketDataInconsistencyError("As partições do snapshot estão incompletas.")
        self._snapshot = snapshot
        self._metadata_path = metadata
        self._metadata_checksum = _file_checksum(metadata)
        self._manifest_path = manifest_path
        self._manifest_checksum = _file_checksum(manifest_path)
        self._manifest = manifest
        return snapshot

    def iter_candles(self) -> Iterator[Candle]:
        snapshot, metadata, expected_metadata_hash, manifest = self._opened()
        if self._manifest_path is None or self._manifest_checksum is None:
            raise MarketDataInconsistencyError("O manifest do snapshot não foi aberto.")
        pair = TradingPair.parse(manifest.identity.symbol)
        timeframe = get_timeframe(manifest.identity.timeframe)
        previous: Candle | None = None
        summaries = {(item.year, item.month): item for item in manifest.partitions}
        for relative in snapshot.partitions:
            if _file_checksum(metadata) != expected_metadata_hash:
                raise MarketDataInconsistencyError("O snapshot mudou durante a leitura.")
            if _file_checksum(self._manifest_path) != self._manifest_checksum:
                raise MarketDataInconsistencyError("O manifest mudou durante a leitura.")
            path = ensure_safe_path(self._store.root, metadata.parent / relative)
            rows = self._store.read_partition(
                path,
                exchange=manifest.identity.exchange,
                market_type=manifest.identity.market_type,
                pair=pair,
                timeframe=timeframe,
            )
            year, month = _path_year_month(path)
            summary = summaries.get((year, month))
            digest = hashlib.sha256()
            for candle in rows:
                digest.update(canonical_candle_bytes(candle))
            if summary is None or digest.hexdigest() != summary.checksum:
                raise MarketDataInconsistencyError("O checksum da partição do snapshot diverge.")
            for candle in rows:
                if not snapshot.data_range.start <= candle.open_time < snapshot.data_range.end:
                    continue
                if not candle.is_closed:
                    raise MarketDataInconsistencyError("Snapshot contém candle aberto.")
                if previous is not None and candle.open_time <= previous.open_time:
                    raise MarketDataInconsistencyError(
                        "Snapshot contém duplicata ou ordem inválida."
                    )
                previous = candle
                yield candle
        if _file_checksum(metadata) != expected_metadata_hash:
            raise MarketDataInconsistencyError("O snapshot mudou ao final da leitura.")
        if _file_checksum(self._manifest_path) != self._manifest_checksum:
            raise MarketDataInconsistencyError("O manifest mudou ao final da leitura.")

    def read(self, data_range: DataRange) -> tuple[Candle, ...]:
        return tuple(
            candle
            for candle in self.iter_candles()
            if data_range.start <= candle.open_time < data_range.end
        )

    def first_last_count(self) -> tuple[Candle | None, Candle | None, int]:
        first: Candle | None = None
        last: Candle | None = None
        count = 0
        for candle in self.iter_candles():
            first = first or candle
            last = candle
            count += 1
        return first, last, count

    def manifest(self) -> DatasetManifest:
        return self._opened()[3]

    def _opened(self) -> tuple[DatasetSnapshot, Path, str, DatasetManifest]:
        if (
            self._snapshot is None
            or self._metadata_path is None
            or self._metadata_checksum is None
            or self._manifest is None
        ):
            raise MarketDataInconsistencyError("Nenhum snapshot foi aberto.")
        return (
            self._snapshot,
            self._metadata_path,
            self._metadata_checksum,
            self._manifest,
        )


def _write_snapshot(path: Path, snapshot: DatasetSnapshot) -> None:
    payload = asdict(snapshot)
    payload["data_range"] = {
        "start": snapshot.data_range.start.isoformat(),
        "end": snapshot.data_range.end.isoformat(),
    }
    encoded = _canonical_json(payload)
    envelope = _canonical_json(
        {"snapshot": payload, "checksum": hashlib.sha256(encoded).hexdigest()}
    )
    with path.open("wb") as stream:
        stream.write(envelope)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def _load_snapshot(path: Path, root: Path) -> DatasetSnapshot:
    safe = ensure_safe_path(root, path)
    try:
        envelope = json.loads(safe.read_text(encoding="utf-8"))
        raw = envelope["snapshot"]
        checksum = envelope["checksum"]
        if hashlib.sha256(_canonical_json(raw)).hexdigest() != checksum:
            raise ValueError
        raw_range = raw["data_range"]
        return DatasetSnapshot(
            snapshot_id=raw["snapshot_id"],
            dataset_key=raw["dataset_key"],
            dataset_version=raw["dataset_version"],
            checksum=raw["checksum"],
            data_range=DataRange(
                datetime.fromisoformat(raw_range["start"]),
                datetime.fromisoformat(raw_range["end"]),
            ),
            partitions=tuple(raw["partitions"]),
            manifest_path=raw["manifest_path"],
            created_at=raw["created_at"],
        )
    except (OSError, ValueError, KeyError, TypeError):
        raise MarketDataStorageError("O snapshot é inválido.") from None


def _partition_intersects(year: int, month: int, data_range: DataRange) -> bool:
    start = datetime(year, month, 1, tzinfo=data_range.start.tzinfo)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=data_range.start.tzinfo)
    else:
        end = datetime(year, month + 1, 1, tzinfo=data_range.start.tzinfo)
    return start < data_range.end and end > data_range.start


def _validate_snapshot_coverage(
    manifest: DatasetManifest,
    data_range: DataRange,
    duration: timedelta,
) -> None:
    if manifest.first_open_time is None or manifest.last_open_time is None:
        raise MarketDataInconsistencyError("O dataset derivado não possui cobertura.")
    first = datetime.fromisoformat(manifest.first_open_time)
    end = datetime.fromisoformat(manifest.last_open_time) + duration
    if data_range.start < first or data_range.end > end:
        raise MarketDataInconsistencyError("O intervalo do snapshot excede a cobertura derivada.")


def _path_year_month(path: Path) -> tuple[int, int]:
    try:
        year = int(next(item for item in path.parts if item.startswith("year="))[5:])
        month = int(next(item for item in path.parts if item.startswith("month="))[6:])
    except (StopIteration, ValueError):
        raise MarketDataStorageError("O caminho do snapshot é inválido.") from None
    return year, month


def _uuid_from_digest(digest: str) -> str:
    return str(UUID(hex=digest[:32]))


def _snapshot_id(manifest: DatasetManifest, data_range: DataRange) -> str:
    logical_id = "|".join(
        (
            manifest.target_dataset_key,
            manifest.target_version,
            manifest.target_checksum,
            data_range.start.isoformat(),
            data_range.end.isoformat(),
        )
    )
    return _uuid_from_digest(hashlib.sha256(logical_id.encode()).hexdigest())


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _remove_staging_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink(missing_ok=True)
    root.rmdir()
