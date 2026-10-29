"""Safe SendInput mouse primitives for absolute and relative modes."""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Iterable

from . import win32
from .models import DesktopError

_BUTTONS = {
    "left": {
        "down": win32.MOUSEEVENTF_LEFTDOWN,
        "up": win32.MOUSEEVENTF_LEFTUP,
    },
    "right": {
        "down": win32.MOUSEEVENTF_RIGHTDOWN,
        "up": win32.MOUSEEVENTF_RIGHTUP,
    },
    "middle": {
        "down": win32.MOUSEEVENTF_MIDDLEDOWN,
        "up": win32.MOUSEEVENTF_MIDDLEUP,
    },
}


class InputController:
    """Generate Win32 mouse input while tracking held buttons.

    A process-wide lock prevents concurrent API requests from interleaving
    mouse-down and mouse-up events. The service also releases every tracked
    button on session close and on an interrupted action list.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pressed: set[str] = set()

    @property
    def pressed_buttons(self) -> list[str]:
        with self._lock:
            return sorted(self._pressed)

    @staticmethod
    def _send(events: Iterable[tuple[int, int, int, int]]) -> int:
        items = list(events)
        if not items:
            return 0
        inputs = (win32.INPUT * len(items))()
        for index, (flags, dx, dy, mouse_data) in enumerate(items):
            inputs[index].type = win32.INPUT_MOUSE
            inputs[index].mi.dx = int(dx)
            inputs[index].mi.dy = int(dy)
            inputs[index].mi.mouseData = int(mouse_data) & 0xFFFFFFFF
            inputs[index].mi.dwFlags = int(flags)
            inputs[index].mi.time = 0
            inputs[index].mi.dwExtraInfo = 0
        sent = int(win32.user32.SendInput(len(items), inputs, ctypes.sizeof(win32.INPUT)))
        if sent != len(items):
            error_code = ctypes.get_last_error()
            raise DesktopError(
                "Windows rejected part of the input sequence.",
                code="send_input_failed",
                status_code=503,
                details={"sent": sent, "expected": len(items), "win32_error": error_code},
            )
        return sent

    @staticmethod
    def _absolute_normalized(x: int, y: int) -> tuple[int, int]:
        desktop = win32.virtual_screen_rect()
        if not desktop.contains(x, y):
            raise DesktopError(
                "The absolute point is outside the Windows virtual desktop.",
                code="point_outside_desktop",
                details={"screen_point": [x, y], "virtual_desktop": desktop.as_dict()},
            )
        max_x = max(1, desktop.width - 1)
        max_y = max(1, desktop.height - 1)
        normalized_x = round((x - desktop.left) * 65535 / max_x)
        normalized_y = round((y - desktop.top) * 65535 / max_y)
        return int(normalized_x), int(normalized_y)

    def move_absolute(self, x: int, y: int) -> None:
        with self._lock:
            normalized_x, normalized_y = self._absolute_normalized(int(x), int(y))
            flags = win32.MOUSEEVENTF_MOVE | win32.MOUSEEVENTF_ABSOLUTE | win32.MOUSEEVENTF_VIRTUALDESK
            self._send([(flags, normalized_x, normalized_y, 0)])

    def move_relative(self, dx: int, dy: int, *, steps: int = 1, interval_ms: int = 0) -> list[list[int]]:
        if steps < 1 or steps > 100:
            raise DesktopError("Relative movement steps must be between 1 and 100.", code="invalid_action")
        if interval_ms < 0 or interval_ms > 1000:
            raise DesktopError("Relative movement interval must be between 0 and 1000 ms.", code="invalid_action")
        chunks: list[list[int]] = []
        previous_x = 0
        previous_y = 0
        with self._lock:
            for index in range(1, steps + 1):
                current_x = round(dx * index / steps)
                current_y = round(dy * index / steps)
                step_x = int(current_x - previous_x)
                step_y = int(current_y - previous_y)
                if step_x or step_y:
                    self._send([(win32.MOUSEEVENTF_MOVE, step_x, step_y, 0)])
                    chunks.append([step_x, step_y])
                previous_x = current_x
                previous_y = current_y
                if interval_ms and index < steps:
                    time.sleep(interval_ms / 1000)
        return chunks

    @staticmethod
    def _button_flags(button: str, direction: str) -> int:
        normalized = button.strip().casefold()
        try:
            return _BUTTONS[normalized][direction]
        except KeyError as exc:
            raise DesktopError(
                f"Unsupported mouse button: {button}. Allowed: left, right, middle.",
                code="invalid_mouse_button",
            ) from exc

    def button_down(self, button: str) -> None:
        normalized = button.strip().casefold()
        flags = self._button_flags(normalized, "down")
        with self._lock:
            self._send([(flags, 0, 0, 0)])
            self._pressed.add(normalized)

    def button_up(self, button: str) -> None:
        normalized = button.strip().casefold()
        flags = self._button_flags(normalized, "up")
        with self._lock:
            self._send([(flags, 0, 0, 0)])
            self._pressed.discard(normalized)

    def click_current(self, *, button: str = "left", count: int = 1, hold_ms: int = 35) -> None:
        if count < 1 or count > 5:
            raise DesktopError("Click count must be between 1 and 5.", code="invalid_action")
        if hold_ms < 0 or hold_ms > 2000:
            raise DesktopError("Click hold time must be between 0 and 2000 ms.", code="invalid_action")
        for _ in range(count):
            self.button_down(button)
            if hold_ms:
                time.sleep(hold_ms / 1000)
            self.button_up(button)
            if count > 1:
                time.sleep(0.04)

    def click_absolute(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        count: int = 1,
        hold_ms: int = 35,
    ) -> None:
        with self._lock:
            self.move_absolute(x, y)
            # Let the application process the move before sending buttons.
            time.sleep(0.01)
            self.click_current(button=button, count=count, hold_ms=hold_ms)

    def wheel(self, *, delta_x: int = 0, delta_y: int = 0) -> None:
        if delta_x == 0 and delta_y == 0:
            return
        if abs(delta_x) > 1200 or abs(delta_y) > 1200:
            raise DesktopError("Wheel delta is too large.", code="invalid_action")
        events: list[tuple[int, int, int, int]] = []
        if delta_y:
            events.append((win32.MOUSEEVENTF_WHEEL, 0, 0, delta_y))
        if delta_x:
            events.append((win32.MOUSEEVENTF_HWHEEL, 0, 0, delta_x))
        with self._lock:
            self._send(events)

    def release_all(self, *, force_standard_buttons: bool = False) -> list[str]:
        """Release tracked buttons and optionally emit safety mouse-up events."""

        released: list[str] = []
        with self._lock:
            buttons = set(self._pressed)
            if force_standard_buttons:
                buttons.update(_BUTTONS)
            # Always release left before right/middle to make interrupted drags recover.
            for button in ("left", "middle", "right"):
                if button not in buttons:
                    continue
                try:
                    self._send([(self._button_flags(button, "up"), 0, 0, 0)])
                    released.append(button)
                finally:
                    self._pressed.discard(button)
        return released
