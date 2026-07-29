import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { App } from "./App";
import { I18nProvider } from "../i18n/I18nProvider";

let currentIdentity: {
  subject: string;
  display_name: string;
  email: string;
  scopes: string[];
  roles: string[];
};
let requestedPaths: string[];

beforeEach(() => {
  currentIdentity = {
    subject: "alice",
    display_name: "Alice",
    email: "alice@example.com",
    scopes: ["assessments:read", "assessments:submit"],
    roles: ["User"],
  };
  requestedPaths = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requestedPaths.push(path);
    let body: unknown;
    if (path.includes("/assessments/admission-summary")) {
      body = {
        running: 0,
        max_running: 2,
        queued: 0,
        oldest_queued_seconds: null,
        admission: "immediate",
        reason: "capacity_available",
      };
    } else if (path.includes("/assessments?")) {
      body = { items: [] };
    } else if (path.includes("/assessments/run-123/")) {
      body = path.endsWith("/decision")
        ? {
            run_id: "run-123",
            rating: "Hold",
            executive_summary: "摘要",
            investment_thesis: "逻辑",
            price_target: null,
            time_horizon: null,
            structured: {},
          }
        : [];
    } else if (path.endsWith("/assessments/run-123")) {
      body = {
        id: "run-123",
        request_id: "request-123",
        ticker: "SPCX",
        asset_type: "stock",
        analysis_date: "2026-07-25",
        status: "succeeded",
        attempt: 1,
        created_at: "2026-07-25T12:00:00Z",
        request_config: {},
        resolved_config: {},
        data_vendors: {},
        tool_vendors: {},
      };
    } else {
      body = currentIdentity;
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
});

test("renders the Chinese management shell and all primary routes", async () => {
  currentIdentity = {
    ...currentIdentity,
    scopes: [
      "assessments:read",
      "assessments:submit",
      "system:read",
      "users:manage",
    ],
    roles: ["Admin"],
  };
  const { hook } = memoryLocation({ path: "/" });
  render(
    <Router hook={hook}>
      <App />
    </Router>,
  );

  expect(screen.getByRole("link", { name: "总览" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "新建评估" })).toHaveAttribute("href", "/new");
  expect(await screen.findByRole("link", { name: "系统状态" })).toHaveAttribute("href", "/system");
  expect(await screen.findByRole("link", { name: "用户管理" })).toHaveAttribute("href", "/users");
  expect(screen.getByRole("link", { name: "退出" })).toHaveAttribute(
    "href",
    "/oauth2/sign_out",
  );
  expect(await screen.findByText("Alice")).toBeInTheDocument();
  expect(screen.getByLabelText("排队任务")).toHaveTextContent("队列");
});

test.each(["/system", "/users"])(
  "ordinary users cannot mount protected route %s or request its data",
  async (path) => {
    const { hook } = memoryLocation({ path });
    render(
      <Router hook={hook}>
        <App />
      </Router>,
    );

    expect(await screen.findByRole("heading", { name: "无权访问" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "系统状态" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "用户管理" })).not.toBeInTheDocument();
    expect(requestedPaths.some((item) => item.includes("/system/"))).toBe(false);
    expect(requestedPaths.some((item) => item.includes("/admin/users"))).toBe(false);
  },
);

test("defines the run detail route", async () => {
  const { hook } = memoryLocation({ path: "/runs/run-123" });
  render(
    <Router hook={hook}>
      <App />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "SPCX" })).toBeInTheDocument();
});

test("lets the user switch the complete management shell to English", async () => {
  const user = userEvent.setup();
  const { hook } = memoryLocation({ path: "/" });
  render(
    <I18nProvider initialLocale="zh-CN">
      <Router hook={hook}>
        <App />
      </Router>
    </I18nProvider>,
  );

  await user.selectOptions(screen.getByRole("combobox", { name: "界面语言" }), "en-US");
  expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Assessment overview" })).toBeInTheDocument();
  expect(window.localStorage.getItem("tradingng.ui.locale")).toBe("en-US");
});
