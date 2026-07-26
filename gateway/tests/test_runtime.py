import asyncio
import contextlib
import json
import logging
from collections import deque
from pathlib import Path

import pytest

import codex_gateway.runtime as runtime_module
from codex_gateway.config import Settings
from codex_gateway.effective_config import EffectiveCodexConfig
from codex_gateway.errors import (
    CodexContextLimit,
    CodexInterrupted,
    CodexRateLimit,
    CodexRuntimeFailure,
    CodexTimeout,
    CodexUnavailable,
)
from codex_gateway.runtime import CodexRuntime
from codex_gateway.transport import TransportClosed


class ScriptedTransport:
    def __init__(self, on_notification, on_exit):
        self.on_notification = on_notification
        self.on_exit = on_exit
        self.requests = []
        self.notifications = []
        self.turn_started = asyncio.Event()
        self.release_turns = asyncio.Event()
        self.running = False
        self.counter = 0
        self.config_responses = deque(
            [
                {
                    "config": {
                        "model": "gpt-5.6-sol",
                        "model_reasoning_effort": "xhigh",
                    },
                    "origins": {},
                }
            ]
        )

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    async def notify(self, method, params):
        self.notifications.append((method, params))

    async def request(self, method, params):
        self.requests.append((method, params))
        if method == "initialize":
            return {"userAgent": "fake"}
        if method == "config/read":
            if len(self.config_responses) > 1:
                return self.config_responses.popleft()
            return self.config_responses[0]
        if method == "thread/start":
            self.counter += 1
            return {"thread": {"id": f"thread-{self.counter}"}}
        if method == "turn/start":
            thread_id = params["threadId"]
            turn_id = thread_id.replace("thread", "turn")
            self.turn_started.set()
            asyncio.create_task(self.finish(thread_id, turn_id))
            return {"turn": {"id": turn_id, "items": [], "status": "inProgress"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    async def finish(self, thread_id, turn_id):
        await self.release_turns.wait()
        await self.on_notification(
            "item/completed",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "completedAtMs": 1,
                "item": {
                    "id": "item-1",
                    "type": "agentMessage",
                    "text": json.dumps({"kind": "message", "content": "ok", "tool_calls": []}),
                },
            },
        )
        await self.on_notification(
            "thread/tokenUsage/updated",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "tokenUsage": {
                    "last": {"inputTokens": 10, "outputTokens": 4},
                    "total": {"inputTokens": 10, "outputTokens": 4},
                },
            },
        )
        await self.on_notification(
            "turn/completed",
            {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "items": [],
                    "status": "completed",
                    "error": None,
                },
            },
        )


@pytest.fixture
def runtime_factory():
    transports = []

    def build(timeout=1, *, clock=None):
        def factory(on_notification, on_exit):
            transport = ScriptedTransport(on_notification, on_exit)
            transports.append(transport)
            return transport

        kwargs = {}
        if clock is not None:
            kwargs["clock"] = clock
        runtime = CodexRuntime(
            Settings(request_timeout_seconds=timeout),
            transport_factory=factory,
            validate_environment=False,
            **kwargs,
        )
        return runtime, transports

    return build


def fake_codex(tmp_path, *, version="0.145.0", login_code=0):
    script = tmp_path / "codex"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"version = {version!r}\n"
        f"login_code = {login_code}\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print(f'codex-cli {version}')\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[1:] == ['login', 'status']:\n"
        "    raise SystemExit(login_code)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_default_app_server_disables_only_gateway_playwright(monkeypatch):
    captured = {}

    class CapturingTransport:
        def __init__(self, command, *, on_notification, on_exit):
            captured["command"] = command

    monkeypatch.setattr(runtime_module, "AppServerTransport", CapturingTransport)
    runtime = CodexRuntime(Settings(), validate_environment=False)
    runtime._default_transport_factory(object(), object())

    assert captured["command"] == [
        "codex",
        "app-server",
        "--listen",
        "stdio://",
        "--config",
        "mcp_servers.playwright.enabled=false",
    ]


@pytest.mark.asyncio
async def test_login_missing_keeps_health_unready(tmp_path):
    runtime = CodexRuntime(
        Settings(codex_bin=str(fake_codex(tmp_path, login_code=1))),
        transport_factory=lambda *_: pytest.fail("transport must not start"),
    )
    await runtime.start()
    assert not runtime.ready
    assert "codex login" in runtime.health_detail


@pytest.mark.asyncio
async def test_old_cli_version_refuses_start(tmp_path):
    runtime = CodexRuntime(
        Settings(codex_bin=str(fake_codex(tmp_path, version="0.144.0"))),
        transport_factory=lambda *_: pytest.fail("transport must not start"),
    )
    with pytest.raises(RuntimeError, match="below minimum"):
        await runtime.start()


