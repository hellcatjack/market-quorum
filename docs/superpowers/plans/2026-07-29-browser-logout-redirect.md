# Browser Logout Redirect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the management Web “Sign out” action terminate both the OAuth2 Proxy session and the browser’s Keycloak SSO session, then land directly in the login flow instead of automatically returning to the protected page.

**Architecture:** The Web exports one fixed, input-free logout URL. OAuth2 Proxy clears its own cookie and replaces `{id_token}` before redirecting the browser through Keycloak’s OIDC end-session endpoint; Keycloak clears the browser SSO session and returns to the platform root, where Caddy starts a fresh login. The clean-install Realm and the live public-URL synchronizer both enforce one exact post-logout redirect URI.

**Tech Stack:** React 19, TypeScript 5.9, Vitest/Testing Library, OAuth2 Proxy 7.15.1, Keycloak 26.3.5, Python 3.10, httpx, pytest, Caddy.

**Execution constraint:** Run inline in the current `main` worktree. The user explicitly prohibited subagents and additional worktrees.

---

## File map

- Create `web/src/auth/logout.ts`: fixed browser logout URL and no user-controlled redirect input.
- Create `web/src/auth/logout.test.ts`: structural assertions for both nested redirect layers.
- Modify `web/src/app/Layout.tsx`: consume the fixed URL with a normal anchor.
- Modify `web/src/app/App.test.tsx`: assert the rendered logout link uses the safe URL.
- Modify `deploy/keycloak/tradingng-realm.json`: clean-install post-logout redirect policy.
- Modify `scripts/sync_keycloak_public_urls.py`: idempotent production drift detection and repair.
- Modify `platform/tests/operations/test_keycloak_sync.py`: exact live reconciliation behavior.
- Modify `platform/tests/operations/test_deploy_config.py`: exact clean-install Realm behavior.
- Modify `README.md` and `README.zh-CN.md`: operator-visible logout semantics.

### Task 1: Safe front-channel logout URL

**Files:**
- Create: `web/src/auth/logout.test.ts`
- Create: `web/src/auth/logout.ts`
- Modify: `web/src/app/Layout.tsx`
- Modify: `web/src/app/App.test.tsx`

- [x] **Step 1: Write the failing URL structure test**

Create `web/src/auth/logout.test.ts`:

```ts
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
```

- [x] **Step 2: Update the shell test to require the new target**

In `web/src/app/App.test.tsx`, import `BROWSER_LOGOUT_URL` and replace the old literal assertion:

```ts
import { BROWSER_LOGOUT_URL } from "../auth/logout";

expect(screen.getByRole("link", { name: "退出" })).toHaveAttribute(
  "href",
  BROWSER_LOGOUT_URL,
);
```

- [x] **Step 3: Run the focused tests and verify RED**

Run:

```bash
cd /app/devs/TradingNG/web
npm test -- --run src/auth/logout.test.ts src/app/App.test.tsx
```

Expected: FAIL because `src/auth/logout.ts` and `BROWSER_LOGOUT_URL` do not exist. The failure must be import/expectation related, not a test-environment error.

- [x] **Step 4: Implement the fixed nested redirect URL**

Create `web/src/auth/logout.ts`:

```ts
const POST_LOGOUT_REDIRECT = "https://ushome.amycat.com/";
const KEYCLOAK_LOGOUT_PATH = "/realms/tradingng/protocol/openid-connect/logout";

const providerLogout =
  `${KEYCLOAK_LOGOUT_PATH}?id_token_hint={id_token}` +
  `&post_logout_redirect_uri=${encodeURIComponent(POST_LOGOUT_REDIRECT)}` +
  "&client_id=tradingng-web";

export const BROWSER_LOGOUT_URL =
  `/oauth2/sign_out?rd=${encodeURIComponent(providerLogout)}`;
```

