from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from ultralytics import YOLO


class IconDetector:
    def __init__(self, weights: str | Path, device: str = "cpu"):
        self.weights = str(weights)
        self.device = device
        self.model = YOLO(self.weights)

    def predict(
        self, image_path: str | Path, imgsz: int = 1280, conf: float = 0.20
    ) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        results = self.model.predict(
            source=str(image_path),
            imgsz=imgsz,
            conf=conf,
            iou=0.55,
            device=self.device,
            verbose=False,
        )
        elapsed = time.perf_counter() - started
        result = results[0]
        boxes = result.boxes
        detections: list[dict[str, Any]] = []
        if boxes is None:
            return detections, elapsed
        xyxy = boxes.xyxy.cpu().numpy() if boxes.xyxy is not None else np.empty((0, 4))
        scores = boxes.conf.cpu().numpy() if boxes.conf is not None else np.empty((0,))
        classes = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else np.empty((0,), dtype=int)
        for index, (box, score, cls) in enumerate(zip(xyxy, scores, classes, strict=False)):
            detections.append(
                {
                    "index": index,
                    "box": [int(round(float(v))) for v in box.tolist()],
                    "conf": round(float(score), 4),
                    "class_id": int(cls),
                    "class_name": str(result.names.get(int(cls), cls)),
                }
            )
        return detections, elapsed


class TextDetector:
    def __init__(self):
        self.engine = RapidOCR()

    def predict(self, image_path: str | Path, min_confidence: float = 0.40) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        result, _ = self.engine(str(image_path), use_det=True, use_cls=True, use_rec=True)
        elapsed = time.perf_counter() - started
        detections: list[dict[str, Any]] = []
        for index, row in enumerate(result or []):
            if len(row) < 3:
                continue
            quad, text, score = row[0], str(row[1]), float(row[2])
            if score < min_confidence or not text.strip():
                continue
            points = np.asarray(quad, dtype=float)
            x1, y1 = points.min(axis=0).tolist()
            x2, y2 = points.max(axis=0).tolist()
            detections.append(
                {
                    "index": index,
                    "box": [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))],
                    "quad": [[round(float(x), 2), round(float(y), 2)] for x, y in points.tolist()],
                    "text": text.strip(),
                    "conf": round(score, 4),
                }
            )
        return detections, elapsed


def load_rgb(image_path: str | Path) -> np.ndarray:
    with Image.open(image_path) as image:
        return np.asarray(image.convert("RGB"))
