"""Reusable helpers for isolated local PostgreSQL integration tests."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from psycopg import Connection
from psycopg.conninfo import make_conninfo

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    PROJECT_ROOT / "supabase" / "migrations" / "20260729000000_phase_1a_initial_schema.sql"
)
POSTGRES_OWNER = "adt_test_owner"
POSTGRES_PORT = 5432


@dataclass(frozen=True)
class PostgresCluster:
    """Connection details for one disposable PostgreSQL test cluster."""

    socket_directory: Path

    def connection_info(self, database: str) -> str:
        """Build a local Unix-socket connection string without credentials."""
        return make_conninfo(
            dbname=database,
            user=POSTGRES_OWNER,
            host=str(self.socket_directory),
            port=POSTGRES_PORT,
        )


def postgres_binary_directory() -> Path:
    """Discover PostgreSQL server binaries through ``pg_config``."""
    pg_config = shutil.which("pg_config")
    if pg_config is None:
        pytest.skip("pg_config is required for local PostgreSQL integration tests")

    result = subprocess.run(
        [pg_config, "--bindir"],
        check=True,
        capture_output=True,
        text=True,
    )
    binary_directory = Path(result.stdout.strip())
    missing = [
        binary_name
        for binary_name in ("initdb", "pg_ctl")
        if not (binary_directory / binary_name).is_file()
    ]
    if missing:
        pytest.skip("PostgreSQL server binaries are required for integration tests")
    return binary_directory


def run_postgres_command(command: list[str], *, action: str) -> None:
    """Run a local server command while keeping diagnostics connection-free."""
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        pytest.fail(f"Local PostgreSQL could not {action}", pytrace=False)


def install_supabase_stubs(connection: Connection[Any]) -> None:
    """Create only the Supabase-owned objects required by the migration."""
    connection.execute(
        """
        alter default privileges in schema public
        grant all privileges on tables to service_role
        """
    )
    connection.execute("create schema auth")
    connection.execute(
        """
        create table auth.users (
            id uuid primary key
        )
        """
    )
    connection.execute(
        """
        create function auth.uid()
        returns uuid
        language sql
        stable
        set search_path = ''
        as $function$
            select nullif(
                pg_catalog.current_setting('request.jwt.claim.sub', true),
                ''
            )::uuid;
        $function$
        """
    )
    connection.execute("grant usage on schema auth to anon, authenticated")
    connection.execute("grant execute on function auth.uid() to anon, authenticated")


def add_auth_user(connection: Connection[Any], user_id: UUID) -> None:
    """Insert a fictitious local Supabase Auth user for FK-backed tests."""
    connection.execute("insert into auth.users (id) values (%s)", (user_id,))
