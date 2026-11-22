"""Pydantic response schemas for website analysis results.

All five analysis sections are represented here, along with a top-level
:class:`AnalysisResponse` that combines them. Every field is designed to
distinguish website-supported information from AI-generated suggestions or
inferences via the ``source`` field where applicable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Allowed provenance values for analysis items.
DataSource = Literal["website", "ai_generated", "inferred"]


# ---------------------------------------------------------------------------
# Section 1: Competitor analysis
# ---------------------------------------------------------------------------


class CompetitorInfo(BaseModel):
    """A single competitor identified for the analyzed website."""

    name: str = Field(description="Name of the competitor.")
    website: str | None = Field(
        default=None, description="Competitor's website URL, if known."
    )
    description: str = Field(description="Brief description of the competitor.")
    why_relevant: str = Field(
        description="Why this competitor is relevant to the analyzed website."
    )
    source: DataSource = Field(
        default="ai_generated",
        description=(
            "Whether this competitor came from the website content "
            "or was AI-generated."
        ),
    )


class CompetitorAnalysis(BaseModel):
    """Competitor analysis section."""

    competitors: list[CompetitorInfo] = Field(
        default_factory=list, description="List of competitors."
    )
    notes: str | None = Field(
        default=None,
        description="Additional notes about the competitor analysis.",
    )


# ---------------------------------------------------------------------------
# Section 2: Top 5 target search queries
# ---------------------------------------------------------------------------


class SearchQuery(BaseModel):
    """A single target search query."""

    query: str = Field(description="The search query phrase.")
    platform: str = Field(
        description=(
            "Platform where this query is relevant "
            "(e.g. Google, Bing, LinkedIn)."
        )
    )
    search_intent: Literal[
        "informational", "navigational", "commercial", "transactional"
    ] = Field(description="The search intent behind this query.")
    source: DataSource = Field(
        default="ai_generated",
        description=(
            "Whether this query came from the website content "
            "or was AI-generated."
        ),
    )


class SearchQueries(BaseModel):
    """Top 5 target search queries section."""

    queries: list[SearchQuery] = Field(
        default_factory=list, description="List of target search queries."
    )


# ---------------------------------------------------------------------------
# Section 3: SEO improvements
# ---------------------------------------------------------------------------


class SEOImprovements(BaseModel):
    """SEO improvement recommendations."""

    keywords: list[str] = Field(
        default_factory=list, description="Recommended target keywords."
    )
    seo_title: str = Field(description="Recommended SEO page title.")
    meta_description: str = Field(description="Recommended meta description.")
    content_recommendations: list[str] = Field(
        default_factory=list,
        description="Content improvement recommendations.",
    )


# ---------------------------------------------------------------------------
# Section 4: Top 5 services
# ---------------------------------------------------------------------------


class ServiceInfo(BaseModel):
    """A single service offered by the website."""

    name: str = Field(description="Name of the service.")
    description: str = Field(description="Description of the service.")
    industries: list[str] = Field(
        default_factory=list, description="Industries this service applies to."
    )
    target_customers: list[str] = Field(
        default_factory=list,
        description="Target customer types for this service.",
    )
    benefits: list[str] = Field(
        default_factory=list, description="Key benefits of this service."
    )
    source: DataSource = Field(
        default="inferred",
        description=(
            "Whether this service came from the website content "
            "or was inferred."
        ),
    )


class ServicesAnalysis(BaseModel):
    """Top 5 services section."""

    services: list[ServiceInfo] = Field(
        default_factory=list, description="List of services."
    )


# ---------------------------------------------------------------------------
# Section 5: Target audience
# ---------------------------------------------------------------------------


class CustomerProfile(BaseModel):
    """A single customer profile for the target audience."""

    name: str = Field(description="Name or label for this customer profile.")
    description: str = Field(description="Description of this customer profile.")
    demographics: list[str] = Field(
        default_factory=list, description="Demographic characteristics."
    )


class TargetAudience(BaseModel):
    """Target audience analysis section."""

    customer_profiles: list[CustomerProfile] = Field(
        default_factory=list, description="Customer profiles."
    )
    industries: list[str] = Field(
        default_factory=list, description="Relevant industries."
    )
    needs: list[str] = Field(
        default_factory=list, description="Customer needs."
    )
    challenges: list[str] = Field(
        default_factory=list, description="Customer challenges."
    )
    geography: list[str] = Field(
        default_factory=list, description="Geographic focus areas."
    )


# ---------------------------------------------------------------------------
# Top-level analysis response
# ---------------------------------------------------------------------------


class AnalysisResponse(BaseModel):
    """Complete analysis response for an extracted website page."""

    url: str = Field(description="URL of the analyzed page.")
    website_title: str | None = Field(
        default=None, description="Title of the website, if available."
    )
    website_description: str | None = Field(
        default=None,
        description="Meta description of the website, if available.",
    )
    competitor_analysis: CompetitorAnalysis = Field(
        default_factory=CompetitorAnalysis
    )
    search_queries: SearchQueries = Field(default_factory=SearchQueries)
    seo_improvements: SEOImprovements = Field(default_factory=SEOImprovements)
    services: ServicesAnalysis = Field(default_factory=ServicesAnalysis)
    target_audience: TargetAudience = Field(default_factory=TargetAudience)
    analysis_notes: str | None = Field(
        default=None, description="General notes about the analysis."
    )
