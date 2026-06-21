"""
Async job queue backed by an in-memory result store.

Features
--------
- asyncio.Queue for back-pressure (maxsize caps pending jobs)
- Thread-safe result store with TTL expiry (default 10 min)
- Job cancellation for items still in the pending queue
- Queue stats for the /health endpoint

WARNING: This is an in-memory implementation. All data is lost on process
restart. For production use with persistence requirements, replace
ResultStore with a Redis or database-backed implementation that satisfies
the same interface (set, get, delete, purge_expired, __len__).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Optional

from app.schemas import JobState, JobResultResponse, NSFWResult
from app.config import NSFW_THRESHOLD


@dataclass
class QueueItem:
    job_id:      str
    image_bytes: bytes
    webhook_url: Optional[str] = None
    queued_at:   float = field(default_factory=time.time)
    cancelled:   bool  = False


class ResultStore:
    """Thread-safe key-value store with TTL expiry."""

    def __init__(self, ttl_seconds: int = 600):
        self._store: Dict[str, dict] = {}
        self._lock = Lock()
        self._ttl = ttl_seconds

    def set(self, job_id: str, value: dict):
        with self._lock:
            self._store[job_id] = {"data": value, "expires": time.time() + self._ttl}

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            entry = self._store.get(job_id)
            if entry is None:
                return None
            if time.time() > entry["expires"]:
                del self._store[job_id]
                return None
            return entry["data"]

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._store.pop(job_id, None) is not None

    def purge_expired(self):
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._store.items() if now > v["expires"]]
            for k in expired:
                del self._store[k]

    def __len__(self):
        with self._lock:
            return len(self._store)


class JobQueue:
    """
    High-level job queue that wraps asyncio.Queue and a ResultStore.

    Lifecycle of a job
    ------------------
    1. enqueue()  → stored in result store as PENDING, placed on the queue
    2. Worker dequeues → marks PROCESSING
    3. Worker finishes → marks DONE or FAILED with result/error
    """

    def __init__(self, maxsize: int = 500, result_ttl: int = 600):
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=maxsize)
        self._results = ResultStore(ttl_seconds=result_ttl)
        self._pending_ids: Dict[str, QueueItem] = {}  # for cancellation
        self._lock = Lock()

    # ── Producers ─────────────────────────────────────────────────────────────

    async def enqueue(self, job_id: str, image_bytes: bytes, webhook_url: Optional[str] = None):
        """
        Add a job to the queue. Raises asyncio.QueueFull if at capacity.
        The initial result record is written synchronously before queuing.
        """
        item = QueueItem(job_id=job_id, image_bytes=image_bytes, webhook_url=webhook_url)

        # Write initial state before touching the queue (avoids a race where
        # a fast worker could try to update state before it's created).
        self._results.set(job_id, {
            "job_id":    job_id,
            "status":    JobState.PENDING,
            "result":    None,
            "error":     None,
            "queued_at": item.queued_at,
            "done_at":   None,
        })

        with self._lock:
            self._pending_ids[job_id] = item

        # Non-blocking put — raises QueueFull immediately if at capacity
        self._queue.put_nowait(item)

    # ── Consumers (called by workers) ─────────────────────────────────────────

    async def dequeue(self) -> QueueItem:
        """Block until an item is available; skips cancelled items."""
        while True:
            item = await self._queue.get()
            with self._lock:
                self._pending_ids.pop(item.job_id, None)
            if item.cancelled:
                self._queue.task_done()
                continue
            return item

    def mark_processing(self, job_id: str):
        data = self._results.get(job_id) or {}
        data["status"] = JobState.PROCESSING
        self._results.set(job_id, data)

    def mark_done(
        self,
        job_id: str,
        nsfw_score: float,
        sfw_score: float,
        elapsed_ms: float,
    ):
        label = "nsfw" if nsfw_score > NSFW_THRESHOLD else "sfw"
        data = self._results.get(job_id) or {}
        data.update({
            "status":  JobState.DONE,
            "done_at": time.time(),
            "result": {
                "nsfw_score": round(nsfw_score, 6),
                "sfw_score":  round(sfw_score, 6),
                "label":      label,
                "elapsed_ms": round(elapsed_ms, 2),
            },
        })
        self._results.set(job_id, data)
        self._queue.task_done()

    def mark_failed(self, job_id: str, error: str):
        data = self._results.get(job_id) or {}
        data.update({
            "status":  JobState.FAILED,
            "done_at": time.time(),
            "error":   error,
        })
        self._results.set(job_id, data)
        self._queue.task_done()

    # ── Readers ───────────────────────────────────────────────────────────────

    def get_result(self, job_id: str) -> Optional[JobResultResponse]:
        data = self._results.get(job_id)
        if data is None:
            return None

        result_data = data.get("result")
        nsfw_result = NSFWResult(**result_data) if result_data else None

        return JobResultResponse(
            job_id=data["job_id"],
            status=data["status"],
            result=nsfw_result,
            error=data.get("error"),
            queued_at=data.get("queued_at"),
            done_at=data.get("done_at"),
        )

    def cancel(self, job_id: str) -> bool:
        """Mark a pending item as cancelled. Returns False if not found."""
        with self._lock:
            item = self._pending_ids.get(job_id)
            if item is None:
                return False
            item.cancelled = True
            del self._pending_ids[job_id]
        self._results.delete(job_id)
        return True

    def stats(self) -> dict:
        return {
            "pending":   self._queue.qsize(),
            "stored":    len(self._results),
        }
