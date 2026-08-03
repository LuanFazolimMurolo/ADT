"""Unit tests for typed and sanitized backend configuration."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import AnyHttpUrl, SecretStr

from app.core.config import (
    ConfigurationError,
    MarketDataSettings,
    Settings,
    get_market_data_settings,
    get_settings,
)

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
        "ADT_DATA_DIR",
        "ADT_MARKET_HTTP_TIMEOUT",
        "ADT_MARKET_HTTP_MAX_CONNECTIONS",
        "ADT_MARKET_HTTP_RETRIES",
        "ADT_MARKET_USER_AGENT",
        "ADT_MARKET_ALLOW_OPEN_CANDLES",
        "ADT_MARKET_ASSET_CATALOG_TTL_SECONDS",
        "ADT_MARKET_ASSET_CATALOG_MAX_INSTRUMENTS",
        "ADT_MARKET_CONTINUOUS_INTERVAL_SECONDS",
        "ADT_MARKET_CONTINUOUS_BOOTSTRAP_CANDLES",
        "ADT_MARKET_CONTINUOUS_MAX_TARGETS",
        "ADT_MARKET_MAX_FETCH_CANDLES",
        "ADT_MARKET_BACKFILL_CHUNK_CANDLES",
        "ADT_MARKET_BACKFILL_MAX_TOTAL_CANDLES",
        "ADT_MARKET_INCREMENTAL_OVERLAP_CANDLES",
        "ADT_MARKET_JOB_LOCK_TIMEOUT",
        "ADT_MARKET_JOB_STALE_AFTER",
        "ADT_MARKET_JOB_MAX_CHUNKS",
        "ADT_MARKET_RESAMPLE_MAX_SOURCE_CANDLES",
        "ADT_MARKET_RESAMPLE_MAX_GROUPS",
        "ADT_MARKET_RESAMPLE_GAP_POLICY",
        "ADT_MARKET_QUALITY_MAX_ISSUES",
        "ADT_MARKET_SNAPSHOT_MAX_PARTITIONS",
        "ADT_MARKET_DERIVED_DIR",
        "ADT_MARKET_MANIFEST_SCHEMA_VERSION",
        "ADT_BACKTEST_DIR",
        "ADT_BACKTEST_MAX_CANDLES",
        "ADT_BACKTEST_MAX_ORDERS",
        "ADT_BACKTEST_MAX_OPEN_ORDERS",
        "ADT_BACKTEST_MAX_EVENTS",
        "ADT_BACKTEST_HISTORY_WINDOW",
        "ADT_BACKTEST_DEFAULT_MAKER_FEE_BPS",
        "ADT_BACKTEST_DEFAULT_TAKER_FEE_BPS",
        "ADT_BACKTEST_DEFAULT_SLIPPAGE_BPS",
        "ADT_BACKTEST_ENGINE_VERSION",
        "ADT_BACKTEST_SCHEMA_VERSION",
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
    monkeypatch.setenv("ADT_DATA_DIR", "/tmp/adt-test-data")
    monkeypatch.setenv("ADT_MARKET_HTTP_TIMEOUT", "12.5")
    monkeypatch.setenv("ADT_MARKET_HTTP_MAX_CONNECTIONS", "3")
    monkeypatch.setenv("ADT_MARKET_HTTP_RETRIES", "2")
    monkeypatch.setenv("ADT_MARKET_HTTP_MAX_RETRY_AFTER", "45")
    monkeypatch.setenv("ADT_MARKET_USER_AGENT", "ADT-Test-Agent/1.0")
    monkeypatch.setenv("ADT_MARKET_ALLOW_OPEN_CANDLES", "true")
    monkeypatch.setenv("ADT_MARKET_ASSET_CATALOG_TTL_SECONDS", "120")
    monkeypatch.setenv("ADT_MARKET_ASSET_CATALOG_MAX_INSTRUMENTS", "5000")
    monkeypatch.setenv("ADT_MARKET_CONTINUOUS_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("ADT_MARKET_CONTINUOUS_BOOTSTRAP_CANDLES", "720")
    monkeypatch.setenv("ADT_MARKET_CONTINUOUS_MAX_TARGETS", "12")
    monkeypatch.setenv("ADT_MARKET_MAX_FETCH_CANDLES", "2500")
    monkeypatch.setenv("ADT_MARKET_BACKFILL_CHUNK_CANDLES", "500")
    monkeypatch.setenv("ADT_MARKET_BACKFILL_MAX_TOTAL_CANDLES", "50000")
    monkeypatch.setenv("ADT_MARKET_INCREMENTAL_OVERLAP_CANDLES", "3")
    monkeypatch.setenv("ADT_MARKET_JOB_LOCK_TIMEOUT", "2.5")
    monkeypatch.setenv("ADT_MARKET_JOB_STALE_AFTER", "600")
    monkeypatch.setenv("ADT_MARKET_JOB_MAX_CHUNKS", "200")
    monkeypatch.setenv("ADT_BACKTEST_DIR", "local-backtests")
    monkeypatch.setenv("ADT_BACKTEST_MAX_CANDLES", "50000")
    monkeypatch.setenv("ADT_BACKTEST_MAX_ORDERS", "5000")
    monkeypatch.setenv("ADT_BACKTEST_MAX_OPEN_ORDERS", "250")
    monkeypatch.setenv("ADT_BACKTEST_MAX_EVENTS", "100000")
    monkeypatch.setenv("ADT_BACKTEST_HISTORY_WINDOW", "128")
    monkeypatch.setenv("ADT_BACKTEST_DEFAULT_MAKER_FEE_BPS", "7.5")
    monkeypatch.setenv("ADT_BACKTEST_DEFAULT_TAKER_FEE_BPS", "12.5")
    monkeypatch.setenv("ADT_BACKTEST_DEFAULT_SLIPPAGE_BPS", "3.25")
    monkeypatch.setenv("ADT_BACKTEST_ENGINE_VERSION", "3a-test.2")
    monkeypatch.setenv("ADT_BACKTEST_SCHEMA_VERSION", "2")

    loaded_settings = get_settings()

    assert loaded_settings.environment == "test"
    assert loaded_settings.log_level == "DEBUG"
    assert loaded_settings.cors_origins_list == [
        "https://admin.example.test",
        "http://localhost:5173",
    ]
    assert loaded_settings.api_host == "127.0.0.1"
    assert loaded_settings.api_port == 8123
    assert str(loaded_settings.data_dir) == "/tmp/adt-test-data"
    assert loaded_settings.market_http_timeout == 12.5
    assert loaded_settings.market_http_max_connections == 3
    assert loaded_settings.market_http_retries == 2
    assert loaded_settings.market_http_max_retry_after == 45
    assert loaded_settings.market_user_agent == "ADT-Test-Agent/1.0"
    assert loaded_settings.market_allow_open_candles is True
    assert loaded_settings.market_asset_catalog_ttl_seconds == 120
    assert loaded_settings.market_asset_catalog_max_instruments == 5000
    assert loaded_settings.market_continuous_interval_seconds == 15
    assert loaded_settings.market_continuous_bootstrap_candles == 720
    assert loaded_settings.market_continuous_max_targets == 12
    assert loaded_settings.market_max_fetch_candles == 2500
    assert loaded_settings.market_backfill_chunk_candles == 500
    assert loaded_settings.market_backfill_max_total_candles == 50000
    assert loaded_settings.market_incremental_overlap_candles == 3
    assert loaded_settings.market_job_lock_timeout == 2.5
    assert loaded_settings.market_job_stale_after == 600
    assert loaded_settings.market_job_max_chunks == 200
    assert loaded_settings.backtest_dir == Path("local-backtests")
    assert loaded_settings.backtest_max_candles == 50_000
    assert loaded_settings.backtest_max_orders == 5_000
    assert loaded_settings.backtest_max_open_orders == 250
    assert loaded_settings.backtest_max_events == 100_000
    assert loaded_settings.backtest_history_window == 128
    assert loaded_settings.backtest_default_maker_fee_bps == Decimal("7.5")
    assert loaded_settings.backtest_default_taker_fee_bps == Decimal("12.5")
    assert loaded_settings.backtest_default_slippage_bps == Decimal("3.25")
    assert loaded_settings.backtest_engine_version == "3a-test.2"
    assert loaded_settings.backtest_schema_version == 2
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


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("market_http_timeout", 0.5),
        ("market_http_max_connections", 0),
        ("market_http_retries", 6),
        ("market_http_max_retry_after", 3_601),
        ("market_asset_catalog_ttl_seconds", 86_401),
        ("market_asset_catalog_max_instruments", 100_001),
        ("market_continuous_interval_seconds", 0),
        ("market_continuous_bootstrap_candles", 1_000_001),
        ("market_continuous_max_targets", 1_001),
        ("market_max_fetch_candles", 100_001),
        ("market_user_agent", "bad\nagent"),
    ],
)
def test_market_settings_have_safe_limits(field_name: str, value: object) -> None:
    kwargs = {
        "supabase_url": AnyHttpUrl(REQUIRED_ENVIRONMENT["SUPABASE_URL"]),
        "supabase_publishable_key": SecretStr(REQUIRED_ENVIRONMENT["SUPABASE_PUBLISHABLE_KEY"]),
        "supabase_database_url": SecretStr(REQUIRED_ENVIRONMENT["SUPABASE_DATABASE_URL"]),
        field_name: value,
    }
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_backtest_settings_load_without_supabase_and_use_conservative_defaults() -> None:
    settings = get_market_data_settings()

    assert settings.backtest_dir == Path("backtests")
    assert settings.backtest_max_candles == 1_000_000
    assert settings.backtest_max_orders == 100_000
    assert settings.backtest_max_open_orders == 1_000
    assert settings.backtest_max_events == 2_000_000
    assert settings.backtest_history_window == 512
    assert settings.backtest_default_maker_fee_bps == Decimal("10")
    assert settings.backtest_default_taker_fee_bps == Decimal("10")
    assert settings.backtest_default_slippage_bps == Decimal("5")
    assert settings.backtest_engine_version == "3b-1"
    assert settings.backtest_schema_version == 2


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("backtest_dir", Path("../outside")),
        ("backtest_dir", Path("/absolute")),
        ("backtest_max_candles", 10_000_001),
        ("backtest_max_orders", 1_000_001),
        ("backtest_max_open_orders", 100_001),
        ("backtest_max_events", 20_000_001),
        ("backtest_history_window", 100_001),
        ("backtest_default_maker_fee_bps", Decimal("1000.01")),
        ("backtest_default_taker_fee_bps", Decimal("NaN")),
        ("backtest_default_slippage_bps", Decimal("Infinity")),
        ("backtest_engine_version", "unsafe/version"),
        ("backtest_schema_version", 3),
    ],
)
def test_backtest_settings_have_safe_limits(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        MarketDataSettings(**{field_name: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"backtest_max_orders": 5, "backtest_max_open_orders": 6},
        {"backtest_max_candles": 5, "backtest_history_window": 6},
        {
            "market_backfill_max_total_candles": 100,
            "market_continuous_bootstrap_candles": 101,
        },
    ],
)
def test_backtest_settings_reject_contradictory_limits(overrides: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        MarketDataSettings(**overrides)


def test_invalid_backtest_environment_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid_value = "not-a-decimal-sensitive-marker"
    monkeypatch.setenv("ADT_BACKTEST_DEFAULT_TAKER_FEE_BPS", invalid_value)

    with pytest.raises(ConfigurationError) as captured_error:
        get_market_data_settings()

    message = str(captured_error.value)
    assert "ADT_BACKTEST_DEFAULT_TAKER_FEE_BPS" in message
    assert invalid_value not in message
