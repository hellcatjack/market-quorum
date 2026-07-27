import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "wouter";

import {
  getCapacity,
  listAssessments,
  type AssessmentStatus,
} from "../../api/assessments";
import { listInstrumentOverviews } from "../../api/records";
import { CapacityBanner } from "../system/CapacityBanner";
import { InstrumentLedgerTable } from "./InstrumentLedgerTable";
import { RunTable } from "./RunTable";

const ACTIVE = new Set<AssessmentStatus>([
  "admitted", "starting", "running_analysts", "research_debate", "trader_plan",
  "risk_debate", "portfolio_decision", "finalizing", "cancel_requested", "cancelling",
]);

type DashboardView = "instruments" | "runs";

function utcBoundary(date: string, end: boolean): string | undefined {
  if (!date) return undefined;
  return `${date}T${end ? "23:59:59.999" : "00:00:00.000"}Z`;
}

export function DashboardPage() {
  const [view, setView] = useState<DashboardView>("instruments");
  const [ticker, setTicker] = useState("");
  const [status, setStatus] = useState<AssessmentStatus | "">("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [runCursor, setRunCursor] = useState<string | undefined>();
  const [runCursorHistory, setRunCursorHistory] = useState<(string | undefined)[]>([]);
  const [ledgerCursor, setLedgerCursor] = useState<string | undefined>();
  const [ledgerCursorHistory, setLedgerCursorHistory] = useState<(string | undefined)[]>([]);

  const commonDates = useMemo(
    () => ({
      createdFrom: utcBoundary(createdFrom, false),
      createdTo: utcBoundary(createdTo, true),
    }),
    [createdFrom, createdTo],
  );
  const runFilters = useMemo(
    () => ({
      ticker: ticker.trim().toUpperCase() || undefined,
      statuses: status ? [status] : undefined,
      ...commonDates,
      cursor: runCursor,
      limit: 50,
    }),
    [ticker, status, commonDates, runCursor],
  );
  const ledgerFilters = useMemo(
    () => ({
      query: ticker.trim() || undefined,
      statuses: status ? [status] : undefined,
      ...commonDates,
      cursor: ledgerCursor,
      limit: 50,
    }),
    [ticker, status, commonDates, ledgerCursor],
  );

  const capacity = useQuery({
    queryKey: ["system-capacity"],
    queryFn: getCapacity,
    refetchInterval: 5_000,
    retry: false,
  });
  const overview = useQuery({
    queryKey: ["instrument-overviews", ledgerFilters],
    queryFn: () => listInstrumentOverviews(ledgerFilters),
    enabled: view === "instruments",
    refetchInterval: (query) => {
      const counts = query.state.data?.run_counts;
      return counts && (counts.queued > 0 || counts.active > 0) ? 5_000 : 60_000;
    },
    retry: false,
  });
  const runs = useQuery({
    queryKey: ["assessments", runFilters],
    queryFn: () => listAssessments(runFilters),
    enabled: view === "runs",
    refetchInterval: (query) =>
      query.state.data?.items.some((run) => run.status === "queued" || ACTIVE.has(run.status))
        ? 5_000
        : 60_000,
    retry: false,
  });

  const counts = view === "instruments"
    ? {
        queued: overview.data?.run_counts?.queued ?? 0,
        active: overview.data?.run_counts?.active ?? 0,
        succeeded: overview.data?.run_counts?.succeeded ?? 0,
        failed: overview.data?.run_counts?.anomalous ?? 0,
      }
    : {
        queued: runs.data?.items.filter((run) => run.status === "queued").length ?? 0,
        active: runs.data?.items.filter((run) => ACTIVE.has(run.status)).length ?? 0,
        succeeded: runs.data?.items.filter((run) => run.status === "succeeded").length ?? 0,
        failed: runs.data?.items.filter(
          (run) => run.status === "failed" || run.status === "needs_attention",
        ).length ?? 0,
      };

  function resetCursors() {
    setRunCursor(undefined);
    setRunCursorHistory([]);
    setLedgerCursor(undefined);
    setLedgerCursorHistory([]);
  }

  return (
    <section className="page-shell dashboard-page">
      <header className="page-header dashboard-heading">
        <div>
          <p className="eyebrow">TradingNG / 研究台账</p>
          <h1>评估总览</h1>
          <p>集中查看最新判断、实际表现与完整任务状态。</p>
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
      <div className="count-grid" aria-label="当前筛选状态统计">
        <article><span>排队</span><strong data-testid="count-queued">{counts.queued}</strong></article>
        <article><span>运行中</span><strong data-testid="count-active">{counts.active}</strong></article>
        <article><span>已完成</span><strong data-testid="count-succeeded">{counts.succeeded}</strong></article>
        <article><span>异常</span><strong data-testid="count-failed">{counts.failed}</strong></article>
      </div>

      <div className="dashboard-view-tabs" role="tablist" aria-label="总览视图">
        <button
          type="button"
          role="tab"
          aria-selected={view === "instruments"}
          onClick={() => setView("instruments")}
        >
          标的台账
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "runs"}
          onClick={() => setView("runs")}
        >
          任务记录
        </button>
      </div>
      <div className="run-section-heading">
        <div>
          <p className="eyebrow">{view === "instruments" ? "预测与实际" : "运行审计"}</p>
          <h2>{view === "instruments" ? "标的研究台账" : "任务队列与历史"}</h2>
        </div>
        {(view === "instruments" ? overview.isFetching : runs.isFetching) ? (
          <span className="refresh-note" role="status">正在刷新…</span>
        ) : null}
      </div>
      <div className="run-filters panel">
        <label>
          <span>标的</span>
          <input
            value={ticker}
            onChange={(event) => { setTicker(event.target.value); resetCursors(); }}
            placeholder={view === "instruments" ? "NVDA / 英伟达" : "NVDA"}
          />
        </label>
        <label>
          <span>最新状态</span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as AssessmentStatus | "");
              resetCursors();
            }}
          >
            <option value="">全部状态</option>
            <option value="queued">排队中</option>
            <option value="running_analysts">分析中</option>
            <option value="succeeded">已完成</option>
            <option value="failed">失败</option>
            <option value="needs_attention">需要处理</option>
          </select>
        </label>
        <label>
          <span>开始日期</span>
          <input
            type="date"
            value={createdFrom}
            onChange={(event) => { setCreatedFrom(event.target.value); resetCursors(); }}
          />
        </label>
        <label>
          <span>结束日期</span>
          <input
            type="date"
            value={createdTo}
            onChange={(event) => { setCreatedTo(event.target.value); resetCursors(); }}
          />
        </label>
      </div>

      {view === "instruments" ? (
        <>
          {overview.isError ? (
            <p className="page-warning" role="alert">
              标的台账暂时不可用；可切换到任务记录继续处理运行问题。
            </p>
          ) : null}
          <InstrumentLedgerTable
            items={overview.data?.items ?? []}
            validationsVisible={overview.data?.validations_visible ?? true}
            hasNext={Boolean(overview.data?.next_cursor)}
            hasPrevious={ledgerCursorHistory.length > 0}
            onNext={() => {
              if (!overview.data?.next_cursor) return;
              setLedgerCursorHistory((history) => [...history, ledgerCursor]);
              setLedgerCursor(overview.data.next_cursor ?? undefined);
            }}
            onPrevious={() => {
              setLedgerCursorHistory((history) => {
                setLedgerCursor(history.at(-1));
                return history.slice(0, -1);
              });
            }}
          />
        </>
      ) : (
        <>
          {runs.isError ? (
            <p className="page-warning" role="alert">评估列表暂时不可用，请稍后重试。</p>
          ) : null}
          <RunTable
            runs={runs.data?.items ?? []}
            hasNext={Boolean(runs.data?.next_cursor)}
            hasPrevious={runCursorHistory.length > 0}
            onNext={() => {
              if (!runs.data?.next_cursor) return;
              setRunCursorHistory((history) => [...history, runCursor]);
              setRunCursor(runs.data.next_cursor ?? undefined);
            }}
            onPrevious={() => {
              setRunCursorHistory((history) => {
                setRunCursor(history.at(-1));
                return history.slice(0, -1);
              });
            }}
          />
        </>
      )}
    </section>
  );
}
