import { render, screen, within } from "@testing-library/react";

import type { Artifact, Decision } from "../../api/records";
import { DecisionPanel } from "./DecisionPanel";

const decision: Decision = {
  run_id: "run-123",
  rating: "Hold",
  executive_summary: "这是不能缩略的完整执行摘要。",
  investment_thesis: "这是不能缩略的完整投资逻辑。",
  price_target: "区间 28.50–35.00 美元，取决于发射节奏与资金成本",
  time_horizon: "未来 12–18 个月，并在每次任务发射和季度财报后重新评估",
  structured: {},
};

const completeReport: Artifact = {
  id: "artifact-complete-report",
  run_id: "run-123",
  kind: "report_18_complete_report",
  media_type: "text/markdown",
  size: 106_886,
  sha256: "complete-report-sha",
  created_at: "2026-07-26T00:42:37Z",
};

test("keeps important investment conclusions unabridged and the complete report compact", () => {
  render(<DecisionPanel decision={decision} completeReport={completeReport} />);

  const panel = screen.getByRole("heading", { name: "投资结论" }).closest("section");
  if (!panel) throw new Error("investment conclusion panel is missing");

  expect(within(panel).getByText(decision.executive_summary)).toHaveClass(
    "decision-content--unabridged",
  );
  expect(within(panel).getByText(decision.price_target as string)).toHaveClass(
    "decision-content--unabridged",
  );
  expect(within(panel).getByText(decision.time_horizon as string)).toHaveClass(
    "decision-content--unabridged",
  );
  expect(within(panel).getByText(decision.investment_thesis)).toHaveClass(
    "decision-content--unabridged",
  );

  const report = within(panel).getByTestId(
    "artifact-preview-artifact-complete-report",
  );
  expect(report).toHaveTextContent("完整评估报告");
  expect(report).not.toHaveAttribute("open");
});
