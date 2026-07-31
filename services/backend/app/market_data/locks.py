"""Explicit Linux file-lock leases for persistent market-data writes."""

from __future__ import annotations

import fcntl
import json
import math
import os
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TextIO

from app.market_data.errors import MarketDataInconsistencyError, MarketJobLockTimeoutError
from app.market_data.filesystem import ensure_safe_path, market_root

Clock = Callable[[], datetime]


class DatasetLease:
    """One active, exact dataset lock ownership token."""

    def __init__(self, root: Path, dataset_key: str, stream: TextIO) -> None:
        self._root = root
        self.dataset_key = dataset_key
        self._stream = stream
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def validate(self, root: Path, dataset_key: str) -> None:
        if not self._active or self._root != root or self.dataset_key != dataset_key:
            raise MarketDataInconsistencyError("A lease não corresponde ao dataset.")

    def __enter__(self) -> DatasetLease:
        if not self._active:
            raise MarketDataInconsistencyError("A lease do dataset está inativa.")
        return self

    def __exit__(self, *_args: object) -> None:
        if self._active:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._active = False


class DatasetLockManager:
    """Create exclusive leases whose kernel flock is the authority."""

    def __init__(
        self,
        data_dir: Path,
        *,
        timeout_seconds: float,
        stale_after_seconds: float,
        clock: Clock | None = None,
    ) -> None:
        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds < 0
            or not math.isfinite(stale_after_seconds)
            or stale_after_seconds < 0
        ):
            raise MarketDataInconsistencyError("A configuração do lock é inválida.")
        self._root = market_root(data_dir)
        self._timeout = timeout_seconds
        self._stale_after = stale_after_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def acquire(self, dataset_key: str) -> DatasetLease:
        digest = sha256(dataset_key.encode()).hexdigest()
        path = ensure_safe_path(self._root, self._root / ".locks" / f"{digest}.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._timeout
        stream = path.open("a+", encoding="utf-8")
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    stream.close()
                    raise MarketJobLockTimeoutError() from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        try:
            stream.seek(0)
            previous = stream.read()
            if previous:
                _inspect_lock_metadata(previous, self._stale_after, self._clock())
            stream.seek(0)
            stream.truncate()
            json.dump(
                {"pid": os.getpid(), "acquired_at": self._clock().astimezone(UTC).isoformat()},
                stream,
                sort_keys=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
        except Exception:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
            raise MarketDataInconsistencyError(
                "Os metadados do lock não puderam ser persistidos."
            ) from None
        return DatasetLease(self._root, dataset_key, stream)

    def validate(self, lease: DatasetLease, dataset_key: str) -> None:
        lease.validate(self._root, dataset_key)

    @contextmanager
    def acquire_many(self, dataset_keys: tuple[str, ...]) -> Iterator[tuple[DatasetLease, ...]]:
        """Acquire unique dataset locks in canonical order to prevent deadlocks."""
        ordered = tuple(sorted(set(dataset_keys)))
        if not ordered:
            raise MarketDataInconsistencyError("Ao menos um dataset deve ser bloqueado.")
        with ExitStack() as stack:
            leases = tuple(stack.enter_context(self.acquire(key)) for key in ordered)
            yield leases


def _inspect_lock_metadata(raw: str, stale_after: float, now: datetime) -> None:
    """Parse age only for diagnostics; it never overrides the acquired flock."""
    try:
        payload = json.loads(raw)
        acquired = datetime.fromisoformat(payload["acquired_at"])
        int(payload["pid"])
    except (ValueError, TypeError, KeyError):
        return
    _is_stale_metadata = (now - acquired).total_seconds() > stale_after
