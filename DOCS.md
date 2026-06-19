# NSFW API Documentation

## Overview

The NSFW Detection API provides multiple ways to scan images for inappropriate content using the `Falconsai/nsfw_image_detection` model.

### Available Endpoints

- **`POST /detect/direct`**: Synchronous endpoint for immediate results.
- **`POST /detect`**: Async endpoint. Uploads an image and returns a job ID.
- **`POST /detect/url`**: Async endpoint. Provide an image URL.
- **`POST /detect/batch`**: Async endpoint. Upload up to 32 images at once.
- **`GET /jobs/{job_id}`**: Polling endpoint to get the result of an async job.
- **`DELETE /jobs/{job_id}`**: Cancel a pending job.
- **`GET /health`**: API health and queue status.

---

## Synchronous Inference

### `POST /detect/direct`
The fastest way to get a result for a single image. Bypasses the job queue.

```bash
curl -X POST http://localhost:8000/detect/direct \
  -F "file=@photo.jpg"
```

**Response:**
```json
{
  "nsfw_score": 0.021442,
  "sfw_score": 0.978558,
  "label": "sfw",
  "elapsed_ms": 648.53
}
```

---

## Asynchronous Inference & Webhooks

When dealing with large files, batches, or unpredictable spikes in traffic, use the background queue.

### `POST /detect` — Upload an image

```bash
curl -X POST http://localhost:8000/detect \
  -F "file=@photo.jpg" \
  -F "webhook_url=https://yourdomain.com/webhook" # Optional
```

**Response:**
```json
{
  "job_id": "b3d2c1a0-...",
  "status": "pending"
}
```

### `POST /detect/url` — Detect via URL

```bash
curl -X POST http://localhost:8000/detect/url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/image.jpg",
    "webhook_url": "https://yourdomain.com/webhook"
  }'
```

### `POST /detect/batch` — Batch upload (up to 32 images)

```bash
curl -X POST http://localhost:8000/detect/batch \
  -F "files=@img1.jpg" \
  -F "files=@img2.jpg" \
  -F "files=@img3.jpg" \
  -F "webhook_url=https://yourdomain.com/webhook" # Optional
```

**Response:**
```json
{
  "job_ids": ["uuid-1", "uuid-2", "uuid-3"],
  "count": 3
}
```

---

## Polling Job Status

If you do not use webhooks, you can poll for the result.

### `GET /jobs/{job_id}`

```bash
curl http://localhost:8000/jobs/b3d2c1a0-...
```

**Pending State:**
```json
{"job_id": "...", "status": "pending", "result": null}
```

**Done State:**
```json
{
  "job_id": "b3d2c1a0-...",
  "status": "done",
  "result": {
    "nsfw_score": 0.034201,
    "sfw_score":  0.965799,
    "label":      "sfw",
    "elapsed_ms": 648.5
  },
  "queued_at": 1718123400.12,
  "done_at":   1718123400.54
}
```

---

## Webhook Payload Format

If you provide a `webhook_url`, the API will send a `POST` request to that URL when the job completes or fails.

**Success:**
```json
{
  "job_id": "35791e06-1c8d-4a0c-8820-bbac0ea81221",
  "status": "done",
  "nsfw_score": 0.021442,
  "sfw_score": 0.978558,
  "label": "sfw",
  "elapsed_ms": 648.53
}
```

**Error:**
```json
{
  "job_id": "35791e06-1c8d-4a0c-8820-bbac0ea81221",
  "status": "failed",
  "error": "Image must be ≤ 10 MB"
}
```

---

## System Health

### `GET /health`

```json
{
  "status": "ok",
  "model_loaded": true,
  "queue_depth": 0,
  "workers": 2
}
```
