"""Immutable contracts for deterministic local paper-trading replay."""

from __future__ import annotations

import hashlib
import re
from copy import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.backtesting.domain import (
    EvaluationBacktestConfig,
    ExecutionAssumptions,
    Fill,
    FillLiquidity,
    FillReason,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    RiskLimits,
    SimulatedOrder,
    StrategyDescriptor,
    TimeInForce,
    validate_backtest_config,
)
from app.backtesting.serialization import canonical_json_bytes, canonical_value
from app.market_data.domain import Candle, DataRange, Timeframe, TradingPair, require_utc
from app.paper_trading.errors import (
    InvalidPaperSessionError,
    PaperSessionCorruptError,
    PaperSessionVerificationError,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
MAX_PAPER_DOCUMENT_BYTES = 16 * 1024 * 1024


class PaperRunAction(StrEnum):
    UPDATED = "UPDATED"
    NOOP = "NOOP"


@dataclass(frozen=True, slots=True)
class PaperSessionConfig:
    """One fixed paper-trading identity replayed as local RAW data grows."""

    pair: TradingPair
    timeframe: Timeframe
    start_at: datetime
    warmup_candles: int
    strategy: StrategyDescriptor
    strategy_lifecycle_version: int
    initial_capital: Decimal
    execution: ExecutionAssumptions
    constraints: InstrumentConstraints
    risk_limits: RiskLimits
    history_window: int
    max_candles: int
    max_orders: int
    max_events: int
    engine_version: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.pair, TradingPair) or not isinstance(self.timeframe, Timeframe):
                raise ValueError("pair and timeframe must be canonical")
            start_at = require_utc(self.start_at, field_name="start_at")
            if not self.timeframe.validate_open_time(start_at):
                raise ValueError("start_at must be aligned to timeframe")
            if (
                isinstance(self.warmup_candles, bool)
                or not isinstance(self.warmup_candles, int)
                or self.warmup_candles < 0
            ):
                raise ValueError("warmup_candles must be a nonnegative integer")
            if self.warmup_candles > self.history_window:
                raise ValueError("warmup_candles must not exceed history_window")
            if type(
                self.strategy_lifecycle_version
            ) is not int or self.strategy_lifecycle_version not in {1, 2}:
                raise ValueError("strategy lifecycle version is invalid")
            if self.warmup_candles > 0 and self.strategy_lifecycle_version != 2:
                raise ValueError("positive warmup requires strategy lifecycle 2")
            if self.execution.force_close_at_end:
                raise ValueError("paper trading must not force-close at cycle end")
            if (
                type(self.schema_version) is not int
                or self.schema_version not in _SUPPORTED_SCHEMA_VERSIONS
            ):
                raise ValueError("paper session schema version is unsupported")
            context_start = start_at - self.warmup_candles * self.timeframe.duration
            context_end = start_at + self.timeframe.duration
            EvaluationBacktestConfig(
                snapshot_id="paper-session-validation",
                data_range=DataRange(context_start, context_end),
                evaluation_range=DataRange(start_at, context_end),
                strategy_lifecycle_version=self.strategy_lifecycle_version,
                strategy=self.strategy,
                initial_capital=self.initial_capital,
                execution=self.execution,
                constraints=self.constraints,
                risk_limits=self.risk_limits,
                history_window=self.history_window,
                max_candles=self.max_candles,
                max_orders=self.max_orders,
                max_events=self.max_events,
                engine_version=self.engine_version,
                schema_version=2,
            )
            object.__setattr__(self, "start_at", start_at)
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None

    @property
    def context_start(self) -> datetime:
        return self.start_at - self.warmup_candles * self.timeframe.duration


@dataclass(frozen=True, slots=True)
class PaperCandleBatch:
    """One immutable source projection used by a replay cycle."""

    data_range: DataRange
    dataset_version: str
    source_checksum: str
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.data_range, DataRange):
            raise InvalidPaperSessionError("O intervalo de candles é inválido.")
        for digest in (self.dataset_version, self.source_checksum):
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise InvalidPaperSessionError("A identidade do dataset é inválida.")
        if not isinstance(self.candles, tuple) or not self.candles:
            raise InvalidPaperSessionError("O replay exige ao menos um candle.")
        if any(not isinstance(candle, Candle) for candle in self.candles):
            raise InvalidPaperSessionError("O lote contém candle inválido.")


