"""OpenAI-compatible tool definition and executor for Label Controllers.

The LLM chooses ``analyze_ui_screenshot``. The tool host binds the current
screenshot through ``screenshot_provider`` or an explicit ``image`` argument;
the model never needs to emit raw image bytes.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import requests

from .contracts import DEFAULT_INCLUDES, PUBLIC_INCLUDES

TOOL_NAME = "analyze_ui_screenshot"
TOOL_DESCRIPTION = (
    "Analyze the current UI screenshot and return clickable controls, recognized text, "
    "semantic roles and exact screenshot coordinates. The tool host supplies the current "
    "screenshot to the executor; do not generate or pass image bytes from the model. "
    "Use this before clicking or navigating a graphical application. When the target is a "
    "web page or an Electron app that exposes a DevTools endpoint, pass the optional "
    "'a11y' argument so accessibility-tree names and boxes are merged with the vision result."
)
A11Y_TOOL_NAME = "probe_ui_accessibility"
A11Y_TOOL_DESCRIPTION = (
    "Grade whether a live page or Electron window exposes a usable accessibility tree before "
    "automating it. Connects to a Chrome DevTools Protocol endpoint (or accepts a pre-collected "
    "tree), reports interactive node counts, naming and coordinate coverage, and recommends "
    "'a11y_primary', 'hybrid' or 'vision_only'."
)
ALLOWED_ARGUMENTS = {"include", "icon_confidence", "ocr_confidence", "annotate", "a11y"}
ALLOWED_A11Y_KEYS = {"mode", "cdp_url", "target", "url", "timeout_seconds", "elements", "node_count"}


class ToolCallError(RuntimeError):
    """Structured error raised by the local tool executor."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "tool_call_failed",
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
        payload: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": self.code,
                "message": str(self),
            },
        }
        if self.status_code is not None:
            payload["error"]["status_code"] = self.status_code
        if self.request_id:
            payload["request_id"] = self.request_id
        if self.details is not None:
            payload["error"]["details"] = self.details
        return payload


def openai_tool_schema(name: str = TOOL_NAME) -> dict[str, Any]:
    """Return a standard OpenAI Chat Completions/Responses function tool."""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "include": {
                        "type": "array",
                        "description": (
                            "Result groups to return. Use controls for clickable targets, elements for "
                            "all fused elements, texts for OCR, metadata for runtime/timing details, "
                            "or annotated_image for a Base64-rendered PNG. Default: controls,texts,metadata."
                        ),
                        "items": {
                            "type": "string",
                            "enum": [*PUBLIC_INCLUDES, "all"],
                        },
                        "default": list(DEFAULT_INCLUDES),
                    },
                    "annotate": {
                        "type": "boolean",
                        "description": "Include a Base64 annotated PNG in the JSON result.",
                        "default": False,
                    },
                    "icon_confidence": {
                        "type": "number",
                        "description": "Optional YOLO icon confidence threshold.",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "ocr_confidence": {
                        "type": "number",
                        "description": "Optional OCR confidence threshold.",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "a11y": {
                        "type": "object",
                        "description": (
                            "Optional accessibility-tree channel. Use mode='cdp' with cdp_url when a "
                            "Chrome/Electron DevTools endpoint is reachable, or mode='inline' with "
                            "pre-collected elements. A matched control gains a11y_role/a11y_name/a11y_rect, "
                            "unnamed icons receive semantic names, and a11y-only nodes are appended as "
                            "clickable elements."
                        ),
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": ["off", "cdp", "inline"],
                                "default": "cdp",
                            },
                            "cdp_url": {
                                "type": "string",
                                "description": "DevTools endpoint, for example http://127.0.0.1:9222.",
                            },
                            "target": {
                                "type": "integer",
                                "description": "Index into the page targets reported by /json/list.",
                                "minimum": 0,
                                "default": 0,
                            },
                            "url": {
                                "type": "string",
                                "description": "Optional page to navigate before the tree is read.",
                            },
                            "timeout_seconds": {
                                "type": "number",
                                "minimum": 1,
                                "maximum": 120,
                                "default": 20,
                            },
                            "elements": {
                                "type": "array",
                                "description": "Inline accessibility nodes, used when mode='inline'.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "role": {"type": "string"},
                                        "name": {"type": "string"},
                                        "rect": {
                                            "type": "array",
                                            "items": {"type": "number"},
                                            "minItems": 4,
                                            "maxItems": 4,
                                        },
                                    },
                                    "required": ["role"],
                                },
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
            "strict": False,
        },
    }


