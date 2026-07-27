# Fast/Slow Model Routing Design

## Goal

Allow administrators to select separate Codex models and reasoning efforts for
TradingAgents' frequent analysis path and decision path. New installations default
to:

- Fast route: `gpt-5.6-terra`, reasoning effort `high`
- Slow route: `gpt-5.6-sol`, reasoning effort `high`

TradingAgents itself remains unmodified.

## Existing constraints

TradingAgents already constructs two OpenAI-compatible clients:
`quick_think_llm` and `deep_think_llm`. It accepts a distinct model name for each,
but both clients share their base URL and default HTTP headers. The platform
currently collapses both clients to the public `codex` alias and pins one physical
model/effort pair in every request.

Existing admitted and running assessments have immutable configuration snapshots.
Changing the global routing policy must not alter those assessments.

## Considered approaches

### 1. Gateway route aliases with an immutable route header bundle (selected)

The runner configures `quick_think_llm=codex-fast` and
`deep_think_llm=codex-slow`. Every request carries the run ID plus both frozen
physical model/effort pairs. The Gateway validates the complete bundle and selects
the appropriate pair from the request's public model alias.

This keeps the TradingAgents submodule unchanged, supports a different effort on
each route, and preserves per-assessment reproducibility.

### 2. Use physical model names directly in the OpenAI request

This can select distinct models, but the shared TradingAgents headers cannot attach
a distinct reasoning effort to each client. Encoding effort into a model string
would create an undocumented protocol and weak validation.

### 3. Infer the route from prompt content in the Gateway

This requires fragile prompt classification and cannot guarantee that future
TradingAgents prompts follow the same patterns. It is rejected.

## Architecture

### Persistent policy

A dedicated `model_routing_policy` record stores:

- fast model
- fast reasoning effort
- slow model
- slow reasoning effort
- version, updater and update timestamp

The repository seeds the defaults lazily, following the existing scheduler policy
pattern. Only an administrator with `assessments:admin` may update it. Updates
produce an audit event containing old and new non-secret values.

The initial selectable model set is:

- `gpt-5.6-terra`
- `gpt-5.6-sol`

The selectable effort set is:

- `low`
- `medium`
- `high`
- `xhigh`
- `max`
- `ultra`

These capabilities are returned by the model-routing settings endpoint so the web
client does not duplicate validation rules.

### Admission snapshot

At every scheduler pass, the active model-routing policy is loaded alongside the
capacity policy. When a queued run is admitted, its immutable snapshot gains:

```json
{
  "gateway": {
    "model": "gpt-5.6-sol",
    "reasoning_effort": "xhigh",
    "snapshot_id": "gateway-runtime-snapshot",
    "routes": {
      "fast": {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high"
      },
      "slow": {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high"
      }
    },
    "routing_snapshot_id": "sha256-of-routes"
  }
}
```

The legacy `model`, `reasoning_effort`, and `snapshot_id` remain for Gateway health
diagnostics and compatibility. A worker reading an older snapshot without
`routes` uses the legacy pair for both routes.

### Runner and Gateway protocol

The runner uses:

- `codex-fast` for `quick_think_llm`
- `codex-slow` for `deep_think_llm`

Its shared headers contain:

- `X-TradingNG-Run-ID`
- `X-TradingNG-Codex-Fast-Model`
- `X-TradingNG-Codex-Fast-Reasoning-Effort`
- `X-TradingNG-Codex-Slow-Model`
- `X-TradingNG-Codex-Slow-Reasoning-Effort`

The Gateway publishes `codex`, `codex-fast`, and `codex-slow` from `/v1/models`.
`codex` retains the existing optional legacy pin protocol. Route aliases require
the complete run routing bundle; partial or mixed bundles return an OpenAI-style
`invalid_request` error. The selected physical pair is passed to the existing
Codex runtime without changing its network policy or process isolation.

### API and web settings

Two protected endpoints are added:

- `GET /api/v1/system/model-routing`
- `PUT /api/v1/system/model-routing`

The system page displays a “模型路由” settings card with four selects. Viewers can
inspect the configuration but cannot edit it. Administrators can save it without
restarting services, and the next newly admitted assessment receives it.

Run details expose the frozen fast and slow model/effort values. Older runs
continue to show their legacy Gateway pair for both routes.

## Failure behavior

- Unsupported models or efforts are rejected with validation errors before
  persistence.
- Partial Gateway route headers are rejected rather than falling back to the
  Gateway's current default.
- Existing admitted/running snapshots are never rewritten.
- If the routing policy record is absent, deterministic defaults are inserted.
- If a legacy snapshot is claimed, both routes use its original single pair.

## Verification

Tests cover policy validation and persistence, authorization and audit events,
snapshot hashing, old-snapshot fallback, TradingAgents client configuration,
Gateway alias resolution and incomplete bundles, OpenAPI generation, web
read-only/admin behavior, and full Python/TypeScript regression suites.

