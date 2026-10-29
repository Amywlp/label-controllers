"""Target-window capture with a safe foreground fallback."""

from __future__ import annotations

import ctypes
import io
import uuid
from ctypes import wintypes

from PIL import Image, ImageGrab

from . import win32
from .models import (
    CapturedFrame,
    DesktopError,
    WindowIdentity,
    WindowInfo,
    geometry_fingerprint,
    utc_timestamp,
)

PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=False, compress_level=3)
    return output.getvalue()


def _printwindow_image(info: WindowInfo) -> Image.Image:
    width = info.client_rect.width
    height = info.client_rect.height
    if width <= 0 or height <= 0:
        raise DesktopError("The target client area has no drawable pixels.", code="window_has_no_client_area")

    hwnd = info.identity.hwnd
    window_dc = win32.user32.GetDC(hwnd)
    if not window_dc:
        raise DesktopError("Unable to acquire the target window device context.", code="capture_device_context_failed")
    memory_dc = None
    bitmap = None
    old_object = None
    try:
        memory_dc = win32.gdi32.CreateCompatibleDC(window_dc)
        if not memory_dc:
            raise DesktopError("Unable to create a capture device context.", code="capture_device_context_failed")
        bitmap = win32.gdi32.CreateCompatibleBitmap(window_dc, width, height)
        if not bitmap:
            raise DesktopError("Unable to allocate the capture bitmap.", code="capture_bitmap_failed")
        old_object = win32.gdi32.SelectObject(memory_dc, bitmap)
        flags = PW_CLIENTONLY | PW_RENDERFULLCONTENT
        if not win32.user32.PrintWindow(hwnd, memory_dc, flags):
            raise DesktopError("PrintWindow capture failed.", code="printwindow_failed")

        win32.gdi32.SelectObject(memory_dc, old_object)
        old_object = None
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # top-down rows
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB
        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader = header
        byte_count = width * height * 4
        pixels = ctypes.create_string_buffer(byte_count)
        rows = int(
            win32.gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                height,
                pixels,
                ctypes.byref(bitmap_info),
                DIB_RGB_COLORS,
            )
        )
        if rows != height:
            raise DesktopError("Unable to read PrintWindow pixels.", code="capture_pixels_failed")
        image = Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1).copy()
        extrema = image.getextrema()
        if all(low == high == 0 for low, high in extrema):
            raise DesktopError("PrintWindow returned an all-black frame.", code="capture_blank")
        return image
    finally:
        if memory_dc:
            if old_object:
                win32.gdi32.SelectObject(memory_dc, old_object)
            if bitmap:
                win32.gdi32.DeleteObject(bitmap)
            win32.gdi32.DeleteDC(memory_dc)
        win32.user32.ReleaseDC(hwnd, window_dc)


def _screen_image(info: WindowInfo) -> Image.Image:
    if info.minimized:
        raise DesktopError("Cannot capture a minimized window from the screen.", code="window_minimized")
    if not info.visible:
        raise DesktopError("The target window is not visible.", code="window_not_visible")
    if not info.foreground:
        raise DesktopError(
            "Screen capture requires the target window to be in the foreground.",
            code="foreground_required",
            status_code=409,
        )
    rect = info.client_rect
    if rect.width <= 0 or rect.height <= 0:
        raise DesktopError("The target client area has no drawable pixels.", code="window_has_no_client_area")
    try:
        image = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom),
            all_screens=True,
        )
    except Exception as exc:
        raise DesktopError(
            "Unable to capture the target window from the screen.", code="screen_capture_failed"
        ) from exc
    if image.width <= 0 or image.height <= 0:
        raise DesktopError("The screen capture returned an empty image.", code="capture_blank")
    return image.convert("RGB")


def _geometry_signature(info: WindowInfo) -> tuple[int, int, int, int, int, bool]:
    return (
        info.client_rect.left,
        info.client_rect.top,
        info.client_rect.width,
        info.client_rect.height,
        info.dpi,
        info.minimized,
    )


def capture_window(identity: WindowIdentity, *, mode: str = "auto") -> CapturedFrame:
    """Capture the client area of one target window.

    ``printwindow`` is preferred because it can capture an occluded window.
    ``screen`` is a foreground-only fallback for GPU-composited applications
    whose PrintWindow surface is blank. ``auto`` tries PrintWindow first and
    falls back to the foreground screen crop when that is safe.
    """

    if mode not in {"auto", "printwindow", "screen"}:
        raise DesktopError("Capture mode must be auto, printwindow, or screen.", code="invalid_capture_mode")
    before = win32.get_window_info(identity)
    if before.minimized:
        raise DesktopError(
            "The target window is minimized. Restore it before capturing.",
            code="window_minimized",
            status_code=409,
        )
    if before.client_rect.width <= 0 or before.client_rect.height <= 0:
        raise DesktopError("The target client area is not drawable.", code="window_has_no_client_area", status_code=409)

    attempts: list[dict[str, str]] = []
    image: Image.Image | None = None
    backend = mode
    if mode in {"auto", "printwindow"}:
        try:
            image = _printwindow_image(before)
            backend = "printwindow"
        except DesktopError as exc:
            attempts.append({"backend": "printwindow", "error": exc.code})
            if mode == "printwindow":
                raise
    if image is None and mode in {"auto", "screen"}:
        image = _screen_image(before)
        backend = "screen"

    if image is None:
        raise DesktopError(
            "No capture backend produced an image.", code="capture_failed", details={"attempts": attempts}
        )

    after = win32.get_window_info(identity)
    if _geometry_signature(before) != _geometry_signature(after):
        raise DesktopError(
            "The target window moved or resized during capture.",
            code="window_changed",
            status_code=409,
        )

    png = _png_bytes(image)
    captured_at = utc_timestamp()
    frame_id = "f_" + uuid.uuid4().hex
    return CapturedFrame(
        frame_id=frame_id,
        window_id=identity.window_id,
        captured_at=captured_at,
        image_width=image.width,
        image_height=image.height,
        client_rect=after.client_rect,
        dpi=after.dpi,
        scale=after.scale,
        capture_backend=backend,
        geometry_hash=geometry_fingerprint(
            window_id=identity.window_id,
            client_rect=after.client_rect,
            dpi=after.dpi,
            image_width=image.width,
            image_height=image.height,
        ),
        png_bytes=png,
        cursor=after.cursor,
    )
