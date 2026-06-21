"""
Tests for the NSFW Detection API.
Run: pytest tests/ -v
"""

import asyncio
import io
import time
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock

from httpx import AsyncClient, ASGITransport
from PIL import Image

from app.queue import JobQueue, ResultStore
from app.schemas import JobState


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_jpeg_bytes(width=64, height=64) -> bytes:
    """Create a minimal valid JPEG in memory."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ── ResultStore ───────────────────────────────────────────────────────────────

class TestResultStore:
    def test_set_and_get(self):
        store = ResultStore(ttl_seconds=60)
        store.set("abc", {"foo": "bar"})
        assert store.get("abc") == {"foo": "bar"}

    def test_missing_key_returns_none(self):
        store = ResultStore()
        assert store.get("nonexistent") is None

    def test_expired_key_returns_none(self):
        store = ResultStore(ttl_seconds=0)  # expires immediately
        store.set("key", {"val": 1})
        time.sleep(0.01)
        assert store.get("key") is None

    def test_delete(self):
        store = ResultStore()
        store.set("x", {})
        assert store.delete("x") is True
        assert store.get("x") is None

    def test_purge_expired(self):
        store = ResultStore(ttl_seconds=0)
        store.set("a", {})
        store.set("b", {})
        time.sleep(0.01)
        store.purge_expired()
        assert len(store) == 0


# ── JobQueue ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestJobQueue:
    async def test_enqueue_and_get_result(self):
        q = JobQueue(maxsize=10)
        await q.enqueue("job-1", b"imagedata")

        result = q.get_result("job-1")
        assert result is not None
        assert result.status == JobState.PENDING
        assert result.job_id == "job-1"

    async def test_dequeue_returns_item(self):
        q = JobQueue(maxsize=10)
        await q.enqueue("job-2", b"data")
        item = await q.dequeue()
        assert item.job_id == "job-2"
        assert item.image_bytes == b"data"

    async def test_mark_done(self):
        q = JobQueue(maxsize=10)
        await q.enqueue("job-3", b"data")
        item = await q.dequeue()
        q.mark_processing(item.job_id)
        q.mark_done(item.job_id, nsfw_score=0.95, sfw_score=0.05, elapsed_ms=12.3)

        result = q.get_result("job-3")
        assert result.status == JobState.DONE
        assert result.result.nsfw_score == pytest.approx(0.95, abs=1e-4)
        assert result.result.label == "nsfw"

    async def test_mark_failed(self):
        q = JobQueue(maxsize=10)
        await q.enqueue("job-4", b"data")
        item = await q.dequeue()
        q.mark_failed(item.job_id, error="decode error")

        result = q.get_result("job-4")
        assert result.status == JobState.FAILED
        assert "decode" in result.error

    async def test_cancel_pending(self):
        q = JobQueue(maxsize=10)
        await q.enqueue("job-5", b"data")
        assert q.cancel("job-5") is True
        assert q.get_result("job-5") is None

    async def test_queue_full_raises(self):
        q = JobQueue(maxsize=1)
        await q.enqueue("j1", b"d")
        with pytest.raises(Exception):
            await q.enqueue("j2", b"d")  # QueueFull

    async def test_stats(self):
        q = JobQueue(maxsize=10)
        await q.enqueue("s1", b"d")
        await q.enqueue("s2", b"d")
        stats = q.stats()
        assert stats["pending"] == 2


# ── API Endpoints ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_worker():
    """Patch NSFWWorker so tests don't need a real model."""
    with patch("app.main.NSFWWorker") as MockWorker:
        instance = MockWorker.return_value
        instance.model_ready = True
        instance.start = asyncio.coroutine(lambda: None) if False else (
            lambda: asyncio.sleep(0)  # async noop
        )
        instance.stop = lambda: asyncio.sleep(0)
        yield instance


@pytest.mark.asyncio
class TestAPI:
    @pytest_asyncio.fixture(autouse=True)
    async def client(self):
        """Start app with mocked worker, yield async test client."""
        with patch("app.main.NSFWWorker") as MockWorkerClass:
            mock_w = MagicMock()
            mock_w.model_ready = True
            mock_w.start = asyncio.coroutine(lambda: None) if False else (
                lambda: asyncio.sleep(0)
            )
            mock_w.stop = lambda: asyncio.sleep(0)
            MockWorkerClass.return_value = mock_w

            from app.main import app
            import app.main as main_mod

            # Directly initialise the globals since ASGI transport
            # doesn't trigger FastAPI lifespan events.
            test_queue = JobQueue(maxsize=10)
            main_mod.job_queue = test_queue
            main_mod.worker = mock_w

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                self.client = ac
                yield

            main_mod.job_queue = None
            main_mod.worker = None

    async def test_health(self):
        resp = await self.client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        # Mock worker can't do real inference, so status may be "degraded"
        assert body["status"] in ("ok", "degraded")
        assert body["model_loaded"] is True

    async def test_detect_returns_job_id(self):
        jpeg = make_jpeg_bytes()
        resp = await self.client.post(
            "/detect",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    async def test_detect_invalid_type(self):
        resp = await self.client.post(
            "/detect",
            files={"file": ("test.gif", b"GIF89a", "image/gif")},
        )
        assert resp.status_code == 415

    async def test_job_not_found(self):
        resp = await self.client.get("/jobs/does-not-exist")
        assert resp.status_code == 404

    async def test_batch_too_many(self):
        jpeg = make_jpeg_bytes()
        files = [("files", (f"{i}.jpg", jpeg, "image/jpeg")) for i in range(33)]
        resp = await self.client.post("/detect/batch", files=files)
        assert resp.status_code == 400

    async def test_batch_returns_job_ids(self):
        jpeg = make_jpeg_bytes()
        files = [("files", (f"{i}.jpg", jpeg, "image/jpeg")) for i in range(3)]
        resp = await self.client.post("/detect/batch", files=files)
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        assert len(body["job_ids"]) == 3
