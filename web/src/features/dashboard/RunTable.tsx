import { Link } from "wouter";

import type { AssessmentRun } from "../../api/assessments";
import { useI18n } from "../../i18n/I18nProvider";
import { assetTypeLabel, runStatusLabel } from "../../i18n/domainLabels";
import { LocalTime } from "../runs/RunTimeline";

const STATUS: Record<string, { symbol: string; tone: string }> = {
  waiting_for_data: { symbol: "◷", tone: "muted" },
  queued: { symbol: "◷", tone: "warning" }, admitted: { symbol: "→", tone: "active" },
  starting: { symbol: "→", tone: "active" }, running_analysts: { symbol: "↻", tone: "active" },
  research_debate: { symbol: "↻", tone: "active" }, trader_plan: { symbol: "↻", tone: "active" },
  risk_debate: { symbol: "↻", tone: "active" }, portfolio_decision: { symbol: "↻", tone: "active" },
  finalizing: { symbol: "↻", tone: "active" }, succeeded: { symbol: "✓", tone: "success" },
  failed: { symbol: "!", tone: "danger" }, cancel_requested: { symbol: "◷", tone: "warning" },
  cancelling: { symbol: "◷", tone: "warning" }, cancelled: { symbol: "×", tone: "muted" },
  needs_attention: { symbol: "!", tone: "danger" },
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
  const { locale, t } = useI18n();
  return (
    <div className="run-table-wrap panel">
      <table className="run-table">
        <thead>
          <tr>
            <th scope="col">{t("标的")}</th>
            <th scope="col">{t("状态")}</th>
            <th scope="col">{t("分析日期")}</th>
            <th scope="col">{t("资产")}</th>
            <th scope="col">{t("尝试")}</th>
            <th scope="col">{t("创建时间")}</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const status = STATUS[run.status] ?? { symbol: "·", tone: "muted" };
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
                    <span aria-hidden="true">{status.symbol}</span> {runStatusLabel(run.status, locale)}
                  </span>
                </td>
                <td className="market-number">{run.analysis_date}</td>
                <td>{assetTypeLabel(run.asset_type, locale)}</td>
                <td className="market-number">#{run.attempt}</td>
                <td><LocalTime value={run.created_at} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {runs.length === 0 ? <p className="table-empty">{t("当前筛选条件下没有评估记录。")}</p> : null}
      <div className="pagination" aria-label={t("评估分页")}>
        <button type="button" onClick={onPrevious} disabled={!hasPrevious}>{t("上一页")}</button>
        <button type="button" onClick={onNext} disabled={!hasNext}>{t("下一页")}</button>
      </div>
    </div>
  );
}
