"""Minimal Python client for Label Controllers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1/analyze")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--include", default="controls,texts,metadata")
    parser.add_argument("--format", choices=["json", "png"], default="json")
    parser.add_argument("--output", type=Path, default=Path("result"))
    args = parser.parse_args()

    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    with args.image.open("rb") as image_file:
        response = requests.post(
            args.url,
            headers=headers,
            files={"file": (args.image.name, image_file, "application/octet-stream")},
            data={"include": args.include, "response_format": args.format},
            timeout=180,
        )
    response.raise_for_status()

    if args.format == "png":
        output = args.output.with_suffix(".png")
        output.write_bytes(response.content)
    else:
        payload = response.json()
        output = args.output.with_suffix(".json")
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"controls={len(payload.get('controls', []))} texts={len(payload.get('texts', []))}")
    print(output)


if __name__ == "__main__":
    main()