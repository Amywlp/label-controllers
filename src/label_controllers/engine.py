from __future__ import annotations

import hashlib
import io
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image, UnidentifiedImageError

from .accelerator import resolve_ocr_provider, resolve_runtime_devices
from .annotation import render_annotation
from .config import Settings
from .detectors import IconDetector, TextDetector, default_ocr_rec_batch_num
from .fusion import fuse_elements


class ImageValidationError(ValueError):
    """Raised when an uploaded image is empty or unsupported."""


class ImageTooLargeError(ImageValidationError):
    """Raised when an upload exceeds the configured byte limit."""


@dataclass(slots=True)
class AnalysisResult:
    request_id: str
    image_width: int
    image_height: int
    image_format: str
    image_sha256: str
    controls: list[dict[str, Any]]
    elements: list[dict[str, Any]]
    texts: list[dict[str, Any]]
    raw_icons: dict[str, Any]
    raw_ocr: dict[str, Any]
    metadata: dict[str, Any]
    annotated_png: bytes | None = field(default=None, repr=False)

    def to_payload(self, includes: set[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "request_id": self.request_id,
            "image": {
                "width": self.image_width,
                "height": self.image_height,
                "format": self.image_format,
                "sha256": self.image_sha256,
            },
        }
        if "controls" in includes:
            payload["controls"] = self.controls
        if "elements" in includes:
            payload["elements"] = self.elements
        if "texts" in includes:
            payload["texts"] = self.texts
        if "raw_icons" in includes:
            payload["raw_icons"] = self.raw_icons
        if "raw_ocr" in includes:
            payload["raw_ocr"] = self.raw_ocr
        if "metadata" in includes:
            metadata = dict(self.metadata)
            metadata["output_fields"] = sorted(includes)
            payload["metadata"] = metadata
        if "annotated_image" in includes:
            if self.annotated_png is None:
                raise RuntimeError("The annotated image was not rendered for this analysis request.")
            import base64

            payload["annotated_image"] = {
                "mime_type": "image/png",
                "encoding": "base64",
                "byte_length": len(self.annotated_png),
                "data": base64.b64encode(self.annotated_png).decode("ascii"),
            }
        return payload


class AnalysisEngine:
    """Loads the detection models once and serializes inference requests."""

    def __init__(self, settings: Settings):
        if not settings.model_path.is_file():
            raise FileNotFoundError(
                f"YOLO model not found at {settings.model_path}. "
                "Run `python scripts/download_model.py` or use the Docker image, which downloads it at build time."
            )

        load_started = time.perf_counter()
        self.settings = settings

        # OCR must claim the DirectML adapter before torch-directml loads or
        # executes YOLO. Keeping this construction order avoids an ORT DML
        # session failure on the current Windows AMD implementation.
        ocr_provider, _ = resolve_ocr_provider(settings.device, settings.ocr_device)
        rec_batch_num = settings.ocr_rec_batch_num or default_ocr_rec_batch_num(ocr_provider)
        self.text_detector = TextDetector(provider=ocr_provider, rec_batch_num=rec_batch_num)
        self.ocr_warmup_seconds = round(self.text_detector.warmup(), 4)

        self.runtime = resolve_runtime_devices(
            requested_device=settings.device,
            requested_ocr_device=settings.ocr_device,
            device_index=settings.device_index,
            ocr_provider=ocr_provider,
        )
        self.icon_detector = IconDetector(
            settings.model_path,
            device=self.runtime.torch_device,
            backend=self.runtime.backend,
        )
        # Warm-up turns lazy CUDA/DirectML kernel compilation into one-time
        # startup cost and fails fast if a provider or operator is unavailable.
        self.icon_warmup_seconds = round(self.icon_detector.warmup(min(settings.imgsz, 640)), 4)
        self.model_load_seconds = round(time.perf_counter() - load_started, 4)
        self.started_at = time.time()
        self.request_count = 0
        self._lock = threading.Lock()

    @property
    def device_header(self) -> str:
        return self.runtime.backend

    @property
    def model_info(self) -> dict[str, Any]:
        runtime = self.runtime.as_dict()
        return {
            "icon": {
                "name": "OmniParser-v2 icon_detect",
                "family": "YOLO11m",
                "parameters": 20_053_779,
                "file": self.settings.model_path.name,
                "device": self.runtime.backend,
                "device_name": self.runtime.device_name,
                "torch_version": self.runtime.torch_version,
            },
            "ocr": {
                "name": "RapidOCR (PP-OCR ONNX)",
                "engine": "rapidocr-onnxruntime==1.2.3",
                "device": self.runtime.ocr_backend,
                "provider": self.runtime.ocr_provider,
                "rec_batch_num": self.text_detector.rec_batch_num,
            },
            "runtime": runtime,
        }

    def readiness(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "models_loaded": True,
            "model_load_seconds": self.model_load_seconds,
            "warmup_seconds": {
                "icon": self.icon_warmup_seconds,
                "ocr": self.ocr_warmup_seconds,
            },
            "requests_processed": self.request_count,
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "models": self.model_info,
        }

    def analyze(
        self,
        image_bytes: bytes,
        *,
        request_id: str,
        filename: str = "upload",
        icon_confidence: float | None = None,
        ocr_confidence: float | None = None,
        render_annotated: bool = False,
    ) -> AnalysisResult:
        if not image_bytes:
            raise ImageValidationError("The uploaded image is empty.")
        if len(image_bytes) > self.settings.max_upload_bytes:
            raise ImageTooLargeError(f"Uploaded image exceeds the {self.settings.max_upload_mb} MB limit.")

        with self._lock:
            self.request_count += 1
            started = time.perf_counter()
            with TemporaryDirectory(prefix="label-controllers-") as temp_dir:
                canonical_path = Path(temp_dir) / "input.png"
                decode_seconds = self._normalize_image(image_bytes, canonical_path)
                icons, icon_seconds = self.icon_detector.predict(
                    canonical_path,
                    imgsz=self.settings.imgsz,
                    conf=icon_confidence if icon_confidence is not None else self.settings.icon_confidence,
                )
                texts, ocr_seconds = self.text_detector.predict(
                    canonical_path,
                    min_confidence=ocr_confidence if ocr_confidence is not None else self.settings.ocr_confidence,
                )

                fusion_started = time.perf_counter()
                fused = fuse_elements(icons, texts, tuple(fused_size(canonical_path)), screen_origin=(0, 0))
                fusion_seconds = time.perf_counter() - fusion_started

                elements = [self._public_element(item) for item in fused["elements"]]
                controls = [item for item in elements if item["clickable"]]
                ocr_public = [
                    {
                        "id": index,
                        "text": item["text"],
                        "confidence": item["conf"],
                        "rect": item["box"],
                    }
                    for index, item in enumerate(texts, 1)
                ]

                annotation_started = time.perf_counter()
                annotated_png = None
                if render_annotated:
                    annotated_png = render_annotation(
                        canonical_path,
                        fused["elements"],
                        control_count=len(controls),
                        text_count=len(texts),
                    )
                annotation_seconds = time.perf_counter() - annotation_started
                total_seconds = time.perf_counter() - started

                width, height = fused["image_size"]
                metadata = {
                    "schema_version": "1.0",
                    "filename": Path(filename).name[:255],
                    "models": self.model_info,
                    "runtime": self.runtime.as_dict(),
                    "model_load_seconds": self.model_load_seconds,
                    "timing_seconds": {
                        "decode_and_normalize": round(decode_seconds, 4),
                        "icon": round(icon_seconds, 4),
                        "ocr": round(ocr_seconds, 4),
                        "fusion": round(fusion_seconds, 4),
                        "annotation": round(annotation_seconds, 4),
                        "total": round(total_seconds, 4),
                    },
                    "fused_counts": fused["counts"],
                }
                return AnalysisResult(
                    request_id=request_id,
                    image_width=width,
                    image_height=height,
                    image_format=self._image_format(image_bytes),
                    image_sha256=hashlib.sha256(image_bytes).hexdigest(),
                    controls=controls,
                    elements=elements,
                    texts=ocr_public,
                    raw_icons={
                        "model": self.settings.model_path.name,
                        "elapsed_seconds": round(icon_seconds, 4),
                        "detections": icons,
                    },
                    raw_ocr={
                        "engine": "rapidocr-onnxruntime",
                        "provider": self.runtime.ocr_provider,
                        "elapsed_seconds": round(ocr_seconds, 4),
                        "detections": texts,
                    },
                    metadata=metadata,
                    annotated_png=annotated_png,
                )

    def _normalize_image(self, raw: bytes, target: Path) -> float:
        started = time.perf_counter()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as source:
                    if source.width * source.height > self.settings.max_image_pixels:
                        raise ImageValidationError(
                            f"Image has {source.width * source.height:,} pixels; "
                            f"the limit is {self.settings.max_image_pixels:,}."
                        )
                    source.load()
                    source.convert("RGB").save(target, format="PNG")
        except ImageValidationError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImageValidationError("Unsupported or corrupt image. Use PNG, JPEG, WEBP, BMP, or TIFF.") from exc
        return time.perf_counter() - started

    @staticmethod
    def _image_format(raw: bytes) -> str:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                return str(image.format or "UNKNOWN").upper()
        except Exception:
            return "UNKNOWN"

    @staticmethod
    def _public_element(element: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": element["id"],
            "text": element["label"],
            "type": element["subtype"],
            "role": element["role"],
            "confidence": element["conf"],
            "ocr_confidence": element["ocr_conf"],
            "clickable": element["clickable"],
            "rect": element["rect"],
            "center": element["center"],
            "click": element["click"],
            "source": element["source"],
        }


def fused_size(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as image:
        return image.size
