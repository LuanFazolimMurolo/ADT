"""Typed, fail-fast application configuration.

Only :func:`get_settings` should be used to load settings from the process
environment.  It deliberately replaces Pydantic's detailed validation output
with a message that contains variable names, never their values.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Literal
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict
from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

REQUIRED_ENVIRONMENT_VARIABLES = (
    "SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_DATABASE_URL",
)

_FIELD_ENVIRONMENT_NAMES = {
    "supabase_url": "SUPABASE_URL",
    "supabase_publishable_key": "SUPABASE_PUBLISHABLE_KEY",
    "supabase_database_url": "SUPABASE_DATABASE_URL",
    "environment": "ADT_ENVIRONMENT",
    "log_level": "ADT_LOG_LEVEL",
    "cors_origins": "ADT_CORS_ORIGINS",
    "api_host": "ADT_API_HOST",
    "api_port": "ADT_API_PORT",
}


class ConfigurationError(RuntimeError):
    """A safe configuration-loading failure suitable for startup output."""


class Settings(BaseSettings):
    """Backend settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="ADT_",
        case_sensitive=False,
        extra="ignore",
        env_file=None,
        populate_by_name=True,
    )

    supabase_url: AnyHttpUrl = Field(validation_alias="SUPABASE_URL")
    supabase_publishable_key: SecretStr = Field(validation_alias="SUPABASE_PUBLISHABLE_KEY")
    supabase_database_url: SecretStr = Field(validation_alias="SUPABASE_DATABASE_URL")

    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:3000",
        ]
    )
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_title: str = "ADT API"
    data_dir: str = "./data"

    @field_validator("supabase_publishable_key", "supabase_database_url")
    @classmethod
    def validate_non_empty_secret(cls, value: SecretStr) -> SecretStr:
        """Reject blank secrets without returning their contents."""
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("supabase_database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """Require the direct psycopg URL form used by this backend."""
        raw_url = value.get_secret_value().strip()
        if not raw_url.startswith("postgresql://"):
            raise ValueError("must use the postgresql:// scheme")

        parsed_url = urlsplit(raw_url)
        if parsed_url.scheme != "postgresql" or parsed_url.path in {"", "/"}:
            raise ValueError("must be a PostgreSQL connection URL")
        try:
            conninfo_to_dict(raw_url)
        except Exception:
            raise ValueError("must be a valid PostgreSQL connection URL") from None
        return SecretStr(raw_url)

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """Allow conventional lowercase input while storing a typed level."""
        return value.upper() if isinstance(value, str) else value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        """Accept a JSON array or a comma-separated list from the environment."""
        if not isinstance(value, str):
            return value

        raw_value = value.strip()
        if raw_value.startswith("["):
            try:
                parsed_value = json.loads(raw_value)
            except json.JSONDecodeError as error:
                raise ValueError("must be a JSON array or comma-separated list") from error
            return parsed_value
        return [origin.strip() for origin in raw_value.split(",") if origin.strip()]

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: list[str]) -> list[str]:
        """Validate and normalize every CORS origin."""
        if not origins:
            raise ValueError("must contain at least one origin")

        normalized_origins: list[str] = []
        for origin in origins:
            stripped_origin = origin.strip()
            if not stripped_origin or stripped_origin == "*":
                raise ValueError("must contain explicit HTTP origins")
            validated_origin = AnyHttpUrl(stripped_origin)
            normalized_origins.append(str(validated_origin).rstrip("/"))
        return normalized_origins

    @field_validator("api_host")
    @classmethod
    def validate_api_host(cls, value: str) -> str:
        """Reject an empty listen address."""
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("must not be blank")
        return normalized_value

    @property
    def cors_origins_list(self) -> list[str]:
        """Return a copy for middleware configuration compatibility."""
        return list(self.cors_origins)

    @property
    def supabase_issuer(self) -> str:
        """Return the exact issuer used by Supabase access tokens."""
        return f"{str(self.supabase_url).rstrip('/')}/auth/v1"


def _environment_name_from_error(error: Mapping[str, object]) -> str:
    location = error.get("loc", ())
    if isinstance(location, tuple) and location:
        field_name = str(location[0])
        return _FIELD_ENVIRONMENT_NAMES.get(field_name, field_name)
    return "backend configuration"


def get_settings() -> Settings:
    """Load settings while preventing Pydantic from echoing secret inputs."""
    try:
        return Settings()  # type: ignore[call-arg]  # Values come from BaseSettings.
    except ValidationError as error:
        errors = error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        missing_variables = sorted(
            {_environment_name_from_error(item) for item in errors if item.get("type") == "missing"}
        )
        invalid_variables = sorted(
            {_environment_name_from_error(item) for item in errors if item.get("type") != "missing"}
        )

        messages: list[str] = []
        if missing_variables:
            messages.append(
                "Missing required environment variables: " + ", ".join(missing_variables)
            )
        if invalid_variables:
            messages.append("Invalid backend configuration: " + ", ".join(invalid_variables))
        raise ConfigurationError("; ".join(messages)) from None


settings = get_settings()
