# Changelog

All notable changes are documented here. The project follows semantic versioning once
a tagged public release exists.

## [0.4.3] - 2026-11-05

### Changed

- Container publishing is now manual-only: `docker-publish.yml` no longer runs on pushes to
  `main` or on version tags, so merging and tagging never publish an image to GHCR. Trigger it
  from the Actions tab when a release is actually meant to be published.

### Fixed

- Dockerfile now copies `src/` after the dependency layer, so editing source no longer invalidates
  the ~2.5 GB dependency install (a one-file change previously cost a full re-install, measured at
  over 23 minutes).

### Added

- Fourth recognition channel: QR and barcode regions powered by zxing-cpp 3.1.1, enabled by
  `include=codes` or `include=all`. Decoded payloads and detected-but-undecodable symbols are
  exposed as non-clickable code elements after the accessibility merge, with quadrilateral
  coordinates, bounding rectangles, symbology and timing/count metadata.
- Conservative format selection, degenerate/small-box filtering and overlap suppression to
  reduce false detections; annotated images label undecodable symbols with a `?` suffix.
- `PIP_TORCH_INDEX_URL` / `PIP_TORCH_TRUSTED_HOST` build args; torch and torchvision are installed
  from a separate index so restricted networks can use a mirror (defaults unchanged).

## [0.4.2] - 2026-11-05

### Fixed

- Docker image no longer skips the `[a11y]` extra dependencies: `--no-deps` silently dropped
  `websocket-client`, so `a11y` mode `cdp` failed at runtime with `a11y_dependency_missing`.
- Build no longer requires reaching pypi.org directly: `PIP_INDEX_URL` / `PIP_TRUSTED_HOST`
  build args default to PyPI and can point at a mirror on restricted networks.
- `__version__` reported `0.3.0` while `pyproject.toml` was `0.4.1`; both are now `0.4.2`, and the
  CI quality job fails when the two drift apart.

### Added

- README / docs/DEPLOYMENT.md build notes for mirror usage and for the optional a11y dependency.
- CI version-consistency check between `pyproject.toml` and `label_controllers.__version__`.

## [0.4.1] - 2026-10-31

### Fixed

- `a11y` target selection can pin a page by URL substring (`target_url`), because Chrome sorts
  `/json/list` by recency and the numeric index drifts on a real desktop with many tabs.
- Off-screen accessibility nodes are no longer appended as clickable elements. The AX tree covers the
  whole document; on a GitHub repo page only 82 of 184 interactive nodes were inside a 1399x855
  viewport, so the previous behaviour handed callers 102 unclickable coordinates. Report gains
  `skipped_offscreen`, `skipped_no_rect` and `visible_interactive`.
- Value-like roles (`textbox`, `searchbox`, `combobox`, `spinbutton`, `slider`, `listbox`, `option`)
  no longer count as text conflicts: the a11y name is a label while OCR reads the value, so they are
  different layers. The label is exposed as `a11y_label`; `value_role_matches` is reported.
  On a Gradio UI this cleared 8 false conflicts out of 24 matches.

### Added

- Field-trial results for GitHub, Bilibili, a Gradio UI (GPT-SoVITS) and a canvas app in
  `docs/A11Y.md`, plus a click-accuracy experiment (8/9 elements hit; the only miss was a plain text
  block that only the vision channel proposed).

## [0.4.0] - 2026-10-31

### Added

- Third input channel: accessibility tree (a11y), merged with the YOLO icon and RapidOCR text channels.
  - `a11y` multipart/JSON field with `mode=cdp` (server pulls `Accessibility.getFullAXTree`) or
    `mode=inline` (caller submits pre-collected nodes).
  - Deterministic one-to-one alignment (IoU + containment + text similarity) with private-use glyph
    stripping, conflict reporting and `sources` tagging per element.
  - Unmatched a11y nodes are appended as `type="a11y_only"` clickable elements; unnamed icons gain
    semantic names instead of being dropped.
- `POST /api/v1/a11y/probe`: grade an accessibility tree (node count, naming/coordinate coverage,
  `a11y_primary` / `hybrid` / `vision_only` verdict) without uploading a screenshot.
- `a11y` and `alignment` response fields; `metadata.a11y` carries collection timing and the merge report.
- `probe_ui_accessibility` tool definition (OpenAI + Anthropic) and `LabelControllersTool.probe_a11y`.
- MCP (stdio) server: `label-controllers-mcp` / `python -m label_controllers.mcp_server`,
  exposing both tools with `image_base64` / `image_path` carriers.
- CLI: `--a11y-cdp` / `--a11y-target` / `--a11y-url` / `--a11y-json` on `analyze`, new `a11y-probe`
  and `mcp` subcommands.
- `docs/A11Y.md`, `docs/MCP.md`, `examples/mcp_client.py`, `tests/test_a11y.py`.

### Changed

- `include` accepts `a11y` and `alignment`; passing `a11y` enables both automatically.
- `GET /api/v1/tools` returns both tool definitions and documents the a11y payload shape.
- Optional dependency group `a11y` (`websocket-client`); the inline path needs no extra dependency.
- Version bumped to `0.4.0`.

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