# Model Observability and UI Internationalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accurately distinguish Gateway compatibility defaults, configured assessment routes, and actual per-call model usage while providing persistent Chinese and English UI languages.

**Architecture:** Extend the platform API with typed model-route projections and a privacy-safe LLM interaction feed derived from live or sealed audit JSONL. Add a dependency-free React i18n provider with browser detection and local preference persistence, then migrate all Web UI chrome and shared domain labels while leaving business content and raw audit data unchanged.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, SQLAlchemy, pytest, React 19, TypeScript, TanStack Query, Vitest, Testing Library, CSS

---

### Task 1: Expose authoritative assessment routes

**Files:**
- Modify: `platform/tests/integration/test_records_system.py`
- Modify: `platform/src/tradingng_platform/system/contracts.py`
- Modify: `platform/src/tradingng_platform/system/service.py`
- Modify: `platform/src/tradingng_platform/records/contracts.py`
- Modify: `platform/src/tradingng_platform/records/service.py`

- [ ] **Step 1: Write failing route projection assertions**

Extend the existing integration fixtures with frozen gateway routes and assert both current and historical projections:

```python
snapshot = RunConfigSnapshot(
    content_json={
        "gateway": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "routes": {
                "fast": {"model": "gpt-5.6-terra", "reasoning_effort": "high"},
                "slow": {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
            },
        },
    },
    sha256="c" * 64,
    gateway_snapshot_id="snapshot-records",
)

assert history[0].gateway_fast_model == "gpt-5.6-terra"
assert history[0].gateway_fast_reasoning_effort == "high"
assert history[0].gateway_slow_model == "gpt-5.6-sol"
assert history[0].gateway_slow_reasoning_effort == "xhigh"

capacity = await service.capacity(principal)
assert capacity.model_routing.fast.model == "gpt-5.6-terra"
assert capacity.model_routing.slow.model == "gpt-5.6-sol"
```

- [ ] **Step 2: Run the focused integration tests and verify RED**

Run: `.venv/bin/pytest platform/tests/integration/test_records_system.py -q`

Expected: FAIL because `CapacityView.model_routing` and historical fast/slow fields do not exist.

- [ ] **Step 3: Add typed route projections**

Add the current policy to `CapacityView` and add nullable frozen fields to `InstrumentHistoryItem`:

```python
class CapacityView(BaseModel):
    admitted_or_running: int
    max_running_total: int
    hard_max_running_total: int
    queued: int
    oldest_queued_seconds: int | None
    gateway_active_completions: int
    gateway_model: str
    gateway_reasoning_effort: str
    model_routing: ModelRoutingPolicy
    open_circuits: list[str]
    admission_allowed: bool
    admission_reasons: list[str]

class InstrumentHistoryItem(BaseModel):
    gateway_fast_model: str | None = None
    gateway_fast_reasoning_effort: str | None = None
    gateway_slow_model: str | None = None
    gateway_slow_reasoning_effort: str | None = None
```

Read `ModelRoutingPolicyRepository(session).get()` in `SystemService.capacity()`. In `RecordService.instrument_history()`, extract `gateway.routes.fast` and `gateway.routes.slow` without falling back to the compatibility default.

- [ ] **Step 4: Run the focused integration tests and verify GREEN**

Run: `.venv/bin/pytest platform/tests/integration/test_records_system.py -q`

Expected: all tests in the file pass.

- [ ] **Step 5: Commit the route projection increment**

```bash
git add platform/tests/integration/test_records_system.py \
  platform/src/tradingng_platform/system/contracts.py \
  platform/src/tradingng_platform/system/service.py \
  platform/src/tradingng_platform/records/contracts.py \
  platform/src/tradingng_platform/records/service.py
git commit -m "feat: expose authoritative assessment model routes"
```

### Task 2: Add a privacy-safe LLM interaction feed

**Files:**
- Create: `platform/src/tradingng_platform/records/llm_interactions.py`
- Create: `platform/tests/unit/records/test_llm_interactions.py`
- Modify: `platform/src/tradingng_platform/records/contracts.py`
- Modify: `platform/src/tradingng_platform/records/service.py`
- Modify: `platform/src/tradingng_platform/api/routes/artifacts.py`
- Modify: `platform/src/tradingng_platform/api/app.py`
- Modify: `platform/src/tradingng_platform/mcp/services.py`
- Modify: `platform/tests/unit/api/test_records.py`
- Modify: `platform/tests/integration/test_records_system.py`

- [ ] **Step 1: Write failing parser and service tests**

Create parser tests with a complete record containing sensitive fields:

