"""MCP stdio bridge for the native Windows desktop adapter.

The MCP server deliberately contains no Win32 capture or coordinate logic. It
proxies the loopback desktop adapter, creating and always closing one analysis
session per tool call. stdout is reserved for newline-delimited JSON-RPC; all
diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from . import __version__
from .contracts import DEFAULT_INCLUDES, PUBLIC_INCLUDES

SERVER_NAME = "label-controllers-desktop-mcp"
SERVER_VERSION = __version__
DEFAULT_DESKTOP_URL = "http://127.0.0.1:8022"
DEFAULT_TIMEOUT_SECONDS = 180.0
MAX_WINDOWS_PER_PAGE = 500
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

TOOL_LIST_WINDOWS = "list_desktop_windows"
TOOL_ANALYZE_WINDOW = "analyze_desktop_window"

logger = logging.getLogger("label_controllers.mcp_server")


class ToolArgumentError(ValueError):
    """An MCP tool received invalid arguments."""

    def __init__(self, message: str, *, code: str = "invalid_arguments", details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


class DesktopAdapterError(RuntimeError):
    """A structured failure returned by the desktop adapter."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "desktop_adapter_error",
        status_code: int | None = None,
        request_id: str | None = None,
        details: Any = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.status_code is not None:
            error["status_code"] = self.status_code
        if self.details is not None:
            error["details"] = self.details
        result: dict[str, Any] = {"ok": False, "error": error}
        if self.request_id:
            result["request_id"] = self.request_id
        return result


