"""Connections service configuration."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/operator"
    encryption_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8001/oauth/callback/google"
    mcp_oauth_redirect_uri: str = "http://localhost:8000/connections/callback"
    dashboard_url: str = "http://localhost:8000"
    mcp_allowlist: str = ""  # Comma-separated MCP server URL patterns. Empty = allow all.


@lru_cache
def get_settings() -> Settings:
    return Settings()
