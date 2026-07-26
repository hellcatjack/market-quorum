import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import type { Capacity } from "../../api/assessments";
import { AssessmentForm } from "./AssessmentForm";
import { parseTickers } from "./tickers";

const BLOCKED_CAPACITY: Capacity = {
  admitted_or_running: 2,
  max_running_total: 2,
  hard_max_running_total: 3,
  queued: 4,
  oldest_queued_seconds: 90,
  gateway_active_completions: 2,
  gateway_model: "gpt-5.6-sol",
  gateway_reasoning_effort: "xhigh",
  open_circuits: ["vendor:finnhub"],
  admission_allowed: false,
  admission_reasons: ["running_limit_reached"],
};

function renderForm(capacity: Capacity = BLOCKED_CAPACITY) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const location = memoryLocation({ path: "/new", record: true });
  render(
    <QueryClientProvider client={queryClient}>
      <Router hook={location.hook}>
        <AssessmentForm capacity={capacity} />
      </Router>
    </QueryClientProvider>,
  );
  return location;
}

test("normalizes ticker separators and removes duplicates", () => {
  expect(parseTickers(" spcx, NVDA\nspcx\t gld ")).toEqual(["SPCX", "NVDA", "GLD"]);
});

test("defaults to Chinese, Deep and all analysts, then routes after a batch submission", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        items: [
          {
            id: "00000000-0000-0000-0000-000000000501",
            request_id: "00000000-0000-0000-0000-000000000502",
            ticker: "SPCX",
            asset_type: "stock",
            analysis_date: "2026-07-25",
            status: "queued",
            attempt: 1,
            created_at: "2026-07-25T12:00:00Z",
          },
        ],
      }),
      { status: 202, headers: { "Content-Type": "application/json" } },
    ),
  );
  const location = renderForm();

  expect(screen.getByRole("combobox", { name: "分析深度" })).toHaveValue("deep");
  expect(screen.getByRole("combobox", { name: "输出语言" })).toHaveValue("Chinese");
  expect(screen.getByRole("combobox", { name: "评估记忆" })).toHaveValue("independent");
  expect(screen.queryByRole("combobox", { name: "资产类型" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("checkbox")).toHaveLength(4);
  for (const checkbox of screen.getAllByRole("checkbox")) expect(checkbox).toBeChecked();
  expect(screen.getByRole("alert")).toHaveTextContent("任务仍可进入受控队列");

  await user.type(screen.getByRole("textbox", { name: "标的代码" }), "spcx, NVDA\nspcx");
  await user.clear(screen.getByLabelText("分析日期"));
  await user.type(screen.getByLabelText("分析日期"), "2026-07-25");
  await user.click(screen.getByRole("button", { name: "派发评估" }));

  await waitFor(() => expect(location.history).toContain("/runs/00000000-0000-0000-0000-000000000501"));
  const [url, options] = fetchMock.mock.calls[0];
  const payload = JSON.parse(String(options?.body));
  expect(url).toBe("/api/v1/assessment-batches");
  expect(payload.items.map((item: { ticker: string }) => item.ticker)).toEqual(["SPCX", "NVDA"]);
  expect(payload.items.every((item: object) => !("asset_type" in item))).toBe(true);
  expect(payload.analysts).toEqual(["market", "social", "news", "fundamentals"]);
  expect(payload.depth).toBe("deep");
  expect(payload.memory_mode).toBe("independent");
  expect(payload.language).toBe("Chinese");
  expect(payload.idempotency_key).toBe("00000000-0000-4000-8000-000000000001");
});

test("can explicitly request a look-ahead-safe historical assessment", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        items: [{
          id: "00000000-0000-0000-0000-000000000601",
          request_id: "00000000-0000-0000-0000-000000000602",
          ticker: "NVDA",
          asset_type: "stock",
          analysis_date: "2026-07-25",
          status: "queued",
          attempt: 1,
          created_at: "2026-07-25T12:00:00Z",
        }],
      }),
      { status: 202, headers: { "Content-Type": "application/json" } },
    ),
  );
  renderForm();

  await user.type(screen.getByRole("textbox", { name: "标的代码" }), "NVDA");
  await user.clear(screen.getByLabelText("分析日期"));
  await user.type(screen.getByLabelText("分析日期"), "2026-07-25");
  await user.selectOptions(
    screen.getByRole("combobox", { name: "评估记忆" }),
    "historical",
  );
  await user.click(screen.getByRole("button", { name: "派发评估" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const payload = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
  expect(payload.memory_mode).toBe("historical");
});

test("validates dates and renders a server error next to the form", async () => {
  const user = userEvent.setup();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        error: {
          code: "capacity_blocked",
          message: "数据源熔断，暂时无法接收任务",
          request_id: "request-99",
        },
      }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    ),
  );
  renderForm({ ...BLOCKED_CAPACITY, admission_allowed: true, admission_reasons: [] });
  await user.type(screen.getByRole("textbox", { name: "标的代码" }), "TSLA");
  await user.clear(screen.getByLabelText("分析日期"));
  await user.type(screen.getByLabelText("分析日期"), "2999-01-01");
  await user.click(screen.getByRole("button", { name: "派发评估" }));
  expect(screen.getByText("分析日期不能晚于今天")).toBeInTheDocument();

  await user.clear(screen.getByLabelText("分析日期"));
  await user.type(screen.getByLabelText("分析日期"), "2026-07-25");
  await user.click(screen.getByRole("button", { name: "派发评估" }));
  expect(await screen.findByText("数据源熔断，暂时无法接收任务")).toBeInTheDocument();
  expect(screen.getByText("请求编号：request-99")).toBeInTheDocument();
});
