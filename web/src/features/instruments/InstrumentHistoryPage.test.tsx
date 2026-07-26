import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { Route, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { InstrumentHistoryPage } from "./InstrumentHistoryPage";

test("compares historical ratings, model metadata and validation placeholders", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const history = String(input).endsWith("/history");
    const body = history
      ? [{
          run: { id: "run-1", request_id: "request-1", ticker: "SPCX", asset_type: "stock", analysis_date: "2026-07-25", status: "succeeded", attempt: 1, created_at: "2026-07-25T12:00:00Z" },
          rating: "Hold",
          executive_summary: "等待更好估值",
          price_target: "31.50",
          gateway_model: "gpt-5.6-sol",
          gateway_reasoning_effort: "xhigh",
          config_snapshot_sha256: "config-sha",
          validation_outcome: null,
        }]
      : { ticker: "SPCX", asset_types: ["stock"], assessment_count: 1, latest_run_id: "run-1", latest_rating: "Hold", latest_created_at: "2026-07-25T12:00:00Z" };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const location = memoryLocation({ path: "/instruments/SPCX" });
  render(<QueryClientProvider client={client}><Router hook={location.hook}><Route path="/instruments/:ticker"><InstrumentHistoryPage /></Route></Router></QueryClientProvider>);

  expect(await screen.findByRole("heading", { name: "SPCX 历史评估" })).toBeInTheDocument();
  expect(await screen.findByText("等待更好估值")).toBeInTheDocument();
  expect(screen.getByText("gpt-5.6-sol / xhigh")).toBeInTheDocument();
  expect(screen.getByText("config-sha")).toBeInTheDocument();
  expect(screen.getByText("待验证")).toBeInTheDocument();
});