```python
record = {
    "route": "fast",
    "model_alias": "codex-fast",
    "physical_model": "gpt-5.6-terra",
    "reasoning_effort": "high",
    "status": "completed",
    "started_at": "2026-07-27T17:26:20+00:00",
    "completed_at": "2026-07-27T17:26:24+00:00",
    "duration_ms": 4426,
    "messages": [{"content": "private prompt"}],
    "response": [{"content": "private response"}],
    "token_usage": {"total_tokens": 123},
}
page = parse_llm_interactions((json.dumps(record) + "\n").encode(), source="live")
assert page.source == "live"
assert page.complete is False
assert page.items[0].physical_model == "gpt-5.6-terra"
assert "messages" not in page.items[0].model_dump()
assert "response" not in page.items[0].model_dump()
assert "token_usage" not in page.items[0].model_dump()
```

Add tests that a malformed last live line is ignored, a malformed sealed line raises `LlmAuditFormatError`, more than 2,000 records or more than 32 MiB is rejected, a sealed artifact is hash-verified, and the controlled live path is used before finalization only when no sealed artifact exists.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/pytest platform/tests/unit/records/test_llm_interactions.py \
  platform/tests/integration/test_records_system.py -q
```

Expected: collection or import failure because the parser and contracts do not exist.

- [ ] **Step 3: Implement safe contracts and parser**

Define these Pydantic contracts:

```python
class LlmInteractionView(BaseModel):
    sequence: int = Field(ge=1)
    route: str | None
    model_alias: str | None
    physical_model: str | None
    reasoning_effort: str | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None

class LlmInteractionPage(BaseModel):
    items: list[LlmInteractionView]
    source: Literal["live", "sealed", "none"]
    complete: bool
```

Implement `parse_llm_interactions(content: bytes, source: Literal["live", "sealed"])`. Enforce `MAX_BYTES = 32 * 1024 * 1024` and `MAX_RECORDS = 2_000`, parse one JSON object per line, validate only the allowlisted fields, and never retain the decoded original object after projection.

- [ ] **Step 4: Implement service selection and REST route**

Extend `RecordService` with an optional `job_dir: Path | None`, then implement:

```python
async def llm_interactions(
    self,
    principal: Principal,
    run_id: uuid.UUID,
) -> LlmInteractionPage:
    principal.require("assessments:read")
    async with self.sessions() as session:
        await self._ensure_run(session, run_id)
        artifact = await session.scalar(
            select(Artifact).where(
                Artifact.run_id == run_id,
                Artifact.kind == "llm_interactions",
                Artifact.deleted_at.is_(None),
            )
        )
        if artifact is not None:
            path = self.artifact_store.resolve(artifact.storage_key)
            if not self.artifact_store.verify(artifact.storage_key, artifact.sha256):
                raise ArtifactIntegrityError("artifact content hash does not match")
            return parse_llm_interactions(path.read_bytes(), source="sealed")
    if self.job_dir is not None:
        path = self.job_dir / str(run_id) / "working" / "llm_interactions.jsonl"
        if path.is_file():
            return parse_llm_interactions(path.read_bytes(), source="live")
    return LlmInteractionPage(items=[], source="none", complete=False)
```

Add `GET /assessments/{run_id}/llm-interactions` with `response_model=LlmInteractionPage`. Map `LlmAuditFormatError` to HTTP 409 with code `llm_audit_invalid`. Pass `settings.job_dir` from API and MCP service construction.

- [ ] **Step 5: Add the API serialization test**

Extend the `_Records` fake with `llm_interactions()` returning one safe item. Assert the REST payload contains `physical_model`, does not contain `messages`, `response`, `token_usage`, a local path, or prompt text.

- [ ] **Step 6: Run parser, service and API tests**

Run:

```bash
.venv/bin/pytest platform/tests/unit/records/test_llm_interactions.py \
  platform/tests/integration/test_records_system.py \
  platform/tests/unit/api/test_records.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the safe feed**

```bash
git add platform/src/tradingng_platform/records platform/src/tradingng_platform/api \
  platform/src/tradingng_platform/mcp/services.py \
  platform/tests/unit/records/test_llm_interactions.py \
  platform/tests/unit/api/test_records.py \
  platform/tests/integration/test_records_system.py
git commit -m "feat: expose safe model interaction metadata"
```

### Task 3: Build the UI language foundation

**Files:**
- Create: `web/src/i18n/messages.ts`
- Create: `web/src/i18n/I18nProvider.tsx`
- Create: `web/src/i18n/I18nProvider.test.tsx`
- Create: `web/src/i18n/domainLabels.ts`
- Create: `web/src/i18n/domainLabels.test.ts`
- Modify: `web/src/main.tsx`
- Modify: `web/src/app/Layout.tsx`
- Modify: `web/src/app/App.test.tsx`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write failing locale-resolution and persistence tests**

