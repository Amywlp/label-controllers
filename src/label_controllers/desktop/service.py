"""Stateful window sessions, analysis integration, and guarded input dispatch."""

from __future__ import annotations

import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

from . import win32
from .capture import capture_window
from .input import InputController
from .models import (
    CapturedFrame,
    DesktopError,
    WindowIdentity,
    WindowInfo,
    geometry_fingerprint,
)
from .settings import DesktopSettings


@dataclass(slots=True)
class DesktopSession:
    session_id: str
    identity: WindowIdentity
    requested_input_mode: str
    effective_input_mode: str
    relative_gain_x: float
    relative_gain_y: float
    relative_max_step: int
    relative_origin_fraction: tuple[float, float]
    created_at: float
    last_used_at: float
    last_frame: CapturedFrame | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "window": self.identity.as_dict(),
            "input": {
                "requested_mode": self.requested_input_mode,
                "effective_mode": self.effective_input_mode,
                "relative_gain": [self.relative_gain_x, self.relative_gain_y],
                "relative_max_step": self.relative_max_step,
                "relative_origin_fraction": list(self.relative_origin_fraction),
            },
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "last_frame": self.last_frame.metadata() if self.last_frame else None,
        }


def _number(action: dict[str, Any], name: str, default: float | None = None) -> float:
    value = action.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DesktopError(f"Action field {name!r} must be a number.", code="invalid_action")
    value = float(value)
    if not math.isfinite(value):
        raise DesktopError(f"Action field {name!r} must be finite.", code="invalid_action")
    return value


def _integer(action: dict[str, Any], name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = action.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DesktopError(f"Action field {name!r} must be an integer.", code="invalid_action")
    if not minimum <= value <= maximum:
        raise DesktopError(
            f"Action field {name!r} must be between {minimum} and {maximum}.",
            code="invalid_action",
        )
    return int(value)


def _point(value: Any) -> tuple[float, float]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise DesktopError("A point must be a two-element [x, y] array.", code="invalid_coordinates")
    x, y = value
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int | float) or not isinstance(y, int | float):
        raise DesktopError("Point coordinates must be numbers.", code="invalid_coordinates")
    return float(x), float(y)


