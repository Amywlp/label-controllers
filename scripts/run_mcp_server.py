"""Launch the MCP bridge with optional project-local .env settings.

MCP clients should launch this Python file directly. The launcher reads the
repository .env without printing anything, then hands full ownership of stdout
to the newline-delimited JSON-RPC server.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        name, value = trimmed.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and value and not os.environ.get(name):
            os.environ[name] = value


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    load_env_file(root / ".env")
    source = root / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from label_controllers.mcp_server import main as server_main

    return server_main(sys.argv[1:])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as exc:
        print(f"Could not start Label Controllers MCP server: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
