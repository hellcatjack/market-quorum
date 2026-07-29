import type { AdmissionSummary } from "../../api/assessments";
import { useI18n } from "../../i18n/I18nProvider";

function duration(seconds: number | null, locale: "zh-CN" | "en-US"): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  if (locale === "en-US") {
    return minutes > 0
      ? `${minutes}m ${String(remaining).padStart(2, "0")}s`
      : `${remaining}s`;
  }
  return minutes > 0
    ? `${minutes}分${String(remaining).padStart(2, "0")}秒`
    : `${remaining}秒`;
}

export function AdmissionBanner({ summary }: { summary: AdmissionSummary }) {
  const { locale, t } = useI18n();
  const available = summary.admission === "immediate";
  const freeSlots = Math.max(0, summary.max_running - summary.running);
  const reason = summary.reason === "capacity_available"
    ? t("当前容量充足，新任务可直接准入。")
    : summary.reason === "capacity_busy"
      ? t("当前运行容量繁忙，新任务会安全排队。")
      : t("系统正在保护性暂缓，新任务会保留在队列中。")
  return (
    <section
      className={available ? "capacity-banner" : "capacity-banner capacity-banner--blocked"}
      aria-label={t("评估准入状态")}
    >
      <div>
        <p className="eyebrow">{t("安全容量")}</p>
        <strong>
          {available ? t("当前可准入") : t("当前任务将排队")} · {t("{count} 个空闲槽位", { count: freeSlots })}
        </strong>
        <span>
          {t("运行 {active}/{limit}，排队 {queued}", {
            active: summary.running,
            limit: summary.max_running,
            queued: summary.queued,
          })}
        </span>
      </div>
      {summary.oldest_queued_seconds !== null && summary.queued > 0 ? (
        <p className="capacity-wait">
          {t("最早任务已等待 {duration}", {
            duration: duration(summary.oldest_queued_seconds, locale),
          })}
        </p>
      ) : null}
      {!available ? (
        <p className="capacity-reasons" role="alert">
          <strong>{t("容量暂缓：")}</strong>
          {reason} {t("任务仍可进入受控队列。")}
        </p>
      ) : null}
    </section>
  );
}
