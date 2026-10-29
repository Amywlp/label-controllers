"""Repeatable end-to-end latency benchmark for CPU and GPU backends.

Usage:
    python scripts/benchmark.py --images "artifacts/frames/*.png" \
        --device directml --ocr-device directml \
        --output artifacts/benchmark/directml.json --repeat 3
"""

from __future__ import annotations

import argparse
import glob as glob_module
import hashlib
import json
import platform
import re
import statistics
import time
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import psutil

from label_controllers.config import Settings
from label_controllers.engine import AnalysisEngine

IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
STAGES = ("decode_and_normalize", "icon", "ocr", "fusion", "annotation", "total")


def expand_images(patterns: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in patterns:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            paths.extend(path for path in candidate.rglob("*") if path.suffix.casefold() in IMAGE_EXTENSIONS)
            continue
        if any(char in raw for char in "*?["):
            paths.extend(Path(match) for match in glob_module.glob(raw, recursive=True))
            continue
        if candidate.is_file():
            paths.append(candidate)
            continue
        raise FileNotFoundError(f"Image path or glob did not match anything: {raw}")
    unique = {path.resolve() for path in paths if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS}
    if not unique:
        raise FileNotFoundError("No supported images were found.")
    return sorted(unique, key=lambda path: str(path).casefold())


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def pretty_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "image"


def compact_element(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "text": item["text"],
        "type": item["type"],
        "role": item["role"],
        "confidence": item["confidence"],
        "ocr_confidence": item["ocr_confidence"],
        "clickable": item["clickable"],
        "rect": item["rect"],
        "center": item["center"],
        "click": item["click"],
        "source": item["source"],
    }


def result_snapshot(result: Any, image_path: Path) -> dict[str, Any]:
    return {
        "image": {
            "path": str(image_path),
            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "width": result.image_width,
            "height": result.image_height,
            "filename": image_path.name,
        },
        "counts": {
            "controls": len(result.controls),
            "elements": len(result.elements),
            "texts": len(result.texts),
            "raw_icons": len(result.raw_icons.get("detections", [])),
            "raw_ocr": len(result.raw_ocr.get("detections", [])),
        },
        "controls": [compact_element(item) for item in result.controls],
        "elements": [compact_element(item) for item in result.elements],
        "texts": result.texts,
        "timing_seconds": result.metadata["timing_seconds"],
    }


def timing_summary(results: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for stage in STAGES:
        values = [float(item["timing_seconds"][stage]) for item in results]
        summary[stage] = {
            "mean": round(statistics.mean(values), 4),
            "median": round(statistics.median(values), 4),
            "p95": round(percentile(values, 0.95), 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
        }
    return summary


def run(args: argparse.Namespace) -> dict[str, Any]:
    images = expand_images(args.images)
    settings = Settings.from_env()
    settings = replace(
        settings,
        model_path=Path(args.model).expanduser().resolve() if args.model else settings.model_path,
        imgsz=args.imgsz,
        device=args.device,
        ocr_device=args.ocr_device,
        device_index=args.device_index,
    )
    engine = AnalysisEngine(settings)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    # The engine performs its own startup warm-up. One discarded full request
    # per backend also warms filesystem and annotation caches before measuring.
    for warmup_index in range(args.warmup):
        path = images[warmup_index % len(images)]
        engine.analyze(
            path.read_bytes(),
            request_id=f"warmup-{warmup_index}",
            filename=path.name,
            render_annotated=args.annotated,
        )

    passes: list[dict[str, Any]] = []
    for pass_index in range(args.repeat):
        pass_started = time.perf_counter()
        results = []
        for image_index, path in enumerate(images):
            result = engine.analyze(
                path.read_bytes(),
                request_id=f"benchmark-{pass_index}-{image_index}",
                filename=path.name,
                render_annotated=args.annotated,
            )
            snapshot = result_snapshot(result, path)
            snapshot["wall_seconds"] = round(result.metadata["timing_seconds"]["total"], 4)
            results.append(snapshot)
            print(
                f"pass={pass_index + 1}/{args.repeat} image={image_index + 1}/{len(images)} "
                f"total={snapshot['timing_seconds']['total']:.4f}s controls={snapshot['counts']['controls']}",
                flush=True,
            )
        passes.append(
            {
                "index": pass_index,
                "wall_seconds": round(time.perf_counter() - pass_started, 4),
                "timings": timing_summary(results),
                "results": results,
            }
        )

    payload = {
        "schema_version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor() or "unknown",
            "logical_cpus": psutil.cpu_count(logical=True),
            "ram_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        },
        "configuration": {
            "model": str(settings.model_path),
            "device": settings.device,
            "ocr_device": settings.ocr_device,
            "device_index": settings.device_index,
            "imgsz": settings.imgsz,
            "images": [str(path) for path in images],
            "warmup_requests": args.warmup,
            "measured_passes": args.repeat,
            "render_annotated": args.annotated,
        },
        "runtime": engine.runtime.as_dict(),
        "models": engine.model_info,
        "model_load_seconds": engine.model_load_seconds,
        "warmup_seconds": {
            "icon": engine.icon_warmup_seconds,
            "ocr": engine.ocr_warmup_seconds,
        },
        "passes": passes,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "runtime": payload["runtime"]}, ensure_ascii=False, indent=2))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Label Controllers end-to-end latency and output counts")
    parser.add_argument("--images", nargs="+", required=True, help="Files, directories or glob patterns")
    parser.add_argument("--output", required=True, help="Destination JSON file")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "rocm", "directml", "mps"], default="auto")
    parser.add_argument("--ocr-device", choices=["auto", "cpu", "cuda", "rocm", "directml"], default="auto")
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--model", help="Override the YOLO model path")
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--annotated",
        action="store_true",
        help="Include annotation rendering, as used by include=annotated_image and response_format=png",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.repeat < 1 or args.warmup < 0:
        raise SystemExit("--repeat must be >= 1 and --warmup must be >= 0")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
