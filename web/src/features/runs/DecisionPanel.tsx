import type { Artifact, Decision } from "../../api/records";
import { ArtifactPreview } from "./ArtifactPreview";

interface DecisionPanelProps {
  decision: Decision | null;
  completeReport: Artifact | null;
}

export function DecisionPanel({ decision, completeReport }: DecisionPanelProps) {
  return (
    <section className="detail-panel detail-panel--wide decision-panel">
      <div className="section-heading"><p className="eyebrow">最终判断</p><h2>投资结论</h2></div>
      {decision ? (
        <>
          <div className="decision-rating"><span>评级</span><strong>{decision.rating}</strong></div>
          <p className="decision-summary decision-content--unabridged">
            {decision.executive_summary}
          </p>
          <dl className="decision-facts">
            <div>
              <dt>目标价</dt>
              <dd className="decision-content--unabridged">
                {decision.price_target ?? "—"}
              </dd>
            </div>
            <div>
              <dt>时间范围</dt>
              <dd className="decision-content--unabridged">
                {decision.time_horizon ?? "—"}
              </dd>
            </div>
          </dl>
          <h3>投资逻辑</h3>
          <p className="decision-thesis decision-content--unabridged">
            {decision.investment_thesis}
          </p>
        </>
      ) : <p className="section-empty">结论尚未生成。</p>}
      {completeReport ? (
        <div className="decision-report">
          <ArtifactPreview artifact={completeReport} title="完整评估报告" />
        </div>
      ) : null}
    </section>
  );
}
