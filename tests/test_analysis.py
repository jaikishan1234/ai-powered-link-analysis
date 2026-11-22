"""Tests for the analysis engine.

All LLM access is mocked. The :class:`~app.services.llm.LLMClient.chat_json`
method is patched to return predefined JSON responses, so no real API key or
network access is required.
"""

from __future__ import annotations

import asyncio
import logging
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


def test_analysis_error_includes_section_name() -> None:
    """AnalysisError identifies which section failed validation."""
    page = _make_extracted_page()
    # Return JSON missing required 'seo_title' field for SEO section
    llm_return = {
        "competitors": [],
        "queries": [],
        "keywords": [],
        "meta_description": "A description.",
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
    with pytest.raises(AnalysisError, match="seo"):
        _run(service.analyze_page(page))


def test_schema_validation_logs_safe_errors_without_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Schema validation logs safe error details without input values."""
    page = _make_extracted_page()
    # Return JSON missing required 'seo_title' field for SEO section
    llm_return = {
        "competitors": [],
        "queries": [],
        "keywords": [],
        "meta_description": "A description.",
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

    with caplog.at_level(logging.ERROR, logger="app.services.analysis"):
        with pytest.raises(AnalysisError):
            _run(service.analyze_page(page))

    # Section name should be logged
    assert "seo" in caplog.text
    # Input values should not be logged (include_input=False excludes 'input' key)
    assert '"input"' not in caplog.text


def test_search_queries_prompt_requires_source_field() -> None:
    """Search queries prompt explicitly requires source field with allowed values."""
    captured_messages: list[dict] = []

    class _CapturingLLM:
        async def chat_json(
            self,
            messages: list[Mapping[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int | None = None,
        ) -> Any:
            captured_messages.extend(messages)
            user_content = messages[-1]["content"] if messages else ""
            if "search queries" in user_content.lower():
                return {
                    "queries": [
                        {
                            "query": "example query",
                            "platform": "Google",
                            "search_intent": "informational",
                            "source": "ai_generated",
                        }
                    ]
                }
            if "seo" in user_content.lower() and "improvement" in user_content.lower():
                return {
                    "keywords": ["example"],
                    "seo_title": "Example Title",
                    "meta_description": "Example description.",
                    "content_recommendations": ["Add more content."],
                }
            if "competitor" in user_content.lower():
                return {"competitors": [], "notes": "No competitors found."}
            if "service" in user_content.lower():
                return {"services": []}
            if "audience" in user_content.lower():
                return {
                    "customer_profiles": [],
                    "industries": [],
                    "needs": [],
                    "challenges": [],
                    "geography": [],
                }
            return {}

    page = _make_extracted_page()
    service = AnalysisService(_CapturingLLM())
    _run(service.analyze_page(page))

    user_messages = [m for m in captured_messages if m["role"] == "user"]
    assert len(user_messages) >= 5
    # Find the search queries user message specifically
    search_user_content = ""
    for m in user_messages:
        if "search queries" in m["content"].lower():
            search_user_content = m["content"]
            break
    assert search_user_content, "Search queries user message not found"

    # Prompt should require exactly five queries
    assert "exactly 5 target search queries" in search_user_content
    # Prompt should list all three allowed source values
    assert "'website'" in search_user_content
    assert "'ai_generated'" in search_user_content
    assert "'inferred'" in search_user_content
    # Prompt should explain source meanings
    assert "directly supported by website content" in search_user_content
    assert "AI recommendation without direct website evidence" in search_user_content
    assert "derived from available website context" in search_user_content


def test_max_tokens_passed_per_section() -> None:
    """Each analysis section passes its own max_tokens to chat_json."""
    captured_tokens: dict[str, int | None] = {}

    class _CapturingLLM:
        async def chat_json(
            self,
            messages: list[Mapping[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int | None = None,
        ) -> Any:
            # Find the section name from the system prompt or user content
            user_content = messages[-1]["content"] if messages else ""
            if "competitors" in user_content.lower() and "Identify" in user_content:
                section = "competitors"
            elif "search queries" in user_content.lower():
                section = "search_queries"
            elif "seo improvement" in user_content.lower():
                section = "seo"
            elif "services offered" in user_content.lower():
                section = "services"
            elif "target audience" in user_content.lower():
                section = "target_audience"
            else:
                section = "unknown"
            captured_tokens[section] = max_tokens

            if section == "competitors":
                return {"competitors": [], "notes": None}
            if section == "search_queries":
                return {"queries": []}
            if section == "seo":
                return {
                    "keywords": [],
                    "seo_title": "Example",
                    "meta_description": "Example site.",
                    "content_recommendations": [],
                }
            if section == "services":
                return {"services": []}
            if section == "target_audience":
                return {
                    "customer_profiles": [],
                    "industries": [],
                    "needs": [],
                    "challenges": [],
                    "geography": [],
                }
            return {}

    page = _make_extracted_page()
    service = AnalysisService(_CapturingLLM())
    _run(service.analyze_page(page))

    assert captured_tokens["competitors"] == 1200
    assert captured_tokens["search_queries"] == 1200
    assert captured_tokens["seo"] == 1000
    assert captured_tokens["services"] == 1500
    assert captured_tokens["target_audience"] == 1200


def test_max_tokens_truncated_json_still_raises() -> None:
    """Truncated JSON from max_tokens limit still produces AnalysisError."""

    class _TruncatingLLM:
        async def chat_json(
            self,
            messages: list[Mapping[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int | None = None,
        ) -> Any:
            # Return truncated/invalid JSON that will fail schema validation
            return '{"competitors": [{"name": "Incomplete'

    page = _make_extracted_page()
    service = AnalysisService(_TruncatingLLM())
    with pytest.raises(AnalysisError, match="schema validation"):
        _run(service.analyze_page(page))

