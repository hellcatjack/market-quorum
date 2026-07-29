# Complete User Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable Admin/User account-management system backed by Keycloak, enforce role changes and account disabling immediately in the platform, isolate system diagnostics from ordinary users, and provide a bilingual administration UI.

**Architecture:** Keycloak remains authoritative for usernames, passwords, enabled state, formal roles, and sessions. A least-privilege `tradingng-user-admin` service account is called through a focused async client; `IdentityAdminService` applies safety rules, mirrors authoritative state into the existing MySQL `users`/`roles`/`user_roles` tables, and writes credential-free audits. Every human OIDC request is narrowed against the local mirror, while service accounts retain the existing client-credential path; the web app uses `/me` for route/navigation guards and consumes a separate sanitized assessment-admission summary.

**Tech Stack:** Python 3.10, FastAPI, Pydantic 2, SQLAlchemy async, httpx, Keycloak 26 Admin REST API, MySQL/Alembic, React 19, TypeScript 5.9, TanStack Query, Wouter, Vitest/Testing Library, pytest.

---

## File map and locked boundaries

The implementation must not modify anything under `TradingAgents/` and must remain on the current `main` worktree.

**Backend identity boundary**

- Create `platform/src/tradingng_platform/identity/__init__.py`: public identity package exports.
- Create `platform/src/tradingng_platform/identity/contracts.py`: local mirror type, Pydantic commands, views, page shape, session summary, and typed Keycloak representation.
- Create `platform/src/tradingng_platform/identity/errors.py`: stable domain error codes and HTTP-independent exception types.
- Create `platform/src/tradingng_platform/identity/keycloak.py`: token cache and Keycloak Admin REST transport only.
- Create `platform/src/tradingng_platform/identity/repository.py`: MySQL mirror lookup/upsert, formal-role replacement, enabled-admin locking/counting, and identity audit writes.
- Create `platform/src/tradingng_platform/identity/access.py`: request-time human-principal provisioning, disabled checks, and privilege narrowing.
- Create `platform/src/tradingng_platform/identity/service.py`: list/detail/create/update/reset/logout orchestration and lockout guards.
- Modify `platform/src/tradingng_platform/auth/oidc.py`: define only `Admin` and `User` human scope ceilings and retain the service-account bypass.
- Modify `platform/src/tradingng_platform/api/auth.py`: pass verified human principals through `IdentityAccessService`; add role-and-scope admin dependency.
- Modify `platform/src/tradingng_platform/assessments/repository.py`: stop assessment submission from reactivating users or overwriting admin-managed roles from token claims.
- Modify `platform/src/tradingng_platform/config.py`: add secret-safe Keycloak Admin settings.
- Modify `platform/src/tradingng_platform/api/app.py`: construct and close Keycloak/identity services.
- Create `platform/src/tradingng_platform/api/routes/users.py`: six admin user endpoints and stable error mapping.
- Modify `platform/src/tradingng_platform/api/routes/__init__.py`: register user routes and return effective `/me` identity.

**Capacity isolation boundary**

- Add the admission-summary contract to `platform/src/tradingng_platform/assessments/contracts.py`.
- Add the sanitized query to `platform/src/tradingng_platform/assessments/service.py` and its repository dependency.
- Modify `platform/src/tradingng_platform/api/routes/assessments.py`: expose `/assessments/admission-summary` under `assessments:read`.
- Keep `platform/src/tradingng_platform/api/routes/system.py` and MCP system resources protected by `system:read`; do not expose models, Gateway, host capacity, or vendor circuit names in the new response.

**Keycloak/deployment boundary**

- Modify `deploy/keycloak/tradingng-realm.json`: formal roles/scopes plus the management client definition for clean installs.
- Create `scripts/sync_keycloak_user_management.py`: idempotent live-realm reconciliation and legacy role migration.
- Create `platform/tests/operations/test_keycloak_user_management.py`: pure reconciliation-plan and config tests.
- Modify `.env.platform.example`, `scripts/verify_platform.sh`, `README.md`, and `README.zh-CN.md`: safe configuration and operational documentation.

**Frontend boundary**

- Create `web/src/auth/CurrentUserProvider.tsx`: one `/me` query and reusable role/scope helpers.
- Create `web/src/app/AuthorizedRoute.tsx`: no-fetch access-denied guard.
- Modify `web/src/app/App.tsx` and `web/src/app/Layout.tsx`: guarded system/users routes and conditional navigation.
- Create `web/src/api/users.ts`: typed management API calls; temporary password exists only in create/reset response types.
- Modify `web/src/api/assessments.ts`: call the sanitized admission endpoint for ordinary pages.
- Create `web/src/features/users/UserManagementPage.tsx`: dense searchable user ledger and action panel.
- Create `web/src/features/users/UserEditor.tsx`: create/edit form and safety affordances.
- Create `web/src/features/users/TemporaryPasswordDialog.tsx`: show/copy/clear one-time secret.
- Create `web/src/features/auth/AccessDeniedPage.tsx`: bilingual restricted-route result.
- Modify `web/src/i18n/messages.ts` and `web/src/styles/global.css`: full bilingual and responsive presentation.

## Invariants used by every task

```python
USER_SCOPES = frozenset({
    "assessments:read",
    "assessments:submit",
    "assessments:cancel",
    "assessments:review",
    "validations:read",
    "validations:write",
    "artifacts:read",
})
ADMIN_SCOPES = USER_SCOPES | frozenset({
    "system:read",
    "users:manage",
    "assessments:admin",
})
FORMAL_ROLES = frozenset({"Admin", "User"})
```

- A human principal is accepted only when its `sub` maps to an `active` local user with one formal role.
- A previously unseen valid human token can provision only `Admin` or `User`; legacy `Viewer`/`Analyst` claims are rejected after migration.
- Effective role is the lower privilege of token and local mirror: local `User` immediately demotes an old Admin token; local `Admin` never elevates an old User token.
- Effective scopes are `token scopes ∩ effective-role ceiling`.
- Service principals do not enter the human mirror path.
- `Admin` endpoints require both effective role `Admin` and scope `users:manage`.
- Identity mutations call Keycloak first, read the authoritative user back, then mirror and audit in one MySQL transaction.
- Temporary passwords, service-client secrets, access tokens, authorization headers, and raw upstream error bodies never enter logs, audit metadata, database fields, exception messages, or Pydantic repr output.
- No user-delete endpoint, method, button, or Keycloak delete call is added.

