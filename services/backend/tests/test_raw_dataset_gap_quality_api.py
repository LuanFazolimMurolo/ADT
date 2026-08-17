"""Remote-free HTTP boundary tests for Phase 7-04 RAW gap and quality reads."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_raw_gap_read_service,
    get_raw_quality_read_service,
)
from app.api.routes import admin_market_datasets
from app.main import create_app
from app.market_data.datasets import QualityIssueCategory
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.market_data.errors import (
    MarketDataCatalogBusyError,
    MarketDataSnapshotBusyError,
)
from app.market_data.integrity import RAW_DATASET_VERSION_ALGORITHM
from app.market_data.operations import (
    MarketDatasetSelector,
    encode_dataset_id,
)
from app.market_data.raw_gap_query import (
    RawGapPage,
    RawGapPageQuery,
    RawGapRange,
)
from app.market_data.raw_quality_query import (
    RawQualityCoverage,
    RawQualityIssue,
    RawQualityIssueTotals,
    RawQualitySnapshot,
    RawQualityStatus,
)
from app.market_data.timeframes import get_timeframe

ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-7-04-test-token"}

START: Final = datetime(
    2026,
    8,
    1,
    0,
    0,
    tzinfo=UTC,
)
END: Final = datetime(
    2026,
    8,
    1,
    3,
    0,
    tzinfo=UTC,
)

SECRET_PARTITION: Final = (
    "exchange=binance/market=spot/base=BTC/quote=USDT/"
    "timeframe=1h/year=2026/month=08/candles.parquet"
)


def _selector() -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1h"),
    )


def _gap_page() -> RawGapPage:
    selector = _selector()

    return RawGapPage(
        dataset=selector,
        dataset_version="a" * 64,
        version_algorithm=RAW_DATASET_VERSION_ALGORITHM,
        checked_start=START,
        checked_end=END,
        expected_candles=3,
        observed_candles=2,
        missing_candles=1,
        total_gap_count=1,
        page=1,
        page_size=25,
        total_pages=1,
        items=(
            RawGapRange(
                start=datetime(
                    2026,
                    8,
                    1,
                    1,
                    0,
                    tzinfo=UTC,
                ),
                end=datetime(
                    2026,
                    8,
                    1,
                    2,
                    0,
                    tzinfo=UTC,
                ),
                missing_candles=1,
            ),
        ),
    )


def _quality_snapshot() -> RawQualitySnapshot:
    selector = _selector()

    return RawQualitySnapshot(
        dataset=selector,
        status=RawQualityStatus.CURRENT,
        dataset_version="a" * 64,
        version_algorithm=RAW_DATASET_VERSION_ALGORITHM,
        baseline_dataset_version="a" * 64,
        baseline_version_algorithm=(RAW_DATASET_VERSION_ALGORITHM),
        scanner_schema_version=2,
        scanner_version="phase2c-3",
        coverage=RawQualityCoverage(
            expected_count=None,
            observed_count=2,
            internal_gap_count=1,
            missing_at_start=0,
            missing_at_end=0,
        ),
        partition_count=1,
        issue_totals=RawQualityIssueTotals(
            total=2,
            errors=1,
            warnings=1,
            other=0,
        ),
        issues=(
            RawQualityIssue(
                code="gap",
                severity="ERROR",
                category=QualityIssueCategory.COVERAGE,
                open_time="2026-08-01T01:00:00+00:00",
            ),
            RawQualityIssue(
                code="catalog_warning",
                severity="WARNING",
                category=QualityIssueCategory.CATALOG,
                open_time=None,
            ),
        ),
    )


class FakeGapReadService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RawGapPageQuery]] = []

    def inspect(
        self,
        dataset_id: str,
        query: RawGapPageQuery,
    ) -> RawGapPage:
        self.calls.append((dataset_id, query))
        return _gap_page()


class FakeQualityReadService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def inspect(
        self,
        dataset_id: str,
    ) -> RawQualitySnapshot:
        self.calls.append(dataset_id)
        return _quality_snapshot()


@pytest.fixture
def api() -> tuple[
    FastAPI,
    FakeGapReadService,
    FakeQualityReadService,
]:
    application = create_app()
    gap_service = FakeGapReadService()
    quality_service = FakeQualityReadService()

    async def administrator_override() -> UUID:
        return ADMIN_ID

    def gap_service_override() -> FakeGapReadService:
        return gap_service

    def quality_service_override() -> FakeQualityReadService:
        return quality_service

    application.dependency_overrides[require_administrator] = administrator_override
    application.dependency_overrides[get_raw_gap_read_service] = gap_service_override
    application.dependency_overrides[get_raw_quality_read_service] = quality_service_override

    return application, gap_service, quality_service


@pytest.fixture
async def client(
    api: tuple[
        FastAPI,
        FakeGapReadService,
        FakeQualityReadService,
    ],
) -> AsyncIterator[AsyncClient]:
    application, _gap, _quality = api

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_gap_route_returns_bounded_sanitized_page(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeGapReadService,
        FakeQualityReadService,
    ],
) -> None:
    dataset_id = encode_dataset_id(_selector())

    response = await client.get(
        (f"/api/v1/admin/market-data/datasets/{dataset_id}/gaps"),
        headers=AUTH_HEADERS,
        params={
            "start": START.isoformat(),
            "end": END.isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    body = response.json()

    assert body["dataset_id"] == dataset_id
    assert body["exchange"] == "binance"
    assert body["market_type"] == "spot"
    assert body["symbol"] == "BTC/USDT"
    assert body["timeframe"] == "1h"

    assert body["dataset_version"] == "a" * 64
    assert body["version_algorithm"] == RAW_DATASET_VERSION_ALGORITHM

    assert body["checked_start"] == ("2026-08-01T00:00:00Z")
    assert body["checked_end"] == ("2026-08-01T03:00:00Z")

    assert body["expected_candles"] == 3
    assert body["observed_candles"] == 2
    assert body["missing_candles"] == 1
    assert body["total_gap_count"] == 1

    assert body["page"] == 1
    assert body["page_size"] == 25
    assert body["total_pages"] == 1

    assert body["items"] == [
        {
            "start": "2026-08-01T01:00:00Z",
            "end": "2026-08-01T02:00:00Z",
            "missing_candles": 1,
        }
    ]

    response_text = response.text
    assert "location" not in response_text
    assert "relative_path" not in response_text
    assert "quality-baselines" not in response_text
    assert SECRET_PARTITION not in response_text

    assert len(api[1].calls) == 1
    called_dataset_id, query = api[1].calls[0]

    assert called_dataset_id == dataset_id
    assert query.start == START
    assert query.end == END
    assert query.page == 1
    assert query.page_size == 25


@pytest.mark.asyncio
async def test_quality_route_returns_sanitized_persisted_status(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeGapReadService,
        FakeQualityReadService,
    ],
) -> None:
    dataset_id = encode_dataset_id(_selector())

    response = await client.get(
        (f"/api/v1/admin/market-data/datasets/{dataset_id}/quality"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    body = response.json()

    assert body["dataset_id"] == dataset_id
    assert body["status"] == "CURRENT"
    assert body["dataset_version"] == "a" * 64
    assert body["baseline_dataset_version"] == "a" * 64
    assert body["version_algorithm"] == RAW_DATASET_VERSION_ALGORITHM
    assert body["baseline_version_algorithm"] == RAW_DATASET_VERSION_ALGORITHM
    assert body["scanner_schema_version"] == 2
    assert body["scanner_version"] == "phase2c-3"
    assert body["partition_count"] == 1

    assert body["coverage"] == {
        "expected_count": None,
        "observed_count": 2,
        "internal_gap_count": 1,
        "missing_at_start": 0,
        "missing_at_end": 0,
    }
    assert body["issue_totals"] == {
        "total": 2,
        "errors": 1,
        "warnings": 1,
        "other": 0,
    }

    assert body["issues"][0] == {
        "code": "gap",
        "severity": "ERROR",
        "category": "COVERAGE",
        "open_time": "2026-08-01T01:00:00Z",
    }

    response_text = response.text

    assert "partition_id" not in response_text
    assert "relative_path" not in response_text
    assert "baseline_path" not in response_text
    assert "logical_checksum" not in response_text
    assert "quality-baselines" not in response_text
    assert SECRET_PARTITION not in response_text

    assert api[2].calls == [dataset_id]


@pytest.mark.asyncio
async def test_gap_page_size_is_rejected_before_service_call(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeGapReadService,
        FakeQualityReadService,
    ],
) -> None:
    dataset_id = encode_dataset_id(_selector())

    response = await client.get(
        (f"/api/v1/admin/market-data/datasets/{dataset_id}/gaps"),
        headers=AUTH_HEADERS,
        params={
            "start": START.isoformat(),
            "end": END.isoformat(),
            "page_size": 101,
        },
    )

    assert response.status_code == 422
    assert api[1].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code",
    [
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ],
)
async def test_gap_and_quality_require_administrator(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeGapReadService,
        FakeQualityReadService,
    ],
    status_code: int,
) -> None:
    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status_code)

    api[0].dependency_overrides[require_administrator] = reject_administrator

    dataset_id = encode_dataset_id(_selector())

    gap_response = await client.get(
        (f"/api/v1/admin/market-data/datasets/{dataset_id}/gaps"),
        params={
            "start": START.isoformat(),
            "end": END.isoformat(),
        },
    )
    quality_response = await client.get(f"/api/v1/admin/market-data/datasets/{dataset_id}/quality")

    assert gap_response.status_code == status_code
    assert quality_response.status_code == status_code
    assert api[1].calls == []
    assert api[2].calls == []


@pytest.mark.asyncio
async def test_snapshot_busy_is_sanitized_as_503(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeGapReadService,
        FakeQualityReadService,
    ],
) -> None:
    class BusyGapReadService:
        def inspect(
            self,
            dataset_id: str,
            query: RawGapPageQuery,
        ) -> RawGapPage:
            raise MarketDataSnapshotBusyError()

    def busy_service_override() -> BusyGapReadService:
        return BusyGapReadService()

    api[0].dependency_overrides[get_raw_gap_read_service] = busy_service_override

    dataset_id = encode_dataset_id(_selector())

    response = await client.get(
        (f"/api/v1/admin/market-data/datasets/{dataset_id}/gaps"),
        headers=AUTH_HEADERS,
        params={
            "start": START.isoformat(),
            "end": END.isoformat(),
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == ("market_data_snapshot_busy")


@pytest.mark.asyncio
async def test_catalog_busy_is_sanitized_as_503(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeGapReadService,
        FakeQualityReadService,
    ],
) -> None:
    class BusyQualityReadService:
        def inspect(
            self,
            dataset_id: str,
        ) -> RawQualitySnapshot:
            raise MarketDataCatalogBusyError()

    def busy_service_override() -> BusyQualityReadService:
        return BusyQualityReadService()

    api[0].dependency_overrides[get_raw_quality_read_service] = busy_service_override

    dataset_id = encode_dataset_id(_selector())

    response = await client.get(
        (f"/api/v1/admin/market-data/datasets/{dataset_id}/quality"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == ("market_data_catalog_busy")


def test_http_boundary_contains_only_expected_get_routes() -> None:
    routes = {route.path: route for route in admin_market_datasets.router.routes}

    assert set(routes) == {
        "/api/v1/admin/market-data/datasets",
        "/api/v1/admin/market-data/datasets/{dataset_id}",
        ("/api/v1/admin/market-data/datasets/{dataset_id}/gaps"),
        ("/api/v1/admin/market-data/datasets/{dataset_id}/quality"),
    }

    assert all(route.methods == {"GET"} for route in routes.values())


def test_openapi_declares_bounded_sanitized_contract(
    api: tuple[
        FastAPI,
        FakeGapReadService,
        FakeQualityReadService,
    ],
) -> None:
    schema = api[0].openapi()
    paths = schema["paths"]

    gap_path = "/api/v1/admin/market-data/datasets/{dataset_id}/gaps"
    quality_path = "/api/v1/admin/market-data/datasets/{dataset_id}/quality"

    gap_operation = paths[gap_path]["get"]
    quality_operation = paths[quality_path]["get"]

    parameters = {item["name"]: item for item in gap_operation["parameters"]}

    assert parameters["start"]["required"] is True
    assert parameters["end"]["required"] is True
    assert parameters["start"]["schema"]["format"] == ("date-time")
    assert parameters["end"]["schema"]["format"] == ("date-time")
    assert parameters["page"]["schema"]["default"] == 1
    assert parameters["page_size"]["schema"]["default"] == 25
    assert parameters["page_size"]["schema"]["maximum"] == 100

    assert "200" in gap_operation["responses"]
    assert "200" in quality_operation["responses"]

    assert "post" not in paths[gap_path]
    assert "patch" not in paths[gap_path]
    assert "post" not in paths[quality_path]
    assert "patch" not in paths[quality_path]

    components = schema["components"]["schemas"]

    gap_properties = components["RawGapPageResponse"]["properties"]
    quality_properties = components["RawQualityResponse"]["properties"]
    issue_properties = components["RawQualityIssueResponse"]["properties"]

    for forbidden in (
        "location",
        "relative_path",
        "baseline_path",
        "logical_checksum",
        "checksum",
    ):
        assert forbidden not in gap_properties
        assert forbidden not in quality_properties

    assert set(issue_properties) == {
        "code",
        "severity",
        "category",
        "open_time",
    }
    assert "partition" not in issue_properties
