import type { InstrumentOverview } from "../../api/records";
import {
  formatPercent,
  formatPredictionOutcome,
  predictionOutcomeTokens,
  ratingDirection,
  ratingTransition,
  reliabilityLabel,
} from "./instrumentPresentation";

function overview(
  overrides: Partial<InstrumentOverview> = {},
): InstrumentOverview {
  return {
    instrument: {
      id: "instrument-nvda",
      ticker: "NVDA",
      name: "英伟达",
      exchange: "NASDAQ",
      asset_type: "stock",
    },
    latest_run: {
      id: "run-failed",
      request_id: "request-failed",
      ticker: "NVDA",
      asset_type: "stock",
      analysis_date: "2026-07-25",
      status: "failed",
      attempt: 1,
      created_at: "2026-07-25T12:00:00Z",
    },
    latest_successful_run: {
      id: "run-success",
      request_id: "request-success",
      ticker: "NVDA",
      asset_type: "stock",
      analysis_date: "2026-06-20",
      status: "succeeded",
      attempt: 1,
      created_at: "2026-06-20T12:00:00Z",
    },
    latest_decision: {
      run_id: "run-success",
      rating: "Underweight",
      executive_summary: "估值风险较高。",
      investment_thesis: "预计跑输基准。",
      price_target: "110",
      time_horizon: "20 trading days",
      structured: {},
    },
    previous_rating: "Hold",
    preferred_validation: {
      id: "validation-20",
      run_id: "run-success",
      horizon: 20,
      status: "completed",
      scheduled_for: "2026-06-20T12:00:00Z",
      matures_at: "2026-07-20T12:00:00Z",
      exit_session: "2026-07-20",
      total_return: "-0.2065",
      total_alpha: "-0.1459",
      direction_correct: true,
      price_target_hit: null,
      error_code: null,
    },
    validation_stats: [
      {
        horizon: 20,
        completed: 2,
        direction_observed: 2,
        direction_correct: 2,
        accuracy: "1",
        excluded_at_risk: 0,
        excluded_unknown: 0,
      },
    ],
    run_counts: { total: 3, waiting_for_data: 0, queued: 0, active: 0, succeeded: 2, anomalous: 1 },
    ...overrides,
  };
}

test("formats one forecast and realized outcome as a bound reading unit", () => {
  expect(formatPredictionOutcome(overview())).toBe(
    "Underweight ↓ → 20D -20.65% / Alpha -14.59% → 方向正确",
  );
});

test("projects forecast and outcome into compact semantic tokens", () => {
  expect(predictionOutcomeTokens(overview())).toEqual({
    rating: "Underweight",
    direction: "↓",
    horizon: "20D",
    performance: "-20.65%",
    alpha: "Alpha -14.59%",
    outcome: "方向正确",
    target: null,
    state: "completed",
  });
});

test("does not mislabel pending or failed validation as a wrong forecast", () => {
  const base = overview().preferred_validation!;
  expect(
    formatPredictionOutcome(
      overview({ preferred_validation: { ...base, status: "scheduled" } }),
    ),
  ).toBe("Underweight ↓ → 20D 待验证");
  expect(
    formatPredictionOutcome(
      overview({
        preferred_validation: { ...base, status: "failed", error_code: "provider_error" },
      }),
    ),
  ).toBe("Underweight ↓ → 20D 验证异常");
  expect(formatPredictionOutcome(overview({ latest_decision: null }))).toBe("尚无有效结论");
});

test("shows target outcome only when it was evaluated", () => {
  const base = overview().preferred_validation!;
  expect(
    formatPredictionOutcome(
      overview({ preferred_validation: { ...base, price_target_hit: false } }),
    ),
  ).toContain("目标价未命中");
});

test("labels small reliability samples and stable samples separately", () => {
  expect(
    reliabilityLabel({
      horizon: 20,
      completed: 2,
      direction_observed: 2,
      direction_correct: 2,
      accuracy: "1",
      excluded_at_risk: 0,
      excluded_unknown: 0,
    }),
  ).toBe("2 次 · 样本不足");
  expect(
    reliabilityLabel({
      horizon: 20,
      completed: 4,
      direction_observed: 4,
      direction_correct: 3,
      accuracy: "0.75",
      excluded_at_risk: 0,
      excluded_unknown: 0,
    }),
  ).toBe("3/4 · 75.0%");
  expect(reliabilityLabel(undefined)).toBe("尚无成熟样本");
});

test("normalizes rating directions, percentages and transitions", () => {
  expect(ratingDirection("Buy")).toBe("↑");
  expect(ratingDirection("Underweight")).toBe("↓");
  expect(ratingDirection("中性")).toBe("→");
  expect(ratingDirection("Review")).toBe("·");
  expect(formatPercent("0.03125")).toBe("+3.13%");
  expect(formatPercent(null)).toBe("—");
  expect(ratingTransition("Hold", "Underweight")).toBe("Hold → Underweight");
  expect(ratingTransition(null, "Hold")).toBe("首次结论 · Hold");
});
