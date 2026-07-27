import { Link } from "wouter";

import type { InstrumentOverview } from "../../api/records";
import {
  predictionOutcomeTokens,
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

function PredictionOutcome({ item }: { item: InstrumentOverview }) {
  const tokens = predictionOutcomeTokens(item);
  if (tokens.state === "empty") {
    return <span className="ledger-empty">{tokens.outcome}</span>;
  }
  const outcomeTone = tokens.state === "error" || tokens.outcome === "方向错误"
    ? "negative"
    : tokens.outcome === "方向正确"
      ? "positive"
      : "neutral";
  return (
    <div className={`prediction-outcome prediction-outcome--${tokens.state}`}>
      <span className="prediction-token prediction-token--direction">{tokens.direction}</span>
      <span className="prediction-separator" aria-hidden="true">·</span>
      {tokens.horizon ? <span className="prediction-token">{tokens.horizon}</span> : null}
      {tokens.performance ? (
        <strong className="prediction-token">{tokens.performance}</strong>
      ) : null}
      {tokens.alpha ? <span className="prediction-token">{tokens.alpha}</span> : null}
      {tokens.state === "completed" ? (
        <span className="prediction-separator" aria-hidden="true">→</span>
      ) : null}
      <span className={`prediction-token prediction-token--outcome prediction-token--${outcomeTone}`}>
        {tokens.outcome}
      </span>
      {tokens.target ? <span className="prediction-token">{tokens.target}</span> : null}
    </div>
  );
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
        <colgroup>
          <col className="ledger-column-identity" />
          <col className="ledger-column-main" />
          <col className="ledger-column-signals" />
          <col className="ledger-column-operations" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">标的</th>
            <th scope="col">结论与表现</th>
            <th scope="col">可靠性与变化</th>
            <th scope="col">运行</th>
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
                <td data-label="结论与表现">
                  <div className="ledger-main">
                    {item.latest_decision && item.latest_successful_run ? (
                      <div className="ledger-decision">
                        <Link
                          href={`/runs/${item.latest_successful_run.id}`}
                          aria-label="查看最新有效结论"
                        >
                          <strong>{item.latest_decision.rating}</strong>
                        </Link>
                        <span>{item.latest_successful_run.analysis_date}</span>
                      </div>
                    ) : null}
                    {validationsVisible ? (
                      <PredictionOutcome item={item} />
                    ) : item.latest_decision ? (
                      <span className="validation-permission">缺少表现验证读取权限</span>
                    ) : <span className="ledger-empty">尚无有效结论</span>}
                  </div>
                </td>
                <td data-label="可靠性与变化">
                  <div className="ledger-signal-stack">
                    {validationsVisible ? (
                      <div className="ledger-reliability">
                        <strong>{preferredHorizon}D</strong>
                        <span>{reliabilityLabel(stats)}</span>
                      </div>
                    ) : <span aria-hidden="true">—</span>}
                    <span className="ledger-rating-transition">
                      {ratingTransition(item.previous_rating, item.latest_decision?.rating)}
                    </span>
                  </div>
                </td>
                <td data-label="运行">
                  <div className="ledger-operation">
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
                    <div className="ledger-counts" aria-label={`共 ${item.run_counts.total} 次任务`}>
                      <span>成功 <strong>{item.run_counts.succeeded}</strong></span>
                      {item.run_counts.active > 0 ? <span>运行 <strong>{item.run_counts.active}</strong></span> : null}
                      {item.run_counts.queued > 0 ? <span>排队 <strong>{item.run_counts.queued}</strong></span> : null}
                      {item.run_counts.anomalous > 0 ? (
                        <span className="ledger-counts__anomaly">异常 <strong>{item.run_counts.anomalous}</strong></span>
                      ) : null}
                      <small>共 {item.run_counts.total}</small>
                    </div>
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
