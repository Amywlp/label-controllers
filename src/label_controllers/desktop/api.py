"""FastAPI application for the native Windows desktop adapter."""

from __future__ import annotations

import base64
import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from .. import __version__
from . import win32
from .models import DesktopError
from .service import DesktopService
from .settings import DesktopSettings

logger = logging.getLogger("label_controllers.desktop.api")
settings = DesktopSettings.from_env()
bearer_scheme = HTTPBearer(auto_error=False)


class SessionCreateRequest(BaseModel):
    """Bind an application window by stable id or a unique selector.

    Prefer ``window_id`` returned by ``GET /windows`` when it is available.
    ``pid`` is useful when an application has exactly one top-level window;
    add ``class_name``, ``process_name`` or ``title_regex`` when a PID owns
    multiple windows (for example browser or Electron applications).
    """

    window_id: str | None = Field(default=None, min_length=3, max_length=80)
    pid: int | None = Field(default=None, gt=0)
    process_name: str = Field(default="", max_length=260)
    class_name: str = Field(default="", max_length=260)
    title_regex: str = Field(default="", max_length=260)
    input_mode: Literal["auto", "absolute", "relative"] = "auto"
    relative_gain_x: float = 1.0
    relative_gain_y: float = 1.0
    relative_max_step: int = Field(default=64, ge=1, le=1000)
    relative_origin_fraction: tuple[float, float] = (0.5, 0.5)

    @model_validator(mode="after")
    def require_selector(self) -> SessionCreateRequest:
        if not any(
            [
                self.window_id,
                self.pid is not None,
                self.process_name.strip(),
                self.class_name.strip(),
                self.title_regex.strip(),
            ]
        ):
            raise ValueError("Provide window_id or at least one of pid, process_name, class_name, title_regex.")
        return self


class AnalyzeRequest(BaseModel):
    include: str = "controls,texts,metadata"
    icon_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    capture_mode: Literal["auto", "printwindow", "screen"] | None = None


class ActionRequest(BaseModel):
    frame_id: str = Field(min_length=3, max_length=80)
    actions: list[dict[str, Any]] = Field(min_length=1, max_length=256)
    dry_run: bool = False
    activate: bool = False
    release_at_end: bool = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    dpi_mode = win32.enable_per_monitor_dpi_awareness()
    app.state.settings = settings
    app.state.service = DesktopService(settings)
    logger.info(
        "Desktop adapter ready: dpi=%s input_enabled=%s inference=%s",
        dpi_mode,
        settings.allow_input,
        settings.inference_url,
    )
    try:
        yield
    finally:
        service: DesktopService | None = getattr(app.state, "service", None)
        if service is not None:
            service.shutdown()
        logger.info("Desktop adapter stopped")


app = FastAPI(
    title="Label Controllers Desktop Adapter",
    summary="Bind Windows applications, capture client windows, map image coordinates, and inject guarded mouse input.",
    version=__version__,
    docs_url="/docs" if settings.expose_docs else None,
    redoc_url="/redoc" if settings.expose_docs else None,
    openapi_url="/openapi.json" if settings.expose_docs else None,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "").strip()
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied) else uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except DesktopError as exc:
        response = _desktop_error_response(request_id, exc)
    except Exception:
        logger.exception("Unhandled desktop request failure request_id=%s", request_id)
        response = JSONResponse(
            status_code=500,
            content={
                "request_id": request_id,
                "error": {"code": "internal_error", "message": "An unexpected desktop adapter error occurred."},
            },
        )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers["X-Processing-Seconds"] = f"{time.perf_counter() - started:.4f}"
    return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", uuid.uuid4().hex)


def _desktop_error_response(request_id: str, exc: DesktopError) -> JSONResponse:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "error": {"code": exc.code, "message": str(exc)},
    }
    if exc.details:
        payload["error"]["details"] = exc.details
    return JSONResponse(status_code=exc.status_code, content=payload, headers={"Cache-Control": "no-store"})


async def require_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected = request.app.state.settings.token
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


def _service(request: Request) -> DesktopService:
    return request.app.state.service


@app.get("/", include_in_schema=False)
async def root() -> dict[str, Any]:
    return {
        "service": "Label Controllers Desktop Adapter",
        "version": __version__,
        "status": "running",
        "documentation": "/docs" if settings.expose_docs else None,
        "windows": "GET /api/v1/desktop/windows",
        "sessions": "POST /api/v1/desktop/sessions",
    }


