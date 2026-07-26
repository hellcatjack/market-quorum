import json
import logging

import pytest
from starlette.testclient import TestClient

from codex_gateway.app import create_app
from codex_gateway.config import Settings
from codex_gateway.effective_config import EffectiveCodexConfig
from codex_gateway.errors import CodexRateLimit
from codex_gateway.models import CodexTurnResult, TokenUsage


class FakeRuntime:
    def __init__(self, result=None, error=None, ready=True, start_error=None):
        self.result = result or CodexTurnResult(
            json.dumps(
                {
                    "result": {
                        "kind": "message",
                        "content": "hello",
                        "tool_calls": [],
                    }
                }
            ),
            TokenUsage(2, 1),
        )
        self.error = error
        self.ready = ready
        self.health_detail = "ready" if ready else "login required"
        self.start_error = start_error
        self.started = False
        self.stopped = False
        self.active_completions = 0
        self.pinned_configs = []
        self.config = EffectiveCodexConfig("gpt-5.6-sol", "xhigh")

    async def start(self):
        self.started = True
        if self.start_error:
            raise self.start_error

    async def stop(self):
        self.stopped = True

    async def effective_config(self):
        return self.config

    async def complete(self, prompt, output_schema, pinned_config=None):
        self.pinned_configs.append(pinned_config)
        if self.error:
            raise self.error
        return self.result


def make_client(runtime=None, settings=None):
    return TestClient(
        create_app(
            runtime=runtime or FakeRuntime(),
            settings=settings or Settings(),
        )
    )


def test_models_health_and_lifespan():
    runtime = FakeRuntime()
    with make_client(runtime) as http:
        assert http.get("/healthz").json() == {"status": "ok"}
        assert http.get("/v1/models").json()["data"][0]["id"] == "codex"
    assert runtime.started and runtime.stopped


def test_internal_status_reports_effective_snapshot():
    runtime = FakeRuntime()

    with make_client(runtime) as http:
        response = http.get("/internal/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "active_completions": 0,
        "model": "gpt-5.6-sol",
        "reasoning_effort": "xhigh",
        "snapshot_id": runtime.config.snapshot_id,
    }


def test_platform_headers_pin_config_for_one_run():
    runtime = FakeRuntime()

    with make_client(runtime) as http:
        response = http.post(
            "/v1/chat/completions",
            headers={
                "X-TradingNG-Run-ID": "run-1",
                "X-TradingNG-Codex-Model": "gpt-5.6-sol",
                "X-TradingNG-Codex-Reasoning-Effort": "xhigh",
            },
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert runtime.pinned_configs == [EffectiveCodexConfig("gpt-5.6-sol", "xhigh")]


@pytest.mark.parametrize(
    "headers",
    [
        {"X-TradingNG-Codex-Model": "gpt-5.6-sol"},
        {
            "X-TradingNG-Codex-Model": "gpt-5.6-sol",
            "X-TradingNG-Codex-Reasoning-Effort": "xhigh",
        },
        {"X-TradingNG-Run-ID": "run-1"},
    ],
)
def test_platform_headers_must_be_complete(headers):
    with make_client() as http:
        response = http.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_unhealthy_runtime_returns_503():
    with make_client(FakeRuntime(ready=False)) as http:
        response = http.get("/healthz")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "codex_unavailable"


def test_text_chat_completion():
    with make_client() as http:
        response = http.post(
            "/v1/chat/completions",
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hello"


def test_wrong_model_and_stream_are_openai_errors():
    with make_client() as http:
        wrong = http.post(
            "/v1/chat/completions",
            json={
                "model": "other",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        stream = http.post(
            "/v1/chat/completions",
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
    assert wrong.status_code == 404
    assert wrong.json()["error"]["code"] == "model_not_found"
    assert stream.status_code == 400
    assert stream.json()["error"]["code"] == "invalid_request"


def test_body_larger_than_limit_is_413():
    with make_client(settings=Settings(max_body_bytes=100)) as http:
        response = http.post(
            "/v1/chat/completions",
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "x" * 200}],
            },
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_runtime_error_status_is_preserved():
    with make_client(FakeRuntime(error=CodexRateLimit("limited"))) as http:
        response = http.post(
            "/v1/chat/completions",
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "codex_rate_limit"


def test_prompt_is_not_logged_and_cors_is_absent(caplog):
    with caplog.at_level(logging.INFO), make_client() as http:
        response = http.post(
            "/v1/chat/completions",
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "private-market-data"}],
            },
        )
    assert response.status_code == 200
    assert "private-market-data" not in caplog.text
    assert "access-control-allow-origin" not in response.headers


def test_lifespan_stops_runtime_when_start_fails():
    runtime = FakeRuntime(start_error=RuntimeError("startup failed"))
    with pytest.raises(RuntimeError, match="startup failed"), make_client(runtime):
        pass
    assert runtime.started and runtime.stopped


def test_unexpected_error_is_sanitized_openai_envelope(caplog):
    runtime = FakeRuntime(error=RuntimeError("private-market-data"))
    with caplog.at_level(logging.ERROR), make_client(runtime) as http:
        response = http.post(
            "/v1/chat/completions",
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "gateway_error"
    assert "private-market-data" not in response.text
    assert "private-market-data" not in caplog.text
