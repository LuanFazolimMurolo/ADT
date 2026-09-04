"""Atomic local repository for paper-trading config and latest state."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLease, DatasetLockManager
from app.paper_trading.documents import (
    decode_paper_config,
    decode_paper_state,
    decode_paper_state_summary,
    encode_paper_config,
    encode_paper_state,
    encode_paper_state_summary,
)
from app.paper_trading.domain import (
    MAX_PAPER_DOCUMENT_BYTES,
    PaperSessionConfig,
    PaperSessionState,
    PaperSessionStateSummary,
    paper_session_id,
    paper_state_summary,
    validate_paper_state_against_config,
    validate_paper_state_summary_against_config,
)
from app.paper_trading.errors import (
    PaperSessionConflictError,
    PaperSessionCorruptError,
    PaperSessionNotFoundError,
)

_MAX_REPOSITORY_ENTRIES = 10_001
_STATE_CHECKSUM_PREFIX = re.compile(rb'^\{"checksum":"([0-9a-f]{64})","state":')
_STATE_CHECKSUM_PREFIX_BYTES = 128


class PaperTradingRepository:
    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("paper-trading"),
        lock_timeout_seconds: float = 10,
        lock_stale_after_seconds: float = 3_600,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise PaperSessionCorruptError()
        root = market_root(data_dir)
        self._root = ensure_safe_path(root, root / directory)
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
        )

    @contextmanager
    def lock(self, session_id: str) -> Iterator[DatasetLease]:
        self._validate_id(session_id)
        with self._locks.acquire(f"paper:{session_id}") as lease:
            yield lease

    def create(self, config: PaperSessionConfig) -> PaperSessionConfig:
        session_id = paper_session_id(config)
        with self.lock(session_id):
            directory = self._session_dir(session_id)
            target = ensure_safe_path(self._root, directory / "config.json")
            encoded = encode_paper_config(config)
            if target.exists():
                persisted = self._read_config_path(target)
                if persisted != config:
                    raise PaperSessionConflictError()
                return persisted
            directory.mkdir(parents=True, exist_ok=True)
            fsync_directory(directory.parent)
            self._atomic_write(target, encoded)
            return self._read_config_path(target)

    def load_config(self, session_id: str) -> PaperSessionConfig:
        target = ensure_safe_path(self._root, self._session_dir(session_id) / "config.json")
        if not target.is_file():
            raise PaperSessionNotFoundError()
        return self._read_config_path(target)

    def load_state(self, session_id: str) -> PaperSessionState | None:
        target = ensure_safe_path(self._root, self._session_dir(session_id) / "state.json")
        if not target.exists():
            return None
        if not target.is_file():
            raise PaperSessionCorruptError()
        return self._read_state_path(target)

    def load_state_summary(
        self,
        config: PaperSessionConfig,
    ) -> PaperSessionStateSummary | None:
        """Load a lightweight state projection, migrating legacy state once if needed."""
        session_id = paper_session_id(config)
        directory = self._session_dir(session_id)
        state_path = ensure_safe_path(self._root, directory / "state.json")
        summary_path = ensure_safe_path(self._root, directory / "summary.json")
        with self.lock(session_id):
            if not state_path.exists():
                if summary_path.exists():
                    raise PaperSessionCorruptError()
                return None
            if not state_path.is_file():
                raise PaperSessionCorruptError()
            if not summary_path.exists():
                state = self._read_state_path(state_path)
                validate_paper_state_against_config(state, config)
                self._publish_summary(directory, paper_state_summary(state))
            if not summary_path.is_file():
                raise PaperSessionCorruptError()
            summary = self._read_summary_path(summary_path)
            validate_paper_state_summary_against_config(summary, config)
            if summary.state_checksum != self._read_state_checksum_prefix(state_path):
                raise PaperSessionCorruptError()
            return summary

    def publish_state(
        self,
        config: PaperSessionConfig,
        state: PaperSessionState,
        *,
        lease: DatasetLease,
    ) -> PaperSessionState:
        session_id = paper_session_id(config)
        self._locks.validate(lease, f"paper:{session_id}")
        try:
            validate_paper_state_against_config(state, config)
        except Exception:
            raise PaperSessionConflictError() from None
        if state.session_id != session_id:
            raise PaperSessionConflictError()
        directory = self._session_dir(session_id)
        if self.load_config(session_id) != config:
            raise PaperSessionConflictError()
        target = ensure_safe_path(self._root, directory / "state.json")
        existing = self.load_state(session_id)
        if existing is not None:
            if existing.state_id == state.state_id:
                self._publish_summary(directory, paper_state_summary(existing))
                return existing
            if existing.data_range.end >= state.data_range.end:
                raise PaperSessionConflictError(
                    "O estado da sessão não pode regredir nem divergir no mesmo intervalo."
                )
        self._atomic_write(target, encode_paper_state(state))
        persisted = self._read_state_path(target)
        if persisted != state:
            raise PaperSessionCorruptError()
        self._publish_summary(directory, paper_state_summary(persisted))
        return persisted

    def list_session_configs_page(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[tuple[PaperSessionConfig, ...], int]:
        """Return one deterministic page without decoding configs outside it."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
            raise PaperSessionCorruptError()
        names = self._list_session_names()
        selected = names[offset : offset + limit]
        configs: list[PaperSessionConfig] = []
        for session_id in selected:
            config_path = ensure_safe_path(
                self._root,
                self._session_dir(session_id) / "config.json",
            )
            config = self._read_config_path(config_path)
            if paper_session_id(config) != session_id:
                raise PaperSessionCorruptError()
            configs.append(config)
        return tuple(configs), len(names)

    def list_session_ids(self) -> tuple[str, ...]:
        """Return all verified session identities in deterministic order."""
        names = self._list_session_names()
        verified: list[str] = []
        for session_id in names:
            config_path = ensure_safe_path(
                self._root,
                self._session_dir(session_id) / "config.json",
            )
            config = self._read_config_path(config_path)
            if paper_session_id(config) != session_id:
                raise PaperSessionCorruptError()
            verified.append(session_id)
        return tuple(verified)

    def _list_session_names(self) -> tuple[str, ...]:
        if not self._root.exists():
            return ()
        try:
            entries = tuple(self._root.iterdir())
        except OSError:
            raise PaperSessionCorruptError() from None
        if len(entries) > _MAX_REPOSITORY_ENTRIES:
            raise PaperSessionCorruptError()
        selected: list[str] = []
        for entry in entries:
            if entry.name == "runner" or entry.name.startswith("."):
                continue
            if not entry.is_dir():
                raise PaperSessionCorruptError()
            try:
                self._validate_id(entry.name)
            except PaperSessionNotFoundError:
                raise PaperSessionCorruptError() from None
            config_path = ensure_safe_path(self._root, entry / "config.json")
            if not config_path.is_file():
                raise PaperSessionCorruptError()
            selected.append(entry.name)
        return tuple(sorted(selected))

    def _publish_summary(
        self,
        directory: Path,
        summary: PaperSessionStateSummary,
    ) -> None:
        target = ensure_safe_path(self._root, directory / "summary.json")
        self._atomic_write(target, encode_paper_state_summary(summary))
        persisted = self._read_summary_path(target)
        if persisted != summary:
            raise PaperSessionCorruptError()
        state_path = ensure_safe_path(self._root, directory / "state.json")
        if summary.state_checksum != self._read_state_checksum_prefix(state_path):
            raise PaperSessionCorruptError()

    def _session_dir(self, session_id: str) -> Path:
        self._validate_id(session_id)
        return ensure_safe_path(self._root, self._root / session_id)

    @staticmethod
    def _validate_id(session_id: str) -> None:
        if (
            not isinstance(session_id, str)
            or len(session_id) != 64
            or any(char not in "0123456789abcdef" for char in session_id)
        ):
            raise PaperSessionNotFoundError()

    @staticmethod
    def _atomic_write(target: Path, encoded: bytes) -> None:
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            fsync_directory(target.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise PaperSessionCorruptError() from None

    @staticmethod
    def _read_config_path(path: Path) -> PaperSessionConfig:
        try:
            raw = PaperTradingRepository._read_bounded(path)
            config = decode_paper_config(raw)
            if raw != encode_paper_config(config):
                raise PaperSessionCorruptError()
            return config
        except PaperSessionCorruptError:
            raise
        except Exception:
            raise PaperSessionCorruptError() from None

    @staticmethod
    def _read_state_path(path: Path) -> PaperSessionState:
        try:
            return decode_paper_state(PaperTradingRepository._read_bounded(path))
        except PaperSessionCorruptError:
            raise
        except Exception:
            raise PaperSessionCorruptError() from None

    @staticmethod
    def _read_summary_path(path: Path) -> PaperSessionStateSummary:
        try:
            return decode_paper_state_summary(PaperTradingRepository._read_bounded(path))
        except PaperSessionCorruptError:
            raise
        except Exception:
            raise PaperSessionCorruptError() from None

    @staticmethod
    def _read_state_checksum_prefix(path: Path) -> str:
        try:
            if path.stat().st_size > MAX_PAPER_DOCUMENT_BYTES:
                raise PaperSessionCorruptError()
            with path.open("rb") as stream:
                prefix = stream.read(_STATE_CHECKSUM_PREFIX_BYTES)
        except PaperSessionCorruptError:
            raise
        except OSError:
            raise PaperSessionCorruptError() from None
        match = _STATE_CHECKSUM_PREFIX.match(prefix)
        if match is None:
            raise PaperSessionCorruptError()
        return match.group(1).decode("ascii")

    @staticmethod
    def _read_bounded(path: Path) -> bytes:
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_PAPER_DOCUMENT_BYTES + 1)
        except OSError:
            raise PaperSessionCorruptError() from None
        if len(raw) > MAX_PAPER_DOCUMENT_BYTES:
            raise PaperSessionCorruptError()
        return raw
