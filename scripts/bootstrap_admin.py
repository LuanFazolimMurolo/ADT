"""Register the initial ADT administrator in an idempotent way."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, Self, cast
from uuid import UUID

ADMIN_USER_ID_VARIABLE = "ADT_ADMIN_USER_ID"
DATABASE_URL_VARIABLE = "SUPABASE_DATABASE_URL"

_INSERT_ADMIN_SQL = """
    INSERT INTO public.app_admins (user_id)
    VALUES (%s)
    ON CONFLICT (user_id) DO NOTHING
"""


class BootstrapConfigurationError(ValueError):
    """Indicate that bootstrap configuration is missing or invalid."""


class ExecutionResult(Protocol):
    """Describe the database result used by the bootstrap."""

    @property
    def rowcount(self) -> int:
        """Return the number of rows changed by the statement."""


class DatabaseConnection(Protocol):
    """Describe the small connection surface needed by the bootstrap."""

    def transaction(self) -> AbstractContextManager[None]:
        """Create an explicit database transaction."""

    def execute(self, query: str, params: tuple[UUID]) -> ExecutionResult:
        """Execute a parameterized statement."""


DatabaseConnector = Callable[[str], AbstractContextManager[DatabaseConnection]]


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    """Validated settings required to register the initial administrator."""

    admin_user_id: UUID
    database_url: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Self:
        """Build configuration from environment variables without reading a dotenv file."""
        values = os.environ if environment is None else environment
        raw_admin_user_id = _required_value(values, ADMIN_USER_ID_VARIABLE)
        database_url = _required_value(values, DATABASE_URL_VARIABLE)

        try:
            admin_user_id = UUID(raw_admin_user_id)
        except ValueError as error:
            message = f"{ADMIN_USER_ID_VARIABLE} deve conter um UUID válido."
            raise BootstrapConfigurationError(message) from error

        return cls(admin_user_id=admin_user_id, database_url=database_url)


def _required_value(environment: Mapping[str, str], variable_name: str) -> str:
    value = environment.get(variable_name)
    if value is None or not value.strip():
        message = f"A variável de ambiente {variable_name} é obrigatória."
        raise BootstrapConfigurationError(message)
    return value.strip()


def _default_connector(database_url: str) -> AbstractContextManager[DatabaseConnection]:
    """Load psycopg only when a real connection is requested."""
    psycopg = import_module("psycopg")
    connect = cast(DatabaseConnector, psycopg.connect)
    return connect(database_url)


def bootstrap_admin(
    config: BootstrapConfig,
    connector: DatabaseConnector = _default_connector,
) -> bool:
    """Ensure the configured administrator exists.

    Returns ``True`` when a row is created and ``False`` when it already exists.
    """
    with connector(config.database_url) as connection, connection.transaction():
        result = connection.execute(_INSERT_ADMIN_SQL, (config.admin_user_id,))
    return result.rowcount == 1


def main(
    environment: Mapping[str, str] | None = None,
    connector: DatabaseConnector = _default_connector,
) -> int:
    """Run the bootstrap and return a process exit code."""
    try:
        config = BootstrapConfig.from_environment(environment)
        bootstrap_admin(config, connector)
    except BootstrapConfigurationError as error:
        print(f"Erro de configuração: {error}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 -- sanitize every failure at the CLI boundary
        # Database exceptions can include connection details, so they are never echoed.
        print(
            "Não foi possível confirmar o administrador inicial no banco de dados.",
            file=sys.stderr,
        )
        return 1

    print("Administrador inicial confirmado com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
