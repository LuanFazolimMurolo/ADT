"""Read-only paper-trading projections for the public HTTP boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.backtesting.domain import Fill, SimulatedOrder
from app.paper_trading.domain import (
    PaperSessionConfig,
    PaperSessionState,
    PaperSessionStateSummary,
    paper_session_id,
    validate_paper_state_against_config,
    validate_paper_state_summary_against_config,
)
from app.paper_trading.errors import InvalidPaperSessionError, PaperSessionVerificationError
from app.paper_trading.repository import PaperTradingRepository

_MAX_PAGE = 100_000
_MAX_PAGE_SIZE = 100
_SESSION_ID = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PaperSessionView:
    config: PaperSessionConfig
    state: PaperSessionState | None

    def __post_init__(self) -> None:
        session_id = paper_session_id(self.config)
        if self.state is not None:
            validate_paper_state_against_config(self.state, self.config)
            if self.state.session_id != session_id:
                raise PaperSessionVerificationError()

    @property
    def session_id(self) -> str:
        return paper_session_id(self.config)


@dataclass(frozen=True, slots=True)
class PaperSessionSummaryView:
    config: PaperSessionConfig
    summary: PaperSessionStateSummary | None

    def __post_init__(self) -> None:
        session_id = paper_session_id(self.config)
        if self.summary is not None:
            validate_paper_state_summary_against_config(self.summary, self.config)
            if self.summary.session_id != session_id:
                raise PaperSessionVerificationError()

    @property
    def session_id(self) -> str:
        return paper_session_id(self.config)


@dataclass(frozen=True, slots=True)
class PaperSessionPage:
    items: tuple[PaperSessionSummaryView, ...]
    page: int
    page_size: int
    total: int
    total_pages: int

    def __post_init__(self) -> None:
        _page(self.page, self.page_size)
        if type(self.total) is not int or self.total < 0:
            raise InvalidPaperSessionError("A contagem de sessões é inválida.")
        expected_pages = (
            0 if self.total == 0 else (self.total + self.page_size - 1) // self.page_size
        )
        if type(self.total_pages) is not int or self.total_pages != expected_pages:
            raise InvalidPaperSessionError("A paginação das sessões é inválida.")
        if not isinstance(self.items, tuple) or len(self.items) > self.page_size:
            raise InvalidPaperSessionError("A página de sessões é inválida.")
        for item in self.items:
            if not isinstance(item, PaperSessionSummaryView):
                raise InvalidPaperSessionError("A página contém sessão inválida.")
            PaperSessionSummaryView.__post_init__(item)
        ids = tuple(item.session_id for item in self.items)
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise InvalidPaperSessionError("As sessões da página são inválidas.")


@dataclass(frozen=True, slots=True)
class PaperOrderPage:
    session_id: str
    items: tuple[SimulatedOrder, ...]
    page: int
    page_size: int
    total: int
    total_pages: int

    def __post_init__(self) -> None:
        _validate_item_page(
            self.session_id,
            self.items,
            self.page,
            self.page_size,
            self.total,
            self.total_pages,
            SimulatedOrder,
        )


@dataclass(frozen=True, slots=True)
class PaperFillPage:
    session_id: str
    items: tuple[Fill, ...]
    page: int
    page_size: int
    total: int
    total_pages: int

    def __post_init__(self) -> None:
        _validate_item_page(
            self.session_id,
            self.items,
            self.page,
            self.page_size,
            self.total,
            self.total_pages,
            Fill,
        )


class PaperTradingReadService:
    def __init__(self, repository: PaperTradingRepository) -> None:
        if not isinstance(repository, PaperTradingRepository):
            raise InvalidPaperSessionError("O repositório de consulta é inválido.")
        self._repository = repository

    def list_sessions(self, *, page: int, page_size: int) -> PaperSessionPage:
        _page(page, page_size)
        start = (page - 1) * page_size
        configs, total = self._repository.list_session_configs_page(
            offset=start,
            limit=page_size,
        )
        items = tuple(
            PaperSessionSummaryView(
                config=config,
                summary=self._repository.load_state_summary(config),
            )
            for config in configs
        )
        return PaperSessionPage(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=0 if total == 0 else (total + page_size - 1) // page_size,
        )

    def get_session(self, session_id: str) -> PaperSessionView:
        config = self._repository.load_config(session_id)
        state = self._repository.load_state(session_id)
        return PaperSessionView(config=config, state=state)

    def list_orders(self, session_id: str, *, page: int, page_size: int) -> PaperOrderPage:
        _page(page, page_size)
        view = self.get_session(session_id)
        orders = () if view.state is None else view.state.orders
        total = len(orders)
        start = (page - 1) * page_size
        return PaperOrderPage(
            session_id=view.session_id,
            items=orders[start : start + page_size],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=0 if total == 0 else (total + page_size - 1) // page_size,
        )

    def list_fills(self, session_id: str, *, page: int, page_size: int) -> PaperFillPage:
        _page(page, page_size)
        view = self.get_session(session_id)
        fills = () if view.state is None else view.state.fills
        total = len(fills)
        start = (page - 1) * page_size
        return PaperFillPage(
            session_id=view.session_id,
            items=fills[start : start + page_size],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=0 if total == 0 else (total + page_size - 1) // page_size,
        )


def _page(page: object, page_size: object) -> tuple[int, int]:
    if type(page) is not int or page < 1 or page > _MAX_PAGE:
        raise InvalidPaperSessionError("A página solicitada é inválida.")
    if type(page_size) is not int or page_size < 1 or page_size > _MAX_PAGE_SIZE:
        raise InvalidPaperSessionError("O tamanho da página é inválido.")
    return page, page_size


def _validate_item_page(
    session_id: object,
    items: object,
    page: object,
    page_size: object,
    total: object,
    total_pages: object,
    item_type: type[object],
) -> None:
    if not isinstance(session_id, str) or _SESSION_ID.fullmatch(session_id) is None:
        raise InvalidPaperSessionError("A identidade da sessão é inválida.")
    _, page_size_value = _page(page, page_size)
    if type(total) is not int or total < 0:
        raise InvalidPaperSessionError("A contagem da página é inválida.")
    expected = 0 if total == 0 else (total + page_size_value - 1) // page_size_value
    if type(total_pages) is not int or total_pages != expected:
        raise InvalidPaperSessionError("A paginação é inválida.")
    if not isinstance(items, tuple) or len(items) > page_size_value:
        raise InvalidPaperSessionError("Os itens da página são inválidos.")
    if any(not isinstance(item, item_type) for item in items):
        raise InvalidPaperSessionError("A página contém item inválido.")
