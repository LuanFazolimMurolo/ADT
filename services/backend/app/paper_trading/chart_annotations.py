"""Bounded verified paper-session annotations for financial charts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.backtesting.domain import (
    FillLiquidity,
    FillReason,
    OrderSide,
    OrderStatus,
    OrderType,
    StrategyParameters,
    TimeInForce,
)
from app.backtesting.serialization import canonical_json_bytes, canonical_value
from app.market_data.domain import Timeframe, TradingPair, require_utc
from app.paper_trading.domain import (
    paper_config_checksum,
    paper_session_id,
    validate_paper_state_against_config,
)
from app.paper_trading.errors import (
    InvalidPaperSessionError,
    PaperSessionVerificationError,
)
from app.paper_trading.journal import build_paper_trade_journal
from app.paper_trading.repository import PaperTradingRepository

PAPER_CHART_ANNOTATION_DEFAULT_LIMIT = 1_000
PAPER_CHART_ANNOTATION_MAX_LIMIT = 5_000
ENGINE_STOP_LOSS_CLIENT_TAG = "engine-stop-loss"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PaperChartFillRole(StrEnum):
    """Verified economic role of one journal execution."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"


@dataclass(frozen=True, slots=True)
class PaperChartAnnotationQuery:
    """One half-open UTC annotation request for a single paper session."""

    session_id: str
    range_start: datetime
    range_end: datetime
    limit: int = PAPER_CHART_ANNOTATION_DEFAULT_LIMIT

    def __post_init__(self) -> None:
        try:
            if _SHA256.fullmatch(self.session_id) is None:
                raise ValueError("session_id must be one lowercase SHA-256 digest")
            range_start = require_utc(
                self.range_start,
                field_name="paper_chart_range_start",
            )
            range_end = require_utc(
                self.range_end,
                field_name="paper_chart_range_end",
            )
            if range_start >= range_end:
                raise ValueError("paper chart range must be increasing and half-open")
            if (
                type(self.limit) is not int
                or self.limit < 1
                or self.limit > PAPER_CHART_ANNOTATION_MAX_LIMIT
            ):
                raise ValueError("paper chart annotation limit is invalid")
            object.__setattr__(self, "range_start", range_start)
            object.__setattr__(self, "range_end", range_end)
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaperChartOrderAnnotation:
    """One verified order-creation annotation."""

    order_id: str
    created_sequence: int
    created_at: datetime
    opened_at: datetime | None
    terminal_at: datetime | None
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    status: OrderStatus
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    client_tag: str | None
    rejection_code: str | None
    is_engine_protective_stop: bool


@dataclass(frozen=True, slots=True)
class PaperChartFillAnnotation:
    """One verified fill classified through the deterministic trade journal."""

    fill_id: str
    order_id: str
    trade_id: str
    trade_sequence: int
    role: PaperChartFillRole
    event_time: datetime
    candle_index: int
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    client_tag: str | None
    fill_reason: FillReason
    liquidity: FillLiquidity
    quantity: Decimal
    base_price: Decimal
    execution_price: Decimal
    notional: Decimal
    fee: Decimal
    slippage_cost: Decimal
    is_engine_protective_stop: bool


