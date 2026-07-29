import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { TemporaryPasswordDialog } from "./TemporaryPasswordDialog";

function Harness() {
  const [password, setPassword] = useState<string | null>("secret-once");
  return password ? (
    <TemporaryPasswordDialog
      username="alice"
      temporaryPassword={password}
      clearAndClose={() => setPassword(null)}
    />
  ) : <span>credential-cleared</span>;
}

test("copies only the secret and clears owner state when acknowledged", async () => {
  const writeText = vi.fn(async () => undefined);
  const user = userEvent.setup();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  render(<Harness />);

  const dialog = screen.getByRole("dialog", { name: "alice 的临时密码" });
  expect(dialog).toHaveTextContent("secret-once");
  expect(dialog).not.toHaveAccessibleName(/secret-once/);

  await user.click(screen.getByRole("button", { name: "复制临时密码" }));
  expect(writeText).toHaveBeenCalledWith("secret-once");
  expect(screen.getByRole("status")).toHaveTextContent("已复制");
  expect(screen.getByRole("status")).not.toHaveTextContent("secret-once");

  await user.click(screen.getByRole("button", { name: "我已安全保存，关闭" }));
  expect(screen.queryByText("secret-once")).not.toBeInTheDocument();
  expect(screen.getByText("credential-cleared")).toBeInTheDocument();
});

test("Escape invokes the same clear-and-close lifecycle", async () => {
  const clearAndClose = vi.fn();
  const user = userEvent.setup();
  render(
    <TemporaryPasswordDialog
      username="alice"
      temporaryPassword="secret-once"
      clearAndClose={clearAndClose}
    />,
  );

  await user.keyboard("{Escape}");
  expect(clearAndClose).toHaveBeenCalledOnce();
});
