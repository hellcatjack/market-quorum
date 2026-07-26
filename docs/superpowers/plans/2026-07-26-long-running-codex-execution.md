# Long-Running Codex Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent TradingNG from interrupting healthy long-running Codex turns while adding safe diagnostics, explicit terminal errors, complete audit records, and graceful shutdown behavior.

**Architecture:** Keep the platform's existing durable Worker/Runner/checkpoint orchestration and preserve the synchronous OpenAI-compatible Gateway API. Make the inner turn deadline optional, enrich the Runtime's in-memory turn state and status snapshot, safely log App Server stderr, and make final LLM failures auditable. Activate only after the live platform has no active Runner or Gateway completion.

**Tech Stack:** Python 3.12, asyncio, FastAPI, Pydantic, pytest, systemd user services, LangChain callbacks

---

### Task 1: Optional Gateway wall-clock timeout

**Files:**
- Modify: `gateway/tests/test_config.py`
- Modify: `gateway/tests/test_runtime.py`
- Modify: `gateway/src/codex_gateway/config.py`
- Modify: `gateway/src/codex_gateway/runtime.py`

- [ ] **Step 1: Write failing configuration tests**

Change the default assertion to `is None`, add an explicit zero test, retain positive values, and reject negatives:

```python
def test_zero_request_timeout_disables_deadline(monkeypatch):
    monkeypatch.setenv("CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS", "0")
    assert Settings.from_env().request_timeout_seconds is None


def test_positive_request_timeout_is_preserved(monkeypatch):
    monkeypatch.setenv("CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS", "21600")
    assert Settings.from_env().request_timeout_seconds == 21600
```

- [ ] **Step 2: Run tests and confirm RED**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_config.py -q
```

Expected: failures because the default is `600` and zero is rejected.

- [ ] **Step 3: Implement optional timeout parsing**

Add a timeout-specific parser and change the field:

```python
def _optional_timeout(env_name: str) -> int | None:
    raw = os.getenv(env_name)
    if raw in (None, "", "0"):
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{env_name} is outside its valid range")
    return value


request_timeout_seconds: int | float | None = None
```

- [ ] **Step 4: Write an unbounded-runtime regression test**

Update `runtime_factory` to default to `None`, then add:

```python
@pytest.mark.asyncio
async def test_unbounded_completion_waits_until_turn_finishes(runtime_factory):
    runtime, transports = runtime_factory(timeout=None)
    await runtime.start()
    task = asyncio.create_task(runtime.complete("prompt", {"type": "object"}))
    await transports[0].turn_started.wait()
    await asyncio.sleep(0.06)
    assert not task.done()
    assert not any(method == "turn/interrupt" for method, _ in transports[0].requests)
    transports[0].release_turns.set()
    assert (await task).final_message
    await runtime.stop()
```

- [ ] **Step 5: Run the regression test and confirm GREEN**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_runtime.py::test_unbounded_completion_waits_until_turn_finishes -q
```

Expected: pass because `asyncio.wait_for` already treats `None` as unbounded. The preceding configuration test is the RED test for making this path reachable from production settings.

- [ ] **Step 6: Make the unbounded path explicit as a green refactor**

Use direct await when the timeout is disabled and retain the existing interrupting deadline for positive values:

```python
turn = self._run_turn(prompt, output_schema, Path(cwd), pinned_config=pinned_config)
timeout = self.settings.request_timeout_seconds
if timeout is None:
    return await turn
try:
    return await asyncio.wait_for(turn, timeout=timeout)
except asyncio.TimeoutError as exc:
    raise CodexTimeout(f"Codex request exceeded {timeout} seconds") from exc
```

- [ ] **Step 7: Verify Task 1 and commit**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_config.py gateway/tests/test_runtime.py -q
git add gateway/src/codex_gateway/config.py gateway/src/codex_gateway/runtime.py gateway/tests/test_config.py gateway/tests/test_runtime.py
git commit -m "fix: allow unbounded Codex turns"
```

Expected: all selected tests pass.

### Task 2: Explicit terminal errors and turn telemetry

**Files:**
- Modify: `gateway/tests/test_errors.py`
- Modify: `gateway/tests/test_runtime.py`
- Modify: `gateway/src/codex_gateway/errors.py`
- Modify: `gateway/src/codex_gateway/runtime.py`

- [ ] **Step 1: Write failing error-mapping tests**

Add public error envelope cases for `CodexInterrupted` and `CodexContextLimit`. Add parametrized Runtime tests using both string and tagged-object error info:

```python
@pytest.mark.parametrize(
    ("status", "info", "expected"),
    [
        ("interrupted", None, CodexInterrupted),
        ("failed", {"contextWindowExceeded": {}}, CodexContextLimit),
        ("failed", {"responseStreamDisconnected": {}}, CodexRuntimeFailure),
        ("failed", "UsageLimitExceeded", CodexRateLimit),
    ],
)
@pytest.mark.asyncio
async def test_terminal_turn_errors_are_classified(
    runtime_factory, status, info, expected
):
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
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_errors.py gateway/tests/test_runtime.py -q
```

Expected: import/mapping failures for the new public errors.

- [ ] **Step 3: Implement safe normalized classification**

Add:

```python
class CodexInterrupted(GatewayError):
    status_code = 502
    code = "codex_interrupted"