@dataclass(frozen=True, slots=True)
class PaperSessionState:
    """Complete verified state produced by replaying one session."""

    session_id: str
    config_checksum: str
    dataset_version: str
    source_checksum: str
    data_range: DataRange
    evaluation_range: DataRange
    candles_processed: int
    last_candle_open_time: datetime
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[Fill, ...]
    portfolio: PortfolioSnapshot
    risk_halt: bool
    replayed_at: datetime
    state_id: str
    checksum: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        try:
            validate_paper_session_state(self)
        except PaperSessionCorruptError:
            raise
        except Exception:
            raise PaperSessionCorruptError() from None


@dataclass(frozen=True, slots=True)
class PaperSessionStateSummary:
    """Small verified projection used by paginated read-only listings."""

    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    evaluation_end: datetime
    last_candle_open_time: datetime
    candles_processed: int
    orders_count: int
    fills_count: int
    portfolio: PortfolioSnapshot
    risk_halt: bool
    replayed_at: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        try:
            validate_paper_state_summary(self)
        except PaperSessionCorruptError:
            raise
        except Exception:
            raise PaperSessionCorruptError() from None


@dataclass(frozen=True, slots=True)
class PaperRunResult:
    action: PaperRunAction
    state: PaperSessionState

    def __post_init__(self) -> None:
        if not isinstance(self.action, PaperRunAction) or not isinstance(
            self.state, PaperSessionState
        ):
            raise InvalidPaperSessionError("O resultado do ciclo de paper trading é inválido.")
        validate_paper_session_state(self.state)


def paper_state_summary(state: PaperSessionState) -> PaperSessionStateSummary:
    validate_paper_session_state(state)
    return PaperSessionStateSummary(
        session_id=state.session_id,
        config_checksum=state.config_checksum,
        state_id=state.state_id,
        state_checksum=state.checksum,
        evaluation_end=state.evaluation_range.end,
        last_candle_open_time=state.last_candle_open_time,
        candles_processed=state.candles_processed,
        orders_count=len(state.orders),
        fills_count=len(state.fills),
        portfolio=state.portfolio,
        risk_halt=state.risk_halt,
        replayed_at=state.replayed_at,
    )


def validate_paper_state_summary(summary: PaperSessionStateSummary) -> None:
    if not isinstance(summary, PaperSessionStateSummary):
        raise PaperSessionCorruptError()
    if type(summary.schema_version) is not int or summary.schema_version != 1:
        raise PaperSessionCorruptError()
    for digest in (
        summary.session_id,
        summary.config_checksum,
        summary.state_id,
        summary.state_checksum,
    ):
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise PaperSessionCorruptError()
    evaluation_end = _utc(summary.evaluation_end, "evaluation_end")
    last_open = _utc(summary.last_candle_open_time, "last_candle_open_time")
    _utc(summary.replayed_at, "replayed_at")
    if last_open >= evaluation_end:
        raise PaperSessionCorruptError()
    if type(summary.candles_processed) is not int or summary.candles_processed < 1:
        raise PaperSessionCorruptError()
    if type(summary.orders_count) is not int or summary.orders_count < 0:
        raise PaperSessionCorruptError()
    if (
        type(summary.fills_count) is not int
        or summary.fills_count < 0
        or summary.fills_count > summary.orders_count
    ):
        raise PaperSessionCorruptError()
    _revalidate_portfolio(summary.portfolio)
    if type(summary.risk_halt) is not bool:
        raise PaperSessionCorruptError()


def validate_paper_state_summary_against_config(
    summary: PaperSessionStateSummary,
    config: PaperSessionConfig,
) -> None:
    _revalidate_config(config)
    validate_paper_state_summary(summary)
    duration = config.timeframe.duration
    expected_session_id = paper_session_id(config)
    if (
        summary.session_id != expected_session_id
        or summary.config_checksum != paper_config_checksum(config)
        or summary.evaluation_end <= config.start_at
        or summary.last_candle_open_time != summary.evaluation_end - duration
        or (summary.evaluation_end - config.start_at) % duration != timedelta(0)
        or summary.candles_processed != (summary.evaluation_end - config.start_at) // duration
        or summary.orders_count > min(config.max_orders, config.risk_limits.max_total_orders)
        or summary.fills_count > summary.orders_count
    ):
        raise PaperSessionVerificationError()


