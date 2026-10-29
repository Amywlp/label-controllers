# Label Controllers API

Base URL in the default Compose deployment:

```text
http://127.0.0.1:8000
```

Interactive OpenAPI documentation is available at `/docs`.

The optional native Windows desktop adapter is a separate service on
`http://127.0.0.1:8022` by default. It adds window enumeration, client-area capture,
image-to-screen coordinate conversion and guarded mouse actions. See
[DESKTOP.md](DESKTOP.md); the Docker inference API remains stateless and never controls
the host mouse.

## Authentication

Authentication is optional in development and recommended for every exposed deployment.
Set `LABEL_CONTROLLERS_API_KEY` to enable it. Send exactly one of:

```http
Authorization: Bearer <api-key>
X-API-Key: <api-key>
```

When the configured key is empty, analysis requests are accepted without authentication.
Health and capability endpoints never require a key.

## `POST /api/v1/analyze`

Consumes `multipart/form-data` and returns JSON or an annotated PNG.

### Multipart fields

| Field | Required | Default | Description |
|---|---:|---|---|
| `file` | yes | — | PNG, JPEG, WEBP, BMP, or TIFF screenshot |
| `include` | no | `controls,texts,metadata` | Comma-separated optional result fields |
| `response_format` | no | `json` | `json` or `png` |
| `icon_confidence` | no | server default `0.18` | Per-request YOLO threshold, `0..1` |
| `ocr_confidence` | no | server default `0.40` | Per-request OCR threshold, `0..1` |

### `include` values

- `controls`: clickable fused controls only.
- `elements`: every fused element, including containers and text-only elements.
- `texts`: all OCR text blocks.
- `metadata`: model, timing, counts and output-field information.
- `raw_icons`: raw YOLO boxes.
- `raw_ocr`: raw OCR boxes.
- `annotated_image`: annotated PNG encoded as Base64 inside the JSON.
- `all`: every field above.

Every JSON response includes `request_id` and the `image` object even when those fields
are not listed in `include`.

### JSON example

Request:

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/analyze" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_API_KEY}" \
  -F "file=@screenshot.png" \
  -F "include=controls,texts,metadata" \
  -F "response_format=json"
```

Response shape:

```json
{
  "request_id": "9d55fdb88f1b4c74ae53b67d82f314e2",
  "image": {
    "width": 2560,
    "height": 1440,
    "format": "PNG",
    "sha256": "..."
  },
  "controls": [
    {
      "id": 12,
      "text": "Downloads",
      "type": "with_text",
      "role": "clickable",
      "confidence": 0.9132,
      "ocr_confidence": 0.9871,
      "clickable": true,
      "rect": [25, 250, 143, 291],
      "center": [84, 270],
      "click": [70, 270],
      "source": "yolo+ocr"
    }
  ],
  "texts": [
    {
      "id": 1,
      "text": "Downloads",
      "confidence": 0.9871,
      "rect": [52, 258, 143, 281]
    }
  ],
  "metadata": {
    "schema_version": "1.0",
    "filename": "screenshot.png",
    "models": {
      "icon": {
        "name": "OmniParser-v2 icon_detect",
        "family": "YOLO11m",
        "parameters": 20053779,
        "file": "icon_detect.pt",
        "device": "cpu",
        "device_name": "AMD64 Family 25 Model 97 Stepping 2, AuthenticAMD",
        "torch_version": "2.4.1+cpu"
      },
      "ocr": {
        "name": "RapidOCR (PP-OCR ONNX)",
        "engine": "rapidocr-onnxruntime==1.2.3",
        "device": "cpu",
        "provider": "CPUExecutionProvider"
      },
      "runtime": {
        "backend": "cpu",
        "device_index": 0,
        "is_gpu": false,
        "ocr": {
          "backend": "cpu",
          "provider": "CPUExecutionProvider"
        }
      }
    },
    "runtime": {
      "backend": "cpu",
      "device_index": 0,
      "is_gpu": false,
      "ocr": {
        "backend": "cpu",
        "provider": "CPUExecutionProvider"
      }
    },
    "model_load_seconds": 4.12,
    "timing_seconds": {
      "decode_and_normalize": 0.03,
      "icon": 1.02,
      "ocr": 0.91,
      "fusion": 0.12,
      "annotation": 0.0,
      "total": 2.16
    },
    "fused_counts": {
      "icons_raw": 90,
      "icons_deduplicated": 84,
      "texts_raw": 60,
      "texts_deduplicated": 58,
      "containers": 2,
      "with_text": 42,
      "icon_only": 30,
      "text_only": 12,
      "controls": 72,
      "elements": 86
    },
    "output_fields": ["controls", "metadata", "texts"]
  }
}
```

Actual coordinates and counts depend on the screenshot and model thresholds.
`annotation` is `0.0` unless the caller requests `response_format=png` or
`include=annotated_image`/`include=all`. This avoids unnecessary CPU rendering for
controls-only and JSON-only consumers.

GPU deployments add richer values to `metadata.models.icon`, `metadata.models.ocr`,
`metadata.models.runtime`, and the top-level `metadata.runtime`. Examples are
`directml` / `DmlExecutionProvider` and `cuda` / `CUDAExecutionProvider`.

### Base64 image example

```json
{
  "annotated_image": {
    "mime_type": "image/png",
    "encoding": "base64",
    "byte_length": 482190,
    "data": "iVBORw0KGgo..."
  }
}
```

Request this form with:

```text
include=annotated_image
```

or `include=all`.

### Direct PNG response

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/analyze" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_API_KEY}" \
  -F "file=@screenshot.png" \
  -F "response_format=png" \
  -o annotated.png
```

