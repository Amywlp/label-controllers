# Changelog

All notable changes are documented here. The project follows semantic versioning once
a tagged public release exists.

## [Unreleased]

### Added

- MCP stdio server exposing `list_desktop_windows` and `analyze_desktop_window`
  as a bridge over the native Windows Desktop Adapter.
- PID/process/class/title/window-ID target selection, automatic desktop-session
  cleanup, structured MCP tool errors, and annotated-image MCP content-block passthrough.
- `label-controllers-mcp` executable, `scripts/run_mcp_server.py` quiet launcher,
  MCP client example, and `docs/MCP.md`.
- `LABEL_CONTROLLERS_OCR_USE_ANGLE_CLS` and benchmark `--[no-]ocr-use-angle-cls`
  controls for the RapidOCR 180-degree classifier.
- Provider-aware OCR recognition batching: 12 on GPU and 6 on CPU by default.
- `LABEL_CONTROLLERS_OCR_REC_BATCH_NUM` and `--ocr-rec-batch-num` overrides, with the
  effective value exposed in model metadata.

### Changed

- GPU OCR defaults to the faster batch size 12. Set the batch setting to `6` when exact
  output parity with the original OCR path is required.
- OCR now skips angle classification by default for upright UI screenshots and reuses the
  RGB array from Pillow normalization for recognition and fusion sizing, removing a second
  normalized-PNG decode.
- DirectML end-to-end latency fell from 0.7625 s to 0.6056 s (20.6%) in a fresh
  36-request A/B run, with exact control and OCR parity; CPU end-to-end latency
  fell from 1.5644 s to 1.4956 s (4.4%).
- Version bumped to `0.5.0` for the MCP service and OCR preprocessing improvements.

## [0.4.0] - 2026-10-30

### Added

- Native Windows Desktop Adapter with stable application binding through `window_id`, PID,
  process start time, HWND, executable name and window class.
- Client-area capture through `PrintWindow`, with a foreground-only screen fallback.
- Per-monitor DPI-aware conversion from recognized image coordinates to client and virtual
  desktop physical coordinates.
- Stateful desktop sessions with frame IDs, geometry hashes, target revalidation and
  foreground guards.
- Guarded `SendInput` mouse actions for absolute movement, relative movement, clicks,
  drags, wheel input and held-button sequences.
- Pointer-lock/mouse-capture profiles using ClipCursor detection, relative gain,
  movement chunking and image-to-relative `aim` actions.
- `label-controllers-desktop` executable and `scripts/run-windows-desktop-adapter.ps1`.
- Desktop integration tests on real Win32/Tk windows, including exact absolute-coordinate
  click delivery and relative pointer-lock clicks.

### Changed

- Version bumped to `0.4.0`.
- Docker remains inference-only; destructive desktop operations are exposed only by the
  native Windows adapter on a loopback address by default.

## [0.3.0] - 2026-10-30

### Added

- OpenAI Chat Completions / Responses-compatible `analyze_ui_screenshot` tool definition.
- Anthropic Messages tool definition generated from the same JSON Schema.
- `GET /api/v1/tools` tool discovery endpoint.
- `LabelControllersTool` Python executor with screenshot-provider binding, validation and
  structured `ToolCallError` handling.
- `label-controllers-tool` CLI and `python -m label_controllers.tool` entry points.
- End-to-end OpenAI function-calling example in `examples/openai_tool_call.py`.
- Dedicated tool integration documentation in `docs/TOOL.md`.

### Changed

- Shared response-field constants moved to `label_controllers.contracts`.
- Version bumped to `0.3.0`.

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