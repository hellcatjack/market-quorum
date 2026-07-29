import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";

import { useCurrentUser } from "./CurrentUserContext";
import { CurrentUserProvider } from "./CurrentUserProvider";


function Probe() {
  const identity = useCurrentUser();
  return (
    <div>
      <span>{identity.user?.display_name}</span>
      <span>{String(identity.hasRole("Admin"))}</span>
      <span>{String(identity.hasScope("users:manage"))}</span>
    </div>
  );
}


test("loads current identity once and exposes role and scope helpers", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        subject: "admin-sub",
        display_name: "Admin",
        email: "admin@example.com",
        scopes: ["users:manage"],
        roles: ["Admin"],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CurrentUserProvider>
        <Probe />
      </CurrentUserProvider>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("Admin")).toBeInTheDocument();
  expect(screen.getAllByText("true")).toHaveLength(2);
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/me");
});
