"""
NSFW Detection API
Production-ready FastAPI service with async queue processing.
"""

import uuid
import time
import asyncio
import ipaddress
import logging
import socket
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.schemas import (
    JobResponse, JobResultResponse,
    BatchRequest, BatchJobResponse, HealthResponse,
    NSFWResult,
)
from app.queue import JobQueue, JobState
from app.worker import NSFWWorker
from app.config import (
    NSFW_THRESHOLD, MAX_IMAGE_BYTES, ALLOWED_CONTENT_TYPES,
    QUEUE_MAXSIZE, NUM_WORKERS, BATCH_SIZE, RATE_LIMIT,
)
from app.logging_config import setup_logging

# ── Logging ──────────────────────────────────────────────────────────────────
setup_logging()
logger = logging.getLogger("nsfw.api")

# ── Globals ──────────────────────────────────────────────────────────────────
job_queue: JobQueue = None
worker: NSFWWorker = None
limiter = Limiter(key_func=get_remote_address)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start worker pool on startup, shut down cleanly on exit."""
    global job_queue, worker

    job_queue = JobQueue(maxsize=QUEUE_MAXSIZE)
    worker = NSFWWorker(
        queue=job_queue,
        weights_path="models/open_nsfw-weights.npy",
        num_workers=NUM_WORKERS,
        batch_size=BATCH_SIZE,
    )
    await worker.start()
    logger.info("NSFW worker pool started")

    yield

    await worker.stop()
    logger.info("NSFW worker pool stopped")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="NSFW Detection API",
    description=(
        "Production-ready NSFW content detection using Yahoo's Open NSFW model. "
        "Supports single-image and batch processing via an async job queue."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Rate Limiting ────────────────────────────────────────────────────────────
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Liveness + readiness probe."""
    stats = job_queue.stats() if job_queue else {}
    return HealthResponse(
        status="ok",
        model_loaded=worker.model_ready if worker else False,
        queue_depth=stats.get("pending", 0),
        workers=stats.get("workers", 0),
    )


# ── Single Image ──────────────────────────────────────────────────────────────
@app.post("/detect", response_model=JobResponse, tags=["Detection"])
@limiter.limit(RATE_LIMIT)
async def detect_image(
    request: Request,
    file: UploadFile = File(...),
    webhook_url: Optional[str] = Form(None)
):
    """
    Submit a single image for NSFW analysis.

    Returns a **job_id** immediately. Poll `/jobs/{job_id}` for the result.
    Accepts: image/jpeg, image/png, image/webp
    """
    _validate_content_type(file.content_type)
    _validate_webhook_url(webhook_url)

    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:  # size guard
        raise HTTPException(413, "Image must be ≤ 10 MB")

    job_id = str(uuid.uuid4())
    try:
        await job_queue.enqueue(job_id, image_bytes, webhook_url=webhook_url)
    except asyncio.QueueFull:
        raise HTTPException(503, "Queue is full — retry later")

    return JobResponse(job_id=job_id, status=JobState.PENDING)


# ── Single Image (Synchronous) ────────────────────────────────────────────────
@app.post("/detect/direct", response_model=NSFWResult, tags=["Detection"])
@limiter.limit(RATE_LIMIT)
async def detect_direct(request: Request, file: UploadFile = File(...)):
    """
    Submit a single image and wait for the result synchronously.
    
    Bypasses the queue system to give an immediate response.
    Accepts: image/jpeg, image/png, image/webp
    """
    _validate_content_type(file.content_type)

    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:  # size guard
        raise HTTPException(413, "Image must be ≤ 10 MB")

    try:
        nsfw, sfw, elapsed = await worker.predict_direct(image_bytes)
    except Exception as e:
        raise HTTPException(500, f"Inference failed: {str(e)}")

    label = "nsfw" if nsfw > NSFW_THRESHOLD else "sfw"
    return NSFWResult(
        nsfw_score=round(nsfw, 6),
        sfw_score=round(sfw, 6),
        label=label,
        elapsed_ms=round(elapsed, 2)
    )


