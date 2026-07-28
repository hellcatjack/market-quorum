import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { RunDetailPage } from "./RunDetailPage";

vi.mock("./ValidationChart", () => ({
  ValidationChart: ({ instrumentTicker, benchmarkTicker }: {
    instrumentTicker: string;
    benchmarkTicker: string;
  }) => (
    <div data-testid="validation-chart">
      {instrumentTicker} 对比 {benchmarkTicker}
    </div>
  ),
}));

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
        scopes: ["assessments:read", "assessments:cancel", "assessments:review", "artifacts:read", "validations:read"],
        roles: ["Analyst"],
      });
    }
    if (path.endsWith("/steps")) {
      return json([{ name: "running_analysts", status: "completed", attempt: 1, started_at: "2026-07-25T12:01:00Z", finished_at: "2026-07-25T12:02:00Z", error_code: null, summary: "分析完成" }]);
    }
    if (path.endsWith("/decision")) {
      return json({ run_id: "run-123", rating: "Hold", executive_summary: "等待更好估值", investment_thesis: "增长与估值平衡", price_target: "31.50", time_horizon: "5 days", structured: {} });
    }
    if (path.endsWith("/integrity")) {
      return json({
        run_id: "run-123",
        policy_version: "point-in-time.v1",
        status: "safe",
        audit_mode: "live",
        temporal_scope: "contemporaneous",
        analysis_date: "2026-07-25",
        checked_at: "2026-07-25T12:03:00Z",
        reason_codes: ["live_current_snapshot"],
        findings: [],
        input_fingerprint: "a".repeat(64),
      });
    }
    if (path.endsWith("/evidence")) {
      return json([{ id: "evidence-1", source: "yfinance", tool_name: "get_stock_data", arguments: { ticker: "SPCX" }, collected_at: "2026-07-25T12:02:00Z", effective_at: "2026-07-25T00:00:00Z", freshness: "fresh", content_hash: "abc123" }]);
    }
    if (path.endsWith("/llm-interactions")) {
      return json({
        items: [{
          sequence: 1,
          route: "slow",
          model_alias: "codex-slow",
          physical_model: "gpt-5.6-sol",
          reasoning_effort: "high",
          status: "completed",
          started_at: "2026-07-25T12:02:10Z",
          completed_at: "2026-07-25T12:02:14Z",
          duration_ms: 4000,
          error_code: null,
        }],
        source: "sealed",
        complete: true,
      });
    }
    if (path.endsWith("/artifacts")) {
      return json([
        { id: "artifact-1", run_id: "run-123", kind: "report_18_complete_report", media_type: "text/markdown", size: 512, sha256: "def456", created_at: "2026-07-25T12:03:00Z" },
        { id: "artifact-validation-20", run_id: "run-123", kind: "validation_20_prices", media_type: "application/json", size: 2048, sha256: "a".repeat(64), created_at: "2026-07-26T12:00:00Z" },
      ]);
    }
    if (path.endsWith("/validations")) {
      return json([{
        id: "validation-20",
        run_id: "run-123",
        horizon: 20,
        status: "completed",
        scheduled_for: "2026-07-25T00:00:00Z",
        observed_at: "2026-07-26T12:00:00Z",
        raw_return: "0.0842",
        benchmark_return: "0.0217",
        alpha: "0.0625",
        max_adverse_excursion: "-0.0310",
        max_favorable_excursion: "0.1020",
        trigger_results: {
          rating: "Buy",
          direction: "bullish",
          direction_correct: true,
          price_target_hit: false,
          entry_price: "100",
          exit_price: "108.42",
          entry_session: "2026-07-01",
          exit_session: "2026-07-21",
        },
        data_artifact_id: "artifact-validation-20",
        error_code: null,
        calculation_version: "validation.v1",
      }]);
    }
    if (path.endsWith("/api/v1/artifacts/artifact-validation-20")) {
      const sessions = Array.from({ length: 21 }, (_, index) =>
        `2026-07-${String(index + 1).padStart(2, "0")}`,
      );
      const series = (ticker: string, start: number) => {
        const prices = sessions.map((_, index) => start + index);
        return {
          ticker,
          currency: "USD",
          sessions,
          open: prices,
          high: prices.map((value) => value + 1),
          low: prices.map((value) => value - 1),
          close: prices,
          adjusted_close: prices,
          source: "yfinance",
          collected_at: "2026-07-26T12:00:00Z",
        };
      };
      return json({ instrument: series("SPCX", 100), benchmark: series("SPY", 200) });
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
      exchange: "NMS",
      asset_type: "stock",
      analysis_date: "2026-07-25",
      status: "succeeded",
      attempt: 1,
      created_at: "2026-07-25T12:00:00Z",
      config_snapshot_sha256: "snapshot-sha",
      gateway_snapshot_id: "gateway-snapshot",
      gateway_model: "gpt-5.6-sol",
      gateway_reasoning_effort: "xhigh",
      gateway_fast_model: "gpt-5.6-terra",
      gateway_fast_reasoning_effort: "medium",
      gateway_slow_model: "gpt-5.6-sol",
      gateway_slow_reasoning_effort: "high",
      model_routing_snapshot_id: "routing-snapshot",
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
  expect(screen.queryByRole("button", { name: "删除评估" })).not.toBeInTheDocument();
  expect(screen.getByText("快速分析路由")).toBeInTheDocument();
  expect(screen.getByText("gpt-5.6-terra · 中")).toBeInTheDocument();
  expect(screen.getAllByText("关键裁决路由")).toHaveLength(2);
  expect(screen.getByText("gpt-5.6-sol · 高")).toBeInTheDocument();
  expect(screen.queryByText("Gateway 模型")).not.toBeInTheDocument();
  expect(await screen.findByTestId("llm-1")).toHaveTextContent("关键裁决路由");
  expect(screen.getByTestId("llm-1")).toHaveTextContent("已完成");
  expect(screen.getByText("snapshot-sha")).toBeInTheDocument();
  expect(screen.getByText("历史辅助")).toBeInTheDocument();
  expect(screen.getByText("历史经验 1 条")).toBeInTheDocument();
  expect(screen.getByText("2026-07-01 · 5 个交易日验证")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看来源评估" })).toHaveAttribute(
    "href",
    "/runs/00000000-0000-0000-0000-000000000777",
  );
  expect(screen.getByText("等待更好估值")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "点时数据已核验" })).toBeInTheDocument();
  expect(screen.getByText("证据充分")).toBeInTheDocument();
  const decisionHeading = screen.getByRole("heading", { name: "投资结论" });
  const timelineHeading = screen.getByRole("heading", { name: "研究时间线" });
  expect(decisionHeading.closest("section")).toHaveClass("detail-panel--wide");
  expect(timelineHeading.closest("section")).toHaveClass("detail-panel--wide");
  expect(
    decisionHeading.compareDocumentPosition(timelineHeading)
      & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  const validationHeading = await screen.findByRole("heading", { name: "表现验证" });
  expect(validationHeading.closest("section")).toHaveClass("detail-panel--wide");
  expect(
    timelineHeading.compareDocumentPosition(validationHeading)
      & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.queryByText(/验证引擎将在下一阶段写入结果/)).not.toBeInTheDocument();
  expect(await screen.findByTestId("validation-chart")).toHaveTextContent("SPCX 对比 SPY");
  expect(screen.getByText(/共有 21 个价格节点/)).toBeInTheDocument();
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

test("admin confirms a terminal assessment deletion and returns to overview", async () => {
  vi.stubGlobal("EventSource", SilentEventSource);
  const requests: Array<{ path: string; method: string }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    requests.push({ path, method });
    if (method === "DELETE") return new Response(null, { status: 204 });
    if (path.endsWith("/api/v1/me")) {
      return json({
        subject: "admin",
        display_name: "管理员",
        scopes: ["assessments:read", "assessments:admin"],
        roles: ["Admin"],
      });
    }
    if (path.endsWith("/decision")) return json({}, 404);
    if (path.endsWith("/integrity")) {
      return json({
        run_id: "run-delete",
        policy_version: "point-in-time.v1",
        status: "safe",
        audit_mode: "live",
        temporal_scope: "contemporaneous",
        analysis_date: "2026-07-25",
        checked_at: "2026-07-25T12:03:00Z",
        reason_codes: [],
        findings: [],
        input_fingerprint: "a".repeat(64),
      });
    }
    if (path.endsWith("/llm-interactions")) {
      return json({ items: [], source: "sealed", complete: true });
    }
    if (
      path.endsWith("/steps")
      || path.endsWith("/evidence")
      || path.endsWith("/artifacts")
      || path.endsWith("/validations")
      || path.endsWith("/reviews")
      || path.endsWith("/comments")
    ) return json([]);
    if (path.includes("/events?")) return json({ items: [] });
    return json({
      id: "run-delete",
      request_id: "request-delete",
      ticker: "NVDA",
      exchange: "NASDAQ",
      asset_type: "stock",
      analysis_date: "2026-07-25",
      status: "succeeded",
      attempt: 1,
      created_at: "2026-07-25T12:00:00Z",
      memory: { mode: "independent", snapshot_sha256: null, sources: [] },
      request_config: {},
      resolved_config: {},
      data_vendors: {},
      tool_vendors: {},
    });
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const location = memoryLocation({ path: "/runs/run-delete" });
  render(
    <QueryClientProvider client={queryClient}>
      <Router hook={location.hook}>
        <Route path="/runs/:runId"><RunDetailPage /></Route>
        <Route path="/"><p>评估总览已返回</p></Route>
      </Router>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();

  expect(await screen.findByRole("heading", { name: "NVDA 评估详情" })).toBeInTheDocument();
  const deleteButton = await screen.findByRole("button", { name: "删除评估" });
  await user.click(deleteButton);
  const dialog = screen.getByRole("dialog", { name: "永久删除 NVDA 的这次评估？" });
  expect(dialog).toHaveTextContent("2026-07-25");
  await user.click(within(dialog).getByRole("button", { name: "暂不删除" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  await user.click(deleteButton);
  const confirmDelete = within(screen.getByRole("dialog")).getByRole(
    "button",
    { name: "确认永久删除" },
  );
  act(() => {
    confirmDelete.click();
    confirmDelete.click();
  });

  expect(await screen.findByText("评估总览已返回")).toBeInTheDocument();
  expect(
    requests.filter(({ path, method }) => method === "DELETE" && path.endsWith("/assessments/run-delete")),
  ).toHaveLength(1);
});
