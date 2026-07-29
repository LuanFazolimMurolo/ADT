"""Tests for the initial administrator bootstrap script."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from scripts.bootstrap_admin import (
    ADMIN_USER_ID_VARIABLE,
    DATABASE_URL_VARIABLE,
    BootstrapConfig,
    BootstrapConfigurationError,
    DatabaseConnector,
    bootstrap_admin,
    main,
)

ADMIN_USER_ID = "2ca1f2a8-fdf6-45d2-ae20-cc78e508fb91"
DATABASE_URL = "postgresql://example.invalid/adt-test"


@pytest.mark.parametrize("missing_variable", [ADMIN_USER_ID_VARIABLE, DATABASE_URL_VARIABLE])
def test_config_rejects_missing_values(missing_variable: str) -> None:
    """Both required settings must exist."""
    environment = {
        ADMIN_USER_ID_VARIABLE: ADMIN_USER_ID,
        DATABASE_URL_VARIABLE: DATABASE_URL,
    }
    del environment[missing_variable]

    with pytest.raises(BootstrapConfigurationError, match=missing_variable):
        BootstrapConfig.from_environment(environment)


@pytest.mark.parametrize("empty_variable", [ADMIN_USER_ID_VARIABLE, DATABASE_URL_VARIABLE])
def test_config_rejects_empty_values(empty_variable: str) -> None:
    """Both required settings must contain a non-whitespace value."""
    environment = {
        ADMIN_USER_ID_VARIABLE: ADMIN_USER_ID,
        DATABASE_URL_VARIABLE: DATABASE_URL,
    }
    environment[empty_variable] = " "

    with pytest.raises(BootstrapConfigurationError, match=empty_variable):
        BootstrapConfig.from_environment(environment)


def test_config_rejects_invalid_admin_uuid_without_echoing_it() -> None:
    """An invalid identifier produces a clear error without reflecting its value."""
    invalid_identifier = "not-a-user-id"

    with pytest.raises(BootstrapConfigurationError) as captured_error:
        BootstrapConfig.from_environment(
            {
                ADMIN_USER_ID_VARIABLE: invalid_identifier,
                DATABASE_URL_VARIABLE: DATABASE_URL,
            }
        )

    assert ADMIN_USER_ID_VARIABLE in str(captured_error.value)
    assert invalid_identifier not in str(captured_error.value)


def test_config_parses_valid_environment() -> None:
    """Valid environment values are normalized into typed settings."""
    config = BootstrapConfig.from_environment(
        {
            ADMIN_USER_ID_VARIABLE: f" {ADMIN_USER_ID} ",
            DATABASE_URL_VARIABLE: f" {DATABASE_URL} ",
        }
    )

    assert config.admin_user_id == UUID(ADMIN_USER_ID)
    assert config.database_url == DATABASE_URL


def test_bootstrap_is_idempotent_and_uses_explicit_transactions() -> None:
    """Repeated registration relies on the primary-key conflict and remains successful."""
    connector_mock = MagicMock()
    connection_mock = connector_mock.return_value.__enter__.return_value
    transaction_mock = connection_mock.transaction.return_value
    connection_mock.execute.side_effect = [
        SimpleNamespace(rowcount=1),
        SimpleNamespace(rowcount=0),
    ]
    connector = cast(DatabaseConnector, connector_mock)
    config = BootstrapConfig(UUID(ADMIN_USER_ID), DATABASE_URL)

    results = [bootstrap_admin(config, connector), bootstrap_admin(config, connector)]

    assert results == [True, False]
    assert connector_mock.call_count == 2
    assert connection_mock.transaction.call_count == 2
    assert transaction_mock.__enter__.call_count == 2
    assert transaction_mock.__exit__.call_count == 2
    assert connection_mock.execute.call_count == 2

    for execution in connection_mock.execute.call_args_list:
        query = " ".join(execution.args[0].split())
        assert "ON CONFLICT (user_id) DO NOTHING" in query
        assert execution.args[1] == (UUID(ADMIN_USER_ID),)


def test_main_does_not_expose_connection_details(capsys: pytest.CaptureFixture[str]) -> None:
    """Database failures return a safe message without printing exception contents."""
    connector_mock = MagicMock(side_effect=RuntimeError(DATABASE_URL))
    connector = cast(DatabaseConnector, connector_mock)

    exit_code = main(
        {
            ADMIN_USER_ID_VARIABLE: ADMIN_USER_ID,
            DATABASE_URL_VARIABLE: DATABASE_URL,
        },
        connector,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert DATABASE_URL not in captured.out
    assert DATABASE_URL not in captured.err
    assert "Não foi possível confirmar" in captured.err


def test_main_prints_only_a_safe_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    """Successful bootstrap output omits database and user details."""
    connector_mock = MagicMock()
    connection_mock = connector_mock.return_value.__enter__.return_value
    connection_mock.execute.return_value = SimpleNamespace(rowcount=0)
    connector = cast(DatabaseConnector, connector_mock)

    exit_code = main(
        {
            ADMIN_USER_ID_VARIABLE: ADMIN_USER_ID,
            DATABASE_URL_VARIABLE: DATABASE_URL,
        },
        connector,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "Administrador inicial confirmado com sucesso.\n"
    assert captured.err == ""
    assert ADMIN_USER_ID not in captured.out
    assert DATABASE_URL not in captured.out
