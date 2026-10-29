"""Configuration for the Windows desktop adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _as_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class DesktopSettings:
    host: str = "127.0.0.1"
    port: int = 8022
    token: str = field(default="", repr=False)
    allow_input: bool = False
    allow_activate: bool = False
    session_ttl_seconds: int = 300
    max_actions: int = 32
    safe_margin_px: int = 2
    capture_mode: str = "auto"
    inference_url: str = "http://127.0.0.1:8011"
    inference_api_key: str = field(default="", repr=False)
    inference_timeout_seconds: float = 120.0
    log_level: str = "info"
    expose_docs: bool = True

    @property
    def is_loopback(self) -> bool:
        return self.host in {"127.0.0.1", "::1", "localhost"}

    @classmethod
    def from_env(cls) -> DesktopSettings:
        host = os.getenv("LABEL_CONTROLLERS_DESKTOP_HOST", "127.0.0.1").strip() or "127.0.0.1"
        token = (
            os.getenv("LABEL_CONTROLLERS_DESKTOP_TOKEN", "").strip()
            or os.getenv("LABEL_CONTROLLERS_API_KEY", "").strip()
        )
        allow_input = _as_bool("LABEL_CONTROLLERS_DESKTOP_ALLOW_INPUT", False)
        if not token:
            raise ValueError(
                "A desktop token is required. Set LABEL_CONTROLLERS_DESKTOP_TOKEN or reuse LABEL_CONTROLLERS_API_KEY."
            )

        capture_mode = os.getenv("LABEL_CONTROLLERS_DESKTOP_CAPTURE_MODE", "auto").strip().casefold()
        if capture_mode not in {"auto", "printwindow", "screen"}:
            raise ValueError("LABEL_CONTROLLERS_DESKTOP_CAPTURE_MODE must be auto, printwindow, or screen")

        log_level = os.getenv("LABEL_CONTROLLERS_DESKTOP_LOG_LEVEL", "info").strip().lower()
        if log_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
            raise ValueError("LABEL_CONTROLLERS_DESKTOP_LOG_LEVEL is invalid")

        inference_url = (
            os.getenv("LABEL_CONTROLLERS_DESKTOP_INFERENCE_URL", "http://127.0.0.1:8011").strip()
            or "http://127.0.0.1:8011"
        ).rstrip("/")

        return cls(
            host=host,
            port=_as_int("LABEL_CONTROLLERS_DESKTOP_PORT", 8022),
            token=token,
            allow_input=allow_input,
            allow_activate=_as_bool("LABEL_CONTROLLERS_DESKTOP_ALLOW_ACTIVATE", False),
            session_ttl_seconds=_as_int("LABEL_CONTROLLERS_DESKTOP_SESSION_TTL_SECONDS", 300),
            max_actions=_as_int("LABEL_CONTROLLERS_DESKTOP_MAX_ACTIONS", 32),
            safe_margin_px=_as_int("LABEL_CONTROLLERS_DESKTOP_SAFE_MARGIN_PX", 2, minimum=0),
            capture_mode=capture_mode,
            inference_url=inference_url,
            inference_api_key=(
                os.getenv("LABEL_CONTROLLERS_DESKTOP_INFERENCE_API_KEY", "").strip()
                or os.getenv("LABEL_CONTROLLERS_API_KEY", "").strip()
            ),
            inference_timeout_seconds=_as_float(
                "LABEL_CONTROLLERS_DESKTOP_INFERENCE_TIMEOUT_SECONDS", 120.0, minimum=1.0
            ),
            log_level=log_level,
            expose_docs=_as_bool("LABEL_CONTROLLERS_DESKTOP_EXPOSE_DOCS", True),
        )
