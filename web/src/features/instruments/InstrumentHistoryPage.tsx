import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "wouter";

import type { InstrumentHistoryItem } from "../../api/records";
import { getInstrument, getInstrumentHistory } from "../../api/records";
import { formatPercent } from "../dashboard/instrumentPresentation";
import { LocalTime } from "../runs/RunTimeline";
import { useI18n } from "../../i18n/I18nProvider";
import { reasoningEffortLabel } from "../../i18n/domainLabels";
import { assetTypeLabel, runStatusLabel } from "../../i18n/domainLabels";
import {
  orderInstrumentHistory,
  projectInstrumentHistory,
  type InstrumentHistoryOrder,
} from "./instrumentHistory";

const HORIZONS = [1, 5, 20] as const;

type InstrumentValidation = NonNullable<InstrumentHistoryItem["validations"]>[number];

function ValidationCell({
  horizon,
  validation,
}: {
  horizon: number;
  validation: InstrumentValidation | undefined;
}) {
  const { t } = useI18n();
  if (!validation) {
    return (
      <div className="history-validation history-validation--empty">
        <strong>{horizon}D</strong><span>{t("待生成")}</span>
      </div>
    );
  }
  if (validation.status !== "completed") {
    const failed = validation.status === "failed" || validation.status === "unavailable";
    return (
      <div className={`history-validation history-validation--${failed ? "error" : "pending"}`}>
        <strong>{horizon}D</strong>
        <span>{failed ? t("验证异常") : t("待验证")}</span>
        {validation.matures_at && !failed ? (
          <small>{t("预计 {date}", { date: validation.matures_at.slice(0, 10) })}</small>
        ) : null}
      </div>
    );
  }
  const directionState = validation.direction_correct === true
    ? "correct"
    : validation.direction_correct === false
      ? "incorrect"
      : "unjudged";
  const directionLabel = validation.direction_correct === true
    ? t("方向正确")
    : validation.direction_correct === false
      ? t("方向错误")
      : t("方向未判定");
  return (
    <div className={`history-validation history-validation--completed history-validation--direction-${directionState}`}>
      <strong>{horizon}D</strong>
      <span>{formatPercent(validation.total_return)}</span>
      {validation.total_alpha !== null ? (
        <small>Alpha {formatPercent(validation.total_alpha)}</small>
      ) : null}
      <small className={`history-validation__direction history-validation__direction--${directionState}`}>
        {directionLabel}
      </small>
      {validation.price_target_hit !== null ? (
        <small>{validation.price_target_hit ? t("目标价命中") : t("目标价未命中")}</small>
      ) : null}
    </div>
  );
}

function MemoryLabel({ item }: { item: InstrumentHistoryItem }) {
  const { t } = useI18n();
  if (item.memory_mode === "historical") {
    return <span className="history-memory">{t("{label} · {count} 个来源", { label: t("历史辅助"), count: item.memory_source_count })}</span>;
  }
  return <span className="history-memory">{t("独立评估")}</span>;
}

