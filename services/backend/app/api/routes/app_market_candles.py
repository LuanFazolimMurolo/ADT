"""Authenticated bounded local RAW candle endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.dependencies.auth import get_authenticated_user
from app.api.dependencies.resources import get_market_candle_read_service
from app.api.openapi import AUTHENTICATED_MARKET_ERROR_RESPONSES
from app.api.schemas.market_candles import MarketCandlePageResponse
from app.market_data.candle_query import (
    MARKET_CANDLE_DEFAULT_LIMIT,
    MARKET_CANDLE_MAX_LIMIT,
    LocalMarketCandleReadService,
    MarketCandlePageQuery,
)
from app.market_data.domain import TradingPair
from app.market_data.timeframes import get_timeframe

_CANDLE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Bounded verified local RAW candle page for an authenticated user.",
        "headers": {
            "Cache-Control": {"schema": {"type": "string"}},
            "X-ADT-Candle-Dataset-Version": {"schema": {"type": "string"}},
            "X-ADT-Candle-Content-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Candle-Rows": {"schema": {"type": "integer"}},
        },
    }
}

router = APIRouter(
    prefix="/api/v1/app/market-data",
    tags=["app market data"],
    responses=AUTHENTICATED_MARKET_ERROR_RESPONSES,
)

AssetCode = Annotated[
    str,
    Path(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"),
]


@router.get(
    "/candles/{base_asset}/{quote_asset}",
    response_model=MarketCandlePageResponse,
    responses=_CANDLE_RESPONSES,
)
def get_app_market_candles(
    response: Response,
    _user_id: Annotated[UUID, Depends(get_authenticated_user)],
    service: Annotated[
        LocalMarketCandleReadService,
        Depends(get_market_candle_read_service),
    ],
    base_asset: AssetCode,
    quote_asset: AssetCode,
    timeframe: Annotated[
        str,
        Query(min_length=2, max_length=16, pattern=r"^[1-9][0-9]*[smhdwM]$"),
    ],
    before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MARKET_CANDLE_MAX_LIMIT),
    ] = MARKET_CANDLE_DEFAULT_LIMIT,
) -> MarketCandlePageResponse:
    """Return persisted closed candles to any authenticated user."""

    page = service.read_page(
        MarketCandlePageQuery(
            pair=TradingPair(base_asset, quote_asset),
            timeframe=get_timeframe(timeframe),
            before=before,
            limit=limit,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-ADT-Candle-Dataset-Version"] = page.dataset_version
    response.headers["X-ADT-Candle-Content-Checksum"] = page.content_checksum
    response.headers["X-ADT-Candle-Rows"] = str(len(page.candles))
    return MarketCandlePageResponse.from_domain(page)