# ── Batch ─────────────────────────────────────────────────────────────────────
@app.post("/detect/batch", response_model=BatchJobResponse, tags=["Detection"])
@limiter.limit(RATE_LIMIT)
async def detect_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    webhook_url: Optional[str] = Form(None)
):
    """
    Submit up to 32 images in one request.

    Returns a list of job IDs in the same order as the uploaded files.
    """
    if len(files) > 32:
        raise HTTPException(400, "Maximum 32 images per batch")

    _validate_webhook_url(webhook_url)

    job_ids = []
    for file in files:
        _validate_content_type(file.content_type)
        image_bytes = await file.read()
        job_id = str(uuid.uuid4())
        try:
            await job_queue.enqueue(job_id, image_bytes, webhook_url=webhook_url)
        except asyncio.QueueFull:
            raise HTTPException(503, "Queue is full — retry later")
        job_ids.append(job_id)

    return BatchJobResponse(job_ids=job_ids, count=len(job_ids))


# ── URL-based detection ───────────────────────────────────────────────────────
@app.post("/detect/url", response_model=JobResponse, tags=["Detection"])
@limiter.limit(RATE_LIMIT)
async def detect_url(request: Request, body: BatchRequest):
    """
    Submit an image URL for NSFW analysis.

    The worker fetches the URL server-side (avoids CORS issues).
    """

    _validate_url_not_internal(str(body.url))
    _validate_webhook_url(body.webhook_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(str(body.url))
            resp.raise_for_status()
    except Exception as e:
        raise HTTPException(400, f"Could not fetch URL: {e}")

    content_type = resp.headers.get("content-type", "")
    if not any(t in content_type for t in ("jpeg", "png", "webp", "image")):
        raise HTTPException(415, "URL must point to an image")

    job_id = str(uuid.uuid4())
    try:
        await job_queue.enqueue(job_id, resp.content, webhook_url=body.webhook_url)
    except asyncio.QueueFull:
        raise HTTPException(503, "Queue is full — retry later")

    return JobResponse(job_id=job_id, status=JobState.PENDING)


# ── Job Status ────────────────────────────────────────────────────────────────
@app.get("/jobs/{job_id}", response_model=JobResultResponse, tags=["Jobs"])
async def get_job(job_id: str):
    """
    Poll a job for its current status and result.

    **States**: `pending` → `processing` → `done` | `failed`

    Result fields (when done):
    - `nsfw_score`  – probability 0.0–1.0 that the image is NSFW
    - `sfw_score`   – probability 0.0–1.0 that the image is safe
    - `label`       – `"nsfw"` if nsfw_score > 0.8, else `"sfw"`
    - `elapsed_ms`  – inference time in milliseconds
    """
    result = job_queue.get_result(job_id)
    if result is None:
        raise HTTPException(404, f"Job '{job_id}' not found")
    return result


@app.delete("/jobs/{job_id}", tags=["Jobs"])
async def cancel_job(job_id: str):
    """Cancel a pending job (has no effect if already processing)."""
    removed = job_queue.cancel(job_id)
    if not removed:
        raise HTTPException(404, f"Job '{job_id}' not found or already started")
    return {"cancelled": job_id}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_content_type(ct: Optional[str]):
    if ct not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            415,
            f"Unsupported media type '{ct}'. Allowed: {', '.join(ALLOWED_CONTENT_TYPES)}"
        )


def _validate_url_not_internal(url: str) -> None:
    """
    Block URLs that resolve to private, loopback, link-local, or metadata IPs.

    Mitigates Server-Side Request Forgery (SSRF) attacks where an attacker
    could probe internal services (e.g. http://169.254.169.254/latest/meta-data/,
    http://localhost:6379/, http://10.0.0.1:8080/).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "URL must use http or https scheme")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(400, "URL has no hostname")

    # Resolve hostname to IP(s); blocks before any HTTP request is made.
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
    except socket.gaierror:
        raise HTTPException(400, f"Could not resolve hostname: {hostname}")

    for family, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise HTTPException(
                400,
                "URL resolves to a private or internal address and is not allowed",
            )
        # Block the AWS/GCP/Azure metadata endpoints explicitly
        # (they sit on a public-looking IP in some clouds)
        if str(ip) in ("169.254.169.254", "fd00:ec2::254"):
            raise HTTPException(400, "URL resolves to a cloud metadata endpoint")


def _validate_webhook_url(url: Optional[str]) -> None:
    """Validate a webhook URL if one was provided."""
    if url is not None:
        _validate_url_not_internal(url)
