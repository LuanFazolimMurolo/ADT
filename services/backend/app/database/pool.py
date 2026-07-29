"""Typed asynchronous PostgreSQL connection pool."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypeAlias

from psycopg import AsyncConnection, InterfaceError, OperationalError
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout

from app.domain.errors import PersistenceUnavailableError

DatabaseConnection: TypeAlias = AsyncConnection[DictRow]


class Database:
    """Own the backend PostgreSQL pool and its lifecycle."""

    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 0,
        max_size: int = 10,
        timeout: float = 10.0,
    ) -> None:
        if min_size < 0:
            raise ValueError("min_size must be greater than or equal to zero")
        if max_size < 1 or max_size < min_size:
            raise ValueError("max_size must be positive and not smaller than min_size")

        connection_kwargs: dict[str, Any] = {
            "autocommit": True,
            "row_factory": dict_row,
        }
        self._pool = AsyncConnectionPool[DatabaseConnection](
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            kwargs=connection_kwargs,
            open=False,
        )

    @property
    def is_open(self) -> bool:
        """Return whether the pool currently accepts connections."""
        return not self._pool.closed

    async def open(self) -> None:
        """Open the lazy pool without logging its connection string."""
        try:
            await self._pool.open(wait=False)
        except (OperationalError, InterfaceError, PoolTimeout, PoolClosed):
            raise PersistenceUnavailableError() from None

    async def start(self) -> None:
        """Lifecycle-friendly alias for opening the pool."""
        await self.open()

    async def close(self) -> None:
        """Close every pooled connection cleanly."""
        await self._pool.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[DatabaseConnection]:
        """Yield one connection inside an explicit database transaction."""
        try:
            async with self._pool.connection() as connection:
                async with connection.transaction():
                    yield connection
        except (OperationalError, InterfaceError, PoolTimeout, PoolClosed) as error:
            raise PersistenceUnavailableError() from error

    async def health_check(self) -> bool:
        """Check database availability without exposing connection details."""
        try:
            async with self.transaction() as connection:
                cursor = await connection.execute("select 1 as healthy")
                row = await cursor.fetchone()
                return row is not None and row["healthy"] == 1
        except Exception:
            return False