class CodexContextLimit(GatewayError):
    status_code = 400
    error_type = "invalid_request_error"
    code = "codex_context_limit"
```

Normalize `codexErrorInfo` by extracting its string or first object key and removing punctuation with `casefold()`. Map rate-limit, unauthorized, context, interrupted, and transient stream failures without exposing prompt or response text.

- [ ] **Step 4: Write failing telemetry tests**

Inject a monotonic clock into `CodexRuntime`, start a blocked completion, advance the clock, send a notification, and assert:

```python
snapshot = runtime.activity_snapshot()
assert snapshot.active_completions == 1
assert snapshot.oldest_active_seconds == 12.0
assert snapshot.stalest_progress_seconds == 2.0
```

- [ ] **Step 5: Run the telemetry test and confirm RED**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_runtime.py -q
```

Expected: failure because `activity_snapshot` and progress timestamps do not exist.

- [ ] **Step 6: Implement telemetry**

Add `started_at` and `last_progress_at` to `_TurnState`, update progress before applying every known-thread notification, and return a frozen `RuntimeActivity` snapshot containing active count plus oldest/stalest ages. Keep the existing `active_completions` property for compatibility.

- [ ] **Step 7: Add correlated lifecycle logging and commit**

Extend `complete()` with optional keyword-only `request_id`, `run_id`, and `retry_count`. Store these in `_TurnState` and log start/terminal events with IDs, status, normalized code, and elapsed milliseconds only.

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_errors.py gateway/tests/test_runtime.py -q
git add gateway/src/codex_gateway/errors.py gateway/src/codex_gateway/runtime.py gateway/tests/test_errors.py gateway/tests/test_runtime.py
git commit -m "feat: classify and observe Codex turn lifecycle"
```

Expected: all selected tests pass.

### Task 3: Backward-compatible status and SDK retry correlation

**Files:**
- Modify: `gateway/tests/test_app.py`
- Modify: `gateway/src/codex_gateway/models.py`
- Modify: `gateway/src/codex_gateway/app.py`

- [ ] **Step 1: Write failing API tests**

Make the fake runtime expose an activity snapshot. Assert `/internal/status` includes `accepting`, `oldest_active_seconds`, and `stalest_progress_seconds`. Send `x-stainless-retry-count: 2` and assert the fake Runtime receives `request_id`, run ID, and integer retry count.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_app.py -q
```

Expected: missing response fields and unexpected Runtime arguments.

- [ ] **Step 3: Implement the API additions**

Extend `GatewayStatus` with optional non-negative age fields and `accepting: bool = True`. Read the retry header as a bounded non-negative integer, reject malformed values with `InvalidRequest`, and pass all correlation fields to `runtime.complete()`.

- [ ] **Step 4: Verify and commit**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_app.py gateway/tests/test_models.py -q
git add gateway/src/codex_gateway/app.py gateway/src/codex_gateway/models.py gateway/tests/test_app.py gateway/tests/test_models.py
git commit -m "feat: expose long-turn activity status"
```

Expected: all selected tests pass.

### Task 4: Redacted App Server stderr diagnostics

**Files:**
- Modify: `gateway/tests/fake_app_server.py`
- Modify: `gateway/tests/test_transport.py`
- Modify: `gateway/src/codex_gateway/transport.py`

- [ ] **Step 1: Write failing stderr tests**

Use `caplog` and the fake server's stderr command to assert a credential-like value is absent, `[REDACTED]` is present, and a newline-free two-megabyte stream still cannot block stdout. Assert emitted diagnostic messages are capped at 8192 characters.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_transport.py -q
```

Expected: no stderr diagnostic record because bytes are discarded.

- [ ] **Step 3: Implement bounded redacted draining**

Replace `_discard_stderr` with `_read_stderr`. Buffer at most one diagnostic chunk, split on newlines when available, redact case-insensitive `authorization`, `api_key`, `token`, `secret`, and `password` assignments, truncate logged messages to 8192 characters, and continue draining all bytes regardless of logging.

- [ ] **Step 4: Verify and commit**

Run:

```bash
PYTHONPATH=gateway/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests/test_transport.py -q
git add gateway/src/codex_gateway/transport.py gateway/tests/test_transport.py gateway/tests/fake_app_server.py
git commit -m "feat: retain safe App Server diagnostics"
```

