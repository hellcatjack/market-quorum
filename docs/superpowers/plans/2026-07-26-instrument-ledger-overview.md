# Instrument Ledger Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the run-first dashboard with an instrument ledger that binds each latest successful forecast to actual validation outcomes while preserving a complete operational run view.

**Architecture:** Add one batched records projection over the existing instrument, request, run, decision, configuration, and validation tables; no new persistence and no changes under `TradingAgents/`. Publish the projection through REST, OpenAPI, TypeScript, and MCP, then render it as the default dashboard tab and reuse the structured validation data in a chronological instrument history timeline.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy async, MySQL, pytest, React 19, TypeScript, TanStack Query, Vitest, Testing Library, CSS, FastMCP.

---

## Working constraints

- Work directly on `main`; do not create a worktree.
- Execute in the current session; do not dispatch subagents.
- Do not modify any file below `TradingAgents/`.
- Build the projection from existing source-of-truth records and use a bounded number of batch queries; do not add a summary table in this phase.
- Keep the existing task table available so failed, queued, and active runs remain operationally visible.

## File map

- `platform/src/tradingng_platform/records/contracts.py`: shared instrument identity, validation, aggregate-statistics, overview-page, and enriched-history response models.
- `platform/src/tradingng_platform/records/service.py`: batched grouping, cursor pagination, latest-successful/latest-run separation, validation selection, reliability statistics, retry grouping, and memory metadata.
- `platform/src/tradingng_platform/api/routes/instruments.py`: REST query contract for `/instrument-overviews` plus the enriched history response.
- `platform/src/tradingng_platform/mcp/tools.py`: MCP read tool exposing the same overview projection.
- `platform/tests/unit/records/test_instrument_overviews.py`: pure projection and service edge cases.
- `platform/tests/unit/api/test_records.py`: route validation and response-shape tests.
- `platform/tests/integration/test_records_system.py`: real-database aggregation and history validation binding.
- `platform/tests/integration/test_mcp.py`: MCP contract smoke test.
- `var/openapi.json` and `web/src/api/schema.d.ts`: generated REST and TypeScript contracts.
- `web/src/api/records.ts`: typed overview query and filter serialization.
- `web/src/features/dashboard/instrumentPresentation.ts`: pure prediction/outcome and reliability labels.
- `web/src/features/dashboard/InstrumentLedgerTable.tsx`: dense responsive instrument ledger.
- `web/src/features/dashboard/DashboardPage.tsx`: default ledger/task tabs, independent cursors, global counts, and degradation behavior.
- `web/src/features/dashboard/DashboardPage.test.tsx`: dashboard behavior and accessibility tests.
- `web/src/features/instruments/instrumentHistory.ts`: chronological request grouping and retry selection.
- `web/src/features/instruments/InstrumentHistoryPage.tsx`: conclusion evolution timeline and 1/5/20-day matrix.
- `web/src/features/instruments/InstrumentHistoryPage.test.tsx`: ordering, grouping, validation, and link tests.
- `web/src/styles.css`: ledger, compact cards, timeline, validation matrix, and responsive styles.

### Task 1: Define and test the records projection contract

**Files:**
- Create: `platform/tests/unit/records/test_instrument_overviews.py`
- Modify: `platform/src/tradingng_platform/records/contracts.py`
- Modify: `platform/src/tradingng_platform/records/service.py`

- [ ] **Step 1: Write failing tests for validation preference and completed-only statistics**

```python
def test_preferred_validation_uses_completed_20_then_5_then_1():
    one = validation(horizon=1, status="completed")
    five = validation(horizon=5, status="completed")
    twenty_scheduled = validation(horizon=20, status="scheduled")
    assert _preferred_validation([one, five, twenty_scheduled]).horizon == 5


def test_validation_stats_exclude_non_completed_and_missing_direction():
    stats = _validation_stats([
        validation(horizon=20, status="completed", direction_correct=True),
        validation(horizon=20, status="completed", direction_correct=False),
        validation(horizon=20, status="failed", direction_correct=True),
    ])
    assert stats[20].completed == 2
    assert stats[20].direction_observed == 2
    assert stats[20].direction_correct == 1
    assert stats[20].accuracy == Decimal("0.5")
```

- [ ] **Step 2: Run the focused tests and confirm the missing helpers fail**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/records/test_instrument_overviews.py -q`

Expected: FAIL because `_preferred_validation`, `_validation_stats`, and the new response models do not exist.

- [ ] **Step 3: Add explicit shared response models and pure helpers**

```python
class InstrumentIdentityView(BaseModel):
    id: uuid.UUID
    ticker: str
    name: str | None
    exchange: str | None
    asset_type: str


