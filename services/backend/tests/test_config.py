"""Unit tests for typed and sanitized backend configuration."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import AnyHttpUrl, SecretStr

from app.core.config import ConfigurationError, Settings, get_settings

REQUIRED_ENVIRONMENT = {
    "SUPABASE_URL": "https://project.example.test",
    "SUPABASE_PUBLISHABLE_KEY": "publishable-test-value",
    "SUPABASE_DATABASE_URL": "postgresql://adt@example.test:5432/adt",
}


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep each loader test independent from the developer environment."""
    for variable_name in (
        *REQUIRED_ENVIRONMENT,
        "ADT_ENVIRONMENT",
        "ADT_LOG_LEVEL",
        "ADT_CORS_ORIGINS",
        "ADT_API_HOST",
        "ADT_API_PORT",
    ):
        monkeypatch.delenv(variable_name, raising=False)
    yield


def _set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable_name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(variable_name, value)


@pytest.mark.parametrize("missing_variable", REQUIRED_ENVIRONMENT)
def test_missing_required_setting_is_named_without_other_values(
    monkeypatch: pytest.MonkeyPatch,
    missing_variable: str,
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.delenv(missing_variable)

    with pytest.raises(ConfigurationError) as captured_error:
        get_settings()

    message = str(captured_error.value)
    assert missing_variable in message
    assert all(secret_value not in message for secret_value in REQUIRED_ENVIRONMENT.values())


@pytest.mark.parametrize(
    ("variable_name", "invalid_value"),
    [
        ("SUPABASE_PUBLISHABLE_KEY", " "),
        ("SUPABASE_DATABASE_URL", "postgresql+asyncpg://secret@example.test/adt"),
        ("ADT_API_PORT", "not-a-port"),
        ("ADT_CORS_ORIGINS", "not-a-url"),
    ],
)
def test_invalid_setting_error_never_echoes_its_value(
    monkeypatch: pytest.MonkeyPatch,
    variable_name: str,
    invalid_value: str,
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv(variable_name, invalid_value)

    with pytest.raises(ConfigurationError) as captured_error:
        get_settings()

    message = str(captured_error.value)
    assert variable_name in message
    if invalid_value.strip():
        assert invalid_value not in message
    assert "password" not in message


def test_settings_are_typed_and_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("ADT_ENVIRONMENT", "test")
    monkeypatch.setenv("ADT_LOG_LEVEL", "debug")
    monkeypatch.setenv(
        "ADT_CORS_ORIGINS",
        "https://admin.example.test/,http://localhost:5173",
    )
    monkeypatch.setenv("ADT_API_HOST", " 127.0.0.1 ")
    monkeypatch.setenv("ADT_API_PORT", "8123")

    loaded_settings = get_settings()

    assert loaded_settings.environment == "test"
    assert loaded_settings.log_level == "DEBUG"
    assert loaded_settings.cors_origins_list == [
        "https://admin.example.test",
        "http://localhost:5173",
    ]
    assert loaded_settings.api_host == "127.0.0.1"
    assert loaded_settings.api_port == 8123
    assert loaded_settings.supabase_issuer == "https://project.example.test/auth/v1"
    assert isinstance(loaded_settings.supabase_publishable_key, SecretStr)
    assert isinstance(loaded_settings.supabase_database_url, SecretStr)


def test_json_cors_origins_are_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv(
        "ADT_CORS_ORIGINS",
        '["https://one.example.test", "https://two.example.test/"]',
    )

    loaded_settings = get_settings()

    assert loaded_settings.cors_origins == [
        "https://one.example.test",
        "https://two.example.test",
    ]


def test_secret_representations_do_not_contain_values() -> None:
    loaded_settings = Settings(
        supabase_url=AnyHttpUrl(REQUIRED_ENVIRONMENT["SUPABASE_URL"]),
        supabase_publishable_key=SecretStr(REQUIRED_ENVIRONMENT["SUPABASE_PUBLISHABLE_KEY"]),
        supabase_database_url=SecretStr(REQUIRED_ENVIRONMENT["SUPABASE_DATABASE_URL"]),
    )

    representation = repr(loaded_settings)

    assert REQUIRED_ENVIRONMENT["SUPABASE_PUBLISHABLE_KEY"] not in representation
    assert REQUIRED_ENVIRONMENT["SUPABASE_DATABASE_URL"] not in representation
    assert "**********" in representation


@pytest.mark.parametrize(
    "origin",
    [
        "https://localhost",
        "https://localhost.",
        "https://foo.localhost",
        "https://127.0.0.1",
        "https://127.1",
        "https://[::1]",
    ],
)
def test_production_rejects_loopback_cors_variants(origin: str) -> None:
    with pytest.raises(ValueError):
        Settings(
            supabase_url=AnyHttpUrl("https://project.example.test"),
            supabase_publishable_key=SecretStr("public-value"),
            supabase_database_url=SecretStr(
                "postgresql://backend@db.example.test/adt?sslmode=require"
            ),
            environment="production",
            cors_origins=[origin],
        )


def test_production_requires_database_tls() -> None:
    with pytest.raises(ValueError):
        Settings(
            supabase_url=AnyHttpUrl("https://project.example.test"),
            supabase_publishable_key=SecretStr("public-value"),
            supabase_database_url=SecretStr("postgresql://backend@db.example.test/adt"),
            environment="production",
            cors_origins=["https://admin.example.test"],
        )
