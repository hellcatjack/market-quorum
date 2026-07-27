# Fast/Slow Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent, user-configurable fast and slow Codex model routes with independently selectable reasoning efforts.

**Architecture:** Store a global audited routing policy, freeze it into each newly admitted assessment snapshot, and pass the two routes through shared TradingAgents headers. Resolve the `codex-fast` and `codex-slow` aliases in the Gateway while retaining the legacy `codex` protocol.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy/Alembic, React/TypeScript, TanStack Query, pytest, Vitest.

---

### Task 1: Model routing domain and persistence

**Files:**
- Create: `platform/src/tradingng_platform/model_routing.py`
- Modify: `platform/src/tradingng_platform/models/execution.py`
- Modify: `platform/src/tradingng_platform/models/__init__.py`
- Create: `platform/migrations/versions/20260727_0009_model_routing_policy.py`
- Test: `platform/tests/unit/test_model_routing.py`
- Test: `platform/tests/integration/test_records_system.py`

- [ ] Write failing tests for default values, allowed values, deterministic snapshot IDs, persistence, authorization, and audit.
- [ ] Run the focused tests and confirm failures are caused by the missing policy types.
- [ ] Implement `ModelRoute`, `ModelRoutingPolicy`, and `ModelRoutingPolicyRepository`.
- [ ] Add the policy table and Alembic migration.
- [ ] Run the focused tests and confirm they pass.

### Task 2: System API and web settings

**Files:**
- Modify: `platform/src/tradingng_platform/system/contracts.py`
- Modify: `platform/src/tradingng_platform/system/service.py`
- Modify: `platform/src/tradingng_platform/api/routes/system.py`
- Modify: `web/src/api/system.ts`
- Modify: `web/src/features/system/SystemPage.tsx`
- Modify: `web/src/features/system/SystemPage.test.tsx`
- Regenerate: `web/src/api/schema.d.ts`

- [ ] Write failing API/service tests for reading and updating the routing policy.
- [ ] Write failing web tests for default values, admin updates, and viewer read-only controls.
- [ ] Implement the API contracts, routes, service methods, query hooks, and settings form.
- [ ] Regenerate the OpenAPI TypeScript contract.
- [ ] Run the focused Python and Vitest tests and confirm they pass.

### Task 3: Admission snapshot and worker compatibility

**Files:**
- Modify: `platform/src/tradingng_platform/scheduler/repository.py`
- Modify: `platform/src/tradingng_platform/scheduler/service.py`
- Modify: `platform/src/tradingng_platform/scheduler/main.py`
- Modify: `platform/src/tradingng_platform/worker/service.py`
- Modify: `platform/src/tradingng_platform/runner/contracts.py`
- Test: `platform/tests/unit/scheduler/test_admission.py`
- Test: `platform/tests/unit/worker/test_service.py`
- Test: `platform/tests/integration/test_scheduler_worker.py`

- [ ] Write failing tests proving new snapshots freeze both routes and legacy snapshots map one pair to both routes.
- [ ] Implement policy loading, route snapshot serialization, and worker fallback.
- [ ] Run the focused snapshot and worker tests and confirm they pass.

### Task 4: TradingAgents adapter and Gateway aliases

**Files:**
- Modify: `platform/src/tradingng_platform/runner/tradingagents.py`
- Modify: `platform/src/tradingng_platform/runner/callbacks.py`
- Modify: `gateway/src/codex_gateway/app.py`
- Test: `platform/tests/unit/runner/test_runner.py`
- Test: `gateway/tests/test_app.py`

- [ ] Write failing tests for `codex-fast`/`codex-slow`, complete route headers, physical model resolution, and partial-bundle rejection.
- [ ] Configure the existing TradingAgents quick/deep clients with the route aliases and shared frozen headers.
- [ ] Add Gateway alias validation and route selection while retaining legacy behavior.
- [ ] Include logical route/model metadata in LLM audit records when available.
- [ ] Run focused runner and Gateway tests and confirm they pass.

### Task 5: Run detail observability and deployment

**Files:**
- Modify: `platform/src/tradingng_platform/assessments/contracts.py`
- Modify: `platform/src/tradingng_platform/assessments/repository.py`
- Modify: `web/src/features/runs/RunDetailPage.tsx`
- Modify: `platform/tests/integration/test_assessment_workflow.py`
- Modify: `web/src/features/runs/RunDetailPage.test.tsx`

- [ ] Write failing tests for displaying frozen fast/slow routes and legacy fallback.
- [ ] Implement API and UI route metadata.
- [ ] Run the database migration.
- [ ] Run all Gateway, platform, and web tests plus lint, type checking, and production build.
- [ ] Restart affected user services and verify health, settings persistence, and a non-billable configuration-path smoke check.
- [ ] Review the final diff for secrets and commit on `main`.
