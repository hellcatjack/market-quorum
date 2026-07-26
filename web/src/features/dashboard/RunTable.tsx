import { Link } from "wouter";

import type { AssessmentRun } from "../../api/assessments";

const STATUS: Record<string, { symbol: string; label: string; tone: string }> = {
  queued: { symbol: "◷", label: "排队中", tone: "warning" },
  admitted: { symbol: "→", label: "已准入", tone: "active" },
  starting: { symbol: "→", label: "启动中", tone: "active" },
  running_analysts: { symbol: "↻", label: "分析中", tone: "active" },
  research_debate: { symbol: "↻", label: "研究辩论", tone: "active" },
  trader_plan: { symbol: "↻", label: "交易计划", tone: "active" },
  risk_debate: { symbol: "↻", label: "风险辩论", tone: "active" },
  portfolio_decision: { symbol: "↻", label: "组合决策", tone: "active" },
  finalizing: { symbol: "↻", label: "整理结果", tone: "active" },
  succeeded: { symbol: "✓", label: "已完成", tone: "success" },
  failed: { symbol: "!", label: "失败", tone: "danger" },
  cancel_requested: { symbol: "◷", label: "等待取消", tone: "warning" },
  cancelling: { symbol: "◷", label: "取消中", tone: "warning" },
  cancelled: { symbol: "×", label: "已取消", tone: "muted" },
  needs_attention: { symbol: "!", label: "需要处理", tone: "danger" },
};

export function RunTable({
  runs,
  hasNext,
  hasPrevious,
  onNext,
  onPrevious,
}: {
  runs: AssessmentRun[];
  hasNext: boolean;
  hasPrevious: boolean;
  onNext: () => void;
  onPrevious: () => void;
}) {
  return (
    <div className="run-table-wrap panel">
      <table className="run-table">
        <thead>
          <tr>
            <th scope="col">标的</th>
            <th scope="col">状态</th>
            <th scope="col">分析日期</th>
            <th scope="col">资产</th>
            <th scope="col">尝试</th>
            <th scope="col">创建时间</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const status = STATUS[run.status] ?? { symbol: "·", label: run.status, tone: "muted" };
            const instrumentLabel = run.instrument_name
              ? [run.instrument_name, run.ticker, run.exchange].filter(Boolean).join(" ")
              : run.ticker;
            return (
              <tr key={run.id}>
                <td>
                  <Link
                    className="instrument-link"
                    href={`/runs/${run.id}`}
                    aria-label={instrumentLabel}
                  >
                    <span className="instrument-name">{run.instrument_name ?? run.ticker}</span>
                    {run.instrument_name ? (
                      <span className="instrument-symbol">
                        {run.ticker}{run.exchange ? ` · ${run.exchange}` : ""}
                      </span>
                    ) : null}
                  </Link>
                </td>
                <td>
                  <span className={`run-status run-status--${status.tone}`}>
                    <span aria-hidden="true">{status.symbol}</span> {status.label}
                  </span>
                </td>
                <td className="market-number">{run.analysis_date}</td>
                <td>{run.asset_type}</td>
                <td className="market-number">#{run.attempt}</td>
                <td title={run.created_at}>{new Date(run.created_at).toLocaleString()}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {runs.length === 0 ? <p className="table-empty">当前筛选条件下没有评估记录。</p> : null}
      <div className="pagination" aria-label="评估分页">
        <button type="button" onClick={onPrevious} disabled={!hasPrevious}>上一页</button>
        <button type="button" onClick={onNext} disabled={!hasNext}>下一页</button>
      </div>
    </div>
  );
}
