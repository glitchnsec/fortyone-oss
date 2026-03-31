from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # NVIDIA NIM  (OpenAI-compatible)
    # Get a free key at: https://build.nvidia.com/
    nvidia_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    # Fast model used for structured extraction (JSON tasks)
    nim_model_fast: str = "meta/llama-3.1-8b-instruct"
    # Capable model used for free-form text (scheduling, general chat)
    nim_model_capable: str = "meta/llama-3.3-70b-instruct"

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

    # Behaviour flags
    mock_sms: bool = True        # Print SMS to logs instead of calling Twilio
    environment: str = "development"

    @property
    def is_mock_sms(self) -> bool:
        # Auto-enable mock if Twilio creds are absent
        return self.mock_sms or not self.twilio_account_sid

    @property
    def has_llm(self) -> bool:
        # Real NVIDIA API keys are "nvapi-" + ~80 chars.  Reject short placeholders.
        return len(self.nvidia_api_key) > 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
