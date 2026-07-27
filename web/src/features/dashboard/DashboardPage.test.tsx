import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { DashboardPage } from "./DashboardPage";

function response(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const capacity = {
  admitted_or_running: 2,
  max_running_total: 2,
  hard_max_running_total: 8,
  queued: 3,
  oldest_queued_seconds: 125,
  gateway_active_completions: 2,
  gateway_model: "gpt-5.6-sol",
  gateway_reasoning_effort: "xhigh",
  open_circuits: ["vendor:finnhub"],
  admission_allowed: false,
  admission_reasons: ["running_limit_reached"],
};

const runPage = {
  items: [
    {
      id: "run-queued",
      request_id: "request-1",
      ticker: "SPCX",
      asset_type: "stock",
      analysis_date: "2026-07-25",
      status: "queued",
      attempt: 1,
      created_at: "2026-07-25T12:00:00Z",
    },
    {
      id: "run-success",
      request_id: "request-2",
      ticker: "NVDA",
      instrument_name: "英伟达",
      exchange: "NASDAQ",
      asset_type: "stock",
      analysis_date: "2026-06-20",
      status: "succeeded",
      attempt: 1,
      created_at: "2026-06-20T11:00:00Z",
    },
  ],
  next_cursor: "next-run-page",
};

const overviewPage = {
  items: [
    {
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
        instrument_name: "英伟达",
        exchange: "NASDAQ",
        asset_type: "stock",
        analysis_date: "2026-07-25",
        status: "failed",
        attempt: 1,
        created_at: "2026-07-25T14:00:00Z",
      },
      latest_successful_run: {
        id: "run-success",
        request_id: "request-success",
        ticker: "NVDA",
        instrument_name: "英伟达",
        exchange: "NASDAQ",
        asset_type: "stock",
        analysis_date: "2026-06-20",
        status: "succeeded",
        attempt: 1,
        created_at: "2026-06-20T11:00:00Z",
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
        scheduled_for: "2026-06-20T11:00:00Z",
        matures_at: "2026-07-20T11:00:00Z",
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
        },
      ],
      run_counts: { total: 20, queued: 0, active: 0, succeeded: 15, anomalous: 2 },
    },
  ],
  next_cursor: "next-instrument-page",
  instrument_count: 38,
  run_counts: { total: 62, queued: 2, active: 4, succeeded: 53, anomalous: 3 },
  validations_visible: true,
};

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const location = memoryLocation({ path: "/" });
  render(
    <QueryClientProvider client={client}>
      <Router hook={location.hook}>
        <DashboardPage />
      </Router>
    </QueryClientProvider>,
  );
}

test("defaults to one instrument row and keeps the full task view available", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/system/capacity")) return response(capacity);
    if (url.includes("/instrument-overviews")) return response(overviewPage);
    if (url.includes("/assessments")) return response(runPage);
    throw new Error(`unexpected request: ${url}`);
  });
  renderDashboard();

  expect(await screen.findByText("gpt-5.6-sol · xhigh")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "标的台账" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByText("vendor:finnhub")).toBeInTheDocument();
  expect(screen.getByTestId("count-queued")).toHaveTextContent("2");
  expect(screen.getByTestId("count-active")).toHaveTextContent("4");
  expect(screen.getByTestId("count-succeeded")).toHaveTextContent("53");
  expect(screen.getByTestId("count-failed")).toHaveTextContent("3");

  const instrument = await screen.findByRole("link", { name: "英伟达 NVDA NASDAQ" });
  expect(instrument).toHaveAttribute("href", "/instruments/NVDA");
  expect(screen.getByText("Underweight ↓ → 20D -20.65% / Alpha -14.59% → 方向正确"))
    .toBeInTheDocument();
  expect(screen.getByText("2 次 · 样本不足")).toBeInTheDocument();
  expect(screen.getByText("Hold → Underweight")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "最新任务失败" })).toHaveAttribute(
    "href",
    "/runs/run-failed",
  );
  expect(screen.getByRole("link", { name: "查看最新有效结论" })).toHaveAttribute(
    "href",
    "/runs/run-success",
  );

  await userEvent.click(screen.getByRole("tab", { name: "任务记录" }));
  expect(screen.getByRole("tab", { name: "任务记录" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(await screen.findByRole("link", { name: "SPCX" })).toHaveAttribute(
    "href",
    "/runs/run-queued",
  );
  expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
});

test("explains validation permission limits without hiding conclusions", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/system/capacity")) return response(capacity);
    if (url.includes("/instrument-overviews")) {
      return response({
        ...overviewPage,
        validations_visible: false,
        items: overviewPage.items.map((item) => ({
          ...item,
          preferred_validation: null,
          validation_stats: [],
        })),
      });
    }
    if (url.includes("/assessments")) return response(runPage);
    throw new Error(`unexpected request: ${url}`);
  });
  renderDashboard();

  expect(await screen.findByText("缺少表现验证读取权限")).toBeInTheDocument();
  expect(screen.getByText("Underweight")).toBeInTheDocument();
});