def validate_paper_state_summary_against_state(
    summary: PaperSessionStateSummary,
    state: PaperSessionState,
) -> None:
    validate_paper_state_summary(summary)
    validate_paper_session_state(state)
    if summary != paper_state_summary(state):
        raise PaperSessionVerificationError()


def paper_config_payload(config: PaperSessionConfig) -> dict[str, object]:
    _revalidate_config(config)
    return {
        "schema_version": config.schema_version,
        "pair": {"base": config.pair.base, "quote": config.pair.quote},
        "timeframe": config.timeframe.code,
        "start_at": config.start_at.isoformat(),
        "warmup_candles": config.warmup_candles,
        "strategy": {
            "name": config.strategy.name,
            "version": config.strategy.version,
            "parameters": [
                {"name": key, **_parameter_payload(value)}
                for key, value in config.strategy.parameters
            ],
        },
        "strategy_lifecycle_version": config.strategy_lifecycle_version,
        "initial_capital": _decimal_text(config.initial_capital),
        "execution": canonical_value(config.execution),
        "constraints": canonical_value(config.constraints),
        "risk_limits": canonical_value(config.risk_limits),
        "history_window": config.history_window,
        "max_candles": config.max_candles,
        "max_orders": config.max_orders,
        "max_events": config.max_events,
        "engine_version": config.engine_version,
    }


def _parameter_payload(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": _decimal_text(value)}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    raise InvalidPaperSessionError("Parâmetro de estratégia não suportado.")


def _decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidPaperSessionError("Valor Decimal inválido.")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def paper_config_checksum(config: PaperSessionConfig) -> str:
    return hashlib.sha256(canonical_json_bytes(paper_config_payload(config))).hexdigest()


def paper_session_id(config: PaperSessionConfig) -> str:
    encoded = b"adt-paper-session-v1\x00" + canonical_json_bytes(paper_config_payload(config))
    return hashlib.sha256(encoded).hexdigest()


def _paper_state_semantic_payload_unchecked(
    state: PaperSessionState,
) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "session_id": state.session_id,
        "config_checksum": state.config_checksum,
        "dataset_version": state.dataset_version,
        "source_checksum": state.source_checksum,
        "data_range": canonical_value(state.data_range),
        "evaluation_range": canonical_value(state.evaluation_range),
        "candles_processed": state.candles_processed,
        "last_candle_open_time": state.last_candle_open_time.isoformat(),
        "orders": canonical_value(state.orders),
        "fills": canonical_value(state.fills),
        "portfolio": canonical_value(state.portfolio),
        "risk_halt": state.risk_halt,
    }


def paper_state_semantic_payload(state: PaperSessionState) -> dict[str, object]:
    validate_paper_session_state(state)
    return _paper_state_semantic_payload_unchecked(state)


def paper_state_id_from_payload(payload: dict[str, object]) -> str:
    if not isinstance(payload, dict):
        raise PaperSessionCorruptError()
    return hashlib.sha256(b"adt-paper-state-v1\x00" + canonical_json_bytes(payload)).hexdigest()


def paper_state_checksum(state: PaperSessionState) -> str:
    validate_paper_session_state(state)
    payload = _paper_state_semantic_payload_unchecked(state)
    document = {
        **payload,
        "replayed_at": state.replayed_at.isoformat(),
        "state_id": state.state_id,
    }
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def build_paper_session_state(
    *,
    config: PaperSessionConfig,
    batch: PaperCandleBatch,
    candles_processed: int,
    orders: tuple[SimulatedOrder, ...],
    fills: tuple[Fill, ...],
    portfolio: PortfolioSnapshot,
    risk_halt: bool,
    replayed_at: datetime,
) -> PaperSessionState:
    session_id = paper_session_id(config)
    evaluation_range = DataRange(config.start_at, batch.data_range.end)
    last_open = batch.candles[-1].open_time
    base = {
        "schema_version": 1,
        "session_id": session_id,
        "config_checksum": paper_config_checksum(config),
        "dataset_version": batch.dataset_version,
        "source_checksum": batch.source_checksum,
        "data_range": canonical_value(batch.data_range),
        "evaluation_range": canonical_value(evaluation_range),
        "candles_processed": candles_processed,
        "last_candle_open_time": last_open.isoformat(),
        "orders": canonical_value(orders),
        "fills": canonical_value(fills),
        "portfolio": canonical_value(portfolio),
        "risk_halt": risk_halt,
    }
    state_id = paper_state_id_from_payload(base)
    replayed_at = require_utc(replayed_at, field_name="replayed_at")
    checksum = hashlib.sha256(
        canonical_json_bytes({**base, "replayed_at": replayed_at.isoformat(), "state_id": state_id})
    ).hexdigest()
    return PaperSessionState(
        session_id=session_id,
        config_checksum=paper_config_checksum(config),
        dataset_version=batch.dataset_version,
        source_checksum=batch.source_checksum,
        data_range=batch.data_range,
        evaluation_range=evaluation_range,
        candles_processed=candles_processed,
        last_candle_open_time=last_open,
        orders=orders,
        fills=fills,
        portfolio=portfolio,
        risk_halt=risk_halt,
        replayed_at=replayed_at,
        state_id=state_id,
        checksum=checksum,
    )


