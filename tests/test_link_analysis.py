"""Tests for the link analysis endpoint.

All external access is mocked: URL validation, website fetching, and the LLM
analysis service are replaced with fakes, so these tests never contact a real
website or LLM provider.
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import health
from app.api.routes.link_analysis import (
    get_analysis_service,
    router as link_analysis_router,
)
from app.schemas.analysis import (
    AnalysisResponse,
    CompetitorAnalysis,
    SEOImprovements,
    SearchQueries,
    ServicesAnalysis,
    TargetAudience,
)
from app.services.extraction import ExtractedPage, Heading
from app.services.llm import LLMNotConfiguredError, LLMRateLimitError
from app.utils.url_validation import ValidatedURL

PUBLIC_IPV4 = "93.184.216.34"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _validated_url() -> ValidatedURL:
    """Build a ValidatedURL without performing DNS."""
    return ValidatedURL(
        url="https://example.com/",
        scheme="https",
        hostname="example.com",
        port=443,
        resolved_addresses=(ipaddress.ip_address(PUBLIC_IPV4),),
    )


def _make_fetched_page() -> Any:
    """Build a FetchedPage with minimal HTML."""
    from app.services.fetching import FetchedPage

    return FetchedPage(
        url="https://example.com/",
        status_code=200,
        content_type="text/html",
        html="<html><body><p>Hello</p></body></html>",
        redirect_count=0,
    )


def _make_analysis_response() -> AnalysisResponse:
    """Build a complete AnalysisResponse for mocking."""
    return AnalysisResponse(
        url="https://example.com/",
        website_title="Example Page",
        website_description="An example website.",
        competitor_analysis=CompetitorAnalysis(),
        search_queries=SearchQueries(),
        seo_improvements=SEOImprovements(
            keywords=[],
            seo_title="Example",
            meta_description="Example site.",
            content_recommendations=[],
        ),
        services=ServicesAnalysis(),
        target_audience=TargetAudience(),
    )


def _build_app() -> FastAPI:
    application = FastAPI()
    application.include_router(health.router)
    application.include_router(link_analysis_router)
    return application


def _override_service(app: FastAPI, response: AnalysisResponse) -> None:
    """Override the analysis service dependency with a mock."""

    class _MockService:
        async def analyze_page(
            self, extracted_page: ExtractedPage
        ) -> AnalysisResponse:
            return response

    app.dependency_overrides[get_analysis_service] = lambda: _MockService()


def _clear_overrides(app: FastAPI) -> None:
    app.dependency_overrides.clear()


def test_successful_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid URL flows through validation, fetch, extraction, and analysis."""
    app = _build_app()
    _override_service(app, _make_analysis_response())

    # Mock URL validation to return a ValidatedURL without DNS.
    monkeypatch.setattr(
        "app.api.routes.link_analysis.validate_public_url",
        lambda url: _validated_url(),
    )

    # Mock fetch_html to return a FetchedPage.
    async def fake_fetch(validated_url: Any) -> Any:
        return _make_fetched_page()

    monkeypatch.setattr(
        "app.api.routes.link_analysis.fetch_html", fake_fetch
    )

    with TestClient(app) as client:
        response = client.post("/link-analysis", json={"url": "https://example.com/"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["url"] == "https://example.com/"
    assert data["website_title"] == "Example Page"
    assert data["website_description"] == "An example website."
    assert "competitor_analysis" in data
    assert "search_queries" in data
    assert "seo_improvements" in data
    assert "services" in data
    assert "target_audience" in data

    _clear_overrides(app)


def test_invalid_url_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed or unsafe URL returns HTTP 400."""
    app = _build_app()
    _override_service(app, _make_analysis_response())

    from app.utils.url_validation import URLValidationError

    def failing_validation(url: str) -> None:
        raise URLValidationError("Unsafe URL.")

    monkeypatch.setattr(
        "app.api.routes.link_analysis.validate_public_url",
        failing_validation,
    )

    with TestClient(app) as client:
        response = client.post("/link-analysis", json={"url": "http://localhost/"})

    assert response.status_code == 400, response.text
    data = response.json()
    assert "detail" in data

    _clear_overrides(app)


def test_fetch_timeout_returns_504(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fetch timeout returns HTTP 504."""
    app = _build_app()
    _override_service(app, _make_analysis_response())

    monkeypatch.setattr(
        "app.api.routes.link_analysis.validate_public_url",
        lambda url: _validated_url(),
    )

    from app.services.fetching import FetchTimeoutError

    async def fake_fetch_timeout(validated_url: Any) -> Any:
        raise FetchTimeoutError("timed out")

    monkeypatch.setattr(
        "app.api.routes.link_analysis.fetch_html", fake_fetch_timeout
    )

    with TestClient(app) as client:
        response = client.post("/link-analysis", json={"url": "https://example.com/"})

    assert response.status_code == 504, response.text

    _clear_overrides(app)


def test_fetch_error_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """A generic fetch error returns HTTP 502."""
    app = _build_app()
    _override_service(app, _make_analysis_response())

    monkeypatch.setattr(
        "app.api.routes.link_analysis.validate_public_url",
        lambda url: _validated_url(),
    )

    from app.services.fetching import FetchConnectionError

    async def fake_fetch_error(validated_url: Any) -> Any:
        raise FetchConnectionError("connection refused")

    monkeypatch.setattr(
        "app.api.routes.link_analysis.fetch_html", fake_fetch_error
    )

    with TestClient(app) as client:
        response = client.post("/link-analysis", json={"url": "https://example.com/"})

    assert response.status_code == 502, response.text

    _clear_overrides(app)


def test_llm_not_configured_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured LLM returns HTTP 503."""
    app = _build_app()

    class _FailingService:
        async def analyze_page(
            self, extracted_page: ExtractedPage
        ) -> AnalysisResponse:
            raise LLMNotConfiguredError("no key")

    app.dependency_overrides[get_analysis_service] = lambda: _FailingService()

    monkeypatch.setattr(
        "app.api.routes.link_analysis.validate_public_url",
        lambda url: _validated_url(),
    )

    async def fake_fetch_ok(validated_url: Any) -> Any:
        return _make_fetched_page()

    monkeypatch.setattr(
        "app.api.routes.link_analysis.fetch_html", fake_fetch_ok
    )

    with TestClient(app) as client:
        response = client.post("/link-analysis", json={"url": "https://example.com/"})

    assert response.status_code == 503, response.text

    _clear_overrides(app)


def test_llm_rate_limit_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """An LLM rate limit returns HTTP 503."""
    app = _build_app()

    class _FailingService:
        async def analyze_page(
            self, extracted_page: ExtractedPage
        ) -> AnalysisResponse:
            raise LLMRateLimitError("rate limited")

    app.dependency_overrides[get_analysis_service] = lambda: _FailingService()

    monkeypatch.setattr(
        "app.api.routes.link_analysis.validate_public_url",
        lambda url: _validated_url(),
    )

    async def fake_fetch_ok(validated_url: Any) -> Any:
        return _make_fetched_page()

    monkeypatch.setattr(
        "app.api.routes.link_analysis.fetch_html", fake_fetch_ok
    )

    with TestClient(app) as client:
        response = client.post("/link-analysis", json={"url": "https://example.com/"})

    assert response.status_code == 503, response.text

    _clear_overrides(app)


def test_health_endpoint_still_works() -> None:
    """The existing /health endpoint is unaffected."""
    app = _build_app()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
