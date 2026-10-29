"""OpenAI function-calling example for the Label Controllers tool.

Requires the optional OpenAI SDK and a running Label Controllers service:

    pip install openai
    python examples/openai_tool_call.py screenshot.png

The screenshot itself is owned by the tool executor. The language model chooses
analysis options and receives only the structured result, never image bytes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from openai import OpenAI

from label_controllers.tool import TOOL_NAME, LabelControllersTool, ToolCallError


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python examples/openai_tool_call.py <screenshot>", file=sys.stderr)
        return 2
    screenshot = Path(sys.argv[1]).expanduser().resolve()
    if not screenshot.is_file():
        print(f"screenshot not found: {screenshot}", file=sys.stderr)
        return 2

    tool = LabelControllersTool(
        base_url=os.getenv("LABEL_CONTROLLERS_BASE_URL", "http://127.0.0.1:8000"),
        screenshot_provider=lambda: screenshot.read_bytes(),
    )
    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    messages = [
        {
            "role": "user",
            "content": (
                "Inspect the current UI screenshot with the provided tool. "
                "Summarize the main clickable controls and call out any ambiguous areas."
            ),
        }
    ]
    first = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=[tool.definition],
        tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
    )
    assistant = first.choices[0].message
    tool_calls = assistant.tool_calls or []
    if not tool_calls:
        print(assistant.content or "The model did not request the tool.")
        return 0

    messages.append(
        {
            "role": "assistant",
            "content": assistant.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ],
        }
    )
    for call in tool_calls:
        try:
            result = tool.execute_tool_call(call.function.arguments)
        except ToolCallError as exc:
            print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    final = client.chat.completions.create(model=model, messages=messages, tools=[tool.definition])
    print(final.choices[0].message.content or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
