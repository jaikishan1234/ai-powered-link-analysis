"""Schemas for the health-check endpoint."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Payload returned by ``GET /health``."""

    status: Literal["ok"] = Field(default="ok", description="Service status.")
    service: str = Field(description="Service name.")
    version: str = Field(description="Application version.")