Cover all precedence rules:

```tsx
expect(resolveLocale(null, ["zh-CN", "en-US"])).toBe("zh-CN");
expect(resolveLocale(null, ["en-GB", "zh-CN"])).toBe("en-US");
expect(resolveLocale(null, ["fr-FR"])).toBe("en-US");
expect(resolveLocale("zh-CN", ["en-US"])).toBe("zh-CN");
expect(resolveLocale("invalid", ["zh-CN"])).toBe("zh-CN");
```

Render the provider with an English browser, switch to Chinese, and assert `localStorage["tradingng.ui.locale"]`, `document.documentElement.lang`, and translated children update immediately.

- [ ] **Step 2: Run i18n tests and verify RED**

Run: `npm test -- --run src/i18n/I18nProvider.test.tsx src/i18n/domainLabels.test.ts`

Expected: FAIL because the i18n modules do not exist.

- [ ] **Step 3: Implement the typed source-key dictionary**

Use Chinese source phrases as typed message keys and a complete English map:

```ts
export const EN_US = {
  "总览": "Overview",
  "新建评估": "New assessment",
  "系统状态": "System status",
  "界面语言": "Interface language",
  "快速分析": "Fast analysis",
  "关键裁决": "Decision",
  "方向正确": "Direction correct",
  "方向错误": "Direction incorrect",
  "未知状态": "Unknown status",
} as const;

export type MessageKey = keyof typeof EN_US;
export type Locale = "zh-CN" | "en-US";
```

Every production call uses `t(messageKey, variables)`, so TypeScript rejects a missing English entry. Parameter interpolation replaces `{name}` without evaluating HTML.

- [ ] **Step 4: Implement provider, formatters and language switcher**

Expose this context:

```ts
interface I18nValue {
  locale: Locale;
  setLocale(locale: Locale): void;
  t(key: MessageKey, variables?: Record<string, string | number>): string;
  formatDateTime(value: string | Date): string;
  formatPercent(value: string | number, digits?: number): string;
  formatDuration(milliseconds: number | null): string;
}
```

Wrap `<App />` in `I18nProvider` from `main.tsx`. Add a topbar `<select aria-label={t("界面语言")}>` with `zh-CN` and `en-US`. Keep the report-output language field independent.

- [ ] **Step 5: Implement shared domain labels**

Create functions accepting `t` for run statuses, step statuses, phases, event types, routes, reasoning efforts and asset types. Unknown identifiers return `t("未知状态")` for the primary label and are preserved separately by the caller.

- [ ] **Step 6: Run i18n tests and verify GREEN**

Run: `npm test -- --run src/i18n/I18nProvider.test.tsx src/i18n/domainLabels.test.ts src/app/App.test.tsx`

Expected: all selected tests pass in both locales.

- [ ] **Step 7: Commit the language foundation**

```bash
git add web/src/i18n web/src/main.tsx web/src/app/Layout.tsx \
  web/src/app/App.test.tsx web/src/styles/global.css
git commit -m "feat: add persistent Chinese and English UI locales"
```

### Task 4: Correct model summaries and add per-call timeline entries

**Files:**
- Modify: `web/src/api/records.ts`
- Modify: `web/src/features/system/CapacityBanner.tsx`
- Modify: `web/src/features/system/SystemPage.tsx`
- Modify: `web/src/features/dashboard/DashboardPage.test.tsx`
- Modify: `web/src/features/system/SystemPage.test.tsx`
- Modify: `web/src/features/runs/RunDetailPage.tsx`
- Modify: `web/src/features/runs/RunDetailPage.test.tsx`
- Modify: `web/src/features/runs/RunTimeline.tsx`
- Modify: `web/src/features/runs/RunTimeline.test.tsx`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.tsx`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.test.tsx`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write failing model-display tests**

Update fixtures with `model_routing` and `LlmInteractionPage`. Assert:

```tsx
expect(screen.getByText(/快速分析.*gpt-5.6-terra.*high/)).toBeInTheDocument();
expect(screen.getByText(/关键裁决.*gpt-5.6-sol.*xhigh/)).toBeInTheDocument();
expect(screen.queryByText("Gateway 模型")).not.toBeInTheDocument();
expect(screen.getByTestId("model-call-1")).toHaveTextContent("gpt-5.6-terra");
expect(screen.getByTestId("model-call-1")).toHaveTextContent("快速分析");
expect(screen.getByTestId("model-call-2")).toHaveTextContent("关键裁决");
```

