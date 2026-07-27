import { Link } from "wouter";

import type { InstrumentOverview } from "../../api/records";
import { useI18n } from "../../i18n/I18nProvider";
import { assetTypeLabel, outcomeLabel, runStatusLabel } from "../../i18n/domainLabels";
import {
  predictionOutcomeTokens,
  ratingTransition,
  reliabilityLabel,
} from "./instrumentPresentation";

const ANOMALOUS = new Set(["failed", "needs_attention"]);

function instrumentLabel(item: InstrumentOverview): string {
  return [item.instrument.name ?? item.instrument.ticker, item.instrument.ticker, item.instrument.exchange]
    .filter((value, index, values) => Boolean(value) && values.indexOf(value) === index)
    .join(" ");
}

function PredictionOutcome({ item }: { item: InstrumentOverview }) {
  const { locale } = useI18n();
  const tokens = predictionOutcomeTokens(item);
  if (tokens.state === "empty") {
    return <span className="ledger-empty">{outcomeLabel(tokens.outcome, locale)}</span>;
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
        {outcomeLabel(tokens.outcome, locale)}
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
  const { locale, t } = useI18n();
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
            <th scope="col">{t("标的")}</th>
            <th scope="col">{t("结论与表现")}</th>
            <th scope="col">{t("可靠性与变化")}</th>
            <th scope="col">{t("运行")}</th>
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
                <td data-label={t("标的")}>
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
                  <span className="ledger-asset-type">{assetTypeLabel(item.instrument.asset_type, locale)}</span>
                </td>
                <td data-label={t("结论与表现")}>
                  <div className="ledger-main">
                    {item.latest_decision && item.latest_successful_run ? (
                      <div className="ledger-decision">
                        <Link
                          href={`/runs/${item.latest_successful_run.id}`}
                          aria-label={t("查看最新有效结论")}
                        >
                          <strong>{item.latest_decision.rating}</strong>
                        </Link>
                        <span>{item.latest_successful_run.analysis_date}</span>
                      </div>
                    ) : null}
                    {validationsVisible ? (
                      <PredictionOutcome item={item} />
                    ) : item.latest_decision ? (
                      <span className="validation-permission">{t("缺少表现验证读取权限")}</span>
                    ) : <span className="ledger-empty">{t("尚无有效结论")}</span>}
                  </div>
                </td>
                <td data-label={t("可靠性与变化")}>
                  <div className="ledger-signal-stack">
                    {validationsVisible ? (
                      <div className="ledger-reliability">
                        <strong>{preferredHorizon}D</strong>
                        <span>{reliabilityLabel(stats, locale)}</span>
                      </div>
                    ) : <span aria-hidden="true">—</span>}
                    <span className="ledger-rating-transition">
                      {ratingTransition(item.previous_rating, item.latest_decision?.rating, locale)}
                    </span>
                  </div>
                </td>
                <td data-label={t("运行")}>
                  <div className="ledger-operation">
                    {latestIsAnomalous ? (
                      <Link
                        className="ledger-anomaly"
                        href={`/runs/${item.latest_run.id}`}
                        aria-label={t("最新任务{status}", { status: runStatusLabel(item.latest_run.status, locale) })}
                      >
                        <span aria-hidden="true">!</span>
                        {t("最新任务{status}", { status: runStatusLabel(item.latest_run.status, locale) })}
                      </Link>
                    ) : (
                      <span className="ledger-latest-status">
                        {t("最新任务：{status}", { status: runStatusLabel(item.latest_run.status, locale) })}
                      </span>
                    )}
                    <div className="ledger-counts" aria-label={t("共 {count} 次任务", { count: item.run_counts.total })}>
                      <span>{t("成功")} <strong>{item.run_counts.succeeded}</strong></span>
                      {item.run_counts.active > 0 ? <span>{t("运行")} <strong>{item.run_counts.active}</strong></span> : null}
                      {item.run_counts.queued > 0 ? <span>{t("排队")} <strong>{item.run_counts.queued}</strong></span> : null}
                      {item.run_counts.anomalous > 0 ? (
                        <span className="ledger-counts__anomaly">{t("异常")} <strong>{item.run_counts.anomalous}</strong></span>
                      ) : null}
                      <small>{t("共 {count}", { count: item.run_counts.total })}</small>
                    </div>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {items.length === 0 ? (
        <p className="table-empty">{t("当前筛选条件下没有标的记录。")}</p>
      ) : null}
      <div className="pagination" aria-label={t("标的分页")}>
        <button type="button" onClick={onPrevious} disabled={!hasPrevious}>{t("上一页")}</button>
        <button type="button" onClick={onNext} disabled={!hasNext}>{t("下一页")}</button>
      </div>
    </div>
  );
}
