"""Transactional derived datasets, durable manifests and deterministic lineage."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote, unquote
from uuid import uuid4

import pyarrow.parquet as pq

from app.market_data.catalog import JsonMarketDataCatalog, dataset_key
from app.market_data.datasets import (
    DatasetIdentity,
    DatasetKind,
    DatasetLineage,
    DatasetManifest,
    DatasetState,
    GapPolicy,
    PartitionSummary,
    ResamplingPlan,
    ResamplingResult,
)
from app.market_data.domain import Candle, DataRange, Exchange, Instrument, MarketType, TradingPair
from app.market_data.errors import MarketDataInconsistencyError, MarketDataStorageError
from app.market_data.filesystem import ensure_safe_path, fsync_directory
from app.market_data.locks import DatasetLease, DatasetLockManager
from app.market_data.resampling import DeterministicCandleResampler
from app.market_data.storage import (
    PARQUET_SCHEMA,
    ParquetCandleStore,
    _candles_to_table,
    canonical_candle_bytes,
)
from app.market_data.timeframes import get_timeframe
from app.market_data.transaction import MarketDataTransactionCoordinator

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
FailureHook = Callable[[str], None]
RecoveryIdentityHook = Callable[[Path], None]


class DerivedDatasetStore:
    """Safe filesystem layout and manifest codec for derived datasets."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("derived"),
        manifest_schema_version: int = 1,
    ) -> None:
        market = ParquetCandleStore(data_dir).root
        if directory.is_absolute() or ".." in directory.parts or not directory.parts:
            raise MarketDataInconsistencyError("ADT_MARKET_DERIVED_DIR deve ser relativo e seguro.")
        self._market_root = market
        self._root = ensure_safe_path(market, market / directory)
        self._schema_version = manifest_schema_version

    @property
    def root(self) -> Path:
        return self._root

    @property
    def schema_version(self) -> int:
        return self._schema_version

    def dataset_root(self, plan: ResamplingPlan) -> Path:
        pair = TradingPair.parse(plan.target.symbol)
        candidate = (
            self._root
            / f"exchange={quote(plan.target.exchange.value, safe='')}"
            / f"market={quote(plan.target.market_type.value, safe='')}"
            / f"base={quote(pair.base, safe='')}"
            / f"quote={quote(pair.quote, safe='')}"
            / f"source_timeframe={quote(plan.source.timeframe, safe='')}"
            / f"timeframe={quote(plan.target.timeframe, safe='')}"
            / f"policy={quote(plan.gap_policy.value, safe='')}"
        )
        return ensure_safe_path(self._market_root, candidate)

    def manifest_path(self, plan: ResamplingPlan) -> Path:
        return ensure_safe_path(self._market_root, self.dataset_root(plan) / "manifest.json")

    def partition_path(self, plan: ResamplingPlan, year: int, month: int) -> Path:
        return ensure_safe_path(
            self._market_root,
            self.dataset_root(plan) / f"year={year:04d}" / f"month={month:02d}" / "candles.parquet",
        )

    def load_manifest(self, path: Path) -> DatasetManifest:
        safe = ensure_safe_path(self._market_root, path)
        try:
            envelope = json.loads(safe.read_text(encoding="utf-8"))
            raw = envelope["manifest"]
            checksum = envelope["checksum"]
        except (OSError, ValueError, KeyError, TypeError):
            raise MarketDataStorageError("O manifest derivado é inválido.") from None
        encoded = _canonical_json(raw)
        if not isinstance(checksum, str) or hashlib.sha256(encoded).hexdigest() != checksum:
            raise MarketDataStorageError("O checksum do manifest diverge.")
        manifest = _decode_manifest(raw)
        if manifest.schema_version != self._schema_version:
            raise MarketDataStorageError("A versão de schema do manifest é incompatível.")
        return manifest

    def write_manifest_atomic(self, path: Path, manifest: DatasetManifest) -> None:
        safe = ensure_safe_path(self._market_root, path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(manifest)
        encoded = _canonical_json(payload)
        envelope = _canonical_json(
            {"manifest": payload, "checksum": hashlib.sha256(encoded).hexdigest()}
        )
        temporary = ensure_safe_path(
            self._market_root,
            safe.with_name(f".{safe.name}.tmp-{uuid4().hex}"),
        )
        try:
            with temporary.open("wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, safe)
            fsync_directory(safe.parent)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise MarketDataStorageError() from None


class DerivedDatasetService:
    """Plan and atomically materialize deterministic derived datasets."""

    def __init__(
        self,
        *,
        raw_store: ParquetCandleStore,
        raw_catalog: JsonMarketDataCatalog,
        derived_store: DerivedDatasetStore,
        lock_manager: DatasetLockManager,
        max_source_candles: int,
        max_groups: int,
        clock: Clock | None = None,
        failure_hook: FailureHook | None = None,
        recovery_identity_hook: RecoveryIdentityHook | None = None,
    ) -> None:
        self._raw = raw_store
        self._catalog = raw_catalog
        self._derived = derived_store
        self._locks = lock_manager
        self._max_source = max_source_candles
        self._max_groups = max_groups
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resampler = DeterministicCandleResampler()
        self._failure_hook = failure_hook or (lambda _step: None)
        self._recovery_identity_hook = recovery_identity_hook or (lambda _path: None)
        self._raw_coordinator = MarketDataTransactionCoordinator(
            raw_store,
            raw_catalog,
            lock_manager=lock_manager,
        )

    def plan(
        self,
        instrument: Instrument,
        source_timeframe: str,
        target_timeframe: str,
        data_range: DataRange,
        *,
        gap_policy: GapPolicy = GapPolicy.STRICT,
    ) -> ResamplingPlan:
        source_tf = get_timeframe(source_timeframe)
        target_tf = get_timeframe(target_timeframe)
        group_size = self._resampler.validate_timeframes(source_tf, target_tf)
        if (
            not source_tf.validate_open_time(data_range.start)
            or not source_tf.validate_open_time(data_range.end)
            or not target_tf.validate_open_time(data_range.start)
            or not target_tf.validate_open_time(data_range.end)
        ):
            raise MarketDataInconsistencyError(
                "O intervalo deve estar alinhado aos dois timeframes."
            )
        source_count = (data_range.end - data_range.start) // source_tf.duration
        groups = (data_range.end - data_range.start) // target_tf.duration
        if source_count > self._max_source or groups > self._max_groups:
            raise MarketDataInconsistencyError("O plano de resampling excede o limite seguro.")
        raw_key = dataset_key(instrument, source_tf)
        with self._locks.acquire(raw_key) as raw_lease:
            self._raw_coordinator.recover_dataset(raw_key, raw_lease)
            metadata = self._catalog.get_dataset(raw_key)
        if metadata is None:
            raise MarketDataInconsistencyError("O dataset RAW de origem não existe.")
        source = DatasetIdentity(
            instrument.exchange,
            instrument.market_type,
            instrument.symbol,
            source_tf.code,
            DatasetKind.RAW,
            "canonical_parquet",
            "source_native",
            1,
        )
        target = DatasetIdentity(
            instrument.exchange,
            instrument.market_type,
            instrument.symbol,
            target_tf.code,
            DatasetKind.DERIVED,
            f"resample:{source.key}",
            gap_policy.value,
            self._derived.schema_version,
        )
        months = _months_in_range(data_range)
        return ResamplingPlan(
            source=source,
            target=target,
            data_range=data_range,
            source_dataset_version=metadata.version,
            source_checksum=metadata.version,
            source_candles=source_count,
            expected_groups=groups,
            estimated_partitions=len(months),
            group_size=group_size,
            gap_policy=gap_policy,
            calendar="CONTINUOUS_UTC_24_7",
        )

    def materialize(
        self,
        plan: ResamplingPlan,
        *,
        dry_run: bool = False,
    ) -> ResamplingResult:
        raw_key = (
            f"{plan.source.exchange.value}:{plan.source.market_type.value}:"
            f"{plan.source.symbol}:{plan.source.timeframe}"
        )
        derived_key = plan.target.key
        with self._locks.acquire_many((raw_key, derived_key)) as leases:
            return self._materialize_locked(plan, leases, dry_run=dry_run)

    def materialize_incremental(self, plan: ResamplingPlan) -> ResamplingResult:
        """Rebuild only the bounded span of RAW partitions whose checksums changed."""
        raw_key = _raw_key(plan)
        with self._locks.acquire_many((raw_key, plan.target.key)) as leases:
            self._recover_locked(raw_key, plan.target.key, leases)
            source_metadata = self._catalog.get_dataset(raw_key)
            if source_metadata is None or source_metadata.version != plan.source_dataset_version:
                raise MarketDataInconsistencyError("A versão RAW mudou desde a criação do plano.")
            return self._materialize_incremental_locked(plan, leases, source_metadata.version)

    def _materialize_incremental_locked(
        self,
        plan: ResamplingPlan,
        leases: tuple[DatasetLease, ...],
        source_version: str,
    ) -> ResamplingResult:
        manifest_path = self._derived.manifest_path(plan)
        if not manifest_path.exists():
            return self._materialize_locked(plan, leases)
        manifest = self._derived.load_manifest(manifest_path)
        known = {item.relative_path: item.checksum for item in manifest.source_partitions}
        current: dict[str, str] = {}
        changed_months: set[tuple[int, int]] = set()
        raw_paths = self._raw.partition_paths(
            plan.source.exchange,
            plan.source.market_type,
            TradingPair.parse(plan.source.symbol),
            get_timeframe(plan.source.timeframe),
            plan.data_range,
        )
        for path in raw_paths:
            relative = path.relative_to(self._raw.root).as_posix()
            checksum = _file_checksum(path)
            current[relative] = checksum
            if known.get(relative) != checksum:
                changed_months.add(_path_year_month(path))
        for summary in manifest.source_partitions:
            if (
                _summary_intersects(summary, plan.data_range)
                and summary.relative_path not in current
            ):
                changed_months.add((summary.year, summary.month))
        if (
            not changed_months
            and manifest.state is DatasetState.COMPLETE
            and manifest.source_dataset_version == plan.source_dataset_version
        ):
            return self._build_result(plan)
        if not changed_months:
            return self._materialize_locked(plan, leases)
        first_month = min(changed_months)
        last_month = max(changed_months)
        affected_start = max(plan.data_range.start, _month_start(*first_month))
        affected_end = min(plan.data_range.end, _next_month_start(*last_month))
        source_tf = get_timeframe(plan.source.timeframe)
        target_tf = get_timeframe(plan.target.timeframe)
        affected = replace(
            plan,
            data_range=DataRange(affected_start, affected_end),
            source_candles=(affected_end - affected_start) // source_tf.duration,
            expected_groups=(affected_end - affected_start) // target_tf.duration,
            estimated_partitions=len(changed_months),
        )
        result = self._build_result(affected)
        metadata = self._catalog.get_dataset(_raw_key(plan))
        if metadata is None or metadata.version != source_version:
            raise MarketDataInconsistencyError("A versão RAW mudou durante o resampling.")
        self._commit(affected, result, source_version)
        return result

    def _materialize_locked(
        self,
        plan: ResamplingPlan,
        leases: tuple[DatasetLease, ...],
        *,
        dry_run: bool = False,
    ) -> ResamplingResult:
        raw_key = _raw_key(plan)
        self._recover_locked(raw_key, plan.target.key, leases)
        source_metadata = self._catalog.get_dataset(raw_key)
        if source_metadata is None or source_metadata.version != plan.source_dataset_version:
            raise MarketDataInconsistencyError("A versão RAW mudou desde a criação do plano.")
        result = self._build_result(plan)
        metadata = self._catalog.get_dataset(raw_key)
        if metadata is None or metadata.version != source_metadata.version:
            raise MarketDataInconsistencyError("A versão RAW mudou durante o resampling.")
        if not dry_run:
            self._commit(plan, result, source_metadata.version)
        return result

    def verify(self, plan: ResamplingPlan) -> DatasetManifest:
        raw_key = (
            f"{plan.source.exchange.value}:{plan.source.market_type.value}:"
            f"{plan.source.symbol}:{plan.source.timeframe}"
        )
        with self._locks.acquire_many((raw_key, plan.target.key)) as leases:
            self._recover_locked(raw_key, plan.target.key, leases)
            return self._verify_unlocked(plan)

    def _verify_unlocked(self, plan: ResamplingPlan) -> DatasetManifest:
        """Verify while the caller holds the canonical RAW and DERIVED locks."""
        path = self._derived.manifest_path(plan)
        manifest = self._derived.load_manifest(path)
        if (
            manifest.identity != plan.target
            or manifest.schema_version != self._derived.schema_version
            or manifest.target_dataset_key != plan.target.key
            or manifest.source_dataset_key != _raw_key(plan)
            or manifest.source_timeframe != plan.source.timeframe
            or manifest.target_timeframe != plan.target.timeframe
            or manifest.gap_policy is not plan.gap_policy
            or manifest.algorithm != plan.algorithm
            or manifest.algorithm_version != plan.algorithm_version
            or manifest.lineage.source_dataset_key != manifest.source_dataset_key
            or manifest.lineage.source_dataset_version != manifest.source_dataset_version
            or manifest.lineage.source_checksum != manifest.source_checksum
        ):
            invalid = replace(manifest, state=DatasetState.INVALID)
            self._derived.write_manifest_atomic(path, invalid)
            return invalid
        metadata = self._catalog.get_dataset(manifest.source_dataset_key)
        if metadata is None or metadata.version != manifest.source_dataset_version:
            stale = replace(manifest, state=DatasetState.STALE)
            self._derived.write_manifest_atomic(path, stale)
            return stale
        for summary in manifest.source_partitions:
            source_path = ensure_safe_path(
                self._raw.root,
                self._raw.root / summary.relative_path,
            )
            if not source_path.exists() or _file_checksum(source_path) != summary.checksum:
                stale = replace(manifest, state=DatasetState.STALE)
                self._derived.write_manifest_atomic(path, stale)
                return stale
        count = 0
        first_open_time: str | None = None
        last_open_time: str | None = None
        pair = TradingPair.parse(plan.target.symbol)
        target_tf = get_timeframe(plan.target.timeframe)
        for summary in manifest.partitions:
            partition_path = ensure_safe_path(
                self._raw.root,
                self._raw.root / summary.relative_path,
            )
            if not partition_path.exists():
                invalid = replace(manifest, state=DatasetState.INVALID)
                self._derived.write_manifest_atomic(path, invalid)
                return invalid
            try:
                rows = self._raw.read_partition(
                    partition_path,
                    exchange=plan.target.exchange,
                    market_type=plan.target.market_type,
                    pair=pair,
                    timeframe=target_tf,
                )
            except Exception:
                invalid = replace(manifest, state=DatasetState.INVALID)
                self._derived.write_manifest_atomic(path, invalid)
                return invalid
            actual_first = rows[0].open_time.isoformat() if rows else None
            actual_last = rows[-1].open_time.isoformat() if rows else None
            if (
                len(rows) != summary.candle_count
                or actual_first != summary.first_open_time
                or actual_last != summary.last_open_time
            ):
                invalid = replace(manifest, state=DatasetState.INVALID)
                self._derived.write_manifest_atomic(path, invalid)
                return invalid
            partition_digest = hashlib.sha256()
            count += len(rows)
            first_open_time = first_open_time or actual_first
            last_open_time = actual_last or last_open_time
            for candle in rows:
                partition_digest.update(canonical_candle_bytes(candle))
            if partition_digest.hexdigest() != summary.checksum:
                invalid = replace(manifest, state=DatasetState.INVALID)
                self._derived.write_manifest_atomic(path, invalid)
                return invalid
        target_checksum = _dataset_checksum(manifest.partitions)
        if (
            count != manifest.candle_count
            or first_open_time != manifest.first_open_time
            or last_open_time != manifest.last_open_time
            or target_checksum != manifest.target_checksum
        ):
            invalid = replace(manifest, state=DatasetState.INVALID)
            self._derived.write_manifest_atomic(path, invalid)
            return invalid
        return manifest

    def recover(self) -> int:
        journal_dir = ensure_safe_path(
            self._raw.root,
            self._derived.root / ".transactions",
        )
        if not journal_dir.exists():
            return 0
        recovered = 0
        for path in sorted(journal_dir.glob("journal-*.json")):
            try:
                identity = _read_journal_identity(path)
            except MarketDataStorageError:
                if not path.exists():
                    continue
                raise
            self._recovery_identity_hook(path)
            keys = (identity[1], identity[2])
            with self._locks.acquire_many(keys) as leases:
                recovered += self._recover_path_locked(path, identity, leases)
        return recovered

    def recover_derived_dataset(
        self,
        raw_key: str,
        derived_key: str,
        leases: tuple[DatasetLease, ...],
    ) -> int:
        """Recover one exact derived dataset while its canonical locks are held."""
        self._validate_leases(raw_key, derived_key, leases)
        journal_dir = ensure_safe_path(self._raw.root, self._derived.root / ".transactions")
        if not journal_dir.exists():
            return 0
        recovered = 0
        for path in sorted(journal_dir.glob("journal-*.json")):
            try:
                identity = _read_journal_identity(path)
            except MarketDataStorageError:
                if not path.exists():
                    continue
                raise
            if identity[1:] == (raw_key, derived_key):
                recovered += self._recover_path_locked(path, identity, leases)
        return recovered

    def _recover_locked(
        self,
        raw_key: str,
        derived_key: str,
        leases: tuple[DatasetLease, ...],
    ) -> None:
        self._validate_leases(raw_key, derived_key, leases)
        raw_lease = next(lease for lease in leases if lease.dataset_key == raw_key)
        self._raw_coordinator.recover_dataset(raw_key, raw_lease)
        self.recover_derived_dataset(raw_key, derived_key, leases)

    def _validate_leases(
        self,
        raw_key: str,
        derived_key: str,
        leases: tuple[DatasetLease, ...],
    ) -> None:
        by_key = {lease.dataset_key: lease for lease in leases}
        if set(by_key) != {raw_key, derived_key}:
            raise MarketDataInconsistencyError("As leases do resampling são inválidas.")
        self._locks.validate(by_key[raw_key], raw_key)
        self._locks.validate(by_key[derived_key], derived_key)

    def _recover_path_locked(
        self,
        path: Path,
        identity: tuple[str, str, str],
        leases: tuple[DatasetLease, ...],
    ) -> int:
        self._validate_leases(identity[1], identity[2], leases)
        if not path.exists():
            return 0
        record = _read_journal(path, self._raw.root)
        current = (
            cast(str, record["transaction_id"]),
            cast(str, record["raw_key"]),
            cast(str, record["derived_key"]),
        )
        if current != identity:
            raise MarketDataStorageError("O journal derivado mudou de identidade.")
        if record["state"] == "PREPARED":
            _rollback_artifacts(record["artifacts"], self._raw.root)
        else:
            _cleanup_artifacts(record["artifacts"], self._raw.root)
        path.unlink(missing_ok=True)
        fsync_directory(path.parent)
        return 1

    def _build_result(self, plan: ResamplingPlan) -> ResamplingResult:
        pair = TradingPair.parse(plan.source.symbol)
        source_tf = get_timeframe(plan.source.timeframe)
        target_tf = get_timeframe(plan.target.timeframe)

        def rows() -> Iterator[Candle]:
            for path in self._raw.partition_paths(
                plan.source.exchange,
                plan.source.market_type,
                pair,
                source_tf,
                plan.data_range,
            ):
                for candle in self._raw.read_partition(
                    path,
                    exchange=plan.source.exchange,
                    market_type=plan.source.market_type,
                    pair=pair,
                    timeframe=source_tf,
                ):
                    if plan.data_range.start <= candle.open_time < plan.data_range.end:
                        yield candle

        return self._resampler.resample(
            rows(),
            plan,
            source_timeframe=source_tf,
            target_timeframe=target_tf,
        )

    def _commit(
        self,
        plan: ResamplingPlan,
        result: ResamplingResult,
        source_version: str,
    ) -> None:
        transaction_id = uuid4().hex
        previous_manifest: DatasetManifest | None = None
        manifest_target = self._derived.manifest_path(plan)
        if manifest_target.exists():
            previous_manifest = self._derived.load_manifest(manifest_target)
        grouped: dict[tuple[int, int], list[Candle]] = defaultdict(list)
        for candle in result.candles:
            grouped[(candle.open_time.year, candle.open_time.month)].append(candle)
        unchanged_summaries: list[PartitionSummary] = []
        if previous_manifest is not None:
            pair = TradingPair.parse(plan.target.symbol)
            target_tf = get_timeframe(plan.target.timeframe)
            for summary in previous_manifest.partitions:
                if not _summary_intersects(summary, plan.data_range):
                    unchanged_summaries.append(summary)
                    continue
                path = ensure_safe_path(
                    self._raw.root,
                    self._raw.root / summary.relative_path,
                )
                existing_rows = self._raw.read_partition(
                    path,
                    exchange=plan.target.exchange,
                    market_type=plan.target.market_type,
                    pair=pair,
                    timeframe=target_tf,
                )
                grouped[(summary.year, summary.month)].extend(
                    candle
                    for candle in existing_rows
                    if not plan.data_range.start <= candle.open_time < plan.data_range.end
                )
        for key, candles in grouped.items():
            unique = {candle.open_time: candle for candle in candles}
            grouped[key] = sorted(unique.values(), key=lambda item: item.open_time)
        artifacts: list[dict[str, object]] = []
        for (year, month), candles in sorted(grouped.items()):
            target = self._derived.partition_path(plan, year, month)
            artifacts.append(
                _artifact(target, transaction_id, tuple(candles), self._raw.root)
                if candles
                else _deletion_artifact(target, transaction_id, self._raw.root)
            )
        now = self._clock().astimezone(UTC).isoformat()
        partitions = tuple(
            sorted(
                (
                    *unchanged_summaries,
                    *(
                        _partition_summary(
                            Path(str(item["target"])),
                            cast(tuple[Candle, ...], item["candles"]),
                            self._raw.root,
                        )
                        for item in artifacts
                        if item["kind"] == "parquet"
                    ),
                ),
                key=lambda item: (item.year, item.month),
            )
        )
        target_checksum = _dataset_checksum(partitions)
        prior_source = (
            tuple(
                summary
                for summary in previous_manifest.source_partitions
                if not _summary_intersects(summary, plan.data_range)
            )
            if previous_manifest
            else ()
        )
        source_partitions = tuple(
            sorted(
                (
                    *prior_source,
                    *(
                        _source_partition_summary(path, self._raw, plan)
                        for path in self._raw.partition_paths(
                            plan.source.exchange,
                            plan.source.market_type,
                            TradingPair.parse(plan.source.symbol),
                            get_timeframe(plan.source.timeframe),
                            plan.data_range,
                        )
                    ),
                ),
                key=lambda item: (item.year, item.month),
            )
        )
        lineage = DatasetLineage(
            source_dataset_key=(
                f"{plan.source.exchange.value}:{plan.source.market_type.value}:"
                f"{plan.source.symbol}:{plan.source.timeframe}"
            ),
            source_dataset_version=source_version,
            source_checksum=plan.source_checksum,
            source_timeframe=plan.source.timeframe,
            target_timeframe=plan.target.timeframe,
            algorithm=plan.algorithm,
            algorithm_version=plan.algorithm_version,
            gap_policy=plan.gap_policy,
            open_candle_policy="REJECT",
            calendar=plan.calendar,
            materialized_at=now,
        )
        manifest = DatasetManifest(
            identity=plan.target,
            schema_version=self._derived.schema_version,
            source_dataset_key=lineage.source_dataset_key,
            source_dataset_version=source_version,
            source_checksum=plan.source_checksum,
            target_dataset_key=plan.target.key,
            target_version=target_checksum,
            target_checksum=target_checksum,
            source_timeframe=plan.source.timeframe,
            target_timeframe=plan.target.timeframe,
            gap_policy=plan.gap_policy,
            calendar=plan.calendar,
            first_open_time=next(
                (item.first_open_time for item in partitions if item.first_open_time),
                None,
            ),
            last_open_time=next(
                (item.last_open_time for item in reversed(partitions) if item.last_open_time),
                None,
            ),
            candle_count=sum(item.candle_count for item in partitions),
            partitions=partitions,
            source_partitions=source_partitions,
            algorithm=plan.algorithm,
            algorithm_version=plan.algorithm_version,
            created_at=previous_manifest.created_at if previous_manifest else now,
            updated_at=now,
            state=DatasetState.COMPLETE,
            lineage=lineage,
        )
        manifest_artifact = _manifest_artifact(
            manifest_target,
            manifest,
            transaction_id,
            self._derived,
            self._raw.root,
        )
        artifacts.append(manifest_artifact)
        journal_dir = ensure_safe_path(
            self._raw.root,
            self._derived.root / ".transactions",
        )
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal = ensure_safe_path(
            self._raw.root,
            journal_dir / f"journal-{transaction_id}.json",
        )
        record: dict[str, object] = {
            "transaction_id": transaction_id,
            "state": "PREPARED",
            "raw_key": lineage.source_dataset_key,
            "derived_key": plan.target.key,
            "dataset_root": self._derived.dataset_root(plan).relative_to(self._raw.root).as_posix(),
            "source_version": source_version,
            "target_checksum": target_checksum,
            "artifacts": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"candles", "content", "store"}
                }
                for item in artifacts
            ],
        }
        _write_journal(journal, record, journal_dir)
        try:
            for index, artifact in enumerate(artifacts):
                self._failure_hook(f"before_prepare:{index}")
                _prepare_artifact(artifact, self._derived)
                self._failure_hook(f"prepared:{index}")
            for index, artifact in enumerate(artifacts):
                self._failure_hook(f"before_promote:{index}")
                _promote_artifact(artifact)
                self._failure_hook(f"promoted:{index}")
            self._failure_hook("before_committed")
            record["state"] = "COMMITTED"
            _write_journal(journal, record, journal_dir)
        except Exception:
            _rollback_artifacts(record["artifacts"], self._raw.root)
            journal.unlink(missing_ok=True)
            fsync_directory(journal_dir)
            raise
        try:
            self._failure_hook("committed")
            _cleanup_artifacts(record["artifacts"], self._raw.root)
            journal.unlink(missing_ok=True)
            fsync_directory(journal_dir)
        except Exception:
            logger.warning(
                "Derived dataset cleanup deferred",
                extra={
                    "transaction_id": transaction_id,
                    "dataset_key": plan.target.key,
                    "failure_code": "derived_cleanup_deferred",
                },
            )


