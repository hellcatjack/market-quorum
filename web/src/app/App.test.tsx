import { render, screen } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { App } from "./App";

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    let body: unknown;
    if (path.includes("/system/capacity")) {
      body = {
        admitted_or_running: 0,
        max_running_total: 2,
        hard_max_running_total: 3,
        queued: 0,
        oldest_queued_seconds: null,
        gateway_active_completions: 0,
        gateway_model: "gpt-5.6-sol",
        gateway_reasoning_effort: "xhigh",
        open_circuits: [],
        admission_allowed: true,
        admission_reasons: [],
      };
    } else if (path.includes("/assessments?")) {
      body = { items: [] };
    } else if (path.includes("/assessments/run-123/")) {
      body = path.endsWith("/decision")
        ? {
            run_id: "run-123",
            rating: "Hold",
            executive_summary: "摘要",
            investment_thesis: "逻辑",
            price_target: null,
            time_horizon: null,
            structured: {},
          }
        : [];
    } else if (path.endsWith("/assessments/run-123")) {
      body = {
        id: "run-123",
        request_id: "request-123",
        ticker: "SPCX",
        asset_type: "stock",
        analysis_date: "2026-07-25",
        status: "succeeded",
        attempt: 1,
        created_at: "2026-07-25T12:00:00Z",
        request_config: {},
        resolved_config: {},
        data_vendors: {},
        tool_vendors: {},
      };
    } else {
      body = {
        subject: "alice",
        display_name: "Alice",
        scopes: ["assessments:read"],
        roles: ["Viewer"],
      };
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
});

test("renders the Chinese management shell and all primary routes", async () => {
  const { hook } = memoryLocation({ path: "/" });
  render(
    <Router hook={hook}>
      <App />
    </Router>,
  );

  expect(screen.getByRole("link", { name: "总览" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "新建评估" })).toHaveAttribute("href", "/new");
  expect(screen.getByRole("link", { name: "系统状态" })).toHaveAttribute("href", "/system");
  expect(screen.getByRole("link", { name: "退出" })).toHaveAttribute(
    "href",
    "/oauth2/sign_out",
  );
  expect(await screen.findByText("Alice")).toBeInTheDocument();
  expect(screen.getByLabelText("排队任务")).toHaveTextContent("队列");
});

test("defines the run detail route", async () => {
  const { hook } = memoryLocation({ path: "/runs/run-123" });
  render(
    <Router hook={hook}>
      <App />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "SPCX 评估详情" })).toBeInTheDocument();
});