### Task 1: Formal role policy and request-time local enforcement

**Files:**
- Create: `platform/src/tradingng_platform/identity/__init__.py`
- Create: `platform/src/tradingng_platform/identity/contracts.py`
- Create: `platform/src/tradingng_platform/identity/repository.py`
- Create: `platform/src/tradingng_platform/identity/access.py`
- Modify: `platform/src/tradingng_platform/auth/oidc.py`
- Modify: `platform/src/tradingng_platform/api/auth.py`
- Modify: `platform/src/tradingng_platform/assessments/repository.py`
- Test: `platform/tests/unit/auth/test_oidc.py`
- Create test: `platform/tests/unit/identity/test_access.py`
- Modify test: `platform/tests/unit/assessments/test_repository.py`

- [x] **Step 1: Replace legacy scope expectations with the formal matrix in failing OIDC tests**

Update `test_human_scopes_are_bounded_by_realm_role` so its cases are exactly `User` and `Admin`, include `assessments:review` and `users:manage` in the candidate token, assert User has no `system:read`, and add:

```python
async def test_legacy_human_role_has_no_platform_scopes(oidc_server):
    private_key, transport, _ = oidc_server
    token = _encode_token(
        private_key,
        scope="assessments:read system:read",
        realm_access={"roles": ["Analyst"]},
    )
    async with httpx.AsyncClient(transport=transport) as client:
        principal = await OidcVerifier(ISSUER, AUDIENCE, client=client).verify(token)
    assert principal.scopes == frozenset()
```

- [x] **Step 2: Run the OIDC test and confirm the old Viewer/Analyst policy fails**

Run: `cd platform && ../.venv/bin/pytest tests/unit/auth/test_oidc.py -q`

Expected: FAIL because `User` is not recognized and the legacy roles still receive scopes.

- [x] **Step 3: Implement the formal scope ceilings**

In `auth/oidc.py`, export `FORMAL_ROLES`, `USER_SCOPES`, `ADMIN_SCOPES`, and `ROLE_SCOPES`; make `_human_scopes()` use only `User` and `Admin`. Preserve the current actor-type detection and leave service principals unbounded by human roles.

- [x] **Step 4: Add failing access-service tests for all stale-token transitions**

Use a fake repository exposing `resolve(principal)` and assert these exact outcomes in `test_access.py`:

```python
from datetime import datetime, timezone
from dataclasses import dataclass
from uuid import uuid4

@dataclass
class FakeIdentityRepository:
    identity: LocalIdentity | None
    provisioned: bool = False
    read_count: int = 0

    async def get_human(self, issuer: str, subject: str, *, for_update: bool = False):
        self.read_count += 1
        return self.identity

    async def provision_from_principal(self, principal: Principal, role: str):
        self.provisioned = True
        self.identity = local_identity(role)
        return self.identity

def token_principal(role: str) -> Principal:
    return Principal(
        issuer="https://issuer.example/realms/tradingng",
        subject="alice-sub",
        actor_type="user",
        scopes=ADMIN_SCOPES,
        display_name="Alice",
        email="alice@example.com",
        roles=frozenset({role}),
    )

def local_identity(role: str, status: str = "active") -> LocalIdentity:
    return LocalIdentity(
        id=uuid4(),
        issuer="https://issuer.example/realms/tradingng",
        subject="alice-sub",
        display_name="Alice",
        email="alice@example.com",
        status=status,
        role=role,
        synced_at=datetime.now(timezone.utc),
    )

@pytest.mark.parametrize(
    ("token_role", "local_role", "expected_role", "has_system"),
    [
        ("User", "User", "User", False),
        ("Admin", "Admin", "Admin", True),
        ("Admin", "User", "User", False),
        ("User", "Admin", "User", False),
    ],
)
async def test_effective_role_never_exceeds_token_or_local_role(
    token_role, local_role, expected_role, has_system
):
    repository = FakeIdentityRepository(identity=local_identity(local_role))
    effective = await IdentityAccessService(repository).enforce(token_principal(token_role))
    assert effective.roles == frozenset({expected_role})
    assert ("system:read" in effective.scopes) is has_system

async def test_disabled_local_user_is_rejected():
    repository = FakeIdentityRepository(identity=local_identity("User", "disabled"))
    with pytest.raises(ApiError) as captured:
        await IdentityAccessService(repository).enforce(token_principal("User"))
    assert captured.value.code == "account_disabled"

async def test_unknown_user_with_legacy_role_is_rejected():
    repository = FakeIdentityRepository(identity=None)
    with pytest.raises(ApiError) as captured:
        await IdentityAccessService(repository).enforce(token_principal("Analyst"))
    assert captured.value.code == "identity_not_provisioned"
    assert repository.provisioned is False
```

The same file must include complete formal-role provisioning and service-principal tests. The repository fake records `provisioned` and `read_count`, proving a new User is inserted once while legacy and service cases perform no accidental insert.

- [x] **Step 5: Run access tests and confirm the package is missing**

Run: `cd platform && ../.venv/bin/pytest tests/unit/identity/test_access.py -q`

Expected: collection FAIL with `ModuleNotFoundError: tradingng_platform.identity`.

- [x] **Step 6: Implement the mirror repository and access narrowing**

Define immutable `LocalIdentity(id, issuer, subject, display_name, email, status, role, synced_at)` in `contracts.py`. `IdentityRepository` must initially provide:

```python
async def get_human(self, issuer: str, subject: str, *, for_update: bool = False) -> LocalIdentity | None
async def provision_from_principal(self, principal: Principal, role: Literal["Admin", "User"]) -> LocalIdentity
```

`IdentityAccessService.enforce(principal)` must:

1. return service principals unchanged;
2. load the local identity by issuer/subject;
3. provision only when exactly one formal token role exists;
4. reject `disabled` as `ApiError(403, "account_disabled", "This account is disabled")`;
5. calculate the lower-privilege role and intersect the already token-bounded scopes with its ceiling;
6. return a new immutable `Principal` with effective formal role/scopes.

Keep repository exceptions framework-neutral; only `access.py` may emit the request-facing authorization error.

- [x] **Step 7: Route OIDC human requests through the access service**

In `current_principal`, verify the bearer token first, then call `request.app.state.identity_access.enforce(principal)`. Add:

```python
def require_admin_scope(scope: str = "users:manage") -> Callable:
    async def dependency(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if "Admin" not in principal.roles or scope not in principal.scopes:
            raise ApiError(403, "insufficient_scope", "Administrator permission is required")
        return principal
    return dependency
```

