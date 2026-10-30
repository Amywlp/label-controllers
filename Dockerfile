FROM python:3.10-slim-bookworm AS model

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /download
COPY scripts/download_model.py /download/download_model.py
COPY models /model-input
RUN pip install --no-cache-dir requests==2.34.2 \
    && mkdir -p /models \
    && if [ -f /model-input/icon_detect.pt ]; then cp /model-input/icon_detect.pt /models/icon_detect.pt; fi \
    && python /download/download_model.py --output /models/icon_detect.pt

FROM python:3.10-slim-bookworm AS runtime

ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LABEL_CONTROLLERS_MODEL_PATH=/app/models/icon_detect.pt \
    LABEL_CONTROLLERS_HOST=0.0.0.0 \
    LABEL_CONTROLLERS_PORT=8000 \
    LABEL_CONTROLLERS_DEVICE=cpu \
    LABEL_CONTROLLERS_OCR_DEVICE=cpu \
    YOLO_CONFIG_DIR=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       fonts-wqy-zenhei \
       libgl1 \
       libglib2.0-0 \
       libgomp1 \
       libsm6 \
       libxext6 \
       libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --upgrade pip setuptools wheel \
    && pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt \
    && pip install --no-deps -e ".[a11y]"

COPY --from=model /models/icon_detect.pt /app/models/icon_detect.pt

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=240s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

ENTRYPOINT ["uvicorn", "label_controllers.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]