def anthropic_tool_schema(name: str = TOOL_NAME) -> dict[str, Any]:
    """Return the same tool in Anthropic Messages tool format."""

    openai_schema = openai_tool_schema(name)
    function = openai_schema["function"]
    return {
        "name": function["name"],
        "description": function["description"],
        "input_schema": function["parameters"],
    }


def openai_a11y_tool_schema(name: str = A11Y_TOOL_NAME) -> dict[str, Any]:
    """Tool that grades accessibility-tree usability before automation starts."""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": A11Y_TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["cdp", "inline"],
                        "default": "cdp",
                    },
                    "cdp_url": {
                        "type": "string",
                        "description": "DevTools endpoint, for example http://127.0.0.1:9222.",
                    },
                    "target": {"type": "integer", "minimum": 0, "default": 0},
                    "url": {
                        "type": "string",
                        "description": "Optional page to navigate before the tree is read.",
                    },
                    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120, "default": 20},
                    "elements": {
                        "type": "array",
                        "description": "Inline accessibility nodes, used when mode='inline'.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "name": {"type": "string"},
                                "rect": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "minItems": 4,
                                    "maxItems": 4,
                                },
                            },
                            "required": ["role"],
                        },
                    },
                },
                "additionalProperties": False,
            },
            "strict": False,
        },
    }


def anthropic_a11y_tool_schema(name: str = A11Y_TOOL_NAME) -> dict[str, Any]:
    """Return the a11y probe tool in Anthropic Messages tool format."""

    function = openai_a11y_tool_schema(name)["function"]
    return {
        "name": function["name"],
        "description": function["description"],
        "input_schema": function["parameters"],
    }


def _decode_base64_image(value: str) -> bytes:
    encoded = value.strip()
    if encoded.startswith("data:"):
        _, separator, encoded = encoded.partition(",")
        if not separator:
            raise ToolCallError("Malformed data URL.", code="invalid_image")
    encoded = "".join(encoded.split())
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ToolCallError("image_base64 is not valid Base64 data.", code="invalid_image") from exc
    if not image:
        raise ToolCallError("The screenshot is empty.", code="invalid_image")
    return image


def _coerce_bytes(value: str | bytes | bytearray | Path) -> bytes:
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    if isinstance(value, Path):
        return value.read_bytes()
    if isinstance(value, str):
        return Path(value).expanduser().read_bytes()
    raise ToolCallError("Unsupported image source.", code="invalid_image")


