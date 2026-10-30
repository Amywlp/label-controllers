"""Minimal MCP (Model Context Protocol) server over stdio.

Exposes the same two capabilities as the HTTP API so MCP-capable hosts can
drive UI automation without writing their own client:

* ``analyze_ui_screenshot`` - vision + optional accessibility merge for one frame
* ``probe_ui_accessibility`` - grade an a11y tree before automating

Transport: newline-delimited JSON-RPC 2.0 on stdin/stdout (MCP stdio framing).
All diagnostics go to stderr; stdout carries protocol messages only.

Configuration (environment):

* ``LABEL_CONTROLLERS_BASE_URL`` - default ``http://127.0.0.1:8000``
* ``LABEL_CONTROLLERS_API_KEY``  - optional bearer/X-API-Key value
* ``LABEL_CONTROLLERS_IMAGE``    - optional default screenshot path for hosts
  that cannot pass ``image_base64``/``image_path`` themselves
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .tool import (
    A11Y_TOOL_NAME,
    TOOL_NAME,
    LabelControllersTool,
    ToolCallError,
    anthropic_a11y_tool_schema,
    anthropic_tool_schema,
)

SERVER_NAME = "label-controllers"
SERVER_VERSION = "0.4.0"
PROTOCOL_VERSION = "2024-11-05"

# MCP needs the screenshot to travel as data, so the tool schema is the HTTP
# schema plus the two image carriers the MCP host can actually use.
IMAGE_ARGUMENTS: dict[str, Any] = {
    "image_base64": {
        "type": "string",
        "description": "Screenshot bytes as Base64 or a data URL.",
    },
    "image_path": {
        "type": "string",
        "description": "Path to a screenshot on the MCP server filesystem.",
    },
}


def _log(message: str) -> None:
    print(f"[{SERVER_NAME}] {message}", file=sys.stderr, flush=True)


def _tool_definitions() -> list[dict[str, Any]]:
    analyze = anthropic_tool_schema()
    analyze["input_schema"]["properties"].update(IMAGE_ARGUMENTS)

    probe = anthropic_a11y_tool_schema()
    return [analyze, probe]


def _result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _resolve_image(arguments: dict[str, Any]) -> str | bytes | None:
    image_base64 = arguments.get("image_base64")
    if image_base64:
        return str(image_base64)
    image_path = arguments.get("image_path") or os.getenv("LABEL_CONTROLLERS_IMAGE")
    if image_path:
        path = Path(str(image_path)).expanduser()
        if not path.is_file():
            raise ToolCallError(f"Screenshot not found: {path}", code="image_required")
        return path.read_bytes()
    return None


def _call_tool(name: str, arguments: dict[str, Any], tool: LabelControllersTool) -> dict[str, Any]:
    if name == TOOL_NAME:
        image = _resolve_image(arguments)
        if image is None:
            raise ToolCallError(
                "Pass image_base64 or image_path, or set LABEL_CONTROLLERS_IMAGE.",
                code="image_required",
            )
        result = tool.analyze(
            image,
            include=arguments.get("include"),
            icon_confidence=arguments.get("icon_confidence"),
            ocr_confidence=arguments.get("ocr_confidence"),
            a11y=arguments.get("a11y"),
        )
        return _result(result)

    if name == A11Y_TOOL_NAME:
        payload: dict[str, Any] = {}
        if arguments.get("elements") is not None:
            payload["elements"] = arguments["elements"]
        else:
            payload["cdp_url"] = arguments.get("cdp_url")
        result = tool.probe_a11y(
            cdp_url=payload.get("cdp_url"),
            target=int(arguments.get("target") or 0),
            url=arguments.get("url"),
            timeout_seconds=float(arguments.get("timeout_seconds") or 20.0),
            elements=payload.get("elements"),
        )
        return _result(result)

    raise ToolCallError(f"Unknown tool: {name}", code="unknown_tool")


def _handle(request: dict[str, Any], tool: LabelControllersTool) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in {"notifications/initialized", "initialized"}:
        return None  # notification: no response

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": definition["name"],
                        "description": definition["description"],
                        "inputSchema": definition["input_schema"],
                    }
                    for definition in _tool_definitions()
                ]
            },
        }

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _result("arguments must be an object", is_error=True),
            }
        try:
            return {"jsonrpc": "2.0", "id": request_id, "result": _call_tool(name, arguments, tool)}
        except ToolCallError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": _result(exc.to_dict(), is_error=True)}
        except Exception as exc:  # noqa: BLE001 - MCP must answer, never crash
            _log(f"tool error: {exc}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _result({"ok": False, "error": {"message": str(exc)}}, is_error=True),
            }

    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    tool = LabelControllersTool(
        base_url=os.getenv("LABEL_CONTROLLERS_BASE_URL"),
        api_key=os.getenv("LABEL_CONTROLLERS_API_KEY"),
    )
    _log(f"ready: base_url={tool.base_url}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            print(
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}),
                flush=True,
            )
            continue
        if not isinstance(request, dict):
            continue
        response = _handle(request, tool)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
