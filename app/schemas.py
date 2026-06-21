"""
Pydantic schemas for the NSFW Detection API.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, HttpUrl, Field


class JobState(str, Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    DONE       = "done"
    FAILED     = "failed"


# ── Responses ─────────────────────────────────────────────────────────────────

class JobResponse(BaseModel):
    job_id: str = Field(..., description="Unique job identifier")
    status: JobState


class BatchJobResponse(BaseModel):
    job_ids: list[str] = Field(..., description="Job IDs in submission order")
    count: int


class NSFWResult(BaseModel):
    nsfw_score: float  = Field(..., ge=0.0, le=1.0)
    sfw_score:  float  = Field(..., ge=0.0, le=1.0)
    label:      str    = Field(..., description="'nsfw' | 'sfw'")
    elapsed_ms: float  = Field(..., description="Inference time in ms")


class JobResultResponse(BaseModel):
    job_id:    str
    status:    JobState
    result:    Optional[NSFWResult] = None
    error:     Optional[str]        = None
    queued_at: Optional[float]      = None  # unix timestamp
    done_at:   Optional[float]      = None


class HealthResponse(BaseModel):
    status:       str
    model_loaded: bool
    inference_ok: bool  = False
    queue_depth:  int
    workers:      int


# ── Requests ──────────────────────────────────────────────────────────────────

class BatchRequest(BaseModel):
    url: HttpUrl = Field(..., description="Publicly accessible image URL")
    webhook_url: Optional[str] = Field(None, description="Optional webhook URL to receive results")
