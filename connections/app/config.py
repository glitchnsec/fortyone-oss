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
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_redirect_uri: str = "http://localhost:8001/oauth/callback/slack"
    mcp_oauth_redirect_uri: str = "http://localhost:8000/connections/callback"
    dashboard_url: str = "http://localhost:8000"
    service_auth_token: str = ""
    # WARNING: Empty allowlist means ALL MCP server URLs are permitted (SSRF risk).
    # Set to comma-separated URL patterns in production.
    mcp_allowlist: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
