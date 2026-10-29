"""Compare two benchmark JSON files and summarize accuracy parity and speed.

Usage:
    python scripts/compare_benchmarks.py cpu.json directml.json \
        --output artifacts/benchmark/comparison.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any


def iou(box_a: list[int | float], box_b: list[int | float]) -> float:
    x1 = max(float(box_a[0]), float(box_b[0]))
    y1 = max(float(box_a[1]), float(box_b[1]))
    x2 = min(float(box_a[2]), float(box_b[2]))
    y2 = min(float(box_a[3]), float(box_b[3]))
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, float(box_a[2]) - float(box_a[0])) * max(0.0, float(box_a[3]) - float(box_a[1]))
    area_b = max(0.0, float(box_b[2]) - float(box_b[0])) * max(0.0, float(box_b[3]) - float(box_b[1]))
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def greedy_matches(
    reference: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    threshold: float = 0.5,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], float]], int, int]:
    pairs = []
    for reference_index, ref_item in enumerate(reference):
        for candidate_index, candidate_item in enumerate(candidate):
            overlap = iou(ref_item["rect"], candidate_item["rect"])
            if overlap >= threshold:
                pairs.append((overlap, reference_index, candidate_index))
    pairs.sort(reverse=True)

    used_reference: set[int] = set()
    used_candidate: set[int] = set()
    matches = []
    for overlap, reference_index, candidate_index in pairs:
        if reference_index in used_reference or candidate_index in used_candidate:
            continue
        used_reference.add(reference_index)
        used_candidate.add(candidate_index)
        matches.append((reference[reference_index], candidate[candidate_index], overlap))
    return matches, len(reference) - len(used_reference), len(candidate) - len(used_candidate)


def text_equal(reference: dict[str, Any], candidate: dict[str, Any]) -> bool:
    return " ".join(str(reference.get("text", "")).split()) == " ".join(str(candidate.get("text", "")).split())


def role_equal(reference: dict[str, Any], candidate: dict[str, Any]) -> bool:
    return reference.get("role") == candidate.get("role")


def click_distance(
    reference: dict[str, Any],
    candidate: dict[str, Any],
    transform: Callable[[dict[str, Any]], tuple[float, float]] | None = None,
) -> float:
    if transform:
        ref_xy = transform(reference)
        cand_xy = transform(candidate)
    else:
        ref_click = reference.get("click") or reference.get("center")
        cand_click = candidate.get("click") or candidate.get("center")
        ref_xy = _point_xy(ref_click)
        cand_xy = _point_xy(cand_click)
    return math.hypot(ref_xy[0] - cand_xy[0], ref_xy[1] - cand_xy[1])


def _point_xy(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return float(value["x"]), float(value["y"])
    return float(value[0]), float(value[1])


def compare_group(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    matches, missing, extra = greedy_matches(reference, candidate)
    matched = len(matches)
    precision = matched / len(candidate) if candidate else 1.0
    recall = matched / len(reference) if reference else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 1.0
    text_accuracy = sum(text_equal(ref, cand) for ref, cand, _ in matches) / matched if matched else 1.0
    role_accuracy = sum(role_equal(ref, cand) for ref, cand, _ in matches) / matched if matched else 1.0
    click_distances = []
    for ref, cand, _ in matches:
        if (ref.get("click") or ref.get("center")) and (cand.get("click") or cand.get("center")):
            click_distances.append(click_distance(ref, cand))
    return {
        "reference_count": len(reference),
        "candidate_count": len(candidate),
        "matched": matched,
        "missing": missing,
        "extra": extra,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "text_exact_accuracy_on_matches": round(text_accuracy, 6),
        "role_accuracy_on_matches": round(role_accuracy, 6),
        "mean_iou": round(statistics.mean([match[2] for match in matches]), 6) if matches else 1.0,
        "min_iou": round(min([match[2] for match in matches]), 6) if matches else 1.0,
        "mean_click_distance_px": round(statistics.mean(click_distances), 4) if click_distances else 0.0,
        "max_click_distance_px": round(max(click_distances), 4) if click_distances else 0.0,
    }


def pair_images(reference: dict[str, Any], candidate: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    ref_by_sha = {item["image"]["sha256"]: item for item in reference["results"]}
    cand_by_sha = {item["image"]["sha256"]: item for item in candidate["results"]}
    if set(ref_by_sha) != set(cand_by_sha):
        missing = sorted(set(ref_by_sha) - set(cand_by_sha))
        extra = sorted(set(cand_by_sha) - set(ref_by_sha))
        raise ValueError(f"Image sets differ. Missing from candidate: {missing}; extra: {extra}")
    return [(ref_by_sha[sha], cand_by_sha[sha]) for sha in sorted(ref_by_sha)]


def aggregate_group(pairs: list[tuple[dict[str, Any], dict[str, Any]]], key: str) -> dict[str, Any]:
    reference = [item for ref, _ in pairs for item in ref[key]]
    candidate = [item for _, cand in pairs for item in cand[key]]
    return compare_group(reference, candidate)


def runtime_timing(report: dict[str, Any]) -> dict[str, float]:
    passes = report["passes"]
    totals = [float(item["timing_seconds"]["total"]) for current_pass in passes for item in current_pass["results"]]
    return {
        "requests": len(totals),
        "mean_seconds": round(statistics.mean(totals), 4),
        "median_seconds": round(statistics.median(totals), 4),
        "min_seconds": round(min(totals), 4),
        "max_seconds": round(max(totals), 4),
    }


def compare(reference_path: Path, candidate_path: Path) -> dict[str, Any]:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if reference.get("schema_version") != "1.0" or candidate.get("schema_version") != "1.0":
        raise ValueError("Unsupported benchmark schema version")
    pairs = pair_images(reference["passes"][0], candidate["passes"][0])

    per_image = []
    for ref, cand in pairs:
        per_image.append(
            {
                "sha256": ref["image"]["sha256"],
                "filename": ref["image"]["filename"],
                "controls": compare_group(ref["controls"], cand["controls"]),
                "elements": compare_group(ref["elements"], cand["elements"]),
                "texts": compare_group(ref["texts"], cand["texts"]),
                "timing_seconds": {
                    "reference": ref["timing_seconds"]["total"],
                    "candidate": cand["timing_seconds"]["total"],
                },
            }
        )

    ref_timing = runtime_timing(reference)
    cand_timing = runtime_timing(candidate)
    speedup = ref_timing["mean_seconds"] / cand_timing["mean_seconds"] if cand_timing["mean_seconds"] else 0.0
    return {
        "schema_version": "1.0",
        "reference": {
            "path": str(reference_path.resolve()),
            "runtime": reference["runtime"],
            "timing": ref_timing,
        },
        "candidate": {
            "path": str(candidate_path.resolve()),
            "runtime": candidate["runtime"],
            "timing": cand_timing,
        },
        "speed": {
            "mean_speedup": round(speedup, 4),
            "latency_reduction_percent": round((1 - 1 / speedup) * 100, 2) if speedup else 0.0,
        },
        "accuracy": {
            "controls": aggregate_group(pairs, "controls"),
            "elements": aggregate_group(pairs, "elements"),
            "texts": aggregate_group(pairs, "texts"),
            "image_count": len(pairs),
        },
        "per_image": per_image,
        "notes": [
            "Accuracy is output parity against the selected reference backend, not ground-truth annotation accuracy.",
            "Control and element matches use intersection-over-union >= 0.5 with greedy one-to-one assignment.",
            "Text exact accuracy is calculated only on box-matched OCR blocks.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Label Controllers CPU/GPU benchmarks")
    parser.add_argument("reference", help="Reference benchmark JSON, normally CPU")
    parser.add_argument("candidate", help="Candidate benchmark JSON, normally GPU")
    parser.add_argument("--output", help="Optional comparison JSON output path")
    args = parser.parse_args()

    result = compare(Path(args.reference), Path(args.candidate))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
