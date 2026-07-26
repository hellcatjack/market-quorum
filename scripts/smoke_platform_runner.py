from __future__ import annotations

import argparse
import json
import uuid

import httpx


def _read_status(client: httpx.Client, base_url: str) -> dict:
    response = client.get(f"{base_url.rstrip('/')}/internal/status", timeout=10)
    response.raise_for_status()
    status = response.json()
    required = ("model", "reasoning_effort", "snapshot_id")
    if status.get("status") != "ok" or not all(status.get(key) for key in required):
        raise RuntimeError("Gateway returned an incomplete effective configuration")
    if len(status["snapshot_id"]) != 64:
        raise RuntimeError("Gateway returned an invalid configuration snapshot ID")
    return status


def run_smoke(
    *,
    base_url: str,
    run_id: str,
    client: httpx.Client | None = None,
) -> dict:
    owned_client = client is None
    client = client or httpx.Client()
    try:
        initial_status = _read_status(client, base_url)
        pinned_snapshot = {
            key: initial_status[key]
            for key in ("model", "reasoning_effort", "snapshot_id")
        }
        recorded_snapshot = dict(pinned_snapshot)
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
        response = client.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers={
                "X-TradingNG-Run-ID": run_id,
                "X-TradingNG-Codex-Model": pinned_snapshot["model"],
                "X-TradingNG-Codex-Reasoning-Effort": pinned_snapshot[
                    "reasoning_effort"
                ],
            },
            json=payload,
            timeout=660,
        )
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        call = choice["message"]["tool_calls"][0]
        arguments = json.loads(call["function"]["arguments"])
        if body["model"] != "codex":
            raise RuntimeError("Gateway changed the public model alias")
        if choice["finish_reason"] != "tool_calls":
            raise RuntimeError("Gateway did not return the forced tool call")
        if call["function"]["name"] != "record_answer" or arguments != {"answer": 42}:
            raise RuntimeError("Gateway returned an unexpected tool call")

        latest_status = _read_status(client, base_url)
        if pinned_snapshot != recorded_snapshot:
            raise RuntimeError("Recorded run snapshot mutated during execution")
        return {
            "run_id": run_id,
            "pinned_snapshot": pinned_snapshot,
            "latest_snapshot": {
                key: latest_status[key]
                for key in ("model", "reasoning_effort", "snapshot_id")
            },
            "usage": body["usage"],
            "tool_call": {"name": call["function"]["name"], "arguments": arguments},
        }
    finally:
        if owned_client:
            client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--run-id", default=f"platform-smoke-{uuid.uuid4().hex}")
    arguments = parser.parse_args()
    print(
        json.dumps(
            run_smoke(base_url=arguments.base_url, run_id=arguments.run_id),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
