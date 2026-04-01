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
    llm_model_fast: str = "meta-llama/llama-3.1-8b-instruct:free"
    # Capable model: free-form responses, scheduling, general chat
    llm_model_capable: str = "meta-llama/llama-3.3-70b-instruct:free"
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
