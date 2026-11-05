FROM python:3.10-slim-bookworm AS model

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /download
COPY scripts/download_model.py /download/download_model.py
COPY models /model-input
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" requests==2.34.2 \
    && mkdir -p /models \
    && if [ -f /model-input/icon_detect.pt ]; then cp /model-input/icon_detect.pt /models/icon_detect.pt; fi \
    && python /download/download_model.py --output /models/icon_detect.pt

FROM python:3.10-slim-bookworm AS runtime

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org
ARG PIP_TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG PIP_TORCH_TRUSTED_HOST=download.pytorch.org

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

# Install system libraries and fonts before Python dependencies.
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

# Install torch and torchvision from their own index: the +cpu local versions do not exist on PyPI,
# while their own dependencies (filelock, sympy, networkx, ...) still come from the main index.
RUN pip install --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" \
    --extra-index-url "${PIP_TORCH_INDEX_URL}" --trusted-host "${PIP_TORCH_TRUSTED_HOST}" \
    torch==2.4.1+cpu torchvision==0.19.1+cpu

# Install the remaining PyPI dependencies before copying source so source edits reuse the dependency cache.
RUN pip install --upgrade --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" pip setuptools wheel \
    && pip install --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" -r requirements.txt

COPY src ./src

# Install the copied source in editable mode without changing dependency pins, and add the optional a11y dependency.
RUN pip install --no-deps -e ".[a11y]" \
    && pip install --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" "websocket-client>=1.7,<2"

COPY --from=model /models/icon_detect.pt /app/models/icon_detect.pt

# Create an unprivileged runtime user and grant ownership of the application.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=240s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

ENTRYPOINT ["uvicorn", "label_controllers.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]