The response has `Content-Type: image/png` and includes:

- `X-Request-ID`
- `X-Control-Count`
- `X-Text-Count`
- `X-Model-Device: cpu`, `cuda`, `rocm`, `directml`, or `mps`
- `X-OCR-Provider: CPUExecutionProvider`, `CUDAExecutionProvider`, `DmlExecutionProvider`, or another provider
- `X-Processing-Seconds`

### Error format

```json
{
  "request_id": "abc123",
  "error": {
    "code": "invalid_image",
    "message": "Unsupported or corrupt image. Use PNG, JPEG, WEBP, BMP, or TIFF."
  }
}
```

| HTTP status | Code | Meaning |
|---:|---|---|
| `400` | `invalid_include` | Unknown or empty output field list |
| `401` | `unauthorized` | Missing/wrong API key |
| `413` | `upload_too_large` | File exceeds configured byte limit |
| `415` | `invalid_image` | Empty, corrupt or unsupported image |
| `422` | `validation_error` | FastAPI field validation failed |
| `500` | `analysis_failed` | Inference or annotation failed |

## `GET /api/v1/capabilities`

Returns supported fields, formats, upload limits, authentication notes, the active
`compute` runtime object (`backend`, `device_name`, `is_gpu`, torch version, device index and
OCR provider), and the standard tool discovery location. It does not run inference.

## `GET /api/v1/tools`

Returns standard tool definitions for the current API:

- `tools`: OpenAI Chat Completions / Responses tool array.
- `anthropic_tools`: Anthropic Messages tool definitions.
- `execution`: the multipart endpoint and image-binding contract used by the local executor.

The single tool is named `analyze_ui_screenshot`. Its schema exposes `include`,
`annotate`, `icon_confidence` and `ocr_confidence`. The current screenshot is supplied by
the trusted tool host through `LabelControllersTool.screenshot_provider`; the model does not
generate or transport screenshot bytes.

See [TOOL.md](TOOL.md) for Python, OpenAI and CLI examples.

## `GET /healthz`

Process liveness check. It does not verify model readiness.

## `GET /readyz`

Returns `200` once the YOLO and OCR models are loaded. Response includes model load time,
warm-up timings, processed request count, uptime, model details and the resolved accelerator
under `models.runtime`.

## Coordinate system

All coordinates are `[x1, y1, x2, y2]` pixel rectangles relative to the uploaded image.
`click` is a recommended safe interior point. It is not an absolute desktop coordinate;
callers that use the controls on a live screen must add the screenshot's screen offset.

## Idempotency and tracing

A caller may supply `X-Request-ID`. Values up to 64 characters using letters, digits,
dot, underscore, colon or hyphen are preserved; other values are replaced with a UUID.
The effective ID is echoed in `X-Request-ID` and in JSON errors.