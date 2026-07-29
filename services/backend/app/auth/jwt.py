"""Supabase JWT signature and claim verification using public JWKS keys."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Final
from uuid import UUID

import httpx
import jwt

_ALLOWED_ALGORITHMS: Final[frozenset[str]] = frozenset({"RS256", "ES256"})
_EXPECTED_AUDIENCE: Final[str] = "authenticated"
_MAX_CACHE_TTL_SECONDS: Final[float] = 600.0


class AuthenticationError(Exception):
    """Base class for safe, client-facing authentication failures."""

    code = "authentication_failed"
    message = "Authentication could not be completed."

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidTokenError(AuthenticationError):
    """The bearer token is malformed, unsupported, or fails validation."""

    code = "invalid_token"
    message = "Authentication token is invalid."


class ExpiredTokenError(InvalidTokenError):
    """The bearer token has passed its expiration time."""

    code = "token_expired"
    message = "Authentication token has expired."


class JWKSUnavailableError(AuthenticationError):
    """Public signing keys could not be obtained or interpreted."""

    code = "authentication_keys_unavailable"
    message = "Authentication service is temporarily unavailable."


class SupabaseJWTVerifier:
    """Verify Supabase access tokens with an injected asynchronous HTTP client.

    The service intentionally knows nothing about FastAPI's ``Authorization``
    header.  HTTP extraction and response mapping belong to an API dependency.
    """

    def __init__(
        self,
        *,
        issuer: str,
        http_client: httpx.AsyncClient,
        cache_ttl_seconds: float = 300.0,
        request_timeout_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        normalized_issuer = issuer.rstrip("/")
        if not normalized_issuer:
            raise ValueError("issuer must not be empty")
        if request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        if cache_ttl_seconds <= 0:
            raise ValueError("cache TTL must be positive")

        self._issuer = normalized_issuer
        self._jwks_url = f"{normalized_issuer}/.well-known/jwks.json"
        self._http_client = http_client
        self._cache_ttl_seconds = min(
            float(cache_ttl_seconds),
            _MAX_CACHE_TTL_SECONDS,
        )
        self._request_timeout_seconds = request_timeout_seconds
        self._clock = clock

        self._keys_by_id: dict[str, Mapping[str, object]] = {}
        self._cache_expires_at = 0.0
        self._unknown_key_refresh_cooldown = min(30.0, self._cache_ttl_seconds)
        self._last_unknown_key_refresh_at = float("-inf")
        self._cache_lock = asyncio.Lock()

    async def verify(self, token: str) -> UUID:
        """Validate a JWT and return only its authenticated UUID subject."""
        if not token or not token.strip():
            raise InvalidTokenError

        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise InvalidTokenError from None

        algorithm = header.get("alg")
        key_id = header.get("kid")
        if not isinstance(algorithm, str) or algorithm not in _ALLOWED_ALGORITHMS:
            raise InvalidTokenError
        if not isinstance(key_id, str) or not key_id or key_id != key_id.strip():
            raise InvalidTokenError

        jwk = await self._get_key(key_id)
        declared_algorithm = jwk.get("alg")
        if declared_algorithm is not None and (
            not isinstance(declared_algorithm, str) or declared_algorithm != algorithm
        ):
            raise InvalidTokenError

        try:
            public_key = jwt.PyJWK.from_dict(dict(jwk), algorithm=algorithm).key
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=[algorithm],
                audience=_EXPECTED_AUDIENCE,
                issuer=self._issuer,
                options={"require": ["aud", "exp", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise ExpiredTokenError from None
        except jwt.PyJWTError:
            raise InvalidTokenError from None
        except (KeyError, TypeError, ValueError):
            raise InvalidTokenError from None

        subject = claims.get("sub")
        if not isinstance(subject, str):
            raise InvalidTokenError
        try:
            return UUID(subject)
        except ValueError:
            raise InvalidTokenError from None

    async def _get_key(self, key_id: str) -> Mapping[str, object]:
        async with self._cache_lock:
            now = self._clock()
            fetched_during_lookup = False

            if not self._keys_by_id or now >= self._cache_expires_at:
                await self._refresh_keys()
                fetched_during_lookup = True

            cached_key = self._keys_by_id.get(key_id)
            if cached_key is not None:
                return cached_key

            # A key absent from a still-valid cache may be a newly rotated key.
            # At most one forced refresh is allowed per cooldown window, even
            # when an attacker submits many different random key identifiers.
            can_force_refresh = (
                now - self._last_unknown_key_refresh_at >= self._unknown_key_refresh_cooldown
            )
            if not fetched_during_lookup and can_force_refresh:
                self._last_unknown_key_refresh_at = now
                await self._refresh_keys()
                cached_key = self._keys_by_id.get(key_id)
                if cached_key is not None:
                    return cached_key

            raise InvalidTokenError

    async def _refresh_keys(self) -> None:
        try:
            response = await self._http_client.get(
                self._jwks_url,
                timeout=self._request_timeout_seconds,
            )
            response.raise_for_status()
            document = response.json()
            keys_by_id = self._parse_jwks(document)
        except AuthenticationError:
            raise
        except Exception:
            raise JWKSUnavailableError from None

        self._keys_by_id = keys_by_id
        self._cache_expires_at = self._clock() + self._cache_ttl_seconds

    @staticmethod
    def _parse_jwks(document: object) -> dict[str, Mapping[str, object]]:
        if not isinstance(document, dict):
            raise JWKSUnavailableError

        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise JWKSUnavailableError

        keys_by_id: dict[str, Mapping[str, object]] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, dict):
                raise JWKSUnavailableError

            key_id = raw_key.get("kid")
            algorithm = raw_key.get("alg")
            intended_use = raw_key.get("use")
            if not isinstance(key_id, str) or not key_id or key_id != key_id.strip():
                raise JWKSUnavailableError
            if algorithm is not None and (
                not isinstance(algorithm, str) or algorithm not in _ALLOWED_ALGORITHMS
            ):
                continue
            if intended_use is not None and (
                not isinstance(intended_use, str) or intended_use != "sig"
            ):
                continue
            if key_id in keys_by_id:
                raise JWKSUnavailableError
            keys_by_id[key_id] = raw_key

        if not keys_by_id:
            raise JWKSUnavailableError
        return keys_by_id