API tokens retain their existing owner-status check and must not be passed through OIDC provisioning.

- [x] **Step 8: Stop assessment submission from overwriting managed identity state**

Refactor `AssessmentRepository.upsert_user()` to use the already enforced user, update only non-authoritative profile fields for an existing active row, never set `status = "active"` after insertion, and never replace formal roles from stale claims. Add repository tests proving an existing disabled user remains disabled and a locally demoted User remains User when an old Admin principal reaches this method.

- [x] **Step 9: Run the focused backend tests**

Run: `cd platform && ../.venv/bin/pytest tests/unit/auth/test_oidc.py tests/unit/identity/test_access.py tests/unit/assessments/test_repository.py -q`

Expected: PASS.

- [x] **Step 10: Commit the formal access boundary**

```bash
git add platform/src/tradingng_platform/identity platform/src/tradingng_platform/auth/oidc.py platform/src/tradingng_platform/api/auth.py platform/src/tradingng_platform/assessments/repository.py platform/tests/unit/auth/test_oidc.py platform/tests/unit/identity/test_access.py platform/tests/unit/assessments/test_repository.py
git commit -m "feat: enforce formal user roles on every request"
```

### Task 2: Secret-safe Keycloak Admin client

**Files:**
- Modify: `platform/src/tradingng_platform/config.py`
- Modify: `platform/src/tradingng_platform/identity/contracts.py`
- Create: `platform/src/tradingng_platform/identity/errors.py`
- Create: `platform/src/tradingng_platform/identity/keycloak.py`
- Modify: `platform/src/tradingng_platform/identity/__init__.py`
- Modify test: `platform/tests/unit/test_config.py`
- Create test: `platform/tests/unit/identity/test_keycloak.py`

- [x] **Step 1: Write failing settings tests for private Keycloak configuration**

Add a test that sets the four documented `TRADINGNG_KEYCLOAK_ADMIN_*` variables, asserts the URL/realm/client id, asserts `get_secret_value()` for the secret, and proves neither `repr(settings)` nor `settings.model_dump_json()` contains the secret. Add a second test proving an unset secret yields `None` so health/read-only endpoints can start while the management feature reports a configured 503.

- [x] **Step 2: Run settings tests and observe missing fields**

Run: `cd platform && ../.venv/bin/pytest tests/unit/test_config.py -q`

Expected: FAIL on missing `keycloak_admin_url` or `keycloak_admin_client_secret`.

- [x] **Step 3: Add settings with non-leaking defaults**

Add:

```python
keycloak_admin_url: AnyHttpUrl = "http://127.0.0.1:18081"
keycloak_admin_realm: str = "tradingng"
keycloak_admin_client_id: str = "tradingng-user-admin"
keycloak_admin_client_secret: SecretStr | None = Field(default=None, repr=False)
keycloak_admin_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
```

Exclude the secret from serialization and validate realm/client identifiers as non-empty path-safe names.

- [x] **Step 4: Define complete client contracts and stable errors**

`contracts.py` must include immutable `KeycloakUser`, `KeycloakSession`, and `KeycloakPage` types. `KeycloakUser` fields are `subject`, `username`, `display_name`, `email`, `enabled`, `role`; role is exactly `Admin|User`. `errors.py` must define `IdentityError(code, status_code, message)` subclasses/makers for conflict, missing, forbidden configuration, unavailable provider, and sync pending; `str(error)` must contain only stable code/message.

- [x] **Step 5: Write MockTransport tests for the full Keycloak protocol**

Create tests that assert:

- token POST is form encoded to `/realms/tradingng/protocol/openid-connect/token`, cached until 30 seconds before expiry, and refreshed once under concurrent expiry;
- list uses `first`, `max`, `search`; detail fetches realm-role mappings and rejects zero or multiple formal roles;
- create POST extracts the subject from the `Location` header;
- profile/enabled update uses PUT;
- role replacement deletes prior formal mappings then posts exactly one role;
- password reset PUT sends `temporary: true` and no password is retained by the client object;
- logout POST targets `/admin/realms/tradingng/users/{id}/logout`;
- sessions GET maps only id/start/last-access timestamps;
- 409 maps by operation to `username_conflict` or `email_conflict`, 404 to `user_not_found`, 401/403 to `identity_provider_forbidden`, and timeout/429/5xx to `identity_provider_unavailable` without upstream body text.

- [x] **Step 6: Run the client tests and confirm missing implementation**

Run: `cd platform && ../.venv/bin/pytest tests/unit/identity/test_keycloak.py -q`

Expected: collection or import FAIL because `KeycloakAdminClient` does not exist.

- [x] **Step 7: Implement the async Keycloak transport**

Give `KeycloakAdminClient` these exact public methods:

```python
async def list_users(self, *, search: str | None, first: int, maximum: int) -> KeycloakPage
async def get_user(self, subject: str) -> KeycloakUser
async def create_user(self, *, username: str, display_name: str, email: str, enabled: bool) -> str
async def update_user(self, subject: str, *, display_name: str, email: str, enabled: bool) -> None
async def replace_role(self, subject: str, role: Literal["Admin", "User"]) -> None
async def set_temporary_password(self, subject: str, password: str) -> None
async def logout(self, subject: str) -> None
async def sessions(self, subject: str) -> tuple[KeycloakSession, ...]
async def close(self) -> None
```

Use one injected/owned `httpx.AsyncClient`, an `asyncio.Lock` around token refresh, `time.monotonic()` for expiry, and a redacting operation-specific response mapper. Never include request JSON for password calls in debug logging.

- [x] **Step 8: Run and lint the client slice**

Run: `cd platform && ../.venv/bin/pytest tests/unit/test_config.py tests/unit/identity/test_keycloak.py -q && ../.venv/bin/ruff check src/tradingng_platform/config.py src/tradingng_platform/identity tests/unit/identity`

Expected: all tests PASS and Ruff exits 0.

- [x] **Step 9: Commit the Keycloak client**

```bash
git add platform/src/tradingng_platform/config.py platform/src/tradingng_platform/identity platform/tests/unit/test_config.py platform/tests/unit/identity
git commit -m "feat: add secure Keycloak administration client"
```

### Task 3: Identity administration service, lockout protection, and audits

