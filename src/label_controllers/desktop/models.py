"""Shared desktop-adapter data structures and safe coordinate mapping."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image


class DesktopError(RuntimeError):
    """A structured Windows desktop-adapter failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "desktop_error",
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def as_dict(self) -> dict[str, int]:
        return {
            "x": self.left,
            "y": self.top,
            "width": self.width,
            "height": self.height,
            "right": self.right,
            "bottom": self.bottom,
        }

    def same_geometry(self, other: Rect) -> bool:
        return self == other


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    window_id: str
    hwnd: int = field(repr=False)
    pid: int
    process_name: str
    process_created_at: float
    class_name: str
    title: str

    def same_target(self, other: WindowIdentity) -> bool:
        return (
            self.window_id == other.window_id
            and self.hwnd == other.hwnd
            and self.pid == other.pid
            and self.process_created_at == other.process_created_at
            and self.class_name == other.class_name
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "pid": self.pid,
            "process": self.process_name,
            "class": self.class_name,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class CursorState:
    x: int
    y: int
    visible: bool
    clipped: bool
    clip_rect: Rect

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": [self.x, self.y],
            "visible": self.visible,
            "clipped": self.clipped,
            "clip_rect": self.clip_rect.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class WindowInfo:
    identity: WindowIdentity
    client_rect: Rect
    dpi: int
    scale: float
    foreground: bool
    minimized: bool
    cloaked: bool
    visible: bool
    cursor: CursorState

    @property
    def likely_relative_input(self) -> bool:
        """Return a conservative pointer-lock/mouse-capture heuristic.

        A hidden cursor by itself is common in ordinary applications, so it is only
        a signal when the cursor clip is also restricted away from the full virtual
        desktop.
        """

        if not self.cursor.clipped or not self.foreground:
            return False
        clip = self.cursor.clip_rect
        client = self.client_rect
        tolerance = 2
        return (
            clip.left >= client.left - tolerance
            and clip.top >= client.top - tolerance
            and clip.right <= client.right + tolerance
            and clip.bottom <= client.bottom + tolerance
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.identity.as_dict(),
            "client_rect": self.client_rect.as_dict(),
            "dpi": self.dpi,
            "scale": self.scale,
            "foreground": self.foreground,
            "minimized": self.minimized,
            "cloaked": self.cloaked,
            "visible": self.visible,
            "cursor": self.cursor.as_dict(),
            "likely_relative_input": self.likely_relative_input,
        }


@dataclass(slots=True)
class CapturedFrame:
    frame_id: str
    window_id: str
    captured_at: float
    image_width: int
    image_height: int
    client_rect: Rect
    dpi: int
    scale: float
    capture_backend: str
    geometry_hash: str
    png_bytes: bytes = field(repr=False)
    cursor: CursorState | None = None

    def map_image_point(self, x: float, y: float, *, safe_margin: int = 2) -> dict[str, Any]:
        if not math.isfinite(x) or not math.isfinite(y):
            raise DesktopError("Coordinates must be finite numbers.", code="invalid_coordinates")
        if not 0 <= x < self.image_width or not 0 <= y < self.image_height:
            raise DesktopError(
                "Image coordinates are outside the captured frame.",
                code="invalid_coordinates",
                details={
                    "point": [x, y],
                    "image_width": self.image_width,
                    "image_height": self.image_height,
                },
            )

        client_x = x * self.client_rect.width / self.image_width
        client_y = y * self.client_rect.height / self.image_height
        max_x = max(0, self.client_rect.width - 1)
        max_y = max(0, self.client_rect.height - 1)
        margin_x = min(safe_margin, max_x // 2)
        margin_y = min(safe_margin, max_y // 2)
        client_x = min(max(client_x, margin_x), max_x - margin_x)
        client_y = min(max(client_y, margin_y), max_y - margin_y)
        rounded_client_x = int(round(client_x))
        rounded_client_y = int(round(client_y))
        screen_x = self.client_rect.left + rounded_client_x
        screen_y = self.client_rect.top + rounded_client_y
        return {
            "image_point": [x, y],
            "client_point": [rounded_client_x, rounded_client_y],
            "screen_point": [screen_x, screen_y],
            "coordinates": "screen_physical_px",
        }

    def metadata(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "window_id": self.window_id,
            "captured_at": self.captured_at,
            "image": {
                "width": self.image_width,
                "height": self.image_height,
                "format": "PNG",
                "capture_backend": self.capture_backend,
            },
            "client_rect": self.client_rect.as_dict(),
            "dpi": self.dpi,
            "scale": self.scale,
            "geometry_hash": self.geometry_hash,
            "cursor": self.cursor.as_dict() if self.cursor else None,
        }

    def png_sha256(self) -> str:
        return hashlib.sha256(self.png_bytes).hexdigest()


def geometry_fingerprint(
    *,
    window_id: str,
    client_rect: Rect,
    dpi: int,
    image_width: int,
    image_height: int,
) -> str:
    payload = {
        "window_id": window_id,
        "client_rect": [client_rect.left, client_rect.top, client_rect.width, client_rect.height],
        "dpi": dpi,
        "image": [image_width, image_height],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def decode_png_size(raw: bytes) -> tuple[int, int]:
    from io import BytesIO

    try:
        with Image.open(BytesIO(raw)) as image:
            return image.size
    except Exception as exc:
        raise DesktopError("The captured image could not be decoded.", code="capture_decode_failed") from exc


def utc_timestamp() -> float:
    return round(time.time(), 6)
