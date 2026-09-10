"""Gate 3 protected administrator operational collector HTTP contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient, Response

import app.operational_market_data_collectors as collectors
from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_market_data_collector_service,
)
from app.api.exceptions import setup_exception_handlers
from app.api.routes.admin_operational_market_data_collectors import (
    router,
)
from app.api.schemas.operational_market_data_collectors import (
    OperationalMarketDataCollectorEpochResponse,
)
from app.database import Database
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
)
from app.services.operational_market_data_collectors import (
    OperationalMarketDataCollectorService,
)

NOW = datetime(
    2026,
    9,
    9,
    20,
    0,
    tzinfo=UTC,
)

ADMIN_ID = UUID("11111111-1111-4111-8111-111111111111")
EPOCH_ID = UUID("22222222-2222-4222-8222-222222222222")

SPEC_SHA = "a" * 64
EPOCH_SHA = "b" * 64


def _specification() -> collectors.OperationalMarketDataCollectorSpecification:
    return collectors.OperationalMarketDataCollectorSpecification(
        schema_version=1,
        collector_contract_version=1,
        scope=(collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW),
        targets=(
            collectors.OperationalMarketDataCollectorTarget(
                symbol="BTC/USDT",
                timeframe="1m",
                bootstrap_candles=500,
            ),
            collectors.OperationalMarketDataCollectorTarget(
                symbol="ETH/USDT",
                timeframe="5m",
                bootstrap_candles=300,
            ),
        ),
        interval_seconds=60,
        overlap_candles=2,
    )


def _epoch(
    *,
    with_worker: bool = True,
    with_failure: bool = False,
) -> collectors.OperationalMarketDataCollectorEpoch:
    specification = _specification()

    worker_claim = (
        SimpleNamespace(
            epoch_id=EPOCH_ID,
            worker_id=UUID("33333333-3333-4333-8333-333333333333"),
            fencing_token=7,
            claimed_at=NOW,
            heartbeat_at=NOW + timedelta(seconds=1),
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        if with_worker
        else None
    )

    failure = (
        SimpleNamespace(
            code=(collectors.OperationalMarketDataCollectorFailureCode.INTERNAL_ERROR),
            failed_at=NOW + timedelta(minutes=1),
        )
        if with_failure
        else None
    )

    return cast(
        collectors.OperationalMarketDataCollectorEpoch,
        SimpleNamespace(
            epoch_id=EPOCH_ID,
            specification=specification,
            specification_checksum=(
                collectors.operational_market_data_collector_specification_checksum(specification)
            ),
            desired_state=(collectors.OperationalMarketDataCollectorDesiredState.RUNNING),
            observed_state=(collectors.OperationalMarketDataCollectorObservedState.RUNNING),
            record_version=8,
            fencing_token=7,
            epoch_checksum=EPOCH_SHA,
            start_requested_by=ADMIN_ID,
            start_requested_at=NOW,
            start_idempotency_key="secret-start-key",
            start_intent_fingerprint=SPEC_SHA,
            worker_claim=worker_claim,
            failure=failure,
            terminal_at=(NOW + timedelta(minutes=1) if with_failure else None),
        ),
    )


def _command(
    kind: collectors.OperationalMarketDataCollectorCommandType,
) -> collectors.OperationalMarketDataCollectorCommand:
    desired = {
        collectors.OperationalMarketDataCollectorCommandType.PAUSE: (
            collectors.OperationalMarketDataCollectorDesiredState.PAUSED
        ),
        collectors.OperationalMarketDataCollectorCommandType.RESUME: (
            collectors.OperationalMarketDataCollectorDesiredState.RUNNING
        ),
        collectors.OperationalMarketDataCollectorCommandType.STOP: (
            collectors.OperationalMarketDataCollectorDesiredState.STOPPED
        ),
    }[kind]

    return cast(
        collectors.OperationalMarketDataCollectorCommand,
        SimpleNamespace(
            command_id=uuid4(),
            command_contract_version=1,
            epoch_id=EPOCH_ID,
            epoch_checksum=EPOCH_SHA,
            command_type=kind,
            desired_state=desired,
            expected_record_version=8,
            resulting_record_version=9,
            actor_id=ADMIN_ID,
            requested_at=NOW,
            idempotency_key="secret-command-key",
            intent_fingerprint=SPEC_SHA,
        ),
    )


class _StubService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.error: Exception | None = None

    def _raise_if_needed(self) -> None:
        if self.error is not None:
            raise self.error

    async def start(
        self,
        specification: collectors.OperationalMarketDataCollectorSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        self._raise_if_needed()

        self.calls.append(
            (
                "start",
                (
                    specification,
                    actor_id,
                    idempotency_key,
                    requested_at,
                ),
            )
        )

        return _epoch()

    async def get(
        self,
        epoch_id: UUID,
    ) -> collectors.OperationalMarketDataCollectorEpoch:
        self._raise_if_needed()
        self.calls.append(
            (
                "get",
                epoch_id,
            )
        )
        return _epoch()

    async def list_commands(
        self,
        epoch_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[collectors.OperationalMarketDataCollectorCommand]:
        self._raise_if_needed()

        self.calls.append(
            (
                "list_commands",
                (
                    epoch_id,
                    limit,
                    offset,
                ),
            )
        )

        return [_command(collectors.OperationalMarketDataCollectorCommandType.PAUSE)]

    async def pause(
        self,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        return await self._command(
            "pause",
            intent,
            actor_id,
            idempotency_key,
            requested_at,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        )

    async def resume(
        self,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        return await self._command(
            "resume",
            intent,
            actor_id,
            idempotency_key,
            requested_at,
            collectors.OperationalMarketDataCollectorCommandType.RESUME,
        )

    async def stop(
        self,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        return await self._command(
            "stop",
            intent,
            actor_id,
            idempotency_key,
            requested_at,
            collectors.OperationalMarketDataCollectorCommandType.STOP,
        )

    async def _command(
        self,
        name: str,
        intent: collectors.OperationalMarketDataCollectorCommandIntent,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
        kind: collectors.OperationalMarketDataCollectorCommandType,
    ) -> collectors.OperationalMarketDataCollectorCommand:
        self._raise_if_needed()

        self.calls.append(
            (
                name,
                (
                    intent,
                    actor_id,
                    idempotency_key,
                    requested_at,
                ),
            )
        )

        return _command(kind)


def _app(
    service: _StubService,
    *,
    auth_error: int | None = None,
) -> FastAPI:
    application = FastAPI()

    setup_exception_handlers(application)

    application.include_router(router)

    async def service_dependency() -> OperationalMarketDataCollectorService:
        return cast(
            OperationalMarketDataCollectorService,
            service,
        )

    application.dependency_overrides[get_operational_market_data_collector_service] = (
        service_dependency
    )

    if auth_error is None:

        async def administrator_dependency() -> UUID:
            return ADMIN_ID

    else:
        denied_status = auth_error

        async def administrator_dependency() -> UUID:
            raise HTTPException(
                status_code=denied_status,
                detail="denied",
            )

    application.dependency_overrides[require_administrator] = administrator_dependency

    return application


async def _request(
    application: FastAPI,
    method: str,
    request_path: str,
    *,
    json: dict[str, object] | None = None,
) -> Response:
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        if json is None:
            return await client.request(
                method,
                request_path,
            )

        return await client.request(
            method,
            request_path,
            json=json,
        )


def _start_payload() -> dict[str, object]:
    return {
        "specification": {
            "schema_version": 1,
            "collector_contract_version": 1,
            "scope": "BINANCE_SPOT_RAW",
            "targets": [
                {
                    "symbol": "BTC/USDT",
                    "timeframe": "1m",
                    "bootstrap_candles": 500,
                },
                {
                    "symbol": "ETH/USDT",
                    "timeframe": "5m",
                    "bootstrap_candles": 300,
                },
            ],
            "interval_seconds": 60,
            "overlap_candles": 2,
        },
        "idempotency_key": "api:start:1",
    }


def _command_payload() -> dict[str, object]:
    return {
        "epoch_checksum": EPOCH_SHA,
        "expected_record_version": 8,
        "idempotency_key": "api:command:1",
    }


def test_epoch_response_redacts_worker_and_replay_internals() -> None:
    payload = OperationalMarketDataCollectorEpochResponse.from_domain(_epoch()).model_dump(
        mode="json"
    )

    rendered = str(payload)

    assert "worker_id" not in rendered
    assert "start_idempotency_key" not in rendered
    assert "start_intent_fingerprint" not in rendered

    assert payload["fencing_token"] == 7
    assert payload["lease"] is not None

    assert set(payload["lease"]) == {
        "claimed_at",
        "heartbeat_at",
        "lease_expires_at",
    }

    specification = cast(
        dict[str, object],
        payload["specification"],
    )

    assert specification["scope"] == "BINANCE_SPOT_RAW"
    assert specification["interval_seconds"] == 60
    assert specification["overlap_candles"] == 2


@pytest.mark.asyncio
async def test_start_builds_exact_specification_and_returns_201() -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "POST",
        "/api/v1/admin/operational-market-data-collectors/start",
        json=_start_payload(),
    )

    assert response.status_code == 201
    assert len(service.calls) == 1

    name, raw_call = service.calls[0]

    assert name == "start"

    call = cast(
        tuple[
            collectors.OperationalMarketDataCollectorSpecification,
            UUID,
            str,
            datetime,
        ],
        raw_call,
    )

    specification, actor_id, idempotency_key, requested_at = call

    assert specification == _specification()
    assert actor_id == ADMIN_ID
    assert idempotency_key == "api:start:1"
    assert requested_at.tzinfo is not None

    body = response.json()

    assert body["epoch_id"] == str(EPOCH_ID)
    assert body["specification"]["targets"] == [
        {
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "bootstrap_candles": 500,
        },
        {
            "symbol": "ETH/USDT",
            "timeframe": "5m",
            "bootstrap_candles": 300,
        },
    ]

    rendered = str(body)

    assert "worker_id" not in rendered
    assert "idempotency" not in rendered
    assert "fingerprint" not in rendered


@pytest.mark.asyncio
async def test_get_is_no_store_and_redacted() -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "GET",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}"),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == [
        (
            "get",
            EPOCH_ID,
        )
    ]

    rendered = str(response.json())

    assert "worker_id" not in rendered
    assert "idempotency" not in rendered
    assert "fingerprint" not in rendered


@pytest.mark.asyncio
async def test_command_history_forwards_bounds_and_is_no_store() -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "GET",
        (
            "/api/v1/admin/operational-market-data-collectors/"
            f"{EPOCH_ID}/commands?limit=17&offset=23"
        ),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    assert service.calls == [
        (
            "list_commands",
            (
                EPOCH_ID,
                17,
                23,
            ),
        )
    ]

    body = response.json()

    assert body["limit"] == 17
    assert body["offset"] == 23
    assert len(body["items"]) == 1

    rendered = str(body)

    assert "idempotency" not in rendered
    assert "fingerprint" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "suffix",
        "kind",
    ),
    [
        (
            "pause",
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        ),
        (
            "resume",
            collectors.OperationalMarketDataCollectorCommandType.RESUME,
        ),
        (
            "stop",
            collectors.OperationalMarketDataCollectorCommandType.STOP,
        ),
    ],
)
async def test_command_routes_build_exact_domain_intent(
    suffix: str,
    kind: collectors.OperationalMarketDataCollectorCommandType,
) -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}/{suffix}"),
        json=_command_payload(),
    )

    assert response.status_code == 200
    assert len(service.calls) == 1

    name, raw_call = service.calls[0]

    assert name == suffix

    call = cast(
        tuple[
            collectors.OperationalMarketDataCollectorCommandIntent,
            UUID,
            str,
            datetime,
        ],
        raw_call,
    )

    intent, actor_id, idempotency_key, requested_at = call

    assert intent.epoch_id == EPOCH_ID
    assert intent.epoch_checksum == EPOCH_SHA
    assert intent.command_type is kind
    assert intent.expected_record_version == 8
    assert actor_id == ADMIN_ID
    assert idempotency_key == "api:command:1"
    assert requested_at.tzinfo is not None

    rendered = str(response.json())

    assert "idempotency" not in rendered
    assert "fingerprint" not in rendered


_PROTECTED_ROUTES = [
    (
        "POST",
        "/api/v1/admin/operational-market-data-collectors/start",
        _start_payload(),
    ),
    (
        "GET",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}"),
        None,
    ),
    (
        "GET",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}/commands"),
        None,
    ),
    (
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}/pause"),
        _command_payload(),
    ),
    (
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}/resume"),
        _command_payload(),
    ),
    (
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}/stop"),
        _command_payload(),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code",
    [
        401,
        403,
    ],
)
@pytest.mark.parametrize(
    (
        "method",
        "request_path",
        "payload",
    ),
    _PROTECTED_ROUTES,
)
async def test_every_endpoint_is_administrator_protected(
    status_code: int,
    method: str,
    request_path: str,
    payload: dict[str, object] | None,
) -> None:
    service = _StubService()

    response = await _request(
        _app(
            service,
            auth_error=status_code,
        ),
        method,
        request_path,
        json=payload,
    )

    assert response.status_code == status_code
    assert service.calls == []


@pytest.mark.asyncio
async def test_collector_not_found_is_normalized_to_404() -> None:
    service = _StubService()
    service.error = collectors.OperationalMarketDataCollectorNotFoundError()

    response = await _request(
        _app(service),
        "GET",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}"),
    )

    assert response.status_code == 404

    assert response.json()["error"]["code"] == "operational_market_data_collector_not_found"


@pytest.mark.asyncio
async def test_record_version_conflict_is_normalized_to_409() -> None:
    service = _StubService()
    service.error = collectors.OperationalMarketDataCollectorRecordVersionConflictError()

    response = await _request(
        _app(service),
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}/pause"),
        json=_command_payload(),
    )

    assert response.status_code == 409

    assert (
        response.json()["error"]["code"]
        == "operational_market_data_collector_record_version_conflict"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "offset=1000001",
    ],
)
async def test_command_history_query_is_transport_bounded(
    query: str,
) -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "GET",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}/commands?{query}"),
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_request_schema_rejects_invalid_control_fields() -> None:
    service = _StubService()

    bad_start = _start_payload()

    specification = cast(
        dict[str, object],
        bad_start["specification"],
    )

    specification["interval_seconds"] = True

    response = await _request(
        _app(service),
        "POST",
        "/api/v1/admin/operational-market-data-collectors/start",
        json=bad_start,
    )

    assert response.status_code == 422
    assert service.calls == []

    bad_command = {
        "epoch_checksum": EPOCH_SHA,
        "expected_record_version": True,
        "idempotency_key": "api:command:bad",
    }

    response = await _request(
        _app(service),
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{EPOCH_ID}/pause"),
        json=bad_command,
    )

    assert response.status_code == 422
    assert service.calls == []


def test_openapi_declares_admin_error_contract_for_all_endpoints() -> None:
    application = _app(_StubService())

    document = application.openapi()

    operations = (
        (
            "/api/v1/admin/operational-market-data-collectors/start",
            "post",
            "201",
        ),
        (
            "/api/v1/admin/operational-market-data-collectors/{epoch_id}",
            "get",
            "200",
        ),
        (
            ("/api/v1/admin/operational-market-data-collectors/{epoch_id}/commands"),
            "get",
            "200",
        ),
        (
            ("/api/v1/admin/operational-market-data-collectors/{epoch_id}/pause"),
            "post",
            "200",
        ),
        (
            ("/api/v1/admin/operational-market-data-collectors/{epoch_id}/resume"),
            "post",
            "200",
        ),
        (
            ("/api/v1/admin/operational-market-data-collectors/{epoch_id}/stop"),
            "post",
            "200",
        ),
    )

    required_errors = {
        "400",
        "401",
        "403",
        "404",
        "409",
        "413",
        "422",
        "500",
        "503",
    }

    for request_path, method, success in operations:
        responses = document["paths"][request_path][method]["responses"]

        assert success in responses
        assert required_errors <= set(responses)


def test_api_layer_contains_no_collector_execution_dependency() -> None:
    from pathlib import Path

    route_source = (
        Path(__file__).resolve().parents[1]
        / "app/api/routes/admin_operational_market_data_collectors.py"
    ).read_text(encoding="utf-8")

    resource_source = (
        Path(__file__).resolve().parents[1] / "app/api/dependencies/resources.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "OperationalMarketDataCollectorWorker",
        "OperationalMarketDataCollectorSupervisor",
        "OperationalMarketDataCollectorRuntimeExecutor",
        "ContinuousCollectionRunner",
    ):
        assert forbidden not in route_source
        assert forbidden not in resource_source


@pytest.mark.asyncio
async def test_start_rejects_server_owned_operational_fields() -> None:
    service = _StubService()

    payload = _start_payload()
    payload["desired_state"] = "RUNNING"
    payload["observed_state"] = "RUNNING"
    payload["fencing_token"] = 999
    payload["worker_id"] = str(uuid4())

    response = await _request(
        _app(service),
        "POST",
        "/api/v1/admin/operational-market-data-collectors/start",
        json=payload,
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_real_postgres_api_preserves_replay_and_preconvergence_reversals(
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository = PostgresOperationalMarketDataCollectorRepository(database)

    service = OperationalMarketDataCollectorService(repository=repository)

    application = FastAPI()
    setup_exception_handlers(application)
    application.include_router(router)

    async def service_dependency() -> OperationalMarketDataCollectorService:
        return service

    async def administrator_dependency() -> UUID:
        return auth_user_id

    application.dependency_overrides[get_operational_market_data_collector_service] = (
        service_dependency
    )

    application.dependency_overrides[require_administrator] = administrator_dependency

    start_payload = _start_payload()
    start_payload["idempotency_key"] = "api:real-postgres:start:1"

    started = await _request(
        application,
        "POST",
        "/api/v1/admin/operational-market-data-collectors/start",
        json=start_payload,
    )

    assert started.status_code == 201

    started_body = started.json()

    epoch_id = UUID(started_body["epoch_id"])
    epoch_checksum = started_body["epoch_checksum"]
    version = started_body["record_version"]

    assert started_body["start_requested_by"] == str(auth_user_id)

    assert started_body["desired_state"] == "RUNNING"
    assert started_body["observed_state"] == "PENDING"
    assert started_body["lease"] is None

    # START replay must resolve to the same persisted epoch,
    # not create a second current epoch.
    replay = await _request(
        application,
        "POST",
        "/api/v1/admin/operational-market-data-collectors/start",
        json=start_payload,
    )

    assert replay.status_code == 201
    assert replay.json()["epoch_id"] == str(epoch_id)
    assert replay.json()["epoch_checksum"] == epoch_checksum

    pause = await _request(
        application,
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{epoch_id}/pause"),
        json={
            "epoch_checksum": epoch_checksum,
            "expected_record_version": version,
            "idempotency_key": ("api:real-postgres:pause:1"),
        },
    )

    assert pause.status_code == 200

    pause_body = pause.json()

    assert pause_body["command_type"] == "PAUSE"
    assert pause_body["desired_state"] == "PAUSED"

    version = pause_body["resulting_record_version"]

    # Desired-state authority deliberately allows reversal
    # before a worker converges observed state to PAUSED.
    resume = await _request(
        application,
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{epoch_id}/resume"),
        json={
            "epoch_checksum": epoch_checksum,
            "expected_record_version": version,
            "idempotency_key": ("api:real-postgres:resume:1"),
        },
    )

    assert resume.status_code == 200

    resume_body = resume.json()

    assert resume_body["command_type"] == "RESUME"
    assert resume_body["desired_state"] == "RUNNING"

    version = resume_body["resulting_record_version"]

    pause_again = await _request(
        application,
        "POST",
        (f"/api/v1/admin/operational-market-data-collectors/{epoch_id}/pause"),
        json={
            "epoch_checksum": epoch_checksum,
            "expected_record_version": version,
            "idempotency_key": ("api:real-postgres:pause:2"),
        },
    )

    assert pause_again.status_code == 200

    pause_again_body = pause_again.json()

    assert pause_again_body["desired_state"] == "PAUSED"

    latest = await _request(
        application,
        "GET",
        (f"/api/v1/admin/operational-market-data-collectors/{epoch_id}"),
    )

    assert latest.status_code == 200
    assert latest.headers["cache-control"] == "no-store"

    latest_body = latest.json()

    assert latest_body["desired_state"] == "PAUSED"

    # No worker ran in this test, therefore observed state
    # must remain PENDING despite PAUSE -> RESUME -> PAUSE.
    assert latest_body["observed_state"] == "PENDING"

    assert latest_body["record_version"] == pause_again_body["resulting_record_version"]

    rendered = str(latest_body)

    assert "worker_id" not in rendered
    assert "idempotency" not in rendered
    assert "fingerprint" not in rendered
