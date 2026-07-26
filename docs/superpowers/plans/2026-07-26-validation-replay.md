# Validation Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an immutable, interactive 1/5/20-session outcome replay to assessment details for internal audit.

**Architecture:** Extend the existing validation list contract with typed trigger facts and the bound artifact identifier, then load the already hash-verified JSON artifact through the existing artifact endpoint. Convert stored prices into adjusted candles and normalized benchmark lines with pure frontend functions; render them with Lightweight Charts while keeping saved metrics authoritative.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy, pytest, React 19, TypeScript 5.9, TanStack Query, Vitest, Lightweight Charts 5

---

### Task 1: Expose validation audit metadata

**Files:**
- Modify: `platform/src/tradingng_platform/validation/contracts.py`
- Modify: `platform/src/tradingng_platform/validation/repository.py`
- Modify: `platform/src/tradingng_platform/validation/worker.py`
- Test: `platform/tests/unit/validation/test_schedule.py`
- Test: `platform/tests/integration/test_validation.py`

- [ ] **Step 1: Write failing contract and integration assertions**

Add assertions that completed validation views contain a typed `trigger_results`, exact `data_artifact_id`, `error_code`, and `calculation_version == "validation.v1"`. Assert the stored validation artifact uses permanent retention.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_schedule.py platform/tests/integration/test_validation.py -q
```

Expected: failures because the response fields and permanent retention do not exist.

- [ ] **Step 3: Implement typed audit metadata**

Add `ValidationTriggerResults` with optional fields for scheduled rows. Map `trigger_results_json` to the public `trigger_results` field, expose the artifact and error identifiers, return the fixed calculation version, and store new validation price artifacts as `permanent`.

- [ ] **Step 4: Run tests to verify GREEN**

Run the same pytest command and expect all selected tests to pass.

- [ ] **Step 5: Regenerate the public contract before frontend work**

Run:

```bash
PYTHONPATH=platform/src .venv/bin/python scripts/export_openapi.py
npm --prefix web run api:generate
```

Confirm `ValidationView` contains the new typed trigger, artifact, error and version fields.

### Task 2: Define and verify the chart data transformation

**Files:**
- Create: `web/src/features/runs/validationReplay.ts`
- Create: `web/src/features/runs/validationReplay.test.ts`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

- [ ] **Step 1: Install the approved chart dependency**

Run:

```bash
npm --prefix web install lightweight-charts@^5.2.0
```

- [ ] **Step 2: Write failing pure-function tests**

Cover artifact shape validation, adjusted OHLC conversion, entry-to-exit window selection, 100-based instrument/benchmark normalization, longest-completed default selection, exact horizon labels, and exchange-aware TradingView symbol URLs.

- [ ] **Step 3: Run the focused test to verify RED**

Run:

```bash
npm --prefix web run test -- --run src/features/runs/validationReplay.test.ts
```

Expected: failure because `validationReplay.ts` does not exist.

- [ ] **Step 4: Implement the minimal pure functions**

Define strict runtime parsing for equal-length monotonic price arrays, generate adjusted candles, align benchmark sessions by date, expose audit/source facts, and URL-encode `EXCHANGE:TICKER` for TradingView.

- [ ] **Step 5: Run the focused test to verify GREEN**

Run the same Vitest command and expect all focused tests to pass.

### Task 3: Render the interactive replay panel

**Files:**
- Create: `web/src/features/runs/ValidationReplayPanel.tsx`
- Create: `web/src/features/runs/ValidationChart.tsx`
- Create: `web/src/features/runs/ValidationReplayPanel.test.tsx`
- Modify: `web/src/api/records.ts`

- [ ] **Step 1: Write failing component tests**

Cover completed summary metrics, longest-horizon default, horizon switching, immutable 21-node explanation, collapsed audit details, artifact/source/hash facts, pending and failed states, missing-artifact permission state, and external research/copy controls. Mock only the Canvas chart adapter.

- [ ] **Step 2: Run the focused component test to verify RED**

Run:

```bash
npm --prefix web run test -- --run src/features/runs/ValidationReplayPanel.test.tsx
```

Expected: failure because the panel does not exist.

- [ ] **Step 3: Implement chart lifecycle and replay UI**

Use two synchronized Lightweight Charts instances: adjusted candlesticks with price/target markers and normalized instrument/benchmark lines. Add accessible textual metrics and all non-chart information so the audit remains usable when Canvas is unavailable.

- [ ] **Step 4: Run the focused component test to verify GREEN**

Run the same Vitest command and expect all focused tests to pass.

### Task 4: Integrate replay into assessment details

**Files:**
- Modify: `web/src/features/runs/RunDetailPage.tsx`
- Modify: `web/src/features/runs/RunDetailPage.test.tsx`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Extend the page test and verify RED**

Mock `/validations` and the bound artifact response, then assert the old placeholder is gone, the replay panel follows the research timeline as a wide section, and a completed 20-day summary is visible.

- [ ] **Step 2: Implement page queries and layout**

Fetch validations for users with `validations:read`, load only the selected completed validation artifact when `artifacts:read` is present, invalidate validations and artifacts on `validation.*` events, and render the wide replay panel.

- [ ] **Step 3: Add responsive audit-focused styling**

Style summary cards, horizon tabs, chart panes, legend, disclosure, warning states and research actions for desktop and narrow screens without truncating important date or metric content.

- [ ] **Step 4: Run detail and replay tests to verify GREEN**

Run:

```bash
npm --prefix web run test -- --run src/features/runs/RunDetailPage.test.tsx src/features/runs/ValidationReplayPanel.test.tsx
```

Expected: both test files pass.

### Task 5: Verify the product

**Files:**
- Verify: `var/openapi.json`
- Verify: `web/src/api/schema.d.ts`

- [ ] **Step 1: Confirm generated contracts are current**

Run:

```bash
PYTHONPATH=platform/src .venv/bin/python scripts/export_openapi.py
npm --prefix web run api:generate
```

Confirm this produces no additional diff beyond the intended schema changes.

- [ ] **Step 2: Run backend verification**

Run:

```bash
PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit platform/tests/integration/test_validation.py -q
.venv/bin/ruff check platform/src platform/tests
```

- [ ] **Step 3: Run frontend verification**

Run:

```bash
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test -- --run
npm --prefix web run build
```

- [ ] **Step 4: Inspect the live page and system health**

Restart only the affected platform/web services using the repository deployment procedure, verify service health, sign in through the configured HTTPS origin, and inspect a completed NVDA 20-session replay. Confirm charts, metrics, disclosures, external link and narrow layout.

- [ ] **Step 5: Review, commit, and push main**

Inspect `git diff`, confirm no secrets are present, commit the implementation and documentation with the configured contributor identity, then push `main` to `origin`.
