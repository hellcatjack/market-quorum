import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { InstrumentHistoryPage } from "./InstrumentHistoryPage";

const validation = (
  horizon: number,
  status: string,
  totalReturn: string | null,
  totalAlpha: string | null,
  directionCorrect: boolean | null = status === "completed" ? true : null,
) => ({
  id: `validation-${horizon}`,
  run_id: "run-new",
  horizon,
  status,
  scheduled_for: "2026-07-25T12:00:00Z",
  matures_at: `2026-08-${String(horizon).padStart(2, "0")}T12:00:00Z`,
  exit_session: status === "completed" ? "2026-08-20" : null,
  total_return: totalReturn,
  total_alpha: totalAlpha,
  direction_correct: directionCorrect,
  price_target_hit: horizon === 20 ? false : null,
  error_code: null,
});

const history = [
  {
    run: {
      id: "run-new",
      request_id: "request-new",
      ticker: "NVDA",
      asset_type: "stock",
      analysis_date: "2026-07-25",
      status: "succeeded",
      attempt: 2,
      created_at: "2026-07-25T14:00:00Z",
    },
    rating: "Underweight",
    executive_summary: "估值风险正在上升。",
    price_target: "110",
    gateway_model: "gpt-5.6-sol",
    gateway_reasoning_effort: "xhigh",
    config_snapshot_sha256: "config-new",
    validation_outcome: "20D · -20.65% · 方向正确",
    validations: [
      validation(1, "completed", "0.03", "0.01"),
      validation(5, "scheduled", null, null),
      validation(20, "completed", "-0.2065", "-0.1459", false),
    ],
    memory_mode: "historical",
    memory_source_count: 2,
    is_latest_attempt: true,
    request_attempt_count: 2,
  },
  {
    run: {
      id: "run-new-first",
      request_id: "request-new",
      ticker: "NVDA",
      asset_type: "stock",
      analysis_date: "2026-07-25",
      status: "failed",
      attempt: 1,
      created_at: "2026-07-25T12:00:00Z",
    },
    rating: null,
    executive_summary: null,
    price_target: null,
    gateway_model: "gpt-5.6-sol",
    gateway_reasoning_effort: "xhigh",
    config_snapshot_sha256: "config-failed",
    validation_outcome: null,
    validations: [],
    memory_mode: "historical",
    memory_source_count: 2,
    is_latest_attempt: false,
    request_attempt_count: 2,
  },
  {
    run: {
      id: "run-old",
      request_id: "request-old",
      ticker: "NVDA",
      asset_type: "stock",
      analysis_date: "2026-06-01",
      status: "succeeded",
      attempt: 1,
      created_at: "2026-06-01T12:00:00Z",
    },
    rating: "Hold",
    executive_summary: "等待更好估值。",
    price_target: "125",
    gateway_model: "gpt-5.6-sol",
    gateway_reasoning_effort: "xhigh",
    config_snapshot_sha256: "config-old",
    validation_outcome: null,
    validations: [],
    memory_mode: "independent",
    memory_source_count: 0,
    is_latest_attempt: true,
    request_attempt_count: 1,
  },
];

test("shows newest research first, preserves transitions and can switch to audit order", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const body = String(input).endsWith("/history")
      ? history
      : {
          ticker: "NVDA",
          name: "NVIDIA CORP",
          exchange: "Nasdaq",
          asset_types: ["stock"],
          assessment_count: 3,
          latest_run_id: "run-new",
          latest_rating: "Underweight",
          latest_created_at: "2026-07-25T14:00:00Z",
        };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const location = memoryLocation({ path: "/instruments/NVDA" });
  render(
    <QueryClientProvider client={client}>
      <Router hook={location.hook}>
        <Route path="/instruments/:ticker"><InstrumentHistoryPage /></Route>
      </Router>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "NVIDIA CORP" })).toBeInTheDocument();
  expect(screen.getByText("NVDA · Nasdaq")).toBeInTheDocument();
  const events = await screen.findAllByTestId("history-event");
  expect(events).toHaveLength(2);
  expect(events[0]).toHaveTextContent("2026-07-25");
  expect(events[1]).toHaveTextContent("2026-06-01");
  expect(screen.getByRole("button", { name: "最新在前" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByText("由新到旧")).toBeInTheDocument();
  expect(events[0]).toHaveTextContent("Hold → Underweight");
  expect(events[0]).toHaveTextContent("历史辅助 · 2 个来源");
  expect(within(events[0]).getByText("1D")).toBeInTheDocument();
  expect(within(events[0]).getByText("5D")).toBeInTheDocument();
  expect(within(events[0]).getByText("20D")).toBeInTheDocument();
  expect(events[0]).toHaveTextContent("+3.00%");
  expect(events[0]).toHaveTextContent("Alpha +1.00%");
  expect(events[0]).toHaveTextContent("待验证");
  expect(events[0]).toHaveTextContent("-20.65%");
  expect(events[0]).toHaveTextContent("方向正确");
  const correctDirection = within(events[0]).getByText("方向正确");
  expect(correctDirection).toHaveClass("history-validation__direction--correct");
  expect(correctDirection.closest(".history-validation")).toHaveClass(
    "history-validation--direction-correct",
  );
  const incorrectDirection = within(events[0]).getByText("方向错误");
  expect(incorrectDirection).toHaveClass("history-validation__direction--incorrect");
  expect(incorrectDirection.closest(".history-validation")).toHaveClass(
    "history-validation--direction-incorrect",
  );
  expect(events[0]).toHaveTextContent("目标价未命中");
  expect(within(events[0]).getByRole("link", { name: "查看评估详情" })).toHaveAttribute(
    "href",
    "/runs/run-new",
  );
  expect(within(events[0]).getByText("其他尝试（1）")).toBeInTheDocument();
  expect(within(events[0]).getByRole("link", { name: "尝试 #1 · 失败" })).toHaveAttribute(
    "href",
    "/runs/run-new-first",
  );

  await userEvent.click(screen.getByRole("button", { name: "最早在前" }));
  const chronologicalEvents = screen.getAllByTestId("history-event");
  expect(screen.getByText("由旧到新")).toBeInTheDocument();
  expect(chronologicalEvents[0]).toHaveTextContent("2026-06-01");
  expect(chronologicalEvents[1]).toHaveTextContent("2026-07-25");
  expect(chronologicalEvents[1]).toHaveTextContent("Hold → Underweight");
});
