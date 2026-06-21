FROM python:3.11-slim

# ── Build args ───────────────────────────────────────────────────────────────
# Set to "gpu" to install CUDA-enabled PyTorch (requires nvidia-docker).
# Defaults to "cpu" for broad compatibility.
ARG TORCH_DEVICE=cpu

WORKDIR /app

# Install PyTorch — CPU or GPU variant depending on build arg
RUN if [ "$TORCH_DEVICE" = "gpu" ]; then \
      pip install --no-cache-dir "torch>=2.4.0" --index-url https://download.pytorch.org/whl/cu121 ; \
    else \
      pip install --no-cache-dir "torch>=2.4.0" --index-url https://download.pytorch.org/whl/cpu ; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY app/           ./app/
# Pre-download Hugging Face model weights to bake them into the image
RUN python -c "from transformers import pipeline; pipeline('image-classification', model='Falconsai/nsfw_image_detection')"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--loop", "uvloop", "--http", "h11"]
