"""Tests for the analysis engine.

All LLM access is mocked. The :class:`~app.services.llm.LLMClient.chat_json`
method is patched to return predefined JSON responses, so no real API key or
network access is required.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.analysis import (
    AnalysisResponse,
    CompetitorAnalysis,
    CompetitorInfo,
    CustomerProfile,
    SEOImprovements,
    SearchQueries,
    SearchQuery,
    ServiceInfo,
    ServicesAnalysis,
    TargetAudience,
)
from app.services.analysis import AnalysisError, AnalysisService
from app.services.llm import LLMInvalidResponseError
from app.services.extraction import ExtractedPage, Heading


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_extracted_page(**overrides: Any) -> ExtractedPage:
    defaults: dict[str, Any] = {
        "url": "https://example.com/",
        "title": "Example Page",
        "description": "An example website.",
        "headings": (
            Heading(level=1, text="Welcome"),
            Heading(level=2, text="Features"),
        ),
        "text": "Welcome to Example Page. We offer great features.",
        "truncated": False,
    }
    defaults.update(overrides)
    return ExtractedPage(**defaults)


def _make_mock_llm(json_return: Any) -> Any:
    """Return a mock LLM client whose chat_json returns json_return."""

    class _MockLLM:
        def __init__(self) -> None:
            self.api_key = "test-key"
            self.base_url = "https://example.com/v1"
            self.model = "test-model"
            self.timeout_seconds = 5.0

        @property
        def is_configured(self) -> bool:
            return True

        async def chat_json(
            self,
            messages: list[Mapping[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int | None = None,
        ) -> Any:
            return json_return

    return _MockLLM()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_competitor_info_schema_valid() -> None:
    c = CompetitorInfo(
        name="Acme Corp",
        website="https://acme.example.com",
        description="A competitor.",
        why_relevant="Same industry.",
        source="ai_generated",
    )
    assert c.name == "Acme Corp"
    assert c.source == "ai_generated"


def test_competitor_info_source_website() -> None:
    c = CompetitorInfo(
        name="Acme Corp",
        description="From website.",
        why_relevant="Mentioned on page.",
        source="website",
    )
    assert c.source == "website"


def test_search_query_valid_intents() -> None:
    for intent in ("informational", "navigational", "commercial", "transactional"):
        q = SearchQuery(
            query="test query",
            platform="Google",
            search_intent=intent,
        )
        assert q.search_intent == intent


def test_search_query_invalid_intent() -> None:
    with pytest.raises(ValidationError):
        SearchQuery(query="test", platform="Google", search_intent="bogus")


def test_seo_improvements_schema() -> None:
    s = SEOImprovements(
        keywords=["widget", "gadget"],
        seo_title="Best Widgets | Example",
        meta_description="Buy widgets at Example.",
        content_recommendations=["Add more product details."],
    )
    assert s.keywords == ["widget", "gadget"]
    assert s.seo_title == "Best Widgets | Example"


def test_service_info_schema() -> None:
    s = ServiceInfo(
        name="Consulting",
        description="Expert consulting.",
        industries=["Tech"],
        target_customers=["Startups"],
        benefits=["Faster delivery"],
        source="website",
    )
    assert s.source == "website"


def test_target_audience_schema() -> None:
    a = TargetAudience(
        customer_profiles=[
            CustomerProfile(
                name="Small business owner",
                description="Runs a small shop.",
                demographics=["25-55", "Local"],
            )
        ],
        industries=["Retail"],
        needs=["Affordable tools"],
        challenges=["Limited budget"],
        geography=["North America"],
    )
    assert len(a.customer_profiles) == 1
    assert a.geography == ["North America"]


def test_analysis_response_schema() -> None:
    r = AnalysisResponse(
        url="https://example.com/",
        website_title="Example Page",
        website_description="An example website.",
        competitor_analysis=CompetitorAnalysis(competitors=[], notes=None),
        search_queries=SearchQueries(queries=[]),
        seo_improvements=SEOImprovements(
            keywords=[],
            seo_title="Example",
            meta_description="Example site.",
            content_recommendations=[],
        ),
        services=ServicesAnalysis(services=[]),
        target_audience=TargetAudience(),
    )
    assert r.url == "https://example.com/"
    assert r.competitor_analysis.competitors == []


# ---------------------------------------------------------------------------
# AnalysisService tests
# ---------------------------------------------------------------------------


def test_analyze_page_all_sections() -> None:
    page = _make_extracted_page()
    llm_return = {
        "competitors": [],
        "notes": "No competitors found on page.",
        "queries": [
            {
                "query": "example widgets",
                "platform": "Google",
                "search_intent": "informational",
                "source": "ai_generated",
            }
        ],
        "keywords": ["widgets"],
        "seo_title": "Example Widgets",
        "meta_description": "Buy widgets.",
        "content_recommendations": ["Add more content."],
        "services": [],
        "customer_profiles": [],
        "industries": ["Tech"],
        "needs": ["Widgets"],
        "challenges": ["Competition"],
        "geography": ["Global"],
    }
    mock_llm = _make_mock_llm(llm_return)

    service = AnalysisService(mock_llm)
    result = _run(service.analyze_page(page))

    assert result.url == "https://example.com/"
    assert result.website_title == "Example Page"
    assert result.website_description == "An example website."
    assert len(result.search_queries.queries) == 1
    assert result.search_queries.queries[0].query == "example widgets"
    assert result.seo_improvements.seo_title == "Example Widgets"
    assert result.analysis_notes is None


def test_analyze_page_missing_info_notes() -> None:
    page = _make_extracted_page(title=None, description=None, text="")
    llm_return = {
        "competitors": [],
        "queries": [],
        "keywords": [],
        "seo_title": "",
        "meta_description": "",
        "content_recommendations": [],
        "services": [],
        "customer_profiles": [],
        "industries": [],
        "needs": [],
        "challenges": [],
        "geography": [],
    }
    mock_llm = _make_mock_llm(llm_return)
    service = AnalysisService(mock_llm)
    result = _run(service.analyze_page(page))

    assert result.analysis_notes is not None
    assert "No page title" in result.analysis_notes
    assert "No meta description" in result.analysis_notes
    assert "No extractable text" in result.analysis_notes


def test_analyze_page_truncated_notes() -> None:
    page = _make_extracted_page(truncated=True)
    llm_return = {
        "competitors": [],
        "queries": [],
        "keywords": [],
        "seo_title": "",
        "meta_description": "",
        "content_recommendations": [],
        "services": [],
        "customer_profiles": [],
        "industries": [],
        "needs": [],
        "challenges": [],
        "geography": [],
    }
    mock_llm = _make_mock_llm(llm_return)
    service = AnalysisService(mock_llm)
    result = _run(service.analyze_page(page))

    assert result.analysis_notes is not None
    assert "truncated" in result.analysis_notes.lower()


def test_analyze_page_invalid_llm_response_raises() -> None:
    """LLM returns valid JSON that doesn't match the schema -> AnalysisError."""
    page = _make_extracted_page()
    # Return JSON missing required fields for CompetitorAnalysis
    mock_llm = _make_mock_llm({"not": "the right shape"})
    service = AnalysisService(mock_llm)
    with pytest.raises(AnalysisError, match="schema validation"):
        _run(service.analyze_page(page))


def test_analyze_page_llm_error_propagates() -> None:
    """LLM-level errors propagate from the client."""
    page = _make_extracted_page()

    class _FailingLLM:
        async def chat_json(
            self,
            messages: list[Mapping[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int | None = None,
        ) -> Any:
            from app.services.llm import LLMInvalidResponseError

            raise LLMInvalidResponseError("bad response")

    service = AnalysisService(_FailingLLM())
    with pytest.raises(LLMInvalidResponseError, match="bad response"):
        _run(service.analyze_page(page))


def test_build_context_includes_page_data() -> None:
    page = _make_extracted_page()
    service = AnalysisService(_make_mock_llm({}))
    context = service._build_context(page)

    assert "https://example.com/" in context
    assert "Example Page" in context
    assert "An example website." in context
    assert "Welcome" in context
    assert "Features" in context
    assert "great features" in context


def test_system_prompt_warns_about_untrusted_data() -> None:
    prompt = AnalysisService._system_prompt()
    assert "UNTRUSTED DATA" in prompt
    assert "instructions" in prompt.lower()
    assert "do not execute" in prompt.lower()

