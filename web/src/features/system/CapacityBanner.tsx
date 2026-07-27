import type { Capacity } from "../../api/assessments";
import { useI18n } from "../../i18n/I18nProvider";
import { admissionReasonLabel, reasoningEffortLabel } from "../../i18n/domainLabels";

function duration(seconds: number | null, locale: "zh-CN" | "en-US"): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  if (locale === "en-US") return minutes > 0
    ? `${minutes}m ${String(remaining).padStart(2, "0")}s`
    : `${remaining}s`;
  return minutes > 0 ? `${minutes}分${String(remaining).padStart(2, "0")}秒` : `${remaining}秒`;
}

export function CapacityBanner({ capacity }: { capacity: Capacity }) {
  const { locale, t } = useI18n();
  const freeSlots = Math.max(0, capacity.max_running_total - capacity.admitted_or_running);
  const routes = capacity.model_routing;
  return (
    <section
      className={capacity.admission_allowed ? "capacity-banner" : "capacity-banner capacity-banner--blocked"}
      aria-label={t("系统容量")}
    >
      <div>
        <p className="eyebrow">{t("安全容量")}</p>
        <strong>
          {capacity.admission_allowed ? t("当前可准入") : t("当前任务将排队")} · {t("{count} 个空闲槽位", { count: freeSlots })}
        </strong>
        <span>
          {t("运行 {active}/{limit}，排队 {queued}", {
            active: capacity.admitted_or_running,
            limit: capacity.max_running_total,
            queued: capacity.queued,
          })}
        </span>
      </div>
      <div className="capacity-routes">
        <div className="capacity-gateway">
          <span>{t("快速分析路由")}</span>
          <strong>{routes?.fast.model ?? "—"} · {reasoningEffortLabel(routes?.fast.reasoning_effort ?? null, locale)}</strong>
        </div>
        <div className="capacity-gateway">
          <span>{t("关键裁决路由")}</span>
          <strong>{routes?.slow.model ?? "—"} · {reasoningEffortLabel(routes?.slow.reasoning_effort ?? null, locale)}</strong>
        </div>
      </div>
      {capacity.oldest_queued_seconds !== null && capacity.queued > 0 ? (
        <p className="capacity-wait">{t("最早任务已等待 {duration}", { duration: duration(capacity.oldest_queued_seconds, locale) })}</p>
      ) : null}
      {!capacity.admission_allowed ? (
        <p className="capacity-reasons" role="alert">
          <strong>{t("容量暂缓：")}</strong>
          {capacity.admission_reasons.map((reason) => admissionReasonLabel(reason, locale)).join(locale === "zh-CN" ? "、" : ", ") || t("系统正在保护性排队")}{locale === "zh-CN" ? "。" : ". "}{t("任务仍可进入受控队列。")}
        </p>
      ) : null}
    </section>
  );
}
