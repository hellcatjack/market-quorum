import { render, screen } from "@testing-library/react";

import type { Integrity } from "../../api/records";
import { IntegrityPanel } from "./IntegrityPanel";

const AT_RISK_INTEGRITY: Integrity = {
  run_id: "00000000-0000-0000-0000-000000000001",
  policy_version: "point-in-time.v1",
  status: "at_risk",
  audit_mode: "retrospective",
  temporal_scope: "historical_reconstruction",
  analysis_date: "2025-07-01",
  checked_at: "2026-07-27T12:00:00Z",
  reason_codes: ["future_publication_exposed"],
  findings: [
    {
      tool_name: "get_income_statement",
      status: "at_risk",
      reason_code: "future_publication_exposed",
      details: {
        fiscal_date_ending: "2025-06-30",
        available_at: "2025-07-24",
      },
    },
  ],
  input_fingerprint: "a".repeat(64),
};

const SAFE_INTEGRITY: Integrity = {
  ...AT_RISK_INTEGRITY,
  status: "safe",
  audit_mode: "live",
  temporal_scope: "contemporaneous",
  reason_codes: ["live_current_snapshot"],
  findings: [],
};

test("shows a compact risk warning and expandable tool findings", () => {
  render(
    <IntegrityPanel
      integrity={AT_RISK_INTEGRITY}
      canCleanReassess
      onCleanReassess={() => undefined}
    />,
  );

  expect(screen.getByRole("alert")).toHaveTextContent("点时数据存在风险");
  expect(screen.getByText("未来发布的财务报表")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "创建干净重评估" })).toBeInTheDocument();
  expect(screen.getByTestId("integrity-findings")).not.toHaveAttribute("open");
});

test("uses a low-emphasis safe state", () => {
  render(
    <IntegrityPanel
      integrity={SAFE_INTEGRITY}
      canCleanReassess={false}
      onCleanReassess={() => undefined}
    />,
  );

  expect(screen.getByText("点时数据已核验")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "创建干净重评估" })).not.toBeInTheDocument();
});
