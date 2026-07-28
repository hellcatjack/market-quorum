import { Link } from "wouter";

import type { Integrity } from "../../api/records";
import { useI18n } from "../../i18n/I18nProvider";

function reasonLabel(reason: string, t: ReturnType<typeof useI18n>["t"]): string {
  const labels: Record<string, Parameters<typeof t>[0]> = {
    future_publication_exposed: "未来发布的财务报表",
    current_snapshot_exposed: "分析日之后的当前快照",
    macro_vintage_missing: "宏观数据未固定历史版本",
    future_dated_output: "输出包含分析日之后日期",
    publication_unverified: "财务报表公开时间未能确认",
    evidence_missing: "封存证据缺失",
    evidence_hash_mismatch: "封存证据哈希不一致",
    integrity_not_assessed: "尚未完成点时数据审计",
  };
  return reason in labels ? t(labels[reason]) : reason;
}

export function IntegrityPanel({
  integrity,
  canCleanReassess,
  cleanPending = false,
  onCleanReassess,
}: {
  integrity: Integrity | null;
  canCleanReassess: boolean;
  cleanPending?: boolean;
  onCleanReassess: () => void;
}) {
  const { t, formatDateTime } = useI18n();
  const reportedStatus = integrity?.status as string | undefined;
  const status = reportedStatus === "safe"
    || reportedStatus === "at_risk"
    || reportedStatus === "unknown"
    ? reportedStatus
    : "unassessed";
  const findings = integrity?.findings ?? [];
  const reasonCodes = integrity?.reason_codes ?? [];
  const copy = {
    safe: {
      title: t("点时数据已核验"),
      body: t("本次报告的数据时间边界通过当前策略核验。"),
    },
    at_risk: {
      title: t("点时数据存在风险"),
      body: t("该报告可能使用了分析日之后才能获得的信息，不应直接进入可信准确率或历史经验。"),
    },
    unknown: {
      title: t("点时数据无法确认"),
      body: t("现有封存证据不足以证明数据在分析日已经可用。"),
    },
    unassessed: {
      title: t("尚未完成点时数据审计"),
      body: t("该报告暂不进入可信准确率或历史经验。"),
    },
  }[status];
  const alert = status !== "safe";
  const badge = {
    safe: t("已核验"),
    at_risk: t("有风险"),
    unknown: t("待确认"),
    unassessed: t("未审计"),
  }[status];
  const findingReasons = new Set(
    findings.map((finding) => finding.reason_code),
  );
  const summaryReasons = reasonCodes.filter(
    (reason) => !findingReasons.has(reason),
  );

  return (
    <section className={`detail-panel detail-panel--wide integrity-panel integrity-panel--${status}`}>
      <div className="integrity-panel__heading">
        <div>
          <p className="eyebrow">{t("报告完整性")}</p>
          <div role={alert ? "alert" : undefined}>
            <h2>{copy.title}</h2>
            <p>{copy.body}</p>
          </div>
        </div>
        <span className={`integrity-badge integrity-badge--${status}`}>{badge}</span>
      </div>

      {summaryReasons.length ? (
        <div className="integrity-reasons" aria-label={t("完整性原因")}>
          {summaryReasons.map((reason) => (
            <span key={reason}>{reasonLabel(reason, t)}</span>
          ))}
        </div>
      ) : null}

      {findings.length ? (
        <details className="integrity-findings" data-testid="integrity-findings">
          <summary>{t("查看 {count} 条工具核验记录", { count: findings.length })}</summary>
          <ol>
            {findings.map((finding, index) => (
              <li key={`${finding.tool_name}-${finding.reason_code}-${index}`}>
                <div>
                  <strong>{reasonLabel(finding.reason_code, t)}</strong>
                  <code>{finding.tool_name}</code>
                </div>
                {Object.keys(finding.details ?? {}).length ? (
                  <pre>{JSON.stringify(finding.details, null, 2)}</pre>
                ) : null}
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      <div className="integrity-panel__footer">
        <small>
          {integrity?.checked_at
            ? t("策略 {policy} · 核验于 {date}", {
                policy: integrity.policy_version,
                date: formatDateTime(integrity.checked_at),
              })
            : t("策略 {policy}", { policy: integrity?.policy_version ?? "point-in-time.v1" })}
        </small>
        <div className="integrity-panel__actions">
          {integrity?.clean_reassessment_of_run_id ? (
            <Link href={`/runs/${integrity.clean_reassessment_of_run_id}`}>
              {t("查看修复来源")}
            </Link>
          ) : null}
          {integrity?.clean_reassessment_run_id ? (
            <Link href={`/runs/${integrity.clean_reassessment_run_id}`}>
              {t("查看干净重评估")}
            </Link>
          ) : null}
          {canCleanReassess && !integrity?.clean_reassessment_run_id ? (
            <button type="button" onClick={onCleanReassess} disabled={cleanPending}>
              {cleanPending ? t("正在创建干净重评估…") : t("创建干净重评估")}
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
