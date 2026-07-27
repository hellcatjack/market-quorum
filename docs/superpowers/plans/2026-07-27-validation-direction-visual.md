# Validation Direction Visual States Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make correct, incorrect, and unjudged validation directions immediately distinguishable on the instrument conclusion-evolution page without confusing direction accuracy with return sign.

**Architecture:** Keep the API and validation logic unchanged. Derive a presentation-only direction state inside `ValidationCell`, apply semantic modifier classes to the completed validation card and its text badge, and define restrained green/red/neutral styles in the existing global stylesheet.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, CSS

---

### Task 1: Add semantic direction states to validation cards

**Files:**
- Modify: `web/src/features/instruments/InstrumentHistoryPage.test.tsx`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.tsx`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write the failing component assertions**

Extend the validation fixture so the 1D result is direction-correct and the 20D result is direction-incorrect. Assert that each visible label and its containing card receive the corresponding semantic classes:

```tsx
const correctDirection = within(events[0]).getByText("方向正确");
expect(correctDirection).toHaveClass("history-validation__direction--correct");
expect(correctDirection.closest(".history-validation")).toHaveClass(
  "history-validation--direction-correct",
);

const incorrectDirection = within(events[0]).getByText("方向错误");
expect(incorrectDirection).toHaveClass("history-validation__direction--incorrect");
expect(incorrectDirection.closest(".history-validation")).toHaveClass(
  "history-validation--direction-incorrect",
);
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm test -- --run src/features/instruments/InstrumentHistoryPage.test.tsx`

Expected: FAIL because `history-validation__direction--correct` and `history-validation--direction-correct` do not exist yet.

- [ ] **Step 3: Add the minimal presentation mapping**

In `InstrumentHistoryPage.tsx`, derive a state from `validation.direction_correct` and use it in the completed result markup:

```tsx
const directionState = validation.direction_correct === true
  ? "correct"
  : validation.direction_correct === false
    ? "incorrect"
    : "unjudged";
const directionLabel = validation.direction_correct === true
  ? "方向正确"
  : validation.direction_correct === false
    ? "方向错误"
    : "方向未判定";

<div className={`history-validation history-validation--completed history-validation--direction-${directionState}`}>
  <strong>{horizon}D</strong>
  <span>{formatPercent(validation.total_return)}</span>
  {validation.total_alpha !== null ? (
    <small>Alpha {formatPercent(validation.total_alpha)}</small>
  ) : null}
  <small className={`history-validation__direction history-validation__direction--${directionState}`}>
    {directionLabel}
  </small>
  {validation.price_target_hit !== null ? (
    <small>{validation.price_target_hit ? "目标价命中" : "目标价未命中"}</small>
  ) : null}
</div>
```

- [ ] **Step 4: Add restrained and accessible CSS states**

In `global.css`, style the complete card and badge. Use a subtle inset accent plus pale background on the card, and a bordered pill with a generated symbol on the badge:

```css
.history-validation--direction-correct {
  border-color: rgb(38 114 76 / 35%);
  background: rgb(38 114 76 / 6%);
  box-shadow: inset 3px 0 var(--success);
}

.history-validation--direction-incorrect {
  border-color: rgb(166 55 52 / 35%);
  background: rgb(166 55 52 / 6%);
  box-shadow: inset 3px 0 var(--danger);
}

.history-validation--direction-unjudged {
  background: var(--surface-muted);
}

.history-validation small.history-validation__direction {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  margin-top: 2px;
  padding: 3px 7px;
  border: 1px solid currentcolor;
  border-radius: 999px;
  font-weight: 800;
  line-height: 1.2;
}

.history-validation__direction--correct {
  color: var(--success);
}

.history-validation__direction--incorrect {
  color: var(--danger);
}

.history-validation__direction--unjudged {
  color: var(--ink-muted);
}

.history-validation__direction--correct::before {
  content: "✓";
}

.history-validation__direction--incorrect::before {
  content: "×";
}

.history-validation__direction--unjudged::before {
  content: "—";
}
```

- [ ] **Step 5: Run the focused test and verify GREEN**

Run: `npm test -- --run src/features/instruments/InstrumentHistoryPage.test.tsx`

Expected: PASS with the correct and incorrect state assertions both satisfied.

- [ ] **Step 6: Run full frontend verification**

Run:

```bash
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: all commands exit 0 with no failed tests, lint errors, type errors, or build errors.

- [ ] **Step 7: Review and commit the implementation**

Run `git diff --check` and inspect the scoped diff, then commit:

```bash
git add web/src/features/instruments/InstrumentHistoryPage.test.tsx \
  web/src/features/instruments/InstrumentHistoryPage.tsx \
  web/src/styles/global.css
git commit -m "feat: color validation direction outcomes"
```
