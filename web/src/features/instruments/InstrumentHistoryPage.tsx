import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useParams } from "wouter";

import type { InstrumentHistoryItem } from "../../api/records";
import { getInstrument, getInstrumentHistory } from "../../api/records";
import { formatPercent, ratingTransition } from "../dashboard/instrumentPresentation";
import { LocalTime } from "../runs/RunTimeline";
import { groupInstrumentHistory } from "./instrumentHistory";

const HORIZONS = [1, 5, 20] as const;

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

type InstrumentValidation = NonNullable<InstrumentHistoryItem["validations"]>[number];

function ValidationCell({
  horizon,
  validation,
}: {
  horizon: number;
  validation: InstrumentValidation | undefined;
}) {
  if (!validation) {
    return (
      <div className="history-validation history-validation--empty">
        <strong>{horizon}D</strong><span>待生成</span>
      </div>
    );
  }
  if (validation.status !== "completed") {
    const failed = validation.status === "failed" || validation.status === "unavailable";
    return (
      <div className={`history-validation history-validation--${failed ? "error" : "pending"}`}>
        <strong>{horizon}D</strong>
        <span>{failed ? "验证异常" : "待验证"}</span>
        {validation.matures_at && !failed ? (
          <small>预计 {validation.matures_at.slice(0, 10)}</small>
        ) : null}
      </div>
    );
  }
  return (
    <div className="history-validation history-validation--completed">
      <strong>{horizon}D</strong>
      <span>{formatPercent(validation.total_return)}</span>
      {validation.total_alpha !== null ? (
        <small>Alpha {formatPercent(validation.total_alpha)}</small>
      ) : null}
      <small>
        {validation.direction_correct === true
          ? "方向正确"
          : validation.direction_correct === false
            ? "方向错误"
            : "方向未判定"}
      </small>
      {validation.price_target_hit !== null ? (
        <small>{validation.price_target_hit ? "目标价命中" : "目标价未命中"}</small>
      ) : null}
    </div>
  );
}

function MemoryLabel({ item }: { item: InstrumentHistoryItem }) {
  if (item.memory_mode === "historical") {
    return <span className="history-memory">历史辅助 · {item.memory_source_count} 个来源</span>;
  }
  return <span className="history-memory">独立评估</span>;
}

export function InstrumentHistoryPage() {
  const { ticker = "" } = useParams<{ ticker: string }>();
  const normalized = ticker.toUpperCase();
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
  const events = useMemo(() => {
    const groups = groupInstrumentHistory(history.data ?? []);
    return groups.map((group, index) => {
      const previousRating = [...groups.slice(0, index)]
        .reverse()
        .find((candidate) => Boolean(candidate.primary.rating))
        ?.primary.rating ?? null;
      const transition = group.primary.rating
        ? ratingTransition(previousRating, group.primary.rating)
        : null;
      return { ...group, transition };
    });
  }, [history.data]);

  if (summary.isError || history.isError) {
    return <p className="page-shell page-warning" role="alert">无法读取该标的的历史评估。</p>;
  }
  return (
    <section className="page-shell instrument-page">
      <header className="instrument-hero">
        <div>
          <p className="eyebrow">标的档案 / 结论演化</p>
          <h1>{normalized} 历史评估</h1>
          <p>按研究发生顺序查看观点变化，并将每次预测与 1/5/20 日实际表现绑定。</p>
        </div>
        <dl>
          <div><dt>评估次数</dt><dd>{summary.data?.assessment_count ?? "—"}</dd></div>
          <div><dt>最新有效评级</dt><dd>{summary.data?.latest_rating ?? "—"}</dd></div>
          <div><dt>资产类型</dt><dd>{summary.data?.asset_types.join(" / ") ?? "—"}</dd></div>
        </dl>
      </header>

      <div className="instrument-history-heading">
        <div>
          <p className="eyebrow">由早到晚</p>
          <h2>结论与表现时间线</h2>
        </div>
        <span>{events.length} 个研究事件</span>
      </div>
      {history.isLoading ? <p className="table-empty" role="status">正在载入历史…</p> : null}
      <ol className="instrument-history-timeline" aria-label={`${normalized} 结论演化`}>
        {events.map(({ primary, priorAttempts, transition }) => (
          <li key={primary.run.request_id} data-testid="history-event">
            <article className={`instrument-history-event instrument-history-event--${primary.run.status}`}>
              <header>
                <div>
                  <time dateTime={primary.run.analysis_date}>{primary.run.analysis_date}</time>
                  <LocalTime value={primary.run.created_at} />
                </div>
                <span className={`run-status run-status--${primary.run.status === "succeeded" ? "success" : primary.run.status === "failed" || primary.run.status === "needs_attention" ? "danger" : "muted"}`}>
                  {STATUS_LABELS[primary.run.status] ?? primary.run.status}
                </span>
              </header>

              <div className="instrument-history-decision">
                <div>
                  <p className="eyebrow">投资判断</p>
                  <h3>{primary.rating ?? "尚无有效结论"}</h3>
                  {transition ? <strong className="rating-transition">{transition}</strong> : null}
                  <p>{primary.executive_summary ?? "本次运行未形成投资摘要。"}</p>
                  {primary.price_target !== null ? (
                    <span>目标价 {primary.price_target}</span>
                  ) : null}
                </div>
                <Link className="secondary-button" href={`/runs/${primary.run.id}`}>
                  查看评估详情
                </Link>
              </div>

              <div className="history-validation-grid" aria-label="表现验证">
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
                <span>{primary.gateway_model ?? "模型未知"} · {primary.gateway_reasoning_effort ?? "深度未知"}</span>
                <code title={primary.config_snapshot_sha256 ?? undefined}>
                  {primary.config_snapshot_sha256 ?? "无配置快照"}
                </code>
              </footer>

              {priorAttempts.length > 0 ? (
                <details className="history-prior-attempts">
                  <summary>其他尝试（{priorAttempts.length}）</summary>
                  <ul>
                    {priorAttempts.map((attempt) => (
                      <li key={attempt.run.id}>
                        <Link href={`/runs/${attempt.run.id}`}>
                          尝试 #{attempt.run.attempt} · {STATUS_LABELS[attempt.run.status] ?? attempt.run.status}
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
        <p className="table-empty">尚无历史评估。</p>
      ) : null}
    </section>
  );
}
