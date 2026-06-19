# NSFW Detection API

Production-ready FastAPI service wrapping the Hugging Face `Falconsai/nsfw_image_detection` model.  
Accepts images via file upload or URL, processes them synchronously or asynchronously, and returns SFW/NSFW probability scores.

---

## Architecture

```
Client ──POST /detect/direct──▶ FastAPI ──▶ NSFWModelRunner (Hugging Face)
                                                   │
Client ──POST /detect─────────▶ AsyncQueue ────────▶ ResultStore (TTL)
                                  │
                          NSFWWorker (×N) ──▶ Webhook Notification
```

**Key design choices**

| Concern | Solution |
|---|---|
| Non-blocking HTTP | FastAPI + uvicorn async event loop |
| CPU-bound inference | `ThreadPoolExecutor` via `run_in_executor` |
| Direct vs Queue | Support both immediate and background processing |
| Webhooks | Optional webhooks upon job completion |
| Containerized Model | Weights are downloaded during Docker build for fast startup |

---

## Quick Start

### Docker (Recommended for Production)

```bash
docker compose up --build -d
```
The model weights (~340MB) are automatically downloaded into the image during the build process.

### Manual Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
uvicorn app.main:app --reload --port 8000
```

---

## Documentation

For full API usage, including endpoints for direct detection, batch processing, URLs, and webhook integration, please see the [Documentation](DOCS.md).

---

## Project Structure

```
nsfw-api/
├── app/
│   ├── main.py          # FastAPI app, routes, lifespan
│   ├── queue.py         # AsyncQueue + ResultStore
│   ├── worker.py        # Worker pool (asyncio + ThreadPoolExecutor)
│   ├── model.py         # NSFWModelRunner (Hugging Face pipeline)
│   └── schemas.py       # Pydantic models
├── tests/
│   └── test_api.py
├── Dockerfile
├── docker-compose.yml
├── DOCS.md              # API Reference and Usage
└── requirements.txt
```
