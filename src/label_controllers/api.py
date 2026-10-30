from __future__ import annotations

import json
import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from . import a11y as a11y_channel
from .config import Settings
from .contracts import PUBLIC_INCLUDES
from .engine import AnalysisEngine, ImageTooLargeError, ImageValidationError
from .tool import (
    A11Y_TOOL_NAME,
    TOOL_NAME,
    anthropic_a11y_tool_schema,
    anthropic_tool_schema,
    openai_a11y_tool_schema,
    openai_tool_schema,
)

logger = logging.getLogger("label_controllers.api")
settings = Settings.from_env()
bearer_scheme = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app.state.settings = settings
    logger.info("Loading OmniParser YOLO and RapidOCR models on device=%s", settings.device)
    app.state.engine = AnalysisEngine(settings)
    logger.info(
        "Models ready in %.2fs on %s (%s), OCR provider=%s",
        app.state.engine.model_load_seconds,
        app.state.engine.runtime.backend,
        app.state.engine.runtime.device_name,
        app.state.engine.runtime.ocr_provider,
    )
    yield
    logger.info("Shutting down Label Controllers API")


app = FastAPI(
    title="Label Controllers API",
    summary="Screenshot-to-controls service powered by YOLO and OCR.",
    description=(
        "Upload a UI screenshot. The service detects icon-like controls, recognizes text, "
        "fuses both sources, and can return clickable controls, all elements, OCR blocks, "
        "raw model output, timing metadata, and an annotated PNG."
    ),
    version=__version__,
    docs_url="/docs" if settings.expose_docs else None,
    redoc_url="/redoc" if settings.expose_docs else None,
    openapi_url="/openapi.json" if settings.expose_docs else None,
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1200)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=[
            "X-Request-ID",
            "X-Control-Count",
            "X-Text-Count",
            "X-Model-Device",
            "X-OCR-Provider",
            "X-Processing-Seconds",
        ],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "").strip()
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied) else uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request failure request_id=%s", request_id)
        response = JSONResponse(
            status_code=500,
            content={
                "request_id": request_id,
                "error": {"code": "internal_error", "message": "An unexpected server error occurred."},
            },
        )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Processing-Seconds"] = f"{time.perf_counter() - started:.4f}"
    return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", uuid.uuid4().hex)


