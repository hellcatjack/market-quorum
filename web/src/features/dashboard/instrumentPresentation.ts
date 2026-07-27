import type { InstrumentOverview } from "../../api/records";
import type { UiLocale } from "../../i18n/I18nProvider";

type ValidationStats = InstrumentOverview["validation_stats"][number];

const BULLISH = ["strong buy", "buy", "overweight", "outperform", "positive", "买入", "增持"];
const BEARISH = ["strong sell", "sell", "underweight", "underperform", "negative", "卖出", "减持"];
const NEUTRAL = ["hold", "neutral", "market perform", "equal weight", "中性", "持有"];

export function ratingDirection(rating: string | null | undefined): string {
  const normalized = rating?.trim().toLocaleLowerCase() ?? "";
  if (BULLISH.some((value) => normalized.includes(value))) return "↑";
  if (BEARISH.some((value) => normalized.includes(value))) return "↓";
  if (NEUTRAL.some((value) => normalized.includes(value))) return "→";
  return "·";
}

export function formatPercent(value: string | null | undefined): string {
  if (value === null || value === undefined || value.trim() === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const percentage = number * 100;
  return `${percentage > 0 ? "+" : ""}${percentage.toFixed(2)}%`;
}

export interface PredictionOutcomeTokens {
  rating: string | null;
  direction: string | null;
  horizon: string | null;
  performance: string | null;
  alpha: string | null;
  outcome: string;
  target: string | null;
  state: "completed" | "pending" | "error" | "empty";
}

export function predictionOutcomeTokens(
  overview: InstrumentOverview,
): PredictionOutcomeTokens {
  const decision = overview.latest_decision;
  if (!decision) {
    return {
      rating: null,
      direction: null,
      horizon: null,
      performance: null,
      alpha: null,
      outcome: "尚无有效结论",
      target: null,
      state: "empty",
    };
  }
  const base = {
    rating: decision.rating,
    direction: ratingDirection(decision.rating),
  };
  const validation = overview.preferred_validation;
  if (!validation) {
    return {
      ...base,
      horizon: null,
      performance: null,
      alpha: null,
      outcome: "待验证",
      target: null,
      state: "pending",
    };
  }
  const horizon = `${validation.horizon}D`;
  if (validation.status === "failed" || validation.status === "unavailable") {
    return {
      ...base,
      horizon,
      performance: null,
      alpha: null,
      outcome: "验证异常",
      target: null,
      state: "error",
    };
  }
  if (validation.status !== "completed") {
    return {
      ...base,
      horizon,
      performance: null,
      alpha: null,
      outcome: "待验证",
      target: null,
      state: "pending",
    };
  }

  const performance = formatPercent(validation.total_return);
  const alpha = validation.total_alpha === null
    ? null
    : `Alpha ${formatPercent(validation.total_alpha)}`;
  const outcome = validation.direction_correct === true
    ? "方向正确"
    : validation.direction_correct === false
      ? "方向错误"
      : "方向未判定";
  const target = validation.price_target_hit === true
    ? "目标价命中"
    : validation.price_target_hit === false
      ? "目标价未命中"
      : null;
  return {
    ...base,
    horizon,
    performance,
    alpha,
    outcome,
    target,
    state: "completed",
  };
}

export function formatPredictionOutcome(overview: InstrumentOverview): string {
  const tokens = predictionOutcomeTokens(overview);
  if (tokens.state === "empty") return tokens.outcome;
  const forecast = `${tokens.rating} ${tokens.direction}`;
  if (tokens.state !== "completed") {
    return `${forecast} → ${tokens.horizon ? `${tokens.horizon} ` : ""}${tokens.outcome}`;
  }
  const alpha = tokens.alpha ? ` / ${tokens.alpha}` : "";
  const target = tokens.target ? ` · ${tokens.target}` : "";
  return `${forecast} → ${tokens.horizon} ${tokens.performance}${alpha} → ${tokens.outcome}${target}`;
}

export function reliabilityLabel(stats: ValidationStats | undefined, locale: UiLocale = "zh-CN"): string {
  if (!stats || stats.completed === 0) return locale === "zh-CN" ? "尚无成熟样本" : "No mature samples";
  if (stats.completed < 3) return locale === "zh-CN" ? `${stats.completed} 次 · 样本不足` : `${stats.completed} · Insufficient sample`;
  if (stats.direction_observed === 0 || stats.accuracy === null) {
    return locale === "zh-CN" ? `${stats.completed} 次 · 方向待判定` : `${stats.completed} · Direction pending`;
  }
  return `${stats.direction_correct}/${stats.direction_observed} · ${(
    Number(stats.accuracy) * 100
  ).toFixed(1)}%`;
}

export function ratingTransition(
  previous: string | null | undefined,
  current: string | null | undefined,
  locale: UiLocale = "zh-CN",
): string {
  if (!current) return "—";
  if (!previous) return locale === "zh-CN" ? `首次结论 · ${current}` : `First conclusion · ${current}`;
  if (previous === current) return locale === "zh-CN" ? `维持 ${current}` : `Maintained ${current}`;
  return `${previous} → ${current}`;
}
