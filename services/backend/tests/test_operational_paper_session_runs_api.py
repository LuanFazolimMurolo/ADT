"""Gate 3 protected administrator run-control HTTP contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient, Response

import app.operational_paper_session_runs as runs
from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_paper_session_run_service,
)
from app.api.exceptions import setup_exception_handlers
from app.api.routes.admin_operational_paper_session_runs import router
from app.api.schemas.operational_paper_session_runs import (
    OperationalPaperSessionRunEpochResponse,
)
from app.services.operational_paper_session_runs import (
    OperationalPaperSessionRunService,
)

NOW = datetime(2026, 9, 8, 23, 0, tzinfo=UTC)

ADMIN_ID = UUID("11111111-1111-4111-8111-111111111111")
EPOCH_ID = UUID("22222222-2222-4222-8222-222222222222")
ACTIVATION_ID = UUID("33333333-3333-4333-8333-333333333333")

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def _bindings() -> tuple[Any, Any, Any]:
    authorization = SimpleNamespace(
        authorization_id=uuid4(),
        authorization_checksum=SHA_A,
    )
    profile = SimpleNamespace(
        profile_id=uuid4(),
        approved_revision=3,
        specification_checksum=SHA_B,
    )
    mandate = SimpleNamespace(
        mandate_id=uuid4(),
        approved_revision=4,
        specification_checksum=SHA_C,
    )
    return authorization, profile, mandate


def _epoch(
    *,
    with_worker: bool = True,
    with_failure: bool = False,
) -> runs.OperationalPaperSessionRunEpoch:
    authorization, profile, mandate = _bindings()

    worker_claim = (
        SimpleNamespace(
            epoch_id=EPOCH_ID,
            worker_id=UUID("44444444-4444-4444-8444-444444444444"),
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
            code=runs.OperationalPaperSessionRunFailureCode.INTERNAL_ERROR,
            failed_at=NOW + timedelta(minutes=1),
        )
        if with_failure
        else None
    )

    return cast(
        runs.OperationalPaperSessionRunEpoch,
        SimpleNamespace(
            epoch_id=EPOCH_ID,
            schema_version=1,
            run_contract_version=1,
            desired_state=(runs.OperationalPaperSessionRunDesiredState.RUNNING),
            observed_state=(runs.OperationalPaperSessionRunObservedState.RUNNING),
            record_version=8,
            fencing_token=7,
            activation_id=ACTIVATION_ID,
            activation_checksum=SHA_A,
            materialization_id=uuid4(),
            materialization_checksum=SHA_B,
            authorization_binding=authorization,
            profile_binding=profile,
            mandate_binding=mandate,
            simulation_id=uuid4(),
            session_id=SHA_C,
            config_checksum=SHA_D,
            epoch_checksum=SHA_E,
            start_requested_by=ADMIN_ID,
            start_requested_at=NOW,
            start_idempotency_key="secret-start-key",
            start_intent_fingerprint=SHA_A,
            worker_claim=worker_claim,
            failure=failure,
            terminal_at=(NOW + timedelta(minutes=1) if with_failure else None),
        ),
    )


def _command(
    kind: runs.OperationalPaperSessionRunCommandType,
) -> runs.OperationalPaperSessionRunEpochCommand:
    desired = {
        runs.OperationalPaperSessionRunCommandType.PAUSE: (
            runs.OperationalPaperSessionRunDesiredState.PAUSED
        ),
        runs.OperationalPaperSessionRunCommandType.RESUME: (
            runs.OperationalPaperSessionRunDesiredState.RUNNING
        ),
        runs.OperationalPaperSessionRunCommandType.STOP: (
            runs.OperationalPaperSessionRunDesiredState.STOPPED
        ),
    }[kind]

    return cast(
        runs.OperationalPaperSessionRunEpochCommand,
        SimpleNamespace(
            command_id=uuid4(),
            command_contract_version=1,
            epoch_id=EPOCH_ID,
            epoch_checksum=SHA_E,
            command_type=kind,
            desired_state=desired,
            expected_record_version=8,
            resulting_record_version=9,
            actor_id=ADMIN_ID,
            requested_at=NOW,
            idempotency_key="secret-command-key",
            intent_fingerprint=SHA_B,
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
        intent: runs.OperationalPaperSessionRunEpochStartIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> runs.OperationalPaperSessionRunEpoch:
        self._raise_if_needed()
        self.calls.append(
            (
                "start",
                (
                    intent,
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
    ) -> runs.OperationalPaperSessionRunEpoch:
        self._raise_if_needed()
        self.calls.append(("get", epoch_id))
        return _epoch()

    async def list_commands(
        self,
        epoch_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[runs.OperationalPaperSessionRunEpochCommand]:
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
        return [_command(runs.OperationalPaperSessionRunCommandType.PAUSE)]

    async def pause(
        self,
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> runs.OperationalPaperSessionRunEpochCommand:
        self._raise_if_needed()
        self.calls.append(
            (
                "pause",
                (
                    intent,
                    actor_id,
                    idempotency_key,
                    requested_at,
                ),
            )
        )
        return _command(runs.OperationalPaperSessionRunCommandType.PAUSE)

    async def resume(
        self,
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> runs.OperationalPaperSessionRunEpochCommand:
        self._raise_if_needed()
        self.calls.append(
            (
                "resume",
                (
                    intent,
                    actor_id,
                    idempotency_key,
                    requested_at,
                ),
            )
        )
        return _command(runs.OperationalPaperSessionRunCommandType.RESUME)

    async def stop(
        self,
        intent: runs.OperationalPaperSessionRunEpochCommandIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        requested_at: datetime,
    ) -> runs.OperationalPaperSessionRunEpochCommand:
        self._raise_if_needed()
        self.calls.append(
            (
                "stop",
                (
                    intent,
                    actor_id,
                    idempotency_key,
                    requested_at,
                ),
            )
        )
        return _command(runs.OperationalPaperSessionRunCommandType.STOP)


def _app(
    service: _StubService,
    *,
    auth_error: int | None = None,
) -> FastAPI:
    application = FastAPI()
    setup_exception_handlers(application)
    application.include_router(router)

    async def service_dependency() -> OperationalPaperSessionRunService:
        return cast(
            OperationalPaperSessionRunService,
            service,
        )

    application.dependency_overrides[get_operational_paper_session_run_service] = service_dependency

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
    path: str,
    *,
    json: dict[str, object] | None = None,
) -> Response:
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        if json is None:
            return await client.request(method, path)
        return await client.request(
            method,
            path,
            json=json,
        )


def _start_payload() -> dict[str, object]:
    return {
        "activation_id": str(ACTIVATION_ID),
        "activation_checksum": SHA_A,
        "idempotency_key": "api:start:1",
    }


def _command_payload() -> dict[str, object]:
    return {
        "epoch_checksum": SHA_E,
        "expected_record_version": 8,
        "idempotency_key": "api:command:1",
    }


def test_epoch_response_redacts_worker_and_replay_internals() -> None:
    payload = OperationalPaperSessionRunEpochResponse.from_domain(_epoch()).model_dump(mode="json")

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


@pytest.mark.asyncio
async def test_start_builds_exact_intent_and_returns_201() -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "POST",
        "/api/v1/admin/operational-paper-session-runs/start",
        json=_start_payload(),
    )

    assert response.status_code == 201
    assert len(service.calls) == 1

    name, raw_call = service.calls[0]
    assert name == "start"

    call = cast(
        tuple[
            runs.OperationalPaperSessionRunEpochStartIntent,
            UUID,
            str,
            datetime,
        ],
        raw_call,
    )

    intent, actor_id, idempotency_key, requested_at = call

    assert intent.activation_id == ACTIVATION_ID
    assert intent.activation_checksum == SHA_A
    assert actor_id == ADMIN_ID
    assert idempotency_key == "api:start:1"
    assert requested_at.tzinfo is not None

    body = response.json()

    assert body["epoch_id"] == str(EPOCH_ID)
    assert "worker_id" not in str(body)
    assert "idempotency" not in str(body)
    assert "fingerprint" not in str(body)


@pytest.mark.asyncio
async def test_get_is_no_store_and_redacted() -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "GET",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}"),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == [("get", EPOCH_ID)]

    body = response.json()

    assert body["fencing_token"] == 7
    assert body["lease"] is not None
    assert "worker_id" not in str(body)
    assert "idempotency" not in str(body)
    assert "fingerprint" not in str(body)


@pytest.mark.asyncio
async def test_command_history_forwards_bounds_and_is_no_store() -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "GET",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/commands?limit=17&offset=23"),
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
    assert "idempotency" not in str(body)
    assert "fingerprint" not in str(body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "kind"),
    [
        (
            "pause",
            runs.OperationalPaperSessionRunCommandType.PAUSE,
        ),
        (
            "resume",
            runs.OperationalPaperSessionRunCommandType.RESUME,
        ),
        (
            "stop",
            runs.OperationalPaperSessionRunCommandType.STOP,
        ),
    ],
)
async def test_command_routes_build_exact_domain_intent(
    suffix: str,
    kind: runs.OperationalPaperSessionRunCommandType,
) -> None:
    service = _StubService()

    response = await _request(
        _app(service),
        "POST",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/{suffix}"),
        json=_command_payload(),
    )

    assert response.status_code == 200
    assert len(service.calls) == 1

    name, raw_call = service.calls[0]
    assert name == suffix

    call = cast(
        tuple[
            runs.OperationalPaperSessionRunEpochCommandIntent,
            UUID,
            str,
            datetime,
        ],
        raw_call,
    )

    intent, actor_id, idempotency_key, requested_at = call

    assert intent.epoch_id == EPOCH_ID
    assert intent.epoch_checksum == SHA_E
    assert intent.command_type is kind
    assert intent.expected_record_version == 8
    assert actor_id == ADMIN_ID
    assert idempotency_key == "api:command:1"
    assert requested_at.tzinfo is not None

    body = response.json()

    assert body["command_type"] == kind.value
    assert "idempotency" not in str(body)
    assert "fingerprint" not in str(body)


_PROTECTED_ROUTES = [
    (
        "POST",
        "/api/v1/admin/operational-paper-session-runs/start",
        _start_payload(),
    ),
    (
        "GET",
        f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}",
        None,
    ),
    (
        "GET",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/commands"),
        None,
    ),
    (
        "POST",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/pause"),
        _command_payload(),
    ),
    (
        "POST",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/resume"),
        _command_payload(),
    ),
    (
        "POST",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/stop"),
        _command_payload(),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    _PROTECTED_ROUTES,
)
async def test_every_endpoint_is_administrator_protected(
    status_code: int,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    service = _StubService()

    response = await _request(
        _app(
            service,
            auth_error=status_code,
        ),
        method,
        path,
        json=payload,
    )

    assert response.status_code == status_code
    assert service.calls == []


@pytest.mark.asyncio
async def test_run_not_found_is_normalized_to_404() -> None:
    service = _StubService()
    service.error = runs.OperationalPaperSessionRunNotFoundError()

    response = await _request(
        _app(service),
        "GET",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}"),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "operational_paper_session_run_not_found"


@pytest.mark.asyncio
async def test_record_version_conflict_is_normalized_to_409() -> None:
    service = _StubService()
    service.error = runs.OperationalPaperSessionRunRecordVersionConflictError()

    response = await _request(
        _app(service),
        "POST",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/pause"),
        json=_command_payload(),
    )

    assert response.status_code == 409
    assert (
        response.json()["error"]["code"] == "operational_paper_session_run_record_version_conflict"
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
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/commands?{query}"),
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_request_schema_rejects_invalid_control_fields() -> None:
    service = _StubService()

    bad_start = await _request(
        _app(service),
        "POST",
        "/api/v1/admin/operational-paper-session-runs/start",
        json={
            "activation_id": str(ACTIVATION_ID),
            "activation_checksum": "not-a-checksum",
            "idempotency_key": "api:start:bad",
        },
    )

    assert bad_start.status_code == 422
    assert service.calls == []

    bad_command = await _request(
        _app(service),
        "POST",
        (f"/api/v1/admin/operational-paper-session-runs/{EPOCH_ID}/pause"),
        json={
            "epoch_checksum": SHA_E,
            "expected_record_version": True,
            "idempotency_key": "api:command:bad",
        },
    )

    assert bad_command.status_code == 422
    assert service.calls == []


def test_openapi_declares_admin_error_contract_for_all_endpoints() -> None:
    application = _app(_StubService())
    document = application.openapi()

    operations = (
        (
            "/api/v1/admin/operational-paper-session-runs/start",
            "post",
            "201",
        ),
        (
            "/api/v1/admin/operational-paper-session-runs/{epoch_id}",
            "get",
            "200",
        ),
        (
            "/api/v1/admin/operational-paper-session-runs/{epoch_id}/commands",
            "get",
            "200",
        ),
        (
            "/api/v1/admin/operational-paper-session-runs/{epoch_id}/pause",
            "post",
            "200",
        ),
        (
            "/api/v1/admin/operational-paper-session-runs/{epoch_id}/resume",
            "post",
            "200",
        ),
        (
            "/api/v1/admin/operational-paper-session-runs/{epoch_id}/stop",
            "post",
            "200",
        ),
    )

    required_admin_errors = {
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

    for path, method, success in operations:
        responses = document["paths"][path][method]["responses"]

        assert success in responses
        assert required_admin_errors <= set(responses)
