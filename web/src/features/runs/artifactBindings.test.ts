import type { RunEvent } from "../../api/events";
import type { Artifact } from "../../api/records";

import { bindArtifactsToEvents } from "./artifactBindings";

function event(
  sequence: number,
  eventType: string,
  progressKey?: string,
): RunEvent {
  return {
    sequence,
    event_type: eventType,
    payload: progressKey ? { progress_key: progressKey } : {},
    created_at: `2026-07-26T00:${String(sequence).padStart(2, "0")}:00Z`,
  };
}

function artifact(kind: string, createdAt = "2026-07-26T01:00:00Z"): Artifact {
  return {
    id: `artifact-${kind}`,
    run_id: "run-123",
    kind,
    media_type: kind.startsWith("report_") ? "text/markdown" : "application/json",
    size: 512,
    sha256: `sha-${kind}`,
    created_at: createdAt,
  };
}

const EVENTS: RunEvent[] = [
  event(1, "assessment.queued"),
  event(2, "assessment.admitted"),
  event(3, "assessment.starting"),
  event(4, "runner.stage.running_analysts", "market_report"),
  event(5, "runner.stage.running_analysts", "sentiment_report"),
  event(6, "runner.stage.running_analysts", "news_report"),
  event(7, "runner.stage.running_analysts", "fundamentals_report"),
  event(8, "runner.stage.research_debate", "investment_debate_state"),
  event(14, "runner.stage.research_debate", "investment_debate_state"),
  event(15, "runner.stage.trader_plan", "trader_investment_plan"),
  event(16, "runner.stage.risk_debate", "risk_debate_state"),
  event(24, "runner.stage.risk_debate", "risk_debate_state"),
  event(25, "runner.stage.portfolio_decision", "final_trade_decision"),
  event(26, "runner.artifact.final_state"),
  event(27, "runner.artifact.decision"),
  event(28, "runner.artifact.reports"),
  event(29, "runner.artifact.evidence"),
  event(30, "runner.artifact.llm_interactions"),
  event(31, "runner.result.assessment.completed"),
  event(32, "assessment.succeeded"),
];

test("binds every persisted TradingAgents artifact to its real business event", () => {
  const expected = new Map<string, number>([
    ["run_config", 3],
    ["memory_context", 3],
    ["report_2_fundamentals", 7],
    ["report_3_market", 4],
    ["report_4_news", 6],
    ["report_5_sentiment", 5],
    ["report_7_bear", 14],
    ["report_8_bull", 14],
    ["report_9_manager", 14],
    ["report_11_trader", 15],
    ["report_13_aggressive", 24],
    ["report_14_conservative", 24],
    ["report_15_neutral", 24],
    ["report_17_decision", 25],
    ["final_state", 26],
    ["decision", 27],
    ["report_18_complete_report", 28],
    ["evidence", 29],
    ["llm_interactions", 30],
    ["dependency_health", 31],
    ["manifest", 32],
  ]);
  const artifacts = [...expected.keys()].map((kind) => artifact(kind));

  const result = bindArtifactsToEvents(artifacts, EVENTS);

  expect(result.unassociated).toEqual([]);
  for (const [kind, sequence] of expected) {
    expect(result.byEventSequence.get(sequence)?.map((item) => item.kind)).toContain(kind);
  }
});

test("uses an exact artifact event before the preceding-event fallback", () => {
  const events = [
    event(1, "assessment.starting"),
    event(2, "runner.artifact.custom_trace"),
    event(3, "runner.result.assessment.completed"),
  ];

  const result = bindArtifactsToEvents([artifact("custom_trace")], events);

  expect(result.byEventSequence.get(2)?.[0].kind).toBe("custom_trace");
});

test("falls back to the latest real event not later than the archive time", () => {
  const events = [
    { ...event(1, "assessment.starting"), created_at: "2026-07-26T00:01:00Z" },
    { ...event(2, "runner.stage.unknown"), created_at: "2026-07-26T00:02:00Z" },
    { ...event(3, "assessment.succeeded"), created_at: "2026-07-26T00:04:00Z" },
  ];

  const result = bindArtifactsToEvents([
    artifact("future_extension", "2026-07-26T00:03:00Z"),
  ], events);

  expect(result.byEventSequence.get(2)?.[0].kind).toBe("future_extension");
});

test("keeps an artifact explicitly unassociated when no real event can own it", () => {
  const result = bindArtifactsToEvents([artifact("orphan")], []);

  expect(result.byEventSequence.size).toBe(0);
  expect(result.unassociated.map((item) => item.kind)).toEqual(["orphan"]);
});
