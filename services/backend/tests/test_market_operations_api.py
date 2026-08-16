"""Remote-free tests for the Phase 7 market-operation HTTP boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_asset_market_service, get_market_operation_service
from app.main import create_app
from app.market_data.asset_catalog import AssetCatalogPage, AssetCatalogQuery
from app.market_data.domain import DataRange, Exchange, Instrument, MarketType, TradingPair
from app.market_data.errors import MarketOperationPlanConflictError
from app.market_data.operations import (
    MarketDatasetSelector,
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    OperationProgress,
    decode_dataset_id,
    encode_dataset_id,
)
from app.market_data.timeframes import get_timeframe
from app.services.market_operations import (
    IncrementalMarketOperationPlanPreview,
    MarketOperationPlanPreview,
)

ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
OPERATION_ID: Final = UUID("20000000-0000-4000-8000-000000000002")
NOW: Final = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
START: Final = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
END: Final = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-7-01c-test-token"}
CHECKSUM: Final = "a" * 64


def _dataset() -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1h"),
    )


def _instrument() -> Instrument:
    return Instrument(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair("BTC", "USDT"),
        native_symbol="BTCUSDT",
        active=True,
    )


DATASET_ID: Final = encode_dataset_id(_dataset())


def _plan(
    *,
    checksum: str = CHECKSUM,
    created_at: datetime = NOW,
) -> OperationPlanSummary:
    return OperationPlanSummary(
        checksum=checksum,
        chunks_planned=2,
        estimated_candles=4,
        estimated_requests=2,
        created_at=created_at,
    )


def _preview(
    operation_type: MarketOperationType = MarketOperationType.RAW_BACKFILL,
) -> MarketOperationPlanPreview:
    return MarketOperationPlanPreview(
        operation_type=operation_type,
        dataset=_dataset(),
        data_range=DataRange(START, END),
        plan=_plan(),
    )


def _snapshot() -> MarketOperationSnapshot:
    request = MarketOperationRequest(
        operation_type=MarketOperationType.RAW_BACKFILL,
        dataset=_dataset(),
        data_range=DataRange(START, END),
        plan_checksum=CHECKSUM,
        idempotency_key="phase7-01c-test",
        requested_by=ADMIN_ID,
    )
    plan = _plan()
    progress = OperationProgress(
        chunks_planned=2,
        chunks_completed=0,
        chunks_failed=0,
        candles_estimated=4,
        candles_received=0,
        candles_persisted=0,
        requests_completed=0,
        updated_at=NOW,
    )
    return MarketOperationSnapshot(
        operation_id=OPERATION_ID,
        request=request,
        plan=plan,
        state=MarketOperationState.PENDING,
        progress=progress,
        created_at=NOW,
        updated_at=NOW,
        record_version=1,
    )


class FakeMarketOperationService:
    """Record HTTP-boundary calls without touching PostgreSQL or local storage."""

    def __init__(self) -> None:
        self.backfill_call: tuple[MarketDatasetSelector, DataRange] | None = None
        self.incremental_call: tuple[MarketDatasetSelector, int, datetime | None] | None = None
        self.submit_call: dict[str, object] | None = None
        self.list_call: dict[str, object] | None = None
        self.get_call: UUID | None = None
        self.control_call: tuple[str, UUID, int] | None = None
        self.submit_error: Exception | None = None
        self.operation = _snapshot()
        self.asset_service = FakeAssetMarketService()

    def observed_at(self) -> datetime:
        return NOW

    def plan_backfill(
        self,
        *,
        dataset: MarketDatasetSelector,
        data_range: DataRange,
    ) -> MarketOperationPlanPreview:
        self.backfill_call = (dataset, data_range)
        return _preview()

    def plan_incremental(
        self,
        *,
        dataset: MarketDatasetSelector,
        overlap_candles: int,
        start: datetime | None = None,
    ) -> IncrementalMarketOperationPlanPreview:
        self.incremental_call = (dataset, overlap_candles, start)
        return IncrementalMarketOperationPlanPreview(
            action="RUN",
            preview=_preview(MarketOperationType.RAW_INCREMENTAL_UPDATE),
            last_open_time=START,
            latest_closed_end=END,
        )

    async def submit(
        self,
        *,
        operation_type: MarketOperationType,
        dataset: MarketDatasetSelector,
        data_range: DataRange,
        plan_checksum: str,
        idempotency_key: str,
        requested_by: UUID,
    ) -> MarketOperationSnapshot:
        self.submit_call = {
            "operation_type": operation_type,
            "dataset": dataset,
            "data_range": data_range,
            "plan_checksum": plan_checksum,
            "idempotency_key": idempotency_key,
            "requested_by": requested_by,
        }
        if self.submit_error is not None:
            raise self.submit_error
        return self.operation

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: MarketOperationState | None = None,
        requested_by: UUID | None = None,
        dataset: MarketDatasetSelector | None = None,
    ) -> tuple[MarketOperationSnapshot, ...]:
        self.list_call = {
            "limit": limit,
            "offset": offset,
            "state": state,
            "requested_by": requested_by,
            "dataset": dataset,
        }
        return (self.operation,)

    async def get(self, operation_id: UUID) -> MarketOperationSnapshot:
        self.get_call = operation_id
        return self.operation

    async def pause(
        self,
        operation_id: UUID,
        *,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        self.control_call = ("pause", operation_id, expected_version)
        return self.operation

    async def resume(
        self,
        operation_id: UUID,
        *,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        self.control_call = ("resume", operation_id, expected_version)
        return self.operation

    async def cancel(
        self,
        operation_id: UUID,
        *,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        self.control_call = ("cancel", operation_id, expected_version)
        return self.operation


class FakeAssetMarketService:
    def __init__(self) -> None:
        self.list_call: AssetCatalogQuery | None = None

    async def list_assets(self, query: AssetCatalogQuery) -> AssetCatalogPage:
        self.list_call = query
        return AssetCatalogPage(
            items=(_instrument(),),
            page=query.page,
            page_size=query.page_size,
            total=1,
            fetched_at=NOW,
            expires_at=NOW.replace(hour=13),
            source="binance_spot_exchange_info",
        )


@pytest.fixture
def api() -> tuple[FastAPI, FakeMarketOperationService]:
    application = create_app()
    service = FakeMarketOperationService()

    async def administrator_override() -> UUID:
        return ADMIN_ID

    async def service_override() -> FakeMarketOperationService:
        return service

    async def asset_service_override() -> FakeAssetMarketService:
        return service.asset_service

    application.dependency_overrides[require_administrator] = administrator_override
    application.dependency_overrides[get_market_operation_service] = service_override
    application.dependency_overrides[get_asset_market_service] = asset_service_override
    return application, service


@pytest.fixture
async def client(
    api: tuple[FastAPI, FakeMarketOperationService],
) -> AsyncIterator[AsyncClient]:
    application, _service = api
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_backfill_preview_decodes_dataset_and_preserves_exact_utc_range(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.post(
        "/api/v1/admin/market-data/operations/preview/backfill",
        headers=AUTH_HEADERS,
        json={
            "dataset_id": DATASET_ID,
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json()["dataset"]["dataset_id"] == DATASET_ID
    assert response.json()["plan"]["checksum"] == CHECKSUM
    service = api[1]
    assert service.backfill_call == (_dataset(), DataRange(START, END))


@pytest.mark.asyncio
async def test_targets_are_admin_bounded_and_backend_own_all_dataset_ids(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.get(
        "/api/v1/admin/market-data/operations/targets",
        headers=AUTH_HEADERS,
        params={"quote_asset": "USDT", "search": "BTC", "page_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["catalog_fetched_at"] == "2026-08-10T12:00:00Z"
    target = body["items"][0]
    assert target["symbol"] == "BTC/USDT"
    assert [item["timeframe"] for item in target["timeframes"]] == [
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "4h",
        "12h",
        "1d",
        "1w",
    ]
    for item in target["timeframes"]:
        dataset = decode_dataset_id(item["dataset_id"])
        assert dataset.pair == TradingPair("BTC", "USDT")
        assert dataset.timeframe.code == item["timeframe"]
    assert "path" not in response.text.lower()
    assert "owner" not in response.text.lower()
    assert api[1].backfill_call is None
    assert api[1].incremental_call is None
    assert api[1].asset_service.list_call == AssetCatalogQuery(
        quote_asset="USDT",
        search="BTC",
        page_size=10,
    )


@pytest.mark.asyncio
async def test_targets_reject_oversized_page_before_catalog_access(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.get(
        "/api/v1/admin/market-data/operations/targets?page_size=51",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 422
    assert api[1].asset_service.list_call is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
async def test_targets_require_administrator(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
    status_code: int,
) -> None:
    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status_code)

    api[0].dependency_overrides[require_administrator] = reject_administrator
    response = await client.get("/api/v1/admin/market-data/operations/targets")

    assert response.status_code == status_code
    assert api[1].asset_service.list_call is None


@pytest.mark.asyncio
async def test_preview_rejects_non_utc_timestamp_before_service_call(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.post(
        "/api/v1/admin/market-data/operations/preview/backfill",
        headers=AUTH_HEADERS,
        json={
            "dataset_id": DATASET_ID,
            "range_start": "2026-08-10T08:00:00+03:00",
            "range_end": END.isoformat(),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api[1].backfill_call is None


@pytest.mark.asyncio
async def test_incremental_preview_passes_explicit_overlap_and_optional_start(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.post(
        "/api/v1/admin/market-data/operations/preview/incremental",
        headers=AUTH_HEADERS,
        json={
            "dataset_id": DATASET_ID,
            "overlap_candles": 2,
            "start": START.isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json()["action"] == "RUN"
    assert response.json()["preview"]["operation_type"] == "RAW_INCREMENTAL_UPDATE"
    assert api[1].incremental_call == (_dataset(), 2, START)


@pytest.mark.asyncio
async def test_submit_requires_literal_confirmation(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.post(
        "/api/v1/admin/market-data/operations",
        headers=AUTH_HEADERS,
        json={
            "operation_type": "RAW_BACKFILL",
            "dataset_id": DATASET_ID,
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
            "plan_checksum": CHECKSUM,
            "idempotency_key": "phase7-01c-http",
            "confirmed": False,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api[1].submit_call is None


@pytest.mark.asyncio
async def test_submit_returns_202_and_does_not_echo_idempotency_key(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.post(
        "/api/v1/admin/market-data/operations",
        headers=AUTH_HEADERS,
        json={
            "operation_type": "RAW_BACKFILL",
            "dataset_id": DATASET_ID,
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
            "plan_checksum": CHECKSUM,
            "idempotency_key": "phase7-01c-http",
            "confirmed": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == str(OPERATION_ID)
    assert body["record_version"] == 1
    assert body["observed_at"] == "2026-08-10T12:00:00Z"
    assert "idempotency_key" not in body
    assert api[1].submit_call == {
        "operation_type": MarketOperationType.RAW_BACKFILL,
        "dataset": _dataset(),
        "data_range": DataRange(START, END),
        "plan_checksum": CHECKSUM,
        "idempotency_key": "phase7-01c-http",
        "requested_by": ADMIN_ID,
    }


@pytest.mark.asyncio
async def test_plan_checksum_conflict_is_stable_http_409(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    api[1].submit_error = MarketOperationPlanConflictError()

    response = await client.post(
        "/api/v1/admin/market-data/operations",
        headers=AUTH_HEADERS,
        json={
            "operation_type": "RAW_BACKFILL",
            "dataset_id": DATASET_ID,
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
            "plan_checksum": CHECKSUM,
            "idempotency_key": "phase7-01c-conflict",
            "confirmed": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_operation_plan_conflict"


@pytest.mark.asyncio
async def test_list_is_bounded_and_uses_one_item_lookahead(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.get(
        (
            "/api/v1/admin/market-data/operations"
            f"?limit=5&offset=10&state=PENDING&requested_by={ADMIN_ID}"
            f"&dataset_id={DATASET_ID}"
        ),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["limit"] == 5
    assert response.json()["offset"] == 10
    assert response.json()["count"] == 1
    assert response.json()["has_more"] is False
    assert api[1].list_call == {
        "limit": 6,
        "offset": 10,
        "state": MarketOperationState.PENDING,
        "requested_by": ADMIN_ID,
        "dataset": _dataset(),
    }


@pytest.mark.asyncio
async def test_get_returns_sanitized_operation(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.get(
        f"/api/v1/admin/market-data/operations/{OPERATION_ID}",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["dataset"]["symbol"] == "BTC/USDT"
    assert response.json()["lease"] is None
    assert response.json()["observed_at"] == "2026-08-10T12:00:00Z"
    assert api[1].get_call == OPERATION_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
async def test_controls_forward_expected_version(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
    action: str,
) -> None:
    response = await client.post(
        f"/api/v1/admin/market-data/operations/{OPERATION_ID}/{action}",
        headers=AUTH_HEADERS,
        json={"expected_version": 1},
    )

    assert response.status_code == 200
    assert api[1].control_call == (action, OPERATION_ID, 1)


@pytest.mark.asyncio
async def test_noncanonical_dataset_identifier_is_rejected_without_service_call(
    client: AsyncClient,
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    response = await client.post(
        "/api/v1/admin/market-data/operations/preview/backfill",
        headers=AUTH_HEADERS,
        json={
            "dataset_id": "notcanonical",
            "range_start": START.isoformat(),
            "range_end": END.isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_dataset_id"
    assert api[1].backfill_call is None


def test_openapi_declares_control_plane_and_accepted_submission(
    api: tuple[FastAPI, FakeMarketOperationService],
) -> None:
    schema = api[0].openapi()
    paths = schema["paths"]

    assert "/api/v1/admin/market-data/operations/preview/backfill" in paths
    assert "/api/v1/admin/market-data/operations/preview/incremental" in paths
    assert "/api/v1/admin/market-data/operations/targets" in paths
    assert "/api/v1/admin/market-data/operations" in paths
    assert (
        paths["/api/v1/admin/market-data/operations"]["post"]["responses"]["202"]["description"]
        == "Successful Response"
    )
    operation_schema = schema["components"]["schemas"]["MarketOperationResponse"]
    assert "observed_at" in operation_schema["required"]