class JsonRpcError(RuntimeError):
    """A protocol-level JSON-RPC error."""

    def __init__(self, code: int, message: str, *, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class DesktopAdapterClient:
    """Small synchronous client for the existing desktop-adapter HTTP API."""

    def __init__(
        self,
        base_url: str = DEFAULT_DESKTOP_URL,
        token: str = "",
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ):
        if not math.isfinite(timeout_seconds) or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least 1 second.")
        self.base_url = _normalize_desktop_url(base_url)
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.session.trust_env = False

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}",
        }
        if self.token:
            headers["X-API-Key"] = self.token
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                params=params,
                json=json_body,
                timeout=(5.0, self.timeout_seconds),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise DesktopAdapterError(
                f"Could not reach the Windows desktop adapter at {self.base_url}: {exc}",
                code="desktop_adapter_unavailable",
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise DesktopAdapterError(
                f"The desktop adapter returned HTTP {response.status_code} without JSON.",
                code="desktop_adapter_invalid_response",
                status_code=response.status_code,
            ) from exc

        if response.status_code >= 400:
            error, request_id = self._extract_error(payload, response.status_code)
            raise DesktopAdapterError(
                str(error.get("message") or f"The desktop adapter returned HTTP {response.status_code}."),
                code=str(error.get("code") or "desktop_adapter_http_error"),
                status_code=response.status_code,
                request_id=request_id,
                details=error.get("details"),
            )
        if not isinstance(payload, dict):
            raise DesktopAdapterError(
                "The desktop adapter returned a non-object JSON response.",
                code="desktop_adapter_invalid_response",
                status_code=response.status_code,
            )
        return payload

    @staticmethod
    def _extract_error(payload: Any, status_code: int) -> tuple[dict[str, Any], str | None]:
        if not isinstance(payload, dict):
            return (
                {
                    "code": "desktop_adapter_http_error",
                    "message": f"The desktop adapter returned HTTP {status_code}.",
                },
                None,
            )
        request_id = payload.get("request_id") if isinstance(payload.get("request_id"), str) else None
        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            return raw_error, request_id
        raw_detail = payload.get("detail")
        if isinstance(raw_detail, dict):
            return raw_detail, request_id
        if isinstance(raw_detail, list):
            first_message = ""
            if raw_detail and isinstance(raw_detail[0], dict):
                first_message = str(raw_detail[0].get("msg") or "")
            return (
                {
                    "code": "validation_error",
                    "message": first_message or "The desktop adapter rejected the request parameters.",
                    "details": {"validation": raw_detail},
                },
                request_id,
            )
        return (
            {
                "code": "desktop_adapter_http_error",
                "message": str(raw_detail) if raw_detail else f"The desktop adapter returned HTTP {status_code}.",
            },
            request_id,
        )

    def list_windows(self, params: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("GET", "/api/v1/desktop/windows", params=params)

    def create_session(self, selectors: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/desktop/sessions", json_body=selectors)

    def analyze(self, session_id: str, options: Mapping[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/desktop/sessions/{session_id}/analyze",
            json_body=options,
        )

    def close_session(self, session_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/api/v1/desktop/sessions/{session_id}")


def list_windows_tool() -> dict[str, Any]:
    return {
        "name": TOOL_LIST_WINDOWS,
        "title": "List Windows Desktop Windows",
        "description": (
            "List visible top-level windows on the interactive Windows desktop. Use this first when "
            "the target window is uncertain. Prefer passing the returned window_id to "
            "analyze_desktop_window. Filters combine as AND. A PID or process can own multiple windows; "
            "use class_name or title_regex to disambiguate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Case-insensitive substring search over title, process and window class.",
                },
                "pid": {"type": "integer", "minimum": 1, "description": "Exact Windows process ID."},
                "process_name": {
                    "type": "string",
                    "maxLength": 260,
                    "description": "Exact executable name, for example chrome.exe.",
                },
                "class_name": {
                    "type": "string",
                    "maxLength": 260,
                    "description": "Exact Win32 window class name.",
                },
                "title_regex": {
                    "type": "string",
                    "maxLength": 260,
                    "description": "Case-insensitive Python regular expression matched against the title.",
                },
                "include_hidden": {"type": "boolean", "default": False},
                "include_untitled": {"type": "boolean", "default": False},
                "include_tool_windows": {"type": "boolean", "default": False},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_WINDOWS_PER_PAGE,
                    "default": 100,
                    "description": "Maximum windows returned in this result; count still reports the total.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }


def analyze_window_tool() -> dict[str, Any]:
    selector_requirements = [
        {"required": ["window_id"]},
        {"required": ["pid"]},
        {"required": ["process_name"]},
        {"required": ["class_name"]},
        {"required": ["title_regex"]},
    ]
    return {
        "name": TOOL_ANALYZE_WINDOW,
        "title": "Analyze Windows Desktop Window",
        "description": (
            "Capture a bound Windows application's client area and run the Label Controllers vision "
            "pipeline. Returns clickable controls, OCR text and optional metadata/raw/annotated fields. "
            "Every control carries image_click, client_click, screen_click and coordinate_space. Prefer "
            "window_id from list_desktop_windows; a selector matching multiple windows fails rather than "
            "guessing. This tool does not inject mouse input. The temporary desktop session is closed "
            "before the result is returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "window_id": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 80,
                    "description": "Stable window ID returned by list_desktop_windows. Preferred selector.",
                },
                "pid": {"type": "integer", "minimum": 1},
                "process_name": {"type": "string", "maxLength": 260},
                "class_name": {"type": "string", "maxLength": 260},
                "title_regex": {"type": "string", "maxLength": 260},
                "include": {
                    "type": "array",
                    "items": {"type": "string", "enum": [*PUBLIC_INCLUDES, "all"]},
                    "uniqueItems": True,
                    "default": list(DEFAULT_INCLUDES),
                    "description": (
                        "Result groups. Default: controls,texts,metadata. Use annotated_image only when "
                        "an image is explicitly needed because it adds a Base64 PNG."
                    ),
                },
                "icon_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "ocr_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "capture_mode": {
                    "type": "string",
                    "enum": ["auto", "printwindow", "screen"],
                    "description": "Override the adapter capture strategy for this call.",
                },
            },
            "anyOf": selector_requirements,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }


def tool_definitions() -> list[dict[str, Any]]:
    return [list_windows_tool(), analyze_window_tool()]


class DesktopBridge:
    """Validated, model-facing tools backed by a DesktopAdapterClient."""

    def __init__(self, client: DesktopAdapterClient):
        self.client = client

    def call_tool(self, name: str, arguments: Any) -> dict[str, Any]:
        if name == TOOL_LIST_WINDOWS:
            return self._list_windows(arguments)
        if name == TOOL_ANALYZE_WINDOW:
            return self._analyze_window(arguments)
        raise ToolArgumentError(
            f"Unknown tool: {name}.",
            code="unknown_tool",
            details={"tools": [TOOL_LIST_WINDOWS, TOOL_ANALYZE_WINDOW]},
        )

    def _list_windows(self, arguments: Any) -> dict[str, Any]:
        values = _argument_object(arguments)
        _reject_unknown(
            values,
            {
                "query",
                "pid",
                "process_name",
                "class_name",
                "title_regex",
                "include_hidden",
                "include_untitled",
                "include_tool_windows",
                "limit",
            },
        )
        params: dict[str, Any] = {
            "query": _optional_string(values, "query", max_length=200),
            "process_name": _optional_string(values, "process_name", max_length=260),
            "class_name": _optional_string(values, "class_name", max_length=260),
            "title_regex": _optional_string(values, "title_regex", max_length=260),
            "include_hidden": _optional_bool(values, "include_hidden", False),
            "include_untitled": _optional_bool(values, "include_untitled", False),
            "include_tool_windows": _optional_bool(values, "include_tool_windows", False),
        }
        pid = _optional_positive_int(values, "pid")
        if pid is not None:
            params["pid"] = pid
        limit = _optional_positive_int(values, "limit", default=100, maximum=MAX_WINDOWS_PER_PAGE)
        payload = self.client.list_windows(params)
        windows = payload.get("windows")
        if not isinstance(windows, list):
            raise DesktopAdapterError(
                "The desktop adapter response did not contain a windows array.",
                code="desktop_adapter_invalid_response",
                request_id=_string_or_none(payload.get("request_id")),
            )
        total = payload.get("count")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            total = len(windows)
        returned = windows[:limit]
        result: dict[str, Any] = {
            "ok": True,
            "request_id": _string_or_none(payload.get("request_id")),
            "count": total,
            "returned": len(returned),
            "truncated": len(returned) < total,
            "windows": returned,
        }
        return result

    def _analyze_window(self, arguments: Any) -> dict[str, Any]:
        values = _argument_object(arguments)
        _reject_unknown(
            values,
            {
                "window_id",
                "pid",
                "process_name",
                "class_name",
                "title_regex",
                "include",
                "icon_confidence",
                "ocr_confidence",
                "capture_mode",
            },
        )
        selectors: dict[str, Any] = {}
        window_id = _optional_string(values, "window_id", max_length=80, min_length=3)
        if window_id:
            selectors["window_id"] = window_id
        pid = _optional_positive_int(values, "pid")
        if pid is not None:
            selectors["pid"] = pid
        for field in ("process_name", "class_name", "title_regex"):
            value = _optional_string(values, field, max_length=260)
            if value:
                selectors[field] = value
        if not selectors:
            raise ToolArgumentError(
                "Provide window_id or at least one of pid, process_name, class_name, title_regex.",
                code="window_selector_required",
                details={"selectors": ["window_id", "pid", "process_name", "class_name", "title_regex"]},
            )

        options: dict[str, Any] = {
            "include": ",".join(_include_fields(values.get("include"))),
        }
        icon_confidence = _optional_confidence(values, "icon_confidence")
        if icon_confidence is not None:
            options["icon_confidence"] = icon_confidence
        ocr_confidence = _optional_confidence(values, "ocr_confidence")
        if ocr_confidence is not None:
            options["ocr_confidence"] = ocr_confidence
        capture_mode = _optional_enum(values, "capture_mode", {"auto", "printwindow", "screen"})
        if capture_mode is not None:
            options["capture_mode"] = capture_mode

        created = self.client.create_session(selectors)
        session = created.get("session")
        if not isinstance(session, dict):
            raise DesktopAdapterError(
                "The desktop adapter did not return a session object.",
                code="desktop_adapter_invalid_response",
                request_id=_string_or_none(created.get("request_id")),
            )
        session_id = session.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise DesktopAdapterError(
                "The desktop adapter did not return a session_id.",
                code="desktop_adapter_invalid_response",
                request_id=_string_or_none(created.get("request_id")),
            )

        result: dict[str, Any] | None = None
        cleanup_error: DesktopAdapterError | None = None
        try:
            result = self.client.analyze(session_id, options)
        finally:
            try:
                self.client.close_session(session_id)
            except DesktopAdapterError as exc:
                if exc.status_code != 404:
                    cleanup_error = exc
                    logger.warning("Could not close desktop session %s after analysis: %s", session_id, exc.code)
            except Exception:
                cleanup_error = DesktopAdapterError(
                    "The temporary desktop session could not be closed cleanly.",
                    code="session_cleanup_failed",
                )
                logger.exception("Unexpected failure while closing desktop session %s", session_id)

        assert result is not None
        if not isinstance(result, dict):
            raise DesktopAdapterError(
                "The desktop adapter returned a non-object analysis response.",
                code="desktop_adapter_invalid_response",
            )
        if "ok" not in result:
            result["ok"] = True
        details: dict[str, Any] = {
            "tool": TOOL_ANALYZE_WINDOW,
            "session_closed": cleanup_error is None,
        }
        if cleanup_error is not None:
            details["cleanup_error"] = {
                "code": cleanup_error.code,
                "message": str(cleanup_error),
            }
        result["mcp"] = details
        return result


def _argument_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ToolArgumentError("Tool arguments must be a JSON object.", code="invalid_arguments")
    return dict(value)


def _reject_unknown(values: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ToolArgumentError(
            f"Unknown tool argument(s): {', '.join(unknown)}.",
            code="invalid_arguments",
            details={"allowed": sorted(allowed)},
        )


def _optional_string(
    values: Mapping[str, Any],
    name: str,
    *,
    max_length: int,
    min_length: int = 0,
) -> str:
    value = values.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolArgumentError(f"{name} must be a string.", code="invalid_arguments")
    value = value.strip()
    if value and len(value) < min_length:
        raise ToolArgumentError(f"{name} must contain at least {min_length} characters.", code="invalid_arguments")
    if len(value) > max_length:
        raise ToolArgumentError(f"{name} must contain at most {max_length} characters.", code="invalid_arguments")
    return value


def _optional_bool(values: Mapping[str, Any], name: str, default: bool) -> bool:
    value = values.get(name, default)
    if not isinstance(value, bool):
        raise ToolArgumentError(f"{name} must be a boolean.", code="invalid_arguments")
    return value


def _optional_positive_int(
    values: Mapping[str, Any],
    name: str,
    *,
    default: int | None = None,
    maximum: int | None = None,
) -> int | None:
    value = values.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolArgumentError(f"{name} must be a positive integer.", code="invalid_arguments")
    if maximum is not None and value > maximum:
        raise ToolArgumentError(f"{name} must be at most {maximum}.", code="invalid_arguments")
    return value


def _optional_confidence(values: Mapping[str, Any], name: str) -> float | None:
    value = values.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ToolArgumentError(f"{name} must be a number between 0 and 1.", code="invalid_arguments")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ToolArgumentError(f"{name} must be between 0 and 1.", code="invalid_arguments")
    return number


def _optional_enum(values: Mapping[str, Any], name: str, allowed: set[str]) -> str | None:
    value = values.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise ToolArgumentError(
            f"{name} must be one of: {', '.join(sorted(allowed))}.",
            code="invalid_arguments",
        )
    return value


def _include_fields(value: Any) -> list[str]:
    if value is None:
        return list(DEFAULT_INCLUDES)
    if isinstance(value, str):
        fields = [field.strip().casefold() for field in value.split(",") if field.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        fields = [str(field).strip().casefold() for field in value if str(field).strip()]
    else:
        raise ToolArgumentError(
            "include must be an array of strings or a comma-separated string.",
            code="invalid_arguments",
        )
    if not fields:
        raise ToolArgumentError("include cannot be empty.", code="invalid_arguments")
    if "all" in fields:
        return ["all"]
    invalid = sorted(set(fields) - set(PUBLIC_INCLUDES))
    if invalid:
        raise ToolArgumentError(
            f"Unknown include field(s): {', '.join(invalid)}.",
            code="invalid_arguments",
            details={"allowed": [*PUBLIC_INCLUDES, "all"]},
        )
    return list(dict.fromkeys(fields))


def _normalize_desktop_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("The desktop adapter URL must be an absolute http:// or https:// URL.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Credentials must not be embedded in the desktop adapter URL.")
    if parts.query or parts.fragment:
        raise ValueError("The desktop adapter URL must not contain a query string or fragment.")
    return raw


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


class McpServer:
    """Protocol dispatch independent of stdin/stdout for straightforward testing."""

    def __init__(self, bridge: DesktopBridge):
        self.bridge = bridge
        self.initialized = False

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _jsonrpc_error(None, -32600, "Invalid Request")
        notification = "id" not in message
        request_id = message.get("id")
        method = message.get("method")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return None if notification else _jsonrpc_error(request_id, -32600, "Invalid Request")

        try:
            if method == "initialize":
                result = self._initialize(message.get("params"))
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tool_definitions()}
            elif method == "tools/call":
                result = self._tools_call(message.get("params"))
            elif method.startswith("notifications/"):
                return None
            else:
                raise JsonRpcError(-32601, f"Method not found: {method}")
        except JsonRpcError as exc:
            if notification:
                return None
            return _jsonrpc_error(request_id, exc.code, str(exc), data=exc.data)
        except Exception:  # Keep malformed/corrupt inputs from killing the stdio process.
            logger.exception("Unhandled MCP request failure")
            if notification:
                return None
            return _jsonrpc_error(request_id, -32603, "Internal error")

        if notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _initialize(self, params: Any) -> dict[str, Any]:
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise JsonRpcError(-32602, "initialize params must be an object")
        requested = params.get("protocolVersion")
        protocol_version = (
            requested
            if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        client_info = params.get("clientInfo")
        client_name = client_info.get("name") if isinstance(client_info, dict) else "unknown"
        logger.info("MCP client initialized: %s (protocol %s)", client_name, protocol_version)
        self.initialized = True
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Use list_desktop_windows to find a target, then analyze_desktop_window with the "
                "returned window_id. Coordinates are embedded in each control; this server never injects input."
            ),
        }

    def _tools_call(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise JsonRpcError(-32602, "tools/call params must be an object")
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(-32602, "tools/call requires a non-empty tool name")
        arguments = params.get("arguments", {})
        logger.info("MCP tool call: %s", name)
        try:
            payload = self.bridge.call_tool(name, arguments)
        except (ToolArgumentError, DesktopAdapterError) as exc:
            if isinstance(exc, ToolArgumentError):
                error = {
                    "ok": False,
                    "error": {"code": exc.code, "message": str(exc)},
                }
                if exc.details is not None:
                    error["error"]["details"] = exc.details
            else:
                error = exc.to_dict()
            return _tool_result(error, is_error=True)
        except Exception:
            logger.exception("Unexpected MCP tool failure: %s", name)
            return _tool_result(
                {
                    "ok": False,
                    "error": {
                        "code": "internal_error",
                        "message": "An unexpected error occurred while executing the tool.",
                    },
                },
                is_error=True,
            )
        return _tool_result(payload, is_error=False)


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    data: Any = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _tool_result(payload: Any, *, is_error: bool) -> dict[str, Any]:
    text_payload = payload
    image_block: dict[str, Any] | None = None
    if not is_error and isinstance(payload, dict):
        annotated = payload.get("annotated_image")
        if isinstance(annotated, dict):
            data = annotated.get("data")
            mime_type = annotated.get("mime_type") or annotated.get("mimeType")
            encoding = annotated.get("encoding")
            if (
                isinstance(data, str)
                and data
                and isinstance(mime_type, str)
                and mime_type.startswith("image/")
                and encoding == "base64"
            ):
                text_payload = dict(payload)
                summary = {
                    key: annotated[key]
                    for key in ("mime_type", "encoding", "byte_length", "sha256")
                    if key in annotated
                }
                summary["transport"] = "mcp_image_content"
                summary["content_block_index"] = 1
                text_payload["annotated_image"] = summary
                image_block = {"type": "image", "data": data, "mimeType": mime_type}

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": json.dumps(text_payload, ensure_ascii=False, separators=(",", ":")),
        }
    ]
    if image_block is not None:
        content.append(image_block)
    return {"content": content, "isError": is_error}


def serve_stdio(server: McpServer, stdin: Any = None, stdout: Any = None) -> int:
    """Serve newline-delimited JSON-RPC until EOF."""

    input_stream = stdin or sys.stdin.buffer
    output_stream = stdout or sys.stdout.buffer
    logger.info("MCP stdio server ready; desktop adapter=%s", server.bridge.client.base_url)
    while True:
        raw = input_stream.readline()
        if not raw:
            break
        if not raw.strip():
            continue
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            response = _jsonrpc_error(None, -32700, f"Parse error: {exc}")
        else:
            if isinstance(message, list):
                if not message:
                    response = _jsonrpc_error(None, -32600, "Invalid Request")
                else:
                    responses = [server.handle_message(item) for item in message]
                    response = [item for item in responses if item is not None]
                    if not response:
                        response = None
            else:
                response = server.handle_message(message)

        if response is None:
            continue
        try:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            output_stream.flush()
        except (BrokenPipeError, OSError):
            logger.info("MCP output pipe closed")
            return 0
    logger.info("MCP stdio server stopped")
    return 0


def _resolve_token() -> str:
    direct = (
        os.getenv("LABEL_CONTROLLERS_MCP_DESKTOP_TOKEN", "").strip()
        or os.getenv("LABEL_CONTROLLERS_DESKTOP_TOKEN", "").strip()
        or os.getenv("LABEL_CONTROLLERS_API_KEY", "").strip()
    )
    if direct:
        return direct
    token_file = os.getenv("LABEL_CONTROLLERS_MCP_TOKEN_FILE", "").strip()
    if token_file:
        try:
            return Path(token_file).expanduser().read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            raise ValueError(f"Could not read LABEL_CONTROLLERS_MCP_TOKEN_FILE: {exc}") from exc
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="label-controllers-mcp",
        description="MCP stdio bridge for the Label Controllers native Windows desktop adapter.",
    )
    parser.add_argument(
        "--desktop-url",
        default=os.getenv("LABEL_CONTROLLERS_MCP_DESKTOP_URL", DEFAULT_DESKTOP_URL),
        help="Desktop adapter base URL (default: env or http://127.0.0.1:8022)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("LABEL_CONTROLLERS_MCP_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        help="Analysis HTTP read timeout in seconds",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LABEL_CONTROLLERS_MCP_LOG_LEVEL", "info"),
        choices=["critical", "error", "warning", "info", "debug"],
    )
    parser.add_argument("--print-tools", action="store_true", help="Print tool definitions and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {SERVER_VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.print_tools:
        print(json.dumps({"tools": tool_definitions()}, ensure_ascii=False, indent=2))
        return 0

    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        args.desktop_url = _normalize_desktop_url(args.desktop_url)
    except ValueError as exc:
        parser.error(str(exc))
    if not math.isfinite(args.timeout) or args.timeout < 1:
        parser.error("--timeout must be at least 1 second")

    try:
        token = _resolve_token()
    except ValueError as exc:
        parser.error(str(exc))
    if not token:
        parser.error(
            "A desktop token is required. Set LABEL_CONTROLLERS_MCP_DESKTOP_TOKEN, "
            "LABEL_CONTROLLERS_DESKTOP_TOKEN, LABEL_CONTROLLERS_API_KEY, or "
            "LABEL_CONTROLLERS_MCP_TOKEN_FILE."
        )

    client = DesktopAdapterClient(args.desktop_url, token, timeout_seconds=args.timeout)
    server = McpServer(DesktopBridge(client))
    try:
        return serve_stdio(server)
    except KeyboardInterrupt:
        logger.info("MCP server interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