System tests assert the compatibility model is only inside a closed `<details>` named “兼容调用默认值”. Timeline tests assert English event codes are displayed with localized primary labels and retained as secondary `<code>` text.

- [ ] **Step 2: Run focused Web tests and verify RED**

Run:

```bash
npm test -- --run src/features/system/SystemPage.test.tsx \
  src/features/dashboard/DashboardPage.test.tsx \
  src/features/runs/RunDetailPage.test.tsx \
  src/features/runs/RunTimeline.test.tsx \
  src/features/instruments/InstrumentHistoryPage.test.tsx
```

Expected: FAIL because route summaries and model-call timeline entries are not rendered.

- [ ] **Step 3: Add the Web API client and live refresh**

Add generated `LlmInteractionPage` and `LlmInteractionView` aliases plus:

```ts
export const getLlmInteractions = (runId: string) =>
  apiRequest<LlmInteractionPage>(
    `/api/v1/assessments/${encodeURIComponent(runId)}/llm-interactions`,
  );
```

Query it in `RunDetailPage`; use a five-second interval for non-terminal runs and stable caching for terminal runs. Pass `items` and feed state to `RunTimeline`.

- [ ] **Step 4: Replace ambiguous model summaries**

Capacity displays `capacity.model_routing.fast` and `.slow`. System Gateway health removes prominent `model` and `reasoning_effort`, preserving them only under compatibility details. Run detail and instrument history use frozen fast and slow fields; missing fields show the localized legacy-record message.

- [ ] **Step 5: Add model-call timeline normalization**

Add a `model` variant to `TimelineItem`, timestamp it from `started_at`, and sort it with steps, business events and evidence. Render route label, actual model, reasoning effort, localized status, start/end time, duration and inferred phase. A closed audit disclosure shows `model_alias` and raw status only.

- [ ] **Step 6: Run focused Web tests and verify GREEN**

Run the command from Step 2 again.

Expected: all selected files pass.

- [ ] **Step 7: Commit the model UI increment**

```bash
git add web/src/api/records.ts web/src/features/system web/src/features/dashboard \
  web/src/features/runs web/src/features/instruments web/src/styles/global.css
git commit -m "feat: show authoritative model usage in assessment UI"
```

### Task 5: Migrate all visible UI chrome to Chinese and English

**Files:**
- Modify: `web/src/app/App.tsx`
- Modify: `web/src/features/assessments/AssessmentForm.tsx`
- Modify: `web/src/features/assessments/NewAssessmentPage.tsx`
- Modify: `web/src/features/dashboard/DashboardPage.tsx`
- Modify: `web/src/features/dashboard/InstrumentLedgerTable.tsx`
- Modify: `web/src/features/dashboard/RunTable.tsx`
- Modify: `web/src/features/dashboard/instrumentPresentation.ts`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.tsx`
- Modify: `web/src/features/runs/ArtifactPreview.tsx`
- Modify: `web/src/features/runs/DecisionPanel.tsx`
- Modify: `web/src/features/runs/ReviewPanel.tsx`
- Modify: `web/src/features/runs/RunDetailPage.tsx`
- Modify: `web/src/features/runs/RunTimeline.tsx`
- Modify: `web/src/features/runs/ValidationChart.tsx`
- Modify: `web/src/features/runs/ValidationReplayPanel.tsx`
- Modify: `web/src/features/runs/validationReplay.ts`
- Modify: `web/src/features/system/CapacityBanner.tsx`
- Modify: `web/src/features/system/SystemPage.tsx`
- Modify: `web/src/i18n/messages.ts`
- Modify: `web/src/app/App.test.tsx`
- Modify: `web/src/features/assessments/AssessmentForm.test.tsx`
- Modify: `web/src/features/dashboard/DashboardPage.test.tsx`
- Modify: `web/src/features/dashboard/instrumentPresentation.test.ts`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.test.tsx`
- Modify: `web/src/features/runs/ArtifactPreview.test.tsx`
- Modify: `web/src/features/runs/DecisionPanel.test.tsx`
- Modify: `web/src/features/runs/RunDetailPage.test.tsx`
- Modify: `web/src/features/runs/RunTimeline.test.tsx`
- Modify: `web/src/features/runs/ValidationReplayPanel.test.tsx`
- Modify: `web/src/features/runs/validationReplay.test.ts`
- Modify: `web/src/features/system/SystemPage.test.tsx`

- [ ] **Step 1: Add failing English route smoke tests**

