"""Database pool transaction ownership regression tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

import app.database.pool as database_pool
from app.database import Database


class _ConstructorProbePool:
    captured: dict[str, Any] | None = None

    @classmethod
    def __class_getitem__(
        cls,
        _item: object,
    ) -> type["_ConstructorProbePool"]:
        return cls

    def __init__(
        self,
        **kwargs: Any,
    ) -> None:
        type(self).captured = kwargs

    @property
    def closed(self) -> bool:
        return True


def test_database_pool_uses_transactional_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database_pool,
        "AsyncConnectionPool",
        _ConstructorProbePool,
    )

    Database(
        "postgresql://example.invalid/test"
    )

    captured = _ConstructorProbePool.captured

    assert captured is not None
    assert captured["kwargs"]["autocommit"] is False
    assert captured["kwargs"]["row_factory"] is (
        database_pool.dict_row
    )
    assert captured["open"] is False


class _ConnectionProbe:
    def transaction(self) -> None:
        raise AssertionError(
            "Database.transaction() must not create "
            "a nested psycopg transaction context"
        )


class _PoolProbe:
    def __init__(self) -> None:
        self.connection_object = _ConnectionProbe()
        self.entered = 0
        self.exited = 0

    @asynccontextmanager
    async def connection(self):
        self.entered += 1

        try:
            yield self.connection_object
        finally:
            self.exited += 1


@pytest.mark.asyncio
async def test_database_transaction_delegates_boundary_to_pool(
) -> None:
    database = object.__new__(Database)
    pool = _PoolProbe()

    database._pool = pool  # type: ignore[assignment]

    async with database.transaction() as connection:
        assert connection is pool.connection_object

    assert pool.entered == 1
    assert pool.exited == 1