Expected: all transport tests pass, including the large stderr regression.

### Task 5: Complete Runner LLM audit records

**Files:**
- Modify: `platform/tests/unit/runner/test_runner.py`
- Modify: `platform/src/tradingng_platform/runner/callbacks.py`

- [ ] **Step 1: Write failing success/failure audit tests**

Assert a success record contains `status="completed"`, `completed_at`, and `duration_ms`. Add a failure test that starts a model call, raises a synthetic timeout whose text contains a secret, and asserts the interaction record contains `status="failed"`, safe `error_type`/`error_code`, timestamps and duration, but neither exception text nor secret.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
PYTHONPATH=platform/src /app/devs/TradingNG/.venv/bin/python -m pytest platform/tests/unit/runner/test_runner.py -q
```

Expected: missing success status/duration and no failure interaction record.

- [ ] **Step 3: Implement terminal records**

Add `status` and `duration_ms` on success. On error, append:

```python
record = {
    **pending,
    "status": "failed",
    "error_type": type(error).__name__,
    "error_code": error_code,
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "duration_ms": _elapsed_ms(pending),
    "retention_class": "raw_180d",
}
```

Do not serialize `str(error)`.

- [ ] **Step 4: Verify and commit**

Run:

```bash
PYTHONPATH=platform/src /app/devs/TradingNG/.venv/bin/python -m pytest platform/tests/unit/runner/test_runner.py -q
git add platform/src/tradingng_platform/runner/callbacks.py platform/tests/unit/runner/test_runner.py
git commit -m "feat: audit failed LLM interactions"
```

Expected: all selected tests pass.

### Task 6: Safe production service defaults and operator documentation

**Files:**
- Modify: `platform/tests/operations/test_deploy_config.py`
- Modify: `systemd/user/tradingng-codex-gateway.service`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Write failing deployment tests**

Add assertions:

```python
def test_gateway_service_supports_unbounded_turns_and_graceful_drain():
    service = (ROOT / "systemd/user/tradingng-codex-gateway.service").read_text()
    assert "Environment=CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS=0" in service
    assert "TimeoutStopSec=infinity" in service
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
PYTHONPATH=platform/src /app/devs/TradingNG/.venv/bin/python -m pytest platform/tests/operations/test_deploy_config.py -q
```

Expected: the service lacks both settings.

- [ ] **Step 3: Update the unit and documentation**

Set the unbounded environment value and unlimited stop timeout. Document timeout semantics, progress fields, journald diagnostics, and the activation gate requiring no Runner processes plus two zero-active snapshots.

- [ ] **Step 4: Verify and commit**

Run:

```bash
PYTHONPATH=platform/src /app/devs/TradingNG/.venv/bin/python -m pytest platform/tests/operations/test_deploy_config.py -q
git add systemd/user/tradingng-codex-gateway.service platform/tests/operations/test_deploy_config.py README.md README.zh-CN.md
git commit -m "ops: make long Codex turns the safe default"
```

Expected: deployment configuration tests pass.

### Task 7: Full verification without live activation

**Files:**
- Modify only if verification identifies a defect in files already in scope.

- [ ] **Step 1: Run formatting and static checks**

Run:

```bash
PYTHONPATH=gateway/src:platform/src /app/devs/TradingNG/.venv/bin/python -m ruff check gateway platform
git diff --check main...HEAD
```

Expected: no diagnostics.

- [ ] **Step 2: Run all Gateway tests**

Run:

```bash
PYTHONPATH=gateway/src:platform/src /app/devs/TradingNG/.venv/bin/python -m pytest gateway/tests -q
```

Expected: all tests pass.

- [ ] **Step 3: Run platform unit and operations tests**

Run:

```bash
PYTHONPATH=gateway/src:platform/src /app/devs/TradingNG/.venv/bin/python -m pytest platform/tests/unit platform/tests/operations -q
```

Expected: all tests pass.

- [ ] **Step 4: Run focused integration tests**

Run:

```bash
PYTHONPATH=gateway/src:platform/src /app/devs/TradingNG/.venv/bin/python -m pytest integration_tests/test_tradingagents_gateway.py integration_tests/test_platform_recovery.py -q
```

Expected: all tests pass or environment-dependent skips are explicitly reported.

- [ ] **Step 5: Confirm isolation and commit any verification-only fix**

Run:

```bash
git status --short
curl --silent --show-error --max-time 3 http://127.0.0.1:8000/internal/status
ps -eo pid,cmd | rg 'tradingng_platform.runner.cli' | rg -v 'rg '
```

Expected: branch clean; the live service is still the original process. Do not reload, restart, copy service files, or merge while any Runner or active completion exists.
