import * as usersApi from "./users";

const userId = "11111111-1111-4111-8111-111111111111";

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => (
    new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  ));
});

function lastRequest(): [string, RequestInit] {
  const call = vi.mocked(globalThis.fetch).mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  return [String(call[0]), call[1] ?? {}];
}

test("encodes every supported list filter", async () => {
  await usersApi.listUsers({
    search: "Alice + Bob",
    role: "Admin",
    status: "disabled",
    page: 3,
    pageSize: 40,
  });

  expect(lastRequest()[0]).toBe(
    "/api/v1/admin/users?search=Alice+%2B+Bob&role=Admin&status=disabled&page=3&page_size=40",
  );
  expect(lastRequest()[1].method).toBeUndefined();
});

test("requests list defaults and user detail", async () => {
  await usersApi.listUsers();
  expect(lastRequest()[0]).toBe("/api/v1/admin/users?page=1&page_size=20");

  await usersApi.getUser(userId);
  expect(lastRequest()[0]).toBe(`/api/v1/admin/users/${userId}`);
});

test("creates and patches users with exact JSON bodies", async () => {
  await usersApi.createUser({
    username: "alice",
    display_name: "Alice",
    email: "alice@example.com",
    role: "User",
  });
  expect(lastRequest()[0]).toBe("/api/v1/admin/users");
  expect(lastRequest()[1]).toMatchObject({
    method: "POST",
    body: JSON.stringify({
      username: "alice",
      display_name: "Alice",
      email: "alice@example.com",
      role: "User",
    }),
  });

  await usersApi.updateUser(userId, {
    display_name: "Alice Chen",
    email: "alice.chen@example.com",
    role: "Admin",
    enabled: false,
  });
  expect(lastRequest()[0]).toBe(`/api/v1/admin/users/${userId}`);
  expect(lastRequest()[1]).toMatchObject({
    method: "PATCH",
    body: JSON.stringify({
      display_name: "Alice Chen",
      email: "alice.chen@example.com",
      role: "Admin",
      enabled: false,
    }),
  });
});

test("reset and logout send explicit empty JSON bodies for CSRF enforcement", async () => {
  await usersApi.resetUserPassword(userId);
  expect(lastRequest()[0]).toBe(`/api/v1/admin/users/${userId}/reset-password`);
  expect(lastRequest()[1]).toMatchObject({ method: "POST", body: "{}" });

  await usersApi.logoutUser(userId);
  expect(lastRequest()[0]).toBe(`/api/v1/admin/users/${userId}/logout`);
  expect(lastRequest()[1]).toMatchObject({ method: "POST", body: "{}" });
});

test("does not expose a destructive delete operation", () => {
  expect("deleteUser" in usersApi).toBe(false);
});