def _artifact(
    target: Path,
    transaction_id: str,
    candles: tuple[Candle, ...],
    root: Path,
) -> dict[str, object]:
    return {
        "kind": "parquet",
        "target": ensure_safe_path(root, target),
        "temporary": ensure_safe_path(
            root, target.with_name(f".candles.parquet.tmp-{transaction_id}")
        ),
        "backup": ensure_safe_path(
            root, target.with_name(f".candles.parquet.bak-{transaction_id}")
        ),
        "had_original": target.exists(),
        "candles": candles,
    }


def _manifest_artifact(
    target: Path,
    manifest: DatasetManifest,
    transaction_id: str,
    store: DerivedDatasetStore,
    root: Path,
) -> dict[str, object]:
    payload = asdict(manifest)
    checksum = hashlib.sha256(_canonical_json(payload)).hexdigest()
    content = _canonical_json({"manifest": payload, "checksum": checksum})
    return {
        "kind": "manifest",
        "target": ensure_safe_path(root, target),
        "temporary": ensure_safe_path(
            root, target.with_name(f".manifest.json.tmp-{transaction_id}")
        ),
        "backup": ensure_safe_path(root, target.with_name(f".manifest.json.bak-{transaction_id}")),
        "had_original": target.exists(),
        "content": content,
        "store": store,
    }


