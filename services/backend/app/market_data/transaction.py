"""Persistent PREPARED/COMMITTED journal for Parquet and catalog."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from app.market_data.catalog import CatalogPlan, JsonMarketDataCatalog
from app.market_data.errors import MarketDataStorageError
from app.market_data.filesystem import ensure_safe_path, fsync_directory
from app.market_data.storage import ParquetCandleStore, ParquetUpsertPlan

FailureHook = Callable[[str], None]
logger = logging.getLogger(__name__)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class JournalArtifact:
    """Paths required to commit or roll back one target."""

    target: Path
    temporary: Path
    backup: Path
    had_original: bool


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """Validated journal state loaded from disk."""

    transaction_id: str
    state: str
    partitions: tuple[JournalArtifact, ...]
    catalog: JournalArtifact
    intended_version: str
    intended_checksum: str
    run_id: str


class MarketDataTransactionCoordinator:
    """Atomically coordinate prepared Parquet partitions and catalog state."""

    def __init__(
        self,
        store: ParquetCandleStore,
        catalog: JsonMarketDataCatalog,
        *,
        failure_hook: FailureHook | None = None,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._root = store.root
        self._journal_dir = ensure_safe_path(self._root, self._root / ".transactions")
        self._failure_hook = failure_hook or (lambda _step: None)

    def execute(
        self,
        parquet_plan: ParquetUpsertPlan,
        catalog_plan: CatalogPlan,
        *,
        intended_version: str,
    ) -> None:
        """Execute the protocol and never downgrade a durably committed result."""
        if parquet_plan.transaction_id != catalog_plan.transaction_id:
            raise MarketDataStorageError("Planos transacionais divergentes.")
        record = self._record(parquet_plan, catalog_plan, intended_version, state="PREPARED")
        self._validate_record(record)
        journal_path = self._journal_path(record.transaction_id)
        self._failure_hook("before_journal_prepared")
        self._write_journal(journal_path, record)
        try:
            self._failure_hook("journal_prepared")
            for index, partition in enumerate(parquet_plan.partitions):
                self._failure_hook(f"before_partition_prepared:{index}")
                self._store.prepare_partition(partition)
                self._failure_hook(f"partition_prepared:{index}")
            self._failure_hook("before_catalog_prepared")
            self._catalog.write_prepared(catalog_plan)
            self._failure_hook("catalog_prepared")
            for index, partition in enumerate(parquet_plan.partitions):
                self._failure_hook(f"before_partition_promoted:{index}")
                self._store.promote_partition(partition)
                self._failure_hook(f"partition_promoted:{index}")
            self._failure_hook("before_catalog_promoted")
            self._catalog.promote(catalog_plan)
            self._failure_hook("catalog_promoted")

            self._failure_hook("before_journal_committed")
            committed = self._record(
                parquet_plan,
                catalog_plan,
                intended_version,
                state="COMMITTED",
            )
            self._validate_record(committed)
            self._write_journal(journal_path, committed)
        except Exception:
            self._rollback_before_commit(record, journal_path)
            raise

        try:
            self._failure_hook("journal_committed")
            self._failure_hook("before_cleanup")
            self._finalize(committed, journal_path)
        except Exception:
            try:
                if not journal_path.exists():
                    self._write_journal(journal_path, committed)
            except Exception:
                logger.warning(
                    "Committed market-data journal could not be restored",
                    extra={
                        "transaction_id": committed.transaction_id,
                        "run_id": committed.run_id,
                        "failure_code": "committed_journal_restore_failed",
                    },
                )
            logger.warning(
                "Market-data transaction cleanup deferred",
                extra={
                    "transaction_id": committed.transaction_id,
                    "run_id": committed.run_id,
                    "failure_code": "committed_cleanup_deferred",
                },
            )

    def recover(self) -> int:
        """Recover every journal idempotently and fail abandoned RUNNING runs."""
        recovered = 0
        if self._journal_dir.exists():
            ensure_safe_path(self._root, self._journal_dir)
            removed_temporary = False
            for temporary in self._journal_dir.glob(".journal-*.tmp"):
                ensure_safe_path(self._root, temporary).unlink(missing_ok=True)
                removed_temporary = True
            if removed_temporary:
                fsync_directory(self._journal_dir)
            for journal_path in sorted(self._journal_dir.glob("journal-*.json")):
                record = self._read_journal(journal_path)
                if record.state == "PREPARED":
                    self._rollback(record)
                elif record.state == "COMMITTED":
                    self._cleanup_artifacts(record)
                else:
                    raise MarketDataStorageError("Estado transacional inválido.")
                journal_path.unlink(missing_ok=True)
                fsync_directory(self._journal_dir)
                recovered += 1
        self._catalog.mark_abandoned_runs_failed()
        return recovered

    def _rollback(self, record: JournalRecord) -> None:
        self._rollback_artifact(record.catalog)
        for artifact in reversed(record.partitions):
            self._rollback_artifact(artifact)
        self._cleanup_temporaries(record)

    def _rollback_before_commit(self, record: JournalRecord, journal_path: Path) -> None:
        self._rollback(record)
        journal_path.unlink(missing_ok=True)
        if self._journal_dir.exists():
            fsync_directory(self._journal_dir)

    def _finalize(self, record: JournalRecord, journal_path: Path) -> None:
        self._cleanup_artifacts(record)
        journal_path.unlink(missing_ok=True)
        fsync_directory(self._journal_dir)

    def _cleanup_artifacts(self, record: JournalRecord) -> None:
        for artifact in (*record.partitions, record.catalog):
            artifact.temporary.unlink(missing_ok=True)
            artifact.backup.unlink(missing_ok=True)
            if artifact.target.parent.exists():
                fsync_directory(artifact.target.parent)

    def _cleanup_temporaries(self, record: JournalRecord) -> None:
        for artifact in (*record.partitions, record.catalog):
            artifact.temporary.unlink(missing_ok=True)
            if artifact.target.parent.exists():
                fsync_directory(artifact.target.parent)

    def _rollback_artifact(self, artifact: JournalArtifact) -> None:
        if artifact.backup.exists():
            artifact.target.unlink(missing_ok=True)
            os.replace(artifact.backup, artifact.target)
        elif not artifact.had_original:
            artifact.target.unlink(missing_ok=True)
        if artifact.target.parent.exists():
            fsync_directory(artifact.target.parent)

    def _record(
        self,
        parquet_plan: ParquetUpsertPlan,
        catalog_plan: CatalogPlan,
        intended_version: str,
        *,
        state: str,
    ) -> JournalRecord:
        partitions = tuple(
            JournalArtifact(
                target=partition.target,
                temporary=partition.temporary,
                backup=partition.backup,
                had_original=partition.had_original,
            )
            for partition in parquet_plan.partitions
        )
        return JournalRecord(
            transaction_id=parquet_plan.transaction_id,
            state=state,
            partitions=partitions,
            catalog=JournalArtifact(
                target=catalog_plan.target,
                temporary=catalog_plan.temporary,
                backup=catalog_plan.backup,
                had_original=catalog_plan.had_original,
            ),
            intended_version=intended_version,
            intended_checksum=parquet_plan.checksum,
            run_id=catalog_plan.run_id,
        )

    def _journal_path(self, transaction_id: str) -> Path:
        try:
            UUID(transaction_id)
        except ValueError:
            if len(transaction_id) != 32:
                raise MarketDataStorageError("transaction_id inválido.") from None
            try:
                int(transaction_id, 16)
            except ValueError:
                raise MarketDataStorageError("transaction_id inválido.") from None
        return ensure_safe_path(
            self._root,
            self._journal_dir / f"journal-{transaction_id}.json",
        )

    def _write_journal(self, path: Path, record: JournalRecord) -> None:
        ensure_safe_path(self._root, self._journal_dir)
        self._journal_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "transaction_id": record.transaction_id,
            "state": record.state,
            "partitions": [self._artifact_json(item) for item in record.partitions],
            "catalog": self._artifact_json(record.catalog),
            "intended_version": record.intended_version,
            "intended_checksum": record.intended_checksum,
            "run_id": record.run_id,
        }
        temporary = ensure_safe_path(
            self._root,
            self._journal_dir / f".journal-{record.transaction_id}.tmp",
        )
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            fsync_directory(self._journal_dir)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise MarketDataStorageError() from None

    def _read_journal(self, path: Path) -> JournalRecord:
        safe_path = ensure_safe_path(self._root, path)
        try:
            payload = json.loads(safe_path.read_text(encoding="utf-8"))
            transaction_id = payload["transaction_id"]
            state = payload["state"]
            raw_partitions = payload["partitions"]
            raw_catalog = payload["catalog"]
            intended_version = payload["intended_version"]
            intended_checksum = payload["intended_checksum"]
            run_id = payload["run_id"]
        except (OSError, ValueError, KeyError, TypeError):
            raise MarketDataStorageError("Journal transacional inválido.") from None
        if (
            not isinstance(transaction_id, str)
            or not isinstance(state, str)
            or not isinstance(raw_partitions, list)
            or not isinstance(raw_catalog, dict)
            or not isinstance(intended_version, str)
            or not isinstance(intended_checksum, str)
            or not isinstance(run_id, str)
        ):
            raise MarketDataStorageError("Journal transacional inválido.")
        expected_path = self._journal_path(transaction_id)
        if expected_path != safe_path:
            raise MarketDataStorageError("Journal possui transaction_id divergente.")
        record = JournalRecord(
            transaction_id=transaction_id,
            state=state,
            partitions=tuple(self._artifact_from_json(item) for item in raw_partitions),
            catalog=self._artifact_from_json(raw_catalog),
            intended_version=intended_version,
            intended_checksum=intended_checksum,
            run_id=run_id,
        )
        self._validate_record(record)
        return record

    def _validate_record(self, record: JournalRecord) -> None:
        if record.state not in {"PREPARED", "COMMITTED"}:
            raise MarketDataStorageError("Estado transacional inválido.")
        try:
            UUID(record.run_id)
        except ValueError:
            raise MarketDataStorageError("run_id transacional inválido.") from None
        if not _SHA256_PATTERN.fullmatch(record.intended_version):
            raise MarketDataStorageError("Versão transacional inválida.")
        if not _SHA256_PATTERN.fullmatch(record.intended_checksum):
            raise MarketDataStorageError("Checksum transacional inválido.")

        artifacts = (*record.partitions, record.catalog)
        targets = [artifact.target for artifact in artifacts]
        if len(targets) != len(set(targets)):
            raise MarketDataStorageError("Targets transacionais duplicados.")
        if record.catalog.target != self._catalog.path:
            raise MarketDataStorageError("Target do catálogo transacional inválido.")

        all_paths: list[Path] = []
        for artifact in artifacts:
            if (
                artifact.temporary.parent != artifact.target.parent
                or artifact.backup.parent != artifact.target.parent
                or record.transaction_id not in artifact.temporary.name
                or record.transaction_id not in artifact.backup.name
                or not artifact.temporary.name.endswith(f".tmp-{record.transaction_id}")
                or not artifact.backup.name.endswith(f".bak-{record.transaction_id}")
            ):
                raise MarketDataStorageError("Artefato transacional inconsistente.")
            all_paths.extend((artifact.target, artifact.temporary, artifact.backup))
        if len(all_paths) != len(set(all_paths)):
            raise MarketDataStorageError("Caminhos transacionais duplicados.")

        for partition in record.partitions:
            if (
                partition.target == self._catalog.path
                or partition.target == self._journal_dir
                or self._journal_dir in partition.target.parents
                or partition.target.name != "candles.parquet"
            ):
                raise MarketDataStorageError("Target de partição transacional inválido.")

    def _artifact_json(self, artifact: JournalArtifact) -> dict[str, object]:
        return {
            "target": self._relative(artifact.target),
            "temporary": self._relative(artifact.temporary),
            "backup": self._relative(artifact.backup),
            "had_original": artifact.had_original,
        }

    def _artifact_from_json(self, raw: object) -> JournalArtifact:
        if not isinstance(raw, dict) or not isinstance(raw.get("had_original"), bool):
            raise MarketDataStorageError("Artefato transacional inválido.")
        return JournalArtifact(
            target=self._from_relative(raw.get("target")),
            temporary=self._from_relative(raw.get("temporary")),
            backup=self._from_relative(raw.get("backup")),
            had_original=raw["had_original"],
        )

    def _relative(self, path: Path) -> str:
        safe_path = ensure_safe_path(self._root, path)
        return safe_path.relative_to(self._root).as_posix()

    def _from_relative(self, raw: object) -> Path:
        if not isinstance(raw, str):
            raise MarketDataStorageError("Caminho transacional inválido.")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise MarketDataStorageError("Caminho transacional inválido.")
        return ensure_safe_path(self._root, self._root.joinpath(*relative.parts))