The placeholder is deliberately encoded only by the outer `rd`: OAuth2 Proxy decodes `rd`, sees the raw `{id_token}`, replaces it from the authenticated session, clears its cookie, and redirects the browser to Keycloak.

- [x] **Step 5: Consume the URL with a full-page anchor**

In `web/src/app/Layout.tsx`:

```tsx
import { BROWSER_LOGOUT_URL } from "../auth/logout";

<a className="logout-link" href={BROWSER_LOGOUT_URL}>
  {t("退出")}
</a>
```

Do not use Wouter navigation or an asynchronous click handler; the browser must cross the Caddy/OAuth2 Proxy boundary.

- [x] **Step 6: Run focused tests, typecheck, and lint**

Run:

```bash
cd /app/devs/TradingNG/web
npm test -- --run src/auth/logout.test.ts src/app/App.test.tsx
npm run typecheck
npm run lint
```

Expected: both test files pass, TypeScript exits 0, and ESLint exits 0.

- [x] **Step 7: Commit the Web behavior**

```bash
cd /app/devs/TradingNG
git add web/src/auth/logout.ts web/src/auth/logout.test.ts web/src/app/Layout.tsx web/src/app/App.test.tsx
git commit -m "fix: route browser logout through Keycloak"
```

### Task 2: Exact Keycloak post-logout redirect policy

**Files:**
- Modify: `platform/tests/operations/test_keycloak_sync.py`
- Modify: `platform/tests/operations/test_deploy_config.py`
- Modify: `scripts/sync_keycloak_public_urls.py`
- Modify: `deploy/keycloak/tradingng-realm.json`

- [x] **Step 1: Write failing live-reconciliation assertions**

In `platform/tests/operations/test_keycloak_sync.py`, import the new constant:

```python
from sync_keycloak_public_urls import PUBLIC_WEB_POST_LOGOUT_REDIRECT
```

Give the mock Web client an unrelated attribute that must be preserved:

```python
"attributes": {"existing": "kept"},
```

Add the expected initial drift item:

```python
"tradingng-web.postLogoutRedirectUris",
```

Require the reconciled Web client payload to preserve the unrelated attribute and add the exact redirect:

```python
"attributes": {
    "existing": "kept",
    "post.logout.redirect.uris": PUBLIC_WEB_POST_LOGOUT_REDIRECT,
},
```

- [x] **Step 2: Write the failing clean-install Realm assertion**

In `platform/tests/operations/test_deploy_config.py`, after resolving the `tradingng-web` client, add:

```python
assert web["attributes"]["post.logout.redirect.uris"] == (
    "https://ushome.amycat.com/"
)
```

- [x] **Step 3: Run operations tests and verify RED**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest \
  platform/tests/operations/test_keycloak_sync.py \
  platform/tests/operations/test_deploy_config.py -q
```

Expected: FAIL because the synchronizer has no post-logout drift rule and the Realm client has no explicit attribute.

- [x] **Step 4: Add drift detection and idempotent repair**

In `scripts/sync_keycloak_public_urls.py`, add:

```python
PUBLIC_WEB_POST_LOGOUT_REDIRECT = f"{PUBLIC_BASE_URL}/"
```

In `PublicUrlSynchronizer.drift`, after the Web origin check:

```python
if web.get("attributes", {}).get("post.logout.redirect.uris") != (
    PUBLIC_WEB_POST_LOGOUT_REDIRECT
):
    drift.add("tradingng-web.postLogoutRedirectUris")
```

Extend the Web-client repair condition and payload in `apply`:

```python
if {
    "tradingng-web.redirectUris",
    "tradingng-web.webOrigins",
    "tradingng-web.postLogoutRedirectUris",
} & drift:
    web = deepcopy(snapshot.clients["tradingng-web"])
    web["redirectUris"] = [PUBLIC_WEB_REDIRECT]
    web["webOrigins"] = [PUBLIC_BASE_URL]
    web.setdefault("attributes", {})[
        "post.logout.redirect.uris"
    ] = PUBLIC_WEB_POST_LOGOUT_REDIRECT
    self._put(f"/admin/realms/{REALM}/clients/{web['id']}", web)
