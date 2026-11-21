"""Tests for the health-check endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import app

client = TestClient(app)


def test_health_check_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
    }


def test_settings_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORT", raising=False)

    assert Settings(_env_file=None).port == 4000
