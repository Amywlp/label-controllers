"""Minimal Win32 bindings used by the desktop adapter.

The module intentionally uses :mod:`ctypes` rather than pywin32 so that the
adapter has no additional native Python dependency. All operations that can
change desktop state are wrapped by the higher-level service with a foreground,
identity, and geometry guard.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import time
from ctypes import wintypes
from pathlib import Path

from .models import CursorState, DesktopError, Rect, WindowIdentity, WindowInfo

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        dwmapi = None
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
    except OSError:
        shcore = None
else:  # pragma: no cover - importing on Linux should remain harmless
    user32 = kernel32 = gdi32 = dwmapi = shcore = None


GW_OWNER = 4
GA_ROOT = 2
SW_RESTORE = 9
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CURSOR_SHOWING = 0x00000001
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
INPUT_MOUSE = 0
WHEEL_DELTA = 120


if IS_WINDOWS:

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", RECT),
        ]

    class CURSORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hCursor", wintypes.HANDLE),
            ("ptScreenPos", POINT),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetCapture.restype = wintypes.HWND
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wintypes.BOOL
    user32.GetClipCursor.argtypes = [ctypes.POINTER(RECT)]
    user32.GetClipCursor.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


def require_windows() -> None:
    if not IS_WINDOWS:
        raise DesktopError(
            "The desktop adapter is available only in a native Windows session.",
            code="unsupported_platform",
            status_code=503,
        )


def enable_per_monitor_dpi_awareness() -> str:
    """Enable physical-pixel coordinates before any window geometry is queried."""

    require_windows()
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is a pseudo-handle with value -4.
    try:
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [wintypes.HANDLE]
        set_context.restype = wintypes.BOOL
        if set_context(wintypes.HANDLE(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    if shcore is not None:
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            if shcore.SetProcessDpiAwareness(2) == 0:
                return "per-monitor"
        except OSError:
            pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "unaware"


def _window_text(hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _process_info(pid: int) -> tuple[str, float]:
    process_name = f"pid-{pid}"
    created_at = 0.0
    handle = int(kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid) or 0)
    if handle:
        try:
            path_buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path_buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(size)):
                process_name = Path(path_buffer.value).name or process_name
            creation = FILETIME()
            exit_time = FILETIME()
            kernel_time = FILETIME()
            user_time = FILETIME()
            if kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                ticks = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                # FILETIME is 100 ns since 1601-01-01; convert to Unix seconds.
                created_at = round((ticks - 116444736000000000) / 10_000_000, 6) if ticks else 0.0
        finally:
            kernel32.CloseHandle(handle)
    if not created_at:
        try:
            import psutil

            created_at = round(float(psutil.Process(pid).create_time()), 6)
        except Exception:
            created_at = 0.0
    return process_name, created_at


def _identity_for_hwnd(hwnd: int) -> WindowIdentity:
    pid_value = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
    pid = int(pid_value.value)
    process_name, created_at = _process_info(pid)
    class_name = _class_name(hwnd)
    title = _window_text(hwnd)
    stable = f"{int(hwnd):x}:{pid}:{created_at:.6f}:{class_name}".encode()
    window_id = "w_" + hashlib.sha256(stable).hexdigest()[:20]
    return WindowIdentity(
        window_id=window_id,
        hwnd=int(hwnd),
        pid=pid,
        process_name=process_name,
        process_created_at=created_at,
        class_name=class_name,
        title=title,
    )


def _virtual_screen_rect() -> Rect:
    left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
    height = max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
    return Rect(left, top, width, height)


def _cursor_state() -> CursorState:
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(info)):
        raise DesktopError("Unable to read cursor state.", code="cursor_state_failed", status_code=503)
    clip = RECT()
    if user32.GetClipCursor(ctypes.byref(clip)):
        clip_rect = Rect(clip.left, clip.top, max(0, clip.right - clip.left), max(0, clip.bottom - clip.top))
    else:
        clip_rect = _virtual_screen_rect()
    return CursorState(
        x=int(info.ptScreenPos.x),
        y=int(info.ptScreenPos.y),
        visible=bool(info.flags & CURSOR_SHOWING),
        clipped=clip_rect != _virtual_screen_rect(),
        clip_rect=clip_rect,
    )


def _dpi_for_window(hwnd: int) -> int:
    try:
        get_dpi = user32.GetDpiForWindow
        get_dpi.argtypes = [wintypes.HWND]
        get_dpi.restype = wintypes.UINT
        dpi = int(get_dpi(hwnd))
        if dpi:
            return dpi
    except (AttributeError, OSError):
        pass
    hdc = user32.GetDC(hwnd)
    try:
        return int(gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
    finally:
        user32.ReleaseDC(hwnd, hdc)


def _is_cloaked(hwnd: int) -> bool:
    if dwmapi is None:
        return False
    value = wintypes.DWORD(0)
    result = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return result == 0 and bool(value.value)


def _same_window_group(hwnd: int | None, target_hwnd: int) -> bool:
    if not hwnd:
        return False
    current = int(hwnd)
    for _ in range(8):
        if current == target_hwnd:
            return True
        current = int(user32.GetWindow(current, GW_OWNER) or 0)
        if not current:
            break
    return False


def get_window_info(identity: WindowIdentity) -> WindowInfo:
    require_windows()
    hwnd = identity.hwnd
    if not user32.IsWindow(hwnd):
        raise DesktopError("The target window no longer exists.", code="window_gone", status_code=410)
    current_identity = _identity_for_hwnd(hwnd)
    if not identity.same_target(current_identity):
        raise DesktopError(
            "The window identity changed; refresh the window list and create a new session.",
            code="window_identity_changed",
            status_code=409,
        )
    client = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise DesktopError("Unable to read the target client rectangle.", code="geometry_failed", status_code=503)
    origin = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise DesktopError("Unable to map the target client origin.", code="geometry_failed", status_code=503)
    width = max(0, int(client.right - client.left))
    height = max(0, int(client.bottom - client.top))
    dpi = _dpi_for_window(hwnd)
    foreground = _same_window_group(int(user32.GetForegroundWindow() or 0), hwnd)
    return WindowInfo(
        identity=current_identity,
        client_rect=Rect(int(origin.x), int(origin.y), width, height),
        dpi=dpi,
        scale=round(dpi / 96.0, 4),
        foreground=foreground,
        minimized=bool(user32.IsIconic(hwnd)),
        cloaked=_is_cloaked(hwnd),
        visible=bool(user32.IsWindowVisible(hwnd)),
        cursor=_cursor_state(),
    )


def list_windows(
    *,
    query: str = "",
    include_hidden: bool = False,
    include_untitled: bool = False,
    include_tool_windows: bool = False,
) -> list[WindowInfo]:
    require_windows()
    handles: list[int] = []

    @WNDENUMPROC
    def callback(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindow(hwnd):
                return True
            if not include_hidden and not user32.IsWindowVisible(hwnd):
                return True
            if not include_tool_windows and int(user32.GetWindowLongW(hwnd, -20)) & WS_EX_TOOLWINDOW:
                return True
            if not include_untitled and user32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            if _is_cloaked(hwnd) and not include_hidden:
                return True
            handles.append(int(hwnd))
        except Exception:
            return True
        return True

    if not user32.EnumWindows(callback, 0):
        raise DesktopError("Unable to enumerate desktop windows.", code="window_enumeration_failed", status_code=503)

    items: list[WindowInfo] = []
    needle = query.strip().casefold()
    for hwnd in handles:
        try:
            identity = _identity_for_hwnd(hwnd)
            if needle:
                haystack = f"{identity.title}\n{identity.process_name}\n{identity.class_name}".casefold()
                if needle not in haystack:
                    continue
            items.append(get_window_info(identity))
        except DesktopError:
            continue
    items.sort(
        key=lambda item: (not item.foreground, item.identity.process_name.casefold(), item.identity.title.casefold())
    )
    return items


def find_window(window_id: str) -> WindowIdentity:
    require_windows()
    for item in list_windows(include_hidden=True, include_untitled=True, include_tool_windows=True):
        if item.identity.window_id == window_id:
            return item.identity
    raise DesktopError("Window was not found.", code="window_not_found", status_code=404)


def refresh_identity(identity: WindowIdentity) -> WindowIdentity:
    info = get_window_info(identity)
    return info.identity


def has_mouse_capture(identity: WindowIdentity) -> bool:
    """Return whether the target window group currently owns Win32 mouse capture."""

    require_windows()
    captured = 0
    foreground = int(user32.GetForegroundWindow() or 0)
    if foreground:
        thread_id = int(user32.GetWindowThreadProcessId(foreground, None))
        if thread_id:
            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
                captured = int(info.hwndCapture or 0)
    if not captured:
        # GetCapture only reports capture for the calling thread, but this fallback
        # covers the case where the adapter itself owns a synthetic drag capture.
        captured = int(user32.GetCapture() or 0)
    return bool(captured) and _same_window_group(captured, identity.hwnd)


def assert_target_window_at_point(identity: WindowIdentity, x: int, y: int) -> None:
    require_windows()
    hwnd = int(user32.WindowFromPoint(POINT(int(x), int(y))) or 0)
    root = int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd) if hwnd else 0
    if not _same_window_group(root, identity.hwnd):
        raise DesktopError(
            "The mapped screen point is not owned by the target window.",
            code="point_obstructed",
            status_code=409,
            details={"screen_point": [x, y], "top_level_hwnd": root},
        )


def activate_window(identity: WindowIdentity) -> bool:
    require_windows()
    if not user32.IsWindow(identity.hwnd):
        raise DesktopError("The target window no longer exists.", code="window_gone", status_code=410)
    if user32.IsIconic(identity.hwnd):
        user32.ShowWindow(identity.hwnd, SW_RESTORE)
    user32.BringWindowToTop(identity.hwnd)
    user32.SetForegroundWindow(identity.hwnd)
    time.sleep(0.12)
    return _same_window_group(int(user32.GetForegroundWindow() or 0), identity.hwnd)


def virtual_screen_rect() -> Rect:
    require_windows()
    return _virtual_screen_rect()


def cursor_state() -> CursorState:
    require_windows()
    return _cursor_state()
