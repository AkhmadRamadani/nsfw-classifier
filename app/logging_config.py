"""
Structured logging configuration.

Provides a JSON formatter for production and a human-readable formatter for
development.  Configured via the LOG_FORMAT environment variable.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per line — easy to ingest with Loki, Datadog, etc."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def setup_logging() -> None:
    """Configure the root logger based on LOG_FORMAT env var."""
    fmt = os.getenv("LOG_FORMAT", "text").lower()
    root = logging.getLogger()

    # Remove any pre-existing handlers (uvicorn adds its own)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
