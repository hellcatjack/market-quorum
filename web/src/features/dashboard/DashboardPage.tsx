import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "wouter";

import {
  getCapacity,
  listAssessments,
  type AssessmentStatus,
} from "../../api/assessments";
import { CapacityBanner } from "../system/CapacityBanner";
import { RunTable } from "./RunTable";

const ACTIVE = new Set<AssessmentStatus>([
  "admitted", "starting", "running_analysts", "research_debate", "trader_plan",
  "risk_debate", "portfolio_decision", "finalizing", "cancel_requested", "cancelling",
]);

function utcBoundary(date: string, end: boolean): string | undefined {
  if (!date) return undefined;
  return `${date}T${end ? "23:59:59.999" : "00:00:00.000"}Z`;
}

export function DashboardPage() {
  const [ticker, setTicker] = useState("");
  const [status, setStatus] = useState<AssessmentStatus | "">("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursorHistory, setCursorHistory] = useState<(string | undefined)[]>([]);

  const filters = useMemo(
    () => ({
      ticker: ticker.trim().toUpperCase() || undefined,
      statuses: status ? [status] : undefined,
      createdFrom: utcBoundary(createdFrom, false),
      createdTo: utcBoundary(createdTo, true),
      cursor,
      limit: 50,
    }),
    [ticker, status, createdFrom, createdTo, cursor],
  );
  const capacity = useQuery({
    queryKey: ["system-capacity"],
    queryFn: getCapacity,
    refetchInterval: 5_000,
    retry: false,
  });
  const runs = useQuery({
    queryKey: ["assessments", filters],
    queryFn: () => listAssessments(filters),
    refetchInterval: (query) =>
      query.state.data?.items.some((run) => run.status === "queued" || ACTIVE.has(run.status))
        ? 5_000
        : 60_000,
    retry: false,
  });

  const counts = {
    queued: runs.data?.items.filter((run) => run.status === "queued").length ?? 0,
    active: runs.data?.items.filter((run) => ACTIVE.has(run.status)).length ?? 0,
    succeeded: runs.data?.items.filter((run) => run.status === "succeeded").length ?? 0,
    failed:
      runs.data?.items.filter((run) => run.status === "failed" || run.status === "needs_attention").length ?? 0,
  };

  function resetCursor() {
    setCursor(undefined);
    setCursorHistory([]);
  }

  return (
    <section className="page-shell dashboard-page">
      <header className="page-header dashboard-heading">
        <div>
          <p className="eyebrow">TradingNG / 实时队列</p>
          <h1>评估总览</h1>
          <p>从派发、准入到结论归档的统一视图。</p>
        </div>
        <Link className="primary-button" href="/new">＋ 新建评估</Link>
      </header>
      {capacity.data ? <CapacityBanner capacity={capacity.data} /> : null}
      {capacity.isError ? <p className="page-warning" role="alert">容量数据暂时不可用。</p> : null}
      {capacity.data?.open_circuits.length ? (
        <div className="circuit-warning" role="alert">
          <strong>数据源熔断</strong>
          {capacity.data.open_circuits.map((circuit) => <span key={circuit}>{circuit}</span>)}
        </div>
      ) : null}
      <div className="count-grid" aria-label="当前列表状态统计">
        <article><span>排队</span><strong data-testid="count-queued">{counts.queued}</strong></article>
        <article><span>运行中</span><strong data-testid="count-active">{counts.active}</strong></article>
        <article><span>已完成</span><strong data-testid="count-succeeded">{counts.succeeded}</strong></article>
        <article><span>异常</span><strong data-testid="count-failed">{counts.failed}</strong></article>
      </div>
      <div className="run-section-heading">
        <div>
          <p className="eyebrow">评估记录</p>
          <h2>任务队列与历史</h2>
        </div>
        {runs.isFetching ? <span className="refresh-note" role="status">正在刷新…</span> : null}
      </div>
      <div className="run-filters panel">
        <label><span>标的</span><input value={ticker} onChange={(event) => { setTicker(event.target.value); resetCursor(); }} placeholder="NVDA" /></label>
        <label>
          <span>状态</span>
          <select value={status} onChange={(event) => { setStatus(event.target.value as AssessmentStatus | ""); resetCursor(); }}>
            <option value="">全部状态</option>
            <option value="queued">排队中</option>
            <option value="running_analysts">分析中</option>
            <option value="succeeded">已完成</option>
            <option value="failed">失败</option>
            <option value="needs_attention">需要处理</option>
          </select>
        </label>
        <label><span>开始日期</span><input type="date" value={createdFrom} onChange={(event) => { setCreatedFrom(event.target.value); resetCursor(); }} /></label>
        <label><span>结束日期</span><input type="date" value={createdTo} onChange={(event) => { setCreatedTo(event.target.value); resetCursor(); }} /></label>
      </div>
      {runs.isError ? <p className="page-warning" role="alert">评估列表暂时不可用，请稍后重试。</p> : null}
      <RunTable
        runs={runs.data?.items ?? []}
        hasNext={Boolean(runs.data?.next_cursor)}
        hasPrevious={cursorHistory.length > 0}
        onNext={() => {
          if (!runs.data?.next_cursor) return;
          setCursorHistory((history) => [...history, cursor]);
          setCursor(runs.data.next_cursor ?? undefined);
        }}
        onPrevious={() => {
          setCursorHistory((history) => {
            const previous = history.at(-1);
            setCursor(previous);
            return history.slice(0, -1);
          });
        }}
      />
    </section>
  );
}
