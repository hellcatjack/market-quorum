import type { Artifact, Decision } from "../../api/records";
import { ArtifactPreview } from "./ArtifactPreview";
import { useI18n } from "../../i18n/I18nProvider";

interface DecisionPanelProps {
  decision: Decision | null;
  completeReport: Artifact | null;
}

export function DecisionPanel({ decision, completeReport }: DecisionPanelProps) {
  const { t } = useI18n();
  return (
    <section className="detail-panel detail-panel--wide decision-panel">
      <div className="section-heading"><p className="eyebrow">{t("最终判断")}</p><h2>{t("投资结论")}</h2></div>
      {decision ? (
        <>
          <div className="decision-rating"><span>{t("评级")}</span><strong>{decision.rating}</strong></div>
          <p className="decision-summary decision-content--unabridged">
            {decision.executive_summary}
          </p>
          <dl className="decision-facts">
            <div>
              <dt>{t("目标价")}</dt>
              <dd className="decision-content--unabridged">
                {decision.price_target ?? "—"}
              </dd>
            </div>
            <div>
              <dt>{t("时间范围")}</dt>
              <dd className="decision-content--unabridged">
                {decision.time_horizon ?? "—"}
              </dd>
            </div>
          </dl>
          <h3>{t("投资逻辑")}</h3>
          <p className="decision-thesis decision-content--unabridged">
            {decision.investment_thesis}
          </p>
        </>
      ) : <p className="section-empty">{t("结论尚未生成。")}</p>}
      {completeReport ? (
        <div className="decision-report">
          <ArtifactPreview artifact={completeReport} title={t("完整评估报告")} />
        </div>
      ) : null}
    </section>
  );
}
