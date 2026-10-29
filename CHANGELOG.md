# Changelog

All notable changes are documented here. The project follows semantic versioning once
a tagged public release exists.

## [0.2.0] - 2026-10-30

### Added

- Automatic CPU, CUDA/ROCm, Apple MPS and Windows DirectML device resolution.
- ONNX Runtime provider selection for CPU, CUDA and DirectML OCR inference.
- NVIDIA CUDA Dockerfile and Compose GPU override.
- CUDA image build plus read-only, non-root CPU-fallback container smoke test.
- Native Windows DirectML setup and serving scripts.
- Runtime backend, GPU name, provider and warm-up metrics in readiness, metadata,
  capabilities and response headers.
- Reproducible `scripts/benchmark.py` and `scripts/compare_benchmarks.py`.
- DirectML validation on AMD Radeon RX 7800 XT: 1.93x default JSON latency speed-up with
  full CPU-output parity across 12 frames and 36 measured requests per backend.
- Dedicated GPU deployment and benchmark documentation.

### Changed

- Annotation rendering is now lazy and occurs only for PNG or `annotated_image` requests.
- Docker Compose explicitly pins the default deployment to CPU inference.
- Version bumped to `0.2.0`.

## [0.1.0] - 2026-10-30

### Added

- `POST /api/v1/analyze` multipart screenshot analysis endpoint.
- Selectable JSON fields for fused controls, all elements, OCR text, metadata and raw model output.
- Direct annotated PNG response and optional Base64 annotated PNG.
- FastAPI OpenAPI documentation, API key authentication, CORS and upload/pixel limits.
- Resident OmniParser YOLO11m and RapidOCR pipeline with a request lock.
- Multi-stage CPU Docker image with verified model download and non-root runtime.
- Docker Compose deployment with `8000` as the default published port.
- GitHub Actions quality and container publishing workflows.