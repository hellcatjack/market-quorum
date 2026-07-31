import json
import logging

import pytest
from starlette.testclient import TestClient

from codex_gateway.app import create_app
from codex_gateway.config import Settings
from codex_gateway.effective_config import EffectiveCodexConfig
from codex_gateway.errors import CodexRateLimit
from codex_gateway.model_catalog import CodexModelOption
from codex_gateway.models import CodexTurnResult, TokenUsage
from codex_gateway.runtime import RuntimeActivity


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
        self.completion_calls = []
        self.config = EffectiveCodexConfig("gpt-5.6-sol", "xhigh")
        self.models = (
            CodexModelOption(
                id="gpt-5.6-sol",
                default_reasoning_effort="medium",
                supported_reasoning_efforts=("low", "medium", "high", "xhigh"),
            ),
            CodexModelOption(
                id="gpt-5.6-terra",
                default_reasoning_effort="low",
                supported_reasoning_efforts=("low", "medium", "high"),
            ),
        )
        self.model_catalog_calls = 0

    async def start(self):
        self.started = True
        if self.start_error:
            raise self.start_error

    async def stop(self):
        self.stopped = True

    async def effective_config(self):
        return self.config

    async def available_models(self):
        self.model_catalog_calls += 1
        return self.models

    def activity_snapshot(self):
        return RuntimeActivity(
            accepting=True,
            active_completions=self.active_completions,
            oldest_active_seconds=None,
            stalest_progress_seconds=None,
        )

    async def complete(self, prompt, output_schema, pinned_config=None, **kwargs):
        self.pinned_configs.append(pinned_config)
        self.completion_calls.append(kwargs)
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
        models = http.get("/v1/models").json()["data"]
        assert [model["id"] for model in models] == [
            "codex",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
        ]
        assert models[1] == {
            "id": "gpt-5.6-sol",
            "object": "model",
            "owned_by": "openai-codex",
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": ["low", "medium", "high", "xhigh"],
        }
    assert runtime.started and runtime.stopped


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
            EffectiveCodexConfig("gpt-5.6-sol", "xhigh"),
        ),
        (
            {"model": "gpt-5.6-terra"},
            EffectiveCodexConfig("gpt-5.6-terra", "low"),
        ),
    ],
)
def test_physical_model_selects_explicit_or_catalog_default_effort(payload, expected):
    runtime = FakeRuntime()
    with make_client(runtime) as http:
        response = http.post(
            "/v1/chat/completions",
            json={
                **payload,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert runtime.pinned_configs == [expected]
    assert runtime.completion_calls[0]["run_id"] is None


def test_unknown_model_and_unsupported_effort_fail_before_completion():
    runtime = FakeRuntime()
    with make_client(runtime) as http:
        unknown = http.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-does-not-exist",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        effort = http.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.6-terra",
                "reasoning_effort": "xhigh",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "model_not_found"
    assert effort.status_code == 400
    assert effort.json()["error"]["code"] == "invalid_request"
    assert effort.json()["error"]["param"] == "reasoning_effort"
    assert runtime.pinned_configs == []


def test_empty_reasoning_effort_is_rejected_instead_of_using_catalog_default():
    runtime = FakeRuntime()
    with make_client(runtime) as http:
        response = http.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.6-terra",
                "reasoning_effort": "",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["param"] == "reasoning_effort"
    assert runtime.pinned_configs == []


def test_codex_alias_rejects_partial_effort_override_but_still_inherits():
    runtime = FakeRuntime()
    with make_client(runtime) as http:
        inherited = http.post(
            "/v1/chat/completions",
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        partial = http.post(
            "/v1/chat/completions",
            json={
                "model": "codex",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert inherited.status_code == 200
    assert partial.status_code == 400
    assert partial.json()["error"]["param"] == "reasoning_effort"
    assert runtime.pinned_configs == [None]


def test_private_route_bundle_wins_over_body_reasoning_effort():
    runtime = FakeRuntime()
    headers = {
        "X-TradingNG-Run-ID": "run-private",
        "X-TradingNG-Codex-Fast-Model": "gpt-5.6-terra",
        "X-TradingNG-Codex-Fast-Reasoning-Effort": "medium",
        "X-TradingNG-Codex-Slow-Model": "gpt-5.6-sol",
        "X-TradingNG-Codex-Slow-Reasoning-Effort": "high",
    }
    with make_client(runtime) as http:
        response = http.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "codex-fast",
                "reasoning_effort": "xhigh",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert runtime.pinned_configs == [EffectiveCodexConfig("gpt-5.6-terra", "medium")]
    assert runtime.completion_calls[0]["run_id"] == "run-private"


def test_internal_status_reports_effective_snapshot():
    runtime = FakeRuntime()

    with make_client(runtime) as http:
        response = http.get("/internal/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "accepting": True,
        "active_completions": 0,
        "oldest_active_seconds": None,
        "stalest_progress_seconds": None,
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
                "x-stainless-retry-count": "2",
            },
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert runtime.pinned_configs == [EffectiveCodexConfig("gpt-5.6-sol", "xhigh")]
    assert runtime.completion_calls[0]["run_id"] == "run-1"
    assert runtime.completion_calls[0]["retry_count"] == 2
    assert len(runtime.completion_calls[0]["request_id"]) == 32


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("codex-fast", EffectiveCodexConfig("gpt-5.6-terra", "medium")),
        ("codex-slow", EffectiveCodexConfig("gpt-5.6-sol", "high")),
    ],
)
def test_route_alias_selects_frozen_fast_or_slow_config(alias, expected):
    runtime = FakeRuntime()
    headers = {
        "X-TradingNG-Run-ID": "run-routed",
        "X-TradingNG-Codex-Fast-Model": "gpt-5.6-terra",
        "X-TradingNG-Codex-Fast-Reasoning-Effort": "medium",
        "X-TradingNG-Codex-Slow-Model": "gpt-5.6-sol",
        "X-TradingNG-Codex-Slow-Reasoning-Effort": "high",
    }

    with make_client(runtime) as http:
        response = http.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": alias,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert runtime.pinned_configs == [expected]
    assert runtime.completion_calls[0]["run_id"] == "run-routed"


@pytest.mark.parametrize(
    "headers",
    [
        {"X-TradingNG-Run-ID": "run-routed"},
        {
            "X-TradingNG-Run-ID": "run-routed",
            "X-TradingNG-Codex-Fast-Model": "gpt-5.6-terra",
            "X-TradingNG-Codex-Fast-Reasoning-Effort": "high",
        },
    ],
)
def test_route_alias_requires_complete_frozen_route_bundle(headers):
    with make_client() as http:
        response = http.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "codex-fast",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("value", ["-1", "not-a-number", "101"])
def test_invalid_sdk_retry_count_is_rejected(value):
    with make_client() as http:
        response = http.post(
            "/v1/chat/completions",
            headers={"x-stainless-retry-count": value},
            json={
                "model": "codex",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


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
