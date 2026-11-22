"""Link analysis API endpoint.

Connects the existing URL validation, safe fetching, HTML extraction, and
analysis services into a single ``POST /link-analysis`` endpoint.
"""

from __future__ import annotations

import anyio
import logging
from functools import lru_cache
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.services.analysis import AnalysisError, AnalysisService
from app.services.extraction import extract_content
from app.services.fetching import (
    FetchError,
    FetchHTTPError,
    FetchTimeoutError,
    fetch_html,
)
from app.services.llm import (
    LLMClient,
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
)
from app.schemas.analysis import AnalysisResponse
from app.schemas.link_analysis import LinkAnalysisRequest, LinkAnalysisError
from app.utils.url_validation import URLValidationError, validate_public_url

router = APIRouter(tags=["link-analysis"])

logger = logging.getLogger(__name__)


@lru_cache()
def get_llm_client() -> LLMClient:
    """Return the shared LLM client (created once per process).

    Using :func:`functools.lru_cache` avoids creating a new client on every
    request, as required by the integration constraints.
    """
    return LLMClient()


@lru_cache()
def get_analysis_service() -> AnalysisService:
    """Return the shared analysis service (created once per process)."""
    return AnalysisService(get_llm_client())


@router.post(
    "/link-analysis",
    response_model=AnalysisResponse,
    responses={
        400: {"model": LinkAnalysisError, "description": "Invalid or unsafe URL."},
        404: {"model": LinkAnalysisError, "description": "Website not found."},
        502: {"model": LinkAnalysisError, "description": "Upstream fetch or LLM error."},
        503: {"model": LinkAnalysisError, "description": "LLM service unavailable."},
        504: {"model": LinkAnalysisError, "description": "Website fetch timed out."},
        500: {"model": LinkAnalysisError, "description": "Internal analysis error."},
    },
    summary="Analyze a website link",
)
async def link_analysis(
    request: LinkAnalysisRequest,
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisResponse:
    """Run the full link-analysis pipeline for a URL.

    The pipeline is:

    1. Validate the URL with SSRF protections.
    2. Fetch the website HTML safely.
    3. Extract readable content from the HTML.
    4. Run the analysis engine to produce all five sections.
    5. Return the structured analysis response.

    Internal exception details are never leaked to the client.
    """
    # ------------------------------------------------------------------
    # Step 1: Validate URL (blocking DNS, run in worker thread)
    # ------------------------------------------------------------------
    try:
        validated_url = await anyio.to_thread.run_sync(
            validate_public_url, request.url
        )
    except URLValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # ------------------------------------------------------------------
    # Step 2: Fetch website HTML
    # ------------------------------------------------------------------
    try:
        fetched_page = await fetch_html(validated_url)
    except FetchTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Website fetch timed out.",
        ) from exc
    except FetchHTTPError as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Website not found.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch website.",
        ) from exc
    except FetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch website.",
        ) from exc

    # ------------------------------------------------------------------
    # Step 3: Extract content
    # ------------------------------------------------------------------
    extracted_page = extract_content(fetched_page)

    # ------------------------------------------------------------------
    # Step 4: Run analysis
    # ------------------------------------------------------------------
    try:
        result = await service.analyze_page(extracted_page)
    except LLMNotConfiguredError as exc:
        logger.error(
            "LLM not configured: %s",
            exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service is not configured. Set LLM_API_KEY.",
        ) from exc
    except LLMRateLimitError as exc:
        logger.error(
            "LLM rate limit error: %s",
            exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service rate limit exceeded. Try again later.",
        ) from exc
    except LLMError as exc:
        logger.error(
            "LLM %s: %s",
            exc.__class__.__name__,
            str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM service error.",
        ) from exc
    except AnalysisError as exc:
        logger.error(
            "Analysis error: %s",
            str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Analysis failed.",
        ) from exc

    return result
