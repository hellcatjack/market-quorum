import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { DashboardPage } from "./DashboardPage";

function response(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

test("shows capacity, status counts, circuit warnings and linked runs", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/system/capacity")) {
      return response({
        admitted_or_running: 2,
        max_running_total: 2,
        hard_max_running_total: 3,
        queued: 3,
        oldest_queued_seconds: 125,
        gateway_active_completions: 2,
        gateway_model: "gpt-5.6-sol",
        gateway_reasoning_effort: "xhigh",
        open_circuits: ["vendor:finnhub"],
        admission_allowed: false,
        admission_reasons: ["running_limit_reached"],
      });
    }
    return response({
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
          id: "run-active",
          request_id: "request-2",
          ticker: "TSLA",
          asset_type: "stock",
          analysis_date: "2026-07-25",
          status: "running_analysts",
          attempt: 1,
          created_at: "2026-07-25T11:00:00Z",
        },
        {
          id: "run-success",
          request_id: "request-3",
          ticker: "NVDA",
          instrument_name: "英伟达",
          exchange: "NASDAQ",
          asset_type: "stock",
          analysis_date: "2026-07-24",
          status: "succeeded",
          attempt: 1,
          created_at: "2026-07-24T11:00:00Z",
        },
        {
          id: "run-failed",
          request_id: "request-4",
          ticker: "GLD",
          asset_type: "fund",
          analysis_date: "2026-07-24",
          status: "failed",
          attempt: 1,
          created_at: "2026-07-24T10:00:00Z",
        },
      ],
      next_cursor: "next-page",
    });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const location = memoryLocation({ path: "/" });
  render(
    <QueryClientProvider client={client}>
      <Router hook={location.hook}>
        <DashboardPage />
      </Router>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("gpt-5.6-sol · xhigh")).toBeInTheDocument();
  expect(screen.getByText("vendor:finnhub")).toBeInTheDocument();
  expect(screen.getByText("最早任务已等待 2分05秒")).toBeInTheDocument();
  expect(screen.getByTestId("count-queued")).toHaveTextContent("1");
  expect(screen.getByTestId("count-active")).toHaveTextContent("1");
  expect(screen.getByTestId("count-succeeded")).toHaveTextContent("1");
  expect(screen.getByTestId("count-failed")).toHaveTextContent("1");
  expect(screen.getByRole("link", { name: "SPCX" })).toHaveAttribute("href", "/runs/run-queued");
  const localizedInstrument = screen.getByRole("link", { name: "英伟达 NVDA NASDAQ" });
  expect(localizedInstrument).toHaveAttribute("href", "/runs/run-success");
  expect(localizedInstrument).toHaveTextContent("英伟达");
  expect(localizedInstrument).toHaveTextContent("NVDA · NASDAQ");
  expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
});
