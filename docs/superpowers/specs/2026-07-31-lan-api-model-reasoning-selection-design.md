# LAN API Model and Reasoning Selection Design

**Date:** 2026-07-31  
**Status:** Approved for implementation planning  
**Scope:** The physical-LAN OpenAI-compatible API only

## Objective

Allow authenticated clients on `192.168.1.0/24` to select an available Codex
model and its supported reasoning effort through the existing
`https://ushome.amycat.com/openai/v1` API. Preserve TradingNG's private fast/slow
model routing and ensure that the LAN Bearer key never becomes an input,
credential, header, or configuration dependency of assessment execution.

External LAN requests may share and consume the same Codex concurrency,
machine resources, and account quota as TradingNG. Resource reservation,
priority scheduling, and a second Codex account are explicitly out of scope.

## Current State

Gateway accepts three aliases:

- `codex` inherits the latest effective Codex configuration unless a complete
  private TradingNG pin bundle is present.
- `codex-fast` and `codex-slow` resolve from a complete set of private
  `X-TradingNG-*` headers frozen into an assessment snapshot.

The current public `/v1/models` response advertises all three aliases, but a LAN
client cannot successfully call the fast or slow aliases because it does not
have a valid TradingNG run and route-pin bundle. The request model permits extra
JSON fields, so `reasoning_effort` is currently accepted and silently ignored.

System Caddy validates the independent LAN Bearer key, strips `Authorization`,
strips `/openai`, and proxies to loopback Gateway. TradingNG bypasses this edge
and calls `http://127.0.0.1:8000/v1` directly.

## Considered Approaches

### Dynamic Codex catalog and body-level selection — selected

Gateway calls the stable Codex App Server `model/list` method and projects the
picker-visible models, default reasoning effort, and supported efforts into an
OpenAI-compatible models response. A chat request selects a physical model with
the standard `model` field and optionally supplies `reasoning_effort`.

This stays aligned with the account's actual model catalog and avoids stale
allowlists when Codex adds, hides, upgrades, or changes model effort support.

### Static model and effort allowlists

This would reuse TradingNG's current `gpt-5.6-terra` and `gpt-5.6-sol` policy
types. It is simple, but it makes an unrelated platform policy the source of
truth for the LAN API and requires code deployments for model-catalog changes.

### Custom model-selection headers

This would keep the request body unchanged and add public `X-Codex-*` headers.
It is less compatible with OpenAI clients, harder to discover, and too easy to
confuse with the private `X-TradingNG-*` contract.

## Architecture

The existing single Gateway and Codex App Server remain in place. No new port,
service, database table, queue, or account is introduced.

```text
LAN client
  -> HTTPS Caddy: CIDR + Bearer validation
  -> strip Authorization and every X-TradingNG-* private header
  -> loopback Gateway
  -> Codex App Server model/list or thread/start + turn/start

TradingNG worker
  -> loopback Gateway directly
  -> retain complete X-TradingNG-* frozen route bundle
  -> existing codex-fast / codex-slow resolution
```

The two flows deliberately share Gateway runtime capacity. Their configuration
and authentication inputs remain separate.

## API Contract

### Model discovery

`GET /openai/v1/models` continues to return an OpenAI list envelope. Gateway
calls Codex App Server `model/list` with `includeHidden: false` and a bounded
page size, validates the response, and returns:

- the reserved `codex` alias first;
- each reasoning-capable picker-visible physical model returned by the current
  Codex account;
- no `codex-fast` or `codex-slow` entry, because those aliases are private
  TradingNG routes rather than usable LAN models.

Each physical model object contains the standard `id`, `object`, and
`owned_by` fields plus additive discovery metadata:

```json
{
  "id": "gpt-5.6-sol",
  "object": "model",
  "owned_by": "openai-codex",
  "default_reasoning_effort": "medium",
  "supported_reasoning_efforts": ["low", "medium", "high", "xhigh"]
}
```

The projection removes duplicates and filters reserved aliases even if an
upstream catalog entry collides with one. Picker rows that do not publish both
a supported-effort set and a valid default effort are not suitable for this
API's explicit reasoning contract and are omitted without breaking the rest of
the catalog. Gateway does not expose hidden models, upgrade prompts, internal
capability metadata, or the local Codex configuration snapshot.

The `codex` object represents inheritance and therefore does not claim a fixed
effort list. Its existing OpenAI model fields remain sufficient.

### Chat completion selection

LAN callers can select a physical model and effort in the request body:

```json
{
  "model": "gpt-5.6-sol",
  "reasoning_effort": "xhigh",
  "messages": [
    {"role": "user", "content": "Analyze this data."}
  ]
}
```

The resolution rules are exact:

1. `model: "codex"` with no `reasoning_effort` inherits the latest effective
   Codex model and effort, preserving existing behavior.
2. `model: "codex"` with `reasoning_effort` is rejected with `400
   invalid_request`; partial inheritance would be ambiguous and harder to
   reproduce.
3. A picker-visible physical model is accepted only if it exists in the current
   Codex catalog.
4. If a physical model omits `reasoning_effort`, Gateway pins the catalog's
   `defaultReasoningEffort` for that request.
5. If a physical model supplies `reasoning_effort`, the value must occur in
   that model's `supportedReasoningEfforts` list.
