from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _as_float(name: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    model_path: Path
    api_key: str = field(default="", repr=False)
    max_upload_mb: int = 20
    max_image_pixels: int = 40_000_000
    imgsz: int = 1280
    icon_confidence: float = 0.18
    ocr_confidence: float = 0.40
    cors_origins: tuple[str, ...] = ()
    log_level: str = "info"
    expose_docs: bool = True

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        project_root = Path(__file__).resolve().parents[2]
        model_path = Path(os.getenv("LABEL_CONTROLLERS_MODEL_PATH", str(project_root / "models" / "icon_detect.pt")))
        cors = tuple(
            item.strip() for item in os.getenv("LABEL_CONTROLLERS_CORS_ORIGINS", "").split(",") if item.strip()
        )
        log_level = os.getenv("LABEL_CONTROLLERS_LOG_LEVEL", "info").strip().lower()
        if log_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
            raise ValueError("LABEL_CONTROLLERS_LOG_LEVEL is invalid")
        return cls(
            model_path=model_path,
            api_key=os.getenv("LABEL_CONTROLLERS_API_KEY", "").strip(),
            max_upload_mb=_as_int("LABEL_CONTROLLERS_MAX_UPLOAD_MB", 20),
            max_image_pixels=_as_int("LABEL_CONTROLLERS_MAX_IMAGE_PIXELS", 40_000_000, minimum=100_000),
            imgsz=_as_int("LABEL_CONTROLLERS_IMGSZ", 1280, minimum=320),
            icon_confidence=_as_float("LABEL_CONTROLLERS_ICON_CONFIDENCE", 0.18),
            ocr_confidence=_as_float("LABEL_CONTROLLERS_OCR_CONFIDENCE", 0.40),
            cors_origins=cors,
            log_level=log_level,
            expose_docs=_as_bool(os.getenv("LABEL_CONTROLLERS_EXPOSE_DOCS"), True),
        )
