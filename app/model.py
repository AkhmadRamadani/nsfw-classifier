"""
NSFWModelRunner — wraps the Hugging Face Falconsai/nsfw_image_detection model.

This module bridges the Hugging Face Transformers pipeline
into a clean synchronous `predict(image_bytes) -> (nsfw, sfw, elapsed_ms)`
interface, suitable for running inside a ThreadPoolExecutor.

Mock mode
---------
If the Hugging Face model fails to load, it falls back to a
random stub so the API remains functional for development/testing.
"""

from __future__ import annotations

import io
import time
import logging
from typing import Optional, Tuple

import numpy as np

from app.config import MODEL_NAME

logger = logging.getLogger("nsfw.model")

# Maximum dimension (width or height) before we resize.  The HF pipeline
# resizes internally anyway, but doing it ourselves on large images saves
# a lot of memory and speeds up the PIL → tensor conversion.
MAX_IMAGE_DIMENSION: int = 1024

class NSFWModelRunner:
    """
    Thread-safe wrapper around the Hugging Face Falconsai/nsfw_image_detection model.

    Parameters
    ----------
    weights_path : str | None
        Ignored, kept for backward compatibility with the worker interface.
    """

    def __init__(self, weights_path: Optional[str] = None):
        self._mock = False

        try:
            from transformers import pipeline
            self.classifier = pipeline("image-classification", model=MODEL_NAME)
            logger.info("Successfully loaded Hugging Face model: %s", MODEL_NAME)
        except (ImportError, Exception) as exc:
            logger.warning(
                "Could not load Hugging Face model (%s) — falling back to MOCK mode.", exc
            )
            self._mock = True

    # ── Public API ────────────────────────────────────────────────────────────

    def predict(self, image_bytes: bytes) -> Tuple[float, float, float]:
        """
        Run inference on raw image bytes.

        Returns
        -------
        (nsfw_score, sfw_score, elapsed_ms)
        """
        t0 = time.perf_counter()

        if self._mock:
            # Deterministic fake scores based on image hash (reproducible in tests)
            rng = np.random.default_rng(hash(image_bytes[:64]) % (2**31))
            nsfw = float(rng.random())
            sfw  = 1.0 - nsfw
            elapsed = (time.perf_counter() - t0) * 1000
            return nsfw, sfw, elapsed

        from PIL import Image
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        # Downscale large images before sending to the model.
        # The pipeline resizes to 224x224 anyway, but decoding a 4K image
        # into memory first is wasteful.
        if max(image.size) > MAX_IMAGE_DIMENSION:
            image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)

        # The Hugging Face pipeline handles resizing, normalization, and inference
        results = self.classifier(image)

        nsfw_score = 0.0
        sfw_score = 0.0

        # The results list looks like: [{'label': 'normal', 'score': 0.99}, {'label': 'nsfw', 'score': 0.01}]
        for res in results:
            if res['label'] == 'nsfw':
                nsfw_score = res['score']
            elif res['label'] == 'normal':
                sfw_score = res['score']

        elapsed = (time.perf_counter() - t0) * 1000

        return nsfw_score, sfw_score, elapsed
