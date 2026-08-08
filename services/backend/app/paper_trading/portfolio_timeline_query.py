"""Bounded read-only projections from persisted paper portfolio timelines."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.backtesting.serialization import canonical_json_bytes, canonical_value
from app.market_data.domain import DataRange, Timeframe, TradingPair, require_utc
from app.paper_trading.domain import (
    PaperSessionConfig,
    PaperSessionState,
    paper_session_id,
    validate_paper_state_against_config,
)
from app.paper_trading.errors import (
    InvalidPaperSessionError,
    PaperPortfolioTimelineNotFoundError,
    PaperSessionVerificationError,
)
from app.paper_trading.portfolio_timeline import (
    PaperPortfolioObservation,
    PaperPortfolioTimeline,
    validate_paper_portfolio_timeline,
)
from app.paper_trading.portfolio_timeline_artifacts import (
    PaperPortfolioTimelineArtifactStore,
)
from app.paper_trading.repository import PaperTradingRepository

PAPER_PORTFOLIO_TIMELINE_PAGE_SCHEMA_VERSION = 1
PAPER_PORTFOLIO_TIMELINE_DEFAULT_LIMIT = 1_000
PAPER_PORTFOLIO_TIMELINE_MAX_LIMIT = 5_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PaperPortfolioTimelinePageQuery:
    """One canonical backward page request for the current persisted session state."""

    session_id: str
    before: datetime | None = None
    limit: int = PAPER_PORTFOLIO_TIMELINE_DEFAULT_LIMIT

    def __post_init__(self) -> None:
        try:
            _digest(self.session_id)
            if (
                type(self.limit) is not int
                or not 1 <= self.limit <= PAPER_PORTFOLIO_TIMELINE_MAX_LIMIT
            ):
                raise ValueError("timeline limit is invalid")
            if self.before is not None:
                object.__setattr__(
                    self,
                    "before",
                    require_utc(self.before, field_name="portfolio_timeline_before"),
                )
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperPortfolioTimelinePage:
    """One checksummed chronological slice of an immutable persisted timeline."""

    schema_version: int
    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    state_replayed_at: datetime
    pair: TradingPair
    timeframe: Timeframe
    dataset_version: str
    source_checksum: str
    timeline_id: str
    timeline_content_checksum: str
    initial_capital: Decimal
    requested_before: datetime | None
    available_range: DataRange
    page_range: DataRange
    limit: int
    total_observations: int
    has_more_before: bool
    next_before: datetime | None
    observations: tuple[PaperPortfolioObservation, ...]
    content_checksum: str

    def __post_init__(self) -> None:
        try:
            if self.schema_version != PAPER_PORTFOLIO_TIMELINE_PAGE_SCHEMA_VERSION:
                raise ValueError
            for value in (
                self.session_id,
                self.config_checksum,
                self.state_id,
                self.state_checksum,
                self.dataset_version,
                self.source_checksum,
                self.timeline_id,
                self.timeline_content_checksum,
                self.content_checksum,
            ):
                _digest(value)
            replayed_at = require_utc(
                self.state_replayed_at,
                field_name="portfolio_timeline_state_replayed_at",
            )
            object.__setattr__(self, "state_replayed_at", replayed_at)
            if not isinstance(self.pair, TradingPair) or not isinstance(
                self.timeframe,
                Timeframe,
            ):
                raise ValueError
            if (
                not isinstance(self.initial_capital, Decimal)
                or not self.initial_capital.is_finite()
                or self.initial_capital <= 0
            ):
                raise ValueError
            if not isinstance(self.available_range, DataRange) or not isinstance(
                self.page_range,
                DataRange,
            ):
                raise ValueError
            if (
                self.page_range.start < self.available_range.start
                or self.page_range.end > self.available_range.end
            ):
                raise ValueError
            if (
                type(self.limit) is not int
                or not 1 <= self.limit <= PAPER_PORTFOLIO_TIMELINE_MAX_LIMIT
                or type(self.total_observations) is not int
                or self.total_observations < 1
            ):
                raise ValueError
            expected_total = (
                self.available_range.end - self.available_range.start
            ) // self.timeframe.duration
            expected_page = (self.page_range.end - self.page_range.start) // self.timeframe.duration
            if (
                expected_total != self.total_observations
                or expected_page < 1
                or not isinstance(self.observations, tuple)
                or len(self.observations) != expected_page
                or len(self.observations) > self.limit
            ):
                raise ValueError

            requested_before = self.requested_before
            if requested_before is not None:
                requested_before = require_utc(
                    requested_before,
                    field_name="portfolio_timeline_requested_before",
                )
                if not self.timeframe.validate_open_time(requested_before):
                    raise ValueError
                object.__setattr__(self, "requested_before", requested_before)

            next_before = self.next_before
            if next_before is not None:
                next_before = require_utc(
                    next_before,
                    field_name="portfolio_timeline_next_before",
                )
                if not self.timeframe.validate_open_time(next_before):
                    raise ValueError
                object.__setattr__(self, "next_before", next_before)

            expected_offset = (
                self.page_range.start - self.available_range.start
            ) // self.timeframe.duration
            for offset, observation in enumerate(self.observations):
                if not isinstance(observation, PaperPortfolioObservation):
                    raise ValueError
                PaperPortfolioObservation.__post_init__(observation)
                if (
                    observation.session_id != self.session_id
                    or observation.config_checksum != self.config_checksum
                    or observation.state_id != self.state_id
                    or observation.dataset_version != self.dataset_version
                    or observation.source_checksum != self.source_checksum
                    or observation.candle_index != expected_offset + offset
                    or observation.candle_open_time
                    != self.page_range.start + self.timeframe.duration * offset
                ):
                    raise ValueError

            if self.observations[0].candle_open_time != self.page_range.start:
                raise ValueError
            if (
                self.observations[-1].candle_open_time + self.timeframe.duration
                != self.page_range.end
            ):
                raise ValueError

            expected_more = self.available_range.start < self.page_range.start
            if self.has_more_before != expected_more:
                raise ValueError
            if self.next_before != (self.page_range.start if expected_more else None):
                raise ValueError

            expected_checksum = _page_content_checksum(
                schema_version=self.schema_version,
                session_id=self.session_id,
                config_checksum=self.config_checksum,
                state_id=self.state_id,
                state_checksum=self.state_checksum,
                state_replayed_at=self.state_replayed_at,
                pair=self.pair,
                timeframe=self.timeframe,
                dataset_version=self.dataset_version,
                source_checksum=self.source_checksum,
                timeline_id=self.timeline_id,
                timeline_content_checksum=self.timeline_content_checksum,
                initial_capital=self.initial_capital,
                requested_before=self.requested_before,
                available_range=self.available_range,
                page_range=self.page_range,
                limit=self.limit,
                total_observations=self.total_observations,
                has_more_before=self.has_more_before,
                next_before=self.next_before,
                observations=self.observations,
            )
            if self.content_checksum != expected_checksum:
                raise ValueError
        except PaperSessionVerificationError:
            raise
        except Exception:
            raise PaperSessionVerificationError(
                "A página persistida da timeline de portfólio é inválida."
            ) from None


class PaperPortfolioTimelineReadService:
    """Read the current state timeline without replay, network or ad-hoc accounting."""

    def __init__(
        self,
        repository: PaperTradingRepository,
        artifact_store: PaperPortfolioTimelineArtifactStore,
    ) -> None:
        if not isinstance(repository, PaperTradingRepository) or not isinstance(
            artifact_store,
            PaperPortfolioTimelineArtifactStore,
        ):
            raise InvalidPaperSessionError(
                "O serviço de leitura da timeline de portfólio é inválido."
            )
        self._repository = repository
        self._artifact_store = artifact_store

    def read_page(
        self,
        query: PaperPortfolioTimelinePageQuery,
    ) -> PaperPortfolioTimelinePage:
        if not isinstance(query, PaperPortfolioTimelinePageQuery):
            raise InvalidPaperSessionError("A consulta da timeline de portfólio é inválida.")
        PaperPortfolioTimelinePageQuery.__post_init__(query)

        config = self._repository.load_config(query.session_id)
        if paper_session_id(config) != query.session_id:
            raise PaperSessionVerificationError("A identidade da sessão de paper trading divergiu.")
        state = self._repository.load_state(query.session_id)
        if state is None:
            raise PaperPortfolioTimelineNotFoundError()
        validate_paper_state_against_config(state, config)

        timeline = self._artifact_store.load_for_state(
            query.session_id,
            state.state_id,
            state.checksum,
        )
        _validate_timeline_against_current_state(timeline, config, state)

        selected_end = timeline.evaluation_range.end if query.before is None else query.before
        if (
            not config.timeframe.validate_open_time(selected_end)
            or selected_end > timeline.evaluation_range.end
            or selected_end <= timeline.evaluation_range.start
        ):
            raise InvalidPaperSessionError(
                "O cursor da timeline está fora da cobertura persistida."
            )

        selected_start = max(
            timeline.evaluation_range.start,
            selected_end - config.timeframe.duration * query.limit,
        )
        start_offset = (
            selected_start - timeline.evaluation_range.start
        ) // config.timeframe.duration
        end_offset = (selected_end - timeline.evaluation_range.start) // config.timeframe.duration
        observations = timeline.observations[start_offset:end_offset]
        if not observations:
            raise InvalidPaperSessionError("A consulta da timeline não selecionou observações.")

        available_range = timeline.evaluation_range
        page_range = DataRange(selected_start, selected_end)
        has_more_before = available_range.start < page_range.start
        next_before = page_range.start if has_more_before else None
        content_checksum = _page_content_checksum(
            schema_version=PAPER_PORTFOLIO_TIMELINE_PAGE_SCHEMA_VERSION,
            session_id=timeline.session_id,
            config_checksum=timeline.config_checksum,
            state_id=timeline.state_id,
            state_checksum=timeline.state_checksum,
            state_replayed_at=state.replayed_at,
            pair=config.pair,
            timeframe=config.timeframe,
            dataset_version=timeline.dataset_version,
            source_checksum=timeline.source_checksum,
            timeline_id=timeline.timeline_id,
            timeline_content_checksum=timeline.content_checksum,
            initial_capital=timeline.initial_capital,
            requested_before=query.before,
            available_range=available_range,
            page_range=page_range,
            limit=query.limit,
            total_observations=len(timeline.observations),
            has_more_before=has_more_before,
            next_before=next_before,
            observations=observations,
        )

        return PaperPortfolioTimelinePage(
            schema_version=PAPER_PORTFOLIO_TIMELINE_PAGE_SCHEMA_VERSION,
            session_id=timeline.session_id,
            config_checksum=timeline.config_checksum,
            state_id=timeline.state_id,
            state_checksum=timeline.state_checksum,
            state_replayed_at=state.replayed_at,
            pair=config.pair,
            timeframe=config.timeframe,
            dataset_version=timeline.dataset_version,
            source_checksum=timeline.source_checksum,
            timeline_id=timeline.timeline_id,
            timeline_content_checksum=timeline.content_checksum,
            initial_capital=timeline.initial_capital,
            requested_before=query.before,
            available_range=available_range,
            page_range=page_range,
            limit=query.limit,
            total_observations=len(timeline.observations),
            has_more_before=has_more_before,
            next_before=next_before,
            observations=observations,
            content_checksum=content_checksum,
        )


def _validate_timeline_against_current_state(
    timeline: PaperPortfolioTimeline,
    config: PaperSessionConfig,
    state: PaperSessionState,
) -> None:
    validate_paper_portfolio_timeline(timeline)
    if (
        timeline.session_id != state.session_id
        or timeline.config_checksum != state.config_checksum
        or timeline.state_id != state.state_id
        or timeline.state_checksum != state.checksum
        or timeline.engine_version != config.engine_version
        or timeline.strategy_lifecycle_version != config.strategy_lifecycle_version
        or timeline.base_asset != config.pair.base
        or timeline.quote_asset != config.pair.quote
        or timeline.timeframe != config.timeframe.code
        or timeline.dataset_version != state.dataset_version
        or timeline.source_checksum != state.source_checksum
        or timeline.data_range != state.data_range
        or timeline.evaluation_range != state.evaluation_range
        or timeline.initial_capital != config.initial_capital
        or timeline.candles_processed != state.candles_processed
        or len(timeline.observations) != state.candles_processed
    ):
        raise PaperSessionVerificationError(
            "A timeline persistida diverge do estado atual da sessão."
        )


def _page_content_checksum(
    *,
    schema_version: int,
    session_id: str,
    config_checksum: str,
    state_id: str,
    state_checksum: str,
    state_replayed_at: datetime,
    pair: TradingPair,
    timeframe: Timeframe,
    dataset_version: str,
    source_checksum: str,
    timeline_id: str,
    timeline_content_checksum: str,
    initial_capital: Decimal,
    requested_before: datetime | None,
    available_range: DataRange,
    page_range: DataRange,
    limit: int,
    total_observations: int,
    has_more_before: bool,
    next_before: datetime | None,
    observations: tuple[PaperPortfolioObservation, ...],
) -> str:
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "session_id": session_id,
        "config_checksum": config_checksum,
        "state_id": state_id,
        "state_checksum": state_checksum,
        "state_replayed_at": state_replayed_at.isoformat(),
        "base_asset": pair.base,
        "quote_asset": pair.quote,
        "timeframe": timeframe.code,
        "dataset_version": dataset_version,
        "source_checksum": source_checksum,
        "timeline_id": timeline_id,
        "timeline_content_checksum": timeline_content_checksum,
        "initial_capital": initial_capital,
        "requested_before": (
            requested_before.isoformat() if requested_before is not None else None
        ),
        "available_range": canonical_value(available_range),
        "page_range": canonical_value(page_range),
        "limit": limit,
        "total_observations": total_observations,
        "has_more_before": has_more_before,
        "next_before": (next_before.isoformat() if next_before is not None else None),
        "observations": canonical_value(observations),
    }
    return hashlib.sha256(
        b"adt-paper-portfolio-timeline-page-v1\x00" + canonical_json_bytes(payload)
    ).hexdigest()


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("digest must be one lowercase SHA-256")
    return value