def validate_paper_session_state(state: PaperSessionState) -> None:
    if not isinstance(state, PaperSessionState):
        raise PaperSessionCorruptError()
    if type(state.schema_version) is not int or state.schema_version not in (
        _SUPPORTED_SCHEMA_VERSIONS
    ):
        raise PaperSessionCorruptError()
    for digest in (
        state.session_id,
        state.config_checksum,
        state.dataset_version,
        state.source_checksum,
        state.state_id,
        state.checksum,
    ):
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise PaperSessionCorruptError()
    _revalidate_data_range(state.data_range)
    _revalidate_data_range(state.evaluation_range)
    if (
        state.evaluation_range.start < state.data_range.start
        or state.evaluation_range.end != state.data_range.end
    ):
        raise PaperSessionCorruptError()
    if type(state.candles_processed) is not int or state.candles_processed < 1:
        raise PaperSessionCorruptError()
    if not isinstance(state.orders, tuple):
        raise PaperSessionCorruptError()
    if not isinstance(state.fills, tuple):
        raise PaperSessionCorruptError()
    _revalidate_orders_and_fills(state.orders, state.fills)
    _revalidate_portfolio(state.portfolio)
    if type(state.risk_halt) is not bool:
        raise PaperSessionCorruptError()
    last_open = _utc(state.last_candle_open_time, "last_candle_open_time")
    replayed_at = _utc(state.replayed_at, "replayed_at")
    if last_open < state.evaluation_range.start or last_open >= state.data_range.end:
        raise PaperSessionCorruptError()
    semantic = _paper_state_semantic_payload_unchecked(state)
    if paper_state_id_from_payload(semantic) != state.state_id:
        raise PaperSessionCorruptError()
    document = {
        **semantic,
        "replayed_at": replayed_at.isoformat(),
        "state_id": state.state_id,
    }
    if hashlib.sha256(canonical_json_bytes(document)).hexdigest() != state.checksum:
        raise PaperSessionCorruptError()


def validate_paper_state_against_config(
    state: PaperSessionState,
    config: PaperSessionConfig,
) -> None:
    _revalidate_config(config)
    validate_paper_session_state(state)
    expected_session_id = paper_session_id(config)
    if (
        state.session_id != expected_session_id
        or state.config_checksum != paper_config_checksum(config)
        or state.data_range.start != config.context_start
        or state.evaluation_range.start != config.start_at
        or state.evaluation_range.end != state.data_range.end
    ):
        raise PaperSessionVerificationError()
    duration = config.timeframe.duration
    if (state.data_range.end - state.data_range.start) % duration != timedelta(0):
        raise PaperSessionVerificationError()
    expected_context = (state.data_range.end - state.data_range.start) // duration
    expected_evaluation = (state.evaluation_range.end - state.evaluation_range.start) // duration
    if (
        expected_context > config.max_candles
        or expected_evaluation < 1
        or state.candles_processed != expected_evaluation
        or state.last_candle_open_time != state.data_range.end - duration
        or len(state.orders) > min(config.max_orders, config.risk_limits.max_total_orders)
        or len(state.fills) > len(state.orders)
    ):
        raise PaperSessionVerificationError()


def _revalidate_data_range(value: object) -> None:
    if not isinstance(value, DataRange):
        raise PaperSessionCorruptError()
    try:
        candidate = DataRange(value.start, value.end)
    except Exception:
        raise PaperSessionCorruptError() from None
    if candidate != value:
        raise PaperSessionCorruptError()


