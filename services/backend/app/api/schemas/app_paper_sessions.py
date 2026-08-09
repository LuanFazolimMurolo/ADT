"""Minimal authenticated paper-session catalog contracts."""

from __future__ import annotations

from app.api.schemas.common import ApiSchema
from app.paper_trading.query import PaperSessionPage, PaperSessionSummaryView


class AppPaperSessionCatalogItemResponse(ApiSchema):
    """Minimum data required to select a future authorized session view."""

    session_id: str
    base_asset: str
    quote_asset: str
    timeframe: str
    strategy_name: str
    strategy_version: str

    @classmethod
    def from_domain(
        cls,
        value: PaperSessionSummaryView,
    ) -> AppPaperSessionCatalogItemResponse:
        PaperSessionSummaryView.__post_init__(value)
        return cls(
            session_id=value.session_id,
            base_asset=value.config.pair.base,
            quote_asset=value.config.pair.quote,
            timeframe=value.config.timeframe.code,
            strategy_name=value.config.strategy.name,
            strategy_version=value.config.strategy.version,
        )


class AppPaperSessionCatalogResponse(ApiSchema):
    """One bounded backend-authorized page of selectable sessions."""

    items: list[AppPaperSessionCatalogItemResponse]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_domain(cls, value: PaperSessionPage) -> AppPaperSessionCatalogResponse:
        PaperSessionPage.__post_init__(value)
        return cls(
            items=[AppPaperSessionCatalogItemResponse.from_domain(item) for item in value.items],
            page=value.page,
            page_size=value.page_size,
            total=value.total,
            total_pages=value.total_pages,
        )

    @classmethod
    def empty(cls, *, page: int, page_size: int) -> AppPaperSessionCatalogResponse:
        return cls(
            items=[],
            page=page,
            page_size=page_size,
            total=0,
            total_pages=0,
        )
