"""Transactional local catalog for Phase 2A dataset traceability."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.market_data.domain import Instrument, Timeframe
from app.market_data.errors import MarketDataInconsistencyError, MarketDataStorageError
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root

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
class CatalogPlan:
    """One validated catalog replacement retained for journal rollback."""

    transaction_id: str
    target: Path
    temporary: Path
    backup: Path
    had_original: bool
    content: bytes
    run_id: str


class MarketDataCatalog(Protocol):
    """Catalog operations required by ingestion."""

    @property
    def path(self) -> Path: ...

    def start_run(self, dataset_key: str) -> IngestionRunRecord: ...

    def fail_run(self, run_id: str, dataset_key: str, error_code: str) -> None: ...

    def list_datasets(self) -> tuple[DatasetMetadata, ...]: ...

    def get_dataset(self, key: str) -> DatasetMetadata | None: ...

    def prepare_completion(
        self,
        run: IngestionRunRecord,
        dataset: DatasetMetadata,
        *,
        transaction_id: str,
    ) -> CatalogPlan: ...

    def write_prepared(self, plan: CatalogPlan) -> None: ...

    def promote(self, plan: CatalogPlan) -> None: ...

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

    def start_run(self, dataset_key: str) -> IngestionRunRecord:
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
    ) -> CatalogPlan:
        """Validate lifecycle and build the exact future catalog bytes."""
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

        state["runs"][run.run_id] = asdict(run)
        state["datasets"][dataset.key] = asdict(dataset)
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
        )

    def write_prepared(self, plan: CatalogPlan) -> None:
        """Write and fsync the future catalog without promoting it."""
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

    def promote(self, plan: CatalogPlan) -> None:
        """Promote the catalog while preserving a rollback-capable backup."""
        try:
            if plan.had_original:
                os.replace(plan.target, plan.backup)
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

    def fail_run(self, run_id: str, dataset_key: str, error_code: str) -> None:
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
        state = self._load()
        changed = 0
        for run_id, raw in tuple(state["runs"].items()):
            if not isinstance(raw, dict) or raw.get("status") != "RUNNING":
                continue
            dataset_key_value = raw.get("dataset_key")
            if not isinstance(dataset_key_value, str):
                raise MarketDataStorageError("O catálogo contém uma ingestão inválida.")
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
            changed += 1
        if changed:
            self._write(state)
        return changed

    def list_datasets(self) -> tuple[DatasetMetadata, ...]:
        state = self._load()
        datasets = [
            DatasetMetadata(**raw) for raw in state["datasets"].values() if isinstance(raw, dict)
        ]
        return tuple(sorted(datasets, key=lambda item: item.key))

    def get_dataset(self, key: str) -> DatasetMetadata | None:
        raw = self._load()["datasets"].get(key)
        return DatasetMetadata(**raw) if isinstance(raw, dict) else None

    def _load(self) -> dict[str, dict[str, object]]:
        if not self._path.exists():
            return {"datasets": {}, "runs": {}}
        ensure_safe_path(self._root, self._path)
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise MarketDataStorageError() from None
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("datasets"), dict)
            or not isinstance(payload.get("runs"), dict)
        ):
            raise MarketDataStorageError()
        return {"datasets": payload["datasets"], "runs": payload["runs"]}

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
