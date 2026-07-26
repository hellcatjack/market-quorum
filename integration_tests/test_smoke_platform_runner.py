import importlib.util
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _load_smoke_module():
    path = ROOT / "scripts" / "smoke_platform_runner.py"
    spec = importlib.util.spec_from_file_location("smoke_platform_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_pins_first_status_snapshot_and_uses_one_completion():
    module = _load_smoke_module()
    counts = {"status": 0, "completion": 0}

    def handler(request):
        if request.url.path == "/internal/status":
            counts["status"] += 1
            suffix = "a" if counts["status"] == 1 else "b"
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "active_completions": 0,
                    "model": f"model-{suffix}",
                    "reasoning_effort": "high" if suffix == "a" else "medium",
                    "snapshot_id": suffix * 64,
                },
            )
        counts["completion"] += 1
        assert request.headers["X-TradingNG-Run-ID"] == "smoke-run"
        assert request.headers["X-TradingNG-Codex-Model"] == "model-a"
        assert request.headers["X-TradingNG-Codex-Reasoning-Effort"] == "high"
        return httpx.Response(
            200,
            json={
                "model": "codex",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "record_answer",
                                        "arguments": '{"answer":42}',
                                    }
                                }
                            ]
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = module.run_smoke(
            base_url="http://gateway",
            run_id="smoke-run",
            client=client,
        )

    assert counts == {"status": 2, "completion": 1}
    assert result["pinned_snapshot"]["snapshot_id"] == "a" * 64
    assert result["latest_snapshot"]["snapshot_id"] == "b" * 64
