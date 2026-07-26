import type { RunEvent } from "../../api/events";
import type { Artifact } from "../../api/records";

export interface ArtifactBindings {
  byEventSequence: Map<number, Artifact[]>;
  unassociated: Artifact[];
}

interface EventMatcher {
  eventType: string;
  progressKey?: string;
}

const ARTIFACT_MATCHERS: Record<string, EventMatcher> = {
  run_config: { eventType: "assessment.starting" },
  report_2_fundamentals: {
    eventType: "runner.stage.running_analysts",
    progressKey: "fundamentals_report",
  },
  report_3_market: {
    eventType: "runner.stage.running_analysts",
    progressKey: "market_report",
  },
  report_4_news: {
    eventType: "runner.stage.running_analysts",
    progressKey: "news_report",
  },
  report_5_sentiment: {
    eventType: "runner.stage.running_analysts",
    progressKey: "sentiment_report",
  },
  report_7_bear: { eventType: "runner.stage.research_debate" },
  report_8_bull: { eventType: "runner.stage.research_debate" },
  report_9_manager: { eventType: "runner.stage.research_debate" },
  report_11_trader: { eventType: "runner.stage.trader_plan" },
  report_13_aggressive: { eventType: "runner.stage.risk_debate" },
  report_14_conservative: { eventType: "runner.stage.risk_debate" },
  report_15_neutral: { eventType: "runner.stage.risk_debate" },
  report_17_decision: { eventType: "runner.stage.portfolio_decision" },
  final_state: { eventType: "runner.artifact.final_state" },
  decision: { eventType: "runner.artifact.decision" },
  report_18_complete_report: { eventType: "runner.artifact.reports" },
  evidence: { eventType: "runner.artifact.evidence" },
  llm_interactions: { eventType: "runner.artifact.llm_interactions" },
  dependency_health: { eventType: "runner.result.assessment.completed" },
  manifest: { eventType: "assessment.succeeded" },
};

function timestamp(value: string): number | null {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function lastMatchingEvent(events: RunEvent[], matcher: EventMatcher): RunEvent | undefined {
  return [...events]
    .sort((left, right) => right.sequence - left.sequence)
    .find(
      (event) =>
        event.event_type === matcher.eventType &&
        (matcher.progressKey === undefined ||
          event.payload.progress_key === matcher.progressKey),
    );
}

function precedingEvent(artifact: Artifact, events: RunEvent[]): RunEvent | undefined {
  const archivedAt = timestamp(artifact.created_at);
  if (archivedAt === null) return undefined;
  return [...events]
    .filter((event) => {
      const eventTime = timestamp(event.created_at);
      return eventTime !== null && eventTime <= archivedAt;
    })
    .sort((left, right) => {
      const timeDifference = (timestamp(right.created_at) ?? 0) - (timestamp(left.created_at) ?? 0);
      return timeDifference || right.sequence - left.sequence;
    })[0];
}

function eventForArtifact(artifact: Artifact, events: RunEvent[]): RunEvent | undefined {
  const knownMatcher = ARTIFACT_MATCHERS[artifact.kind];
  if (knownMatcher) {
    const knownEvent = lastMatchingEvent(events, knownMatcher);
    if (knownEvent) return knownEvent;
  }

  const exactArtifactEvent = lastMatchingEvent(events, {
    eventType: `runner.artifact.${artifact.kind}`,
  });
  return exactArtifactEvent ?? precedingEvent(artifact, events);
}

export function bindArtifactsToEvents(
  artifacts: Artifact[],
  events: RunEvent[],
): ArtifactBindings {
  const bindings: ArtifactBindings = {
    byEventSequence: new Map<number, Artifact[]>(),
    unassociated: [],
  };

  for (const artifact of artifacts) {
    const event = eventForArtifact(artifact, events);
    if (!event) {
      bindings.unassociated.push(artifact);
      continue;
    }
    const bound = bindings.byEventSequence.get(event.sequence) ?? [];
    bound.push(artifact);
    bindings.byEventSequence.set(event.sequence, bound);
  }
  return bindings;
}
