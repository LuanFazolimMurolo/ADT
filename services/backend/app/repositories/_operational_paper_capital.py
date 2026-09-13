"""Database-only row and SQL primitives shared by official capital repositories."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from psycopg import sql

from app.database.pool import DatabaseConnection
from app.domain.errors import PersistenceError


def uuid_value(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError
    return value


def text_value(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value


def integer_value(value: object) -> int:
    if type(value) is not int:
        raise ValueError
    return value


def decimal_value(value: object) -> Decimal:
    if type(value) is not Decimal:
        raise ValueError
    return value


def timestamp_value(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError
    return value.astimezone(UTC)


def utc_now(value: object) -> datetime:
    canonical = timestamp_value(value)
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError
    return canonical


async def lock_simulation(
    connection: DatabaseConnection,
    simulation_id: UUID,
) -> Mapping[str, object] | None:
    """Reuse Phase 1's cross-process financial mutex and canonical lock order."""
    cursor = await connection.execute(
        "select id, status, currency, initial_capital, started_at "
        "from public.simulation_runs where id = %s for update",
        (simulation_id,),
    )
    return await cursor.fetchone()


async def insert_evidence(
    connection: DatabaseConnection,
    table: str,
    values: Mapping[str, object],
) -> Mapping[str, object]:
    if table not in {"operational_paper_capital_eras", "operational_paper_session_settlements"}:
        raise PersistenceError()
    cursor = await connection.execute(
        sql.SQL("insert into public.{} ({}) values ({}) returning *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ),
        tuple(values.values()),
    )
    row = await cursor.fetchone()
    if row is None:
        raise PersistenceError()
    return row
