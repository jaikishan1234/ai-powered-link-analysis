"""Application settings loaded from environment variables."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Runtime configuration.

    Values are read from environment variables and, when present, from the
    project-root ``.env`` file. Environment variables take precedence over
    ``.env`` values. Credentials must only ever come from the environment;
    never hardcode them in source code.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI-Powered Website Link Analysis API"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=4000, ge=1, le=65535)

    # Outbound website fetching. Conservative defaults, overridable via
    # FETCH_TIMEOUT_SECONDS, FETCH_MAX_RESPONSE_BYTES, and FETCH_MAX_REDIRECTS.
    fetch_timeout_seconds: float = Field(default=10.0, gt=0)
    fetch_max_response_bytes: int = Field(default=2_000_000, gt=0)
    fetch_max_redirects: int = Field(default=5, ge=0, le=20)

    # HTML extraction. The cleaned text is capped to keep later LLM inputs
    # bounded; override with EXTRACT_MAX_TEXT_CHARS.
    extract_max_text_chars: int = Field(default=20_000, gt=0)

    # LLM provider credential, supplied via the LLM_API_KEY environment
    # variable. Unused until LLM integration is implemented.
    llm_api_key: SecretStr | None = None

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def normalize_llm_api_key(cls, value: object) -> object:
        """Treat an empty or whitespace-only credential as unset."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_debug_for_environment(self) -> Self:
        """Reject debug mode in production."""
        if self.environment == "production" and self.debug:
            raise ValueError(
                "DEBUG must be false when ENVIRONMENT=production; "
                "debug mode and auto-reload are not allowed in production."
            )
        return self


settings = Settings()
