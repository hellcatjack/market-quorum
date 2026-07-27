# Assessment Memory Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make verified historical assistance the recommended Web default and replace the ambiguous memory dropdown with an accessible, explanatory two-card selector.

**Architecture:** Keep the existing `memory_mode` API field and platform memory implementation unchanged. Change only `AssessmentForm` state and markup, add presentation styles, and prove the Web default plus explicit independent override through existing form submission tests.

**Tech Stack:** React 19, TypeScript 5.9, TanStack Query, Testing Library, Vitest, CSS, Vite.

---

## Constraints

- Work directly on `main` in the current workspace; do not create a worktree or use subagents.
- Do not modify `TradingAgents/`, platform API contracts, OpenAPI schema, MCP tools, scheduler defaults, or memory selection logic.
- Web submissions must continue sending an explicit `memory_mode` value.
- REST and MCP callers that omit `memory_mode` must continue receiving the existing `independent` default.

## File map

- Modify `web/src/features/assessments/AssessmentForm.tsx`: Web default, accessible radio-card selector, and explanatory copy.
- Modify `web/src/features/assessments/AssessmentForm.test.tsx`: default-historical and explicit-independent submission behavior.
- Modify `web/src/styles/global.css`: card selection states and desktop/mobile layout.
- Do not create new API, state-management, or backend files.

### Task 1: Pin the Web memory-mode behavior with tests

**Files:**
- Modify: `web/src/features/assessments/AssessmentForm.test.tsx`

- [ ] **Step 1: Change the default-submission test to require historical assistance**

Replace the old combobox assertion with native radio assertions and require the default payload to be historical:

```tsx
const historical = screen.getByRole("radio", { name: /历史辅助/ });
const independent = screen.getByRole("radio", { name: /独立评估/ });
expect(historical).toBeChecked();
expect(independent).not.toBeChecked();
expect(screen.queryByRole("combobox", { name: "评估记忆" })).not.toBeInTheDocument();
expect(screen.getByText("推荐")).toBeInTheDocument();
expect(screen.getByText(/最多 5 条/)).toBeInTheDocument();
expect(screen.getByText(/零记忆继续运行/)).toBeInTheDocument();
expect(screen.getByText(/不会训练或修改模型/)).toBeInTheDocument();
expect(screen.getByText(/当前证据优先/)).toBeInTheDocument();
```

After submitting the existing SPCX/NVDA fixture, assert:

```tsx
expect(payload.memory_mode).toBe("historical");
```

- [ ] **Step 2: Change the explicit-mode test to prove independent override**

Rename it to `can explicitly request an independent assessment` and replace the select interaction with:

```tsx
await user.click(screen.getByRole("radio", { name: /独立评估/ }));
expect(screen.getByRole("radio", { name: /独立评估/ })).toBeChecked();
```

After submission, assert:

```tsx
expect(payload.memory_mode).toBe("independent");
```

- [ ] **Step 3: Run the focused test and confirm the old UI fails**

Run:

```bash
npm --prefix web test -- --run src/features/assessments/AssessmentForm.test.tsx
```

Expected: FAIL because the form still exposes a combobox and initializes `memory_mode` as `independent`.

### Task 2: Implement the recommended historical selector

**Files:**
- Modify: `web/src/features/assessments/AssessmentForm.tsx`
- Test: `web/src/features/assessments/AssessmentForm.test.tsx`

- [ ] **Step 1: Change only the Web initial state**

Replace the initial memory state with:

```tsx
const [memoryMode, setMemoryMode] =
  useState<SubmitAssessmentBatch["memory_mode"]>("historical");
```

Do not change the submission payload shape.

- [ ] **Step 2: Add one shared radio change handler**

Inside `AssessmentForm`, add:

```tsx
function selectMemoryMode(value: SubmitAssessmentBatch["memory_mode"]) {
  setMemoryMode(value);
  idempotencyKey.current = globalThis.crypto.randomUUID();
}
```

- [ ] **Step 3: Replace the memory dropdown with a full-width fieldset**

Remove the existing `label.field` containing the `select` and insert this fieldset after the three compact select fields:

```tsx
<fieldset className="memory-mode-fieldset">
  <legend>评估记忆</legend>
  <div className="memory-mode-options">
    <label className="memory-mode-option">
      <input
        type="radio"
        name="memory-mode"
        value="historical"
        checked={memoryMode === "historical"}
        onChange={() => selectMemoryMode("historical")}
      />
      <span className="memory-mode-copy">
        <span className="memory-mode-title">
          <strong>历史辅助</strong>
          <em>推荐</em>
        </span>
        <span>参考同标的、分析日前已经完成表现验证的旧评估。</span>
        <small>最多 5 条；没有合格记录时，以零记忆继续运行。</small>
        <small>当前证据优先，历史结论不是投票。</small>
      </span>
    </label>
    <label className="memory-mode-option">
      <input
        type="radio"
        name="memory-mode"
        value="independent"
        checked={memoryMode === "independent"}
        onChange={() => selectMemoryMode("independent")}
      />
      <span className="memory-mode-copy">
        <span className="memory-mode-title"><strong>独立评估</strong></span>
        <span>不读取任何旧评估结论。</span>
        <small>适合基准对照、争议复核或需要隔离历史观点的任务。</small>
      </span>
    </label>
  </div>
  <p className="memory-mode-note">
    历史辅助不会训练或修改模型，只向最终投资判断提供可审计的历史校准信息。
  </p>
</fieldset>
```

