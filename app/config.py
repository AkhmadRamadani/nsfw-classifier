"""
Centralized configuration for the NSFW Detection API.

All tuneable constants live here so they can be overridden via environment
variables or adjusted in one place.
"""

from __future__ import annotations

import os

# ── Detection ────────────────────────────────────────────────────────────────
# Score threshold above which an image is labelled "nsfw".
NSFW_THRESHOLD: float = float(os.getenv("NSFW_THRESHOLD", "0.8"))

# Maximum upload size per image (bytes).
MAX_IMAGE_BYTES: int = int(os.getenv("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))  # 10 MB

# Allowed MIME types for uploaded images.
ALLOWED_CONTENT_TYPES: set[str] = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

# ── Queue / Workers ──────────────────────────────────────────────────────────
# NOTE: The default queue and result store are IN-MEMORY. All pending jobs and
# completed results are lost on restart. For production deployments that need
# persistence across restarts, swap the ResultStore backend for Redis or a
# database. See the ResultStore class in app/queue.py for the interface to
# implement.
QUEUE_MAXSIZE: int = int(os.getenv("QUEUE_MAXSIZE", "500"))
RESULT_TTL_SECONDS: int = int(os.getenv("RESULT_TTL_SECONDS", "600"))

NUM_WORKERS: int = int(os.getenv("NUM_WORKERS", "2"))
EXECUTOR_THREADS: int = int(os.getenv("EXECUTOR_THREADS", "4"))
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "1"))

# ── Rate Limiting ────────────────────────────────────────────────────────────
# slowapi rate limit string (e.g. "60/minute", "10/second").
RATE_LIMIT: str = os.getenv("RATE_LIMIT", "60/minute")

# ── Webhooks ─────────────────────────────────────────────────────────────────
# Max retry attempts for webhook delivery (exponential backoff: 2s, 4s, 8s, ...).
WEBHOOK_MAX_RETRIES: int = int(os.getenv("WEBHOOK_MAX_RETRIES", "3"))

# ── Model ────────────────────────────────────────────────────────────────────
MODEL_NAME: str = os.getenv("MODEL_NAME", "Falconsai/nsfw_image_detection")
WEIGHTS_PATH: str = os.getenv("WEIGHTS_PATH", "models/open_nsfw-weights.npy")
