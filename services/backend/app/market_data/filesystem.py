"""Safe filesystem primitives for the local market-data root."""

from __future__ import annotations

import os
from pathlib import Path

from app.market_data.errors import MarketDataStorageError


def market_root(data_dir: Path) -> Path:
    """Return a physical market root and reject a pre-existing root symlink."""
    data_root = data_dir.expanduser().resolve()
    root = data_root / "market"
    if root.is_symlink():
        raise MarketDataStorageError("ADT_DATA_DIR/market não pode ser um symlink.")
    return root


def ensure_safe_path(root: Path, candidate: Path) -> Path:
    """Keep a path below root and reject every existing symlink component."""
    if not root.is_absolute() or not candidate.is_absolute():
        raise MarketDataStorageError("Caminhos de dados devem ser absolutos.")
    try:
        candidate.relative_to(root)
    except ValueError:
        raise MarketDataStorageError("O caminho de dados escapa de ADT_DATA_DIR/market.") from None

    current = root
    relative_parts = candidate.relative_to(root).parts
    for component in relative_parts:
        if component in {"", ".", ".."}:
            raise MarketDataStorageError("O caminho de dados contém traversal.")
        if current.exists() and current.is_symlink():
            raise MarketDataStorageError("Symlinks não são permitidos no dataset.")
        current = current / component
    if current.exists() and current.is_symlink():
        raise MarketDataStorageError("Symlinks não são permitidos no dataset.")

    resolved_root = root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        raise MarketDataStorageError("O caminho resolvido escapa de ADT_DATA_DIR/market.") from None
    return candidate


def fsync_directory(path: Path) -> None:
    """Persist directory entry changes on Linux."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
