"""Deterministic close-bound paper-portfolio timeline reconstruction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.backtesting.domain import Fill, PortfolioSnapshot
from app.backtesting.portfolio import apply_fill, initialize_portfolio, mark_to_market
from app.backtesting.risk import DeterministicRiskManager
from app.backtesting.serialization import canonical_json_bytes, canonical_value
from app.market_data.domain import DataRange, require_utc
from app.market_data.storage import canonical_candle_bytes, validate_candle_serialization
from app.paper_trading.domain import (
    PaperCandleBatch,
    PaperSessionConfig,
    PaperSessionState,
    paper_config_checksum,
    paper_session_id,
    validate_paper_state_against_config,
)
from app.paper_trading.errors import PaperSessionVerificationError

_SHA256 = frozenset("0123456789abcdef")
_TIMELINE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PaperPortfolioObservation:
    """One engine-accounting observation bound to one verified closed candle."""

    session_id: str
    config_checksum: str
    state_id: str
    dataset_version: str
    source_checksum: str
    candle_index: int
    candle_open_time: datetime
    candle_close_time: datetime
    mark_price: Decimal
    quote_cash: Decimal
    base_quantity: Decimal
    average_entry_price: Decimal
    cost_basis: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    total_slippage_cost: Decimal
    equity: Decimal
    peak_equity: Decimal
    drawdown: Decimal
    drawdown_pct: Decimal
    risk_halt: bool

    def __post_init__(self) -> None:
        try:
            for value in (
                self.session_id,
                self.config_checksum,
                self.state_id,
                self.dataset_version,
                self.source_checksum,
            ):
                _digest(value)
            if type(self.candle_index) is not int or self.candle_index < 0:
                raise ValueError
            opened = require_utc(self.candle_open_time, field_name="candle_open_time")
            closed = require_utc(self.candle_close_time, field_name="candle_close_time")
            if closed <= opened:
                raise ValueError
            _positive(self.mark_price)
            if type(self.risk_halt) is not bool:
                raise ValueError
            snapshot = self.portfolio
            market_value = snapshot.base_quantity * self.mark_price
            if snapshot.equity != snapshot.quote_cash + market_value:
                raise ValueError
            if snapshot.unrealized_pnl != market_value - snapshot.cost_basis:
                raise ValueError
            if snapshot.base_quantity > 0:
                if snapshot.average_entry_price != snapshot.cost_basis / snapshot.base_quantity:
                    raise ValueError
            elif snapshot.average_entry_price != 0 or snapshot.cost_basis != 0:
                raise ValueError
            object.__setattr__(self, "candle_open_time", opened)
            object.__setattr__(self, "candle_close_time", closed)
        except PaperSessionVerificationError:
            raise
        except Exception:
            raise PaperSessionVerificationError(
                "Uma observação da timeline de portfólio é inválida."
            ) from None

    @property
    def portfolio(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            quote_cash=self.quote_cash,
            base_quantity=self.base_quantity,
            average_entry_price=self.average_entry_price,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            total_fees=self.total_fees,
            total_slippage_cost=self.total_slippage_cost,
            equity=self.equity,
            peak_equity=self.peak_equity,
            drawdown=self.drawdown,
            cost_basis=self.cost_basis,
            drawdown_pct=self.drawdown_pct,
        )


@dataclass(frozen=True, slots=True)
class PaperPortfolioTimeline:
    """Content-addressed semantic timeline reconstructed from verified replay events."""

    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    engine_version: str
    strategy_lifecycle_version: int
    base_asset: str
    quote_asset: str
    timeframe: str
    dataset_version: str
    source_checksum: str
    data_range: DataRange
    evaluation_range: DataRange
    initial_capital: Decimal
    candles_processed: int
    observations: tuple[PaperPortfolioObservation, ...]
    timeline_id: str
    content_checksum: str
    schema_version: int = _TIMELINE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_paper_portfolio_timeline(self)


def build_paper_portfolio_timeline(
    config: PaperSessionConfig,
    batch: PaperCandleBatch,
    state: PaperSessionState,
) -> PaperPortfolioTimeline:
    """Reconstruct every evaluated close without replaying strategy decisions."""
    try:
        validate_paper_state_against_config(state, config)
        _validate_batch_against_state(config, batch, state)
        evaluation_candles = tuple(
            candle for candle in batch.candles if candle.open_time >= config.start_at
        )
        if (
            len(evaluation_candles) != state.candles_processed
            or not evaluation_candles
            or evaluation_candles[0].open_time != config.start_at
            or evaluation_candles[-1].open_time != state.last_candle_open_time
        ):
            raise ValueError

        fills_by_index: dict[int, list[Fill]] = {}
        for fill in state.fills:
            if fill.candle_index >= len(evaluation_candles):
                raise ValueError
            candle = evaluation_candles[fill.candle_index]
            if fill.event_time != candle.open_time:
                raise ValueError
            fills_by_index.setdefault(fill.candle_index, []).append(fill)

        portfolio = initialize_portfolio(config.initial_capital)
        risk = DeterministicRiskManager(
            constraints=config.constraints,
            limits=config.risk_limits,
            fees=config.execution.fees,
            slippage=config.execution.slippage,
        )
        risk_halt = False
        observations: list[PaperPortfolioObservation] = []

        for candle_index, candle in enumerate(evaluation_candles):
            for fill in fills_by_index.get(candle_index, ()):
                mutation = apply_fill(portfolio, fill)
                portfolio = mutation.after

            portfolio = mark_to_market(portfolio, candle.close)
            if not risk_halt and risk.drawdown_halt_required(portfolio.snapshot()):
                risk_halt = True

            snapshot = portfolio.snapshot()
            observations.append(
                PaperPortfolioObservation(
                    session_id=state.session_id,
                    config_checksum=state.config_checksum,
                    state_id=state.state_id,
                    dataset_version=state.dataset_version,
                    source_checksum=state.source_checksum,
                    candle_index=candle_index,
                    candle_open_time=candle.open_time,
                    candle_close_time=candle.close_time,
                    mark_price=candle.close,
                    quote_cash=snapshot.quote_cash,
                    base_quantity=snapshot.base_quantity,
                    average_entry_price=snapshot.average_entry_price,
                    cost_basis=snapshot.cost_basis,
                    realized_pnl=snapshot.realized_pnl,
                    unrealized_pnl=snapshot.unrealized_pnl,
                    total_fees=snapshot.total_fees,
                    total_slippage_cost=snapshot.total_slippage_cost,
                    equity=snapshot.equity,
                    peak_equity=snapshot.peak_equity,
                    drawdown=snapshot.drawdown,
                    drawdown_pct=snapshot.drawdown_pct,
                    risk_halt=risk_halt,
                )
            )

        if portfolio.snapshot() != state.portfolio or risk_halt != state.risk_halt:
            raise PaperSessionVerificationError(
                "A timeline reconstruída diverge do estado final verificado."
            )

        payload = _timeline_payload(
            config=config,
            state=state,
            observations=tuple(observations),
        )
        timeline_id = hashlib.sha256(
            b"adt-paper-portfolio-timeline-v1\x00" + canonical_json_bytes(payload)
        ).hexdigest()
        content_checksum = hashlib.sha256(
            canonical_json_bytes({**payload, "timeline_id": timeline_id})
        ).hexdigest()

        return PaperPortfolioTimeline(
            session_id=state.session_id,
            config_checksum=state.config_checksum,
            state_id=state.state_id,
            state_checksum=state.checksum,
            engine_version=config.engine_version,
            strategy_lifecycle_version=config.strategy_lifecycle_version,
            base_asset=config.pair.base,
            quote_asset=config.pair.quote,
            timeframe=config.timeframe.code,
            dataset_version=state.dataset_version,
            source_checksum=state.source_checksum,
            data_range=state.data_range,
            evaluation_range=state.evaluation_range,
            initial_capital=config.initial_capital,
            candles_processed=state.candles_processed,
            observations=tuple(observations),
            timeline_id=timeline_id,
            content_checksum=content_checksum,
        )
    except PaperSessionVerificationError:
        raise
    except Exception:
        raise PaperSessionVerificationError(
            "A timeline de portfólio não pôde ser reconstruída."
        ) from None


def validate_paper_portfolio_timeline(timeline: PaperPortfolioTimeline) -> None:
    """Revalidate identities, ordering, accounting and canonical content hashes."""
    try:
        if not isinstance(timeline, PaperPortfolioTimeline):
            raise ValueError
        if (
            type(timeline.schema_version) is not int
            or timeline.schema_version != _TIMELINE_SCHEMA_VERSION
        ):
            raise ValueError
        for value in (
            timeline.session_id,
            timeline.config_checksum,
            timeline.state_id,
            timeline.state_checksum,
            timeline.dataset_version,
            timeline.source_checksum,
            timeline.timeline_id,
            timeline.content_checksum,
        ):
            _digest(value)
        if (
            not timeline.engine_version
            or type(timeline.strategy_lifecycle_version) is not int
            or timeline.strategy_lifecycle_version not in {1, 2}
            or not timeline.base_asset
            or not timeline.quote_asset
            or not timeline.timeframe
        ):
            raise ValueError
        if not isinstance(timeline.data_range, DataRange) or not isinstance(
            timeline.evaluation_range, DataRange
        ):
            raise ValueError
        if (
            timeline.evaluation_range.start < timeline.data_range.start
            or timeline.evaluation_range.end != timeline.data_range.end
        ):
            raise ValueError
        _positive(timeline.initial_capital)
        if (
            type(timeline.candles_processed) is not int
            or timeline.candles_processed < 1
            or not isinstance(timeline.observations, tuple)
            or len(timeline.observations) != timeline.candles_processed
        ):
            raise ValueError

        previous_open: datetime | None = None
        previous_close: datetime | None = None
        for expected_index, point in enumerate(timeline.observations):
            if not isinstance(point, PaperPortfolioObservation):
                raise ValueError
            PaperPortfolioObservation.__post_init__(point)
            if (
                point.candle_index != expected_index
                or point.session_id != timeline.session_id
                or point.config_checksum != timeline.config_checksum
                or point.state_id != timeline.state_id
                or point.dataset_version != timeline.dataset_version
                or point.source_checksum != timeline.source_checksum
            ):
                raise ValueError
            if previous_open is not None and point.candle_open_time <= previous_open:
                raise ValueError
            if previous_close is not None and point.candle_open_time < previous_close:
                raise ValueError
            if point.equity != timeline.initial_capital + point.realized_pnl + point.unrealized_pnl:
                raise ValueError
            previous_open = point.candle_open_time
            previous_close = point.candle_close_time

        first = timeline.observations[0]
        last = timeline.observations[-1]
        if (
            first.candle_open_time != timeline.evaluation_range.start
            or last.candle_open_time >= timeline.evaluation_range.end
        ):
            raise ValueError

        payload = _timeline_payload_from_timeline(timeline)
        expected_id = hashlib.sha256(
            b"adt-paper-portfolio-timeline-v1\x00" + canonical_json_bytes(payload)
        ).hexdigest()
        expected_checksum = hashlib.sha256(
            canonical_json_bytes({**payload, "timeline_id": expected_id})
        ).hexdigest()
        if timeline.timeline_id != expected_id or timeline.content_checksum != expected_checksum:
            raise ValueError
    except PaperSessionVerificationError:
        raise
    except Exception:
        raise PaperSessionVerificationError("A timeline de portfólio é inválida.") from None


def _validate_batch_against_state(
    config: PaperSessionConfig,
    batch: PaperCandleBatch,
    state: PaperSessionState,
) -> None:
    if not isinstance(batch, PaperCandleBatch):
        raise PaperSessionVerificationError()
    if (
        state.session_id != paper_session_id(config)
        or state.config_checksum != paper_config_checksum(config)
        or batch.data_range != state.data_range
        or batch.dataset_version != state.dataset_version
        or batch.source_checksum != state.source_checksum
    ):
        raise PaperSessionVerificationError("O lote de candles diverge da identidade do estado.")

    digest = hashlib.sha256()
    expected_open = config.context_start
    for candle in batch.candles:
        validate_candle_serialization(candle)
        if (
            not candle.is_closed
            or candle.symbol != config.pair.symbol
            or candle.timeframe != config.timeframe
            or candle.open_time != expected_open
            or candle.close_time
            not in {
                expected_open + config.timeframe.duration,
                expected_open + config.timeframe.duration - timedelta(milliseconds=1),
            }
        ):
            raise PaperSessionVerificationError(
                "Os candles da timeline divergem do contrato da sessão."
            )
        digest.update(canonical_candle_bytes(candle))
        expected_open += config.timeframe.duration

    if expected_open != batch.data_range.end or digest.hexdigest() != batch.source_checksum:
        raise PaperSessionVerificationError("O conteúdo dos candles da timeline foi alterado.")


def _timeline_payload(
    *,
    config: PaperSessionConfig,
    state: PaperSessionState,
    observations: tuple[PaperPortfolioObservation, ...],
) -> dict[str, object]:
    return {
        "schema_version": _TIMELINE_SCHEMA_VERSION,
        "session_id": state.session_id,
        "config_checksum": state.config_checksum,
        "state_id": state.state_id,
        "state_checksum": state.checksum,
        "engine_version": config.engine_version,
        "strategy_lifecycle_version": config.strategy_lifecycle_version,
        "base_asset": config.pair.base,
        "quote_asset": config.pair.quote,
        "timeframe": config.timeframe.code,
        "dataset_version": state.dataset_version,
        "source_checksum": state.source_checksum,
        "data_range": canonical_value(state.data_range),
        "evaluation_range": canonical_value(state.evaluation_range),
        "initial_capital": config.initial_capital,
        "candles_processed": state.candles_processed,
        "observations": canonical_value(observations),
    }


def _timeline_payload_from_timeline(
    timeline: PaperPortfolioTimeline,
) -> dict[str, object]:
    return {
        "schema_version": timeline.schema_version,
        "session_id": timeline.session_id,
        "config_checksum": timeline.config_checksum,
        "state_id": timeline.state_id,
        "state_checksum": timeline.state_checksum,
        "engine_version": timeline.engine_version,
        "strategy_lifecycle_version": timeline.strategy_lifecycle_version,
        "base_asset": timeline.base_asset,
        "quote_asset": timeline.quote_asset,
        "timeframe": timeline.timeframe,
        "dataset_version": timeline.dataset_version,
        "source_checksum": timeline.source_checksum,
        "data_range": canonical_value(timeline.data_range),
        "evaluation_range": canonical_value(timeline.evaluation_range),
        "initial_capital": timeline.initial_capital,
        "candles_processed": timeline.candles_processed,
        "observations": canonical_value(timeline.observations),
    }


def _digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256 for character in value)
    ):
        raise ValueError
    return value


def _positive(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError
    return value
