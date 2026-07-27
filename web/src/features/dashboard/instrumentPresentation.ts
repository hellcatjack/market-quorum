import type { InstrumentOverview } from "../../api/records";

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

export function formatPredictionOutcome(overview: InstrumentOverview): string {
  const decision = overview.latest_decision;
  if (!decision) return "尚无有效结论";
  const forecast = `${decision.rating} ${ratingDirection(decision.rating)}`;
  const validation = overview.preferred_validation;
  if (!validation) return `${forecast} → 待验证`;
  const horizon = `${validation.horizon}D`;
  if (validation.status === "failed" || validation.status === "unavailable") {
    return `${forecast} → ${horizon} 验证异常`;
  }
  if (validation.status !== "completed") return `${forecast} → ${horizon} 待验证`;

  const performance = formatPercent(validation.total_return);
  const alpha = validation.total_alpha === null
    ? ""
    : ` / Alpha ${formatPercent(validation.total_alpha)}`;
  const direction = validation.direction_correct === true
    ? "方向正确"
    : validation.direction_correct === false
      ? "方向错误"
      : "方向未判定";
  const target = validation.price_target_hit === true
    ? " · 目标价命中"
    : validation.price_target_hit === false
      ? " · 目标价未命中"
      : "";
  return `${forecast} → ${horizon} ${performance}${alpha} → ${direction}${target}`;
}

export function reliabilityLabel(stats: ValidationStats | undefined): string {
  if (!stats || stats.completed === 0) return "尚无成熟样本";
  if (stats.completed < 3) return `${stats.completed} 次 · 样本不足`;
  if (stats.direction_observed === 0 || stats.accuracy === null) {
    return `${stats.completed} 次 · 方向待判定`;
  }
  return `${stats.direction_correct}/${stats.direction_observed} · ${(
    Number(stats.accuracy) * 100
  ).toFixed(1)}%`;
}

export function ratingTransition(
  previous: string | null | undefined,
  current: string | null | undefined,
): string {
  if (!current) return "—";
  if (!previous) return `首次结论 · ${current}`;
  if (previous === current) return `维持 ${current}`;
  return `${previous} → ${current}`;
}
