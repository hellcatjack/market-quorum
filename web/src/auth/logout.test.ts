import { BROWSER_LOGOUT_URL } from "./logout";

test("browser logout clears OAuth2 Proxy and Keycloak before returning to login", () => {
  const proxyLogout = new URL(BROWSER_LOGOUT_URL, "https://ushome.amycat.com");
  expect(proxyLogout.pathname).toBe("/oauth2/sign_out");

  const providerTarget = proxyLogout.searchParams.get("rd");
  expect(providerTarget).not.toBeNull();
  const providerLogout = new URL(providerTarget!, "https://ushome.amycat.com");
  expect(providerLogout.pathname).toBe(
    "/realms/tradingng/protocol/openid-connect/logout",
  );
  expect(providerLogout.searchParams.get("id_token_hint")).toBe("{id_token}");
  expect(providerLogout.searchParams.get("client_id")).toBe("tradingng-web");
  expect(providerLogout.searchParams.get("post_logout_redirect_uri")).toBe(
    "https://ushome.amycat.com/",
  );
});
