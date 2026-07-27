import { translate } from "./I18nProvider";
import type { UiLocale } from "./I18nProvider";
import type { MessageKey } from "./messages";

const RUN_STATUS_KEYS: Record<string, MessageKey> = {
  queued: "排队中",
  admitted: "已准入",
  starting: "启动准备",
  running_analysts: "分析师研究",
  research_debate: "研究辩论",
  trader_plan: "交易方案",
  risk_debate: "风险辩论",
  portfolio_decision: "组合决策",
  finalizing: "结果归档",
  cancel_requested: "等待取消",
  cancelling: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  failed: "失败",
  needs_attention: "需要处理",
};

const STEP_STATUS_KEYS: Record<string, MessageKey> = {
  pending: "等待中",
  running: "进行中",
  succeeded: "已完成",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const PHASE_KEYS: Record<string, MessageKey> = {
  queued: "等待调度",
  admitted: "任务准入",
  starting: "启动准备",
  running_analysts: "分析师研究",
  research_debate: "研究辩论",
  trader_plan: "交易方案",
  risk_debate: "风险辩论",
  portfolio_decision: "组合决策",
  finalizing: "结果归档",
};

const ASSESSMENT_EVENT_KEYS: Record<string, MessageKey> = {
  "assessment.queued": "任务已排队",
  "assessment.admitted": "任务准入",
  "assessment.starting": "开始执行",
  "assessment.cancel_requested": "请求取消",
  "assessment.cancelling": "正在取消",
  "assessment.cancelled": "任务已取消",
  "assessment.succeeded": "评估完成",
  "assessment.failed": "评估失败",
  "assessment.needs_attention": "需要人工处理",
  "assessment.recovery": "恢复执行",
};

const ROUTE_KEYS: Record<string, MessageKey> = {
  fast: "快速分析路由",
  slow: "关键裁决路由",
  codex: "兼容默认路由",
};

const EFFORT_KEYS: Record<string, MessageKey> = {
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "很高",
  max: "最大",
  ultra: "极致",
};

const SYSTEM_STATUS_KEYS: Record<string, MessageKey> = {
  ok: "正常",
  healthy: "正常",
  idle: "空闲",
  closed: "关闭",
  open: "开启",
  half_open: "半开",
};

const ASSET_TYPE_KEYS: Record<string, MessageKey> = {
  stock: "股票",
  fund: "基金",
  crypto: "加密资产",
};

const OUTCOME_KEYS: Record<string, MessageKey> = {
  "尚无有效结论": "尚无有效结论",
  "待验证": "待验证",
  "验证异常": "验证异常",
  "方向正确": "方向正确",
  "方向错误": "方向错误",
  "方向未判定": "方向未判定",
  "目标价命中": "目标价命中",
  "目标价未命中": "目标价未命中",
};

const REVIEW_VERDICT_KEYS: Record<string, MessageKey> = {
  approved: "通过",
  changes_requested: "要求修改",
  rejected: "拒绝",
};

const FRESHNESS_KEYS: Record<string, MessageKey> = {
  fresh: "新鲜",
  stale: "过期",
};

const ADMISSION_REASON_KEYS: Record<string, MessageKey> = {
  run_capacity: "并发评估已达上限",
  running_limit_reached: "并发评估已达上限",
  gateway_capacity: "Gateway 活动请求已达上限",
  cpu: "CPU 使用率超过阈值",
  memory: "可用内存低于阈值",
  disk_gib: "可用磁盘容量低于阈值",
  disk_percent: "可用磁盘比例低于阈值",
  circuit_breaker: "依赖熔断器已开启",
};

export function runStatusLabel(status: string, locale: UiLocale): string {
  const key = RUN_STATUS_KEYS[status];
  return key
    ? translate(locale, key)
    : translate(locale, "未知状态（{code}）", { code: status });
}

export function stepStatusLabel(status: string, locale: UiLocale): string {
  const key = STEP_STATUS_KEYS[status];
  return key
    ? translate(locale, key)
    : translate(locale, "未知状态（{code}）", { code: status });
}

export function modelRouteLabel(route: string | null, locale: UiLocale): string {
  const code = route ?? "unknown";
  const key = ROUTE_KEYS[code];
  return key
    ? translate(locale, key)
    : translate(locale, "未知路由（{code}）", { code });
}

export function reasoningEffortLabel(effort: string | null, locale: UiLocale): string {
  const code = effort ?? "unknown";
  const key = EFFORT_KEYS[code];
  return key
    ? translate(locale, key)
    : translate(locale, "未知深度（{code}）", { code });
}

export function phaseLabel(phase: string, locale: UiLocale): string {
  const key = PHASE_KEYS[phase];
  return key ? translate(locale, key) : phase;
}

export function eventTypeLabel(eventType: string, locale: UiLocale): string {
  const exact = ASSESSMENT_EVENT_KEYS[eventType];
  if (exact) return translate(locale, exact);
  if (eventType === "runner.result.assessment.completed") return translate(locale, "分析完成");
  if (eventType === "runner.error.runner.failed") return translate(locale, "执行器失败");
  if (eventType.startsWith("runner.stage.")) {
    return translate(locale, "{phase}进展", {
      phase: phaseLabel(eventType.slice("runner.stage.".length), locale),
    });
  }
  if (eventType.startsWith("runner.artifact.")) {
    return translate(locale, "归档产物：{name}", {
      name: eventType.slice("runner.artifact.".length),
    });
  }
  return translate(locale, "未知事件（{code}）", { code: eventType });
}

export function systemStatusLabel(status: string, locale: UiLocale): string {
  const key = SYSTEM_STATUS_KEYS[status];
  return key
    ? translate(locale, key)
    : translate(locale, "未知状态（{code}）", { code: status });
}

export function assetTypeLabel(assetType: string, locale: UiLocale): string {
  const key = ASSET_TYPE_KEYS[assetType];
  return key ? translate(locale, key) : assetType;
}

export function outcomeLabel(outcome: string, locale: UiLocale): string {
  const key = OUTCOME_KEYS[outcome];
  return key ? translate(locale, key) : outcome;
}

export function reviewVerdictLabel(verdict: string, locale: UiLocale): string {
  const key = REVIEW_VERDICT_KEYS[verdict];
  return key ? translate(locale, key) : verdict;
}

export function freshnessLabel(freshness: string | null, locale: UiLocale): string {
  if (!freshness) return translate(locale, "未标注");
  const key = FRESHNESS_KEYS[freshness];
  return key ? translate(locale, key) : freshness;
}

export function admissionReasonLabel(reason: string, locale: UiLocale): string {
  const key = ADMISSION_REASON_KEYS[reason];
  return key ? translate(locale, key) : reason;
}