**Files:**
- Modify: `platform/src/tradingng_platform/identity/contracts.py`
- Modify: `platform/src/tradingng_platform/identity/repository.py`
- Create: `platform/src/tradingng_platform/identity/service.py`
- Modify: `platform/src/tradingng_platform/identity/__init__.py`
- Modify: `platform/pyproject.toml`
- Create test: `platform/tests/unit/identity/test_service.py`
- Create test: `platform/tests/integration/test_identity_management.py`

- [x] **Step 1: Define API-facing identity commands and views**

Add strict Pydantic models:

```python
class CreateUserCommand(BaseModel):
    username: str  # 3..64, lower-case normalized, [a-z0-9._-]
    display_name: str  # 1..255 after strip
    email: EmailStr
    role: Literal["Admin", "User"]

class UpdateUserCommand(BaseModel):
    display_name: str | None = None
    email: EmailStr | None = None
    role: Literal["Admin", "User"] | None = None
    enabled: bool | None = None

class UserView(BaseModel):
    id: UUID
    subject: str
    username: str
    display_name: str
    email: str | None
    role: Literal["Admin", "User"]
    enabled: bool
    synced_at: datetime

class TemporaryPasswordView(BaseModel):
    user: UserView
    temporary_password: SecretStr
```

Also define `UserPage(items, page, page_size, total)`, `UserDetailView` with session summary and allowed-action flags, and a dedicated response serializer that reveals `temporary_password` only for HTTP create/reset responses.

Add `email-validator>=2.2,<3` to `platform/pyproject.toml` so `EmailStr` validation is deterministic in API and service tests.

- [x] **Step 2: Write service tests with a fake Keycloak client and fake repository**

Cover list filtering/paging, detail action flags, password length/entropy shape, create operation order, profile update, role update, enabled update, reset+logout, explicit logout, and tests named `test_current_admin_cannot_disable_self`, `test_current_admin_cannot_demote_self`, `test_last_enabled_admin_cannot_be_disabled`, `test_last_enabled_admin_cannot_be_demoted`, `test_create_returns_secret_once_and_audit_does_not_contain_it`, and `test_keycloak_success_mysql_failure_becomes_sync_pending`.

Assert the first two self-operation tests raise code `self_admin_change_forbidden`; assert both last-admin tests raise `last_admin_protected`. Assert call order for writes is Keycloak mutation, authoritative `get_user`, mirror sync, audit commit. Assert role/status changes call logout after the change. Assert a profile-only update does not revoke sessions.

- [x] **Step 3: Run service tests and confirm failure**

Run: `cd platform && ../.venv/bin/pytest tests/unit/identity/test_service.py -q`

Expected: FAIL because `IdentityAdminService` and commands are incomplete.

- [x] **Step 4: Complete repository transactional primitives**

Add:

```python
async def sync_authoritative(self, user: KeycloakUser, issuer: str) -> LocalIdentity
async def replace_formal_role(self, user_id: UUID, role: Literal["Admin", "User"]) -> None
async def acquire_admin_guard(self) -> None
async def enabled_admin_count(self) -> int
async def append_audit(self, principal: Principal, action: str, target: LocalIdentity, request_id: str, metadata: dict) -> None
async def commit(self) -> None
async def rollback(self) -> None
```

Use `acquire_transaction_lock(session, "identity-admin-guard")`. Formal-role replacement must delete only `Admin`, `User`, `Analyst`, and `Viewer` associations, then insert exactly one current formal role. Audit metadata is allow-listed to `changed_fields`, `old_role`, `new_role`, `old_status`, `new_status`, `keycloak_subject`, and `result`; reject keys containing `password`, `secret`, `token`, or `authorization`.

Use these audit action names exactly: `user.create`, `user.profile_update`, `user.role_change`, `user.enable`, `user.disable`, `user.password_reset`, `user.logout`, and `user.reconcile`. List/detail reconciliation writes `user.reconcile` only when authoritative Keycloak state actually changes the local mirror, so repeated reads do not create audit noise.

- [x] **Step 5: Implement orchestration and password generation**

`IdentityAdminService` public methods must be:

```python
async def list_users(self, principal, *, search, role, status, page, page_size) -> UserPage
async def get_user(self, principal, user_id: UUID) -> UserDetailView
async def create_user(self, principal, command, request_id: str) -> TemporaryPasswordView
async def update_user(self, principal, user_id: UUID, command, request_id: str) -> UserDetailView
async def reset_password(self, principal, user_id: UUID, request_id: str) -> TemporaryPasswordView
async def logout_user(self, principal, user_id: UUID, request_id: str) -> UserDetailView
```

Generate 32 URL-safe random bytes with `secrets.token_urlsafe(32)` and enforce at least 24 characters. Wrap it in `SecretStr` immediately. For create: create disabled, assign role, set temporary password, enable, logout, read back, mirror, audit. On a failure before enable, leave the new account disabled; never compensate by deleting it. Convert a mirror commit failure after an authoritative mutation to `identity_sync_pending`.

- [x] **Step 6: Add database integration tests for mirror preservation and audit**

Using `session_factory` and a fake Keycloak client, prove:

- an existing platform user UUID is preserved across reconciliation;
- legacy Analyst/Viewer role links become one User role;
- disabled state blocks API credentials through the existing token verifier;
- audits store action, actor, target and allow-listed before/after data;
- serialized rows contain no generated password;
- repeated authoritative sync is idempotent.

- [x] **Step 7: Run identity unit and integration tests**

Run unit tests: `cd platform && ../.venv/bin/pytest tests/unit/identity -q`

Expected: PASS.

Run integration tests: `cd platform && TRADINGNG_TEST_DATABASE_URL="${TRADINGNG_TEST_DATABASE_URL:-}" ../.venv/bin/pytest tests/integration/test_identity_management.py -q`

Expected: PASS when the test DB URL is configured; otherwise SKIP with the existing explicit fixture reason.

- [x] **Step 8: Commit the administration domain**

```bash
git add platform/src/tradingng_platform/identity platform/pyproject.toml platform/tests/unit/identity platform/tests/integration/test_identity_management.py
git commit -m "feat: add audited identity administration service"
```

### Task 4: Admin REST API and application lifecycle wiring

**Files:**
- Create: `platform/src/tradingng_platform/api/routes/users.py`
- Modify: `platform/src/tradingng_platform/api/routes/__init__.py`
- Modify: `platform/src/tradingng_platform/api/app.py`
- Modify: `platform/src/tradingng_platform/api/errors.py`
- Create test: `platform/tests/unit/api/test_users.py`
- Modify test: `platform/tests/unit/api/test_app.py`
- Modify test: `platform/tests/integration/test_rest_api.py`

