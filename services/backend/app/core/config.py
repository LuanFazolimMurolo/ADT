from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration from environment variables."""

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_title: str = "ADT API"

    # Environment
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Data
    data_dir: str = "./data"

    # Admin
    admin_user_id: str = "admin-default-id"

    class Config:
        env_prefix = "ADT_"
        case_sensitive = False

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


def get_settings() -> Settings:
    """Get application settings."""
    return Settings()


settings = get_settings()
