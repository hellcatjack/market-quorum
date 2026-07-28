# Alpha Vantage Global Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every platform Alpha Vantage call through one quota-aware broker and recover eligible rate/transient failures automatically.

**Architecture:** A loopback FastAPI broker owns outbound Alpha calls, per-key quota state, priority scheduling, cooldown, request coalescing, and caching. External platform adapters call it without modifying TradingAgents; scheduler admission and the management UI consume its safe status snapshot.

**Tech Stack:** Python 3.10, asyncio, FastAPI, httpx/requests, Pydantic, pytest, React/TypeScript, Vitest, systemd user services.

---

### Task 1: Configuration and broker contracts

**Files:**
- Modify: `platform/src/tradingng_platform/config.py`
- Create: `platform/src/tradingng_platform/vendors/alpha_vantage_client.py`
- Modify: `platform/src/tradingng_platform/runner/contracts.py`
- Test: `platform/tests/unit/test_config.py`
- Test: `platform/tests/unit/vendors/test_alpha_vantage_client.py`

- [ ] Write failing tests proving loopback broker defaults, 80% utilization, positive
  in-flight/queue limits, safe status parsing, and stable error mapping.
- [ ] Run the focused tests and confirm they fail because the contracts do not exist.
- [ ] Add validated settings, request/status models, sync/async clients, and typed broker
  errors without exposing credentials.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Quota coordinator and broker service

**Files:**
- Create: `platform/src/tradingng_platform/vendors/alpha_vantage_broker.py`
- Create: `platform/src/tradingng_platform/vendors/alpha_vantage_broker_app.py`
- Modify: `platform/pyproject.toml`
- Test: `platform/tests/unit/vendors/test_alpha_vantage_broker.py`

- [ ] Write failing asynchronous tests for smooth dispatch, bounded in-flight work,
  research priority, global cooldown, one half-open probe, coalescing, and successful
  response caching.
- [ ] Run the broker tests and verify the missing implementation causes the failures.
- [ ] Implement the minimal coordinator, endpoint-aware cache, bounded central retries,
  safe FastAPI endpoints, and loopback uvicorn entry point.
- [ ] Run the broker tests and confirm all state-machine cases pass.

### Task 3: Research and validation integration

**Files:**
- Modify: `platform/src/tradingng_platform/runner/tradingagents.py`
- Modify: `platform/src/tradingng_platform/worker/service.py`
- Modify: `platform/src/tradingng_platform/worker/main.py`
- Modify: `platform/src/tradingng_platform/validation/providers.py`
- Test: `platform/tests/unit/runner/test_runner.py`
- Test: `platform/tests/unit/worker/test_service.py`
- Test: `platform/tests/unit/validation/test_providers.py`

- [ ] Write failing tests showing configured research and validation paths call the
  broker once and never invoke their direct Alpha HTTP fallback.
- [ ] Run the focused tests and confirm the broker route is not yet wired.
- [ ] Pass the broker URL through runner input, replace the vendored request dynamically,
  and build validation providers with the asynchronous broker client.
- [ ] Run the focused tests and confirm existing direct-mode compatibility remains green.

### Task 4: Typed failure and automatic assessment retry

**Files:**
- Modify: `platform/src/tradingng_platform/runner/cli.py`
- Modify: `platform/src/tradingng_platform/worker/repository.py`
- Modify: `platform/src/tradingng_platform/worker/service.py`
- Test: `platform/tests/unit/runner/test_cli.py`
- Test: `platform/tests/unit/worker/test_repository_steps.py`

- [ ] Write failing tests classifying broker/transient errors as `vendor_transient` and
  scheduling no more than two linked retries for vendor rate/transient codes only.
- [ ] Run the tests and confirm the current generic error/failure behavior fails them.
- [ ] Add explicit classification and atomic linked retry scheduling after failure.
- [ ] Run focused tests and confirm non-vendor failures remain terminal.

### Task 5: Scheduler admission and safe system status

**Files:**
- Modify: `platform/src/tradingng_platform/scheduler/service.py`
- Modify: `platform/src/tradingng_platform/scheduler/repository.py`
- Modify: `platform/src/tradingng_platform/scheduler/main.py`
- Modify: `platform/src/tradingng_platform/system/service.py`
- Modify: `platform/src/tradingng_platform/api/app.py`
- Test: `platform/tests/unit/scheduler/test_admission.py`
- Test: `platform/tests/integration/test_records_system.py`

- [ ] Write failing tests proving cooldown, half-open, unavailable, and excessive broker
  queue states block new Alpha admissions and that safe broker metrics appear in system
  status.
- [ ] Run the tests and confirm the scheduler currently ignores broker state.
- [ ] Inject the async broker client into scheduler and system services and add an
  external admission blocker without exposing infrastructure or credentials.
- [ ] Run focused tests and confirm non-Alpha configurations are unaffected.

### Task 6: Management UI

**Files:**
- Modify: `web/src/api/system.ts`
- Modify: `web/src/features/system/SystemPage.tsx`
- Modify: `web/src/features/system/SystemPage.test.tsx`
- Modify: `web/src/i18n/messages.ts`
- Modify: `web/src/styles/global.css`

- [ ] Add a failing Vitest case for normal and cooldown broker snapshots.
- [ ] Run the SystemPage test and confirm the Alpha quota panel is absent.
- [ ] Render safe quota, queue, cooldown, cache, and failure information with bilingual
  labels and compact responsive layout.
- [ ] Run SystemPage tests, typecheck, and lint.

### Task 7: Service management and documentation

**Files:**
- Create: `systemd/user/tradingng-platform-alpha-broker.service`
- Modify: `systemd/user/tradingng-platform-scheduler.service`
- Modify: `systemd/user/tradingng-platform-worker@.service`
- Modify: `systemd/user/tradingng-platform-validation.service`
- Modify: `.env.platform.example`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `scripts/restore_platform.sh`
- Modify: `scripts/verify_platform.sh`
- Modify: `platform/tests/operations/test_systemd.py`
- Modify: `platform/tests/operations/test_deploy_config.py`

- [ ] Write failing deployment tests requiring the loopback broker, dependency ordering,
  documented settings, restore handling, and system verification.
- [ ] Run operations tests and confirm the new service is missing.
- [ ] Add the systemd service and bilingual configuration/operations documentation.
- [ ] Run operations tests and `systemd-analyze --user verify`.

### Task 8: Full verification and non-disruptive deployment

**Files:**
- Modify generated asset: `web/dist/**`

- [ ] Run all platform unit and operations tests, frontend tests, typecheck, lint, and
  production build.
- [ ] Export OpenAPI and verify the generated TypeScript contract remains current.
- [ ] Start and health-check the broker before changing other services.
- [ ] Stop scheduler admission, inspect active runs, wait for existing assessments to
  finish, and restart scheduler/validation/workers without terminating a live runner.
- [ ] Verify API readiness, broker status, Gateway health, absence of Yahoo calls, and
  sanitized service logs.
- [ ] Submit a controlled Alpha-backed assessment and confirm its broker counters,
  artifacts, validations, and final status.
