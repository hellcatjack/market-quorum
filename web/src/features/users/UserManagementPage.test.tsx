import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClientError } from "../../api/client";
import {
  createUser,
  getUser,
  listUsers,
  logoutUser,
  resetUserPassword,
  updateUser,
  type UserDetail,
  type UserView,
} from "../../api/users";
import { UserManagementPage } from "./UserManagementPage";

vi.mock("../../api/users", () => ({
  listUsers: vi.fn(),
  getUser: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  resetUserPassword: vi.fn(),
  logoutUser: vi.fn(),
}));

const alice: UserView = {
  id: "11111111-1111-4111-8111-111111111111",
  subject: "alice-sub",
  username: "alice",
  display_name: "Alice Chen",
  email: "alice@example.com",
  role: "User",
  enabled: true,
  synced_at: "2026-07-29T12:00:00Z",
};
const detail: UserDetail = {
  user: alice,
  sessions: { active_count: 2, last_access_at: "2026-07-29T12:30:00Z" },
  allowed_actions: {
    edit_profile: true,
    change_role: true,
    change_enabled: true,
    reset_password: true,
    logout: true,
  },
  action_reasons: {},
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <UserManagementPage />
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => {
  vi.mocked(listUsers).mockResolvedValue({ items: [alice], page: 1, page_size: 20, total: 25 });
  vi.mocked(getUser).mockResolvedValue(detail);
  vi.mocked(createUser).mockResolvedValue({ user: alice, temporary_password: "created-secret" });
  vi.mocked(updateUser).mockResolvedValue(detail);
  vi.mocked(resetUserPassword).mockResolvedValue({ user: alice, temporary_password: "reset-secret" });
  vi.mocked(logoutUser).mockResolvedValue(detail);
});

test("debounces search and keeps role, status, and page in list queries", async () => {
  const user = userEvent.setup();
  renderPage();
  expect(await screen.findByText("Alice Chen")).toBeInTheDocument();

  await user.type(screen.getByLabelText("搜索用户"), "alice");
  await waitFor(() => expect(listUsers).toHaveBeenCalledWith(expect.objectContaining({ search: "alice" })), { timeout: 1_000 });

  await user.selectOptions(screen.getByLabelText("角色筛选"), "Admin");
  await user.selectOptions(screen.getByLabelText("状态筛选"), "disabled");
  await waitFor(() => expect(listUsers).toHaveBeenCalledWith(expect.objectContaining({
    search: "alice", role: "Admin", status: "disabled", page: 1,
  })));

  await user.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(listUsers).toHaveBeenCalledWith(expect.objectContaining({ page: 2 })));
});

test("creates an account and transfers its password to one-time state", async () => {
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("Alice Chen");

  await user.click(screen.getByRole("button", { name: "新建用户" }));
  await user.type(screen.getByLabelText("用户名"), "bob");
  await user.type(screen.getByLabelText("显示名称"), "Bob Lee");
  await user.type(screen.getByLabelText("邮箱"), "bob@example.com");
  await user.click(screen.getByRole("button", { name: "创建账号" }));

  await waitFor(() => expect(createUser).toHaveBeenCalledWith({
    username: "bob", display_name: "Bob Lee", email: "bob@example.com", role: "User",
  }));
  expect(await screen.findByText("created-secret")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "我已安全保存，关闭" }));
  expect(screen.queryByText("created-secret")).not.toBeInTheDocument();
});

test("supports profile edits and all safety actions with named confirmations", async () => {
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("Alice Chen");
  await user.click(screen.getByRole("button", { name: "查看 alice" }));
  expect(await screen.findByRole("heading", { name: "alice" })).toBeInTheDocument();
  expect(screen.getByText("2 个活动会话")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "编辑资料" }));
  await user.clear(screen.getByLabelText("显示名称"));
  await user.type(screen.getByLabelText("显示名称"), "Alice Updated");
  await user.click(screen.getByRole("button", { name: "保存更改" }));
  await waitFor(() => expect(updateUser).toHaveBeenCalledWith(alice.id, expect.objectContaining({ display_name: "Alice Updated" })));

  await user.click(screen.getByRole("button", { name: "停用账号" }));
  let confirmation = screen.getByRole("dialog", { name: "停用 alice？" });
  await user.click(within(confirmation).getByRole("button", { name: "确认停用" }));
  await waitFor(() => expect(updateUser).toHaveBeenCalledWith(alice.id, { enabled: false }));

  await user.click(screen.getByRole("button", { name: "重置密码" }));
  confirmation = screen.getByRole("dialog", { name: "重置 alice 的密码？" });
  await user.click(within(confirmation).getByRole("button", { name: "确认重置" }));
  expect(await screen.findByText("reset-secret")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "我已安全保存，关闭" }));

  await user.click(screen.getByRole("button", { name: "强制退出" }));
  confirmation = screen.getByRole("dialog", { name: "强制退出 alice？" });
  await user.click(within(confirmation).getByRole("button", { name: "确认退出" }));
  await waitFor(() => expect(logoutUser).toHaveBeenCalledWith(alice.id));
  expect(vi.mocked(listUsers).mock.calls.length).toBeGreaterThan(1);
});

test("explains sync-pending errors, retains form input, and shows request id", async () => {
  vi.mocked(createUser).mockRejectedValue(
    new ApiClientError(503, "identity_sync_pending", "upstream detail", "request-support-123"),
  );
  const user = userEvent.setup();
  renderPage();
  await screen.findByText("Alice Chen");
  await user.click(screen.getByRole("button", { name: "新建用户" }));
  await user.type(screen.getByLabelText("用户名"), "bob");
  await user.type(screen.getByLabelText("显示名称"), "Bob Lee");
  await user.type(screen.getByLabelText("邮箱"), "bob@example.com");
  await user.click(screen.getByRole("button", { name: "创建账号" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("身份提供方已完成变更，但本地同步仍在等待；请刷新核对，勿重复创建。");
  expect(screen.getByRole("alert")).toHaveTextContent("request-support-123");
  expect(screen.getByLabelText("显示名称")).toHaveValue("Bob Lee");
});
