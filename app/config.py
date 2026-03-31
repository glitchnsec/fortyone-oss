from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Database
    database_url: str = "sqlite:///./assistant.db"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Queue / pub-sub names
    queue_name: str = "jobs"
    response_channel: str = "job_completed"

    # Behaviour flags
    mock_sms: bool = True        # Print SMS to logs instead of calling Twilio
    environment: str = "development"

    @property
    def is_mock_sms(self) -> bool:
        # Auto-enable mock if Twilio creds are absent
        return self.mock_sms or not self.twilio_account_sid

    @property
    def has_llm(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
