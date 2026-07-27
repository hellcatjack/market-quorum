# Ledger Density and History Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the instrument ledger materially denser and make instrument history open with the latest research first without losing chronological audit semantics.

**Architecture:** Keep the existing API contract and restructure only React presentation projections and CSS. Render forecast outcomes as semantic tokens, reduce the ledger from six uneven columns to four balanced columns, compute rating transitions chronologically, and apply the selected display order afterward.

**Tech Stack:** React 19, TypeScript 5.9, TanStack Query, Vitest, Testing Library, CSS.

---

## Constraints

- Work on `main` in the current workspace; do not use a worktree or subagents.
- Do not modify `TradingAgents/` or backend contracts.
- Preserve REST/MCP behavior and every existing detail/audit link.

### Task 1: Project forecast outcomes into compact semantic tokens

**Files:**
- Modify: `web/src/features/dashboard/instrumentPresentation.ts`
- Modify: `web/src/features/dashboard/instrumentPresentation.test.ts`

- [ ] **Step 1: Write a failing token-projection test**

```typescript
expect(predictionOutcomeTokens(overview)).toEqual({
  rating: "Underweight",
  direction: "↓",
  horizon: "20D",
  performance: "-20.65%",
  alpha: "-14.59%",
  outcome: "方向正确",
  target: null,
  state: "completed",
});
```

Also assert scheduled validation returns `state: "pending"`, failed validation returns `state: "error"`, and no decision returns `state: "empty"` without describing it as a failed forecast.

- [ ] **Step 2: Verify the test fails for the missing projection**

Run: `npm --prefix web test -- --run src/features/dashboard/instrumentPresentation.test.ts`

Expected: FAIL because `predictionOutcomeTokens` is not exported.

- [ ] **Step 3: Implement the typed projection**

```typescript
export interface PredictionOutcomeTokens {
  rating: string | null;
  direction: string | null;
  horizon: string | null;
  performance: string | null;
  alpha: string | null;
  outcome: string;
  target: string | null;
  state: "completed" | "pending" | "error" | "empty";
}
```

Populate it from `latest_decision` and `preferred_validation`; use `formatPercent`, preserve target status only when evaluated, and keep the existing `formatPredictionOutcome` output stable by composing it from the projection.

- [ ] **Step 4: Run the presentation tests**

Run: `npm --prefix web test -- --run src/features/dashboard/instrumentPresentation.test.ts`

Expected: all tests pass.

### Task 2: Replace the six-column ledger with a balanced four-column table

**Files:**
- Modify: `web/src/features/dashboard/InstrumentLedgerTable.tsx`
- Modify: `web/src/features/dashboard/DashboardPage.test.tsx`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write failing structure and density assertions**

In `DashboardPage.test.tsx`, assert exactly these headers:

```typescript
expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
  "标的", "结论与表现", "可靠性与变化", "运行",
]);
expect(screen.queryByText("估值风险较高。")).not.toBeInTheDocument();
```

Assert the row still contains `Underweight`, `20D`, `-20.65%`, `Alpha -14.59%`, `方向正确`, `2 次 · 样本不足`, `Hold → Underweight`, and the latest failed-task link.

- [ ] **Step 2: Verify the old six-column table fails**

Run: `npm --prefix web test -- --run src/features/dashboard/DashboardPage.test.tsx`

Expected: FAIL because the old table has six headers and renders the executive summary.

- [ ] **Step 3: Render the compact four-column structure**

Combine decision and tokenized outcome in one main cell; combine reliability and rating transition in one supporting cell; combine latest task status and counts in one operations cell. Render each token in a dedicated `span` with classes such as `prediction-token`, `prediction-token--metric`, and `prediction-token--outcome`; do not render `executive_summary` in the ledger.

- [ ] **Step 4: Rebalance desktop and responsive CSS**

Use a four-column layout with fixed identity/support/operations widths and a flexible main column. Reduce row padding, replace wide count pills with compact text metrics, set `white-space: nowrap` only on tokens/dates/statuses, and keep the existing 820px grouped-card fallback with four matching `data-label` values.

- [ ] **Step 5: Run dashboard tests and static checks**

Run: `npm --prefix web test -- --run src/features/dashboard/DashboardPage.test.tsx src/features/dashboard/instrumentPresentation.test.ts && npm --prefix web run lint && npm --prefix web run typecheck`

Expected: tests and static checks exit 0.

- [ ] **Step 6: Commit the ledger slice**

```bash
git add web/src/features/dashboard/instrumentPresentation.ts web/src/features/dashboard/instrumentPresentation.test.ts web/src/features/dashboard/InstrumentLedgerTable.tsx web/src/features/dashboard/DashboardPage.test.tsx web/src/styles/global.css
git commit -m "feat: densify instrument research ledger"
```

### Task 3: Default history to latest-first and harden text layout

**Files:**
- Modify: `web/src/features/instruments/instrumentHistory.ts`
- Modify: `web/src/features/instruments/instrumentHistory.test.ts`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.tsx`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.test.tsx`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write failing ordering tests**

Add a pure projection test:

```typescript
const projection = projectInstrumentHistory(descendingApiItems);
expect(projection.map((event) => event.primary.run.id)).toEqual(["run-new", "run-old"]);
expect(projection[0].transition).toBe("Hold → Underweight");
```

In the page test, assert the first event is `2026-07-25`, the `最新在前` control is selected, clicking `最早在前` makes `2026-06-01` first, and both orders retain `Hold → Underweight` on the newer event.

- [ ] **Step 2: Verify current oldest-first behavior fails**

Run: `npm --prefix web test -- --run src/features/instruments/instrumentHistory.test.ts src/features/instruments/InstrumentHistoryPage.test.tsx`

Expected: FAIL because history defaults to ascending order and has no sort control.

- [ ] **Step 3: Implement chronological transition projection and display ordering**

Export `projectInstrumentHistory(items)` that groups events ascending, computes each transition against the preceding successful rating, then returns newest-first. Export `orderInstrumentHistory(events, "newest" | "oldest")` to return a copied array in the selected display order. In the page, default state to `newest`, render two pressed-state buttons, and update the direction label to `由新到旧` or `由旧到新`.

- [ ] **Step 4: Fix text wrapping at semantic boundaries**

Use a three-area event header for analysis date, local creation time, and status; give the decision text `min-width: 0` and the detail action a non-shrinking width; prevent date/status/validation metrics from splitting; let Chinese summaries use normal paragraph wrapping; truncate the configuration hash on desktop and allow safe hash wrapping only below 480px.

- [ ] **Step 5: Run focused and complete frontend verification**

Run: `npm --prefix web test -- --run src/features/instruments/instrumentHistory.test.ts src/features/instruments/InstrumentHistoryPage.test.tsx && npm --prefix web run lint && npm --prefix web run typecheck && npm --prefix web run test -- --run && npm --prefix web run build`

Expected: all frontend tests pass and the production bundle builds.

- [ ] **Step 6: Deploy and verify**

Restart only `tradingng-platform-api.service` if API code changed; otherwise the Caddy-served `web/dist` bundle updates in place. Verify `http://127.0.0.1:8010/health/ready`, Gateway health, production asset contents, and authenticated public REST without restarting the Gateway.

- [ ] **Step 7: Commit and push main**

```bash
git add web/src/features/instruments web/src/styles/global.css docs/superpowers
git commit -m "feat: prioritize latest instrument research"
git push origin main
```