Render `I18nProvider` with stored `en-US` and cover these primary headings and actions:

```tsx
expect(screen.getByRole("heading", { name: "Assessment overview" })).toBeInTheDocument();
expect(screen.getByRole("link", { name: "New assessment" })).toBeInTheDocument();
expect(screen.getByRole("heading", { name: "Dispatch assessment" })).toBeInTheDocument();
expect(screen.getByRole("heading", { name: /assessment details/i })).toBeInTheDocument();
expect(screen.getByRole("heading", { name: "System status" })).toBeInTheDocument();
```

Keep a Chinese smoke test proving the original primary navigation and headings remain available.

- [ ] **Step 2: Run the English smoke tests and verify RED**

Run: `npm test -- --run src/app/App.test.tsx`

Expected: FAIL on English headings because the pages still contain fixed Chinese strings.

- [ ] **Step 3: Migrate all interface text**

Replace every user-visible fixed string in the listed production components with `t(...)`. Pass `t` or `locale` into pure presentation and validation helpers. Use the provider formatters instead of hard-coded `toLocaleString()` or `"zh-CN"`. Keep these values unchanged: report language payload values `Chinese` and `English`, model and vendor identifiers, ticker, hashes, raw JSON, comments, decisions and reports.

Add every source phrase to `EN_US`; examples of required dynamic keys are:

```ts
"运行 {running}/{limit}，排队 {queued}": "Running {running}/{limit}, queued {queued}",
"第 {attempt} 次尝试": "Attempt {attempt}",
"历史辅助 · {count} 个来源": "Historical context · {count} sources",
"事件序号 #{sequence}": "Event sequence #{sequence}",
"最早任务已等待 {duration}": "Oldest task has waited {duration}",
"预计 {date}": "Expected {date}",
```

- [ ] **Step 4: Translate chart and validation runtime labels**

Feed current `t` and `locale` into chart initialization and validation artifact parsing. Translate chart series titles, markers, legends, status explanations, copy feedback and TradingView helper text. Preserve raw provider IDs, calculation versions and stored error codes.

- [ ] **Step 5: Run the full Web test suite**

Run: `npm test -- --run`

Expected: every Web test passes in Chinese defaults and explicit English coverage.

- [ ] **Step 6: Commit the full UI migration**

```bash
git add web/src
git commit -m "feat: translate the management UI into English"
```

### Task 6: Regenerate contracts and verify the integrated system

**Files:**
- Modify: `var/openapi.json`
- Modify: `web/src/api/schema.d.ts`
- Modify: `web/src/test/contract.test.ts`
- Modify: `docs/superpowers/plans/2026-07-27-model-observability-i18n.md`

- [ ] **Step 1: Regenerate API contracts**

Run:

```bash
.venv/bin/python scripts/export_openapi.py
cd web && npm run api:generate
```

Expected: generated contracts include `model_routing`, `LlmInteractionPage`, `LlmInteractionView`, and `/api/v1/assessments/{run_id}/llm-interactions`.

- [ ] **Step 2: Run platform verification**

Run:

```bash
.venv/bin/pytest platform/tests -q
.venv/bin/ruff check platform gateway scripts integration_tests
```

Expected: zero failed tests and zero Ruff errors.

- [ ] **Step 3: Run complete Web verification**

Run:

```bash
cd web
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: zero failed tests, lint errors, type errors or build errors.

- [ ] **Step 4: Validate a real historical model feed**

Instantiate `RecordService` against the production MySQL configuration and local artifact/job directories, read run `e925a9cf-0ef5-467c-b62c-ade9cf424069`, and assert 27 safe records grouped as 25 fast `gpt-5.6-terra/high` and 2 slow `gpt-5.6-sol/xhigh`, with no prompt or response fields in serialized output.

- [ ] **Step 5: Commit generated contracts and plan completion**

Mark all plan checkboxes complete, then:

```bash
git add var/openapi.json web/src/api/schema.d.ts web/src/test/contract.test.ts \
  docs/superpowers/plans/2026-07-27-model-observability-i18n.md
git commit -m "chore: publish model observability API contract"
```

- [ ] **Step 6: Deploy and verify service health**

Build `web/dist`, restart `tradingng-platform-api.service`, and verify:

```bash
systemctl --user restart tradingng-platform-api.service
systemctl --user is-active tradingng-platform-api.service tradingng-codex-gateway.service
curl -fsS http://127.0.0.1:8010/health/ready
curl -fsS http://127.0.0.1:8000/healthz
systemctl is-active caddy
```

Expected: all services report active or `{"status":"ok"}` and Caddy serves the freshly built hashed assets.