@app.get("/healthz", tags=["Health"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "label-controllers-desktop", "version": __version__}


@app.get("/readyz", tags=["Health"])
async def readyz(request: Request) -> dict[str, Any]:
    return await run_in_threadpool(_service(request).readiness)


@app.get("/api/v1/desktop/capabilities", tags=["Metadata"])
async def capabilities(request: Request) -> dict[str, Any]:
    service = _service(request)
    return {
        "service": "label-controllers-desktop",
        "version": __version__,
        "platform": "windows",
        "dpi_awareness": "per-monitor-v2",
        "window_identity": ["window_id", "pid", "process_created_at", "hwnd", "window_class"],
        "window_selectors": ["window_id", "pid", "process_name", "class_name", "title_regex"],
        "capture_modes": ["auto", "printwindow", "screen"],
        "input_modes": ["auto", "absolute", "relative"],
        "action_types": [
            "move",
            "click",
            "drag",
            "move_relative",
            "click_current",
            "mouse_down",
            "mouse_up",
            "wheel",
            "aim",
            "release_all",
        ],
        "coordinate_spaces": ["image_px", "client_px", "screen_px"],
        "input_enabled": settings.allow_input,
        "activation_enabled": settings.allow_activate,
        "session_ttl_seconds": settings.session_ttl_seconds,
        "safe_margin_px": settings.safe_margin_px,
        "inference_url": settings.inference_url,
        "security": ("Bind the adapter to loopback, use a dedicated token, and enable input injection deliberately."),
        "state": service.readiness(),
    }


@app.get("/api/v1/desktop/windows", tags=["Windows"])
async def windows(
    request: Request,
    query: str = Query(default="", max_length=200),
    pid: int | None = Query(default=None, gt=0),
    process_name: str = Query(default="", max_length=260),
    class_name: str = Query(default="", max_length=260),
    title_regex: str = Query(default="", max_length=260),
    include_hidden: bool = False,
    include_untitled: bool = False,
    include_tool_windows: bool = False,
    _: Annotated[None, Depends(require_api_key)] = None,
) -> dict[str, Any]:
    items = await run_in_threadpool(
        _service(request).list_windows,
        query=query,
        pid=pid,
        process_name=process_name,
        class_name=class_name,
        title_regex=title_regex,
        include_hidden=include_hidden,
        include_untitled=include_untitled,
        include_tool_windows=include_tool_windows,
    )
    return {"request_id": _request_id(request), "count": len(items), "windows": items}


@app.post("/api/v1/desktop/sessions", tags=["Sessions"], status_code=201)
async def create_session(
    request: Request,
    body: SessionCreateRequest,
    _: Annotated[None, Depends(require_api_key)] = None,
) -> dict[str, Any]:
    session = await run_in_threadpool(
        _service(request).create_session,
        window_id=body.window_id,
        pid=body.pid,
        process_name=body.process_name,
        class_name=body.class_name,
        title_regex=body.title_regex,
        input_mode=body.input_mode,
        relative_gain_x=body.relative_gain_x,
        relative_gain_y=body.relative_gain_y,
        relative_max_step=body.relative_max_step,
        relative_origin_fraction=body.relative_origin_fraction,
    )
    return {"request_id": _request_id(request), "session": session.metadata()}


@app.get("/api/v1/desktop/sessions/{session_id}", tags=["Sessions"])
async def get_session(
    request: Request,
    session_id: str,
    _: Annotated[None, Depends(require_api_key)] = None,
) -> dict[str, Any]:
    session = await run_in_threadpool(_service(request).get_session, session_id)
    return {"request_id": _request_id(request), "session": session.metadata()}


@app.post("/api/v1/desktop/sessions/{session_id}/capture", tags=["Capture"])
async def capture(
    request: Request,
    session_id: str,
    response_format: Literal["json", "png"] = "json",
    capture_mode: Literal["auto", "printwindow", "screen"] | None = None,
    _: Annotated[None, Depends(require_api_key)] = None,
) -> Response:
    frame = await run_in_threadpool(_service(request).capture, session_id, mode=capture_mode)
    headers = {
        "Cache-Control": "no-store",
        "X-Frame-ID": frame.frame_id,
        "X-Window-ID": frame.window_id,
        "X-Geometry-Hash": frame.geometry_hash,
        "X-Capture-Backend": frame.capture_backend,
        "X-Image-Width": str(frame.image_width),
        "X-Image-Height": str(frame.image_height),
    }
    if response_format == "png":
        headers["Content-Disposition"] = f'inline; filename="{frame.frame_id}.png"'
        return Response(content=frame.png_bytes, media_type="image/png", headers=headers)
    return JSONResponse(
        content={
            "request_id": _request_id(request),
            "frame": frame.metadata(),
            "image": {
                "mime_type": "image/png",
                "encoding": "base64",
                "byte_length": len(frame.png_bytes),
                "sha256": frame.png_sha256(),
                "data": base64.b64encode(frame.png_bytes).decode("ascii"),
            },
        },
        headers=headers,
    )


@app.post("/api/v1/desktop/sessions/{session_id}/analyze", tags=["Analysis"])
async def analyze(
    request: Request,
    session_id: str,
    body: AnalyzeRequest,
    _: Annotated[None, Depends(require_api_key)] = None,
) -> dict[str, Any]:
    payload = await run_in_threadpool(
        _service(request).analyze,
        session_id,
        include=body.include,
        icon_confidence=body.icon_confidence,
        ocr_confidence=body.ocr_confidence,
        capture_mode=body.capture_mode,
    )
    if "request_id" not in payload:
        payload["request_id"] = _request_id(request)
    return payload


@app.post("/api/v1/desktop/sessions/{session_id}/actions", tags=["Actions"])
async def actions(
    request: Request,
    session_id: str,
    body: ActionRequest,
    _: Annotated[None, Depends(require_api_key)] = None,
) -> dict[str, Any]:
    result = await run_in_threadpool(
        _service(request).execute_actions,
        session_id,
        frame_id=body.frame_id,
        actions=body.actions,
        dry_run=body.dry_run,
        activate=body.activate,
        release_at_end=body.release_at_end,
    )
    result["request_id"] = _request_id(request)
    return result


@app.post("/api/v1/desktop/sessions/{session_id}/release", tags=["Actions"])
async def release(
    request: Request,
    session_id: str,
    _: Annotated[None, Depends(require_api_key)] = None,
) -> dict[str, Any]:
    result = await run_in_threadpool(_service(request).release_all, session_id)
    result["request_id"] = _request_id(request)
    return result


@app.delete("/api/v1/desktop/sessions/{session_id}", tags=["Sessions"])
async def close_session(
    request: Request,
    session_id: str,
    _: Annotated[None, Depends(require_api_key)] = None,
) -> dict[str, Any]:
    result = await run_in_threadpool(_service(request).close_session, session_id)
    result["request_id"] = _request_id(request)
    return result


def main() -> None:
    import uvicorn

    uvicorn.run(
        "label_controllers.desktop.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level if settings.log_level != "trace" else "debug",
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