class InstrumentValidationView(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    horizon: int
    status: str
    matures_at: datetime | None
    exit_session: date | None
    total_return: Decimal | None
    total_alpha: Decimal | None
    direction_correct: bool | None
    price_target_hit: bool | None
    error_code: str | None


class InstrumentValidationStats(BaseModel):
    horizon: int
    completed: int
    direction_observed: int
    direction_correct: int
    accuracy: Decimal | None


class InstrumentRunCounts(BaseModel):
    total: int
    queued: int
    active: int
    succeeded: int
    anomalous: int
```

Add `InstrumentOverviewItem` with `instrument`, `latest_run`, `latest_successful_run`, `latest_decision`, `previous_rating`, `preferred_validation`, `validation_stats`, and `run_counts`; add `InstrumentOverviewPage` with `items`, `next_cursor`, `instrument_count`, global `run_counts`, and `validations_visible`. Extend `InstrumentHistoryItem` with `validations`, `memory_mode`, `memory_source_count`, `is_latest_attempt`, and `request_attempt_count`, while retaining `validation_outcome` for compatibility.

Implement `_preferred_validation()` so completed horizons win in order 20, 5, 1; if none are completed, expose the longest pending validation. Implement `_validation_stats()` so only `completed` records increment mature samples and only boolean `direction_correct` values enter the accuracy denominator.

- [ ] **Step 4: Run projection helper tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/records/test_instrument_overviews.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the contract slice**

```bash
git add platform/src/tradingng_platform/records/contracts.py platform/src/tradingng_platform/records/service.py platform/tests/unit/records/test_instrument_overviews.py
git commit -m "feat: define instrument ledger projection"
```

### Task 2: Build the batched overview and enriched history service

**Files:**
- Modify: `platform/tests/unit/records/test_instrument_overviews.py`
- Modify: `platform/tests/integration/test_records_system.py`
- Modify: `platform/src/tradingng_platform/records/service.py`

- [ ] **Step 1: Write service tests for grouping, failure preservation, pagination, and retry metadata**

Create fixtures with two instruments and these records: an older successful NVDA decision, a newer failed NVDA retry, completed 1/5/20-day validations, one failed validation, and a single successful TSLA decision. Assert:

```python
page = await records.instrument_overviews(principal, InstrumentOverviewFilters(limit=1))
assert page.instrument_count == 2
assert page.items[0].instrument.ticker == "NVDA"
assert page.items[0].latest_run.status == "failed"
assert page.items[0].latest_successful_run.status == "succeeded"
assert page.items[0].latest_decision.rating == "Underweight"
assert page.items[0].preferred_validation.horizon == 20
assert page.items[0].run_counts.anomalous == 1
assert page.next_cursor is not None

history = await records.instrument_history(principal, "NVDA", 50)
assert history[0].request_attempt_count == 2
assert history[0].is_latest_attempt is True
assert {item.horizon for item in history[0].validations} == {1, 5, 20}
```

Also assert a status filter applies to the latest run without discarding the older successful decision, and a cursor returns the next instrument without duplication.

- [ ] **Step 2: Run the service tests and confirm the missing method/fields fail**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/records/test_instrument_overviews.py platform/tests/integration/test_records_system.py -q`

Expected: FAIL because `instrument_overviews()` and structured history hydration are absent.

- [ ] **Step 3: Implement a bounded-query aggregation**

Add `InstrumentOverviewFilters` with `ticker`, `asset_type`, `statuses`, `anomalous_only`, `created_from`, `created_to`, `cursor`, and `limit`. In `RecordService.instrument_overviews()`:

```python
rows = (await session.execute(
    select(AssessmentRun, AssessmentRequest, Instrument, Decision, RunConfigSnapshot)
    .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
    .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
    .outerjoin(Decision, Decision.run_id == AssessmentRun.id)
    .outerjoin(RunConfigSnapshot, RunConfigSnapshot.id == AssessmentRun.config_snapshot_id)
    .order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc())
)).all()
```

Group rows by `Instrument.id`; choose the first row as latest run, choose the first succeeded row with a decision as latest successful, and choose the next succeeded decision rating as `previous_rating`. Apply ticker/name, latest status, anomaly, and latest-created filters to groups; sort by `(latest_run.created_at, instrument.id)` descending; decode the cursor; and take `limit + 1` groups.

When `validations:read` is present, load all validations for selected groups in one `WHERE Validation.run_id.in_(...)` query, map v2 `total_return`/`total_alpha` with legacy fallbacks, derive direction/target booleans from `trigger_results_json`, and build each overview. Without that scope, do not query or serialize validation values and set `validations_visible=false`, allowing the UI to explain the permission-limited view. Compute page-level `instrument_count` and `run_counts` before cursor slicing so dashboard counters reflect all filtered groups.

For history, retain the batched run query and add one validation query. Calculate the maximum attempt per request, parse memory mode from requested configuration/snapshot, count serialized memory source entries, attach each run's validations, and derive the compatibility `validation_outcome` from the preferred validation.

- [ ] **Step 4: Run focused backend tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/records/test_instrument_overviews.py platform/tests/integration/test_records_system.py -q`

Expected: PASS with no per-instrument query loop.

- [ ] **Step 5: Commit the service slice**

```bash
git add platform/src/tradingng_platform/records/service.py platform/tests/unit/records/test_instrument_overviews.py platform/tests/integration/test_records_system.py
git commit -m "feat: aggregate instrument forecasts and outcomes"
```

### Task 3: Publish REST, OpenAPI, TypeScript, and MCP contracts

**Files:**
- Modify: `platform/src/tradingng_platform/api/routes/instruments.py`
- Modify: `platform/src/tradingng_platform/mcp/tools.py`
- Modify: `platform/tests/unit/api/test_records.py`
- Modify: `platform/tests/integration/test_mcp.py`
- Modify: `var/openapi.json`
- Modify: `web/src/api/schema.d.ts`
- Modify: `web/src/api/records.ts`

- [ ] **Step 1: Write failing REST and MCP tests**

```python
response = client.get("/api/v1/instrument-overviews?status=failed&limit=25")
assert response.status_code == 200
assert response.json()["items"][0]["latest_run"]["status"] == "failed"

result = await mcp.call_tool("list_instrument_overviews", {"ticker": "NVDA"})
assert result["items"][0]["instrument"]["ticker"] == "NVDA"
```

Assert invalid limits return 422, the REST operation ID is stable, and the enriched history schema includes `validations`.

- [ ] **Step 2: Run route and MCP tests and confirm 404/tool-not-found failures**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/api/test_records.py platform/tests/integration/test_mcp.py -q`

Expected: FAIL because the route and MCP tool are not registered.

- [ ] **Step 3: Add the REST endpoint and MCP wrapper**

Define `GET /instrument-overviews` before `GET /instruments/{ticker}` so route matching remains unambiguous. Map query parameters into `InstrumentOverviewFilters` and return `InstrumentOverviewPage`. Add an MCP tool with the same ticker, asset type, status, anomaly, cursor, and limit semantics and serialize the Pydantic page with `model_dump(mode="json")`.

- [ ] **Step 4: Regenerate and consume the API types**

Run:

```bash
.venv/bin/python scripts/export_openapi.py
cd web && npm run api:generate
```

Add to `web/src/api/records.ts`:

```typescript
export type InstrumentOverviewPage = components["schemas"]["InstrumentOverviewPage"];

export interface InstrumentOverviewFilters {
  ticker?: string;
  status?: string;
  anomalousOnly?: boolean;
  createdFrom?: string;
  createdTo?: string;
  cursor?: string;
  limit?: number;
}

export const listInstrumentOverviews = (filters: InstrumentOverviewFilters) =>
  apiRequest<InstrumentOverviewPage>(`/api/v1/instrument-overviews?${params(filters)}`);
```

Use `URLSearchParams`; append repeated status values only when provided and omit empty values.

- [ ] **Step 5: Run REST/MCP tests and TypeScript checking**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/api/test_records.py platform/tests/integration/test_mcp.py -q && npm --prefix web run typecheck`

Expected: all tests pass and TypeScript exits 0.

- [ ] **Step 6: Commit the public contract slice**

```bash
git add platform/src/tradingng_platform/api/routes/instruments.py platform/src/tradingng_platform/mcp/tools.py platform/tests/unit/api/test_records.py platform/tests/integration/test_mcp.py var/openapi.json web/src/api/schema.d.ts web/src/api/records.ts
git commit -m "feat: expose instrument ledger api and mcp tool"
```

### Task 4: Implement and test dashboard presentation helpers

**Files:**
- Create: `web/src/features/dashboard/instrumentPresentation.ts`
- Create: `web/src/features/dashboard/instrumentPresentation.test.ts`

- [ ] **Step 1: Write failing formatting tests**

```typescript
expect(formatPredictionOutcome(overview)).toBe(
  "Underweight ↓ → 20D -20.65% / Alpha -14.59% → 方向正确",
);
expect(reliabilityLabel({ completed: 2, direction_observed: 2, direction_correct: 2, accuracy: "1" }))
  .toBe("2 次 · 样本不足");
expect(reliabilityLabel({ completed: 4, direction_observed: 4, direction_correct: 3, accuracy: "0.75" }))
  .toBe("3/4 · 75.0%");
```

Also cover no decision, pending validation, failed validation, unknown rating direction, missing alpha, and target-hit labels.

- [ ] **Step 2: Run the helper test and confirm module-not-found failure**

Run: `cd web && npm test -- --run src/features/dashboard/instrumentPresentation.test.ts`

Expected: FAIL because `instrumentPresentation.ts` is absent.

- [ ] **Step 3: Implement deterministic labels**

Export `ratingDirection()`, `formatPercent()`, `formatPredictionOutcome()`, `reliabilityLabel()`, and `ratingTransition()`. Treat positive/overweight/buy ratings as `↑`, negative/underweight/sell ratings as `↓`, neutral/hold ratings as `→`, and all other values as `·`. Convert decimal returns to percentages, preserve an explicit horizon label, and never report a failed/unavailable validation as an incorrect prediction.

- [ ] **Step 4: Run helper tests**

Run: `cd web && npm test -- --run src/features/dashboard/instrumentPresentation.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit presentation helpers**

```bash
git add web/src/features/dashboard/instrumentPresentation.ts web/src/features/dashboard/instrumentPresentation.test.ts
git commit -m "feat: format forecast outcome summaries"
```

### Task 5: Make the instrument ledger the default dashboard view

**Files:**
- Create: `web/src/features/dashboard/InstrumentLedgerTable.tsx`
- Modify: `web/src/features/dashboard/DashboardPage.tsx`
- Modify: `web/src/features/dashboard/DashboardPage.test.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Replace dashboard tests with the approved two-view behavior**

Mock `listInstrumentOverviews` and assert that initial render shows one NVDA row despite multiple run counts, includes its Chinese name, paired prediction/outcome, reliability sample warning, rating transition, latest failed alert, and links to both instrument and run detail. Click the `任务记录` tab and assert the existing `RunTable` appears. Verify tab roles, selected state, previous/next instrument pagination, task pagination independence, loading/error messages, and a ledger API error that does not prevent switching to task records.

- [ ] **Step 2: Run the dashboard test and confirm it fails against the run-first page**

Run: `cd web && npm test -- --run src/features/dashboard/DashboardPage.test.tsx`

Expected: FAIL because `标的台账` and `InstrumentLedgerTable` do not exist.

- [ ] **Step 3: Build the dense ledger table**

Render columns for identity, latest valid conclusion, forecast-to-outcome, reliability, rating change, and run counts. The identity links to `/instruments/{ticker}`; decision and anomaly links use `/runs/{id}`. Preserve latest successful content when `latest_run.status` is failed or needs attention and give the latter a separate text/icon alert. Use table headers on wide screens and CSS-labeled cells on narrow screens.

- [ ] **Step 4: Add default ledger/task tabs and independent state**

Keep `ledgerCursor`/`ledgerCursorHistory` separate from `runCursor`/`runCursorHistory`; reset both when shared filters change. Use overview `run_counts` for top summary counters in ledger view. Continue polling when any overview has active or queued work. Keep capacity and circuit warnings above the tabs. Search text is passed to the backend so both ticker and localized instrument names match. When `validations_visible=false`, replace performance cells with `缺少表现验证读取权限` while retaining conclusions and run status.

- [ ] **Step 5: Add responsive and accessible styling**

Add visible focus states, `role="tablist"`, `role="tab"`, `aria-selected`, semantic table headers, non-color direction labels, compact count pills, and a media query that converts rows into bordered groups below 820px without hiding any field.

- [ ] **Step 6: Run dashboard tests, lint, and type checking**

Run: `cd web && npm test -- --run src/features/dashboard/DashboardPage.test.tsx && npm run lint && npm run typecheck`

Expected: PASS and both static checks exit 0.

- [ ] **Step 7: Commit the dashboard slice**

```bash
git add web/src/features/dashboard/InstrumentLedgerTable.tsx web/src/features/dashboard/DashboardPage.tsx web/src/features/dashboard/DashboardPage.test.tsx web/src/styles.css
git commit -m "feat: make instrument ledger the default overview"
```

### Task 6: Upgrade instrument history to a chronological validation timeline

**Files:**
- Create: `web/src/features/instruments/instrumentHistory.ts`
- Create: `web/src/features/instruments/instrumentHistory.test.ts`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.tsx`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.test.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write failing grouping and page tests**

For pure grouping, pass descending runs with two attempts sharing one request and assert groups are returned by analysis date/creation time ascending, the highest attempt is primary, and older attempts remain expandable. For the page, assert each successful event shows 1D/5D/20D cells, completed returns and alpha, pending maturity, memory mode/source count, rating transition, and a run-detail link. Assert technical attempts render inside a collapsed `<details>` element and do not create extra conclusion nodes.

- [ ] **Step 2: Run history tests and confirm the table-based page fails**

Run: `cd web && npm test -- --run src/features/instruments/instrumentHistory.test.ts src/features/instruments/InstrumentHistoryPage.test.tsx`

Expected: FAIL because request grouping and the semantic timeline are absent.

- [ ] **Step 3: Implement chronological request grouping**

```typescript
export function groupInstrumentHistory(items: InstrumentHistoryItem[]): HistoryGroup[] {
  const requests = Map.groupBy(items, (item) => item.run.request_id);
  return [...requests.values()]
    .map((attempts) => ({
      primary: attempts.toSorted((a, b) => b.run.attempt - a.run.attempt)[0],
      priorAttempts: attempts.toSorted((a, b) => b.run.attempt - a.run.attempt).slice(1),
    }))
    .toSorted(compareByAnalysisDateThenCreatedAt);
}
```

If the configured TypeScript target does not provide `Map.groupBy`/`toSorted`, implement the same immutable behavior with a local `Map` and copied arrays.

- [ ] **Step 4: Replace the history table with a semantic timeline**

Use an ordered list whose nodes contain analysis date, created time, status, rating/transition, summary, memory mode, source count, and a fixed 1/5/20 validation grid. Completed cells show return, alpha, direction, and target status; scheduled cells show maturity; failed/unavailable cells show an error label and are not colored as prediction misses. Put prior attempts in a collapsed details block with audit links. Keep model and config hash in compact metadata.

- [ ] **Step 5: Run history tests and static checks**

Run: `cd web && npm test -- --run src/features/instruments/instrumentHistory.test.ts src/features/instruments/InstrumentHistoryPage.test.tsx && npm run lint && npm run typecheck`

Expected: PASS and static checks exit 0.

- [ ] **Step 6: Commit the history slice**

```bash
git add web/src/features/instruments/instrumentHistory.ts web/src/features/instruments/instrumentHistory.test.ts web/src/features/instruments/InstrumentHistoryPage.tsx web/src/features/instruments/InstrumentHistoryPage.test.tsx web/src/styles.css
git commit -m "feat: show instrument conclusion validation timeline"
```

### Task 7: Verify, deploy, and inspect production behavior

**Files:**
- Modify only if checks reveal a defect: files already listed in Tasks 1–6.

- [ ] **Step 1: Run the complete backend verification suite**

Run: `PYTHONPATH=platform/src .venv/bin/ruff check platform/src platform/tests && PYTHONPATH=platform/src .venv/bin/ruff format --check platform/src platform/tests && PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit platform/tests/integration platform/tests/operations -q`

Expected: all commands exit 0.

- [ ] **Step 2: Run the complete frontend verification suite**

Run: `cd web && npm test -- --run && npm run lint && npm run typecheck && npm run build`

Expected: all tests pass and the production bundle builds successfully.

- [ ] **Step 3: Verify generated contracts and repository hygiene**

Run: `git diff --check && git status --short && git diff --name-only origin/main...HEAD | rg '^TradingAgents/'`

Expected: no whitespace errors, only intended files are changed, and the final command has no output.

- [ ] **Step 4: Deploy through the repository's existing service workflow**

Inspect the committed deployment documentation and active units, rebuild only the platform/web services used by this repository, then verify `https://ushome.amycat.com/api/v1/health` and the authenticated dashboard. Do not restart or reconfigure the standalone Codex gateway unless its own health check is degraded.

- [ ] **Step 5: Perform production data acceptance checks**

Confirm that NVDA appears once in the ledger despite its repeated assessments; latest failed/attention state, if present, does not replace its latest successful rating; completed 20-day validation binds to the correct run; a no-validation instrument says `待验证`; the task tab still exposes every run; and `/instruments/NVDA` is chronologically ordered with retries collapsed.

- [ ] **Step 6: Commit any verification fixes and push main**

```bash
git add platform web var/openapi.json docs/superpowers
git commit -m "fix: harden instrument ledger presentation"
git push origin main
```

Skip the fix commit when the verification diff is empty; push all already-created main-branch commits after every acceptance check passes.
