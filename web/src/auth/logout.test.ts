import { BROWSER_LOGOUT_URL } from "./logout";

test("browser logout clears OAuth2 Proxy before starting a fresh login", () => {
  const proxyLogout = new URL(BROWSER_LOGOUT_URL, "https://ushome.amycat.com");
  expect(proxyLogout.pathname).toBe("/oauth2/sign_out");

  const loginTarget = proxyLogout.searchParams.get("rd");
  expect(loginTarget).not.toBeNull();
  const loginStart = new URL(loginTarget!, "https://ushome.amycat.com");
  expect(loginStart.pathname).toBe("/oauth2/start");
  expect(loginStart.searchParams.get("rd")).toBe("/");
  expect(loginTarget).not.toContain("id_token_hint");
  expect(loginTarget).not.toContain("openid-connect/logout");
});
