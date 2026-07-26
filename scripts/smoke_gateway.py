from __future__ import annotations

import argparse
import json

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    args = parser.parse_args()
    payload = {
        "model": "codex",
        "messages": [
            {
                "role": "user",
                "content": "Call record_answer with answer 42. Do not answer in plain text.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "record_answer",
                    "description": "Record the requested integer",
                    "parameters": {
                        "type": "object",
                        "properties": {"answer": {"type": "integer"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "record_answer"}},
    }
    response = httpx.post(
        f"{args.base_url.rstrip('/')}/chat/completions",
        json=payload,
        timeout=660,
    )
    response.raise_for_status()
    body = response.json()
    choice = body["choices"][0]
    call = choice["message"]["tool_calls"][0]
    arguments = json.loads(call["function"]["arguments"])
    assert body["model"] == "codex"
    assert choice["finish_reason"] == "tool_calls"
    assert call["function"]["name"] == "record_answer"
    assert arguments == {"answer": 42}
    assert all(
        body["usage"][key] >= 0
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    )
    print(json.dumps(body, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
