FROM python:3.11-slim


WORKDIR /app

RUN pip install --no-cache-dir "torch>=2.4.0" --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY app/           ./app/
# Pre-download Hugging Face model weights to bake them into the image
RUN python -c "from transformers import pipeline; pipeline('image-classification', model='Falconsai/nsfw_image_detection')"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--loop", "uvloop", "--http", "h11"]
