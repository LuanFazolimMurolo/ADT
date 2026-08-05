"""Deterministic read models for the paper-trading performance dashboard."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.backtesting.domain import OrderStatus, PortfolioSnapshot, StrategyDescriptor
from app.indicators.regime import MarketRegimePoint
from app.market_data.domain import Timeframe, TradingPair, require_utc
from app.paper_trading.continuous import (
    PaperRunnerCycleStatus,
    PaperRunnerSessionResult,
    PaperRunnerSessionStatus,
    PaperRunnerState,
    validate_paper_runner_state,
)
from app.paper_trading.domain import (
    PaperSessionConfig,
    PaperSessionState,
    paper_session_id,
    validate_paper_state_against_config,
)
from app.paper_trading.errors import InvalidPaperSessionError
from app.paper_trading.repository import PaperTradingRepository

_PERCENT = Decimal("100")
_SESSION_ID = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PaperPerformanceMetrics:
    """Verified monetary performance derived from one persisted session state."""

    initial_capital: Decimal
    equity: Decimal
    total_pnl: Decimal
    return_pct: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    drawdown: Decimal
    drawdown_pct: Decimal
    total_fees: Decimal
    total_slippage_cost: Decimal

    def __post_init__(self) -> None:
        values = (
            self.initial_capital,
            self.equity,
            self.total_pnl,
            self.return_pct,
            self.realized_pnl,
            self.unrealized_pnl,
            self.drawdown,
            self.drawdown_pct,
            self.total_fees,
            self.total_slippage_cost,
        )
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise InvalidPaperSessionError("As métricas do dashboard são inválidas.")
        if self.initial_capital <= 0 or self.equity < 0:
            raise InvalidPaperSessionError("O capital do dashboard é inválido.")
        if self.drawdown < 0 or self.drawdown_pct < 0:
            raise InvalidPaperSessionError("O drawdown do dashboard é inválido.")
        if self.total_fees < 0 or self.total_slippage_cost < 0:
            raise InvalidPaperSessionError("Os custos do dashboard são inválidos.")
        if self.total_pnl != self.equity - self.initial_capital:
            raise InvalidPaperSessionError("O PnL do dashboard é inconsistente.")
        if self.return_pct != self.total_pnl / self.initial_capital * _PERCENT:
            raise InvalidPaperSessionError("O retorno do dashboard é inconsistente.")


@dataclass(frozen=True, slots=True)
class PaperPositionSnapshot:
    """Current Spot position projection without introducing mutable behavior."""

    is_open: bool
    base_quantity: Decimal
    average_entry_price: Decimal
    cost_basis: Decimal
    market_value: Decimal

    def __post_init__(self) -> None:
        values = (
            self.base_quantity,
            self.average_entry_price,
            self.cost_basis,
            self.market_value,
        )
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in values
        ):
            raise InvalidPaperSessionError("A posição do dashboard é inválida.")
        if type(self.is_open) is not bool or self.is_open != (self.base_quantity > 0):
            raise InvalidPaperSessionError("O estado da posição do dashboard é inválido.")
        if self.is_open:
            if self.average_entry_price <= 0 or self.cost_basis <= 0:
                raise InvalidPaperSessionError("A posição aberta do dashboard é inválida.")
        elif self.average_entry_price != 0 or self.cost_basis != 0 or self.market_value != 0:
            raise InvalidPaperSessionError("A posição encerrada do dashboard é inválida.")


@dataclass(frozen=True, slots=True)
class PaperDashboardRunnerResult:
    """Latest runner result joined to a session card."""

    status: PaperRunnerSessionStatus
    started_at: datetime
    finished_at: datetime
    state_id: str | None
    candles_processed: int | None
    last_candle_open_time: datetime | None
    error_code: str | None
    matches_current_state: bool

    @classmethod
    def from_domain(
        cls,
        value: PaperRunnerSessionResult,
        current_state: PaperSessionState | None,
    ) -> PaperDashboardRunnerResult:
        PaperRunnerSessionResult.__post_init__(value)
        matches_current = (
            current_state is not None
            and value.state_id == current_state.state_id
            and value.candles_processed == current_state.candles_processed
            and value.last_candle_open_time == current_state.last_candle_open_time
        )
        return cls(
            status=value.status,
            started_at=value.started_at,
            finished_at=value.finished_at,
            state_id=value.state_id,
            candles_processed=value.candles_processed,
            last_candle_open_time=value.last_candle_open_time,
            error_code=value.error_code,
            matches_current_state=matches_current,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.status, PaperRunnerSessionStatus):
            raise InvalidPaperSessionError("O resultado do runner no dashboard é inválido.")
        started_at = require_utc(self.started_at, field_name="runner_started_at")
        finished_at = require_utc(self.finished_at, field_name="runner_finished_at")
        if finished_at < started_at:
            raise InvalidPaperSessionError("A temporalidade do runner é inválida.")
        if type(self.matches_current_state) is not bool:
            raise InvalidPaperSessionError("A associação do runner é inválida.")
        if self.status is PaperRunnerSessionStatus.FAILED:
            if (
                self.error_code is None
                or self.state_id is not None
                or self.candles_processed is not None
                or self.last_candle_open_time is not None
                or self.matches_current_state
            ):
                raise InvalidPaperSessionError("A falha do runner é inválida.")
        else:
            if (
                self.error_code is not None
                or not isinstance(self.state_id, str)
                or _SESSION_ID.fullmatch(self.state_id) is None
                or type(self.candles_processed) is not int
                or self.candles_processed < 1
                or self.last_candle_open_time is None
            ):
                raise InvalidPaperSessionError("O resultado concluído do runner é inválido.")
            last_candle_open_time = self.last_candle_open_time
            if last_candle_open_time is None:  # pragma: no cover - guarded above
                raise InvalidPaperSessionError("O resultado concluído do runner é inválido.")
            require_utc(
                last_candle_open_time,
                field_name="runner_last_candle_open_time",
            )
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)


@dataclass(frozen=True, slots=True)
class PaperDashboardSession:
    """One verified session card for the read-only performance dashboard."""

    session_id: str
    pair: TradingPair
    timeframe: Timeframe
    strategy: StrategyDescriptor
    initial_capital: Decimal
    state_available: bool
    candles_processed: int | None
    last_candle_open_time: datetime | None
    replayed_at: datetime | None
    orders_count: int
    fills_count: int
    open_orders_count: int
    risk_halt: bool | None
    metrics: PaperPerformanceMetrics | None
    portfolio: PortfolioSnapshot | None
    position: PaperPositionSnapshot | None
    latest_market_regime: MarketRegimePoint | None
    runner: PaperDashboardRunnerResult | None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or _SESSION_ID.fullmatch(self.session_id) is None:
            raise InvalidPaperSessionError("A identidade da sessão no dashboard é inválida.")
        if not isinstance(self.pair, TradingPair) or not isinstance(self.timeframe, Timeframe):
            raise InvalidPaperSessionError("A identidade de mercado do dashboard é inválida.")
        if not isinstance(self.strategy, StrategyDescriptor):
            raise InvalidPaperSessionError("A estratégia do dashboard é inválida.")
        if (
            not isinstance(self.initial_capital, Decimal)
            or not self.initial_capital.is_finite()
            or self.initial_capital <= 0
        ):
            raise InvalidPaperSessionError("O capital configurado do dashboard é inválido.")
        if type(self.state_available) is not bool:
            raise InvalidPaperSessionError("A disponibilidade da sessão é inválida.")
        for value in (self.orders_count, self.fills_count, self.open_orders_count):
            if type(value) is not int or value < 0:
                raise InvalidPaperSessionError("As contagens do dashboard são inválidas.")
        if self.fills_count > self.orders_count or self.open_orders_count > self.orders_count:
            raise InvalidPaperSessionError("As contagens do dashboard são inconsistentes.")
        state_fields = (
            self.candles_processed,
            self.last_candle_open_time,
            self.replayed_at,
            self.risk_halt,
            self.metrics,
            self.portfolio,
            self.position,
        )
        if self.state_available:
            if any(value is None for value in state_fields):
                raise InvalidPaperSessionError("A sessão inicializada está incompleta.")
            if type(self.candles_processed) is not int or self.candles_processed < 1:
                raise InvalidPaperSessionError("A contagem de candles é inválida.")
            if type(self.risk_halt) is not bool:
                raise InvalidPaperSessionError("O estado de risco é inválido.")
            if self.metrics is not None and self.metrics.initial_capital != self.initial_capital:
                raise InvalidPaperSessionError("O capital da sessão é inconsistente.")
        else:
            if any(value is not None for value in state_fields):
                raise InvalidPaperSessionError("A sessão pendente possui estado inesperado.")
            if self.orders_count or self.fills_count or self.open_orders_count:
                raise InvalidPaperSessionError("A sessão pendente possui atividade inesperada.")
            if self.latest_market_regime is not None:
                raise InvalidPaperSessionError("A sessão pendente possui regime inesperado.")


@dataclass(frozen=True, slots=True)
class PaperDashboardPageTotals:
    """Aggregates explicitly limited to the sessions included in one page."""

    sessions_count: int
    initialized_count: int
    pending_count: int
    runner_failed_count: int
    risk_halted_count: int
    open_positions_count: int
    open_orders_count: int
    configured_capital: Decimal
    initialized_capital: Decimal
    equity: Decimal
    total_pnl: Decimal
    return_pct: Decimal
    maximum_drawdown_pct: Decimal

    def __post_init__(self) -> None:
        counts = (
            self.sessions_count,
            self.initialized_count,
            self.pending_count,
            self.runner_failed_count,
            self.risk_halted_count,
            self.open_positions_count,
            self.open_orders_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise InvalidPaperSessionError("Os totais do dashboard são inválidos.")
        if self.initialized_count + self.pending_count != self.sessions_count:
            raise InvalidPaperSessionError("Os totais de sessões são inconsistentes.")
        if any(
            value > self.sessions_count
            for value in (
                self.initialized_count,
                self.pending_count,
                self.runner_failed_count,
                self.risk_halted_count,
                self.open_positions_count,
            )
        ):
            raise InvalidPaperSessionError("Os totais do dashboard excedem a página.")
        decimals = (
            self.configured_capital,
            self.initialized_capital,
            self.equity,
            self.total_pnl,
            self.return_pct,
            self.maximum_drawdown_pct,
        )
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in decimals):
            raise InvalidPaperSessionError("Os valores agregados são inválidos.")
        if (
            self.configured_capital < 0
            or self.initialized_capital < 0
            or self.equity < 0
            or self.maximum_drawdown_pct < 0
            or self.initialized_capital > self.configured_capital
        ):
            raise InvalidPaperSessionError("Os valores agregados são inválidos.")
        if self.total_pnl != self.equity - self.initialized_capital:
            raise InvalidPaperSessionError("O PnL agregado é inconsistente.")
        expected_return = (
            Decimal("0")
            if self.initialized_capital == 0
            else self.total_pnl / self.initialized_capital * _PERCENT
        )
        if self.return_pct != expected_return:
            raise InvalidPaperSessionError("O retorno agregado é inconsistente.")


@dataclass(frozen=True, slots=True)
class PaperDashboardPage:
    """Bounded dashboard page and its page-local aggregate metrics."""

    items: tuple[PaperDashboardSession, ...]
    totals: PaperDashboardPageTotals
    page: int
    page_size: int
    total: int
    total_pages: int
    runner_cycle_index: int | None
    runner_cycle_status: PaperRunnerCycleStatus | None
    runner_finished_at: datetime | None
    runner_next_cycle_at: datetime | None

    def __post_init__(self) -> None:
        if type(self.page) is not int or self.page < 1:
            raise InvalidPaperSessionError("A página do dashboard é inválida.")
        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise InvalidPaperSessionError("O tamanho da página do dashboard é inválido.")
        if type(self.total) is not int or self.total < 0:
            raise InvalidPaperSessionError("O total do dashboard é inválido.")
        expected_pages = (
            0 if self.total == 0 else (self.total + self.page_size - 1) // self.page_size
        )
        if type(self.total_pages) is not int or self.total_pages != expected_pages:
            raise InvalidPaperSessionError("A paginação do dashboard é inconsistente.")
        if not isinstance(self.items, tuple) or len(self.items) > self.page_size:
            raise InvalidPaperSessionError("Os itens do dashboard são inválidos.")
        if any(not isinstance(item, PaperDashboardSession) for item in self.items):
            raise InvalidPaperSessionError("O dashboard contém sessão inválida.")
        ids = tuple(item.session_id for item in self.items)
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise InvalidPaperSessionError("As sessões do dashboard não estão ordenadas.")
        if self.totals.sessions_count != len(self.items):
            raise InvalidPaperSessionError("Os totais não correspondem à página.")
        runner_fields = (
            self.runner_cycle_index,
            self.runner_cycle_status,
            self.runner_finished_at,
            self.runner_next_cycle_at,
        )
        if all(value is None for value in runner_fields):
            return
        if any(value is None for value in runner_fields):
            raise InvalidPaperSessionError("O estado agregado do runner está incompleto.")
        if type(self.runner_cycle_index) is not int or self.runner_cycle_index < 1:
            raise InvalidPaperSessionError("O ciclo do runner é inválido.")
        if not isinstance(self.runner_cycle_status, PaperRunnerCycleStatus):
            raise InvalidPaperSessionError("O status do runner é inválido.")
        runner_finished_at = self.runner_finished_at
        runner_next_cycle_at = self.runner_next_cycle_at
        if runner_finished_at is None or runner_next_cycle_at is None:  # pragma: no cover
            raise InvalidPaperSessionError("O estado agregado do runner está incompleto.")
        require_utc(runner_finished_at, field_name="dashboard_runner_finished_at")
        require_utc(runner_next_cycle_at, field_name="dashboard_runner_next_cycle_at")


class PaperDashboardReadService:
    """Build deterministic dashboard projections from verified local state."""

    def __init__(self, repository: PaperTradingRepository) -> None:
        if not isinstance(repository, PaperTradingRepository):
            raise InvalidPaperSessionError("O repositório do dashboard é inválido.")
        self._repository = repository

    def build_page(
        self,
        *,
        page: int,
        page_size: int,
        runner_state: PaperRunnerState | None = None,
    ) -> PaperDashboardPage:
        if type(page) is not int or page < 1 or page > 100_000:
            raise InvalidPaperSessionError("A página do dashboard é inválida.")
        if type(page_size) is not int or page_size < 1 or page_size > 100:
            raise InvalidPaperSessionError("O tamanho da página do dashboard é inválido.")
        runner_results: dict[str, PaperRunnerSessionResult] = {}
        if runner_state is not None:
            validate_paper_runner_state(runner_state)
            runner_results = {result.session_id: result for result in runner_state.results}
        offset = (page - 1) * page_size
        configs, total = self._repository.list_session_configs_page(
            offset=offset,
            limit=page_size,
        )
        items = tuple(
            self._build_session(config, runner_results.get(paper_session_id(config)))
            for config in configs
        )
        return PaperDashboardPage(
            items=items,
            totals=_page_totals(items),
            page=page,
            page_size=page_size,
            total=total,
            total_pages=0 if total == 0 else (total + page_size - 1) // page_size,
            runner_cycle_index=None if runner_state is None else runner_state.cycle_index,
            runner_cycle_status=None if runner_state is None else runner_state.status,
            runner_finished_at=None if runner_state is None else runner_state.finished_at,
            runner_next_cycle_at=None if runner_state is None else runner_state.next_cycle_at,
        )

    def _build_session(
        self,
        config: PaperSessionConfig,
        runner_result: PaperRunnerSessionResult | None,
    ) -> PaperDashboardSession:
        session_id = paper_session_id(config)
        state = self._repository.load_state(session_id)
        if state is not None:
            validate_paper_state_against_config(state, config)
        return _session_projection(config, state, runner_result)


def _session_projection(
    config: PaperSessionConfig,
    state: PaperSessionState | None,
    runner_result: PaperRunnerSessionResult | None,
) -> PaperDashboardSession:
    if (
        state is None
        and runner_result is not None
        and runner_result.status is not PaperRunnerSessionStatus.FAILED
    ):
        raise InvalidPaperSessionError(
            "O runner concluiu uma sessão sem estado persistido correspondente."
        )
    runner = (
        None
        if runner_result is None
        else PaperDashboardRunnerResult.from_domain(runner_result, state)
    )
    if state is None:
        return PaperDashboardSession(
            session_id=paper_session_id(config),
            pair=config.pair,
            timeframe=config.timeframe,
            strategy=config.strategy,
            initial_capital=config.initial_capital,
            state_available=False,
            candles_processed=None,
            last_candle_open_time=None,
            replayed_at=None,
            orders_count=0,
            fills_count=0,
            open_orders_count=0,
            risk_halt=None,
            metrics=None,
            portfolio=None,
            position=None,
            latest_market_regime=None,
            runner=runner,
        )
    portfolio = state.portfolio
    metrics = _performance_metrics(config.initial_capital, portfolio)
    position = PaperPositionSnapshot(
        is_open=portfolio.base_quantity > 0,
        base_quantity=portfolio.base_quantity,
        average_entry_price=portfolio.average_entry_price,
        cost_basis=portfolio.cost_basis,
        market_value=portfolio.equity - portfolio.quote_cash,
    )
    return PaperDashboardSession(
        session_id=state.session_id,
        pair=config.pair,
        timeframe=config.timeframe,
        strategy=config.strategy,
        initial_capital=config.initial_capital,
        state_available=True,
        candles_processed=state.candles_processed,
        last_candle_open_time=state.last_candle_open_time,
        replayed_at=state.replayed_at,
        orders_count=len(state.orders),
        fills_count=len(state.fills),
        open_orders_count=sum(
            order.status in {OrderStatus.CREATED, OrderStatus.OPEN} for order in state.orders
        ),
        risk_halt=state.risk_halt,
        metrics=metrics,
        portfolio=portfolio,
        position=position,
        latest_market_regime=state.latest_market_regime,
        runner=runner,
    )


def _performance_metrics(
    initial_capital: Decimal,
    portfolio: PortfolioSnapshot,
) -> PaperPerformanceMetrics:
    return PaperPerformanceMetrics(
        initial_capital=initial_capital,
        equity=portfolio.equity,
        total_pnl=portfolio.equity - initial_capital,
        return_pct=(portfolio.equity - initial_capital) / initial_capital * _PERCENT,
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=portfolio.unrealized_pnl,
        drawdown=portfolio.drawdown,
        drawdown_pct=portfolio.drawdown_pct,
        total_fees=portfolio.total_fees,
        total_slippage_cost=portfolio.total_slippage_cost,
    )


def _page_totals(items: tuple[PaperDashboardSession, ...]) -> PaperDashboardPageTotals:
    initialized = tuple(item for item in items if item.metrics is not None)
    configured_capital = sum((item.initial_capital for item in items), Decimal("0"))
    initialized_capital = sum(
        (item.metrics.initial_capital for item in initialized if item.metrics is not None),
        Decimal("0"),
    )
    equity = sum(
        (item.metrics.equity for item in initialized if item.metrics is not None),
        Decimal("0"),
    )
    total_pnl = equity - initialized_capital
    return PaperDashboardPageTotals(
        sessions_count=len(items),
        initialized_count=len(initialized),
        pending_count=len(items) - len(initialized),
        runner_failed_count=sum(
            item.runner is not None and item.runner.status is PaperRunnerSessionStatus.FAILED
            for item in items
        ),
        risk_halted_count=sum(item.risk_halt is True for item in items),
        open_positions_count=sum(
            item.position is not None and item.position.is_open for item in items
        ),
        open_orders_count=sum(item.open_orders_count for item in items),
        configured_capital=configured_capital,
        initialized_capital=initialized_capital,
        equity=equity,
        total_pnl=total_pnl,
        return_pct=(
            Decimal("0") if initialized_capital == 0 else total_pnl / initialized_capital * _PERCENT
        ),
        maximum_drawdown_pct=max(
            (item.metrics.drawdown_pct for item in initialized if item.metrics is not None),
            default=Decimal("0"),
        ),
    )
