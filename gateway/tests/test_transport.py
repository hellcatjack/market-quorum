import asyncio
import sys
from pathlib import Path

import pytest

from codex_gateway.transport import AppServerTransport, JsonRpcError, TransportClosed

FAKE_SERVER = Path(__file__).with_name("fake_app_server.py")


async def wait_until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


async def wait_for_stopped(transport):
    await asyncio.wait_for(wait_until(lambda: not transport.running), timeout=1)


@pytest.mark.asyncio
async def test_routes_out_of_order_responses_by_id():
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    try:
        slow, fast = await asyncio.gather(
            transport.request("echo", {"value": "slow", "delay": 0.05}),
            transport.request("echo", {"value": "fast", "delay": 0}),
        )
        assert slow == {"value": "slow"}
        assert fast == {"value": "fast"}
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_dispatches_notifications():
    seen = []

    async def on_notification(method, params):
        seen.append((method, params))

    transport = AppServerTransport(
        [sys.executable, str(FAKE_SERVER)], on_notification=on_notification
    )
    await transport.start()
    try:
        await transport.notify("emit", {"x": 1})
        await asyncio.wait_for(wait_until(lambda: bool(seen)), timeout=1)
        assert seen == [("event/test", {"x": 1})]
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_json_rpc_error_is_raised():
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    try:
        with pytest.raises(JsonRpcError, match="failed"):
            await transport.request("fail", {})
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_process_exit_fails_pending_requests():
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    pending = asyncio.create_task(transport.request("echo", {"value": "never", "delay": 30}))
    await asyncio.wait_for(wait_until(lambda: bool(transport._pending)), timeout=1)
    await transport.notify("exit", {})
    with pytest.raises(TransportClosed):
        await asyncio.wait_for(pending, timeout=1)
    await wait_for_stopped(transport)


@pytest.mark.asyncio
async def test_concurrent_starts_create_one_process(monkeypatch):
    real_create = asyncio.create_subprocess_exec
    calls = 0

    async def counted_create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", counted_create)
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await asyncio.gather(transport.start(), transport.start(), transport.start())
    try:
        assert calls == 1
        assert transport.running
        assert transport._reader_task is not None
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_concurrent_stops_and_interleaved_start_stop_leave_no_process(monkeypatch):
    real_create = asyncio.create_subprocess_exec
    entered = asyncio.Event()
    proceed = asyncio.Event()

    async def blocked_create(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocked_create)
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    starting = asyncio.create_task(transport.start())
    await asyncio.wait_for(entered.wait(), timeout=1)
    stopping = asyncio.create_task(transport.stop())
    proceed.set()
    await asyncio.wait_for(asyncio.gather(starting, stopping), timeout=1)
    await transport.start()
    await asyncio.gather(transport.stop(), transport.stop())
    assert not transport.running
    assert transport._process is None
    assert transport._reader_task is None
    assert transport._stderr_task is None
    assert transport._notification_task is None


@pytest.mark.asyncio
async def test_transport_restarts_after_stop():
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    await transport.stop()
    await transport.start()
    try:
        assert await transport.request("echo", {"value": "restarted"}) == {"value": "restarted"}
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_startup_failure_leaves_restartable_stopped_transport(monkeypatch):
    real_create = asyncio.create_subprocess_exec
    attempts = 0

    async def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("cannot start")
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_once)
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    with pytest.raises(OSError, match="cannot start"):
        await transport.start()
    assert not transport.running
    assert transport._process is None
    await transport.start()
    await transport.stop()


@pytest.mark.asyncio
async def test_cancelled_startup_does_not_cancel_shared_transition(monkeypatch):
    real_create = asyncio.create_subprocess_exec
    entered = asyncio.Event()
    proceed = asyncio.Event()

    async def blocked_create(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocked_create)
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    cancelled_start = asyncio.create_task(transport.start())
    await asyncio.wait_for(entered.wait(), timeout=1)
    cancelled_start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_start
    proceed.set()
    await transport.start()
    try:
        assert transport.running
    finally:
        await transport.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["exit", "malformed"])
async def test_terminal_stdout_failure_reaps_child_and_stop_is_safe(method):
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    process = transport._process
    assert process is not None
    pending = asyncio.create_task(transport.request("echo", {"value": "never", "delay": 30}))
    await asyncio.wait_for(wait_until(lambda: bool(transport._pending)), timeout=1)
    await transport.notify(method, {})
    with pytest.raises(TransportClosed):
        await asyncio.wait_for(pending, timeout=1)
    await wait_for_stopped(transport)
    await asyncio.wait_for(wait_until(lambda: process.returncode is not None), timeout=1)
    assert process.returncode is not None
    await transport.stop()


@pytest.mark.asyncio
async def test_transport_restarts_after_spontaneous_exit():
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    await transport.notify("exit", {})
    await wait_for_stopped(transport)
    await transport.start()
    try:
        assert await transport.request("echo", {"value": "restarted"}) == {"value": "restarted"}
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_exit_callback_error_does_not_leak_or_prevent_cleanup():
    called = asyncio.Event()

    async def on_exit(error):
        called.set()
        raise RuntimeError("callback failed")

    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)], on_exit=on_exit)
    await transport.start()
    await transport.notify("exit", {})
    await asyncio.wait_for(called.wait(), timeout=1)
    await wait_for_stopped(transport)
    await transport.stop()