```

- [x] **Step 5: Add the clean-install client attribute**

In the `tradingng-web` object in `deploy/keycloak/tradingng-realm.json`, add:

```json
"attributes": {
  "post.logout.redirect.uris": "https://ushome.amycat.com/"
},
```

- [x] **Step 6: Run operations tests and formatting checks**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/pytest \
  platform/tests/operations/test_keycloak_sync.py \
  platform/tests/operations/test_deploy_config.py -q
.venv/bin/ruff check scripts/sync_keycloak_public_urls.py \
  platform/tests/operations/test_keycloak_sync.py \
  platform/tests/operations/test_deploy_config.py
.venv/bin/ruff format --check scripts/sync_keycloak_public_urls.py \
  platform/tests/operations/test_keycloak_sync.py \
  platform/tests/operations/test_deploy_config.py
```

Expected: all tests and formatting checks exit 0.

- [x] **Step 7: Commit the Keycloak desired state**

```bash
cd /app/devs/TradingNG
git add deploy/keycloak/tradingng-realm.json scripts/sync_keycloak_public_urls.py \
  platform/tests/operations/test_keycloak_sync.py \
  platform/tests/operations/test_deploy_config.py
git commit -m "fix: constrain browser post-logout redirect"
```

### Task 3: Operator documentation and complete regression verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Document the logout behavior in separate languages**

Add to the authentication/user-management section of `README.md`:

```markdown
Browser sign-out is front-channel: OAuth2 Proxy clears the application session,
Keycloak clears the browser SSO session, and the browser returns to the login
flow. The post-logout target is a fixed same-origin URI; it is not derived from
request input.
```

Add the corresponding text to `README.zh-CN.md`:

```markdown
浏览器退出采用前端退出链路：OAuth2 Proxy 清除应用会话，Keycloak 清除浏览器 SSO
会话，随后浏览器返回登录流程。退出后的目标是固定的同源地址，不接受请求输入。
```

- [ ] **Step 2: Run the complete repository verifier**

Run:

```bash
cd /app/devs/TradingNG
bash scripts/verify_platform.sh
```

Expected: Gateway, platform unit/integration/operations, random real MySQL, Web tests/lint/typecheck/build, npm audit, Caddy validation, Keycloak config checks, and artifact integrity all exit 0. Existing explicitly reported migration-database skips remain acceptable.

- [ ] **Step 3: Check the implementation against the design**

Run:

```bash
cd /app/devs/TradingNG
git diff --check
rg -n 'TB[D]|TO[D]O|implement la[t]er|fill in detai[l]s' \
  web/src/auth/logout.ts web/src/auth/logout.test.ts \
  scripts/sync_keycloak_public_urls.py README.md README.zh-CN.md
test -z "$(git status --short TradingAgents)"
```

Expected: diff check is silent, placeholder scan has no matches, and `TradingAgents/` is clean.

- [ ] **Step 4: Commit documentation**

```bash
cd /app/devs/TradingNG
git add README.md README.zh-CN.md
git commit -m "docs: explain front-channel browser logout"
```

### Task 4: Safe live reconciliation, static deployment, and logout smoke test

**Files:**
- Modify private runtime state only: Keycloak Realm configuration through the idempotent synchronizer.
- Build generated static assets only: `web/dist/` (gitignored).

- [ ] **Step 1: Prove deployment will not interrupt assessments**

Run the existing read-only activity query and service checks:

```bash
cd /app/devs/TradingNG
systemctl --user is-active tradingng-platform-api.service \
  tradingng-platform-scheduler.service 'tradingng-platform-worker@1.service'
curl -fsS http://127.0.0.1:8010/health/ready >/dev/null
```

