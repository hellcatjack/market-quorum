import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Artifact } from "../../api/records";
import type { components } from "../../api/schema";
import { ValidationReplayPanel } from "./ValidationReplayPanel";

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

type Validation = components["schemas"]["ValidationView"];

function validation(
  horizon: 1 | 5 | 20,
  overrides: Partial<Validation> = {},
): Validation {
  const exitDay = horizon + 1;
  return {
    id: `validation-${horizon}`,
    run_id: "run-123",
    horizon,
    status: "completed",
    scheduled_for: "2026-07-25T00:00:00Z",
    observed_at: "2026-07-26T12:00:00Z",
    raw_return: horizon === 20 ? "0.0842" : "0.0100",
    benchmark_return: horizon === 20 ? "0.0217" : "0.0020",
    alpha: horizon === 20 ? "0.0625" : "0.0080",
    max_adverse_excursion: "-0.0310",
    max_favorable_excursion: "0.1020",
    trigger_results: {
      rating: "Buy",
      direction: "bullish",
      direction_correct: true,
      price_target_hit: false,
      entry_price: "100",
      exit_price: horizon === 20 ? "108.42" : "101",
      entry_session: "2026-07-01",
      exit_session: `2026-07-${String(exitDay).padStart(2, "0")}`,
    },
    data_artifact_id: `artifact-${horizon}`,
    error_code: null,
    calculation_version: "validation.v1",
    ...overrides,
  };
}

function priceArtifact(horizon: number): string {
  const count = horizon + 1;
  const sessions = Array.from({ length: count }, (_, index) =>
    `2026-07-${String(index + 1).padStart(2, "0")}`,
  );
  const instrument = sessions.map((_, index) => 100 + index);
  const benchmark = sessions.map((_, index) => 200 + index);
  const series = (ticker: string, values: number[]) => ({
    ticker,
    currency: "USD",
    sessions,
    open: values,
    high: values.map((value) => value + 1),
    low: values.map((value) => value - 1),
    close: values,
    adjusted_close: values,
    source: "yfinance",
    collected_at: "2026-07-26T12:00:00Z",
  });
  return JSON.stringify({
    instrument: series("NVDA", instrument),
    benchmark: series("SPY", benchmark),
  });
}

const artifacts: Artifact[] = [1, 5, 20].map((horizon) => ({
  id: `artifact-${horizon}`,
  run_id: "run-123",
  kind: `validation_${horizon}_prices`,
  media_type: "application/json",
  size: 2048,
  sha256: `${horizon}`.repeat(64).slice(0, 64),
  created_at: "2026-07-26T12:00:00Z",
}));

function renderPanel({
  validations = [validation(1), validation(20)],
  canReadArtifacts = true,
  loadArtifact = vi.fn(async (artifactId: string) => {
    const horizon = Number(artifactId.split("-").at(-1));
    return priceArtifact(horizon);
  }),
}: {
  validations?: Validation[];
  canReadArtifacts?: boolean;
  loadArtifact?: (artifactId: string) => Promise<string>;
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ValidationReplayPanel
        ticker="NVDA"
        exchange="NMS"
        analysisDate="2026-07-01"
        priceTarget="120"
        validations={validations}
        artifacts={artifacts}
        canReadArtifacts={canReadArtifacts}
        loadArtifact={loadArtifact}
      />
    </QueryClientProvider>,
  );
  return { loadArtifact };
}

test("defaults to the longest completed audit replay", async () => {
  renderPanel();

  expect(await screen.findByTestId("validation-chart")).toHaveTextContent("NVDA 对比 SPY");
  expect(screen.getByRole("button", { name: "20 日" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("方向正确")).toBeInTheDocument();
  expect(screen.getByText("+8.4%")).toBeInTheDocument();
  expect(screen.getByText("+2.2%")).toBeInTheDocument();
  expect(screen.getByText("+6.3%")).toBeInTheDocument();
  expect(screen.getByText(/共有 21 个价格节点/)).toBeInTheDocument();

  const audit = screen.getByText("计算与数据依据").closest("details");
  expect(audit).not.toHaveAttribute("open");
  if (!audit) throw new Error("audit disclosure is missing");
  expect(within(audit).getByText("validation.v1")).toBeInTheDocument();
  expect(within(audit).getByText("artifact-20")).toBeInTheDocument();
  expect(within(audit).getByText("yfinance")).toBeInTheDocument();

  expect(screen.getByRole("link", { name: "在 TradingView 深入研究" })).toHaveAttribute(
    "href",
    "https://www.tradingview.com/chart/?symbol=NASDAQ%3ANVDA&interval=D",
  );
  expect(screen.getByText(/外部行情仅供延伸研究/)).toBeInTheDocument();
});

test("switches horizons without mixing bound artifacts", async () => {
  const loadArtifact = vi.fn(async (artifactId: string) =>
    priceArtifact(Number(artifactId.split("-").at(-1))),
  );
  const user = userEvent.setup();
  renderPanel({ loadArtifact });
  await screen.findByTestId("validation-chart");

  await user.click(screen.getByRole("button", { name: "1 日" }));

  expect(await screen.findByText("+1.0%")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "1 日" })).toHaveAttribute("aria-pressed", "true");
  expect(loadArtifact).toHaveBeenCalledWith("artifact-20");
  expect(loadArtifact).toHaveBeenCalledWith("artifact-1");
});

test("explains scheduled and failed validation states", async () => {
  const user = userEvent.setup();
  renderPanel({
    validations: [
      validation(5, { status: "scheduled", data_artifact_id: null }),
      validation(20, {
        status: "failed",
        data_artifact_id: null,
        error_code: "calculation_error",
      }),
    ],
  });
  expect(screen.getByText(/验证无法完成/)).toBeInTheDocument();
  expect(screen.getByText(/calculation_error/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "5 日" }));
  expect(screen.getByText(/尚未到达验证时间/)).toBeInTheDocument();
});

test("keeps saved metrics visible when artifact permission is unavailable", () => {
  const loadArtifact = vi.fn(async () => priceArtifact(20));
  renderPanel({ canReadArtifacts: false, loadArtifact });

  expect(screen.getByText("+8.4%")).toBeInTheDocument();
  expect(screen.getByText(/缺少产物读取权限/)).toBeInTheDocument();
  expect(loadArtifact).not.toHaveBeenCalled();
});

test("copies the exact immutable review range", async () => {
  const writeText = vi.fn(async () => undefined);
  const user = userEvent.setup();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  renderPanel();
  await screen.findByTestId("validation-chart");

  await user.click(screen.getByRole("button", { name: "复制验证区间" }));

  expect(writeText).toHaveBeenCalledWith(
    "NVDA | 2026-07-01 至 2026-07-21 | 日线 | 基准 SPY",
  );
  expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();
});