- [x] **Step 1: Write API contract tests for all six endpoints**

Use dependency overrides and a recording fake service. Assert:

- Admin+`users:manage` receives list/detail, 201 create, patch result, password result, and logout result;
- User with a forged `users:manage` scope but no Admin role receives 403;
- Admin role without scope receives 403;
- create/reset JSON contains the temporary password once;
- no DELETE route exists (`DELETE /api/v1/admin/users/{id}` returns 405);
- validation rejects mutable username on PATCH and invalid role values;
- each stable `IdentityError` maps to the designed 400/403/404/409/503 JSON error code and preserves request id.

- [x] **Step 2: Run route tests and see 404 failures**

Run: `cd platform && ../.venv/bin/pytest tests/unit/api/test_users.py -q`

Expected: FAIL with endpoint 404 responses.

- [x] **Step 3: Implement thin routes and error mapping**

Implement the exact routes:

```text
GET  /api/v1/admin/users
GET  /api/v1/admin/users/{user_id}
POST /api/v1/admin/users
PATCH /api/v1/admin/users/{user_id}
POST /api/v1/admin/users/{user_id}/reset-password
POST /api/v1/admin/users/{user_id}/logout
```

All routes depend on `require_admin_scope("users:manage")`. They pass `request_id_for(request)` into writes and contain no Keycloak logic. Register one `IdentityError` exception handler that emits the stable error envelope without upstream details.

- [x] **Step 4: Construct services in FastAPI lifespan**

Create `KeycloakAdminClient` only when the secret is configured; otherwise inject an unavailable client whose management methods raise `identity_provider_forbidden`. Store `identity_access` and `identity_admin` on `app.state`. Close the owned async client during lifespan shutdown after requests stop, without affecting the database lifecycle.

For testability, extend `create_app()` with optional `keycloak_admin` and `identity_admin` injection arguments rather than monkeypatching internals.

- [x] **Step 5: Add `/me` and disabled-session integration assertions**

Assert `/me` returns the effective local formal role and narrowed scopes. Add a request sequence where an Admin token works, the local mirror changes to User, and the same token immediately receives 403 on `/admin/users`; then set disabled and prove the same token receives `account_disabled` on `/me`.

- [x] **Step 6: Run API and auth tests**

Run: `cd platform && ../.venv/bin/pytest tests/unit/api/test_users.py tests/unit/api/test_app.py tests/unit/auth tests/unit/identity -q`

Expected: PASS.

- [x] **Step 7: Export OpenAPI and prove user routes are complete**

Run: `cd /app/devs/TradingNG && .venv/bin/python scripts/export_openapi.py && rg -n 'admin/users|users:manage|admission-summary' var/openapi.json`

Expected at this stage: six `admin/users` operations and `users:manage`; `admission-summary` is added in Task 5.

- [x] **Step 8: Commit the REST boundary**

```bash
git add platform/src/tradingng_platform/api platform/tests/unit/api/test_users.py platform/tests/unit/api/test_app.py platform/tests/integration/test_rest_api.py
git commit -m "feat: expose protected user administration API"
```

### Task 5: Sanitized assessment admission summary

**Files:**
- Modify: `platform/src/tradingng_platform/assessments/contracts.py`
- Modify: `platform/src/tradingng_platform/assessments/service.py`
- Modify: `platform/src/tradingng_platform/api/routes/assessments.py`
- Modify: `web/src/api/assessments.ts`
- Modify: `web/src/features/dashboard/DashboardPage.tsx`
- Modify: `web/src/features/assessments/AssessmentForm.tsx`
- Modify test: `platform/tests/unit/api/test_assessments.py`
- Modify test: `platform/tests/integration/test_rest_api.py`
- Modify test: `web/src/features/dashboard/DashboardPage.test.tsx`
- Modify test: `web/src/features/assessments/AssessmentForm.test.tsx`

- [x] **Step 1: Add failing backend tests for the sanitized response**

Expect `GET /api/v1/assessments/admission-summary` under `assessments:read` to return only:

```json
{
  "running": 2,
  "max_running": 4,
  "queued": 3,
  "oldest_queued_seconds": 18,
  "admission": "queued",
  "reason": "capacity_busy"
}
```

Allow `admission` values `immediate|queued|paused` and reason values `capacity_available|capacity_busy|temporarily_paused`. Explicitly assert the response lacks `gateway`, `model`, `reasoning`, `circuit`, `vendor`, CPU, memory, hard maximum, and internal admission reasons. Assert `system/capacity` remains 403 to User.

- [x] **Step 2: Run backend tests and observe 404**

Run: `cd platform && ../.venv/bin/pytest tests/unit/api/test_assessments.py -q -k admission_summary`

Expected: FAIL with 404.

- [x] **Step 3: Implement the summary by projecting existing capacity state**

Add `AdmissionSummaryView` and an `AssessmentService.admission_summary(principal)` method. Reuse the system/scheduler query internally, but map every low-level block reason into one stable user category before returning. The route requires only `assessments:read`. Do not alter MCP `tradingng://system/capacity`, which remains protected by `system:read`.

- [x] **Step 4: Switch ordinary pages to the new endpoint**

In `web/src/api/assessments.ts`, replace the ordinary `fetchCapacity()` call with `fetchAdmissionSummary()` targeting `/api/v1/assessments/admission-summary`. Give `CapacityBanner` a sanitized `AdmissionSummary` mode or create an adjacent compact `AdmissionBanner`; the System page keeps its full system-capacity type and endpoint.

- [x] **Step 5: Prove ordinary pages never call system diagnostics**

Update Dashboard and AssessmentForm tests to record requested URLs and assert:

```typescript
expect(requests).toContain("/api/v1/assessments/admission-summary");
expect(requests.some((url) => url.includes("/system/"))).toBe(false);
```

- [x] **Step 6: Run backend and web slices**

Run: `cd platform && ../.venv/bin/pytest tests/unit/api/test_assessments.py tests/integration/test_rest_api.py -q`

Expected: PASS or only the configured integration DB skip.

Run: `cd web && npm test -- --run src/features/dashboard/DashboardPage.test.tsx src/features/assessments/AssessmentForm.test.tsx`

Expected: PASS.

- [x] **Step 7: Commit diagnostic isolation**

```bash
git add platform/src/tradingng_platform/assessments platform/src/tradingng_platform/api/routes/assessments.py platform/tests/unit/api/test_assessments.py platform/tests/integration/test_rest_api.py web/src/api/assessments.ts web/src/features/dashboard web/src/features/assessments
git commit -m "feat: isolate system diagnostics from ordinary users"
```

