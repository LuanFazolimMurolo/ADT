"""Transactional local catalog for Phase 2A dataset traceability."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic, sleep
from typing import Protocol, TextIO
from uuid import uuid4

from app.market_data.domain import DataRange, Instrument, Timeframe
from app.market_data.errors import (
    MarketDataCatalogBusyError,
    MarketDataInconsistencyError,
    MarketDataStorageError,
    MarketJobLockTimeoutError,
)
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.integrity import (
    LEGACY_RAW_DATASET_VERSION_ALGORITHM,
    RawPartitionIntegrityEntry,
    RawPartitionIntegrityManifest,
)
from app.market_data.locks import DatasetLockManager

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class DatasetMetadata:
    """Logical dataset state; candles remain exclusively in Parquet."""

    key: str
    exchange: str
    market_type: str
    symbol: str
    native_symbol: str
    timeframe: str
    location: str
    first_open_time: str | None
    last_open_time: str | None
    candle_count: int
    version: str
    updated_at: str
    version_algorithm: str = LEGACY_RAW_DATASET_VERSION_ALGORITHM
    partition_integrity: RawPartitionIntegrityManifest | None = None

    def __post_init__(self) -> None:
        if self.partition_integrity is not None and (
            not isinstance(self.partition_integrity, RawPartitionIntegrityManifest)
            or self.partition_integrity.bound_dataset_version != self.version
        ):
            raise MarketDataInconsistencyError(
                "O manifesto RAW diverge da versão catalogada do dataset."
            )


@dataclass(frozen=True, slots=True)
class IngestionRunRecord:
    """Sanitized operational ingestion state."""

    run_id: str
    dataset_key: str
    status: str
    started_at: str
    finished_at: str | None
    fetched_count: int
    stored_count: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ChunkOperationContext:
    job_id: str
    chunk_index: int
    data_range: DataRange


@dataclass(frozen=True, slots=True)
class ChunkCommitReceipt:
    job_id: str
    chunk_index: int
    dataset_key: str
    start: str
    end: str
    fetched_count: int
    stored_count: int
    duplicate_count: int
    request_count: int
    version: str
    checksum: str
    committed_at: str
    version_algorithm: str = LEGACY_RAW_DATASET_VERSION_ALGORITHM


class CatalogLease:
    """Exclusive ownership of the main catalog across plan and commit."""

    def __init__(self, catalog: JsonMarketDataCatalog, stream: TextIO) -> None:
        self._catalog = catalog
        self._stream = stream
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def __enter__(self) -> CatalogLease:
        if not self._active:
            raise MarketDataInconsistencyError("A lease do catálogo está inativa.")
        return self

    def __exit__(self, *_args: object) -> None:
        if self._active:
            stream = self._stream
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
            self._active = False


@dataclass(frozen=True, slots=True)
class CatalogPlan:
    """One validated catalog replacement retained for journal rollback."""

    transaction_id: str
    target: Path
    temporary: Path
    backup: Path
    had_original: bool
    content: bytes
    run_id: str
    dataset_key: str
    dataset_before: dict[str, object] | None
    dataset_intended: dict[str, object]
    run_before: dict[str, object]
    run_intended: dict[str, object]
    receipt_key: str | None
    receipt_before: dict[str, object] | None
    receipt_intended: dict[str, object] | None


class MarketDataCatalog(Protocol):
    """Catalog operations required by ingestion."""

    @property
    def path(self) -> Path: ...

    def start_run(
        self, dataset_key: str, *, lease: CatalogLease | None = None
    ) -> IngestionRunRecord: ...

    def fail_run(self, run_id: str, dataset_key: str, error_code: str) -> None: ...

    def list_datasets(
        self, *, lease: CatalogLease | None = None
    ) -> tuple[DatasetMetadata, ...]: ...

    def get_dataset(
        self, key: str, *, lease: CatalogLease | None = None
    ) -> DatasetMetadata | None: ...

    def list_chunk_receipts(
        self, *, lease: CatalogLease | None = None
    ) -> tuple[ChunkCommitReceipt, ...]: ...

    def prepare_completion(
        self,
        run: IngestionRunRecord,
        dataset: DatasetMetadata,
        *,
        transaction_id: str,
        lease: CatalogLease,
        receipt: ChunkCommitReceipt | None = None,
    ) -> CatalogPlan: ...

    def write_prepared(self, plan: CatalogPlan, *, lease: CatalogLease) -> None: ...

    def promote(self, plan: CatalogPlan, *, lease: CatalogLease) -> None: ...

    def mark_abandoned_runs_failed(self) -> int: ...


class JsonMarketDataCatalog:
    """Small atomic manifest with validated transactional completion."""

    def __init__(self, data_dir: Path, *, clock: Clock | None = None) -> None:
        self._root = market_root(data_dir)
        self._path = ensure_safe_path(self._root, self._root / "catalog.json")
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def path(self) -> Path:
        return self._path

    def start_run(
        self, dataset_key: str, *, lease: CatalogLease | None = None
    ) -> IngestionRunRecord:
        if lease is None:
            with self.acquire_lease() as acquired:
                return self._start_run(dataset_key, acquired)
        return self._start_run(dataset_key, lease)

    def _start_run(self, dataset_key: str, lease: CatalogLease) -> IngestionRunRecord:
        self.validate_lease(lease)
        run = IngestionRunRecord(
            run_id=str(uuid4()),
            dataset_key=dataset_key,
            status="RUNNING",
            started_at=self._clock().astimezone(UTC).isoformat(),
            finished_at=None,
            fetched_count=0,
            stored_count=0,
            error_code=None,
        )
        state = self._load()
        state["runs"][run.run_id] = asdict(run)
        self._write(state)
        return run

    def prepare_completion(
        self,
        run: IngestionRunRecord,
        dataset: DatasetMetadata,
        *,
        transaction_id: str,
        lease: CatalogLease,
        receipt: ChunkCommitReceipt | None = None,
    ) -> CatalogPlan:
        """Validate lifecycle and build the exact future catalog bytes."""
        self.validate_lease(lease)
        state = self._load()
        existing = state["runs"].get(run.run_id)
        if not isinstance(existing, dict):
            raise MarketDataInconsistencyError("A ingestão não existe no catálogo.")
        if (
            existing.get("dataset_key") != dataset.key
            or run.dataset_key != dataset.key
            or existing.get("dataset_key") != run.dataset_key
        ):
            raise MarketDataInconsistencyError("A ingestão pertence a outro dataset.")
        if existing.get("status") != "RUNNING":
            raise MarketDataInconsistencyError("A ingestão não está em estado RUNNING.")
        if existing.get("started_at") != run.started_at:
            raise MarketDataInconsistencyError("O início da ingestão diverge do catálogo.")
        if (
            run.status != "COMPLETED"
            or run.finished_at is None
            or run.error_code is not None
            or run.fetched_count < 0
            or run.stored_count < 0
            or run.stored_count > run.fetched_count
            or dataset.candle_count < 0
        ):
            raise MarketDataInconsistencyError("O estado final da ingestão é incoerente.")

        run_before = dict(existing)
        dataset_before_raw = state["datasets"].get(dataset.key)
        dataset_before = dict(dataset_before_raw) if isinstance(dataset_before_raw, dict) else None
        run_intended = asdict(run)
        dataset_intended = asdict(dataset)
        state["runs"][run.run_id] = run_intended
        state["datasets"][dataset.key] = dataset_intended
        receipt_key: str | None = None
        receipt_before: dict[str, object] | None = None
        receipt_intended: dict[str, object] | None = None
        if receipt is not None:
            receipt_key = _receipt_key(receipt.job_id, receipt.chunk_index)
            existing_receipt = state["receipts"].get(receipt_key)
            encoded_receipt = asdict(receipt)
            if existing_receipt is not None and existing_receipt != encoded_receipt:
                raise MarketDataInconsistencyError("O recibo persistido do chunk diverge.")
            receipt_before = dict(existing_receipt) if isinstance(existing_receipt, dict) else None
            receipt_intended = encoded_receipt
            state["receipts"][receipt_key] = encoded_receipt
        content = _encode_state(state)
        temporary = ensure_safe_path(
            self._root, self._path.with_name(f".catalog.json.tmp-{transaction_id}")
        )
        backup = ensure_safe_path(
            self._root, self._path.with_name(f".catalog.json.bak-{transaction_id}")
        )
        return CatalogPlan(
            transaction_id=transaction_id,
            target=self._path,
            temporary=temporary,
            backup=backup,
            had_original=self._path.exists(),
            content=content,
            run_id=run.run_id,
            dataset_key=dataset.key,
            dataset_before=dataset_before,
            dataset_intended=dataset_intended,
            run_before=run_before,
            run_intended=run_intended,
            receipt_key=receipt_key,
            receipt_before=receipt_before,
            receipt_intended=receipt_intended,
        )

    def write_prepared(self, plan: CatalogPlan, *, lease: CatalogLease) -> None:
        """Write and fsync the future catalog without promoting it."""
        self.validate_lease(lease)
        self._root.mkdir(parents=True, exist_ok=True)
        ensure_safe_path(self._root, plan.temporary)
        try:
            with plan.temporary.open("wb") as stream:
                stream.write(plan.content)
                stream.flush()
                os.fsync(stream.fileno())
            fsync_directory(plan.temporary.parent)
        except OSError:
            plan.temporary.unlink(missing_ok=True)
            raise MarketDataStorageError() from None

    def promote(self, plan: CatalogPlan, *, lease: CatalogLease) -> None:
        """Promote the catalog while preserving a rollback-capable backup."""
        self.validate_lease(lease)
        try:
            if plan.had_original:
                plan.backup.unlink(missing_ok=True)
                os.link(plan.target, plan.backup)
                fsync_directory(plan.target.parent)
            os.replace(plan.temporary, plan.target)
            fsync_directory(plan.target.parent)
        except Exception:
            plan.temporary.unlink(missing_ok=True)
            if plan.backup.exists():
                plan.target.unlink(missing_ok=True)
                os.replace(plan.backup, plan.target)
                fsync_directory(plan.target.parent)
            elif not plan.had_original:
                plan.target.unlink(missing_ok=True)
                fsync_directory(plan.target.parent)
            raise

    def rollback_semantic(
        self,
        *,
        dataset_key: str,
        dataset_before: dict[str, object] | None,
        dataset_intended: dict[str, object],
        run_id: str,
        run_before: dict[str, object],
        run_intended: dict[str, object],
        receipt_key: str | None,
        receipt_before: dict[str, object] | None,
        receipt_intended: dict[str, object] | None,
        lease: CatalogLease,
    ) -> None:
        """Revert only catalog keys owned by one PREPARED transaction."""
        self.validate_lease(lease)
        state = self._load()
        mutations = (
            (state["datasets"], dataset_key, dataset_before, dataset_intended),
            (state["runs"], run_id, run_before, run_intended),
        )
        optional_mutations = (
            ()
            if receipt_key is None
            else ((state["receipts"], receipt_key, receipt_before, receipt_intended),)
        )
        all_mutations = (*mutations, *optional_mutations)
        for mapping, key, previous, intended in all_mutations:
            current = mapping.get(key)
            if current != previous and current != intended:
                raise MarketDataInconsistencyError("O catálogo divergiu da transação pendente.")
        changed = False
        for mapping, key, previous, intended in all_mutations:
            if mapping.get(key) != intended:
                continue
            if previous is None:
                mapping.pop(key, None)
            else:
                mapping[key] = previous
            changed = True
        if changed:
            self._write(state)

    def fail_run(
        self,
        run_id: str,
        dataset_key: str,
        error_code: str,
        *,
        lease: CatalogLease | None = None,
    ) -> None:
        if lease is None:
            with self.acquire_lease() as acquired:
                self.fail_run(run_id, dataset_key, error_code, lease=acquired)
            return
        self.validate_lease(lease)
        state = self._load()
        existing = state["runs"].get(run_id)
        if not isinstance(existing, dict):
            raise MarketDataInconsistencyError("A ingestão não existe no catálogo.")
        if existing.get("dataset_key") != dataset_key:
            raise MarketDataInconsistencyError("A ingestão pertence a outro dataset.")
        if existing.get("status") == "COMPLETED":
            raise MarketDataInconsistencyError("Uma ingestão concluída não pode falhar.")
        state["runs"][run_id] = asdict(
            IngestionRunRecord(
                run_id=run_id,
                dataset_key=dataset_key,
                status="FAILED",
                started_at=str(existing.get("started_at")),
                finished_at=self._clock().astimezone(UTC).isoformat(),
                fetched_count=int(existing.get("fetched_count", 0)),
                stored_count=0,
                error_code=_sanitize_error_code(error_code),
            )
        )
        self._write(state)

    def mark_abandoned_runs_failed(self) -> int:
        """Sanitize every RUNNING record left behind after recovery."""
        snapshot = self._load()
        candidates = tuple(snapshot["runs"].items())
        changed = 0
        lock_manager = DatasetLockManager(
            self._root.parent,
            timeout_seconds=0,
            stale_after_seconds=3_600,
        )
        for run_id, raw in candidates:
            if not isinstance(raw, dict) or raw.get("status") != "RUNNING":
                continue
            dataset_key_value = raw.get("dataset_key")
            if not isinstance(dataset_key_value, str):
                raise MarketDataStorageError("O catálogo contém uma ingestão inválida.")
            try:
                with lock_manager.acquire(dataset_key_value), self.acquire_lease() as acquired:
                    self.validate_lease(acquired)
                    state = self._load()
                    current = state["runs"].get(run_id)
                    if current != raw:
                        continue
                    state["runs"][run_id] = asdict(
                        IngestionRunRecord(
                            run_id=run_id,
                            dataset_key=dataset_key_value,
                            status="FAILED",
                            started_at=str(raw.get("started_at")),
                            finished_at=self._clock().astimezone(UTC).isoformat(),
                            fetched_count=int(raw.get("fetched_count", 0)),
                            stored_count=0,
                            error_code="interrupted_ingestion",
                        )
                    )
                    self._write(state)
                    changed += 1
            except MarketJobLockTimeoutError:
                continue
        return changed

    def list_datasets(self, *, lease: CatalogLease | None = None) -> tuple[DatasetMetadata, ...]:
        if lease is None:
            with self.acquire_lease() as acquired:
                return self.list_datasets(lease=acquired)
        self.validate_lease(lease)
        state = self._load()
        datasets = [
            _decode_dataset_metadata(raw)
            for raw in state["datasets"].values()
            if isinstance(raw, dict)
        ]
        return tuple(sorted(datasets, key=lambda item: item.key))

    def get_dataset(self, key: str, *, lease: CatalogLease | None = None) -> DatasetMetadata | None:
        if lease is None:
            with self.acquire_lease() as acquired:
                return self.get_dataset(key, lease=acquired)
        self.validate_lease(lease)
        raw = self._load()["datasets"].get(key)
        return _decode_dataset_metadata(raw) if isinstance(raw, dict) else None

    def get_chunk_receipt(
        self,
        job_id: str,
        chunk_index: int,
        *,
        lease: CatalogLease | None = None,
    ) -> ChunkCommitReceipt | None:
        if lease is None:
            with self.acquire_lease() as acquired:
                return self.get_chunk_receipt(job_id, chunk_index, lease=acquired)
        self.validate_lease(lease)
        raw = self._load()["receipts"].get(_receipt_key(job_id, chunk_index))
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise MarketDataStorageError("O recibo persistido é inválido.")
        try:
            return _decode_chunk_receipt(raw)
        except TypeError:
            raise MarketDataStorageError("O recibo persistido é inválido.") from None

    def list_chunk_receipts(
        self,
        *,
        lease: CatalogLease | None = None,
    ) -> tuple[ChunkCommitReceipt, ...]:
        if lease is None:
            with self.acquire_lease() as acquired:
                return self.list_chunk_receipts(lease=acquired)
        self.validate_lease(lease)
        receipts: list[ChunkCommitReceipt] = []
        for raw in self._load()["receipts"].values():
            if not isinstance(raw, dict):
                raise MarketDataStorageError("O recibo persistido é inválido.")
            try:
                receipts.append(_decode_chunk_receipt(raw))
            except TypeError:
                raise MarketDataStorageError("O recibo persistido é inválido.") from None
        return tuple(sorted(receipts, key=lambda item: (item.job_id, item.chunk_index)))

    def list_datasets_snapshot(
        self,
        *,
        timeout_seconds: float,
    ) -> tuple[DatasetMetadata, ...]:
        """Read one atomic catalog snapshot under a bounded shared lock."""
        stream = self._acquire_snapshot_lock(timeout_seconds)
        try:
            state = self._load()
            datasets = [
                _decode_dataset_metadata(raw)
                for raw in state["datasets"].values()
                if isinstance(raw, dict)
            ]
            return tuple(sorted(datasets, key=lambda item: item.key))
        finally:
            self._release_snapshot_lock(stream)

    def get_dataset_snapshot(
        self,
        key: str,
        *,
        timeout_seconds: float,
    ) -> DatasetMetadata | None:
        """Read one dataset from an atomic catalog snapshot."""
        stream = self._acquire_snapshot_lock(timeout_seconds)
        try:
            raw = self._load()["datasets"].get(key)
            return _decode_dataset_metadata(raw) if isinstance(raw, dict) else None
        finally:
            self._release_snapshot_lock(stream)

    def _acquire_snapshot_lock(self, timeout_seconds: float) -> TextIO:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite")

        self._root.mkdir(parents=True, exist_ok=True)
        lock_path = ensure_safe_path(self._root, self._root / ".catalog.lock")
        stream = lock_path.open("a+", encoding="utf-8")
        deadline = monotonic() + float(timeout_seconds)

        while True:
            try:
                fcntl.flock(
                    stream.fileno(),
                    fcntl.LOCK_SH | fcntl.LOCK_NB,
                )
                return stream
            except BlockingIOError:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    stream.close()
                    raise MarketDataCatalogBusyError() from None
                sleep(min(0.01, remaining))

    @staticmethod
    def _release_snapshot_lock(stream: TextIO) -> None:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def acquire_lease(self) -> CatalogLease:
        self._root.mkdir(parents=True, exist_ok=True)
        lock_path = ensure_safe_path(self._root, self._root / ".catalog.lock")
        stream = lock_path.open("a+", encoding="utf-8")
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        return CatalogLease(self, stream)

    def validate_lease(self, lease: CatalogLease) -> None:
        if not lease.active or lease._catalog is not self:
            raise MarketDataInconsistencyError("A lease não pertence ao catálogo.")

    def _load(self) -> dict[str, dict[str, object]]:
        if not self._path.exists():
            return {"datasets": {}, "runs": {}, "receipts": {}}
        ensure_safe_path(self._root, self._path)
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise MarketDataStorageError() from None
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("datasets"), dict)
            or not isinstance(payload.get("runs"), dict)
            or not isinstance(payload.get("receipts", {}), dict)
        ):
            raise MarketDataStorageError()
        return {
            "datasets": payload["datasets"],
            "runs": payload["runs"],
            "receipts": payload.get("receipts", {}),
        }

    def _write(self, state: dict[str, dict[str, object]]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        ensure_safe_path(self._root, self._path)
        temporary = ensure_safe_path(
            self._root, self._path.with_name(f".catalog.json.tmp-{uuid4().hex}")
        )
        try:
            with temporary.open("wb") as stream:
                stream.write(_encode_state(state))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            fsync_directory(self._path.parent)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise MarketDataStorageError() from None


def dataset_key(instrument: Instrument, timeframe: Timeframe) -> str:
    return (
        f"{instrument.exchange.value}:{instrument.market_type.value}:"
        f"{instrument.symbol}:{timeframe.code}"
    )


def _encode_state(state: dict[str, dict[str, object]]) -> bytes:
    return json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _sanitize_error_code(value: str) -> str:
    sanitized = "".join(character for character in value if character.isalnum() or character == "_")
    return (sanitized or "ingestion_failed")[:64]


def _receipt_key(job_id: str, chunk_index: int) -> str:
    return f"{job_id}:{chunk_index}"


def _decode_dataset_metadata(raw: dict[str, object]) -> DatasetMetadata:
    payload = dict(raw)
    payload.setdefault("version_algorithm", LEGACY_RAW_DATASET_VERSION_ALGORITHM)
    raw_integrity = payload.pop("partition_integrity", None)
    try:
        partition_integrity = _decode_partition_integrity(raw_integrity)
        payload["partition_integrity"] = partition_integrity
        return DatasetMetadata(**payload)  # type: ignore[arg-type]
    except (TypeError, MarketDataInconsistencyError):
        raise MarketDataStorageError("O dataset persistido é inválido.") from None


def _decode_partition_integrity(raw: object) -> RawPartitionIntegrityManifest | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "bound_dataset_version",
        "checksum_algorithm",
        "entries",
    }:
        raise MarketDataInconsistencyError("O manifesto RAW persistido é inválido.")
    raw_entries = raw["entries"]
    if not isinstance(raw_entries, list):
        raise MarketDataInconsistencyError("As entradas RAW persistidas são inválidas.")
    entries: list[RawPartitionIntegrityEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict) or set(item) != {"relative_path", "checksum"}:
            raise MarketDataInconsistencyError("Uma entrada RAW persistida é inválida.")
        entries.append(
            RawPartitionIntegrityEntry(
                relative_path=item["relative_path"],
                checksum=item["checksum"],
            )
        )
    return RawPartitionIntegrityManifest(
        schema_version=raw["schema_version"],
        bound_dataset_version=raw["bound_dataset_version"],
        checksum_algorithm=raw["checksum_algorithm"],
        entries=tuple(entries),
    )


def _decode_chunk_receipt(raw: dict[str, object]) -> ChunkCommitReceipt:
    payload = dict(raw)
    payload.setdefault("version_algorithm", LEGACY_RAW_DATASET_VERSION_ALGORITHM)
    try:
        return ChunkCommitReceipt(**payload)  # type: ignore[arg-type]
    except TypeError:
        raise MarketDataStorageError("O recibo persistido é inválido.") from None
