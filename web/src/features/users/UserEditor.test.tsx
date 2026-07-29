import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { UserDetail, UserView } from "../../api/users";
import { UserEditor } from "./UserEditor";

const admin: UserView = {
  id: "11111111-1111-4111-8111-111111111111",
  subject: "admin-sub",
  username: "platform-admin",
  display_name: "Platform Admin",
  email: "admin@example.com",
  role: "Admin",
  enabled: true,
  synced_at: "2026-07-29T12:00:00Z",
};

function protectedDetail(reason: "self_admin_change_forbidden" | "last_admin_protected"): UserDetail {
  return {
    user: admin,
    sessions: { active_count: 1, last_access_at: "2026-07-29T12:00:00Z" },
    allowed_actions: {
      edit_profile: true,
      change_role: false,
      change_enabled: false,
      reset_password: true,
      logout: true,
    },
    action_reasons: { change_role: reason, change_enabled: reason },
  };
}

test("validates create fields, focuses the first error, and submits normalized values", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn();
  render(<UserEditor mode="create" pending={false} onSubmit={onSubmit} onCancel={vi.fn()} />);

  await user.click(screen.getByRole("button", { name: "创建账号" }));
  expect(screen.getByLabelText("用户名")).toHaveFocus();
  expect(screen.getByText("请输入用户名")).toBeInTheDocument();

  await user.type(screen.getByLabelText("用户名"), "alice");
  await user.type(screen.getByLabelText("显示名称"), "  Alice Chen  ");
  await user.type(screen.getByLabelText("邮箱"), "alice@example.com");
  await user.selectOptions(screen.getByLabelText("角色"), "User");
  await user.click(screen.getByRole("button", { name: "创建账号" }));

  expect(onSubmit).toHaveBeenCalledWith({
    username: "alice",
    display_name: "Alice Chen",
    email: "alice@example.com",
    role: "User",
  });
});

test.each([
  ["self_admin_change_forbidden", "不能移除当前登录管理员自己的管理权限。"],
  ["last_admin_protected", "必须至少保留一个已启用的管理员。"],
] as const)("keeps protected %s controls visible and disabled", (reason, explanation) => {
  render(
    <UserEditor
      mode="edit"
      detail={protectedDetail(reason)}
      pending={false}
      onSubmit={vi.fn()}
      onCancel={vi.fn()}
    />,
  );

  expect(screen.getByLabelText("用户名")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("角色")).toBeDisabled();
  expect(screen.getByLabelText("账号已启用")).toBeDisabled();
  expect(screen.getAllByText(explanation)).toHaveLength(2);
  expect(screen.getByRole("button", { name: "保存更改" })).toBeEnabled();
});