### Task 6: Idempotent Keycloak realm and legacy-role migration

**Files:**
- Modify: `deploy/keycloak/tradingng-realm.json`
- Create: `scripts/sync_keycloak_user_management.py`
- Create: `platform/tests/operations/test_keycloak_user_management.py`
- Modify: `.env.platform.example`
- Modify: `scripts/verify_platform.sh`
- Modify: `platform/pyproject.toml`

- [x] **Step 1: Write deployment tests against the desired realm JSON**

Assert the clean-install realm has:

- realm roles `Admin` and `User`, with no Viewer/Analyst;
- client scopes `assessments:review` and `users:manage`;
- the web client carries all candidate human scopes;
- `tradingng-user-admin` is confidential, service-account enabled, standard/direct grants disabled;
- its secret comes from `${TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET}`;
- no management client scope is granted to web, API, or MCP clients.

- [x] **Step 2: Write pure migration-plan tests**

Import the script as a module and feed recorded realm snapshots. Assert `plan(snapshot)`:

- creates only absent scopes/role/client;
- maps each enabled or disabled Viewer/Analyst user to User while preserving password/profile/enabled state;
- preserves Admin users;
- plans logout only for users whose role changes;
- refuses to apply when there is no enabled Admin;
- produces an empty mutation list on the converged second snapshot;
- never prints or stores the client secret in its report.

- [x] **Step 3: Run operations tests and see desired-state failures**

Run: `cd platform && ../.venv/bin/pytest tests/operations/test_keycloak_user_management.py tests/operations/test_deploy_config.py -q`

Expected: FAIL because the role, scopes, client, and script do not exist.

- [x] **Step 4: Update clean-install realm configuration**

Replace Viewer/Analyst realm roles with User; add the two scopes; add them to the web client candidate/default scopes; add `assessments:review` where assessment service clients need it; add `tradingng-user-admin` with only `basic` as a default client scope. Do not embed realm-management roles in browser tokens.

- [x] **Step 5: Implement the live synchronizer**

Follow the authenticated request pattern in `scripts/sync_keycloak_public_urls.py`, but keep this script focused on identity management. Provide `--check` and `--apply`; `--check` exits 1 on drift and 0 when converged. `--apply` must:

1. authenticate to master only with existing bootstrap credentials for deployment reconciliation;
2. validate at least one enabled Admin before mutating users;
3. create scopes/role/client if absent;
4. configure the management service account with only `query-users`, `view-users`, and `manage-users` realm-management roles;
5. migrate legacy user assignments to User and revoke changed-user sessions;
6. remove legacy role assignments, then remove unused legacy roles;
7. emit counts and stable action names only;
8. never print the bootstrap password, management client secret, or tokens.

The runtime platform never uses bootstrap credentials. Add a `tradingng-platform-identity-sync` console entry if the synchronizer shares packaged identity reconciliation helpers; otherwise keep the standalone script importable by operations tests.

- [x] **Step 6: Add safe example configuration and config verification**

Add the four documented management variables with blank/placeholder secrets to `.env.platform.example`. Extend `scripts/verify_platform.sh` to parse the realm JSON, validate the new scopes/client, and ensure tracked files contain no non-placeholder client secret.

- [x] **Step 7: Run deployment tests and config verification**

Run: `cd platform && ../.venv/bin/pytest tests/operations/test_keycloak_user_management.py tests/operations/test_deploy_config.py -q`

Expected: PASS.

Run: `cd /app/devs/TradingNG && bash scripts/verify_platform.sh`

Expected: all static/config checks PASS; database-dependent checks use the script's existing environment behavior.

- [x] **Step 8: Commit the realm migration**

```bash
git add deploy/keycloak/tradingng-realm.json scripts/sync_keycloak_user_management.py platform/tests/operations/test_keycloak_user_management.py platform/tests/operations/test_deploy_config.py .env.platform.example scripts/verify_platform.sh platform/pyproject.toml
git commit -m "feat: provision formal Keycloak user management roles"
```

### Task 7: Shared frontend authorization and no-fetch route guards

**Files:**
- Create: `web/src/auth/CurrentUserProvider.tsx`
- Create: `web/src/auth/CurrentUserProvider.test.tsx`
- Create: `web/src/app/AuthorizedRoute.tsx`
- Create: `web/src/features/auth/AccessDeniedPage.tsx`
- Modify: `web/src/app/App.tsx`
- Modify: `web/src/app/Layout.tsx`
- Modify test: `web/src/app/App.test.tsx`

- [x] **Step 1: Write failing navigation and direct-route tests**

Render the app with Admin and User `/me` responses. Assert Admin sees System Status and User Management links. Assert User sees neither. Navigate directly to `/system` and `/users` as User, assert the bilingual access-denied heading, and assert the recorded fetch list contains neither `/system/` nor `/admin/users`.

- [x] **Step 2: Run the app tests and observe unconditional links/requests**

Run: `cd web && npm test -- --run src/app/App.test.tsx`

Expected: FAIL because system navigation is unconditional and `/users` is absent.

- [x] **Step 3: Implement one current-user source of truth**

`CurrentUserProvider` owns the TanStack query `queryKey: ["current-user"]` for `/api/v1/me` and exposes:

```typescript
interface CurrentUser {
  subject: string;
  display_name: string;
  email: string | null;
  scopes: string[];
  roles: string[];
}
interface CurrentUserContextValue {
  user?: CurrentUser;
  isLoading: boolean;
  isError: boolean;
  hasRole(role: "Admin" | "User"): boolean;
  hasScope(scope: string): boolean;
}
```

Remove the duplicate `/me` query from `Layout`.

- [x] **Step 4: Implement guards before protected page mounting**

`AuthorizedRoute` receives `role` and `scope`. While identity loads it renders a status shell; when denied it renders `AccessDeniedPage`; only when allowed does it render `children`. Wrap `/system` with Admin+`system:read` and `/users` with Admin+`users:manage`. Because children are not mounted when denied, their queries cannot run.

- [x] **Step 5: Make navigation conditional on the same effective identity**

Always show Overview and New Assessment. Show System Status only when Admin+`system:read`; show User Management only when Admin+`users:manage`. Keep the existing locale and sign-out controls.

- [x] **Step 6: Run authorization tests**