The native labels provide the accessible radio names; do not add duplicate `aria-label` values.

- [ ] **Step 4: Run the focused test**

Run:

```bash
npm --prefix web test -- --run src/features/assessments/AssessmentForm.test.tsx
```

Expected: all `AssessmentForm` tests pass, including default `historical` and explicit `independent` payloads.

- [ ] **Step 5: Commit the behavior slice**

```bash
git add web/src/features/assessments/AssessmentForm.tsx web/src/features/assessments/AssessmentForm.test.tsx
git commit -m "feat: default web assessments to historical memory"
```

### Task 3: Style and verify the explanatory selector

**Files:**
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Add fieldset and two-card layout styles next to the assessment form styles**

Add:

```css
.memory-mode-fieldset {
  grid-column: 1 / -1;
  min-width: 0;
  padding: 0;
  border: 0;
}

.memory-mode-fieldset legend {
  margin-bottom: 10px;
  color: #405064;
  font-size: 0.86rem;
  font-weight: 650;
}

.memory-mode-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.memory-mode-option {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 11px;
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fafaf6;
  cursor: pointer;
}

.memory-mode-option:has(input:checked) {
  border-color: var(--action);
  background: #f0f7fa;
  box-shadow: inset 0 0 0 1px var(--action);
}

.memory-mode-option:focus-within {
  outline: 3px solid rgb(29 117 168 / 22%);
  outline-offset: 2px;
}

.memory-mode-option input {
  width: 18px;
  min-height: 18px;
  margin-top: 2px;
}

.memory-mode-copy {
  display: grid;
  gap: 5px;
  min-width: 0;
  color: var(--ink);
  line-height: 1.45;
}

.memory-mode-copy small {
  color: var(--ink-muted);
}

.memory-mode-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
}

.memory-mode-title em {
  padding: 2px 7px;
  border-radius: 999px;
  color: var(--action);
  background: rgb(29 117 168 / 9%);
  font-size: 0.68rem;
  font-style: normal;
  font-weight: 800;
}

.memory-mode-note {
  margin: 10px 0 0;
  color: var(--ink-muted);
  font-size: 0.78rem;
  line-height: 1.55;
}
```

- [ ] **Step 2: Add the narrow-screen single-column rule**

Inside `@media (max-width: 760px)`, add:

```css
.memory-mode-options {
  grid-template-columns: 1fr;
}
```

No fixed width or `white-space: nowrap` may be applied to explanatory text.

- [ ] **Step 3: Run all frontend checks**

Run:

```bash
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test -- --run
npm --prefix web run build
```

Expected: lint and typecheck exit 0, all frontend tests pass, and Vite emits the production bundle.

- [ ] **Step 4: Inspect desktop and 390px layouts**

Serve `web/dist` temporarily, mock only the existing capacity and user API reads, and inspect `/new` at 1440px and 390px. Verify:

- two cards are side-by-side on desktop and stacked at 390px;
- the historical card is visibly selected and includes the recommendation badge;
- every explanatory sentence wraps naturally;
- `document.body.scrollWidth === document.body.clientWidth` at 390px;
- the native radio controls remain focusable and selectable.

- [ ] **Step 5: Commit the presentation slice**

```bash
git add web/src/styles/global.css
git commit -m "style: clarify assessment memory choices"
```

### Task 4: Full regression, deployment, and push

**Files:**
- Verify only: `TradingAgents/`, `platform/`, `gateway/`, `web/`, deployment configuration.

- [ ] **Step 1: Prove protected boundaries are unchanged**

Run:

```bash
test -z "$(git diff origin/main --name-only -- TradingAgents platform/src/tradingng_platform/assessments platform/src/tradingng_platform/mcp)"
```

Expected: exit 0 with no paths printed.

- [ ] **Step 2: Run the complete project verification**

Run:

```bash
scripts/verify_platform.sh
```

Expected: Gateway, platform, integration, temporary MySQL, frontend, lint, typecheck, build, audit, Caddy, systemd, Keycloak URL and artifact checks all exit 0. The existing dedicated migration-database skip is acceptable.

- [ ] **Step 3: Verify live services and static deployment**

Because only Web source changes, do not restart Gateway, API, scheduler or validation services. Verify:

```bash
systemctl --user is-active \
  tradingng-codex-gateway.service \
  tradingng-platform-api.service \
  tradingng-platform-scheduler.service \
  tradingng-platform-validation.service
curl --fail --silent http://127.0.0.1:8010/health/ready
```

Expected: all services print `active`; readiness reports MySQL status `ok`. Caddy serves the freshly built `web/dist` in place.

- [ ] **Step 4: Push `main` and prove it is synchronized**

```bash
git push origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
git status --short --branch
```

Expected: push succeeds and the worktree is clean on `main`.
