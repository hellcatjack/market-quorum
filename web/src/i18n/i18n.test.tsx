import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { I18nProvider, resolveLocale, useI18n } from "./I18nProvider";
import { runStatusLabel } from "./domainLabels";
import { RunTimeline } from "../features/runs/RunTimeline";

function LanguageProbe() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <span>{t("系统状态")}</span>
      <button type="button" onClick={() => setLocale("en-US")}>English</button>
    </div>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "";
});

test("prefers a saved UI locale and otherwise follows the browser language", () => {
  expect(resolveLocale("en-US", ["zh-CN"])).toBe("en-US");
  expect(resolveLocale(null, ["zh-HK", "en-US"])).toBe("zh-CN");
  expect(resolveLocale(null, ["fr-FR"])).toBe("en-US");
});

test("persists an explicit language choice and updates the document language", async () => {
  const user = userEvent.setup();
  render(
    <I18nProvider initialLocale="zh-CN">
      <LanguageProbe />
    </I18nProvider>,
  );

  expect(screen.getByText("系统状态")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByText("System status")).toBeInTheDocument();
  expect(screen.getByTestId("locale")).toHaveTextContent("en-US");
  expect(window.localStorage.getItem("tradingng.ui.locale")).toBe("en-US");
  expect(document.documentElement.lang).toBe("en-US");
});

test("renders domain status identifiers as readable labels in both languages", () => {
  expect(runStatusLabel("waiting_for_data", "zh-CN")).toBe("等待数据");
  expect(runStatusLabel("waiting_for_data", "en-US")).toBe("Waiting for data");
  expect(runStatusLabel("running_analysts", "zh-CN")).toBe("分析师研究");
  expect(runStatusLabel("running_analysts", "en-US")).toBe("Analyst research");
  expect(runStatusLabel("future_state", "en-US")).toBe("Unknown status (future_state)");
});

test("renders execution phases, events, and model calls as readable English", () => {
  render(
    <I18nProvider initialLocale="en-US">
      <RunTimeline
        steps={[{
          name: "running_analysts",
          status: "completed",
          attempt: 1,
          started_at: "2026-07-25T12:00:00Z",
          finished_at: "2026-07-25T12:01:00Z",
          error_code: null,
          summary: null,
        }]}
        events={[{
          sequence: 1,
          event_type: "assessment.admitted",
          payload: {},
          created_at: "2026-07-25T12:01:10Z",
        }]}
        evidence={[]}
        artifacts={[]}
        llmInteractions={[{
          sequence: 1,
          route: "slow",
          model_alias: "codex-slow",
          physical_model: "gpt-5.6-sol",
          reasoning_effort: "high",
          status: "completed",
          started_at: "2026-07-25T12:02:00Z",
          completed_at: "2026-07-25T12:02:04Z",
          duration_ms: 4000,
          error_code: null,
        }]}
        canReadArtifacts
      />
    </I18nProvider>,
  );

  expect(screen.getByRole("heading", { name: "Research timeline" })).toBeInTheDocument();
  expect(screen.getByTestId("step-running_analysts-1")).toHaveTextContent("Analyst research");
  expect(screen.getByTestId("step-running_analysts-1")).toHaveTextContent("Completed");
  expect(screen.getByTestId("event-1")).toHaveTextContent("Task admitted");
  expect(screen.getByTestId("llm-1")).toHaveTextContent("Critical decision route");
  expect(screen.getByTestId("llm-1")).toHaveTextContent("High");
});
