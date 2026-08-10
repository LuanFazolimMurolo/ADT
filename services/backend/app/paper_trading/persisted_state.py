"""External persisted-artifact binding for paper read models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.paper_trading.domain import (
    PaperSessionConfig,
    PaperSessionState,
    paper_config_checksum,
    paper_session_id,
    validate_paper_state_against_config,
)
from app.paper_trading.errors import (
    PaperPortfolioTimelineNotFoundError,
    PaperSessionCorruptError,
    PaperSessionVerificationError,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PaperPersistedStateBinding:
    """Metadata-only binding jointly authenticated by timeline reference and manifest."""

    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    dataset_version: str
    source_checksum: str
    timeline_id: str
    timeline_content_checksum: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
            for value in (
                self.session_id,
                self.config_checksum,
                self.state_id,
                self.state_checksum,
                self.dataset_version,
                self.source_checksum,
                self.timeline_id,
                self.timeline_content_checksum,
            )
        ):
            raise PaperSessionCorruptError()


class PaperPersistedStateBindingStore(Protocol):
    """Metadata-only persisted binding port used by read models."""

    def load_state_binding(
        self,
        session_id: str,
        state_id: str,
        state_checksum: str,
    ) -> PaperPersistedStateBinding: ...


@dataclass(frozen=True, slots=True)
class PaperPersistedStateVerifier:
    """Authorize one state for derived reads through external persisted artifacts."""

    artifact_store: PaperPersistedStateBindingStore

    def verify(
        self,
        config: PaperSessionConfig,
        state: PaperSessionState,
    ) -> PaperPersistedStateBinding:
        validate_paper_state_against_config(state, config)
        try:
            binding = self.artifact_store.load_state_binding(
                state.session_id,
                state.state_id,
                state.checksum,
            )
        except PaperPortfolioTimelineNotFoundError:
            raise PaperSessionVerificationError() from None
        expected = (
            state.session_id,
            state.config_checksum,
            state.state_id,
            state.checksum,
            state.dataset_version,
            state.source_checksum,
        )
        actual = (
            binding.session_id,
            binding.config_checksum,
            binding.state_id,
            binding.state_checksum,
            binding.dataset_version,
            binding.source_checksum,
        )
        if (
            actual != expected
            or binding.session_id != paper_session_id(config)
            or binding.config_checksum != paper_config_checksum(config)
        ):
            raise PaperSessionVerificationError()
        return binding
