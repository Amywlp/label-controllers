from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .config import Settings
from .engine import PUBLIC_INCLUDES, AnalysisEngine, ImageValidationError


def _includes(value: str) -> set[str]:
    fields = {item.strip().casefold() for item in value.split(",") if item.strip()}
    if "all" in fields:
        return set(PUBLIC_INCLUDES)
    invalid = fields - set(PUBLIC_INCLUDES)
    if not fields or invalid:
        raise argparse.ArgumentTypeError(
            f"choose from {', '.join(PUBLIC_INCLUDES)}, or all; invalid: {', '.join(sorted(invalid)) or 'none'}"
        )
    return fields


def analyze_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    image_path = Path(args.input).expanduser().resolve()
    if not image_path.is_file():
        print(f"Input image does not exist: {image_path}", file=sys.stderr)
        return 2
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    engine = AnalysisEngine(settings)
    result = engine.analyze(
        image_path.read_bytes(),
        request_id=uuid.uuid4().hex,
        filename=image_path.name,
        icon_confidence=args.icon_confidence,
        ocr_confidence=args.ocr_confidence,
        render_annotated=True,
    )
    annotation_path = output_dir / "annotated.png"
    if result.annotated_png is None:
        raise RuntimeError("Annotated PNG was requested but not rendered.")
    annotation_path.write_bytes(result.annotated_png)
    json_path = output_dir / "result.json"
    json_path.write_text(
        json.dumps(result.to_payload(args.include), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "request_id": result.request_id,
                "controls": len(result.controls),
                "texts": len(result.texts),
                "elements": len(result.elements),
                "total_seconds": result.metadata["timing_seconds"]["total"],
                "result": str(json_path),
                "annotation": str(annotation_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def serve_command(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "label_controllers.api:app",
        host=args.host,
        port=args.port,
        log_level=Settings.from_env().log_level,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="label-controllers", description="UI screenshot control labeling service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze one local image")
    analyze_parser.add_argument("--input", required=True, help="Path to a screenshot")
    analyze_parser.add_argument(
        "--output-dir", default="artifacts/cli", help="Directory for result.json and annotated.png"
    )
    analyze_parser.add_argument(
        "--include",
        type=_includes,
        default={"controls", "texts", "metadata"},
        help="Comma-separated output fields or all",
    )
    analyze_parser.add_argument("--icon-confidence", type=float, default=None)
    analyze_parser.add_argument("--ocr-confidence", type=float, default=None)
    analyze_parser.set_defaults(handler=analyze_command)

    serve_parser = subparsers.add_parser("serve", help="Run the HTTP API")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(handler=serve_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ImageValidationError as exc:
        print(f"Invalid image: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