def _error(request: Request, status_code: int, code: str, message: str, **extra: Any) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    error.update(extra)
    return JSONResponse(
        status_code=status_code,
        content={"request_id": _request_id(request), "error": error},
        headers={"Cache-Control": "no-store"},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code", "http_error"))
        message = str(detail.get("message", detail))
    else:
        code = "http_error"
        message = str(detail)
    return _error(request, exc.status_code, code, message)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(
        request,
        422,
        "validation_error",
        "One or more multipart fields are invalid.",
        details=jsonable_encoder(exc.errors()),
    )


async def require_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected = request.app.state.settings.api_key
    if not expected:
        return
    supplied = x_api_key
    if not supplied and credentials and credentials.scheme.casefold() == "bearer":
        supplied = credentials.credentials
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "A valid Bearer token or X-API-Key header is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )


def _parse_includes(raw: str) -> set[str]:
    tokens = {token.strip().casefold() for token in raw.split(",") if token.strip()}
    if not tokens:
        raise ValueError("The include list cannot be empty.")
    if "all" in tokens:
        return set(PUBLIC_INCLUDES)
    invalid = sorted(tokens - set(PUBLIC_INCLUDES))
    if invalid:
        raise ValueError(f"Unknown include field(s): {', '.join(invalid)}. Allowed: {', '.join(PUBLIC_INCLUDES)}, all.")
    return tokens


def _parse_content_length(request: Request) -> int | None:
    raw = request.headers.get("Content-Length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@app.get("/", include_in_schema=False)
async def root() -> dict[str, Any]:
    return {
        "service": "Label Controllers API",
        "version": __version__,
        "status": "running",
        "documentation": "/docs" if settings.expose_docs else None,
        "endpoints": {
            "analyze": "POST /api/v1/analyze",
            "capabilities": "GET /api/v1/capabilities",
            "tools": "GET /api/v1/tools",
            "readiness": "GET /readyz",
        },
    }


@app.get("/healthz", tags=["Health"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "label-controllers", "version": __version__}


@app.get("/readyz", tags=["Health"])
async def readyz(request: Request) -> dict[str, Any]:
    return request.app.state.engine.readiness()


@app.get("/api/v1/capabilities", tags=["Metadata"])
async def capabilities(request: Request) -> dict[str, Any]:
    return {
        "service": "label-controllers",
        "version": __version__,
        "compute": request.app.state.engine.runtime.as_dict(),
        "upload": {
            "field": "file",
            "content_type": "multipart/form-data",
            "formats": ["PNG", "JPEG", "WEBP", "BMP", "TIFF"],
            "max_upload_mb": settings.max_upload_mb,
            "max_image_pixels": settings.max_image_pixels,
        },
        "include": {
            "fields": list(PUBLIC_INCLUDES),
            "default": ["controls", "texts", "metadata"],
            "presets": {
                "minimal": ["controls"],
                "standard": ["controls", "texts", "metadata"],
                "full": list(PUBLIC_INCLUDES),
                "all": "all",
            },
            "descriptions": {
                "controls": (
                    "Clickable controls only, with text, role, confidence, rectangle, center and safe click point."
                ),
                "elements": "All fused elements, including non-clickable containers.",
                "texts": "All OCR text blocks and their image coordinates.",
                "metadata": "Model information, fused counts, timings and output field list.",
                "raw_icons": "Raw OmniParser YOLO detections.",
                "raw_ocr": "Raw RapidOCR detections.",
                "a11y": "Accessibility-tree summary and node list when the a11y channel was requested.",
                "alignment": "Vision \u2194 accessibility alignment: matched pairs, match rates and text conflicts.",
                "annotated_image": "Base64-encoded annotated PNG in the JSON response.",
            },
        },
        "response_formats": {
            "json": "JSON with only the requested include fields.",
            "png": "An annotated PNG returned directly; no JSON body.",
        },
        "coordinates": "Pixel coordinates are relative to the uploaded image's top-left corner.",
        "authentication": "If an API key is configured, send Authorization: Bearer <key> or X-API-Key: <key>.",
        "tools": {
            "discovery": "/api/v1/tools",
            "openai_tool_name": TOOL_NAME,
            "a11y_tool_name": A11Y_TOOL_NAME,
            "python_client": "label_controllers.tool:LabelControllersTool",
            "mcp_server": "label-controllers-mcp",
        },
    }


@app.get("/api/v1/tools", tags=["Metadata"])
async def tools() -> dict[str, Any]:
    """Return OpenAI- and Anthropic-compatible tool definitions.

    The caller's tool executor binds or captures the current screenshot and
    forwards it to `/api/v1/analyze`; the model only chooses analysis options.
    """

    return {
        "schema_version": "1.1",
        "tools": [openai_tool_schema(), openai_a11y_tool_schema()],
        "anthropic_tools": [anthropic_tool_schema(), anthropic_a11y_tool_schema()],
        "execution": {
            "endpoint": "/api/v1/analyze",
            "probe_endpoint": "/api/v1/a11y/probe",
            "transport": "multipart/form-data",
            "image_field": "file",
            "a11y_field": "a11y",
            "image_handling": (
                "The tool host supplies the current screenshot bytes. "
                "The model should call the tool without generating image data."
            ),
            "a11y_handling": (
                "Pass the a11y object either as the multipart 'a11y' field (JSON string) for "
                "/api/v1/analyze, or as the JSON body of /api/v1/a11y/probe. "
                "mode='cdp' pulls the tree server-side; mode='inline' accepts a pre-collected one."
            ),
            "python_client": "label_controllers.tool:LabelControllersTool",
            "cli": "label-controllers-tool",
            "mcp": "label-controllers-mcp",
        },
    }


@app.post(
    "/api/v1/analyze",
    tags=["Analysis"],
    response_model=None,
    responses={
        200: {
            "description": "JSON analysis result or annotated PNG.",
            "content": {
                "application/json": {},
                "image/png": {},
            },
        },
        400: {"description": "Invalid multipart form value."},
        401: {"description": "Missing or invalid API key."},
        413: {"description": "Upload too large."},
        415: {"description": "Unsupported or corrupt image."},
    },
)
async def analyze(
    request: Request,
    file: Annotated[UploadFile, File(description="Screenshot to analyze (PNG, JPEG, WEBP, BMP or TIFF).")],
    include: Annotated[
        str,
        Form(
            description=(
                "Comma-separated fields: controls, elements, texts, metadata, raw_icons, raw_ocr, annotated_image, all."
            )
        ),
    ] = "controls,texts,metadata",
    response_format: Annotated[
        Literal["json", "png"], Form(description="Return JSON data or an annotated PNG.")
    ] = "json",
    icon_confidence: Annotated[
        float | None,
        Form(ge=0.0, le=1.0, description="Optional YOLO confidence override for this request."),
    ] = None,
    ocr_confidence: Annotated[
        float | None,
        Form(ge=0.0, le=1.0, description="Optional OCR confidence override for this request."),
    ] = None,
    a11y: Annotated[
        str | None,
        Form(
            description=(
                "Optional accessibility-tree channel, as JSON. Use "
                '{"mode":"cdp","cdp_url":"http://127.0.0.1:9222","target":0} to let the service '
                'pull the tree, or {"mode":"inline","elements":[{"role":"button","name":"Log in",'
                '"rect":[x1,y1,x2,y2]}]} to submit a pre-collected tree.'
            )
        ),
    ] = None,
    _: Annotated[None, Depends(require_api_key)] = None,
):
    """Analyze one screenshot and select exactly which result groups to return.

    Examples:

    * `include=controls` returns only clickable controls plus request/image metadata.
    * `include=controls,texts,metadata` is the default.
    * `include=all` additionally returns raw model output and a base64 PNG.
    * `response_format=png` streams an annotated PNG directly.
    * Passing `a11y` merges accessibility roles/names/boxes into the same element list;
      add `include=alignment` to see the match report.
    """
    try:
        requested_includes = _parse_includes(include)
    except ValueError as exc:
        return _error(request, 400, "invalid_include", str(exc))

    declared_length = _parse_content_length(request)
    multipart_overhead = 1024 * 1024
    if declared_length is not None and declared_length > settings.max_upload_bytes + multipart_overhead:
        return _error(
            request,
            413,
            "upload_too_large",
            f"Request body exceeds the allowed size. Image limit: {settings.max_upload_mb} MB.",
        )

    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        return _error(
            request,
            413,
            "upload_too_large",
            f"Uploaded image exceeds the {settings.max_upload_mb} MB limit.",
        )
    if not raw:
        return _error(request, 415, "empty_image", "The uploaded image is empty.")

    a11y_spec: dict[str, Any] | None = None
    if a11y:
        try:
            parsed_a11y = json.loads(a11y)
        except json.JSONDecodeError:
            return _error(request, 400, "invalid_a11y", "The a11y form field must be valid JSON.")
        if not isinstance(parsed_a11y, dict):
            return _error(request, 400, "invalid_a11y", "The a11y form field must be a JSON object.")
        a11y_spec = parsed_a11y
        if "all" not in requested_includes:
            # A merge the caller cannot inspect is a merge they cannot trust.
            requested_includes.update({"alignment", "a11y"})

    engine: AnalysisEngine = request.app.state.engine
    try:
        result = await run_in_threadpool(
            engine.analyze,
            raw,
            request_id=_request_id(request),
            filename=file.filename or "upload",
            icon_confidence=icon_confidence,
            ocr_confidence=ocr_confidence,
            render_annotated=response_format == "png" or "annotated_image" in requested_includes,
            a11y=a11y_spec,
        )
    except ImageTooLargeError as exc:
        return _error(request, 413, "upload_too_large", str(exc))
    except ImageValidationError as exc:
        return _error(request, 415, "invalid_image", str(exc))
    except Exception:
        logger.exception("Analysis failed request_id=%s", _request_id(request))
        return _error(request, 500, "analysis_failed", "Model inference failed. Check server logs with the request ID.")

    result_headers = {
        "Cache-Control": "no-store",
        "X-Control-Count": str(len(result.controls)),
        "X-Text-Count": str(len(result.texts)),
        "X-Model-Device": engine.device_header,
        "X-OCR-Provider": engine.runtime.ocr_provider,
    }
    if response_format == "png":
        result_headers["Content-Disposition"] = f'inline; filename="label-controllers-{result.request_id}.png"'
        if result.annotated_png is None:
            raise RuntimeError("Annotated PNG was requested but not rendered.")
        return Response(content=result.annotated_png, media_type="image/png", headers=result_headers)

    payload = result.to_payload(requested_includes)
    return JSONResponse(content=payload, headers=result_headers)


@app.post("/api/v1/a11y/probe", tags=["Analysis"])
async def a11y_probe(
    request: Request,
    payload: Annotated[
        dict[str, Any],
        Body(
            description=(
                'Either {"mode":"cdp","cdp_url":"http://127.0.0.1:9222","target":0} or '
                '{"mode":"inline","elements":[{"role":"button","name":"Log in","rect":[0,0,10,10]}]}.'
            )
        ),
    ],
    _: Annotated[None, Depends(require_api_key)] = None,
):
    """Grade an accessibility tree before automating: usable, partial or useless?

    Returns the interative node list, naming/coordinate coverage and a strategy
    verdict (`a11y_primary`, `hybrid` or `vision_only`) plus the checks behind it.
    No screenshot is needed; this is a metadata-only endpoint.
    """
    try:
        result = await run_in_threadpool(a11y_channel.probe, payload)
    except a11y_channel.A11yError as exc:
        return _error(request, 400, exc.code, str(exc), details=exc.details)
    headers = {
        "Cache-Control": "no-store",
        "X-A11y-Strategy": str(result["verdict"]["strategy"]),
    }
    return JSONResponse(content=jsonable_encoder(result), headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "label_controllers.api:app",
        host="0.0.0.0",
        port=8000,
        log_level=settings.log_level,
    )
