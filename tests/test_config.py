"""Tests for application configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import ENV_FILE, PROJECT_ROOT, Settings


@pytest.mark.parametrize("raw_key", ["", "   ", "\t\n "])
def test_blank_llm_api_key_is_normalized_to_none(raw_key: str) -> None:
    settings = Settings(_env_file=None, llm_api_key=raw_key)

    assert settings.llm_api_key is None


def test_non_blank_llm_api_key_is_preserved() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key")

    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "test-key"


def test_environment_variables_override_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=from-dotenv\nPORT=5000\n", encoding="utf-8")
    monkeypatch.setenv("APP_NAME", "from-environment")
    monkeypatch.delenv("PORT", raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "from-environment"
    assert settings.port == 5000


def test_env_file_is_resolved_from_project_root() -> None:
    assert ENV_FILE.is_absolute()
    assert ENV_FILE == PROJECT_ROOT / ".env"


def test_production_rejects_enabled_debug() -> None:
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        Settings(_env_file=None, environment="production", debug=True)


def test_production_allows_disabled_debug() -> None:
    settings = Settings(_env_file=None, environment="production", debug=False)

    assert settings.environment == "production"
    assert settings.debug is False


def test_development_allows_enabled_debug() -> None:
    settings = Settings(_env_file=None, environment="development", debug=True)

    assert settings.debug is True


def test_settings_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORT", raising=False)

    assert Settings(_env_file=None).port == 4000