@pytest.mark.asyncio
async def test_handshake_turn_policy_usage_and_temp_cleanup(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    transport.release_turns.set()
    result = await runtime.complete("prompt", {"type": "object"})
    assert transport.requests[0][0] == "initialize"
    assert transport.notifications[0][0] == "initialized"
    thread = next(params for method, params in transport.requests if method == "thread/start")
    assert thread["ephemeral"] is True
    assert thread["approvalPolicy"] == "never"
    assert thread["sandbox"] == "read-only"
    assert not Path(thread["cwd"]).exists()
    turn = next(params for method, params in transport.requests if method == "turn/start")
    assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": True}
    assert turn["outputSchema"] == {"type": "object"}
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 4
    await runtime.stop()


@pytest.mark.asyncio
async def test_each_turn_inherits_latest_effective_codex_config(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    transport.config_responses = deque(
        [
            {
                "config": {
                    "model": "gpt-5.6-sol",
                    "model_reasoning_effort": "xhigh",
                },
                "origins": {},
            },
            {
                "config": {
                    "model": "gpt-5.6-terra",
                    "model_reasoning_effort": "medium",
                },
                "origins": {},
            },
        ]
    )
    transport.release_turns.set()

    await runtime.complete("first", {"type": "object"})
    await runtime.complete("second", {"type": "object"})

    reads = [params for method, params in transport.requests if method == "config/read"]
    threads = [params for method, params in transport.requests if method == "thread/start"]
    turns = [params for method, params in transport.requests if method == "turn/start"]
    assert len(reads) == 2
    assert all(Path(params["cwd"]).name.startswith("tradingng-codex-") for params in reads)
    assert [params["model"] for params in threads] == ["gpt-5.6-sol", "gpt-5.6-terra"]
    assert [params["effort"] for params in turns] == ["xhigh", "medium"]
    await runtime.stop()


@pytest.mark.asyncio
async def test_explicit_config_is_pinned_across_turns(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    transport.config_responses = deque(
        [
            {
                "config": {
                    "model": "gpt-5.6-terra",
                    "model_reasoning_effort": "medium",
                },
                "origins": {},
            }
        ]
    )
    transport.release_turns.set()
    pinned = EffectiveCodexConfig(model="gpt-5.6-sol", reasoning_effort="xhigh")

    await runtime.complete("first", {"type": "object"}, pinned_config=pinned)
    await runtime.complete("second", {"type": "object"}, pinned_config=pinned)

    threads = [params for method, params in transport.requests if method == "thread/start"]
    turns = [params for method, params in transport.requests if method == "turn/start"]
    assert [item["model"] for item in threads] == ["gpt-5.6-sol", "gpt-5.6-sol"]
    assert [item["effort"] for item in turns] == ["xhigh", "xhigh"]
    assert not [method for method, _ in transport.requests if method == "config/read"]
    assert runtime.active_completions == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_missing_effective_model_settings_delegate_to_codex(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    transport.config_responses = deque([{"config": {}, "origins": {}}])
    transport.release_turns.set()
    await runtime.complete("prompt", {"type": "object"})
    reads = [params for method, params in transport.requests if method == "config/read"]
    thread = next(params for method, params in transport.requests if method == "thread/start")
    turn = next(params for method, params in transport.requests if method == "turn/start")
    assert len(reads) == 1
    assert "model" not in thread
    assert "effort" not in turn
    await runtime.stop()


@pytest.mark.asyncio
async def test_malformed_effective_model_config_fails_without_starting_thread(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    transport.config_responses = deque(
        [
            {
                "config": {"model": 56, "model_reasoning_effort": "xhigh"},
                "origins": {},
            }
        ]
    )
    transport.release_turns.set()
    with pytest.raises(CodexRuntimeFailure, match="app-server request failed"):
        await runtime.complete("prompt", {"type": "object"})
    assert not any(method == "thread/start" for method, _ in transport.requests)
    await runtime.stop()


@pytest.mark.asyncio
async def test_no_application_concurrency_limit(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    tasks = [
        asyncio.create_task(runtime.complete(f"prompt-{index}", {"type": "object"}))
        for index in range(3)
    ]
    while sum(method == "turn/start" for method, _ in transport.requests) < 3:
        await asyncio.sleep(0.01)
    assert runtime.active_completions == 3
    transport.release_turns.set()
    assert len(await asyncio.gather(*tasks)) == 3
    assert runtime.active_completions == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_activity_snapshot_tracks_turn_age_and_notification_progress(runtime_factory):
    now = [100.0]
    runtime, transports = runtime_factory(timeout=None, clock=lambda: now[0])
    await runtime.start()
    transport = transports[0]
    task = asyncio.create_task(runtime.complete("prompt", {"type": "object"}))
    await transport.turn_started.wait()

    now[0] = 110.0
    await transport.on_notification(
        "item/started",
        {"threadId": "thread-1", "turnId": "turn-1", "item": {"type": "reasoning"}},
    )
    now[0] = 112.0
    snapshot = runtime.activity_snapshot()
    assert snapshot.active_completions == 1
    assert snapshot.oldest_active_seconds == 12.0
    assert snapshot.stalest_progress_seconds == 2.0

    transport.release_turns.set()
    await task
    assert runtime.activity_snapshot().active_completions == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_turn_lifecycle_logs_correlation_without_prompt(runtime_factory, caplog):
    runtime, transports = runtime_factory(timeout=None)
    await runtime.start()
    caplog.set_level(logging.INFO, logger="codex_gateway.runtime")
    transports[0].release_turns.set()

    await runtime.complete(
        "secret prompt must not be logged",
        {"type": "object"},
        request_id="request-123",
        run_id="run-456",
        retry_count=2,
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "codex_turn_started" in messages
    assert "codex_turn_terminal" in messages
    assert "request-123" in messages
    assert "run-456" in messages
    assert "retry_count=2" in messages
    assert "secret prompt" not in messages
    await runtime.stop()


@pytest.mark.asyncio
async def test_unbounded_completion_waits_until_turn_finishes(runtime_factory):
    runtime, transports = runtime_factory(timeout=None)
    await runtime.start()
    transport = transports[0]
    task = asyncio.create_task(runtime.complete("prompt", {"type": "object"}))
    await transport.turn_started.wait()
    await asyncio.sleep(0.06)
    assert not task.done()
    assert not any(method == "turn/interrupt" for method, _ in transport.requests)
    transport.release_turns.set()
    assert (await task).final_message
    await runtime.stop()


@pytest.mark.asyncio
async def test_timeout_interrupts_turn(runtime_factory):
    runtime, transports = runtime_factory(timeout=0.05)
    await runtime.start()
    with pytest.raises(CodexTimeout):
        await runtime.complete("prompt", {"type": "object"})
    assert any(method == "turn/interrupt" for method, _ in transports[0].requests)
    await runtime.stop()


@pytest.mark.asyncio
async def test_failed_turn_maps_rate_limit(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]

    async def rate_limited(thread_id, turn_id):
        await transport.on_notification(
            "turn/completed",
            {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "items": [],
                    "status": "failed",
                    "error": {
                        "message": "limited",
                        "codexErrorInfo": "usageLimitExceeded",
                    },
                },
            },
        )

    transport.finish = rate_limited
    with pytest.raises(CodexRateLimit):
        await runtime.complete("prompt", {"type": "object"})
    await runtime.stop()


@pytest.mark.asyncio
async def test_failed_turn_maps_unauthorized(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]

    async def unauthorized(thread_id, turn_id):
        await transport.on_notification(
            "turn/completed",
            {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "items": [],
                    "status": "failed",
                    "error": {
                        "message": "login expired",
                        "codexErrorInfo": "unauthorized",
                    },
                },
            },
        )

    transport.finish = unauthorized
    with pytest.raises(CodexUnavailable):
        await runtime.complete("prompt", {"type": "object"})
    assert not runtime.ready
    await runtime.stop()


@pytest.mark.parametrize(
    ("status", "info", "expected"),
    [
        ("interrupted", None, CodexInterrupted),
        ("failed", {"ContextWindowExceeded": {}}, CodexContextLimit),
        ("failed", {"ResponseStreamDisconnected": {}}, CodexRuntimeFailure),
        ("failed", "UsageLimitExceeded", CodexRateLimit),
        ("failed", {"Unauthorized": {}}, CodexUnavailable),
    ],
)
@pytest.mark.asyncio
async def test_terminal_turn_errors_are_classified(runtime_factory, status, info, expected):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]

    async def finish_with_error(thread_id, turn_id):
        await transport.on_notification(
            "turn/completed",
            {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "items": [],
                    "status": status,
                    "error": {"message": "safe failure", "codexErrorInfo": info},
                },
            },
        )

    transport.finish = finish_with_error
    with pytest.raises(expected):
        await runtime.complete("prompt", {"type": "object"})
    await runtime.stop()


@pytest.mark.asyncio
async def test_transport_exit_fails_turn_and_restarts(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    task = asyncio.create_task(runtime.complete("prompt", {"type": "object"}))
    await transports[0].turn_started.wait()
    await transports[0].on_exit(RuntimeError("crash"))
    with pytest.raises(CodexRuntimeFailure):
        await task
    for _ in range(200):
        if len(transports) >= 2 and runtime.ready:
            break
        await asyncio.sleep(0.01)
    assert len(transports) >= 2
    assert runtime.ready
    await runtime.stop()


@pytest.mark.asyncio
async def test_concurrent_and_repeated_start_are_idempotent(runtime_factory):
    runtime, transports = runtime_factory()
    await asyncio.gather(*(runtime.start() for _ in range(10)))
    try:
        assert len(transports) == 1
        await runtime.start()
        assert len(transports) == 1
    finally:
        await runtime.stop()
    assert not transports[0].running
    assert not runtime.ready
    assert runtime.health_detail == "stopped"


@pytest.mark.asyncio
async def test_stale_transport_callbacks_cannot_corrupt_replacement(runtime_factory, monkeypatch):
    monkeypatch.setattr(runtime_module, "_RESTART_DELAYS", (0,))
    runtime, transports = runtime_factory()
    await runtime.start()
    first = transports[0]
    await first.on_exit(RuntimeError("first crash"))
    for _ in range(100):
        if len(transports) == 2 and runtime.ready:
            break
        await asyncio.sleep(0.01)
    assert len(transports) == 2
    second = transports[1]

    task = asyncio.create_task(runtime.complete("prompt", {"type": "object"}))
    await second.turn_started.wait()
    await first.on_notification(
        "turn/completed",
        {
            "threadId": "thread-1",
            "turn": {
                "id": "turn-1",
                "items": [
                    {
                        "type": "agentMessage",
                        "text": json.dumps(
                            {"kind": "message", "content": "stale", "tool_calls": []}
                        ),
                    }
                ],
                "status": "completed",
                "error": None,
            },
        },
    )
    await asyncio.sleep(0)
    assert not task.done()
    second.release_turns.set()
    result = await task
    assert json.loads(result.final_message)["content"] == "ok"

    await first.on_exit(RuntimeError("late stale exit"))
    await asyncio.sleep(0.01)
    assert runtime.ready
    assert len(transports) == 2
    await runtime.stop()


@pytest.mark.asyncio
async def test_malformed_turn_start_result_is_runtime_failure(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    original_request = transport.request

    async def malformed_request(method, params):
        if method == "thread/start":
            return {"thread": None}
        return await original_request(method, params)

    transport.request = malformed_request
    try:
        with pytest.raises(CodexRuntimeFailure, match="request failed"):
            await runtime.complete("prompt", {"type": "object"})
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_malformed_usage_notification_fails_turn(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    task = asyncio.create_task(runtime.complete("prompt", {"type": "object"}))
    await transport.turn_started.wait()
    try:
        await transport.on_notification(
            "thread/tokenUsage/updated",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "tokenUsage": {"last": {"inputTokens": "invalid", "outputTokens": 4}},
            },
        )
        with pytest.raises(CodexRuntimeFailure, match="notification was invalid"):
            await asyncio.wait_for(task, timeout=0.2)
    finally:
        transport.release_turns.set()
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await runtime.stop()


@pytest.mark.asyncio
async def test_manual_recovery_stops_unready_transport(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    first = transports[0]

    async def unauthorized(thread_id, turn_id):
        await first.on_notification(
            "turn/completed",
            {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "items": [],
                    "status": "failed",
                    "error": {
                        "message": "login expired",
                        "codexErrorInfo": "unauthorized",
                    },
                },
            },
        )

    first.finish = unauthorized
    with pytest.raises(CodexUnavailable):
        await runtime.complete("prompt", {"type": "object"})
    assert not runtime.ready
    assert first.running

    await runtime.start()
    try:
        assert len(transports) == 2
        assert not first.running
        assert transports[1].running
        assert runtime.ready
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_transport_failure_during_turn_start_has_no_unretrieved_future(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transport = transports[0]
    original_request = transport.request
    release_failure = asyncio.Event()

    async def failing_request(method, params):
        if method == "turn/start":
            transport.turn_started.set()
            await release_failure.wait()
            raise TransportClosed("crashed during turn/start")
        return await original_request(method, params)

    transport.request = failing_request
    contexts = []
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    try:
        task = asyncio.create_task(runtime.complete("prompt", {"type": "object"}))
        await transport.turn_started.wait()
        turn_future = runtime._turns["thread-1"].future
        await transport.on_exit(RuntimeError("crash"))
        release_failure.set()
        with pytest.raises(CodexRuntimeFailure):
            await task
        await asyncio.sleep(0)
        assert turn_future._log_traceback is False
        assert not any(
            "Future exception was never retrieved" in context.get("message", "")
            for context in contexts
        )
    finally:
        loop.set_exception_handler(old_handler)
        await runtime.stop()
