import { Link } from "wouter";

import type { InstrumentOverview } from "../../api/records";
import {
  formatPredictionOutcome,
  ratingTransition,
  reliabilityLabel,
} from "./instrumentPresentation";

const ANOMALOUS = new Set(["failed", "needs_attention"]);

const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  admitted: "已准入",
  starting: "启动中",
  running_analysts: "分析中",
  research_debate: "研究辩论",
  trader_plan: "交易计划",
  risk_debate: "风险辩论",
  portfolio_decision: "组合决策",
  finalizing: "整理结果",
  succeeded: "已完成",
  failed: "失败",
  cancel_requested: "等待取消",
  cancelling: "取消中",
  cancelled: "已取消",
  needs_attention: "需要处理",
};

function instrumentLabel(item: InstrumentOverview): string {
  return [item.instrument.name ?? item.instrument.ticker, item.instrument.ticker, item.instrument.exchange]
    .filter((value, index, values) => Boolean(value) && values.indexOf(value) === index)
    .join(" ");
}

export function InstrumentLedgerTable({
  items,
  validationsVisible,
  hasNext,
  hasPrevious,
  onNext,
  onPrevious,
}: {
  items: InstrumentOverview[];
  validationsVisible: boolean;
  hasNext: boolean;
  hasPrevious: boolean;
  onNext: () => void;
  onPrevious: () => void;
}) {
  return (
    <div className="ledger-table-wrap panel">
      <table className="ledger-table">
        <thead>
          <tr>
            <th scope="col">标的</th>
            <th scope="col">最新有效结论</th>
            <th scope="col">预测 → 表现</th>
            <th scope="col">历史可靠性</th>
            <th scope="col">观点变化</th>
            <th scope="col">任务</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const preferredHorizon = item.preferred_validation?.horizon ?? 20;
            const stats = item.validation_stats.find(
              (candidate) => candidate.horizon === preferredHorizon,
            );
            const latestIsAnomalous = ANOMALOUS.has(item.latest_run.status);
            return (
              <tr key={item.instrument.id}>
                <td data-label="标的">
                  <Link
                    className="instrument-link"
                    href={`/instruments/${encodeURIComponent(item.instrument.ticker)}`}
                    aria-label={instrumentLabel(item)}
                  >
                    <span className="instrument-name">
                      {item.instrument.name ?? item.instrument.ticker}
                    </span>
                    <span className="instrument-symbol">
                      {item.instrument.ticker}
                      {item.instrument.exchange ? ` · ${item.instrument.exchange}` : ""}
                    </span>
                  </Link>
                  <span className="ledger-asset-type">{item.instrument.asset_type}</span>
                </td>
                <td data-label="最新有效结论">
                  {item.latest_decision && item.latest_successful_run ? (
                    <div className="ledger-decision">
                      <Link
                        href={`/runs/${item.latest_successful_run.id}`}
                        aria-label="查看最新有效结论"
                      >
                        <strong>{item.latest_decision.rating}</strong>
                      </Link>
                      <span>{item.latest_successful_run.analysis_date}</span>
                      <p title={item.latest_decision.executive_summary}>
                        {item.latest_decision.executive_summary}
                      </p>
                    </div>
                  ) : <span className="ledger-empty">尚无有效结论</span>}
                  {latestIsAnomalous ? (
                    <Link
                      className="ledger-anomaly"
                      href={`/runs/${item.latest_run.id}`}
                      aria-label={`最新任务${STATUS_LABELS[item.latest_run.status] ?? item.latest_run.status}`}
                    >
                      <span aria-hidden="true">!</span>
                      最新任务{STATUS_LABELS[item.latest_run.status] ?? item.latest_run.status}
                    </Link>
                  ) : (
                    <span className="ledger-latest-status">
                      最新任务：{STATUS_LABELS[item.latest_run.status] ?? item.latest_run.status}
                    </span>
                  )}
                </td>
                <td data-label="预测 → 表现">
                  {validationsVisible ? (
                    <p className="prediction-outcome">
                      {formatPredictionOutcome(item)}
                    </p>
                  ) : <span className="validation-permission">缺少表现验证读取权限</span>}
                </td>
                <td data-label="历史可靠性">
                  {validationsVisible ? (
                    <div className="ledger-reliability">
                      <strong>{preferredHorizon}D</strong>
                      <span>{reliabilityLabel(stats)}</span>
                    </div>
                  ) : <span aria-hidden="true">—</span>}
                </td>
                <td data-label="观点变化">
                  {ratingTransition(item.previous_rating, item.latest_decision?.rating)}
                </td>
                <td data-label="任务">
                  <div className="ledger-counts" aria-label={`共 ${item.run_counts.total} 次任务`}>
                    <span>成功 {item.run_counts.succeeded}</span>
                    {item.run_counts.active > 0 ? <span>运行 {item.run_counts.active}</span> : null}
                    {item.run_counts.queued > 0 ? <span>排队 {item.run_counts.queued}</span> : null}
                    {item.run_counts.anomalous > 0 ? (
                      <span className="ledger-counts__anomaly">异常 {item.run_counts.anomalous}</span>
                    ) : null}
                    <small>共 {item.run_counts.total}</small>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {items.length === 0 ? (
        <p className="table-empty">当前筛选条件下没有标的记录。</p>
      ) : null}
      <div className="pagination" aria-label="标的分页">
        <button type="button" onClick={onPrevious} disabled={!hasPrevious}>上一页</button>
        <button type="button" onClick={onNext} disabled={!hasNext}>下一页</button>
      </div>
    </div>
  );
}
