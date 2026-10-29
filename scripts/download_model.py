from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import requests

PRIMARY_URL = "https://huggingface.co/microsoft/OmniParser-v2.0/resolve/main/icon_detect/model.pt"
MIRROR_URL = "https://hf-mirror.com/microsoft/OmniParser-v2.0/resolve/main/icon_detect/model.pt"
EXPECTED_SIZE = 40_623_819
EXPECTED_SHA256 = "dab3d4351ad00b035db829909a4db98354d5a90f6990e4ac00222a9a95d4bf57"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(path: Path) -> bool:
    return path.is_file() and path.stat().st_size == EXPECTED_SIZE and file_digest(path) == EXPECTED_SHA256


def download_from(url: str, partial: Path) -> None:
    digest = hashlib.sha256()
    total = 0
    headers = {"User-Agent": "label-controllers-model-downloader/0.1"}
    with requests.get(url, stream=True, timeout=(20, 180), headers=headers) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                print(f"\r{total / 1024 / 1024:6.1f} MB", end="", flush=True)
    print()
    if total != EXPECTED_SIZE or digest.hexdigest() != EXPECTED_SHA256:
        raise RuntimeError(f"Downloaded model failed validation: size={total} sha256={digest.hexdigest()}")


def download(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if verified(target):
        print(f"Verified model already exists: {target}")
        return
    target.unlink(missing_ok=True)

    configured = os.getenv("LABEL_CONTROLLERS_MODEL_URL", "").strip()
    candidates = list(dict.fromkeys([value for value in (configured, MIRROR_URL, PRIMARY_URL) if value]))
    partial = target.with_suffix(target.suffix + ".part")
    errors: list[str] = []
    for attempt in range(1, 4):
        for url in candidates:
            try:
                print(f"Attempt {attempt}: downloading {url}")
                download_from(url, partial)
                if not verified(partial):
                    raise RuntimeError("Downloaded file failed final verification")
                partial.replace(target)
                print(f"Saved and verified: {target}")
                return
            except Exception as exc:
                partial.unlink(missing_ok=True)
                message = f"{url}: {type(exc).__name__}: {exc}"
                errors.append(message)
                print(f"WARNING: {message}", file=sys.stderr)
        time.sleep(attempt)
    raise RuntimeError("All model download attempts failed:\n" + "\n".join(errors))


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Download and verify the OmniParser icon model")
    parser.add_argument("--output", type=Path, default=project_root / "models" / "icon_detect.pt")
    args = parser.parse_args()
    download(args.output.resolve())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise