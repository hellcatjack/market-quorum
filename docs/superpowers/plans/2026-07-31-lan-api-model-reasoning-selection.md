# LAN API Model and Reasoning Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let authenticated physical-LAN OpenAI clients dynamically discover and select Codex models and supported reasoning efforts without making the LAN key or public headers part of TradingNG assessment execution.

**Architecture:** Normalize the stable Codex App Server `model/list` response in a focused Gateway module, expose reasoning-capable physical models through `/v1/models`, and resolve physical model requests into the existing per-completion `EffectiveCodexConfig`. Preserve private `codex-fast` and `codex-slow` routing for direct TradingNG calls while Caddy strips the LAN credential and every `X-TradingNG-*` header before proxying public requests.

**Tech Stack:** Python 3.10, FastAPI, Pydantic 2, Codex App Server JSON-RPC, pytest, Caddy 2.6.2, systemd, OpenAI Python SDK.

---

**Execution constraint:** Execute inline in the current `main` worktree. The user explicitly prohibited subagents and additional worktrees. Do not modify `TradingAgents/`. LAN requests may share Codex concurrency and account quota; do not add throttling, reservation, or a second Gateway.

## File map

- Create `gateway/src/codex_gateway/model_catalog.py`: immutable normalized model entries and fail-closed catalog parsing.
- Create `gateway/tests/test_model_catalog.py`: unit contract for valid rows, filtering, deduplication, caps, and malformed envelopes.
- Modify `gateway/src/codex_gateway/runtime.py`: request and sanitize the current App Server model catalog.
- Modify `gateway/tests/test_runtime.py`: script `model/list` responses, prove the runtime requests the stable catalog method, and sanitize failures.
- Modify `gateway/src/codex_gateway/models.py`: make `reasoning_effort` an explicit Chat Completions request field.
- Modify `gateway/src/codex_gateway/app.py`: advertise physical models and resolve public body selection without changing private route pins.
- Modify `gateway/tests/test_app.py`: lock the external and internal request contracts.
- Modify `deploy/caddy/tradingng.caddy`: delete every private TradingNG header on the LAN proxy only.
- Modify `platform/tests/operations/test_deploy_config.py`: lock explicit edge-header deletion and secret isolation.
- Modify `integration_tests/test_platform_security.py`: assert the public route cannot forward private run or route identity.
- Modify `README.md`: document dynamic model discovery and English client examples.
- Modify `README.zh-CN.md`: document the same flow independently in Chinese.
- Modify this plan after live verification to record production acceptance.

### Task 1: Normalize the dynamic Codex model catalog

**Files:**
- Create: `gateway/src/codex_gateway/model_catalog.py`
- Create: `gateway/tests/test_model_catalog.py`
- Test: `gateway/tests/test_model_catalog.py`

- [x] **Step 1: Write failing normalization tests**

Create `gateway/tests/test_model_catalog.py` with focused examples:

```python
import pytest

from codex_gateway.model_catalog import CodexModelOption, normalize_model_catalog


def test_normalizes_reasoning_models_filters_reserved_and_deduplicates_efforts():
    payload = {
        "data": [
            {
                "id": "gpt-5.6-sol",
                "model": "gpt-5.6-sol",
                "hidden": False,
                "defaultReasoningEffort": "medium",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "medium"},
                    {"reasoningEffort": "medium"},
                    {"reasoningEffort": "high"},
                ],
            },
            {
                "id": "codex-fast",
                "defaultReasoningEffort": "high",
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
            },
            {
                "id": "no-reasoning-model",
                "defaultReasoningEffort": None,
                "supportedReasoningEfforts": [],
            },
        ]
    }

    assert normalize_model_catalog(payload) == (
        CodexModelOption(
            id="gpt-5.6-sol",
            default_reasoning_effort="medium",
            supported_reasoning_efforts=("low", "medium", "high"),
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": "not-a-list"},
        {"data": []},
        {
            "data": [
                {
                    "id": "gpt-5.6-sol",
                    "defaultReasoningEffort": "xhigh",
                    "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                }
            ]
        },
    ],
)
def test_rejects_malformed_or_entirely_unusable_catalog(payload):
    with pytest.raises(ValueError, match="model catalog"):
        normalize_model_catalog(payload)


def test_rejects_catalog_over_the_hard_cap():
    row = {
        "defaultReasoningEffort": "low",
        "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
    }
    payload = {"data": [{"id": f"model-{index}", **row} for index in range(101)]}

    with pytest.raises(ValueError, match="model catalog"):
        normalize_model_catalog(payload, max_models=100)
```

- [x] **Step 2: Run the new test and verify RED**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest gateway/tests/test_model_catalog.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'codex_gateway.model_catalog'`.

- [x] **Step 3: Implement the immutable catalog boundary**

Create `gateway/src/codex_gateway/model_catalog.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RESERVED_MODEL_IDS = frozenset({"codex", "codex-fast", "codex-slow"})


@dataclass(frozen=True)
class CodexModelOption:
    id: str
    default_reasoning_effort: str
    supported_reasoning_efforts: tuple[str, ...]


def _bounded_string(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def normalize_model_catalog(
    payload: dict[str, Any],
    *,
    max_models: int = 100,
) -> tuple[CodexModelOption, ...]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > max_models:
        raise ValueError("Codex model catalog envelope is invalid")

    options: list[CodexModelOption] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = _bounded_string(row.get("id"), 128)
        default = _bounded_string(row.get("defaultReasoningEffort"), 32)
        raw_efforts = row.get("supportedReasoningEfforts")
        if (
            model_id is None
            or model_id in RESERVED_MODEL_IDS
            or model_id in seen
            or default is None
            or not isinstance(raw_efforts, list)
        ):
            continue
        efforts: list[str] = []
        for item in raw_efforts:
            if not isinstance(item, dict):
                continue
            effort = _bounded_string(item.get("reasoningEffort"), 32)
            if effort is not None and effort not in efforts:
                efforts.append(effort)
        if not efforts or default not in efforts:
            continue
        seen.add(model_id)
        options.append(
            CodexModelOption(
                id=model_id,
                default_reasoning_effort=default,
                supported_reasoning_efforts=tuple(efforts),
            )
        )

    if not options:
        raise ValueError("Codex model catalog has no usable reasoning models")
    return tuple(options)
```

- [x] **Step 4: Run catalog tests and verify GREEN**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest gateway/tests/test_model_catalog.py -q
```

Expected: all catalog tests pass.

- [x] **Step 5: Commit the catalog unit**

```bash
cd /app/devs/TradingNG
git add gateway/src/codex_gateway/model_catalog.py gateway/tests/test_model_catalog.py
git commit -m "feat: normalize Codex model catalog"
```

### Task 2: Read the model catalog through the Gateway runtime

**Files:**
- Modify: `gateway/src/codex_gateway/runtime.py`
- Modify: `gateway/tests/test_runtime.py`
- Test: `gateway/tests/test_runtime.py`

- [x] **Step 1: Script the App Server catalog in the runtime fixture**

In `gateway/tests/test_runtime.py`, add this response to `ScriptedTransport.__init__`:

```python
self.model_response = {
    "data": [
        {
            "id": "gpt-5.6-sol",
            "defaultReasoningEffort": "medium",
            "supportedReasoningEfforts": [
                {"reasoningEffort": "low"},
                {"reasoningEffort": "medium"},
                {"reasoningEffort": "high"},
                {"reasoningEffort": "xhigh"},
            ],
        }
    ],
    "nextCursor": None,
}
```

Handle the request before the fixture's final assertion:

```python
if method == "model/list":
    return self.model_response
```

- [x] **Step 2: Write failing runtime catalog tests**

Append to `gateway/tests/test_runtime.py`:

```python
@pytest.mark.asyncio
async def test_available_models_uses_picker_visible_bounded_catalog(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()

    models = await runtime.available_models()

    assert [model.id for model in models] == ["gpt-5.6-sol"]
    assert models[0].default_reasoning_effort == "medium"
    assert ("model/list", {"limit": 100, "includeHidden": False}) in transports[0].requests
    await runtime.stop()


@pytest.mark.asyncio
async def test_malformed_model_catalog_is_sanitized_as_unavailable(runtime_factory):
    runtime, transports = runtime_factory()
    await runtime.start()
    transports[0].model_response = {"data": []}

    with pytest.raises(CodexUnavailable, match="model catalog is unavailable"):
        await runtime.available_models()

    await runtime.stop()
```

- [x] **Step 3: Run the focused runtime tests and verify RED**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest gateway/tests/test_runtime.py \
  -k 'available_models or malformed_model_catalog' -q
```

Expected: both tests fail because `CodexRuntime.available_models` does not exist.

- [x] **Step 4: Implement sanitized runtime discovery**

Import the catalog types in `gateway/src/codex_gateway/runtime.py`:

```python
from codex_gateway.model_catalog import CodexModelOption, normalize_model_catalog
```

Add this method immediately after `effective_config`:

```python
async def available_models(self) -> tuple[CodexModelOption, ...]:
    transport = self._require_transport()
    try:
        response = await transport.request(
            "model/list",
            {"limit": 100, "includeHidden": False},
        )
        return normalize_model_catalog(response, max_models=100)
    except (JsonRpcError, TransportClosed, TypeError, ValueError) as exc:
        raise CodexUnavailable("Codex model catalog is unavailable") from exc
```

Do not log `response` or the exception message.

- [x] **Step 5: Run runtime and catalog tests and verify GREEN**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest gateway/tests/test_model_catalog.py gateway/tests/test_runtime.py -q
```

Expected: all tests pass.

- [x] **Step 6: Commit runtime discovery**

```bash
cd /app/devs/TradingNG
git add gateway/src/codex_gateway/runtime.py gateway/tests/test_runtime.py
git commit -m "feat: discover available Codex models"
```

### Task 3: Expose physical models and per-request reasoning selection

**Files:**
- Modify: `gateway/src/codex_gateway/models.py`
- Modify: `gateway/src/codex_gateway/app.py`
- Modify: `gateway/tests/test_app.py`
- Test: `gateway/tests/test_app.py`

- [x] **Step 1: Give the fake app runtime a physical model catalog**

Import `CodexModelOption` in `gateway/tests/test_app.py` and add to
`FakeRuntime.__init__`:

```python
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
```

Add:

```python
async def available_models(self):
    self.model_catalog_calls += 1
    return self.models
```

- [x] **Step 2: Replace the model-list expectation and add failing public selection tests**

Change `test_models_health_and_lifespan` so the expected ids are:

```python
assert [model["id"] for model in http.get("/v1/models").json()["data"]] == [
    "codex",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
]
```

Also assert the Sol row exposes:

```python
assert http.get("/v1/models").json()["data"][1] == {
    "id": "gpt-5.6-sol",
    "object": "model",
    "owned_by": "openai-codex",
    "default_reasoning_effort": "medium",
    "supported_reasoning_efforts": ["low", "medium", "high", "xhigh"],
}
```

Add these tests:

```python
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
```

- [x] **Step 3: Run the selected app tests and verify RED**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest gateway/tests/test_app.py \
  -k 'models_health or physical_model or unknown_model or partial_effort or private_route_bundle' -q
```

Expected: failures show that physical models are not advertised or accepted and body `reasoning_effort` is not resolved.

- [x] **Step 4: Add the explicit request field**

In `ChatCompletionRequest` in `gateway/src/codex_gateway/models.py`, add:

```python
reasoning_effort: str | None = None
```

Keep `ConfigDict(extra="allow")` for existing OpenAI client compatibility.

- [x] **Step 5: Implement public selection without weakening private pins**

In `gateway/src/codex_gateway/app.py`:

1. Replace `_PUBLIC_MODELS` with:

```python
_INHERITED_MODEL = "codex"
_PRIVATE_ROUTE_MODELS = ("codex-fast", "codex-slow")
_MODEL_ALIASES = (_INHERITED_MODEL, *_PRIVATE_ROUTE_MODELS)
```

2. Make `/v1/models` await `runtime.available_models()` and return `codex`
   followed by the normalized physical rows:

```python
@app.get("/v1/models")
async def models():
    physical = await runtime.available_models()
    return {
        "object": "list",
        "data": [
            {"id": "codex", "object": "model", "owned_by": "local"},
            *[
                {
                    "id": item.id,
                    "object": "model",
                    "owned_by": "openai-codex",
                    "default_reasoning_effort": item.default_reasoning_effort,
                    "supported_reasoning_efforts": list(item.supported_reasoning_efforts),
                }
                for item in physical
            ],
        ],
    }
```

3. In `chat_completions`, track internal pinning separately:

```python
pinned_config = None
is_tradingng_pin = False
```

4. Preserve the existing alias branches. Set `is_tradingng_pin = True` only
   after a complete legacy or fast/slow header bundle constructs its
   `EffectiveCodexConfig`. If `body.model == "codex"`, no private bundle is
   present, and `body.reasoning_effort is not None`, raise:

```python
raise InvalidRequest(
    "reasoning_effort requires an explicit physical model",
    param="reasoning_effort",
)
```

5. Replace the old global `_PUBLIC_MODELS` rejection with a physical-model
   branch for `body.model not in _MODEL_ALIASES`:

```python
private_values = (
    tradingng_run_id,
    codex_model,
    codex_reasoning_effort,
    fast_codex_model,
    fast_codex_reasoning_effort,
    slow_codex_model,
    slow_codex_reasoning_effort,
)
if any(value is not None for value in private_values):
    raise InvalidRequest("TradingNG pin headers require a private model alias")
catalog = await runtime.available_models()
selected = next((item for item in catalog if item.id == body.model), None)
if selected is None:
    raise ModelNotFound(body.model)
effort = (
    selected.default_reasoning_effort
    if body.reasoning_effort is None
    else body.reasoning_effort
)
if effort not in selected.supported_reasoning_efforts:
    raise InvalidRequest(
        f"reasoning_effort {effort!r} is not supported by model {body.model!r}",
        param="reasoning_effort",
    )
pinned_config = EffectiveCodexConfig(
    model=selected.id,
    reasoning_effort=effort,
).require_complete()
```

6. Run the current run-id, pin-length, and snapshot log validation only inside
   `if is_tradingng_pin:`. Do not require a run id merely because a physical LAN
   request has a `pinned_config`.

7. Continue passing `pinned_config` to `runtime.complete`; pass
   `run_id=tradingng_run_id if is_tradingng_pin else None`. For a complete
   private route bundle, ignore `body.reasoning_effort` and retain the frozen
   header effort.

- [x] **Step 6: Run the full Gateway suite and verify GREEN**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest gateway/tests -q
```

Expected: every Gateway test passes; existing tool-call, retry, size, timeout,
transport, audit, and error behavior remains green.

- [x] **Step 7: Commit the public API contract**

```bash
cd /app/devs/TradingNG
git add gateway/src/codex_gateway/models.py gateway/src/codex_gateway/app.py gateway/tests/test_app.py
git commit -m "feat: select Codex model per LAN request"
```

### Task 4: Strip private TradingNG headers at the LAN edge

**Files:**
- Modify: `platform/tests/operations/test_deploy_config.py`
- Modify: `integration_tests/test_platform_security.py`
- Modify: `deploy/caddy/tradingng.caddy`
- Test: `platform/tests/operations/test_deploy_config.py`
- Test: `integration_tests/test_platform_security.py`

- [x] **Step 1: Add failing explicit-header deletion assertions**

Define this tuple in both relevant tests, or inline the same exact values:

```python
private_headers = (
    "X-TradingNG-Run-ID",
    "X-TradingNG-Codex-Model",
    "X-TradingNG-Codex-Reasoning-Effort",
    "X-TradingNG-Codex-Fast-Model",
    "X-TradingNG-Codex-Fast-Reasoning-Effort",
    "X-TradingNG-Codex-Slow-Model",
    "X-TradingNG-Codex-Slow-Reasoning-Effort",
)
for header in private_headers:
    assert f"header_up -{header}" in config
```

In `integration_tests/test_platform_security.py`, use `caddy` instead of
`config` for the source string. Retain the existing assertion that Authorization
is deleted and the internal status path is absent.

In `test_public_caddy_installer_isolates_and_rotates_the_lan_api_key`, also
prove that the credential cannot become a platform dependency:

```python
platform_configuration = "\n".join(
    (ROOT / path).read_text()
    for path in (
        ".env.platform.example",
        "systemd/user/tradingng-platform-api.service",
        "systemd/user/tradingng-platform-scheduler.service",
        "systemd/user/tradingng-platform-validation.service",
        "systemd/user/tradingng-platform-alpha-broker.service",
        "systemd/user/tradingng-platform-worker@.service",
    )
)
assert "CODEX_GATEWAY_LAN_API_KEY" not in platform_configuration
```

- [x] **Step 2: Run the two tests and verify RED**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest \
  platform/tests/operations/test_deploy_config.py::test_public_caddy_exposes_only_authenticated_physical_lan_codex_api \
  integration_tests/test_platform_security.py::test_public_routing_exposes_only_authenticated_physical_lan_gateway \
  -q
```

Expected: both tests fail because Caddy currently deletes only Authorization.

- [x] **Step 3: Delete all private headers in the LAN reverse proxy**

Extend the existing proxy block in `deploy/caddy/tradingng.caddy`:

```caddyfile
reverse_proxy 127.0.0.1:8000 {
    header_up -Authorization
    header_up -X-TradingNG-Run-ID
    header_up -X-TradingNG-Codex-Model
    header_up -X-TradingNG-Codex-Reasoning-Effort
    header_up -X-TradingNG-Codex-Fast-Model
    header_up -X-TradingNG-Codex-Fast-Reasoning-Effort
    header_up -X-TradingNG-Codex-Slow-Model
    header_up -X-TradingNG-Codex-Slow-Reasoning-Effort
}
```

Do not add these deletions to loopback Gateway or the platform runner; direct
TradingNG calls must retain them.

- [x] **Step 4: Validate adapted Caddy and verify GREEN**

Run:

```bash
cd /app/devs/TradingNG
CODEX_GATEWAY_LAN_API_KEY=test-value caddy validate \
  --config deploy/caddy/tradingng.caddy --adapter caddyfile
.venv/bin/pytest \
  platform/tests/operations/test_deploy_config.py \
  integration_tests/test_platform_security.py -q
```

Expected: Caddy reports `Valid configuration` and all selected tests pass.

- [x] **Step 5: Commit the edge isolation**

```bash
cd /app/devs/TradingNG
git add deploy/caddy/tradingng.caddy \
  platform/tests/operations/test_deploy_config.py \
  integration_tests/test_platform_security.py
git commit -m "fix: isolate LAN requests from TradingNG pins"
```

### Task 5: Document the dynamic LAN client contract and verify the repository

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [x] **Step 1: Update the English LAN API example**

In the `LAN OpenAI-compatible API` section of `README.md`, retain installation,
key retrieval, rotation, CIDR, and denial behavior. Replace the static alias-only
client description with:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://ushome.amycat.com/openai/v1",
    api_key="<LAN Gateway key>",
)
models = client.models.list()
completion = client.chat.completions.create(
    model="gpt-5.6-sol",
    reasoning_effort="high",
    messages=[{"role": "user", "content": "Analyze this data."}],
)
```

Document these exact rules:

- inspect `/models` for current physical model ids, supported efforts, and the
  per-model default;
- omit `reasoning_effort` to use the selected physical model's catalog default;
- use `model="codex"` without `reasoning_effort` to inherit the current local
  Codex configuration;
- `codex-fast` and `codex-slow` are private TradingNG routes and are not public
  model choices;
- the LAN key and public headers are stripped at Caddy and never configure
  assessment execution;
- external requests still share Codex capacity and quota with assessments.

- [x] **Step 2: Update the Chinese LAN API example separately**

Add the equivalent Python example and rules to `README.zh-CN.md`. Use the terms
“物理模型”“思考深度”“继承本机配置”“TradingNG 私有路由” and state plainly that
the key does not need to be copied to `.env.platform`.

- [x] **Step 3: Run source, documentation, and full repository verification**

Run:

```bash
cd /app/devs/TradingNG
git diff --check
rg -n 'TB[D]|TO[D]O|implement la[t]er|fill in detai[l]s' \
  README.md README.zh-CN.md \
  gateway/src/codex_gateway/model_catalog.py \
  gateway/src/codex_gateway/runtime.py \
  gateway/src/codex_gateway/app.py \
  deploy/caddy/tradingng.caddy
test -z "$(git status --short TradingAgents)"
bash scripts/verify_platform.sh
```

Expected: placeholder and TradingAgents checks are silent. Gateway, platform,
real MySQL, Web tests/build, npm audit, Caddy, identity, and artifact checks all
exit 0. Existing environment-dependent migration-database skips remain
acceptable when explicitly reported.

- [x] **Step 4: Commit the bilingual documentation**

```bash
cd /app/devs/TradingNG
git add README.md README.zh-CN.md
git commit -m "docs: explain LAN model and reasoning selection"
```

### Task 6: Deploy without interrupting assessments and exercise the live API

**Files:**
- Modify runtime state only: user Gateway process and system Caddy configuration.
- Modify after acceptance: `docs/superpowers/plans/2026-07-31-lan-api-model-reasoning-selection.md`

- [x] **Step 1: Record an idle production baseline**

Query `/internal/status`, platform `/readyz`, MySQL assessment counts through
`Settings()`, and systemd PIDs. Require:

```text
Gateway active_completions = 0
active assessment runs = 0
Gateway health = 200
platform readiness = 200
```

Record Gateway, Caddy, API, scheduler, validation, Alpha broker, and all worker
PIDs. If a Gateway completion or assessment is active, leave every service
running and poll in short intervals until the system is idle; do not interrupt
the work.

- [x] **Step 2: Restart only Gateway and install final Caddy**

Run:

```bash
cd /app/devs/TradingNG
systemctl --user restart tradingng-codex-gateway.service
sudo -n scripts/install_public_caddy.sh \
  --mode final --confirm-domain ushome.amycat.com
```

Expected: Gateway becomes active and healthy; installer reports
`lan_api_key_state=reused`; Caddy becomes active with its dedicated secret
environment and no `--environ`. API, scheduler, validation, Alpha broker, and
worker PIDs remain unchanged.

- [x] **Step 3: Verify dynamic discovery and explicit selection through the LAN edge**

Use a root-readable in-memory key and a local-address-pinned HTTPS transport so
the test preserves `ushome.amycat.com` TLS validation while connecting from
`192.168.1.31`. Through the installed OpenAI Python SDK:

1. list models;
2. choose a returned physical model and one of its returned supported efforts;
3. send one non-streaming completion with that explicit pair;
4. assert a non-empty assistant choice and finish reason;
5. print only model id, selected effort, status, and `lan_model_selection=passed`.

Do not print the key, prompt, answer, usage, raw model catalog, or response
headers.

- [x] **Step 4: Prove forged private headers are stripped**

Send a second explicit physical-model completion through Caddy with
`X-TradingNG-Run-ID: lan-forgery-probe` and all six fake route-pin headers.
Assert the request still resolves from its body model and effort. Scan Gateway
journal entries since the probe and assert the marker and fake model names do
not appear. Print only `private_header_strip=passed`.

- [x] **Step 5: Prove internal loopback remains keyless**

Call `http://127.0.0.1:8000/v1/chat/completions` with `model="codex"`, no
Authorization header, and no LAN key. Assert a non-empty assistant choice and
finish reason. Do not print response content; print only
`internal_keyless_gateway=passed`.

- [x] **Step 6: Prove persistence, secret safety, and business-state stability**

Assert:

```text
Caddy and Gateway are active and enabled
Caddy loads only .env.gateway-lan and has no --environ
.env.gateway-lan is root-owned, mode 600, ignored, and untracked
the full LAN key is absent from Caddy and Gateway journals since deployment
assessment totals and statuses match the baseline except independent user work
API, scheduler, validation, Alpha broker, and worker PIDs are unchanged
only Gateway and Caddy PIDs changed as planned
TradingAgents has no worktree changes
```

- [x] **Step 7: Record acceptance and commit without pushing**

Mark every completed checkbox in this plan only after fresh automated and live
evidence. Then run:

```bash
cd /app/devs/TradingNG
git diff --check
test -z "$(git status --short TradingAgents)"
git check-ignore -q .env.gateway-lan
test -z "$(git ls-files .env.gateway-lan var reports)"
git status --short --branch
git add docs/superpowers/plans/2026-07-31-lan-api-model-reasoning-selection.md
git commit -m "docs: record LAN model selection acceptance"
```

Do not push unless the user explicitly requests GitHub submission. Never stage
the LAN key, `.env.platform`, runtime artifacts, journals, prompts, answers,
model-list response bodies, or credentials.

## Production acceptance evidence

- Deployed from an idle baseline on 2026-07-31 at 16:39 EDT: zero active
  Gateway completions, zero active or queued assessments, and 111 succeeded
  assessment runs.
- Fresh repository verification passed 699 Python tests with two explicitly
  environment-dependent skips, plus 86 Web tests, lint, typecheck, production
  build, npm audit, Caddy validation, identity convergence, and artifact checks.
- Live discovery returned the inherited alias plus seven reasoning-capable
  physical models. OpenAI SDK completions passed with explicit
  `gpt-5.6-terra/high` and with forged private TradingNG headers.
- The forgery marker, fake pin values, and full LAN key were absent from the
  relevant Gateway and Caddy journals. A direct loopback completion without
  Authorization also passed.
- All 36 platform service PIDs and the 111-run business state were unchanged.
  Only Gateway and Caddy restarted as planned; both are active and enabled.
- `.env.gateway-lan` remains root-owned mode `0600`, ignored and untracked.
  `TradingAgents/` remains unchanged.
