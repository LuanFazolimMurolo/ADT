"""Remote-free HTTP → JWT → authorization → PostgreSQL integration proof."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import AnyHttpUrl, SecretStr

from app.api.dependencies.resources import get_database, get_jwt_verifier
from app.auth import SupabaseJWTVerifier
from app.core.config import Settings
from app.database import Database
from app.main import create_app

ISSUER: Final = "https://phase1d.example.invalid/auth/v1"
KEY_ID: Final = "phase1d-local-rsa-key"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _base64url_unsigned(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


PUBLIC_NUMBERS = PRIVATE_KEY.public_key().public_numbers()
PUBLIC_JWK: Final[dict[str, object]] = {
    "kty": "RSA",
    "kid": KEY_ID,
    "use": "sig",
    "alg": "RS256",
    "n": _base64url_unsigned(PUBLIC_NUMBERS.n),
    "e": _base64url_unsigned(PUBLIC_NUMBERS.e),
}


def _token(subject: UUID) -> str:
    return jwt.encode(
        {
            "sub": str(subject),
            "iss": ISSUER,
            "aud": "authenticated",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _settings() -> Settings:
    return Settings(
        supabase_url=AnyHttpUrl("https://phase1d.example.invalid"),
        supabase_publishable_key=SecretStr("phase1d-public-test-key"),
        # The real disposable Database is injected below.  This deliberately
        # fictitious URL satisfies application configuration without being used.
        supabase_database_url=SecretStr("postgresql://phase1d@db.example.invalid:5432/adt"),
        environment="test",
        log_level="WARNING",
        cors_origins=["http://localhost:5173"],
        api_host="127.0.0.1",
        api_port=8000,
    )


@pytest.mark.asyncio
async def test_signed_admin_request_reaches_real_transactional_database(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    """Exercise the full local administrative boundary without Supabase I/O."""

    def jwks_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{ISSUER}/.well-known/jwks.json"
        return httpx.Response(200, json={"keys": [PUBLIC_JWK]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(jwks_handler)) as jwks_client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=jwks_client)
        application = create_app(_settings())

        async def override_database() -> Database:
            return database

        async def override_verifier() -> SupabaseJWTVerifier:
            return verifier

        application.dependency_overrides[get_database] = override_database
        application.dependency_overrides[get_jwt_verifier] = override_verifier
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://phase1d.test",
        ) as client:
            headers = {"Authorization": f"Bearer {_token(admin_user_id)}"}
            identity = await client.get("/api/v1/admin/me", headers=headers)
            created = await client.post(
                "/api/v1/admin/simulations",
                headers=headers,
                json={
                    "name": "Integração vertical local",
                    "initial_capital": "1234.56780000",
                    "currency": "BRL",
                },
            )

    assert identity.status_code == 200
    assert identity.json() == {"user_id": str(admin_user_id), "is_admin": True}
    assert created.status_code == 201
    assert created.json()["initial_capital"] == "1234.56780000"
    assert created.json()["current_balance"] == "1234.56780000"

    with psycopg.connect(database_url, autocommit=True) as connection:
        persisted = connection.execute(
            """
            select
                simulation.name,
                simulation.status,
                simulation.initial_capital,
                movement.type,
                movement.amount,
                movement.created_by
            from public.simulation_runs as simulation
            join public.capital_movements as movement
              on movement.simulation_id = simulation.id
            """
        ).fetchall()

    assert persisted == [
        (
            "Integração vertical local",
            "ACTIVE",
            Decimal("1234.56780000"),
            "INITIAL_CAPITAL",
            Decimal("1234.56780000"),
            admin_user_id,
        )
    ]


@pytest.mark.asyncio
async def test_signed_non_admin_is_denied_by_real_allow_list(
    database: Database,
    database_url: str,
) -> None:
    """A cryptographically valid token is insufficient without app_admins."""
    non_admin_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("insert into auth.users (id) values (%s)", (non_admin_id,))

    def jwks_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [PUBLIC_JWK]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(jwks_handler)) as jwks_client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=jwks_client)
        application = create_app(_settings())

        async def override_database() -> Database:
            return database

        async def override_verifier() -> SupabaseJWTVerifier:
            return verifier

        application.dependency_overrides[get_database] = override_database
        application.dependency_overrides[get_jwt_verifier] = override_verifier
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://phase1d.test",
        ) as client:
            response = await client.get(
                "/api/v1/admin/me",
                headers={"Authorization": f"Bearer {_token(non_admin_id)}"},
            )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "forbidden",
            "message": "You are not allowed to perform this action.",
        }
    }