class DesktopService:
    def __init__(self, settings: DesktopSettings):
        self.settings = settings
        self.input = InputController()
        self._sessions: dict[str, DesktopSession] = {}
        self._operation_lock = threading.RLock()

    def list_windows(
        self,
        *,
        query: str = "",
        pid: int | None = None,
        process_name: str = "",
        class_name: str = "",
        title_regex: str = "",
        include_hidden: bool = False,
        include_untitled: bool = False,
        include_tool_windows: bool = False,
    ) -> list[dict[str, Any]]:
        with self._operation_lock:
            items = win32.list_windows(
                query=query,
                include_hidden=include_hidden,
                include_untitled=include_untitled,
                include_tool_windows=include_tool_windows,
            )
            try:
                title_pattern = re.compile(title_regex, re.IGNORECASE) if title_regex else None
            except re.error as exc:
                raise DesktopError("title_regex is not a valid regular expression.", code="invalid_selector") from exc
            process_filter = process_name.strip().casefold()
            class_filter = class_name.strip().casefold()
            result: list[dict[str, Any]] = []
            for item in items:
                public = item.as_dict()
                if pid is not None and int(public["pid"]) != pid:
                    continue
                if process_filter and str(public["process"]).casefold() != process_filter:
                    continue
                if class_filter and str(public["class"]).casefold() != class_filter:
                    continue
                if title_pattern and not title_pattern.search(str(public["title"])):
                    continue
                result.append(public)
            return result

    def create_session(
        self,
        *,
        window_id: str | None = None,
        pid: int | None = None,
        process_name: str = "",
        class_name: str = "",
        title_regex: str = "",
        input_mode: str = "auto",
        relative_gain_x: float = 1.0,
        relative_gain_y: float = 1.0,
        relative_max_step: int = 64,
        relative_origin_fraction: tuple[float, float] = (0.5, 0.5),
    ) -> DesktopSession:
        input_mode = input_mode.strip().casefold()
        if input_mode not in {"auto", "absolute", "relative"}:
            raise DesktopError("input_mode must be auto, absolute, or relative.", code="invalid_input_mode")
        if abs(relative_gain_x) < 0.01 or abs(relative_gain_x) > 20:
            raise DesktopError(
                "relative_gain_x must have an absolute value between 0.01 and 20.", code="invalid_action"
            )
        if abs(relative_gain_y) < 0.01 or abs(relative_gain_y) > 20:
            raise DesktopError(
                "relative_gain_y must have an absolute value between 0.01 and 20.", code="invalid_action"
            )
        if not 1 <= relative_max_step <= 1000:
            raise DesktopError("relative_max_step must be between 1 and 1000.", code="invalid_action")
        origin_x, origin_y = relative_origin_fraction
        if not 0.0 <= origin_x <= 1.0 or not 0.0 <= origin_y <= 1.0:
            raise DesktopError("relative_origin_fraction values must be between 0 and 1.", code="invalid_action")

        if not any([window_id, pid is not None, process_name.strip(), class_name.strip(), title_regex.strip()]):
            raise DesktopError(
                "Provide window_id or at least one selector: pid, process_name, class_name, title_regex.",
                code="window_selector_required",
            )

        with self._operation_lock:
            self._prune_expired_sessions()
            if window_id:
                identity = win32.find_window(window_id)
                if pid is not None and identity.pid != pid:
                    raise DesktopError(
                        "window_id does not match the requested pid.", code="window_selector_mismatch", status_code=409
                    )
                if process_name and identity.process_name.casefold() != process_name.strip().casefold():
                    raise DesktopError(
                        "window_id does not match the requested process_name.",
                        code="window_selector_mismatch",
                        status_code=409,
                    )
                if class_name and identity.class_name.casefold() != class_name.strip().casefold():
                    raise DesktopError(
                        "window_id does not match the requested class_name.",
                        code="window_selector_mismatch",
                        status_code=409,
                    )
                if title_regex:
                    try:
                        if not re.search(title_regex, identity.title, re.IGNORECASE):
                            raise DesktopError(
                                "window_id does not match the requested title_regex.",
                                code="window_selector_mismatch",
                                status_code=409,
                            )
                    except re.error as exc:
                        raise DesktopError(
                            "title_regex is not a valid regular expression.", code="invalid_selector"
                        ) from exc
            else:
                candidates = self.list_windows(
                    pid=pid,
                    process_name=process_name,
                    class_name=class_name,
                    title_regex=title_regex,
                )
                if not candidates:
                    raise DesktopError(
                        "No visible window matched the selector.", code="window_not_found", status_code=404
                    )
                if len(candidates) > 1:
                    raise DesktopError(
                        "The selector matched multiple windows. Add title_regex or use window_id.",
                        code="window_selector_ambiguous",
                        status_code=409,
                        details={"candidates": candidates[:20]},
                    )
                identity = win32.find_window(str(candidates[0]["window_id"]))
            info = win32.get_window_info(identity)
            if info.cloaked or not info.visible:
                raise DesktopError("The selected window is not visible.", code="window_not_visible", status_code=409)
            if info.minimized:
                raise DesktopError(
                    "The selected window is minimized. Restore it before creating a session.",
                    code="window_minimized",
                    status_code=409,
                )
            effective = self._resolve_input_mode(input_mode, info)
            now = time.time()
            session = DesktopSession(
                session_id="s_" + secrets.token_urlsafe(24),
                identity=identity,
                requested_input_mode=input_mode,
                effective_input_mode=effective,
                relative_gain_x=float(relative_gain_x),
                relative_gain_y=float(relative_gain_y),
                relative_max_step=int(relative_max_step),
                relative_origin_fraction=(float(origin_x), float(origin_y)),
                created_at=now,
                last_used_at=now,
            )
            self._sessions[session.session_id] = session
            return session

    @staticmethod
    def _resolve_input_mode(requested: str, info: WindowInfo) -> str:
        if requested != "auto":
            return requested
        return "relative" if info.likely_relative_input else "absolute"

    def get_session(self, session_id: str) -> DesktopSession:
        with self._operation_lock:
            self._prune_expired_sessions()
            session = self._sessions.get(session_id)
            if session is None:
                raise DesktopError(
                    "Desktop session was not found or expired.", code="session_not_found", status_code=404
                )
            info = win32.get_window_info(session.identity)
            session.identity = info.identity
            if session.requested_input_mode == "auto":
                session.effective_input_mode = self._resolve_input_mode("auto", info)
            session.last_used_at = time.time()
            return session

    def close_session(self, session_id: str) -> dict[str, Any]:
        with self._operation_lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                raise DesktopError(
                    "Desktop session was not found or expired.", code="session_not_found", status_code=404
                )
            released = self.input.release_all(force_standard_buttons=True)
            return {"session_id": session_id, "closed": True, "released_buttons": released}

    def capture(self, session_id: str, *, mode: str | None = None) -> CapturedFrame:
        with self._operation_lock:
            session = self.get_session(session_id)
            capture_mode = mode or self.settings.capture_mode
            frame = capture_window(session.identity, mode=capture_mode)
            session.last_frame = frame
            if session.requested_input_mode == "auto":
                session.effective_input_mode = self._resolve_input_mode("auto", win32.get_window_info(session.identity))
            session.last_used_at = time.time()
            return frame

    def analyze(
        self,
        session_id: str,
        *,
        include: str = "controls,texts,metadata",
        icon_confidence: float | None = None,
        ocr_confidence: float | None = None,
        capture_mode: str | None = None,
    ) -> dict[str, Any]:
        frame = self.capture(session_id, mode=capture_mode)
        with self._operation_lock:
            session = self._sessions[session_id]
            window = session.identity.as_dict()
            input_mode = session.effective_input_mode
            relative_gain = [session.relative_gain_x, session.relative_gain_y]

        headers = {"Accept": "application/json", "X-Request-ID": f"desktop-{secrets.token_hex(8)}"}
        if self.settings.inference_api_key:
            headers["X-API-Key"] = self.settings.inference_api_key
        data: dict[str, str] = {"include": include}
        if icon_confidence is not None:
            data["icon_confidence"] = str(icon_confidence)
        if ocr_confidence is not None:
            data["ocr_confidence"] = str(ocr_confidence)
        try:
            response = requests.post(
                f"{self.settings.inference_url}/api/v1/analyze",
                headers=headers,
                files={"file": ("window.png", frame.png_bytes, "image/png")},
                data=data,
                timeout=self.settings.inference_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise DesktopError(
                f"Could not reach the inference service at {self.settings.inference_url}: {exc}",
                code="inference_unavailable",
                status_code=502,
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise DesktopError(
                "The inference service returned a non-JSON response.",
                code="inference_invalid_response",
                status_code=502,
                details={"http_status": response.status_code},
            ) from exc
        if response.status_code >= 400:
            raise DesktopError(
                f"Inference failed with HTTP {response.status_code}.",
                code="inference_failed",
                status_code=502,
                details={"inference_response": payload},
            )
        if not isinstance(payload, dict):
            raise DesktopError(
                "The inference response must be a JSON object.", code="inference_invalid_response", status_code=502
            )

        control_count = 0
        for field in ("controls", "elements"):
            values = payload.get(field)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                raw_click = item.get("click", item.get("center"))
                if not isinstance(raw_click, list | tuple) or len(raw_click) != 2:
                    continue
                mapped = frame.map_image_point(
                    float(raw_click[0]),
                    float(raw_click[1]),
                    safe_margin=self.settings.safe_margin_px,
                )
                item["image_click"] = [float(raw_click[0]), float(raw_click[1])]
                item["client_click"] = mapped["client_point"]
                item["screen_click"] = mapped["screen_point"]
                item["coordinate_space"] = "screen_physical_px"
                if field == "controls":
                    control_count += 1

        payload["desktop"] = {
            "session_id": session_id,
            "window": window,
            "frame": frame.metadata(),
            "image_to_screen": {
                "formula": ("screen = client_rect.origin + image_point * client_rect.size / frame.image.size"),
                "safe_margin_px": self.settings.safe_margin_px,
                "coordinate_spaces": ["image_px", "client_px", "screen_px"],
            },
            "input": {
                "mode": input_mode,
                "relative_gain": relative_gain,
                "mapped_control_count": control_count,
            },
        }
        return payload

    def execute_actions(
        self,
        session_id: str,
        *,
        frame_id: str,
        actions: list[dict[str, Any]],
        dry_run: bool = False,
        activate: bool = False,
        release_at_end: bool = True,
    ) -> dict[str, Any]:
        if not self.settings.allow_input and not dry_run:
            raise DesktopError(
                "Input injection is disabled. Start the adapter with "
                "LABEL_CONTROLLERS_DESKTOP_ALLOW_INPUT=true to enable actions.",
                code="input_disabled",
                status_code=403,
            )
        if not actions:
            raise DesktopError("At least one action is required.", code="invalid_action")
        if len(actions) > self.settings.max_actions:
            raise DesktopError(
                f"At most {self.settings.max_actions} actions may be submitted in one request.",
                code="too_many_actions",
            )

        with self._operation_lock:
            session = self.get_session(session_id)
            frame = session.last_frame
            if frame is None:
                raise DesktopError("Capture a frame before executing actions.", code="frame_required", status_code=409)
            if frame_id != frame.frame_id:
                raise DesktopError(
                    "The frame is stale. Capture and analyze the window again before acting.",
                    code="stale_frame",
                    status_code=409,
                    details={"submitted_frame_id": frame_id, "current_frame_id": frame.frame_id},
                )
            info = self._guard_frame(session, frame, require_foreground=False)
            if not dry_run and not info.foreground:
                if activate and self.settings.allow_activate:
                    if not win32.activate_window(session.identity):
                        raise DesktopError(
                            "Windows refused to bring the target window to the foreground.",
                            code="foreground_activation_failed",
                            status_code=409,
                        )
                    info = self._guard_frame(session, frame, require_foreground=True)
                else:
                    raise DesktopError(
                        "The target window must be in the foreground. Retry with activate=true when permitted.",
                        code="foreground_required",
                        status_code=409,
                    )

            before_cursor = info.cursor.as_dict()
            results: list[dict[str, Any]] = []
            released: list[str] = []
            try:
                for index, action in enumerate(actions):
                    if not isinstance(action, dict):
                        raise DesktopError("Each action must be a JSON object.", code="invalid_action")
                    action_type = str(action.get("type", "")).strip().casefold()
                    result = self._execute_one(
                        session,
                        frame,
                        action_type,
                        action,
                        dry_run=dry_run,
                    )
                    result["index"] = index
                    result["type"] = action_type
                    results.append(result)
                    if not dry_run:
                        info = self._guard_frame(session, frame, require_foreground=True)
            except Exception:
                if not dry_run:
                    released.extend(self.input.release_all(force_standard_buttons=True))
                raise
            finally:
                if not dry_run and release_at_end:
                    for button in self.input.release_all():
                        if button not in released:
                            released.append(button)
                session.last_used_at = time.time()

            return {
                "session_id": session_id,
                "frame_id": frame.frame_id,
                "window_id": session.identity.window_id,
                "dry_run": dry_run,
                "input_mode": session.effective_input_mode,
                "actions": results,
                "released_buttons": released,
                "pressed_buttons": self.input.pressed_buttons,
                "cursor_before": before_cursor,
                "cursor_after": win32.cursor_state().as_dict(),
            }

    def release_all(self, session_id: str) -> dict[str, Any]:
        with self._operation_lock:
            self.get_session(session_id)
            released = self.input.release_all(force_standard_buttons=True)
            return {"released_buttons": released, "pressed_buttons": self.input.pressed_buttons}

    def _guard_frame(
        self,
        session: DesktopSession,
        frame: CapturedFrame,
        *,
        require_foreground: bool,
    ) -> WindowInfo:
        info = win32.get_window_info(session.identity)
        current_hash = geometry_fingerprint(
            window_id=session.identity.window_id,
            client_rect=info.client_rect,
            dpi=info.dpi,
            image_width=frame.image_width,
            image_height=frame.image_height,
        )
        if info.client_rect != frame.client_rect or info.dpi != frame.dpi or current_hash != frame.geometry_hash:
            raise DesktopError(
                "The target window geometry changed after the frame was captured.",
                code="stale_frame",
                status_code=409,
                details={
                    "frame_client_rect": frame.client_rect.as_dict(),
                    "current_client_rect": info.client_rect.as_dict(),
                    "frame_dpi": frame.dpi,
                    "current_dpi": info.dpi,
                },
            )
        if info.minimized:
            raise DesktopError("The target window is minimized.", code="window_minimized", status_code=409)
        if require_foreground and not info.foreground:
            raise DesktopError(
                "The target window is not in the foreground.", code="foreground_required", status_code=409
            )
        session.identity = info.identity
        return info

    def _action_point(
        self,
        action: dict[str, Any],
        frame: CapturedFrame,
        *,
        x_key: str = "x",
        y_key: str = "y",
    ) -> dict[str, Any]:
        point_value = action.get("point")
        if point_value is None:
            if x_key not in action or y_key not in action:
                raise DesktopError(
                    f"Action requires either point=[x,y] or {x_key}/{y_key}.",
                    code="invalid_coordinates",
                )
            point_value = [action[x_key], action[y_key]]
        x, y = _point(point_value)
        space = str(action.get("space", "image_px")).strip().casefold()
        if space == "image_px":
            return frame.map_image_point(x, y, safe_margin=self.settings.safe_margin_px)
        if space == "client_px":
            client_x = int(round(x))
            client_y = int(round(y))
            if not 0 <= client_x < frame.client_rect.width or not 0 <= client_y < frame.client_rect.height:
                raise DesktopError(
                    "Client coordinates are outside the captured client area.", code="invalid_coordinates"
                )
            return {
                "image_point": None,
                "client_point": [client_x, client_y],
                "screen_point": [frame.client_rect.left + client_x, frame.client_rect.top + client_y],
                "coordinates": "screen_physical_px",
            }
        if space == "screen_px":
            screen_x = int(round(x))
            screen_y = int(round(y))
            if not frame.client_rect.contains(screen_x, screen_y):
                raise DesktopError(
                    "Screen coordinates must remain inside the captured target client area.",
                    code="invalid_coordinates",
                )
            return {
                "image_point": None,
                "client_point": [screen_x - frame.client_rect.left, screen_y - frame.client_rect.top],
                "screen_point": [screen_x, screen_y],
                "coordinates": "screen_physical_px",
            }
        raise DesktopError("space must be image_px, client_px, or screen_px.", code="invalid_coordinates")

    def _execute_one(
        self,
        session: DesktopSession,
        frame: CapturedFrame,
        action_type: str,
        action: dict[str, Any],
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        if action_type in {"move", "click", "drag"}:
            if action_type == "drag":
                start = self._action_point(action, frame, x_key="from_x", y_key="from_y")
                end = self._action_point(action, frame, x_key="to_x", y_key="to_y")
                if dry_run:
                    return {"from": start, "to": end, "executed": False}
                self._assert_action_target(session, start["screen_point"])
                self._assert_action_target(session, end["screen_point"])
                button = str(action.get("button", "left")).strip().casefold()
                steps = _integer(action, "steps", 12, minimum=2, maximum=200)
                duration_ms = _integer(action, "duration_ms", 350, minimum=0, maximum=10_000)
                hold_ms = _integer(action, "hold_ms", 20, minimum=0, maximum=1000)
                self.input.move_absolute(*start["screen_point"])
                time.sleep(0.01)
                self.input.button_down(button)
                try:
                    for step in range(1, steps + 1):
                        fraction = step / steps
                        x = round(
                            start["screen_point"][0] + (end["screen_point"][0] - start["screen_point"][0]) * fraction
                        )
                        y = round(
                            start["screen_point"][1] + (end["screen_point"][1] - start["screen_point"][1]) * fraction
                        )
                        if not win32.has_mouse_capture(session.identity):
                            win32.assert_target_window_at_point(session.identity, x, y)
                        self.input.move_absolute(x, y)
                        if duration_ms:
                            time.sleep(duration_ms / 1000 / steps)
                    if hold_ms:
                        time.sleep(hold_ms / 1000)
                finally:
                    self.input.button_up(button)
                return {"from": start, "to": end, "executed": True}

            mapped = self._action_point(action, frame)
            if dry_run:
                return {"mapping": mapped, "executed": False}
            self._assert_action_target(session, mapped["screen_point"])
            if action_type == "move":
                self.input.move_absolute(*mapped["screen_point"])
            else:
                button = str(action.get("button", "left")).strip().casefold()
                count = _integer(action, "count", 1, minimum=1, maximum=5)
                hold_ms = _integer(action, "hold_ms", 35, minimum=0, maximum=2000)
                self.input.click_absolute(*mapped["screen_point"], button=button, count=count, hold_ms=hold_ms)
            return {"mapping": mapped, "executed": True}

        if action_type == "move_relative":
            dx = int(round(_number(action, "dx")))
            dy = int(round(_number(action, "dy")))
            if abs(dx) > 100_000 or abs(dy) > 100_000:
                raise DesktopError("Relative movement is too large.", code="invalid_action")
            steps = _integer(action, "steps", 1, minimum=1, maximum=100)
            interval_ms = _integer(action, "interval_ms", 0, minimum=0, maximum=1000)
            if dry_run:
                return {"relative_delta": [dx, dy], "steps": steps, "executed": False}
            chunks = self.input.move_relative(dx, dy, steps=steps, interval_ms=interval_ms)
            return {"relative_delta": [dx, dy], "chunks": chunks, "executed": True}

        if action_type in {"click_current", "mouse_down", "mouse_up"}:
            button = str(action.get("button", "left")).strip().casefold()
            if dry_run:
                return {"button": button, "executed": False}
            if action_type == "click_current":
                self._assert_current_target(session)
                count = _integer(action, "count", 1, minimum=1, maximum=5)
                hold_ms = _integer(action, "hold_ms", 35, minimum=0, maximum=2000)
                self.input.click_current(button=button, count=count, hold_ms=hold_ms)
            elif action_type == "mouse_down":
                if not self.input.pressed_buttons:
                    self._assert_current_target(session)
                self.input.button_down(button)
            else:
                # Release is always allowed; it is a safety operation.
                self.input.button_up(button)
            return {"button": button, "executed": True}

        if action_type == "wheel":
            delta_x = _integer(action, "delta_x", 0, minimum=-1200, maximum=1200)
            delta_y = _integer(action, "delta_y", 0, minimum=-1200, maximum=1200)
            if dry_run:
                return {"wheel_delta": [delta_x, delta_y], "executed": False}
            self._assert_current_target(session)
            self.input.wheel(delta_x=delta_x, delta_y=delta_y)
            return {"wheel_delta": [delta_x, delta_y], "executed": True}

        if action_type == "aim":
            mapped = self._action_point(action, frame)
            client_x, client_y = mapped["client_point"]
            origin_x = frame.client_rect.width * session.relative_origin_fraction[0]
            origin_y = frame.client_rect.height * session.relative_origin_fraction[1]
            delta_x = int(round((client_x - origin_x) * session.relative_gain_x))
            delta_y = int(round((client_y - origin_y) * session.relative_gain_y))
            chunk_count = max(1, math.ceil(max(abs(delta_x), abs(delta_y)) / session.relative_max_step))
            if chunk_count > 100:
                raise DesktopError(
                    "The calibrated relative aim exceeds 100 movement chunks. Increase relative_max_step "
                    "or reduce the target distance.",
                    code="invalid_action",
                )
            click_after = bool(action.get("click_after", False))
            button = str(action.get("button", "left")).strip().casefold()
            hold_ms = _integer(action, "hold_ms", 35, minimum=0, maximum=2000)
            interval_ms = _integer(action, "interval_ms", 8, minimum=0, maximum=250)
            if dry_run:
                return {
                    "mapping": mapped,
                    "relative_delta": [delta_x, delta_y],
                    "steps": chunk_count,
                    "click_after": click_after,
                    "executed": False,
                }
            chunks = self.input.move_relative(delta_x, delta_y, steps=chunk_count, interval_ms=interval_ms)
            if click_after:
                self._assert_current_target(session)
                self.input.click_current(button=button, hold_ms=hold_ms)
            return {
                "mapping": mapped,
                "relative_delta": [delta_x, delta_y],
                "chunks": chunks,
                "click_after": click_after,
                "executed": True,
            }

        if action_type == "release_all":
            if dry_run:
                return {"executed": False}
            return {"released_buttons": self.input.release_all(force_standard_buttons=True), "executed": True}

        raise DesktopError(
            "Unsupported action type. Use move, click, drag, move_relative, click_current, "
            "mouse_down, mouse_up, wheel, aim, or release_all.",
            code="invalid_action",
        )

    def _assert_action_target(self, session: DesktopSession, screen_point: list[int]) -> None:
        info = win32.get_window_info(session.identity)
        if not info.foreground:
            raise DesktopError("The target window lost foreground focus.", code="foreground_required", status_code=409)
        # During an owned SetCapture drag, input may validly continue outside the
        # original client rectangle. Without capture, never click through another window.
        if not self.input.pressed_buttons or not win32.has_mouse_capture(session.identity):
            win32.assert_target_window_at_point(session.identity, int(screen_point[0]), int(screen_point[1]))

    def _assert_current_target(self, session: DesktopSession) -> None:
        info = win32.get_window_info(session.identity)
        if not info.foreground:
            raise DesktopError("The target window lost foreground focus.", code="foreground_required", status_code=409)
        cursor = info.cursor
        win32.assert_target_window_at_point(session.identity, cursor.x, cursor.y)

    def _prune_expired_sessions(self) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_used_at > self.settings.session_ttl_seconds
        ]
        if expired:
            for session_id in expired:
                self._sessions.pop(session_id, None)
            self.input.release_all(force_standard_buttons=True)

    def shutdown(self) -> None:
        with self._operation_lock:
            self.input.release_all(force_standard_buttons=True)
            self._sessions.clear()

    def readiness(self) -> dict[str, Any]:
        with self._operation_lock:
            return {
                "status": "ready",
                "platform": "windows",
                "input_enabled": self.settings.allow_input,
                "activation_enabled": self.settings.allow_activate,
                "sessions": len(self._sessions),
                "pressed_buttons": self.input.pressed_buttons,
                "inference_url": self.settings.inference_url,
                "capture_mode": self.settings.capture_mode,
            }
