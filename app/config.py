from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # OpenRouter — model-agnostic LLM gateway (OpenAI-compatible)
    # Get a free key at: https://openrouter.ai/keys
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Fast model: structured extraction, ACKs, classification
    # Any OpenRouter model ID works — e.g. openai/gpt-4o-mini, google/gemini-flash-1.5
    llm_model_fast: str = "openai/gpt-4o-mini"
    # Capable model: free-form responses, scheduling, general chat
    llm_model_capable: str = "anthropic/claude-3.5-haiku"
    # Optional: shown on openrouter.ai dashboard for usage tracking
    openrouter_site_url: str = ""
    openrouter_site_name: str = "Personal Assistant"

    # Database
    database_url: str = "sqlite:///./assistant.db"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Queue / pub-sub names
    queue_name: str = "jobs"
    response_channel: str = "job_completed"

    # Slack (optional — leave blank to run without Slack support)
    slack_bot_token: str = ""       # xoxb-...  (Bot User OAuth Token)
    slack_signing_secret: str = ""  # from App Credentials page

    # Encryption — required in production; generated with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    # Public URL — set when behind a reverse proxy (ngrok, nginx, etc.)
    # Twilio signs requests against this URL; must match exactly.
    # Example: https://abcd-1234.ngrok-free.app
    base_url: str = ""

    # JWT Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # External services
    connections_service_url: str = "http://connections:8001"
    twilio_verify_service_sid: str = ""    # For SMS OTP verification (plan 02-05)
    brave_api_key: str = ""                # For Brave Search (plan 02-07)
    dashboard_url: str = "http://localhost:5173"  # Redirect target post-OAuth

    # Race timeout: wait for worker before sending ACK (UAT showed 2.4s typical)
    race_timeout_s: float = 4.0

    # Behaviour flags
    mock_sms: bool = True        # Print SMS to logs instead of calling Twilio
    environment: str = "development"

    @property
    def is_mock_sms(self) -> bool:
        # Auto-enable mock if Twilio creds are absent
        return self.mock_sms or not self.twilio_account_sid

    @property
    def has_llm(self) -> bool:
        # OpenRouter keys start with "sk-or-" and are ~50+ chars
        return len(self.openrouter_api_key) > 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
