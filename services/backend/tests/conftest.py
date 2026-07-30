"""Shared, remote-free test configuration and PostgreSQL fixtures."""

from __future__ import annotations

import os

# These values are deliberately fictitious.  They must exist before any test
# module imports ``app.core.config``, whose module-level settings fail fast.
os.environ["SUPABASE_URL"] = "https://adt-tests.example.invalid"
os.environ["SUPABASE_PUBLISHABLE_KEY"] = "adt-tests-public-key-not-a-secret"
os.environ["SUPABASE_DATABASE_URL"] = "postgresql://adt_tests@127.0.0.1:1/adt_tests"
os.environ["ADT_ENVIRONMENT"] = "test"
os.environ["ADT_LOG_LEVEL"] = "WARNING"
os.environ["ADT_CORS_ORIGINS"] = '["http://localhost:5173"]'
os.environ["ADT_API_HOST"] = "127.0.0.1"
os.environ["ADT_API_PORT"] = "8000"

from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql

from app.database import Database
from tests.postgres_support import (
    MIGRATION_PATHS,
    POSTGRES_OWNER,
    POSTGRES_PORT,
    PostgresCluster,
    add_auth_user,
    install_supabase_stubs,
    postgres_binary_directory,
    run_postgres_command,
)


@pytest.fixture(scope="session")
def postgres_cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PostgresCluster]:
    """Start one disposable local PostgreSQL cluster for the complete test run."""
    binary_directory = postgres_binary_directory()
    cluster_directory = tmp_path_factory.mktemp("adt-postgres")
    data_directory = cluster_directory / "data"
    socket_directory = cluster_directory / "socket"
    socket_directory.mkdir()
    log_path = cluster_directory / "postgres.log"

    run_postgres_command(
        [
            str(binary_directory / "initdb"),
            "--auth=trust",
            "--encoding=UTF8",
            "--locale=C",
            "--no-instructions",
            "--no-sync",
            f"--username={POSTGRES_OWNER}",
            f"--pgdata={data_directory}",
        ],
        action="initialize the test cluster",
    )

    server_options = f"-F -h '' -k {socket_directory} -p {POSTGRES_PORT}"
    run_postgres_command(
        [
            str(binary_directory / "pg_ctl"),
            "--pgdata",
            str(data_directory),
            "--log",
            str(log_path),
            "--options",
            server_options,
            "--wait",
            "start",
        ],
        action="start the test cluster",
    )

    cluster = PostgresCluster(socket_directory)
    with psycopg.connect(cluster.connection_info("postgres"), autocommit=True) as connection:
        connection.execute("create role anon nologin")
        connection.execute("create role authenticated nologin")
        connection.execute("create role service_role nologin bypassrls")

    try:
        yield cluster
    finally:
        run_postgres_command(
            [
                str(binary_directory / "pg_ctl"),
                "--pgdata",
                str(data_directory),
                "--mode",
                "fast",
                "--wait",
                "stop",
            ],
            action="stop the test cluster",
        )


@pytest.fixture
def database_url(postgres_cluster: PostgresCluster) -> Iterator[str]:
    """Create a clean database and apply every local migration in order."""
    database_name = f"adt_test_{uuid4().hex}"
    maintenance_url = postgres_cluster.connection_info("postgres")

    with psycopg.connect(maintenance_url, autocommit=True) as connection:
        connection.execute(sql.SQL("create database {}").format(sql.Identifier(database_name)))

    isolated_database_url = postgres_cluster.connection_info(database_name)
    try:
        with psycopg.connect(isolated_database_url, autocommit=True) as connection:
            install_supabase_stubs(connection)
            for migration_path in MIGRATION_PATHS:
                connection.execute(migration_path.read_text(encoding="utf-8"))
        yield isolated_database_url
    finally:
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("drop database {} with (force)").format(sql.Identifier(database_name))
            )


@pytest_asyncio.fixture
async def database(database_url: str) -> AsyncIterator[Database]:
    """Yield an opened asynchronous pool and always close it before DB teardown."""
    pooled_database = Database(database_url, min_size=1, max_size=4, timeout=2)
    await pooled_database.open()
    try:
        yield pooled_database
    finally:
        await pooled_database.close()


@pytest.fixture
def auth_user_id(database_url: str) -> UUID:
    """Create one fictitious local Supabase Auth identity."""
    user_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, user_id)
    return user_id


@pytest.fixture
def admin_user_id(database_url: str, auth_user_id: UUID) -> UUID:
    """Add the fictitious identity to the local administrator allow-list."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into public.app_admins (user_id, created_by)
            values (%s, %s)
            """,
            (auth_user_id, auth_user_id),
        )
    return auth_user_id
