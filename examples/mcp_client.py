"""Drive the MCP stdio server by hand (no MCP client library required).

Run the API first, then:

    LABEL_CONTROLLERS_BASE_URL=http://127.0.0.1:8000 \
    PYTHONPATH=src python examples/mcp_client.py /tmp/shot.png http://127.0.0.1:9222
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def send(process: subprocess.Popen[str], payload: dict) -> dict:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()
    assert process.stdout is not None
    while True:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout")
        message = json.loads(line)
        if message.get("id") == payload.get("id"):
            return message


def main() -> int:
    image_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shot.png"
    cdp_url = sys.argv[2] if len(sys.argv) > 2 else None

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "label_controllers.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        env=env,
    )
    try:
        print(json.dumps(send(process, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "example", "version": "1.0"},
            },
        })["result"]["serverInfo"], ensure_ascii=False))

        tools = send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        print("tools:", [tool["name"] for tool in tools["result"]["tools"]])

        if cdp_url:
            probe = send(process, {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "probe_ui_accessibility", "arguments": {"cdp_url": cdp_url}},
            })
            text = probe["result"]["content"][0]["text"]
            report = json.loads(text)
            print("probe strategy:", report.get("verdict", {}).get("strategy"))

        arguments: dict = {"image_path": image_path, "include": ["controls"]}
        if cdp_url:
            arguments["a11y"] = {"mode": "cdp", "cdp_url": cdp_url}
        result = send(process, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "analyze_ui_screenshot", "arguments": arguments},
        })
        payload = json.loads(result["result"]["content"][0]["text"])
        print("controls:", len(payload.get("controls", [])))
        if "alignment" in payload:
            alignment = payload["alignment"]
            print(
                "alignment:",
                alignment.get("matched"),
                alignment.get("vision_to_a11y_rate"),
                alignment.get("a11y_to_vision_rate"),
                "conflicts:",
                alignment.get("text_conflicts"),
            )
    finally:
        process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