Query MySQL through `Settings()` and assert that this deployment does not mutate any assessment row. Record active counts before and after; no platform service restart is required even if work is active because only Keycloak client metadata and static Web assets change.

- [ ] **Step 2: Reconcile the exact live post-logout URI**

Run:

```bash
cd /app/devs/TradingNG
PYTHONPATH=platform/src .venv/bin/python scripts/sync_keycloak_public_urls.py \
  --env-file .env.platform --check
PYTHONPATH=platform/src .venv/bin/python scripts/sync_keycloak_public_urls.py \
  --env-file .env.platform --apply
PYTHONPATH=platform/src .venv/bin/python scripts/sync_keycloak_public_urls.py \
  --env-file .env.platform --check
```

Expected: the first check reports at most the new stable drift count, apply prints no secret values, and the second check prints `keycloak_public_urls=ok`.

- [ ] **Step 3: Build and publish the static Web application**

Run:

```bash
cd /app/devs/TradingNG/web
npm run build
```

Expected: TypeScript and Vite exit 0. Caddy already serves `/app/devs/TradingNG/web/dist`, so no Caddy, Gateway, platform, scheduler, or worker restart is required.

- [ ] **Step 4: Exercise the authenticated front-channel chain**

Use an ephemeral Keycloak User and an in-memory OAuth Authorization Code + PKCE HTTP client. The harness must keep the password, ID token, cookies, authorization code, and CSRF state only in process memory and print only status names. It must:

1. create a uniquely named enabled User with a non-temporary test password and complete first/last name fields;
2. authenticate through `/oauth2/start?rd=/` and obtain the OAuth2 Proxy browser cookie;
3. request the rendered `BROWSER_LOGOUT_URL` without automatically following redirects;
4. assert the first `Location` is the Keycloak end-session endpoint, contains a real `id_token_hint` rather than `{id_token}`, has `client_id=tradingng-web`, and has the exact root post-logout URI;
5. follow the Keycloak redirect and then the root/Caddy redirect;
6. assert the final authorization response is the Keycloak login form and that no `/oauth2/callback` occurred without entering credentials again;
7. assert the old platform cookie cannot read the protected root or `/api/v1/me` without being redirected/rejected;
8. in `finally`, assign User, disable the test account, and revoke its sessions; never delete it and never print secrets.

Expected output contains only:

```text
logout_proxy_cookie_cleared=true
logout_keycloak_frontchannel=true
logout_returns_to_login=true
logout_old_session_rejected=true
logout_smoke=passed
```

- [ ] **Step 5: Verify live logs and unchanged business state**

Run redacted log/status checks:

```bash
docker logs --since 10m deploy-oauth2-proxy-1 2>&1 | \
  rg 'sign_out|/oauth2/start|/oauth2/callback|backend logout'
systemctl --user is-active tradingng-platform-api.service \
  tradingng-platform-scheduler.service 'tradingng-platform-worker@1.service'
curl -fsS -o /dev/null -w 'ready=%{http_code}\n' \
  http://127.0.0.1:8010/health/ready
git status --short --branch
git diff --check origin/main...HEAD
git ls-files .env .env.platform var reports
```

Expected: the smoke trace ends at a new login authorization page without an automatic callback, platform services remain active and ready, before/after assessment counts match, the repository is clean, and no private/runtime paths are tracked.

- [ ] **Step 6: Record acceptance only if source did not change during smoke**

If smoke testing requires no additional fix, mark every checklist item complete and commit only this plan:

```bash
cd /app/devs/TradingNG
git add docs/superpowers/plans/2026-07-29-browser-logout-redirect.md
git commit -m "docs: record browser logout acceptance"
```

If smoke exposes a reproducible defect, return to the relevant Task, add a failing regression test, implement the minimal fix, rerun the complete verifier, and commit that fix before recording acceptance. Never stage `.env.platform`, browser cookies, token output, Keycloak exports, or smoke credentials.
