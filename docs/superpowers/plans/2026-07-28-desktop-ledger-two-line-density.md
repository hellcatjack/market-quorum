# PC 端标的台账双轨密度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不删减任何台账业务字段的前提下，将 PC 端每个标的数据行稳定组织为两条信息轨道。

**Architecture:** 保留现有语义化四列表格，在每个 `td` 内引入统一的 `ledger-lines`、主轨和辅轨结构。桌面端通过最小表格宽度、单行令牌和紧凑间距保证两轨布局；现有 `820px` 响应式断点恢复自然换行和卡片展示。

**Tech Stack:** React 19、TypeScript、Vitest、Testing Library、CSS、Vite

---

### Task 1: 建立双轨结构并保持全部业务信息

**Files:**
- Modify: `web/src/features/dashboard/DashboardPage.test.tsx`
- Modify: `web/src/features/dashboard/InstrumentLedgerTable.tsx`

- [ ] **Step 1: 写入失败的结构测试**

在 `defaults to one instrument row and keeps the full task view available` 中取得标的所在 `tr`，验证四个数据单元格各自仅有一个 `ledger-lines`，且每个容器包含两个直接子元素 `ledger-line`：

```typescript
const ledgerRow = instrument.closest("tr");
expect(ledgerRow).not.toBeNull();
for (const cell of ledgerRow?.querySelectorAll("td") ?? []) {
  const lines = cell.querySelector(":scope > .ledger-lines");
  expect(lines).not.toBeNull();
  expect(lines?.querySelectorAll(":scope > .ledger-line")).toHaveLength(2);
}
expect(ledgerRow).toHaveTextContent("英伟达");
expect(ledgerRow).toHaveTextContent("NVDA · NASDAQ");
expect(ledgerRow).toHaveTextContent("股票");
expect(ledgerRow).toHaveTextContent("成功 15");
expect(ledgerRow).toHaveTextContent("异常 2");
expect(ledgerRow).toHaveTextContent("共 20");
```

- [ ] **Step 2: 运行测试并确认因缺少双轨结构而失败**

Run: `npm --prefix web test -- --run src/features/dashboard/DashboardPage.test.tsx`

Expected: FAIL，因为当前单元格没有 `.ledger-lines` 和两条 `.ledger-line`。

- [ ] **Step 3: 最小化重排四列 JSX**

在每个 `td` 内使用以下骨架，保留现有链接、状态色类、文本与条件渲染：

```tsx
<div className="ledger-lines">
  <div className="ledger-line ledger-line--primary">主信息</div>
  <div className="ledger-line ledger-line--secondary">辅助信息</div>
</div>
```

具体映射：标的名称 / `代码 · 交易所 + 资产类型`；评级日期 / `PredictionOutcome`；可靠性与排除统计 / 评级演化；最新运行状态 / 全部任务计数。标的链接继续使用完整的 `aria-label`，但视觉链接只包裹名称，使第二轨元数据保持紧凑。

- [ ] **Step 4: 运行聚焦测试并确认通过**

Run: `npm --prefix web test -- --run src/features/dashboard/DashboardPage.test.tsx`

Expected: 2 tests PASS，现有结论、验证、可靠性、演化、异常入口断言继续通过。

### Task 2: 添加桌面紧凑样式和移动端回退

**Files:**
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: 添加桌面双轨样式**

给 `.ledger-table` 设置约 `1120px` 的最小宽度，给包装器设置 `overflow-x: auto`。将数据单元格垂直内边距收紧至 `7px`，新增：

```css
.ledger-lines {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.ledger-line {
  display: flex;
  align-items: center;
  gap: 4px 8px;
  min-width: 0;
  min-height: 1.05rem;
  white-space: nowrap;
}
```

将 `.prediction-outcome`、`.ledger-decision`、`.ledger-reliability` 和 `.ledger-counts` 在桌面端设为不换行；资产类型改为第二轨内联令牌；排除统计取消 `flex-basis: 100%`。不得使用 `display: none`、文本截断或将字段仅移入 `title`。

- [ ] **Step 2: 保留移动端自然换行**

在现有 `@media (max-width: 820px)` 内把表格 `min-width` 恢复为 `0`，并让 `.ledger-line`、预测、可靠性和计数重新允许换行。继续使用已有的四段卡片和 `data-label`。

- [ ] **Step 3: 执行完整前端验证**

Run: `npm --prefix web test -- --run && npm --prefix web run typecheck && npm --prefix web run lint && npm --prefix web run build`

Expected: 所有测试通过，TypeScript 与 ESLint 无错误，Vite 成功生成 `web/dist`。允许保留项目已有的 bundle 大小提示。

- [ ] **Step 4: 核对范围和生产资源**

Run: `git diff --check && git status --short --branch && rg -n "ledger-lines" web/dist/assets/*.js`

Expected: 差异只包含计划、规范、台账组件、样式与测试；生产 bundle 包含双轨类名。Caddy 直接读取 `web/dist`，无需重启 API、Gateway 或 Worker。

- [ ] **Step 5: 提交并推送主分支**

```bash
git add docs/superpowers web/src/features/dashboard web/src/styles/global.css
git commit -m "feat: compact desktop instrument ledger"
git push origin main
```
