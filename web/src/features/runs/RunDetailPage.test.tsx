import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { Route, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { RunDetailPage } from "./RunDetailPage";

class SilentEventSource {
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close() {}
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("renders immutable metadata, evidence, artifacts and collaboration", async () => {
  vi.stubGlobal("EventSource", SilentEventSource);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.endsWith("/api/v1/me")) {
      return json({
        subject: "analyst",
        display_name: "分析员",
        scopes: ["assessments:read", "assessments:cancel", "assessments:review", "artifacts:read"],
        roles: ["Analyst"],
      });
    }
    if (path.endsWith("/steps")) {
      return json([{ name: "running_analysts", status: "completed", attempt: 1, started_at: "2026-07-25T12:01:00Z", finished_at: "2026-07-25T12:02:00Z", error_code: null, summary: "分析完成" }]);
    }
    if (path.endsWith("/decision")) {
      return json({ run_id: "run-123", rating: "Hold", executive_summary: "等待更好估值", investment_thesis: "增长与估值平衡", price_target: "31.50", time_horizon: "5 days", structured: {} });
    }
    if (path.endsWith("/evidence")) {
      return json([{ id: "evidence-1", source: "yfinance", tool_name: "get_stock_data", arguments: { ticker: "SPCX" }, collected_at: "2026-07-25T12:02:00Z", effective_at: "2026-07-25T00:00:00Z", freshness: "fresh", content_hash: "abc123" }]);
    }
    if (path.endsWith("/artifacts")) {
      return json([{ id: "artifact-1", run_id: "run-123", kind: "report_18_complete_report", media_type: "text/markdown", size: 512, sha256: "def456", created_at: "2026-07-25T12:03:00Z" }]);
    }
    if (path.endsWith("/reviews")) {
      return json([{ id: "review-1", run_id: "run-123", reviewer: "复核员", verdict: "approved", comment: "证据充分", created_at: "2026-07-25T13:00:00Z" }]);
    }
    if (path.endsWith("/comments")) return json([]);
    if (path.includes("/events?")) return json({ items: [] });
    return json({
      id: "run-123",
      request_id: "request-123",
      ticker: "SPCX",
      asset_type: "stock",
      analysis_date: "2026-07-25",
      status: "succeeded",
      attempt: 1,
      created_at: "2026-07-25T12:00:00Z",
      config_snapshot_sha256: "snapshot-sha",
      gateway_snapshot_id: "gateway-snapshot",
      gateway_model: "gpt-5.6-sol",
      gateway_reasoning_effort: "xhigh",
      root_commit: "root-commit",
      tradingagents_commit: "ta-commit",
      prompt_schema_version: "v1",
      request_config: { depth: "deep", language: "Chinese" },
      resolved_config: { debate_rounds: 3, risk_rounds: 3 },
      data_vendors: { market: "yfinance" },
      tool_vendors: {},
      memory: {
        mode: "historical",
        snapshot_sha256: "memory-snapshot-sha",
        sources: [{
          source_run_id: "00000000-0000-0000-0000-000000000777",
          validation_id: "00000000-0000-0000-0000-000000000778",
          analysis_date: "2026-07-01",
          exit_session: "2026-07-06",
          horizon: 5,
          rating: "Buy",
          raw_return: "0.05",
          alpha: "0.02",
          direction_correct: true,
          price_target_hit: false,
          content_sha256: "memory-entry-sha",
        }],
      },
    });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const location = memoryLocation({ path: "/runs/run-123" });
  render(
    <QueryClientProvider client={client}>
      <Router hook={location.hook}>
        <Route path="/runs/:runId"><RunDetailPage /></Route>
      </Router>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "SPCX 评估详情" })).toBeInTheDocument();
  expect(screen.getByText("gpt-5.6-sol")).toBeInTheDocument();
  expect(screen.getByText("xhigh")).toBeInTheDocument();
  expect(screen.getByText("snapshot-sha")).toBeInTheDocument();
  expect(screen.getByText("历史辅助")).toBeInTheDocument();
  expect(screen.getByText("历史经验 1 条")).toBeInTheDocument();
  expect(screen.getByText("2026-07-01 · 5 个交易日验证")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看来源评估" })).toHaveAttribute(
    "href",
    "/runs/00000000-0000-0000-0000-000000000777",
  );
  expect(screen.getByText("等待更好估值")).toBeInTheDocument();
  expect(screen.getByText("证据充分")).toBeInTheDocument();
  const decisionHeading = screen.getByRole("heading", { name: "投资结论" });
  const timelineHeading = screen.getByRole("heading", { name: "研究时间线" });
  expect(decisionHeading.closest("section")).toHaveClass("detail-panel--wide");
  expect(timelineHeading.closest("section")).toHaveClass("detail-panel--wide");
  expect(
    decisionHeading.compareDocumentPosition(timelineHeading)
      & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    screen.queryByRole("heading", { name: "证据与工具调用" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "产物文件" })).not.toBeInTheDocument();
  expect(screen.getByTestId("evidence-evidence-1")).toHaveTextContent("yfinance");
  const timelinePanel = timelineHeading.closest("section");
  if (!timelinePanel) throw new Error("research timeline panel is missing");
  const artifact = await within(timelinePanel).findByTestId(
    "bound-artifact-artifact-1",
  );
  expect(artifact).toHaveTextContent("report_18_complete_report");
  expect(
    within(artifact).getByRole("link", { name: "下载原文件", hidden: true }),
  ).toHaveAttribute("href", "/api/v1/artifacts/artifact-1");
  const decisionPanel = decisionHeading.closest("section");
  if (!decisionPanel) throw new Error("investment conclusion panel is missing");
  const completeReport = within(decisionPanel).getByTestId("artifact-preview-artifact-1");
  expect(completeReport).toHaveTextContent("完整评估报告");
  expect(completeReport).not.toHaveAttribute("open");
  for (const disclosure of screen.getAllByTestId("timeline-disclosure")) {
    expect(disclosure).not.toHaveAttribute("open");
  }
});