@dataclass(frozen=True, slots=True)
class PaperChartAnnotationPage:
    """One deterministic state-bound annotation projection."""

    session_id: str
    config_checksum: str
    state_available: bool
    state_id: str | None
    state_checksum: str | None
    dataset_version: str | None
    source_checksum: str | None
    pair: TradingPair
    timeframe: Timeframe
    strategy_name: str
    strategy_version: str
    strategy_parameters: StrategyParameters
    ema_fast_period: int | None
    ema_slow_period: int | None
    range_start: datetime
    range_end: datetime
    limit: int
    orders: tuple[PaperChartOrderAnnotation, ...]
    fills: tuple[PaperChartFillAnnotation, ...]
    last_candle_open_time: datetime | None
    replayed_at: datetime | None
    schema_version: int = 1
    content_checksum: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            PaperChartAnnotationQuery(
                session_id=self.session_id,
                range_start=self.range_start,
                range_end=self.range_end,
                limit=self.limit,
            )
            if _SHA256.fullmatch(self.config_checksum) is None:
                raise ValueError("config_checksum must be one lowercase SHA-256 digest")
            if type(self.state_available) is not bool:
                raise ValueError("state_available must be boolean")
            optional_digests = (
                self.state_id,
                self.state_checksum,
                self.dataset_version,
                self.source_checksum,
            )
            if self.state_available:
                if any(
                    value is None or _SHA256.fullmatch(value) is None for value in optional_digests
                ):
                    raise ValueError("available state requires complete identities")
                if self.last_candle_open_time is None or self.replayed_at is None:
                    raise ValueError("available state requires timestamps")
                require_utc(
                    self.last_candle_open_time,
                    field_name="paper_chart_last_candle_open_time",
                )
                require_utc(
                    self.replayed_at,
                    field_name="paper_chart_replayed_at",
                )
            elif any(value is not None for value in optional_digests) or any(
                value is not None for value in (self.last_candle_open_time, self.replayed_at)
            ):
                raise ValueError("unavailable state must not expose state identities")
            if not isinstance(self.pair, TradingPair) or not isinstance(
                self.timeframe,
                Timeframe,
            ):
                raise ValueError("pair and timeframe must be canonical")
            if type(self.schema_version) is not int or self.schema_version != 1:
                raise ValueError("annotation schema version is unsupported")
            if len(self.orders) + len(self.fills) > self.limit:
                raise ValueError("annotation page exceeds its explicit limit")
            if (
                tuple(
                    sorted(
                        self.orders,
                        key=lambda item: (
                            item.created_at,
                            item.created_sequence,
                            item.order_id,
                        ),
                    )
                )
                != self.orders
            ):
                raise ValueError("orders are not in canonical chronological order")
            if (
                tuple(
                    sorted(
                        self.fills,
                        key=lambda item: (
                            item.event_time,
                            item.candle_index,
                            item.fill_id,
                        ),
                    )
                )
                != self.fills
            ):
                raise ValueError("fills are not in canonical chronological order")
            if len({item.order_id for item in self.orders}) != len(self.orders):
                raise ValueError("order annotations contain duplicates")
            if len({item.fill_id for item in self.fills}) != len(self.fills):
                raise ValueError("fill annotations contain duplicates")
            fast = self.ema_fast_period
            slow = self.ema_slow_period
            if (fast is None) != (slow is None):
                raise ValueError("EMA periods must be both present or both absent")
            if fast is not None and slow is not None and not (1 <= fast < slow):
                raise ValueError("EMA periods are invalid")
            object.__setattr__(
                self,
                "content_checksum",
                paper_chart_annotation_checksum(self),
            )
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None

    @property
    def count(self) -> int:
        return len(self.orders) + len(self.fills)


