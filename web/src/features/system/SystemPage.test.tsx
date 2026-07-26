import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { SystemPage } from "./SystemPage";

function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

test("shows safe diagnostics and keeps scheduler policy read-only for viewers", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ subject: "viewer", display_name: "查看者", scopes: ["system:read"], roles: ["Viewer"] });
    if (path.endsWith("/capacity")) return json({ admitted_or_running: 1, max_running_total: 2, hard_max_running_total: 32, queued: 0, oldest_queued_seconds: null, gateway_active_completions: 1, gateway_model: "gpt-5.6-sol", gateway_reasoning_effort: "xhigh", open_circuits: [], admission_allowed: true, admission_reasons: [] });
    if (path.endsWith("/scheduler-policy")) return json({ max_running_total: 2, hard_max_running_total: 32, gateway_active_limit: 3, cpu_limit_percent: 85, minimum_memory_gib: 8, minimum_disk_gib: 10, minimum_disk_percent: 10, version: 4, updated_at: "2026-07-25T12:00:00Z" });
    return json({ gateway: { status: "ok", active_completions: 1, model: "gpt-5.6-sol", reasoning_effort: "xhigh", snapshot_id: "snapshot-id", latency_ms: 12 }, workers: [{ instance_name: "worker-1", status: "idle", heartbeat_at: "2026-07-25T12:00:00Z", capabilities: { deep: true } }], circuits: [{ name: "vendor:finnhub", status: "closed", failure_count: 0, opened_until: null, last_error_code: null }] });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SystemPage /></QueryClientProvider>);

  expect(await screen.findByRole("heading", { name: "系统状态" })).toBeInTheDocument();
  expect(await screen.findByText("worker-1")).toBeInTheDocument();
  expect(screen.getByText("vendor:finnhub")).toBeInTheDocument();
  expect(screen.getByLabelText("最大并发评估")).toHaveAttribute("max", "32");
  expect(screen.getByLabelText("最大并发评估")).toBeDisabled();
  expect(document.body.textContent).not.toContain("/app/devs");
  expect(document.body.textContent).not.toContain("secret");
});

test("lets administrators save a concurrency value above three", async () => {
  let savedPolicy: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ subject: "admin", display_name: "管理员", scopes: ["system:read", "assessments:admin"], roles: ["Admin"] });
    if (path.endsWith("/capacity")) return json({ admitted_or_running: 3, max_running_total: 3, hard_max_running_total: 32, queued: 12, oldest_queued_seconds: 60, gateway_active_completions: 3, gateway_model: "gpt-5.6-sol", gateway_reasoning_effort: "xhigh", open_circuits: [], admission_allowed: false, admission_reasons: ["run_capacity"] });
    if (path.endsWith("/scheduler-policy") && init?.method === "PUT") {
      savedPolicy = JSON.parse(String(init.body));
      return json({ ...savedPolicy, version: 5, updated_at: "2026-07-25T12:01:00Z" });
    }
    if (path.endsWith("/scheduler-policy")) return json({ max_running_total: 3, hard_max_running_total: 32, gateway_active_limit: 10, cpu_limit_percent: 85, minimum_memory_gib: 8, minimum_disk_gib: 10, minimum_disk_percent: 10, version: 4, updated_at: "2026-07-25T12:00:00Z" });
    return json({ gateway: { status: "ok", active_completions: 3, model: "gpt-5.6-sol", reasoning_effort: "xhigh", snapshot_id: "snapshot-id", latency_ms: 12 }, workers: [], circuits: [] });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SystemPage /></QueryClientProvider>);

  const concurrency = await screen.findByLabelText("最大并发评估");
  expect(concurrency).toHaveAttribute("max", "32");
  expect(concurrency).toBeEnabled();
  fireEvent.change(concurrency, { target: { value: "32" } });
  fireEvent.click(screen.getByRole("button", { name: "保存调度策略" }));

  await waitFor(() => expect(savedPolicy?.max_running_total).toBe(32));
  expect(savedPolicy?.hard_max_running_total).toBe(32);
});
