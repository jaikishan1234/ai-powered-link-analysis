"""Application settings loaded from environment variables."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Values are read from environment variables and, when present, from a
    local ``.env`` file. Credentials must only ever come from the
    environment; never hardcode them in source code.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI-Powered Website Link Analysis API"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=4000, ge=1, le=65535)

    # LLM provider credential, supplied via the LLM_API_KEY environment
    # variable. Unused until LLM integration is implemented.
    llm_api_key: SecretStr | None = None


settings = Settings()
