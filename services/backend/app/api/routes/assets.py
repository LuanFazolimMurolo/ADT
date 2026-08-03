"""Public read-only asset catalog and current-price routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.dependencies.resources import (
    get_asset_market_service,
    get_continuous_collection_state_store,
)
from app.api.openapi import MARKET_ERROR_RESPONSES
from app.api.schemas.assets import AssetListResponse, AssetPriceResponse, AssetResponse
from app.api.schemas.collection import ContinuousCollectionStatusResponse
from app.market_data.asset_catalog import AssetCatalogQuery, AssetMarketService
from app.market_data.continuous import ContinuousCollectionStateStore
from app.market_data.domain import TradingPair
from app.market_data.errors import ContinuousCollectionStateNotFoundError

router = APIRouter(
    prefix="/api/v1/market",
    tags=["market"],
    responses=MARKET_ERROR_RESPONSES,
)

AssetCode = Annotated[
    str,
    Path(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"),
]


@router.get("/assets", response_model=AssetListResponse)
async def list_assets(
    service: Annotated[AssetMarketService, Depends(get_asset_market_service)],
    active_only: Annotated[bool, Query()] = True,
    quote_asset: Annotated[
        str | None,
        Query(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"),
    ] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AssetListResponse:
    """List Binance Spot instruments through the normalized ADT catalog."""
    result = await service.list_assets(
        AssetCatalogQuery(
            active_only=active_only,
            quote_asset=quote_asset,
            search=search,
            page=page,
            page_size=page_size,
        )
    )
    return AssetListResponse.from_domain(result)


@router.get(
    "/collection/status",
    response_model=ContinuousCollectionStatusResponse,
)
def get_continuous_collection_status(
    state_store: Annotated[
        ContinuousCollectionStateStore,
        Depends(get_continuous_collection_state_store),
    ],
) -> ContinuousCollectionStatusResponse:
    """Return the latest atomically published continuous collection cycle."""
    state = state_store.read()
    if state is None:
        raise ContinuousCollectionStateNotFoundError()
    return ContinuousCollectionStatusResponse.from_domain(state)


@router.get("/assets/{base_asset}/{quote_asset}", response_model=AssetResponse)
async def get_asset(
    base_asset: AssetCode,
    quote_asset: AssetCode,
    service: Annotated[AssetMarketService, Depends(get_asset_market_service)],
) -> AssetResponse:
    """Return one normalized instrument from the current catalog snapshot."""
    instrument = await service.get_asset(TradingPair(base_asset, quote_asset))
    return AssetResponse.from_domain(instrument)


@router.get("/assets/{base_asset}/{quote_asset}/price", response_model=AssetPriceResponse)
async def get_asset_price(
    base_asset: AssetCode,
    quote_asset: AssetCode,
    service: Annotated[AssetMarketService, Depends(get_asset_market_service)],
) -> AssetPriceResponse:
    """Return one uncached current public price for an active instrument."""
    observation = await service.get_price(TradingPair(base_asset, quote_asset))
    return AssetPriceResponse.from_domain(observation)