Run: `cd web && npm test -- --run src/auth/CurrentUserProvider.test.tsx src/app/App.test.tsx`

Expected: PASS.

- [x] **Step 7: Commit the frontend authorization shell**

```bash
git add web/src/auth web/src/app web/src/features/auth
git commit -m "feat: guard administrative web routes"
```

### Task 8: User management API client and one-time credential behavior

**Files:**
- Create: `web/src/api/users.ts`
- Create: `web/src/api/users.test.ts`
- Create: `web/src/features/users/TemporaryPasswordDialog.tsx`
- Create: `web/src/features/users/TemporaryPasswordDialog.test.tsx`

- [x] **Step 1: Write typed-client request tests**

Assert exact method/path/body combinations for list, detail, create, patch, reset-password, and logout. Assert list query encoding supports `search`, `role`, `status`, `page`, `page_size`; no client delete function may exist.

- [x] **Step 2: Write one-time password lifecycle tests**

Render the dialog with `temporaryPassword="secret-once"`, copy it, close it, rerender the owning harness, and assert the value no longer appears in the DOM or owner state. Assert Escape and the explicit acknowledgment button both invoke a `clearAndClose()` callback; do not render the secret into an aria-label or notification message.

- [x] **Step 3: Run tests and confirm missing modules**

Run: `cd web && npm test -- --run src/api/users.test.ts src/features/users/TemporaryPasswordDialog.test.tsx`

Expected: FAIL because both modules are missing.

- [x] **Step 4: Implement the API client and secret dialog**

Use API types matching the OpenAPI response. Only `CreatedUserResponse` and `ResetPasswordResponse` include `temporary_password: string`; `UserView` and cached list/detail types never include it. The dialog uses local selection/copy state, calls `navigator.clipboard.writeText`, reports only “copied” status, and clears secret state before closing.

- [x] **Step 5: Run the focused frontend tests**

Run: `cd web && npm test -- --run src/api/users.test.ts src/features/users/TemporaryPasswordDialog.test.tsx`

Expected: PASS.

- [ ] **Step 6: Commit typed user operations**

```bash
git add web/src/api/users.ts web/src/api/users.test.ts web/src/features/users/TemporaryPasswordDialog.tsx web/src/features/users/TemporaryPasswordDialog.test.tsx
git commit -m "feat: add safe web user administration client"
```

### Task 9: Dense user management page and safety actions

**Files:**
- Create: `web/src/features/users/UserEditor.tsx`
- Create: `web/src/features/users/UserEditor.test.tsx`
- Create: `web/src/features/users/UserManagementPage.tsx`
- Create: `web/src/features/users/UserManagementPage.test.tsx`
- Modify: `web/src/app/App.tsx`
- Modify: `web/src/i18n/messages.ts`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write page behavior tests**

Test search debounce, role/status filters, pagination, row detail selection, create form, profile edit, role edit, enable/disable confirmation, reset confirmation, logout confirmation, query invalidation, and stable API error messages. The fixture detail must include action flags:

```typescript
allowed_actions: {
  edit_profile: true,
  change_role: false,
  change_enabled: false,
  reset_password: true,
  logout: true,
}
action_reasons: {
  change_role: "self_admin_change_forbidden",
  change_enabled: "self_admin_change_forbidden",
}
```

Assert protected controls are disabled with visible explanations, not merely hidden.

Add a second fixture for a different last enabled Admin whose role/enabled flags are disabled with `last_admin_protected`, while profile edit, reset password, and logout retain their independently allowed states.

- [ ] **Step 2: Run page tests and confirm missing components**

Run: `cd web && npm test -- --run src/features/users/UserEditor.test.tsx src/features/users/UserManagementPage.test.tsx`

Expected: FAIL due to missing components.

- [ ] **Step 3: Implement validated create/edit forms**

`UserEditor` has immutable username in edit mode, trimmed display name, valid email, single Admin/User selector, and enabled control only in edit mode. Prevent duplicate submissions, retain non-secret form values after server errors, and put focus on the first invalid field. Create success immediately transfers the returned password into the one-time dialog and removes it from mutation cache/state after close.

- [ ] **Step 4: Implement the dense ledger and detail action panel**

Use one compact table row per user on desktop: username/name, email, formal role, enabled state, and last sync. On narrow screens use two-line cards without dropping fields. Search/filter parameters are part of the query key. Selecting a row loads detail and session summary. Every destructive state transition has an explicit confirmation naming the target username.

Map stable error codes to localized messages; show request id for support, never raw upstream content. On `identity_sync_pending`, explain that Keycloak changed successfully and refresh/reconciliation is required rather than telling the admin to repeat create blindly.

- [ ] **Step 5: Add complete zh-CN/en-US messages and responsive styles**

Add all labels, states, confirmations, validation errors, access-denied copy, action-reason copy, and provider/sync error copy to `messages.ts`. Style modal focus, status colors with text/icons, 44px action targets, table overflow, and a 720px narrow layout. Do not convey role/status only by color.

- [ ] **Step 6: Register `/users` and run UI tests**

Run: `cd web && npm test -- --run src/features/users src/app/App.test.tsx src/i18n/i18n.test.tsx`

Expected: PASS.

- [ ] **Step 7: Run typecheck and lint**

Run: `cd web && npm run typecheck && npm run lint`

Expected: both exit 0.

- [ ] **Step 8: Commit the administration UI**

```bash
git add web/src/features/users web/src/app/App.tsx web/src/i18n/messages.ts web/src/styles/global.css
git commit -m "feat: add bilingual user management console"
```

### Task 10: Generated contract, documentation, and complete automated verification

**Files:**
- Modify: `web/src/api/schema.d.ts`
- Modify: `web/src/test/contract.test.ts`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/superpowers/specs/2026-07-29-user-management-design.md`
- Modify: `scripts/verify_platform.sh`

- [ ] **Step 1: Regenerate the OpenAPI TypeScript contract**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/python scripts/export_openapi.py
cd web
npm run api:generate
```

Expected: `web/src/api/schema.d.ts` includes the six admin-user paths and assessment admission-summary path, and contains no delete-user operation.

- [ ] **Step 2: Strengthen the contract test**

Assert operation ids and schemas for list/detail/create/update/reset/logout plus admission summary. Assert the temporary-password property occurs only in create/reset response schemas, not `UserView`, `UserPage`, `/me`, errors, or audits.

- [ ] **Step 3: Document operator and user behavior in separate languages**

In both READMEs document:

