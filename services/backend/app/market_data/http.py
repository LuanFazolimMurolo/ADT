"""Reusable asynchronous HTTP client for public market adapters."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx

from app.market_data.errors import (
    InvalidMarketResponseError,
    MarketDataUnavailableError,
    MarketRateLimitError,
)

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[], float]


@dataclass(frozen=True, slots=True)
class HttpMetrics:
    """Non-sensitive request metrics."""

    request_id: str
    attempts: int
    duration_ms: int
    used_weight: int | None


@dataclass(frozen=True, slots=True)
class JsonHttpResult:
    """Validated JSON payload with source metadata."""

    data: object
    headers: Mapping[str, str]
    metrics: HttpMetrics


class PublicMarketHttpClient:
    """Bounded idempotent client with injectable transport and retry timing."""

    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        timeout_seconds: float,
        max_connections: int,
        retries: int,
        max_retry_after_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        jitter: Jitter = random.random,
    ) -> None:
        self._retries = retries
        if max_retry_after_seconds < 0 or not math.isfinite(max_retry_after_seconds):
            raise ValueError("max_retry_after_seconds must be finite and non-negative")
        self._max_retry_after_seconds = max_retry_after_seconds
        self._sleep = sleep
        self._jitter = jitter
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=timeout_seconds),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=False,
            transport=transport,
        )
        self._closed = False

    async def __aenter__(self) -> PublicMarketHttpClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close pooled connections exactly once."""
        if not self._closed:
            self._closed = True
            await self._client.aclose()

    async def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str | int],
        operation: str,
    ) -> JsonHttpResult:
        """Perform one public GET, retrying only explicitly transient failures."""
        if self._closed:
            raise RuntimeError("public market HTTP client is closed")
        request_id = str(uuid4())
        started_at = monotonic()
        attempts = 0
        response: httpx.Response | None = None

        while attempts <= self._retries:
            attempts += 1
            try:
                response = await self._client.get(
                    path,
                    params=params,
                    headers={"X-Request-ID": request_id},
                )
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempts > self._retries:
                    raise MarketDataUnavailableError() from None
                await self._sleep(self._backoff(attempts))
                continue

            if response.status_code == 418:
                raise MarketRateLimitError(self._retry_after(response))
            if response.status_code == 429:
                retry_after = self._retry_after(response)
                delay = retry_after if retry_after is not None else self._backoff(attempts)
                if attempts > self._retries or delay > self._max_retry_after_seconds:
                    raise MarketRateLimitError(retry_after)
                await self._sleep(delay)
                continue
            if response.status_code >= 500:
                if attempts > self._retries:
                    raise MarketDataUnavailableError()
                await self._sleep(self._backoff(attempts))
                continue
            if response.status_code >= 400:
                return self._result(response, request_id, attempts, started_at, operation)
            return self._result(response, request_id, attempts, started_at, operation)

        raise MarketDataUnavailableError()

    def _result(
        self,
        response: httpx.Response,
        request_id: str,
        attempts: int,
        started_at: float,
        operation: str,
    ) -> JsonHttpResult:
        try:
            data: Any = response.json()
        except ValueError:
            raise InvalidMarketResponseError() from None
        used_weight = self._used_weight(response.headers)
        duration_ms = round((monotonic() - started_at) * 1000)
        logger.info(
            "Public market request completed",
            extra={
                "request_id": request_id,
                "operation": operation,
                "provider": "public_market",
                "http_status": response.status_code,
                "duration_ms": duration_ms,
                "attempts": attempts,
                "used_weight": used_weight,
            },
        )
        return JsonHttpResult(
            data=data,
            headers=response.headers,
            metrics=HttpMetrics(
                request_id=request_id,
                attempts=attempts,
                duration_ms=duration_ms,
                used_weight=used_weight,
            ),
        )

    def _backoff(self, attempts: int) -> float:
        exponential = min(8.0, 0.25 * (2.0 ** (attempts - 1)))
        return exponential + min(0.25, self._jitter() * 0.25)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw_delay = response.headers.get("Retry-After")
        if raw_delay is not None:
            try:
                delay = float(raw_delay)
            except ValueError:
                return None
            if math.isfinite(delay):
                return max(0.0, delay)
        return None

    @staticmethod
    def _used_weight(headers: Mapping[str, str]) -> int | None:
        for name, value in headers.items():
            if name.lower().startswith("x-mbx-used-weight"):
                try:
                    return int(value)
                except ValueError:
                    return None
        return None
