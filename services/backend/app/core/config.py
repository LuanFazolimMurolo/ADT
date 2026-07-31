"""Typed, fail-fast application configuration.

Only :func:`get_settings` should be used to load settings from the process
environment.  It deliberately replaces Pydantic's detailed validation output
with a message that contains variable names, never their values.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict
from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
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
    "data_dir": "ADT_DATA_DIR",
    "market_http_timeout": "ADT_MARKET_HTTP_TIMEOUT",
    "market_http_max_connections": "ADT_MARKET_HTTP_MAX_CONNECTIONS",
    "market_http_retries": "ADT_MARKET_HTTP_RETRIES",
    "market_http_max_retry_after": "ADT_MARKET_HTTP_MAX_RETRY_AFTER",
    "market_user_agent": "ADT_MARKET_USER_AGENT",
    "market_allow_open_candles": "ADT_MARKET_ALLOW_OPEN_CANDLES",
    "market_max_fetch_candles": "ADT_MARKET_MAX_FETCH_CANDLES",
    "market_backfill_chunk_candles": "ADT_MARKET_BACKFILL_CHUNK_CANDLES",
    "market_backfill_max_total_candles": "ADT_MARKET_BACKFILL_MAX_TOTAL_CANDLES",
    "market_incremental_overlap_candles": "ADT_MARKET_INCREMENTAL_OVERLAP_CANDLES",
    "market_job_lock_timeout": "ADT_MARKET_JOB_LOCK_TIMEOUT",
    "market_job_stale_after": "ADT_MARKET_JOB_STALE_AFTER",
    "market_job_max_chunks": "ADT_MARKET_JOB_MAX_CHUNKS",
    "market_resample_max_source_candles": "ADT_MARKET_RESAMPLE_MAX_SOURCE_CANDLES",
    "market_resample_max_groups": "ADT_MARKET_RESAMPLE_MAX_GROUPS",
    "market_resample_gap_policy": "ADT_MARKET_RESAMPLE_GAP_POLICY",
    "market_quality_max_issues": "ADT_MARKET_QUALITY_MAX_ISSUES",
    "market_snapshot_max_partitions": "ADT_MARKET_SNAPSHOT_MAX_PARTITIONS",
    "market_derived_dir": "ADT_MARKET_DERIVED_DIR",
    "market_manifest_schema_version": "ADT_MARKET_MANIFEST_SCHEMA_VERSION",
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
    data_dir: Path = Path("./data")
    market_http_timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    market_http_max_connections: int = Field(default=4, ge=1, le=32)
    market_http_retries: int = Field(default=3, ge=0, le=5)
    market_http_max_retry_after: float = Field(default=30.0, ge=0.0, le=3_600.0)
    market_user_agent: str = Field(default="ADT-MarketData/0.1", min_length=8, max_length=128)
    market_allow_open_candles: bool = False
    market_max_fetch_candles: int = Field(default=10_000, ge=1, le=100_000)
    market_backfill_chunk_candles: int = Field(default=1_000, ge=1, le=10_000)
    market_backfill_max_total_candles: int = Field(
        default=1_000_000,
        ge=1,
        le=10_000_000,
    )
    market_incremental_overlap_candles: int = Field(default=2, ge=0, le=100)
    market_job_lock_timeout: float = Field(default=10.0, ge=0.0, le=300.0)
    market_job_stale_after: float = Field(default=3_600.0, ge=1.0, le=604_800.0)
    market_job_max_chunks: int = Field(default=10_000, ge=1, le=100_000)
    market_resample_max_source_candles: int = Field(default=2_000_000, ge=1, le=10_000_000)
    market_resample_max_groups: int = Field(default=500_000, ge=1, le=2_000_000)
    market_resample_gap_policy: Literal["STRICT", "SKIP_INCOMPLETE", "MARK_INCOMPLETE"] = "STRICT"
    market_quality_max_issues: int = Field(default=1_000, ge=1, le=100_000)
    market_snapshot_max_partitions: int = Field(default=1_200, ge=1, le=10_000)
    market_derived_dir: Path = Path("derived")
    market_manifest_schema_version: int = Field(default=1, ge=1, le=100)

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Require a plain HTTP origin, never credentials or URL suffixes."""
        parsed = urlsplit(str(value))
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("must be an HTTP origin without credentials or path")
        return value

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
        seen_origins: set[str] = set()
        for origin in origins:
            stripped_origin = origin.strip()
            if not stripped_origin or stripped_origin == "*":
                raise ValueError("must contain explicit HTTP origins")
            parsed_origin = urlsplit(stripped_origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or parsed_origin.hostname is None
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise ValueError("must contain HTTP origins without credentials or paths")
            validated_origin = AnyHttpUrl(stripped_origin)
            normalized_origin = str(validated_origin).rstrip("/")
            if normalized_origin not in seen_origins:
                normalized_origins.append(normalized_origin)
                seen_origins.add(normalized_origin)
        return normalized_origins

    @field_validator("api_host")
    @classmethod
    def validate_api_host(cls, value: str) -> str:
        """Reject an empty listen address."""
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("must not be blank")
        return normalized_value

    @field_validator("market_user_agent")
    @classmethod
    def validate_market_user_agent(cls, value: str) -> str:
        """Require an identifiable single-line public API user agent."""
        normalized = value.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise ValueError("must be a nonblank single-line identifier")
        return normalized

    @field_validator("market_derived_dir")
    @classmethod
    def validate_market_derived_dir(cls, value: Path) -> Path:
        if value.is_absolute() or ".." in value.parts or not value.parts:
            raise ValueError("must be a safe relative path")
        return value

    @model_validator(mode="after")
    def validate_production_origins(self) -> Settings:
        """Production accepts HTTPS origins only and never local browser hosts."""
        if self.environment != "production":
            return self

        def is_local_host(hostname: str | None) -> bool:
            if hostname is None:
                return True
            normalized_host = hostname.rstrip(".").lower()
            if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
                return True
            try:
                return ipaddress.ip_address(normalized_host).is_loopback
            except ValueError:
                try:
                    packed_ipv4 = socket.inet_aton(normalized_host)
                except OSError:
                    return False
                return ipaddress.ip_address(packed_ipv4).is_loopback

        invalid_origins = [
            origin
            for origin in self.cors_origins
            if ((parsed := urlsplit(origin)).scheme != "https" or is_local_host(parsed.hostname))
        ]
        if invalid_origins:
            raise ValueError("production CORS origins must be non-local HTTPS origins")
        parsed_supabase_url = urlsplit(str(self.supabase_url))
        if parsed_supabase_url.scheme != "https" or is_local_host(parsed_supabase_url.hostname):
            raise ValueError("production Supabase URL must use non-local HTTPS")
        connection_options = conninfo_to_dict(self.supabase_database_url.get_secret_value())
        if connection_options.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("production database URL must require TLS")
        return self

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