def _deletion_artifact(
    target: Path,
    transaction_id: str,
    root: Path,
) -> dict[str, object]:
    return {
        "kind": "delete",
        "target": ensure_safe_path(root, target),
        "temporary": ensure_safe_path(
            root, target.with_name(f".candles.parquet.tmp-{transaction_id}")
        ),
        "backup": ensure_safe_path(
            root, target.with_name(f".candles.parquet.bak-{transaction_id}")
        ),
        "had_original": target.exists(),
    }


def _prepare_artifact(artifact: dict[str, object], store: DerivedDatasetStore) -> None:
    target = Path(str(artifact["target"]))
    temporary = Path(str(artifact["temporary"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    if artifact["kind"] == "parquet":
        candles = cast(tuple[Candle, ...], artifact["candles"])
        table = _candles_to_table(candles)
        pq.write_table(table, temporary, compression="zstd")
        if not pq.ParquetFile(temporary).schema_arrow.equals(PARQUET_SCHEMA):
            raise MarketDataStorageError("O Parquet derivado preparado possui schema inválido.")
    elif artifact["kind"] == "manifest":
        temporary.write_bytes(cast(bytes, artifact["content"]))
    if artifact["kind"] != "delete":
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
    fsync_directory(target.parent)


def _promote_artifact(artifact: dict[str, object]) -> None:
    target = Path(str(artifact["target"]))
    temporary = Path(str(artifact["temporary"]))
    backup = Path(str(artifact["backup"]))
    try:
        if bool(artifact["had_original"]):
            backup.unlink(missing_ok=True)
            os.link(target, backup)
            fsync_directory(target.parent)
        if artifact["kind"] == "delete":
            target.unlink(missing_ok=True)
        else:
            os.replace(temporary, target)
        fsync_directory(target.parent)
    except Exception:
        if backup.exists():
            target.unlink(missing_ok=True)
            os.replace(backup, target)
            fsync_directory(target.parent)
        elif not bool(artifact["had_original"]):
            target.unlink(missing_ok=True)
        raise


def _rollback_artifacts(raw_artifacts: object, root: Path) -> None:
    if not isinstance(raw_artifacts, list):
        raise MarketDataStorageError("O journal derivado é inválido.")
    for raw in reversed(raw_artifacts):
        artifact = _validated_artifact(raw, root)
        target = Path(str(artifact["target"]))
        backup = Path(str(artifact["backup"]))
        temporary = Path(str(artifact["temporary"]))
        if backup.exists():
            target.unlink(missing_ok=True)
            os.replace(backup, target)
        elif not bool(artifact["had_original"]):
            target.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        if target.parent.exists():
            fsync_directory(target.parent)


def _cleanup_artifacts(raw_artifacts: object, root: Path) -> None:
    if not isinstance(raw_artifacts, list):
        raise MarketDataStorageError("O journal derivado é inválido.")
    for raw in raw_artifacts:
        artifact = _validated_artifact(raw, root)
        for field in ("temporary", "backup"):
            Path(str(artifact[field])).unlink(missing_ok=True)
        target = Path(str(artifact["target"]))
        if target.parent.exists():
            fsync_directory(target.parent)


def _validated_artifact(raw: object, root: Path) -> dict[str, object]:
    if not isinstance(raw, dict) or not isinstance(raw.get("had_original"), bool):
        raise MarketDataStorageError("O artefato derivado é inválido.")
    result = dict(raw)
    for field in ("target", "temporary", "backup"):
        value = raw.get(field)
        if not isinstance(value, (str, Path)):
            raise MarketDataStorageError("O caminho derivado é inválido.")
        candidate = value if isinstance(value, Path) else root / value
        result[field] = ensure_safe_path(root, candidate)
    target = Path(str(result["target"]))
    if (
        Path(str(result["temporary"])).parent != target.parent
        or Path(str(result["backup"])).parent != target.parent
    ):
        raise MarketDataStorageError("O artefato derivado possui diretório divergente.")
    return result


def _write_journal(path: Path, record: dict[str, object], journal_dir: Path) -> None:
    serializable = dict(record)
    raw_artifacts = record.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise MarketDataStorageError("O journal derivado é inválido.")
    serializable["artifacts"] = [
        {
            **item,
            "target": Path(str(item["target"])).relative_to(journal_dir.parents[1]).as_posix(),
            "temporary": Path(str(item["temporary"]))
            .relative_to(journal_dir.parents[1])
            .as_posix(),
            "backup": Path(str(item["backup"])).relative_to(journal_dir.parents[1]).as_posix(),
        }
        for item in raw_artifacts
        if isinstance(item, dict)
    ]
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(_canonical_json(serializable))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(journal_dir)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise MarketDataStorageError() from None


def _read_journal(path: Path, root: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MarketDataStorageError("O journal derivado é inválido.") from None
    transaction_id = raw.get("transaction_id") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or raw.get("state") not in {"PREPARED", "COMMITTED"}
        or not isinstance(transaction_id, str)
        or len(transaction_id) != 32
        or any(character not in "0123456789abcdef" for character in transaction_id)
        or not isinstance(raw.get("raw_key"), str)
        or not isinstance(raw.get("derived_key"), str)
        or not isinstance(raw.get("dataset_root"), str)
        or not isinstance(raw.get("artifacts"), list)
    ):
        raise MarketDataStorageError("O journal derivado é inválido.")
    if path.name != f"journal-{transaction_id}.json":
        raise MarketDataStorageError("O journal derivado possui identidade divergente.")
    dataset_root = ensure_safe_path(root, root / cast(str, raw["dataset_root"]))
    if not _dataset_root_matches_key(dataset_root, root, cast(str, raw["derived_key"])):
        raise MarketDataStorageError("O journal derivado possui raiz inválida.")
    targets: set[Path] = set()
    manifest_targets = 0
    for artifact in raw["artifacts"]:
        validated = _validated_artifact(artifact, root)
        if validated.get("kind") not in {"parquet", "manifest", "delete"}:
            raise MarketDataStorageError("O journal derivado possui tipo inválido.")
        target = Path(str(validated["target"]))
        temporary = Path(str(validated["temporary"]))
        backup = Path(str(validated["backup"]))
        if (
            target in targets
            or not target.is_relative_to(dataset_root)
            or temporary.name.count(transaction_id) != 1
            or backup.name.count(transaction_id) != 1
        ):
            raise MarketDataStorageError("O journal derivado possui artefatos divergentes.")
        kind = validated["kind"]
        relative = target.relative_to(dataset_root)
        if kind == "manifest":
            if relative != Path("manifest.json"):
                raise MarketDataStorageError("O target do manifest derivado é inválido.")
            manifest_targets += 1
        elif not _valid_partition_relative(relative):
            raise MarketDataStorageError("O target Parquet derivado é inválido.")
        targets.add(target)
    if manifest_targets != 1:
        raise MarketDataStorageError("O journal exige exatamente um manifest derivado.")
    return raw


def _read_journal_identity(path: Path) -> tuple[str, str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        transaction_id = raw["transaction_id"]
        raw_key = raw["raw_key"]
        derived_key = raw["derived_key"]
    except (OSError, ValueError, KeyError, TypeError):
        raise MarketDataStorageError("A identidade do journal derivado é inválida.") from None
    if not all(isinstance(item, str) and item for item in (transaction_id, raw_key, derived_key)):
        raise MarketDataStorageError("A identidade do journal derivado é inválida.")
    return transaction_id, raw_key, derived_key


def _valid_partition_relative(path: Path) -> bool:
    parts = path.parts
    if len(parts) != 3 or parts[2] != "candles.parquet":
        return False
    year, month = parts[:2]
    return (
        len(year) == 9
        and year.startswith("year=")
        and year[5:].isdigit()
        and len(month) == 8
        and month.startswith("month=")
        and month[6:].isdigit()
        and 1 <= int(month[6:]) <= 12
    )


def _dataset_root_matches_key(path: Path, root: Path, derived_key: str) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if len(relative.parts) < 8 or relative.parts[0] != "derived":
        return False
    components = {
        key: unquote(value)
        for part in relative.parts[1:]
        if "=" in part
        for key, value in (part.split("=", 1),)
    }
    prefix = "derived:"
    if not derived_key.startswith(prefix):
        return False
    try:
        exchange, market, symbol, target_timeframe, rest = derived_key[len(prefix) :].split(":", 4)
        source_prefix = "resample:"
        if not rest.startswith(source_prefix):
            return False
        source_and_policy, schema = rest.rsplit(":v", 1)
        source, policy = source_and_policy.rsplit(":", 1)
        raw_source = source.removeprefix(source_prefix)
        (
            raw_kind,
            raw_exchange,
            raw_market,
            raw_symbol,
            source_timeframe,
            _raw_rest,
        ) = raw_source.split(":", 5)
        pair = TradingPair.parse(symbol)
    except (ValueError, IndexError, MarketDataInconsistencyError):
        return False
    return (
        raw_kind == "raw"
        and raw_exchange == exchange
        and raw_market == market
        and raw_symbol == symbol
        and components
        == {
            "exchange": exchange,
            "market": market,
            "base": pair.base,
            "quote": pair.quote,
            "source_timeframe": source_timeframe,
            "timeframe": target_timeframe,
            "policy": policy,
        }
        and schema.isdigit()
    )


def _partition_summary(
    target: Path,
    candles: tuple[Candle, ...],
    root: Path,
) -> PartitionSummary:
    digest = hashlib.sha256()
    for candle in candles:
        digest.update(canonical_candle_bytes(candle))
    return PartitionSummary(
        relative_path=target.relative_to(root).as_posix(),
        year=candles[0].open_time.year,
        month=candles[0].open_time.month,
        candle_count=len(candles),
        first_open_time=candles[0].open_time.isoformat(),
        last_open_time=candles[-1].open_time.isoformat(),
        checksum=digest.hexdigest(),
    )


def _source_partition_summary(
    path: Path,
    store: ParquetCandleStore,
    plan: ResamplingPlan,
) -> PartitionSummary:
    rows = store.read_partition(
        path,
        exchange=plan.source.exchange,
        market_type=plan.source.market_type,
        pair=TradingPair.parse(plan.source.symbol),
        timeframe=get_timeframe(plan.source.timeframe),
    )
    year = int(path.parent.parent.name.removeprefix("year="))
    month = int(path.parent.name.removeprefix("month="))
    return PartitionSummary(
        relative_path=path.relative_to(store.root).as_posix(),
        year=year,
        month=month,
        candle_count=len(rows),
        first_open_time=rows[0].open_time.isoformat() if rows else None,
        last_open_time=rows[-1].open_time.isoformat() if rows else None,
        checksum=_file_checksum(path),
    )


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_checksum(partitions: tuple[PartitionSummary, ...]) -> str:
    digest = hashlib.sha256()
    for summary in sorted(partitions, key=lambda item: (item.year, item.month)):
        digest.update(summary.relative_path.encode())
        digest.update(b"\0")
        digest.update(summary.checksum.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _summary_intersects(summary: PartitionSummary, data_range: DataRange) -> bool:
    start = _month_start(summary.year, summary.month)
    end = _next_month_start(summary.year, summary.month)
    return start < data_range.end and end > data_range.start


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=UTC)


def _next_month_start(year: int, month: int) -> datetime:
    return (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=UTC)
    )


def _months_in_range(data_range: DataRange) -> set[tuple[int, int]]:
    cursor = _month_start(data_range.start.year, data_range.start.month)
    months: set[tuple[int, int]] = set()
    while cursor < data_range.end:
        months.add((cursor.year, cursor.month))
        cursor = _next_month_start(cursor.year, cursor.month)
    return months


def _raw_key(plan: ResamplingPlan) -> str:
    return (
        f"{plan.source.exchange.value}:{plan.source.market_type.value}:"
        f"{plan.source.symbol}:{plan.source.timeframe}"
    )


def _path_year_month(path: Path) -> tuple[int, int]:
    try:
        return (
            int(path.parent.parent.name.removeprefix("year=")),
            int(path.parent.name.removeprefix("month=")),
        )
    except ValueError:
        raise MarketDataStorageError("A partição RAW possui caminho inválido.") from None


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _decode_manifest(raw: object) -> DatasetManifest:
    if not isinstance(raw, dict):
        raise MarketDataStorageError("O manifest derivado é inválido.")
    try:
        identity_raw = raw["identity"]
        lineage_raw = raw["lineage"]
        if not isinstance(identity_raw, dict) or not isinstance(lineage_raw, dict):
            raise TypeError
        identity = DatasetIdentity(
            exchange=Exchange(identity_raw["exchange"]),
            market_type=MarketType(identity_raw["market_type"]),
            symbol=identity_raw["symbol"],
            timeframe=identity_raw["timeframe"],
            kind=DatasetKind(identity_raw["kind"]),
            source=identity_raw["source"],
            construction_policy=identity_raw["construction_policy"],
            schema_version=identity_raw["schema_version"],
        )
        lineage = DatasetLineage(
            **{**lineage_raw, "gap_policy": GapPolicy(lineage_raw["gap_policy"])}
        )
        return DatasetManifest(
            **{
                **raw,
                "identity": identity,
                "gap_policy": GapPolicy(raw["gap_policy"]),
                "state": DatasetState(raw["state"]),
                "lineage": lineage,
                "partitions": tuple(PartitionSummary(**item) for item in raw["partitions"]),
                "source_partitions": tuple(
                    PartitionSummary(**item) for item in raw["source_partitions"]
                ),
            }
        )
    except (KeyError, TypeError, ValueError):
        raise MarketDataStorageError("O manifest derivado é inválido.") from None
