"""Unit tests for Supabase JWT verification and JWKS caching."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from app.auth.jwt import (
    ExpiredTokenError,
    InvalidTokenError,
    JWKSUnavailableError,
    SupabaseJWTVerifier,
)

ISSUER = "https://project.example.test/auth/v1"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
AUDIENCE = "authenticated"
RSA_KEY_ID = "rsa-test-key"
EC_KEY_ID = "ec-test-key"


def _base64url_unsigned(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


RSA_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
RSA_PUBLIC_NUMBERS = RSA_PRIVATE_KEY.public_key().public_numbers()
RSA_JWK: dict[str, object] = {
    "kty": "RSA",
    "kid": RSA_KEY_ID,
    "use": "sig",
    "alg": "RS256",
    "n": _base64url_unsigned(RSA_PUBLIC_NUMBERS.n),
    "e": _base64url_unsigned(RSA_PUBLIC_NUMBERS.e),
}

EC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
EC_PUBLIC_NUMBERS = EC_PRIVATE_KEY.public_key().public_numbers()
EC_JWK: dict[str, object] = {
    "kty": "EC",
    "kid": EC_KEY_ID,
    "use": "sig",
    "alg": "ES256",
    "crv": "P-256",
    "x": _base64url_unsigned(EC_PUBLIC_NUMBERS.x),
    "y": _base64url_unsigned(EC_PUBLIC_NUMBERS.y),
}


def _token(
    *,
    subject: str | None = None,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expiration: datetime | None = None,
    algorithm: str = "RS256",
    key_id: str = RSA_KEY_ID,
    private_key: RSAPrivateKey | EllipticCurvePrivateKey = RSA_PRIVATE_KEY,
) -> str:
    claims = {
        "sub": subject or str(uuid4()),
        "iss": issuer,
        "aud": audience,
        "exp": expiration or datetime.now(UTC) + timedelta(minutes=5),
    }
    return jwt.encode(
        claims,
        private_key,
        algorithm=algorithm,
        headers={"kid": key_id},
    )


def _client_for_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("algorithm", "key_id", "private_key", "jwk"),
    [
        ("RS256", RSA_KEY_ID, RSA_PRIVATE_KEY, RSA_JWK),
        ("ES256", EC_KEY_ID, EC_PRIVATE_KEY, EC_JWK),
    ],
)
async def test_valid_asymmetric_token_returns_uuid_subject(
    algorithm: str,
    key_id: str,
    private_key: RSAPrivateKey | EllipticCurvePrivateKey,
    jwk: dict[str, object],
) -> None:
    user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        return httpx.Response(200, json={"keys": [jwk]})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        result = await verifier.verify(
            _token(
                subject=str(user_id),
                algorithm=algorithm,
                key_id=key_id,
                private_key=private_key,
            )
        )

    assert result == user_id
    assert isinstance(result, UUID)


@pytest.mark.asyncio
async def test_symmetric_algorithm_is_rejected_before_jwks_request() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"keys": [RSA_JWK]})

    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "not-a-supabase-secret-value-32-bytes",
        algorithm="HS256",
        headers={"kid": RSA_KEY_ID},
    )
    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        with pytest.raises(InvalidTokenError):
            await verifier.verify(token)

    assert request_count == 0


@pytest.mark.asyncio
async def test_expired_token_has_specific_safe_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [RSA_JWK]})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        with pytest.raises(ExpiredTokenError) as captured_error:
            await verifier.verify(_token(expiration=datetime.now(UTC) - timedelta(seconds=1)))

    assert captured_error.value.code == "token_expired"
    assert str(captured_error.value) == "Authentication token has expired."


@pytest.mark.asyncio
async def test_token_with_invalid_signature_is_rejected() -> None:
    unrelated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [RSA_JWK]})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        with pytest.raises(InvalidTokenError):
            await verifier.verify(_token(private_key=unrelated_key))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-jwt",
        _token(subject="not-a-uuid"),
        _token(issuer="https://attacker.example.test/auth/v1"),
        _token(audience="another-audience"),
    ],
)
async def test_invalid_token_claims_are_rejected(token: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [RSA_JWK]})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        with pytest.raises(InvalidTokenError) as captured_error:
            await verifier.verify(token)

    assert captured_error.value.code == "invalid_token"
    assert str(captured_error.value) == "Authentication token is invalid."


@pytest.mark.asyncio
async def test_jwks_failure_is_sanitized() -> None:
    sensitive_detail = "database-credential-that-must-not-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(sensitive_detail)

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        with pytest.raises(JWKSUnavailableError) as captured_error:
            await verifier.verify(_token())

    message = str(captured_error.value)
    assert captured_error.value.code == "authentication_keys_unavailable"
    assert message == "Authentication service is temporarily unavailable."
    assert sensitive_detail not in message


@pytest.mark.asyncio
async def test_valid_cached_key_avoids_repeated_requests() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"keys": [RSA_JWK]})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        await verifier.verify(_token())
        await verifier.verify(_token())

    assert request_count == 1


@pytest.mark.asyncio
async def test_unknown_key_causes_only_one_refresh_for_concurrent_requests() -> None:
    request_count = 0
    first_key_id = "first-key"
    first_jwk = {**RSA_JWK, "kid": first_key_id}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"keys": [first_jwk]})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        await verifier.verify(_token(key_id=first_key_id))

        unknown_token = _token(key_id="rotated-but-unknown")
        results = await asyncio.gather(
            *(verifier.verify(unknown_token) for _ in range(20)),
            return_exceptions=True,
        )
        with pytest.raises(InvalidTokenError):
            await verifier.verify(unknown_token)

    assert request_count == 2
    assert all(isinstance(result, InvalidTokenError) for result in results)


@pytest.mark.asyncio
async def test_distinct_unknown_key_ids_cannot_force_unbounded_refreshes() -> None:
    """Random attacker-controlled key IDs share one refresh cooldown."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"keys": [RSA_JWK]})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        await verifier.verify(_token())
        results = await asyncio.gather(
            *(verifier.verify(_token(key_id=f"unknown-key-{index}")) for index in range(20)),
            return_exceptions=True,
        )

    assert request_count == 2
    assert all(isinstance(result, InvalidTokenError) for result in results)


@pytest.mark.asyncio
async def test_unknown_rotated_key_is_accepted_after_single_refresh() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        keys = [RSA_JWK] if request_count == 1 else [RSA_JWK, EC_JWK]
        return httpx.Response(200, json={"keys": keys})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        await verifier.verify(_token())
        user_id = uuid4()
        result = await verifier.verify(
            _token(
                subject=str(user_id),
                algorithm="ES256",
                key_id=EC_KEY_ID,
                private_key=EC_PRIVATE_KEY,
            )
        )

    assert result == user_id
    assert request_count == 2


@pytest.mark.asyncio
async def test_cache_ttl_is_capped_at_ten_minutes() -> None:
    request_count = 0
    now = 1000.0

    def clock() -> float:
        return now

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"keys": [RSA_JWK]})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(
            issuer=ISSUER,
            http_client=client,
            cache_ttl_seconds=3600,
            clock=clock,
        )
        await verifier.verify(_token())
        now += 599
        await verifier.verify(_token())
        now += 2
        await verifier.verify(_token())

    assert request_count == 2


@pytest.mark.asyncio
async def test_malformed_jwks_is_reported_as_service_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": []})

    async with _client_for_handler(handler) as client:
        verifier = SupabaseJWTVerifier(issuer=ISSUER, http_client=client)
        with pytest.raises(JWKSUnavailableError):
            await verifier.verify(_token())