class PaperChartAnnotationReadService:
    """Project verified state events into one bounded chart interval."""

    def __init__(self, repository: PaperTradingRepository) -> None:
        if not isinstance(repository, PaperTradingRepository):
            raise InvalidPaperSessionError("O repositório de anotações do gráfico é inválido.")
        self._repository = repository

    def read_page(
        self,
        query: PaperChartAnnotationQuery,
    ) -> PaperChartAnnotationPage:
        if not isinstance(query, PaperChartAnnotationQuery):
            raise InvalidPaperSessionError("A consulta de anotações é inválida.")
        PaperChartAnnotationQuery.__post_init__(query)

        config = self._repository.load_config(query.session_id)
        if paper_session_id(config) != query.session_id:
            raise PaperSessionVerificationError("A identidade da configuração da sessão divergiu.")

        state = self._repository.load_state(query.session_id)
        fast_period, slow_period = _ema_periods(
            config.strategy.name,
            config.strategy.parameters,
        )

        if state is None:
            return PaperChartAnnotationPage(
                session_id=query.session_id,
                config_checksum=paper_config_checksum(config),
                state_available=False,
                state_id=None,
                state_checksum=None,
                dataset_version=None,
                source_checksum=None,
                pair=config.pair,
                timeframe=config.timeframe,
                strategy_name=config.strategy.name,
                strategy_version=config.strategy.version,
                strategy_parameters=config.strategy.parameters,
                ema_fast_period=fast_period,
                ema_slow_period=slow_period,
                range_start=query.range_start,
                range_end=query.range_end,
                limit=query.limit,
                orders=(),
                fills=(),
                last_candle_open_time=None,
                replayed_at=None,
            )

        validate_paper_state_against_config(state, config)
        journal = build_paper_trade_journal(config, state)

        execution_roles: dict[
            str,
            tuple[str, int, PaperChartFillRole],
        ] = {}
        for trade in journal.trades:
            for execution in trade.entry_executions:
                execution_roles[execution.fill_id] = (
                    trade.trade_id,
                    trade.sequence,
                    PaperChartFillRole.ENTRY,
                )
            for execution in trade.exit_executions:
                execution_roles[execution.fill_id] = (
                    trade.trade_id,
                    trade.sequence,
                    PaperChartFillRole.EXIT,
                )
        if len(execution_roles) != len(state.fills):
            raise PaperSessionVerificationError(
                "A classificação econômica dos fills ficou incompleta."
            )

        selected_orders = tuple(
            sorted(
                (
                    order
                    for order in state.orders
                    if query.range_start <= order.created_at < query.range_end
                ),
                key=lambda item: (
                    item.created_at,
                    item.created_sequence,
                    item.order_id,
                ),
            )
        )
        selected_fills = tuple(
            sorted(
                (
                    fill
                    for fill in state.fills
                    if query.range_start <= fill.event_time < query.range_end
                ),
                key=lambda item: (
                    item.event_time,
                    item.candle_index,
                    item.fill_id,
                ),
            )
        )

        count = len(selected_orders) + len(selected_fills)
        if count > query.limit:
            raise InvalidPaperSessionError(
                f"A projeção do gráfico excede o limite de {query.limit} anotações."
            )

        orders = tuple(
            PaperChartOrderAnnotation(
                order_id=order.order_id,
                created_sequence=order.created_sequence,
                created_at=order.created_at,
                opened_at=order.opened_at,
                terminal_at=order.terminal_at,
                side=order.intent.side,
                order_type=order.intent.order_type,
                time_in_force=order.intent.time_in_force,
                status=order.status,
                quantity=order.intent.quantity,
                limit_price=order.intent.limit_price,
                stop_price=order.intent.stop_price,
                client_tag=order.intent.client_tag,
                rejection_code=order.rejection_code,
                is_engine_protective_stop=(order.intent.client_tag == ENGINE_STOP_LOSS_CLIENT_TAG),
            )
            for order in selected_orders
        )

        order_by_id = {order.order_id: order for order in state.orders}
        fills: list[PaperChartFillAnnotation] = []
        for fill in selected_fills:
            try:
                order = order_by_id[fill.order_id]
                trade_id, trade_sequence, role = execution_roles[fill.fill_id]
            except KeyError:
                raise PaperSessionVerificationError(
                    "Uma anotação de fill perdeu seu vínculo verificado."
                ) from None
            fills.append(
                PaperChartFillAnnotation(
                    fill_id=fill.fill_id,
                    order_id=fill.order_id,
                    trade_id=trade_id,
                    trade_sequence=trade_sequence,
                    role=role,
                    event_time=fill.event_time,
                    candle_index=fill.candle_index,
                    side=fill.side,
                    order_type=order.intent.order_type,
                    time_in_force=order.intent.time_in_force,
                    client_tag=order.intent.client_tag,
                    fill_reason=fill.reason,
                    liquidity=fill.liquidity,
                    quantity=fill.quantity,
                    base_price=fill.base_price,
                    execution_price=fill.execution_price,
                    notional=fill.notional,
                    fee=fill.fee,
                    slippage_cost=fill.slippage_cost,
                    is_engine_protective_stop=(
                        order.intent.client_tag == ENGINE_STOP_LOSS_CLIENT_TAG
                    ),
                )
            )

        return PaperChartAnnotationPage(
            session_id=query.session_id,
            config_checksum=state.config_checksum,
            state_available=True,
            state_id=state.state_id,
            state_checksum=state.checksum,
            dataset_version=state.dataset_version,
            source_checksum=state.source_checksum,
            pair=config.pair,
            timeframe=config.timeframe,
            strategy_name=config.strategy.name,
            strategy_version=config.strategy.version,
            strategy_parameters=config.strategy.parameters,
            ema_fast_period=fast_period,
            ema_slow_period=slow_period,
            range_start=query.range_start,
            range_end=query.range_end,
            limit=query.limit,
            orders=orders,
            fills=tuple(fills),
            last_candle_open_time=state.last_candle_open_time,
            replayed_at=state.replayed_at,
        )


def _ema_periods(
    strategy_name: str,
    parameters: StrategyParameters,
) -> tuple[int | None, int | None]:
    if strategy_name != "ema-cross-example":
        return None, None
    values = dict(parameters)
    fast = values.get("fast_period")
    slow = values.get("slow_period")
    if type(fast) is not int or type(slow) is not int or not 1 <= fast < slow:
        raise PaperSessionVerificationError("Os parâmetros EMA da sessão são incompatíveis.")
    return fast, slow


def paper_chart_annotation_checksum(
    page: PaperChartAnnotationPage,
) -> str:
    payload = {
        "schema_version": page.schema_version,
        "session_id": page.session_id,
        "config_checksum": page.config_checksum,
        "state_available": page.state_available,
        "state_id": page.state_id,
        "state_checksum": page.state_checksum,
        "dataset_version": page.dataset_version,
        "source_checksum": page.source_checksum,
        "pair": canonical_value(page.pair),
        "timeframe": page.timeframe.code,
        "strategy_name": page.strategy_name,
        "strategy_version": page.strategy_version,
        "strategy_parameters": canonical_value(page.strategy_parameters),
        "ema_fast_period": page.ema_fast_period,
        "ema_slow_period": page.ema_slow_period,
        "range_start": page.range_start.isoformat(),
        "range_end": page.range_end.isoformat(),
        "limit": page.limit,
        "orders": canonical_value(page.orders),
        "fills": canonical_value(page.fills),
        "last_candle_open_time": (
            None if page.last_candle_open_time is None else page.last_candle_open_time.isoformat()
        ),
        "replayed_at": (None if page.replayed_at is None else page.replayed_at.isoformat()),
    }
    return hashlib.sha256(
        b"adt-paper-chart-annotations-v1\x00" + canonical_json_bytes(payload)
    ).hexdigest()
