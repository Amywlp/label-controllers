from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from . import a11y as a11y_channel
from .config import Settings
from .contracts import PUBLIC_INCLUDES
from .engine import AnalysisEngine, ImageValidationError


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


def _a11y_spec(args: argparse.Namespace) -> dict | None:
    if getattr(args, "a11y_json", None):
        path = Path(args.a11y_json).expanduser()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Cannot read --a11y-json {path}: {exc}") from exc
    if getattr(args, "a11y_cdp", None):
        spec: dict = {"mode": "cdp", "cdp_url": args.a11y_cdp}
        if getattr(args, "a11y_target", 0):
            spec["target"] = args.a11y_target
        if getattr(args, "a11y_url", None):
            spec["url"] = args.a11y_url
        return spec
    return None


def analyze_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    image_path = Path(args.input).expanduser().resolve()
    if not image_path.is_file():
        print(f"Input image does not exist: {image_path}", file=sys.stderr)
        return 2
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    spec = _a11y_spec(args)
    include = set(args.include)
    if spec and "all" not in include:
        include.update({"a11y", "alignment"})

    engine = AnalysisEngine(settings)
    result = engine.analyze(
        image_path.read_bytes(),
        request_id=uuid.uuid4().hex,
        filename=image_path.name,
        icon_confidence=args.icon_confidence,
        ocr_confidence=args.ocr_confidence,
        render_annotated=True,
        a11y=spec,
    )
    annotation_path = output_dir / "annotated.png"
    if result.annotated_png is None:
        raise RuntimeError("Annotated PNG was requested but not rendered.")
    annotation_path.write_bytes(result.annotated_png)
    json_path = output_dir / "result.json"
    json_path.write_text(
        json.dumps(result.to_payload(include), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        "request_id": result.request_id,
        "controls": len(result.controls),
        "texts": len(result.texts),
        "elements": len(result.elements),
        "total_seconds": result.metadata["timing_seconds"]["total"],
        "result": str(json_path),
        "annotation": str(annotation_path),
    }
    if result.alignment is not None:
        summary["a11y_alignment"] = {
            "matched": result.alignment["matched"],
            "vision_to_a11y_rate": result.alignment["vision_to_a11y_rate"],
            "a11y_to_vision_rate": result.alignment["a11y_to_vision_rate"],
            "text_conflicts": result.alignment["text_conflicts"],
            "a11y_only_added": result.alignment["a11y_only_added"],
        }
    elif result.metadata.get("a11y", {}).get("error"):
        summary["a11y_error"] = result.metadata["a11y"]["error"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def a11y_probe_command(args: argparse.Namespace) -> int:
    spec = _a11y_spec(args)
    if spec is None:
        print("Pass --a11y-cdp URL or --a11y-json FILE.", file=sys.stderr)
        return 2
    try:
        result = a11y_channel.probe(spec)
    except a11y_channel.A11yError as exc:
        error = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2
    output = Path(args.output).expanduser() if args.output else None
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        summary = {"ok": True, "output": str(output), "strategy": result["verdict"]["strategy"]}
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(text)
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


def mcp_command(args: argparse.Namespace) -> int:
    from . import mcp_server

    return mcp_server.main()


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
    analyze_parser.add_argument(
        "--a11y-cdp",
        default=None,
        help="DevTools endpoint for the accessibility channel, e.g. http://127.0.0.1:9222",
    )
    analyze_parser.add_argument("--a11y-target", type=int, default=0, help="Page target index for --a11y-cdp")
    analyze_parser.add_argument("--a11y-url", default=None, help="Navigate the CDP target before reading the tree")
    analyze_parser.add_argument("--a11y-json", default=None, help="Pre-collected a11y tree JSON file")
    analyze_parser.set_defaults(handler=analyze_command)

    probe_parser = subparsers.add_parser("a11y-probe", help="Grade an accessibility tree without a screenshot")
    probe_parser.add_argument("--a11y-cdp", default=None, help="DevTools endpoint, e.g. http://127.0.0.1:9222")
    probe_parser.add_argument("--a11y-target", type=int, default=0)
    probe_parser.add_argument("--a11y-url", default=None)
    probe_parser.add_argument("--a11y-json", default=None, help="Pre-collected a11y tree JSON file")
    probe_parser.add_argument("--output", default=None, help="Write the report to this path")
    probe_parser.set_defaults(handler=a11y_probe_command)

    serve_parser = subparsers.add_parser("serve", help="Run the HTTP API")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(handler=serve_command)

    mcp_parser = subparsers.add_parser("mcp", help="Run the MCP stdio server")
    mcp_parser.set_defaults(handler=mcp_command)
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
