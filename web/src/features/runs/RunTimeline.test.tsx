import { render, screen } from "@testing-library/react";

import { RunTimeline } from "./RunTimeline";

test("sorts research records chronologically and binds artifacts inside real events", () => {
  render(
    <RunTimeline
      steps={[
        {
          name: "portfolio_decision",
          status: "completed",
          attempt: 1,
          started_at: "2026-07-25T12:04:00Z",
          finished_at: "2026-07-25T12:05:00Z",
          error_code: null,
          summary: "形成结论",
        },
        {
          name: "running_analysts",
          status: "completed",
          attempt: 1,
          started_at: "2026-07-25T12:01:00Z",
          finished_at: "2026-07-25T12:03:00Z",
          error_code: null,
          summary: "分析完成",
        },
      ]}
      events={[
        {
          sequence: 1,
          event_type: "assessment.admitted",
          payload: { reason: "capacity_available" },
          created_at: "2026-07-25T12:00:30Z",
        },
        {
          sequence: 2,
          event_type: "runner.stage.portfolio_decision",
          payload: { progress_key: "final_trade_decision" },
          created_at: "2026-07-25T12:05:00Z",
        },
      ]}
      evidence={[
        {
          id: "evidence-1",
          source: "yfinance",
          tool_name: "get_stock_data",
          arguments: { ticker: "SPCX" },
          collected_at: "2026-07-25T12:02:00Z",
          effective_at: "2026-07-25T00:00:00Z",
          freshness: "fresh",
          content_hash: "abc123",
        },
        {
          id: "evidence-early",
          source: "request",
          tool_name: "load_config",
          arguments: {},
          collected_at: "2026-07-25T12:00:00Z",
          effective_at: null,
          freshness: null,
          content_hash: "early123",
        },
      ]}
      artifacts={[
        {
          id: "artifact-1",
          run_id: "run-123",
          kind: "report_17_decision",
          media_type: "text/markdown",
          size: 512,
          sha256: "def456",
          created_at: "2026-07-25T12:05:30Z",
        },
      ]}
      canReadArtifacts
    />,
  );

  expect(
    screen
      .getAllByTestId("timeline-entry")
      .map((node) => node.dataset.timelineId),
  ).toEqual([
    "evidence-evidence-early",
    "event-1",
    "step-running_analysts-1",
    "evidence-evidence-1",
    "step-portfolio_decision-1",
    "event-2",
  ]);
  expect(screen.getByTestId("evidence-evidence-early")).toHaveTextContent(
    "时间归属：分析师研究",
  );
  expect(screen.getByTestId("evidence-evidence-1")).toHaveTextContent(
    "时间归属：分析师研究",
  );
  expect(screen.getByTestId("event-2")).toHaveTextContent("产物 1");
  expect(screen.getByTestId("event-2")).toHaveTextContent("report_17_decision");
  expect(screen.queryByTestId("artifact-artifact-1")).not.toBeInTheDocument();
});

test("keeps events, evidence and bound artifacts compact until requested", () => {
  render(
    <RunTimeline
      steps={[]}
      events={[
        {
          sequence: 2,
          event_type: "runner.stage.running_analysts",
          payload: { progress_key: "news_report" },
          created_at: "2026-07-25T12:01:00Z",
        },
      ]}
      evidence={[
        {
          id: "evidence-2",
          source: "finnhub",
          tool_name: "get_news",
          arguments: { ticker: "SPCX" },
          collected_at: "2026-07-25T12:01:01Z",
          effective_at: null,
          freshness: "fresh",
          content_hash: "evidence-hash",
        },
      ]}
      artifacts={[
        {
          id: "artifact-2",
          run_id: "run-123",
          kind: "report_4_news",
          media_type: "text/markdown",
          size: 512,
          sha256: "artifact-hash",
          created_at: "2026-07-25T12:01:02Z",
        },
      ]}
      canReadArtifacts
    />,
  );

  for (const disclosure of screen.getAllByTestId("timeline-disclosure")) {
    expect(disclosure).not.toHaveAttribute("open");
  }
  expect(screen.getByTestId("evidence-evidence-2")).toHaveTextContent(
    "时间归属：未关联阶段",
  );
  const eventEntry = screen.getByTestId("event-2");
  expect(eventEntry).toHaveTextContent("产物 1");
  expect(eventEntry).toHaveTextContent("report_4_news");
  expect(eventEntry).toHaveTextContent("512 bytes");
  expect(screen.queryByTestId("artifact-artifact-2")).not.toBeInTheDocument();
});

test("shows artifacts without a matching event in one explicit fallback group", () => {
  render(
    <RunTimeline
      steps={[]}
      events={[]}
      evidence={[]}
      artifacts={[
        {
          id: "artifact-orphan",
          run_id: "run-123",
          kind: "orphan_report",
          media_type: "text/markdown",
          size: 128,
          sha256: "orphan-hash",
          created_at: "2026-07-25T12:01:02Z",
        },
      ]}
      canReadArtifacts
    />,
  );

  expect(screen.getByTestId("unassociated-artifacts")).toHaveTextContent("未关联产物");
  expect(screen.getByTestId("unassociated-artifacts")).toHaveTextContent("orphan_report");
  expect(screen.queryAllByTestId("timeline-entry")).toHaveLength(0);
});
