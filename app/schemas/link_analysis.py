"""Pydantic request schemas for the link analysis endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LinkAnalysisRequest(BaseModel):
    """Request body for ``POST /link-analysis``."""

    url: str = Field(
        description="The URL of the website to analyze.",
        examples=["https://example.com/"],
    )


class LinkAnalysisError(BaseModel):
    """Structured error response for link analysis failures."""

    error: str = Field(description="A human-readable error message.")
    code: str = Field(description="A machine-readable error code.")
