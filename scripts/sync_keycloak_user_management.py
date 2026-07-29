#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import tempfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

REALM = "tradingng"
MANAGEMENT_CLIENT = "tradingng-user-admin"
WEB_CLIENT = "tradingng-web"
FORMAL_ROLES = frozenset({"Admin", "User"})
LEGACY_ROLES = frozenset({"Analyst", "Viewer"})
REQUIRED_SCOPES = ("assessments:review", "users:manage")
REQUIRED_SERVICE_ROLES = frozenset({"query-users", "view-users", "manage-users"})


class KeycloakUserManagementError(RuntimeError):
    """A safe identity-management reconciliation could not be completed."""


def plan(snapshot: dict) -> tuple[str, ...]:
    users = snapshot.get("users", [])
    if not any(
        user.get("enabled") is True and "Admin" in set(user.get("roles", ()))
        for user in users
    ):
        raise KeycloakUserManagementError("at least one enabled Admin is required")

    actions: list[str] = []
    roles = set(snapshot.get("roles", ()))
    scopes = set(snapshot.get("scopes", ()))
    clients = set(snapshot.get("clients", ()))
    web_scopes = set(snapshot.get("web_scopes", ()))
    service_roles = set(snapshot.get("service_roles", ()))
    if "User" not in roles:
        actions.append("create_role:User")
    for scope in REQUIRED_SCOPES:
        if scope not in scopes:
            actions.append(f"create_scope:{scope}")
    if MANAGEMENT_CLIENT not in clients:
        actions.append(f"create_client:{MANAGEMENT_CLIENT}")
    if not service_roles >= REQUIRED_SERVICE_ROLES:
        actions.append(f"grant_service_roles:{MANAGEMENT_CLIENT}")
    for scope in REQUIRED_SCOPES:
        if scope not in web_scopes:
            actions.append(f"attach_web_scope:{scope}")

    for user in users:
        user_roles = set(user.get("roles", ()))
        if not user_roles.intersection(LEGACY_ROLES):
            continue
        target_role = "Admin" if "Admin" in user_roles else "User"
        actions.append(f"migrate_user:{user['id']}:{target_role}")
        actions.append(f"logout_user:{user['id']}")
    for role in ("Analyst", "Viewer"):
        if role in roles:
            actions.append(f"remove_role:{role}")
    return tuple(actions)


def render_report(actions: tuple[str, ...], *, secret: str | None = None) -> str:
    del secret
    lines = [f"identity_actions={len(actions)}"]
    lines.extend(f"identity_action={action}" for action in actions)
    return "\n".join(lines)


def parse_action(action: str) -> tuple[str, str, str | None]:
    kind, separator, value = action.partition(":")
    if not separator or not kind or not value:
        raise KeycloakUserManagementError("invalid reconciliation action")
    if kind == "migrate_user":
        subject, role_separator, role = value.rpartition(":")
        if not role_separator or not subject or role not in FORMAL_ROLES:
            raise KeycloakUserManagementError("invalid user migration action")
        return kind, subject, role
    return kind, value, None


def _credential(env_file: Path, name: str) -> str:
    value = os.getenv(name) or dotenv_values(env_file).get(name)
    if not value:
        raise KeycloakUserManagementError(
            f"required deployment setting is missing: {name}"
        )
    return str(value)


def _optional_credential(env_file: Path, name: str) -> str | None:
    value = os.getenv(name) or dotenv_values(env_file).get(name)
    return str(value) if value else None


def ensure_private_secret(env_file: Path) -> None:
    if _optional_credential(env_file, "TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET"):
        print("management client credential: configured")
        return
    if not env_file.exists():
        raise KeycloakUserManagementError(
            "private platform environment file does not exist"
        )
    generated = secrets.token_urlsafe(48)
    original = env_file.read_text(encoding="utf-8")
    lines = [
        line
        for line in original.splitlines()
        if not line.startswith("TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET=")
    ]
    lines.append(f"TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET={generated}")
    mode = env_file.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{env_file.name}.",
        dir=env_file.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, env_file)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    print("management client credential: configured")


def _admin_token(client: httpx.Client, username: str, password: str) -> str:
    response = client.post(
        "/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": username,
            "password": password,
        },
    )
    response.raise_for_status()
    document = response.json()
    token = document.get("access_token") if isinstance(document, dict) else None
    if not isinstance(token, str) or not token:
        raise KeycloakUserManagementError("Keycloak administrator token is incomplete")
    return token


