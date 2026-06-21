"""
Async worker pool for NSFW inference.

Architecture
------------
- N worker *coroutines* run concurrently (good for async I/O wait)
- Model inference runs in a ThreadPoolExecutor to avoid blocking the
  event loop (TF/numpy are CPU-bound and not async-friendly)
- A single model instance is shared across workers (thread-safe for
  inference when session is created with the right config)
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from app.queue import JobQueue, QueueItem
from app.model import NSFWModelRunner
from app.config import NSFW_THRESHOLD, WEBHOOK_MAX_RETRIES
from prometheus_client import Counter as PromCounter

logger = logging.getLogger("nsfw.worker")

WEBHOOK_COUNT = PromCounter(
    "nsfw_webhooks_total",
    "Webhook delivery outcomes",
    ["status"],
)


class NSFWWorker:
    def __init__(
        self,
        queue: JobQueue,
        weights_path: Optional[str] = None,
        num_workers: int = 2,
        batch_size: int = 1,        # reserved for future batched inference
        executor_threads: int = 4,
    ):
        self._queue       = queue
        self._weights     = weights_path
        self._num_workers = num_workers
        self._executor    = ThreadPoolExecutor(max_workers=executor_threads,
                                               thread_name_prefix="nsfw-inf")
        self._tasks: list[asyncio.Task] = []
        self._runner: Optional[NSFWModelRunner] = None
        self.model_ready  = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self):
        """Load model (blocking) then spawn worker coroutines."""
        loop = asyncio.get_running_loop()

        logger.info("Loading NSFW model weights from '%s'…", self._weights)
        try:
            self._runner = await loop.run_in_executor(
                self._executor,
                NSFWModelRunner,
                self._weights,
            )
            self.model_ready = True
            logger.info("✅  Model loaded successfully")
        except Exception as exc:
            logger.warning(
                "⚠️  Failed to load model. "
                "Worker will use MOCK inference. "
                "Error: %s", exc
            )
            self._runner = NSFWModelRunner(weights_path=None)  # mock mode
            self.model_ready = False

        for i in range(self._num_workers):
            task = asyncio.create_task(self._worker_loop(worker_id=i))
            self._tasks.append(task)

        logger.info("🚀  %d worker(s) running", self._num_workers)

    async def stop(self):
        """Cancel all worker tasks and shut down the thread pool."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._executor.shutdown(wait=False)
        logger.info("Worker pool stopped")

    # ── Worker loop ───────────────────────────────────────────────────────────

    async def _worker_loop(self, worker_id: int):
        logger.info("Worker-%d ready", worker_id)
        loop = asyncio.get_running_loop()

        while True:
            try:
                item: QueueItem = await self._queue.dequeue()
            except asyncio.CancelledError:
                logger.info("Worker-%d shutting down", worker_id)
                break

            self._queue.mark_processing(item.job_id)
            logger.debug("Worker-%d processing job %s", worker_id, item.job_id)

            try:
                nsfw, sfw, elapsed = await loop.run_in_executor(
                    self._executor,
                    self._runner.predict,
                    item.image_bytes,
                )
                self._queue.mark_done(
                    item.job_id,
                    nsfw_score=nsfw,
                    sfw_score=sfw,
                    elapsed_ms=elapsed,
                )
                logger.debug(
                    "Job %s done | nsfw=%.4f sfw=%.4f elapsed=%.1f ms",
                    item.job_id, nsfw, sfw, elapsed,
                )
                
                if item.webhook_url:
                    asyncio.create_task(self._send_webhook(
                        item.webhook_url,
                        {
                            "job_id": item.job_id,
                            "status": "done",
                            "nsfw_score": round(nsfw, 6),
                            "sfw_score": round(sfw, 6),
                            "label": "nsfw" if nsfw > NSFW_THRESHOLD else "sfw",
                            "elapsed_ms": round(elapsed, 2)
                        }
                    ))
            except Exception as exc:
                logger.exception("Job %s failed: %s", item.job_id, exc)
                self._queue.mark_failed(item.job_id, error=str(exc))
                
                if item.webhook_url:
                    asyncio.create_task(self._send_webhook(
                        item.webhook_url,
                        {
                            "job_id": item.job_id,
                            "status": "failed",
                            "error": str(exc)
                        }
                    ))

    async def predict_direct(self, image_bytes: bytes) -> tuple[float, float, float]:
        """Run straight-through inference bypassing the queue."""
        if not self._runner:
            raise RuntimeError("Model not loaded yet")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._runner.predict,
            image_bytes,
        )

    async def _send_webhook(self, url: str, payload: dict):
        """Send an HTTP POST to the specified webhook URL with retry."""
        import httpx

        job_id = payload.get("job_id", "unknown")
        for attempt in range(1, WEBHOOK_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                logger.debug(
                    "Webhook sent to %s for job %s (attempt %d)",
                    url, job_id, attempt,
                )
                WEBHOOK_COUNT.labels(status="success").inc()
                return  # success — stop retrying
            except Exception as exc:
                if attempt < WEBHOOK_MAX_RETRIES:
                    delay = 2 ** attempt  # 2s, 4s, 8s, ...
                    logger.warning(
                        "Webhook attempt %d/%d failed for %s: %s — retrying in %ds",
                        attempt, WEBHOOK_MAX_RETRIES, url, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Webhook delivery failed after %d attempts for %s: %s",
                        WEBHOOK_MAX_RETRIES, url, exc,
                    )
                    WEBHOOK_COUNT.labels(status="failed").inc()