class LabelControllersTool:
    """Execute the standard tool against a running Label Controllers API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        screenshot_provider: Callable[[], bytes | bytearray] | None = None,
        *,
        timeout_seconds: float = 120.0,
        session: requests.Session | None = None,
    ):
        self.base_url = (base_url or os.getenv("LABEL_CONTROLLERS_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("LABEL_CONTROLLERS_API_KEY", "")
        self.screenshot_provider = screenshot_provider
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @property
    def definition(self) -> dict[str, Any]:
        return openai_tool_schema()

    @property
    def anthropic_definition(self) -> dict[str, Any]:
        return anthropic_tool_schema()

    def analyze(
        self,
        image: str | bytes | bytearray | Path,
        *,
        include: Sequence[str] | str | None = None,
        icon_confidence: float | None = None,
        ocr_confidence: float | None = None,
        filename: str | None = None,
        a11y: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        image_bytes = _coerce_bytes(image)
        include_fields = self._normalize_include(include)
        if icon_confidence is not None:
            self._validate_confidence("icon_confidence", icon_confidence)
        if ocr_confidence is not None:
            self._validate_confidence("ocr_confidence", ocr_confidence)
        if a11y is not None:
            a11y = self._validate_a11y(a11y)
            # Make the merge visible by default: without "alignment" the caller
            # cannot tell which controls actually matched the a11y tree.
            for field_name in ("alignment", "a11y"):
                if "all" not in include_fields and field_name not in include_fields:
                    include_fields.append(field_name)

        upload_name = filename or "screenshot.png"
        content_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        headers = {"Accept": "application/json", "X-Request-ID": f"tool-{os.urandom(8).hex()}"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        data = {"include": ",".join(include_fields)}
        if icon_confidence is not None:
            data["icon_confidence"] = str(icon_confidence)
        if ocr_confidence is not None:
            data["ocr_confidence"] = str(ocr_confidence)
        if a11y is not None:
            data["a11y"] = json.dumps(a11y, ensure_ascii=False)
        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/analyze",
                headers=headers,
                files={"file": (upload_name, image_bytes, content_type)},
                data=data,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise ToolCallError(
                f"Could not reach Label Controllers at {self.base_url}: {exc}",
                code="connection_error",
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolCallError(
                f"Label Controllers returned HTTP {response.status_code} without JSON.",
                code="invalid_response",
                status_code=response.status_code,
            ) from exc
        if response.status_code >= 400:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise ToolCallError(
                str(error.get("message") or f"Label Controllers returned HTTP {response.status_code}."),
                code=str(error.get("code") or "http_error"),
                status_code=response.status_code,
                request_id=payload.get("request_id") if isinstance(payload, dict) else None,
                details=error.get("details"),
            )
        if not isinstance(payload, dict):
            raise ToolCallError("Label Controllers returned an unexpected JSON value.", code="invalid_response")
        return payload

    def analyze_base64(
        self,
        image_base64: str,
        *,
        include: Sequence[str] | str | None = None,
        icon_confidence: float | None = None,
        ocr_confidence: float | None = None,
        filename: str = "screenshot.png",
        a11y: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze image bytes encoded as standard Base64 or a data URL."""

        return self.analyze(
            _decode_base64_image(image_base64),
            include=include,
            icon_confidence=icon_confidence,
            ocr_confidence=ocr_confidence,
            filename=filename,
            a11y=a11y,
        )

    def execute_tool_call(
        self,
        arguments: Mapping[str, Any] | str | None = None,
        *,
        image: str | bytes | bytearray | Path | None = None,
    ) -> dict[str, Any]:
        """Execute arguments returned by a model in a standard tool call."""

        parsed = self._parse_arguments(arguments)
        unknown = sorted(set(parsed) - ALLOWED_ARGUMENTS)
        if unknown:
            raise ToolCallError(
                f"Unknown tool argument(s): {', '.join(unknown)}.",
                code="invalid_arguments",
                details={"allowed": sorted(ALLOWED_ARGUMENTS)},
            )
        annotate = parsed.get("annotate", False)
        if not isinstance(annotate, bool):
            raise ToolCallError("annotate must be a boolean.", code="invalid_arguments")
        selected_include = parsed.get("include")
        if annotate and "annotated_image" not in self._as_include_list(selected_include):
            selected_include = [*self._as_include_list(selected_include), "annotated_image"]

        if image is None:
            if self.screenshot_provider is None:
                raise ToolCallError(
                    "No screenshot was bound to the tool executor. Pass image=... or configure screenshot_provider.",
                    code="image_required",
                )
            try:
                image_bytes = self.screenshot_provider()
            except Exception as exc:
                raise ToolCallError(
                    f"The screenshot provider failed: {exc}",
                    code="screenshot_provider_error",
                ) from exc
        else:
            image_bytes = image
        a11y_spec = parsed.get("a11y")
        if a11y_spec is not None and not isinstance(a11y_spec, Mapping):
            raise ToolCallError("a11y must be a JSON object.", code="invalid_arguments")
        return self.analyze(
            image_bytes,
            include=selected_include,
            icon_confidence=parsed.get("icon_confidence"),
            ocr_confidence=parsed.get("ocr_confidence"),
            a11y=a11y_spec,
        )

    def probe_a11y(
        self,
        *,
        cdp_url: str | None = None,
        target: int = 0,
        url: str | None = None,
        timeout_seconds: float = 20.0,
        elements: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Ask the service whether an a11y tree is usable (no screenshot needed)."""

        if elements is not None:
            payload: dict[str, Any] = {"mode": "inline", "elements": list(elements)}
        else:
            if not cdp_url:
                raise ToolCallError("probe_a11y needs cdp_url or elements.", code="invalid_arguments")
            payload = {
                "mode": "cdp",
                "cdp_url": cdp_url,
                "target": int(target),
                "timeout_seconds": float(timeout_seconds),
            }
            if url:
                payload["url"] = url

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/a11y/probe",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise ToolCallError(
                f"Could not reach Label Controllers at {self.base_url}: {exc}",
                code="connection_error",
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise ToolCallError(
                f"Label Controllers returned HTTP {response.status_code} without JSON.",
                code="invalid_response",
                status_code=response.status_code,
            ) from exc
        if response.status_code >= 400:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise ToolCallError(
                str(error.get("message") or f"Label Controllers returned HTTP {response.status_code}."),
                code=str(error.get("code") or "http_error"),
                status_code=response.status_code,
                details=error.get("details"),
            )
        if not isinstance(body, dict):
            raise ToolCallError("Label Controllers returned an unexpected JSON value.", code="invalid_response")
        return body

    @staticmethod
    def _validate_a11y(spec: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(spec, Mapping):
            raise ToolCallError("a11y must be a JSON object.", code="invalid_arguments")
        unknown = sorted(set(spec) - ALLOWED_A11Y_KEYS)
        if unknown:
            raise ToolCallError(
                f"Unknown a11y key(s): {', '.join(unknown)}.",
                code="invalid_arguments",
                details={"allowed": sorted(ALLOWED_A11Y_KEYS)},
            )
        fallback = "inline" if spec.get("elements") else "off"
        mode = str(spec.get("mode") or ("cdp" if spec.get("cdp_url") else fallback))
        if mode not in {"off", "cdp", "inline"}:
            raise ToolCallError("a11y.mode must be one of: off, cdp, inline.", code="invalid_arguments")
        if mode == "cdp" and not spec.get("cdp_url"):
            raise ToolCallError("a11y.mode='cdp' requires a11y.cdp_url.", code="invalid_arguments")
        if mode == "inline" and not spec.get("elements"):
            raise ToolCallError("a11y.mode='inline' requires a11y.elements.", code="invalid_arguments")
        return dict(spec, mode=mode)

    @staticmethod
    def _parse_arguments(arguments: Mapping[str, Any] | str | None) -> dict[str, Any]:
        if arguments is None:
            return {}
        if isinstance(arguments, str):
            try:
                value = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ToolCallError("Tool arguments are not valid JSON.", code="invalid_arguments") from exc
        else:
            value = dict(arguments)
        if not isinstance(value, dict):
            raise ToolCallError("Tool arguments must be a JSON object.", code="invalid_arguments")
        return value

    @staticmethod
    def _as_include_list(include: Any) -> list[str]:
        if include is None:
            return list(DEFAULT_INCLUDES)
        if isinstance(include, str):
            return [item.strip().casefold() for item in include.split(",") if item.strip()]
        if isinstance(include, Sequence) and not isinstance(include, bytes | bytearray):
            return [str(item).strip().casefold() for item in include if str(item).strip()]
        raise ToolCallError(
            "include must be an array of strings or a comma-separated string.",
            code="invalid_arguments",
        )

    @classmethod
    def _normalize_include(cls, include: Sequence[str] | str | None) -> list[str]:
        fields = cls._as_include_list(include)
        if not fields:
            raise ToolCallError("include cannot be empty.", code="invalid_arguments")
        if "all" in fields:
            return ["all"]
        invalid = sorted(set(fields) - set(PUBLIC_INCLUDES))
        if invalid:
            raise ToolCallError(
                f"Unknown include field(s): {', '.join(invalid)}.",
                code="invalid_arguments",
                details={"allowed": [*PUBLIC_INCLUDES, "all"]},
            )
        return list(dict.fromkeys(fields))

    @staticmethod
    def _validate_confidence(name: str, value: Any) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ToolCallError(f"{name} must be a number between 0 and 1.", code="invalid_arguments") from exc
        if not 0.0 <= number <= 1.0:
            raise ToolCallError(f"{name} must be between 0 and 1.", code="invalid_arguments")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="label-controllers-tool",
        description="Standard OpenAI/Anthropic-compatible tool wrapper for Label Controllers.",
    )
    parser.add_argument("--schema", action="store_true", help="Print the OpenAI-compatible JSON tool schema and exit")
    parser.add_argument("--base-url", default=os.getenv("LABEL_CONTROLLERS_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--image", help="Local screenshot path for a direct CLI tool execution")
    parser.add_argument("--include", default=",".join(DEFAULT_INCLUDES))
    parser.add_argument("--icon-confidence", type=float)
    parser.add_argument("--ocr-confidence", type=float)
    parser.add_argument("--output", help="Write the JSON result to this path instead of stdout")
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(openai_tool_schema(), ensure_ascii=False, indent=2))
        return 0
    if not args.image:
        parser.error("--image is required unless --schema is used")

    tool = LabelControllersTool(
        base_url=args.base_url,
        timeout_seconds=args.timeout,
        screenshot_provider=lambda: Path(args.image).expanduser().read_bytes(),
    )
    try:
        result = tool.execute_tool_call(
            {
                "include": args.include.split(","),
                "icon_confidence": args.icon_confidence,
                "ocr_confidence": args.ocr_confidence,
            }
        )
    except (OSError, ToolCallError) as exc:
        structured = exc.to_dict() if isinstance(exc, ToolCallError) else {"ok": False, "error": {"message": str(exc)}}
        print(json.dumps(structured, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