class LiveSynchronizer:
    def __init__(self, client: httpx.Client, token: str, management_secret: str):
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}
        self.management_secret = management_secret

    def _get(self, path: str, *, params: dict | None = None):
        response = self.client.get(path, params=params, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: dict | list | None = None) -> httpx.Response:
        response = self.client.post(path, json=payload, headers=self.headers)
        response.raise_for_status()
        return response

    def _put(self, path: str, payload: dict | None = None) -> None:
        kwargs = {"headers": self.headers}
        if payload is not None:
            kwargs["json"] = payload
        response = self.client.put(path, **kwargs)
        response.raise_for_status()

    def _delete(self, path: str, payload: list | None = None) -> None:
        response = self.client.request(
            "DELETE", path, json=payload, headers=self.headers
        )
        response.raise_for_status()

    def _one_client(self, client_id: str) -> dict:
        clients = self._get(
            f"/admin/realms/{REALM}/clients",
            params={"clientId": client_id},
        )
        if len(clients) != 1:
            raise KeycloakUserManagementError(
                f"expected one Keycloak client: {client_id}"
            )
        return clients[0]

    def _user_roles(self, user_id: str) -> list[dict]:
        return self._get(f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm")

    def snapshot(self) -> dict:
        roles = {item["name"] for item in self._get(f"/admin/realms/{REALM}/roles")}
        scope_rows = self._get(f"/admin/realms/{REALM}/client-scopes")
        scopes = {item["name"] for item in scope_rows}
        client_rows = self._get(f"/admin/realms/{REALM}/clients")
        clients = {item["clientId"] for item in client_rows}
        web_scopes: set[str] = set()
        if WEB_CLIENT in clients:
            web = self._one_client(WEB_CLIENT)
            web_scopes = {
                item["name"]
                for item in self._get(
                    f"/admin/realms/{REALM}/clients/{web['id']}/default-client-scopes"
                )
            }
        service_roles: set[str] = set()
        if MANAGEMENT_CLIENT in clients:
            management = self._one_client(MANAGEMENT_CLIENT)
            service_user = self._get(
                f"/admin/realms/{REALM}/clients/{management['id']}/service-account-user"
            )
            realm_management = self._one_client("realm-management")
            service_roles = {
                item["name"]
                for item in self._get(
                    f"/admin/realms/{REALM}/users/{service_user['id']}"
                    f"/role-mappings/clients/{realm_management['id']}"
                )
            }
        users = []
        first = 0
        while True:
            page = self._get(
                f"/admin/realms/{REALM}/users",
                params={"first": first, "max": 100},
            )
            for user in page:
                user["roles"] = {item["name"] for item in self._user_roles(user["id"])}
                users.append(user)
            if len(page) < 100:
                break
            first += 100
        return {
            "roles": roles,
            "scopes": scopes,
            "clients": clients,
            "web_scopes": web_scopes,
            "service_roles": service_roles,
            "users": users,
        }

    def apply(self, actions: tuple[str, ...]) -> None:
        for action in actions:
            kind, value, secondary = parse_action(action)
            if kind == "create_role":
                self._post(
                    f"/admin/realms/{REALM}/roles",
                    {"name": value},
                )
            elif kind == "create_scope":
                self._post(
                    f"/admin/realms/{REALM}/client-scopes",
                    {"name": value, "protocol": "openid-connect"},
                )
            elif kind == "create_client":
                self._post(
                    f"/admin/realms/{REALM}/clients",
                    {
                        "clientId": MANAGEMENT_CLIENT,
                        "enabled": True,
                        "publicClient": False,
                        "secret": self.management_secret,
                        "standardFlowEnabled": False,
                        "directAccessGrantsEnabled": False,
                        "serviceAccountsEnabled": True,
                        "defaultClientScopes": ["basic"],
                    },
                )
            elif kind == "grant_service_roles":
                self._grant_service_roles()
            elif kind == "attach_web_scope":
                self._attach_web_scope(value)
            elif kind == "migrate_user":
                self._migrate_user(value, str(secondary))
            elif kind == "logout_user":
                self._post(f"/admin/realms/{REALM}/users/{value}/logout")
            elif kind == "remove_role":
                self._delete(f"/admin/realms/{REALM}/roles/{value}")
            else:
                raise KeycloakUserManagementError(
                    f"unsupported reconciliation action: {kind}"
                )

    def _grant_service_roles(self) -> None:
        management = self._one_client(MANAGEMENT_CLIENT)
        service_user = self._get(
            f"/admin/realms/{REALM}/clients/{management['id']}/service-account-user"
        )
        realm_management = self._one_client("realm-management")
        roles = [
            self._get(
                f"/admin/realms/{REALM}/clients/{realm_management['id']}/roles/{role}"
            )
            for role in sorted(REQUIRED_SERVICE_ROLES)
        ]
        self._post(
            f"/admin/realms/{REALM}/users/{service_user['id']}"
            f"/role-mappings/clients/{realm_management['id']}",
            roles,
        )

    def _attach_web_scope(self, scope_name: str) -> None:
        web = self._one_client(WEB_CLIENT)
        scopes = self._get(f"/admin/realms/{REALM}/client-scopes")
        matching = [item for item in scopes if item.get("name") == scope_name]
        if len(matching) != 1:
            raise KeycloakUserManagementError(
                f"expected one client scope: {scope_name}"
            )
        self._put(
            f"/admin/realms/{REALM}/clients/{web['id']}"
            f"/default-client-scopes/{matching[0]['id']}"
        )

    def _migrate_user(self, user_id: str, target_role: str) -> None:
        roles = self._user_roles(user_id)
        by_name = {item["name"]: item for item in roles}
        if target_role not in by_name:
            role = self._get(f"/admin/realms/{REALM}/roles/{target_role}")
            self._post(
                f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
                [role],
            )
        legacy = [item for item in roles if item.get("name") in LEGACY_ROLES]
        if legacy:
            self._delete(
                f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
                legacy,
            )


async def _sync_platform(snapshot: dict) -> None:
    from tradingng_platform.auth.principal import Principal
    from tradingng_platform.config import Settings
    from tradingng_platform.db import Database
    from tradingng_platform.identity.contracts import KeycloakUser
    from tradingng_platform.identity.repository import IdentityRepository

    settings = Settings()
    database = Database(settings)
    repository = IdentityRepository(database.sessions)
    actor = Principal(
        issuer=str(settings.oidc_issuer).rstrip("/"),
        subject="keycloak-user-management-sync",
        actor_type="service",
        scopes=frozenset(),
    )
    try:
        for user in snapshot["users"]:
            formal = set(user.get("roles", ())).intersection(FORMAL_ROLES)
            if len(formal) != 1:
                continue
            first_name = str(user.get("firstName", "")).strip()
            last_name = str(user.get("lastName", "")).strip()
            display_name = " ".join(item for item in (first_name, last_name) if item)
            authoritative = KeycloakUser(
                subject=user["id"],
                username=user.get("username", user["id"]),
                display_name=display_name or user.get("username", user["id"]),
                email=user.get("email"),
                enabled=bool(user.get("enabled", False)),
                role=next(iter(formal)),
            )
            async with repository.transaction(guard=True) as transaction:
                sync = await transaction.sync_authoritative(
                    authoritative,
                    str(settings.oidc_issuer).rstrip("/"),
                )
                if sync.changed_fields:
                    await transaction.append_audit(
                        actor,
                        "user.reconcile",
                        sync.identity,
                        "keycloak-user-management-sync",
                        {
                            "changed_fields": list(sync.changed_fields),
                            "old_role": sync.old_role,
                            "new_role": sync.identity.role,
                            "old_status": sync.old_status,
                            "new_status": sync.identity.status,
                            "keycloak_subject": sync.identity.subject,
                            "result": "succeeded",
                        },
                    )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile TradingNG Keycloak user management"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--ensure-private-secret", action="store_true")
    action.add_argument("--check-private-secret", action="store_true")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env.platform",
    )
    arguments = parser.parse_args()
    if arguments.ensure_private_secret:
        ensure_private_secret(arguments.env_file)
        return
    if arguments.check_private_secret:
        _credential(arguments.env_file, "TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET")
        print("management client credential: configured")
        return

    username = _credential(arguments.env_file, "KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME")
    password = _credential(arguments.env_file, "KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD")
    management_secret = _credential(
        arguments.env_file,
        "TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET",
    )
    with httpx.Client(base_url="http://127.0.0.1:18081", timeout=30) as client:
        synchronizer = LiveSynchronizer(
            client,
            _admin_token(client, username, password),
            management_secret,
        )
        snapshot = synchronizer.snapshot()
        actions = plan(snapshot)
        if arguments.check:
            if actions:
                print(render_report(actions))
                raise SystemExit(1)
            print("identity management realm: converged")
            return
        synchronizer.apply(actions)
        converged = synchronizer.snapshot()
        remaining = plan(converged)
        if remaining:
            raise KeycloakUserManagementError(
                "identity management reconciliation did not converge"
            )
        asyncio.run(_sync_platform(converged))
        print(f"identity management realm: synchronized ({len(actions)} actions)")


if __name__ == "__main__":
    main()