- Admin/User permission matrix;
- `/users` workflow and one-time password behavior;
- disable versus permanent delete policy;
- Keycloak authority and runtime service account;
- the four runtime environment settings without real values;
- `sync_keycloak_user_management.py --check/--apply` commands;
- rollback rule that retains User role/accounts/audits;
- ordinary admission summary versus admin-only system diagnostics;
- no MCP user-management tools and no TradingAgents changes.

Change the design spec status to implemented only after the complete verification in Step 4 succeeds.

- [ ] **Step 4: Run complete repository verification**

Run backend:

```bash
cd /app/devs/TradingNG/platform
../.venv/bin/ruff check src tests
../.venv/bin/pytest tests/unit tests/operations -q
```

Expected: Ruff exits 0 and all non-environmental tests PASS.

Run integration:

```bash
cd /app/devs/TradingNG/platform
TRADINGNG_TEST_DATABASE_URL="${TRADINGNG_TEST_DATABASE_URL:-}" ../.venv/bin/pytest tests/integration -q
```

Expected: all tests PASS when the configured DB is reachable; otherwise tests using the standard fixture SKIP explicitly.

Run frontend:

```bash
cd /app/devs/TradingNG/web
npm test -- --run
npm run typecheck
npm run lint
npm run build
```

Expected: all tests PASS, typecheck/lint exit 0, and Vite produces `dist/`.

Run deployment checks:

```bash
cd /app/devs/TradingNG
bash scripts/verify_platform.sh
git diff --check
rg -n 'TB[D]|TO[D]O|implement la[t]er|fill in detai[l]s' platform/src web/src scripts deploy README.md README.zh-CN.md
```

Expected: config verification passes, `git diff --check` is silent, and the placeholder scan returns no newly introduced placeholder.

- [ ] **Step 5: Commit contracts and documentation**

```bash
git add web/src/api/schema.d.ts web/src/test/contract.test.ts README.md README.zh-CN.md docs/superpowers/specs/2026-07-29-user-management-design.md scripts/verify_platform.sh
git commit -m "docs: document secure user administration"
```

### Task 11: Live migration, safe rollout, and production smoke test

**Files:**
- Modify private deployment state only: `.env.platform` (gitignored; secret value must never be printed)
- No tracked source changes unless a smoke test exposes a reproducible defect, in which case return to the relevant TDD task and commit the fix separately.

- [ ] **Step 1: Check repository and active assessment state before deployment**

Run:

```bash
cd /app/devs/TradingNG
git status --short --branch
systemctl --user is-active tradingng-platform-api tradingng-platform-scheduler 'tradingng-platform-worker@1'
.venv/bin/python - <<'PY'
import asyncio
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from sqlalchemy import func, select
from tradingng_platform.models import AssessmentRun

async def main():
    db = Database(Settings())
    async with db.sessions() as session:
        rows = (await session.execute(
            select(AssessmentRun.status, func.count()).
            where(AssessmentRun.status.in_(("queued", "running", "cancelling"))).
            group_by(AssessmentRun.status)
        )).all()
        print({status: count for status, count in rows})
    await db.close()

asyncio.run(main())
PY
```

Expected: services report active and the count is recorded. A nonzero count does not block Keycloak reconciliation or static web build, but API restart waits for no `running`/`cancelling` work or uses the already verified worker-lifecycle coordination without stopping workers.

- [ ] **Step 2: Ensure a private management client secret without exposing it**

Run the synchronizer's secret bootstrap mode:

```bash
cd /app/devs/TradingNG
.venv/bin/python scripts/sync_keycloak_user_management.py --env-file .env.platform --ensure-private-secret
```

Expected: it reports only `management client secret: configured`, preserves `.env.platform` permissions, writes/updates exactly `TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET`, and prints no secret value. Verify with a boolean-only command supplied by the script: `--check-private-secret` exits 0.

- [ ] **Step 3: Reconcile the live realm twice**

Run:

```bash
cd /app/devs/TradingNG
.venv/bin/python scripts/sync_keycloak_user_management.py --env-file .env.platform --check
.venv/bin/python scripts/sync_keycloak_user_management.py --env-file .env.platform --apply
.venv/bin/python scripts/sync_keycloak_user_management.py --env-file .env.platform --check
```

Expected: first check may report stable drift action names, apply reports counts only, second check exits 0 with `identity management realm: converged`; at least one enabled Admin remains.

- [ ] **Step 4: Restart only the API when operationally safe and deploy the built web assets**

Use the repository's existing deployment/service commands; do not stop scheduler/workers. Restart `tradingng-platform-api.service` only after the activity check satisfies the lifecycle rule, then verify:

```bash
systemctl --user restart tradingng-platform-api.service
systemctl --user is-active tradingng-platform-api.service
curl --fail --silent http://127.0.0.1:8010/health/ready
```

Expected: API is active and readiness returns `status: ok`. The configured Caddy/OAuth2 Proxy path continues to serve the web build at `https://ushome.amycat.com`.

- [ ] **Step 5: Execute role, session, and audit smoke acceptance**

With an authenticated test Admin session/API harness:

1. list users and record the enabled Admin count;
2. create a uniquely named User and capture the temporary password only in process memory;
3. complete first-login password change in Keycloak;
4. prove the User can call `/me`, admission summary, read assessments, and submit an assessment;
5. prove `/system/status`, `/system/capacity`, and all `/admin/users` calls return 403;
6. disable the User and prove the existing browser bearer and its API credential are immediately rejected;
7. re-enable, reset password, and prove the old session cannot refresh;
8. force logout and prove the current session is gone;
9. leave the smoke user disabled, not deleted;
10. query `audit_events` and prove create/profile/role/status/password/logout records contain no password, client secret, or token-shaped values.

Expected: every boundary passes, the smoke account remains disabled for traceability, and no live assessment is interrupted.

- [ ] **Step 6: Final security and repository check**

Run:

```bash
cd /app/devs/TradingNG
git status --short --branch
git log --oneline --decorate -12
git diff --check
git ls-files .env .env.platform var reports
```

Expected: only intended commits/changes exist, diff check is silent, and the final command prints no private environment, runtime, or report file.

- [ ] **Step 7: Commit any smoke-only documentation result, without committing secrets**

If Step 5 required no source fix, do not create an empty commit. If documentation records the successful acceptance, stage only that tracked document and use:

```bash
git commit -m "docs: record user management acceptance"
```

Never stage `.env.platform`, Keycloak exports containing credentials, browser tokens, audit dumps, or temporary-password output.