export function InstrumentHistoryPage() {
  const { locale, t } = useI18n();
  const { ticker = "" } = useParams<{ ticker: string }>();
  const normalized = ticker.toUpperCase();
  const [order, setOrder] = useState<InstrumentHistoryOrder>("newest");
  const summary = useQuery({
    queryKey: ["instrument", normalized],
    queryFn: () => getInstrument(normalized),
    enabled: Boolean(normalized),
    retry: false,
  });
  const history = useQuery({
    queryKey: ["instrument-history", normalized],
    queryFn: () => getInstrumentHistory(normalized),
    enabled: Boolean(normalized),
    retry: false,
  });
  const projectedEvents = useMemo(
    () => projectInstrumentHistory(history.data ?? [], locale),
    [history.data, locale],
  );
  const events = useMemo(
    () => orderInstrumentHistory(projectedEvents, order),
    [order, projectedEvents],
  );
  const displayName = summary.data?.name ?? normalized;
  const instrumentIdentity = [
    summary.data?.name ? normalized : null,
    summary.data?.exchange,
  ].filter(Boolean).join(" · ");

  if (summary.isError || history.isError) {
    return <p className="page-shell page-warning" role="alert">{t("无法读取该标的的历史评估。")}</p>;
  }
  return (
    <section className="page-shell instrument-page">
      <header className="instrument-hero">
        <div>
          <p className="eyebrow">{t("标的档案 / 结论演化")}</p>
          <h1>{displayName}</h1>
          {instrumentIdentity ? (
            <p className="instrument-hero__identity">{instrumentIdentity}</p>
          ) : null}
          <p>{t("默认优先查看最新研究，也可切换为审计顺序，并将每次预测与 1/5/20 日实际表现绑定。")}</p>
        </div>
        <dl>
          <div><dt>{t("评估次数")}</dt><dd>{summary.data?.assessment_count ?? "—"}</dd></div>
          <div><dt>{t("最新有效评级")}</dt><dd>{summary.data?.latest_rating ?? "—"}</dd></div>
          <div><dt>{t("资产类型")}</dt><dd>{summary.data?.asset_types.map((item) => assetTypeLabel(item, locale)).join(" / ") ?? "—"}</dd></div>
        </dl>
      </header>

      <div className="instrument-history-heading">
        <div>
          <p className="eyebrow">{order === "newest" ? t("由新到旧") : t("由旧到新")}</p>
          <h2>{t("结论与表现时间线")}</h2>
        </div>
        <div className="history-heading-actions">
          <span>{t("{count} 个研究事件", { count: events.length })}</span>
          <div className="history-order-control" aria-label={t("研究事件排序")}>
            <button
              type="button"
              aria-pressed={order === "newest"}
              onClick={() => setOrder("newest")}
            >
              {t("最新在前")}
            </button>
            <button
              type="button"
              aria-pressed={order === "oldest"}
              onClick={() => setOrder("oldest")}
            >
              {t("最早在前")}
            </button>
          </div>
        </div>
      </div>
      {history.isLoading ? <p className="table-empty" role="status">{t("正在载入历史…")}</p> : null}
      <ol className="instrument-history-timeline" aria-label={t("{ticker} 结论演化", { ticker: normalized })}>
        {events.map(({ primary, priorAttempts, transition }) => (
          <li key={primary.run.request_id} data-testid="history-event">
            <article className={`instrument-history-event instrument-history-event--${primary.run.status}`}>
              <header>
                <div>
                  <time dateTime={primary.run.analysis_date}>{primary.run.analysis_date}</time>
                  <LocalTime value={primary.run.created_at} />
                </div>
                <span className={`run-status run-status--${primary.run.status === "succeeded" ? "success" : primary.run.status === "failed" || primary.run.status === "needs_attention" ? "danger" : "muted"}`}>
                  {runStatusLabel(primary.run.status, locale)}
                </span>
              </header>

              <div className="instrument-history-decision">
                <div>
                  <p className="eyebrow">{t("投资判断")}</p>
                  <h3>{primary.rating ?? t("尚无有效结论")}</h3>
                  {transition ? <strong className="rating-transition">{transition}</strong> : null}
                  <p>{primary.executive_summary ?? t("本次运行未形成投资摘要。")}</p>
                  {primary.price_target !== null ? (
                    <span>{t("目标价")} {primary.price_target}</span>
                  ) : null}
                </div>
                <Link className="secondary-button" href={`/runs/${primary.run.id}`}>
                  {t("查看评估详情")}
                </Link>
              </div>

              <div className="history-validation-grid" aria-label={t("表现验证")}>
                {HORIZONS.map((horizon) => (
                  <ValidationCell
                    key={horizon}
                    horizon={horizon}
                    validation={(primary.validations ?? []).find(
                      (candidate) => candidate.horizon === horizon,
                    )}
                  />
                ))}
              </div>

              <footer className="instrument-history-meta">
                <MemoryLabel item={primary} />
                {primary.gateway_fast_model || primary.gateway_slow_model ? (
                  <span className="history-model-routes">
                    <span>{t("快速分析路由")}：{primary.gateway_fast_model ?? "—"} · {reasoningEffortLabel(primary.gateway_fast_reasoning_effort ?? null, locale)}</span>
                    <span>{t("关键裁决路由")}：{primary.gateway_slow_model ?? "—"} · {reasoningEffortLabel(primary.gateway_slow_reasoning_effort ?? null, locale)}</span>
                  </span>
                ) : (
                  <span>{t("旧版单路由")}：{primary.gateway_model ?? t("模型未知")} · {reasoningEffortLabel(primary.gateway_reasoning_effort ?? null, locale)}</span>
                )}
                <code title={primary.config_snapshot_sha256 ?? undefined}>
                  {primary.config_snapshot_sha256 ?? t("无配置快照")}
                </code>
              </footer>

              {priorAttempts.length > 0 ? (
                <details className="history-prior-attempts">
                  <summary>{t("其他尝试（{count}）", { count: priorAttempts.length })}</summary>
                  <ul>
                    {priorAttempts.map((attempt) => (
                      <li key={attempt.run.id}>
                        <Link href={`/runs/${attempt.run.id}`}>
                          {t("尝试 #{attempt} · {status}", { attempt: attempt.run.attempt, status: runStatusLabel(attempt.run.status, locale) })}
                        </Link>
                        <LocalTime value={attempt.run.created_at} />
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </article>
          </li>
        ))}
      </ol>
      {!history.isLoading && events.length === 0 ? (
        <p className="table-empty">{t("尚无历史评估。")}</p>
      ) : null}
    </section>
  );
}
