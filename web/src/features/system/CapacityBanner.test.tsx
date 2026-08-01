import { render, screen } from "@testing-library/react";

import type { Capacity } from "../../api/assessments";
import { CapacityBanner } from "./CapacityBanner";

const capacity: Capacity = {
  admitted_or_running: 1,
  max_running_total: 3,
  hard_max_running_total: 32,
  queued: 0,
  oldest_queued_seconds: null,
  waiting_for_data: 0,
  oldest_waiting_seconds: null,
  gateway_active_completions: 1,
  gateway_model: "gpt-5.6-sol",
  gateway_reasoning_effort: "xhigh",
  model_routing: {
    fast: { model: "gpt-5.6-terra", reasoning_effort: "medium" },
    slow: { model: "gpt-5.6-sol", reasoning_effort: "high" },
  },
  open_circuits: [],
  admission_allowed: true,
  admission_reasons: [],
};

test("shows the two assessment routes instead of the gateway compatibility default", () => {
  render(<CapacityBanner capacity={capacity} />);

  expect(screen.getByText("快速分析路由")).toBeInTheDocument();
  expect(screen.getByText("gpt-5.6-terra · 中")).toBeInTheDocument();
  expect(screen.getByText("关键裁决路由")).toBeInTheDocument();
  expect(screen.getByText("gpt-5.6-sol · 高")).toBeInTheDocument();
  expect(screen.queryByText("Gateway 模型")).not.toBeInTheDocument();
  expect(screen.queryByText("gpt-5.6-sol · xhigh")).not.toBeInTheDocument();
});
