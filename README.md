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
| Rate Limiting | slowapi per-IP rate limiting on all detect endpoints |
| SSRF Protection | URL validation blocks private/internal/metadata IPs |
| Structured Logging | JSON or text logging via `LOG_FORMAT` env var |
| Containerized Model | Weights are downloaded during Docker build for fast startup |
| GPU Support | CUDA-enabled PyTorch via `TORCH_DEVICE=gpu` build arg |

---

## Quick Start

### Docker (Recommended for Production)

```bash
# CPU (default)
docker compose up --build -d

# GPU (requires nvidia-docker)
docker compose build --build-arg TORCH_DEVICE=gpu
docker compose up -d
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

## Configuration

All tuneable values are centralized in `app/config.py` and overridable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `NSFW_THRESHOLD` | `0.8` | Score above which an image is labelled "nsfw" |
| `MAX_IMAGE_BYTES` | `10485760` | Max upload size per image (bytes) |
| `QUEUE_MAXSIZE` | `500` | Max pending jobs in the async queue |
| `RESULT_TTL_SECONDS` | `600` | How long job results are kept (seconds) |
| `NUM_WORKERS` | `2` | Number of worker coroutines |
| `EXECUTOR_THREADS` | `4` | ThreadPoolExecutor threads for inference |
| `BATCH_SIZE` | `1` | Reserved for future batched inference |
| `RATE_LIMIT` | `60/minute` | slowapi rate limit per IP |
| `MODEL_NAME` | `Falconsai/nsfw_image_detection` | Hugging Face model ID |
| `LOG_FORMAT` | `text` | `text` for development, `json` for production |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Documentation

For full API usage, including endpoints for direct detection, batch processing, URLs, and webhook integration, please see the [Documentation](DOCS.md).

---

## Project Structure

```
nsfw-api/
├── app/
│   ├── main.py            # FastAPI app, routes, lifespan
│   ├── config.py           # Centralized configuration (env vars)
│   ├── logging_config.py   # Structured logging (text / JSON)
│   ├── queue.py            # AsyncQueue + ResultStore
│   ├── worker.py           # Worker pool (asyncio + ThreadPoolExecutor)
│   ├── model.py            # NSFWModelRunner (Hugging Face pipeline)
│   └── schemas.py          # Pydantic models
├── tests/
│   └── test_api.py
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── DOCS.md                 # API Reference and Usage
└── requirements.txt
```
