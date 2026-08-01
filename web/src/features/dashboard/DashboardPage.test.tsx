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
  running: 2,
  max_running: 2,
  waiting_for_data: 5,
  oldest_waiting_seconds: 240,
  queued: 3,
  oldest_queued_seconds: 125,
  admission: "queued",
  reason: "capacity_busy",
};

const runPage = {
  items: [
    {
      id: "run-waiting",
      request_id: "request-0",
      ticker: "NEW",
      asset_type: "stock",
      analysis_date: "2026-07-25",
      status: "waiting_for_data",
      attempt: 1,
      created_at: "2026-07-25T11:00:00Z",
    },
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
      instrument_name: "NVIDIA CORP",
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
        name: "NVIDIA CORP",
        exchange: "NASDAQ",
        asset_type: "stock",
      },
      latest_run: {
        id: "run-failed",
        request_id: "request-failed",
        ticker: "NVDA",
        instrument_name: "NVIDIA CORP",
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
        instrument_name: "NVIDIA CORP",
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
          excluded_at_risk: 2,
          excluded_unknown: 1,
        },
      ],
      run_counts: { total: 20, queued: 0, active: 0, succeeded: 15, anomalous: 2 },
    },
  ],
  next_cursor: "next-instrument-page",
  instrument_count: 38,
  run_counts: { total: 69, waiting_for_data: 7, queued: 2, active: 4, succeeded: 53, anomalous: 3 },
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
  const requests: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    requests.push(url);
    if (url.includes("/assessments/admission-summary")) return response(capacity);
    if (url.includes("/instrument-overviews")) return response(overviewPage);
    if (url.includes("/assessments")) return response(runPage);
    throw new Error(`unexpected request: ${url}`);
  });
  renderDashboard();

  expect(await screen.findByText("运行 2/2，排队 3")).toBeInTheDocument();
  expect(requests).toContain("/api/v1/assessments/admission-summary");
  expect(requests.some((url) => url.includes("/system/"))).toBe(false);
  expect(screen.getByRole("tab", { name: "标的台账" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByText("vendor:finnhub")).not.toBeInTheDocument();
  expect(screen.getByTestId("count-queued")).toHaveTextContent("2");
  expect(screen.getByTestId("count-waiting")).toHaveTextContent("7");
  expect(screen.getByTestId("count-active")).toHaveTextContent("4");
  expect(screen.getByTestId("count-succeeded")).toHaveTextContent("53");
  expect(screen.getByTestId("count-failed")).toHaveTextContent("3");

  const instrument = await screen.findByRole("link", { name: "NVIDIA CORP NVDA NASDAQ" });
  expect(instrument).toHaveAttribute("href", "/instruments/NVDA");
  const ledgerRow = instrument.closest("tr");
  expect(ledgerRow).not.toBeNull();
  for (const cell of ledgerRow?.querySelectorAll("td") ?? []) {
    const lines = cell.querySelector(":scope > .ledger-lines");
    expect(lines).not.toBeNull();
    expect(lines?.querySelectorAll(":scope > .ledger-line")).toHaveLength(2);
  }
  expect(ledgerRow).toHaveTextContent("NVIDIA CORP");
  expect(ledgerRow).toHaveTextContent("NVDA · NASDAQ");
  expect(ledgerRow).toHaveTextContent("股票");
  expect(ledgerRow).toHaveTextContent("成功 15");
  expect(ledgerRow).toHaveTextContent("异常 2");
  expect(ledgerRow).toHaveTextContent("共 20");
  expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
    "标的",
    "结论与表现",
    "可靠性与变化",
    "运行",
  ]);
  expect(screen.getByText("Underweight")).toBeInTheDocument();
  expect(screen.getAllByText("20D")).toHaveLength(2);
  expect(screen.getByText("-20.65%")).toBeInTheDocument();
  expect(screen.getByText("Alpha -14.59%")).toBeInTheDocument();
  expect(screen.getByText("方向正确")).toHaveClass("prediction-token--positive");
  expect(screen.queryByText("估值风险较高。")).not.toBeInTheDocument();
  expect(screen.getByText("2 次 · 样本不足")).toBeInTheDocument();
  expect(screen.getByText("排除 2 条风险 / 1 条未知")).toBeInTheDocument();
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
    if (url.includes("/assessments/admission-summary")) return response(capacity);
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