def _revalidate_orders_and_fills(
    orders: tuple[SimulatedOrder, ...],
    fills: tuple[Fill, ...],
) -> None:
    order_ids: set[str] = set()
    sequences: list[int] = []
    for order in orders:
        if not isinstance(order, SimulatedOrder):
            raise PaperSessionCorruptError()
        order_candidate = copy(order)
        try:
            SimulatedOrder.__post_init__(order_candidate)
        except Exception:
            raise PaperSessionCorruptError() from None
        if (
            order_candidate != order
            or not isinstance(order.status, OrderStatus)
            or not isinstance(order.intent, OrderIntent)
            or not isinstance(order.intent.side, OrderSide)
            or not isinstance(order.intent.order_type, OrderType)
            or not isinstance(order.intent.time_in_force, TimeInForce)
            or order.order_id in order_ids
        ):
            raise PaperSessionCorruptError()
        order_ids.add(order.order_id)
        sequences.append(order.created_sequence)
    if sequences != list(range(1, len(orders) + 1)):
        raise PaperSessionCorruptError()

    fill_ids: set[str] = set()
    filled_order_ids: set[str] = set()
    for fill in fills:
        if not isinstance(fill, Fill):
            raise PaperSessionCorruptError()
        fill_candidate = copy(fill)
        try:
            Fill.__post_init__(fill_candidate)
        except Exception:
            raise PaperSessionCorruptError() from None
        if (
            fill_candidate != fill
            or not isinstance(fill.reason, FillReason)
            or not isinstance(fill.liquidity, FillLiquidity)
            or not isinstance(fill.side, OrderSide)
            or fill.fill_id in fill_ids
            or fill.order_id in filled_order_ids
            or fill.order_id not in order_ids
        ):
            raise PaperSessionCorruptError()
        fill_ids.add(fill.fill_id)
        filled_order_ids.add(fill.order_id)
    status_by_id = {order.order_id: order.status for order in orders}
    if any(status_by_id[order_id] is not OrderStatus.FILLED for order_id in filled_order_ids):
        raise PaperSessionCorruptError()
    if any(
        order.status is OrderStatus.FILLED and order.order_id not in filled_order_ids
        for order in orders
    ):
        raise PaperSessionCorruptError()


def _revalidate_portfolio(value: object) -> None:
    if not isinstance(value, PortfolioSnapshot):
        raise PaperSessionCorruptError()
    candidate = copy(value)
    try:
        PortfolioSnapshot.__post_init__(candidate)
    except Exception:
        raise PaperSessionCorruptError() from None
    if candidate != value:
        raise PaperSessionCorruptError()


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise PaperSessionCorruptError()
    try:
        return require_utc(value, field_name=field_name)
    except Exception:
        raise PaperSessionCorruptError() from None


def _revalidate_config(config: PaperSessionConfig) -> None:
    if not isinstance(config, PaperSessionConfig):
        raise InvalidPaperSessionError()
    try:
        candidate = PaperSessionConfig(
            pair=config.pair,
            timeframe=config.timeframe,
            start_at=config.start_at,
            warmup_candles=config.warmup_candles,
            strategy=config.strategy,
            strategy_lifecycle_version=config.strategy_lifecycle_version,
            initial_capital=config.initial_capital,
            execution=config.execution,
            constraints=config.constraints,
            risk_limits=config.risk_limits,
            history_window=config.history_window,
            max_candles=config.max_candles,
            max_orders=config.max_orders,
            max_events=config.max_events,
            engine_version=config.engine_version,
            schema_version=config.schema_version,
        )
        context_end = config.start_at + config.timeframe.duration
        validate_backtest_config(
            EvaluationBacktestConfig(
                snapshot_id="paper-session-validation",
                data_range=DataRange(config.context_start, context_end),
                evaluation_range=DataRange(config.start_at, context_end),
                strategy_lifecycle_version=config.strategy_lifecycle_version,
                strategy=config.strategy,
                initial_capital=config.initial_capital,
                execution=config.execution,
                constraints=config.constraints,
                risk_limits=config.risk_limits,
                history_window=config.history_window,
                max_candles=config.max_candles,
                max_orders=config.max_orders,
                max_events=config.max_events,
                engine_version=config.engine_version,
                schema_version=2,
            )
        )
    except InvalidPaperSessionError:
        raise
    except Exception:
        raise InvalidPaperSessionError() from None
    if candidate != config:
        raise InvalidPaperSessionError("A configuração da sessão não é canônica.")