6. `codex-fast` and `codex-slow` retain their existing behavior only when a
   complete private TradingNG route bundle is present. They remain valid for
   direct internal calls and are not public discovery entries.
7. For a valid internal route bundle, its frozen header values always win;
   body-level `reasoning_effort` is ignored so a client-library default cannot
   change an admitted assessment snapshot.

Gateway constructs an `EffectiveCodexConfig` from the selected physical model
and resolved effort and passes it as the existing per-completion
`pinned_config`. Runtime already maps the model to App Server `thread/start`
and effort to `turn/start`, so no persistent Codex configuration is written.

### Errors

- Unknown or hidden physical model: `404 model_not_found`, with `param=model`.
- Unsupported or empty effort: `400 invalid_request`, with
  `param=reasoning_effort`.
- Malformed catalog response or unavailable model catalog: `503
  codex_unavailable` without leaking the upstream response.
- Existing request-size, runtime, rate-limit, timeout, and sanitized error
  behavior remains unchanged.

No request silently falls back to another physical model or effort.

## Authentication and TradingNG Isolation

The LAN key remains only in root-owned mode-`0600` `.env.gateway-lan`, loaded by
the system Caddy unit. It is not added to `.env.platform`, Gateway settings,
TradingNG settings, database records, assessment snapshots, or worker command
lines.

For the `/openai/*` reverse proxy, Caddy removes:

- `Authorization`;
- `X-TradingNG-Run-ID`;
- `X-TradingNG-Codex-Model`;
- `X-TradingNG-Codex-Reasoning-Effort`;
- `X-TradingNG-Codex-Fast-Model`;
- `X-TradingNG-Codex-Fast-Reasoning-Effort`;
- `X-TradingNG-Codex-Slow-Model`;
- `X-TradingNG-Codex-Slow-Reasoning-Effort`.

The deletion is explicit rather than wildcard-dependent so the deployed Caddy
2.6.2 behavior is reviewable and testable. A LAN caller therefore cannot use
the public key to impersonate an assessment run or inject a private route pin.

TradingNG continues to call loopback Gateway without a LAN key. Existing
platform fast/slow model settings, run snapshots, reports, audit projections,
and internal status behavior are unchanged. Key generation and rotation
continue to restart only Caddy.

## Runtime Model Catalog Boundary

Gateway adds one focused runtime operation that requests `model/list` and
normalizes each row into an immutable internal catalog entry. It must:

- request only picker-visible entries;
- accept the App Server's documented camelCase response fields;
- admit only rows with a non-empty model id, a non-empty default effort, and a
  non-empty, deduplicated set of supported efforts;
- require the default effort to be supported, omitting an incompatible row
  while retaining other valid rows;
- cap the response size and reject a malformed envelope or an entirely unusable
  catalog;
- avoid logging raw catalog responses.

The first implementation uses a fresh local App Server lookup for discovery
and physical-model validation. Caching is unnecessary for this scope and could
make recently changed account availability stale.

## Observability

Existing completion logs continue to record the resolved physical model and
reasoning effort without logging prompts, answers, Bearer keys, or complete
headers. LAN requests have no TradingNG run id. Internal assessment requests
retain their real run id and snapshot id.

No LAN request is written to assessment tables, run events, artifacts,
validation records, or historical memory.

## Verification Strategy

Automated tests must prove:

- model catalog normalization, reserved-id filtering, and malformed response
  failure;
- `/v1/models` advertises `codex` plus physical models and effort metadata but
  not private route aliases;
- explicit model and effort produce the expected per-request pinned config;
- omitted effort uses the model's documented default;
- unknown model and unsupported effort fail without starting a Codex thread;
- `codex` still inherits, while partial `codex` override is rejected;
- complete internal fast/slow bundles remain unchanged and body effort cannot
  override their snapshots;
- Caddy strips Authorization and all private TradingNG headers only on the LAN
  route;
- the LAN secret remains ignored, untracked, isolated from platform service
  environments, and absent from logs;
- all existing Gateway, platform, Caddy, and Web verification remains green.

Live acceptance must use an OpenAI Python client to list models and complete at
least one request with an explicit physical model and effort. It must also send
forged private headers through the LAN edge and verify from a controlled test
runtime or sanitized logs that they do not reach Gateway as route pins.

## Deployment and Rollback

Before restarting Gateway, record platform assessment counts and confirm there
are no active Gateway completions or running assessments. Do not interrupt an
active assessment merely to deploy this feature.

Deploy the Gateway code and Caddy header-stripping change, then restart only
Gateway and system Caddy. API, scheduler, validation, Alpha broker, and worker
services remain running. Recheck readiness, model discovery, explicit selection,
internal loopback behavior, assessment counts, service PIDs, and secret scans.

Rollback restores the previous Gateway commit and Caddy site backup, then
restarts only Gateway and Caddy after the same idle check. The LAN key remains
valid unless the operator separately requests rotation.

## Out of Scope

- Separate external and internal Gateway processes.
- Reserved capacity, request priority, throttling, or quota accounting.
- Responses API or streaming completions.
- Changes to TradingAgents source.
- Changes to TradingNG model-routing settings or UI.
- Persistent public-client identities, per-client keys, billing, or usage
  dashboards.
