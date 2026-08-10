"""Immutable authoritative RAW partition-integrity catalog contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.market_data.errors import MarketDataInconsistencyError

RAW_DATASET_VERSION_ALGORITHM = "raw-partition-canonical-sha256-v1"
LEGACY_RAW_DATASET_VERSION_ALGORITHM = "raw-canonical-stream-sha256-legacy"
RAW_PARTITION_INTEGRITY_SCHEMA_VERSION = 1

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RawPartitionIntegrityEntry:
    """One canonical monthly RAW partition and its logical content hash."""

    relative_path: str
    checksum: str

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise MarketDataInconsistencyError("O caminho do manifesto RAW é inválido.")
        relative = PurePosixPath(self.relative_path)
        if (
            relative.is_absolute()
            or relative.as_posix() != self.relative_path
            or len(relative.parts) != 8
            or "." in relative.parts
            or ".." in relative.parts
            or "\\" in self.relative_path
            or relative.name != "candles.parquet"
        ):
            raise MarketDataInconsistencyError("O caminho do manifesto RAW não é canônico.")
        year = relative.parts[-3]
        month = relative.parts[-2]
        identity_prefixes = ("exchange=", "market=", "base=", "quote=", "timeframe=")
        if (
            any(
                not component.startswith(prefix) or component == prefix
                for component, prefix in zip(relative.parts[:5], identity_prefixes, strict=True)
            )
            or not year.startswith("year=")
            or len(year) != 9
            or not year[5:].isdigit()
            or not month.startswith("month=")
            or len(month) != 8
            or not month[6:].isdigit()
            or not 1 <= int(month[6:]) <= 12
        ):
            raise MarketDataInconsistencyError("A partição mensal do manifesto RAW é inválida.")
        if not isinstance(self.checksum, str) or not _SHA256_PATTERN.fullmatch(self.checksum):
            raise MarketDataInconsistencyError("O checksum da partição RAW é inválido.")


@dataclass(frozen=True, slots=True)
class RawPartitionIntegrityManifest:
    """Versioned immutable partition proof bound to one cataloged dataset version."""

    schema_version: int
    bound_dataset_version: str
    checksum_algorithm: str
    entries: tuple[RawPartitionIntegrityEntry, ...]

    def __post_init__(self) -> None:
        if self.schema_version != RAW_PARTITION_INTEGRITY_SCHEMA_VERSION:
            raise MarketDataInconsistencyError("A versão do manifesto RAW é inválida.")
        if not isinstance(self.bound_dataset_version, str) or not _SHA256_PATTERN.fullmatch(
            self.bound_dataset_version
        ):
            raise MarketDataInconsistencyError("O vínculo de versão do manifesto RAW é inválido.")
        if self.checksum_algorithm != RAW_DATASET_VERSION_ALGORITHM:
            raise MarketDataInconsistencyError("O algoritmo do manifesto RAW é inválido.")
        if not isinstance(self.entries, tuple) or any(
            not isinstance(entry, RawPartitionIntegrityEntry) for entry in self.entries
        ):
            raise MarketDataInconsistencyError("As entradas do manifesto RAW são inválidas.")
        paths = tuple(entry.relative_path for entry in self.entries)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise MarketDataInconsistencyError(
                "As entradas do manifesto RAW devem ser únicas e canonicamente ordenadas."
            )


def build_raw_partition_integrity_manifest(
    dataset_version: str,
    entries: tuple[RawPartitionIntegrityEntry, ...],
) -> RawPartitionIntegrityManifest:
    """Bind already canonical partition entries to an unchanged dataset version."""
    return RawPartitionIntegrityManifest(
        schema_version=RAW_PARTITION_INTEGRITY_SCHEMA_VERSION,
        bound_dataset_version=dataset_version,
        checksum_algorithm=RAW_DATASET_VERSION_ALGORITHM,
        entries=entries,
    )