@pytest.mark.asyncio
async def test_exit_callback_can_stop_after_terminal_transition_completes():
    completed = asyncio.Event()
    transport = None

    async def on_exit(error):
        await transport.stop()
        completed.set()

    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)], on_exit=on_exit)
    await transport.start()
    await transport.notify("exit", {})
    await asyncio.wait_for(completed.wait(), timeout=1)
    await asyncio.wait_for(wait_until(lambda: not transport._exit_tasks), timeout=1)
    await wait_for_stopped(transport)
    await transport.start()
    try:
        assert await transport.request("echo", {"value": "restarted"}) == {"value": "restarted"}
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_exit_callback_can_restart_and_request_after_terminal_transition():
    restarted = asyncio.Event()
    results = []
    transport = None

    async def on_exit(error):
        await transport.start()
        results.append(await transport.request("echo", {"value": "restarted"}))
        restarted.set()

    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)], on_exit=on_exit)
    await transport.start()
    await transport.notify("exit", {})
    await asyncio.wait_for(restarted.wait(), timeout=1)
    await asyncio.wait_for(wait_until(lambda: not transport._exit_tasks), timeout=1)
    process = transport._process
    assert process is not None
    assert results == [{"value": "restarted"}]
    await transport.stop()
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_slow_exit_callback_does_not_gate_restart_or_stop():
    entered = asyncio.Event()
    release = asyncio.Event()
    transport = None

    async def on_exit(error):
        entered.set()
        await release.wait()

    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)], on_exit=on_exit)
    await transport.start()
    await transport.notify("exit", {})
    await asyncio.wait_for(entered.wait(), timeout=1)
    await asyncio.wait_for(transport.start(), timeout=1)
    assert await transport.request("echo", {"value": "new child"}) == {"value": "new child"}
    await asyncio.wait_for(transport.stop(), timeout=1)
    release.set()
    await asyncio.wait_for(wait_until(lambda: not transport._exit_tasks), timeout=1)


@pytest.mark.asyncio
async def test_notification_handler_can_make_request_without_blocking_reader():
    seen = []
    transport = None

    async def on_notification(method, params):
        seen.append((method, await transport.request("echo", {"value": params["x"]})))

    transport = AppServerTransport(
        [sys.executable, str(FAKE_SERVER)], on_notification=on_notification
    )
    await transport.start()
    try:
        await transport.notify("emit", {"x": 1})
        other = await transport.request("echo", {"value": "other"})
        await asyncio.wait_for(wait_until(lambda: bool(seen)), timeout=1)
        assert other == {"value": "other"}
        assert seen == [("event/test", {"value": 1})]
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_notification_handler_error_does_not_stop_routing():
    called = asyncio.Event()

    async def on_notification(method, params):
        called.set()
        raise RuntimeError("notification failed")

    transport = AppServerTransport(
        [sys.executable, str(FAKE_SERVER)], on_notification=on_notification
    )
    await transport.start()
    try:
        await transport.notify("emit", {"x": 1})
        await asyncio.wait_for(called.wait(), timeout=1)
        assert await transport.request("echo", {"value": "still works"}) == {"value": "still works"}
        assert transport.running
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_routes_large_stdout_result():
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    try:
        result = await asyncio.wait_for(transport.request("large", {"size": 100_000}), timeout=2)
        assert result == {"value": "x" * 100_000}
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_discards_large_newline_free_stderr_without_blocking_stdout():
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    try:
        result = await asyncio.wait_for(
            transport.request("stderr", {"size": 2_000_000, "value": "ok"}), timeout=3
        )
        assert result == {"value": "ok"}
    finally:
        await transport.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [BrokenPipeError(), ConnectionResetError()])
@pytest.mark.parametrize("operation", ["write", "drain"])
async def test_pipe_write_failures_become_transport_closed(monkeypatch, failure, operation):
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    process = transport._process
    assert process is not None and process.stdin is not None

    if operation == "write":

        def fail(data):
            raise failure
    else:

        async def fail():
            raise failure

    monkeypatch.setattr(process.stdin, operation, fail)
    with pytest.raises(TransportClosed):
        await transport.request("echo", {"value": "never"})
    assert not transport._pending
    await wait_for_stopped(transport)
    await transport.stop()


@pytest.mark.asyncio
async def test_cancelled_request_removes_pending_future():
    transport = AppServerTransport([sys.executable, str(FAKE_SERVER)])
    await transport.start()
    try:
        pending = asyncio.create_task(transport.request("echo", {"value": "never", "delay": 30}))
        await asyncio.wait_for(wait_until(lambda: bool(transport._pending)), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not transport._pending
        assert await transport.request("echo", {"value": "still works"}) == {"value": "still works"}
    finally:
        await transport.stop()
