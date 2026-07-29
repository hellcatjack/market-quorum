const POST_LOGOUT_REDIRECT = "https://ushome.amycat.com/";
const KEYCLOAK_LOGOUT_PATH = "/realms/tradingng/protocol/openid-connect/logout";

const providerLogout =
  `${KEYCLOAK_LOGOUT_PATH}?id_token_hint={id_token}` +
  `&post_logout_redirect_uri=${encodeURIComponent(POST_LOGOUT_REDIRECT)}` +
  "&client_id=tradingng-web";

export const BROWSER_LOGOUT_URL =
  `/oauth2/sign_out?rd=${encodeURIComponent(providerLogout)}`;
