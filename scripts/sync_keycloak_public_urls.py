#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import dotenv_values

PUBLIC_BASE_URL = "https://ushome.amycat.com"
PUBLIC_WEB_REDIRECT = f"{PUBLIC_BASE_URL}/oauth2/callback"
PUBLIC_MCP_RESOURCE = f"{PUBLIC_BASE_URL}/mcp"
REALM = "tradingng"
CLIENT_IDS = ("tradingng-web", "tradingng-api", "tradingng-mcp")
MCP_SCOPE = "tradingng-mcp-resource"
MCP_MAPPER = "tradingng-mcp-audience"
BASIC_SCOPE = "basic"
BASIC_SCOPE_PAYLOAD = {
    "name": BASIC_SCOPE,
    "protocol": "openid-connect",
    "attributes": {
        "include.in.token.scope": "false",
        "display.on.consent.screen": "false",
    },
}
SUBJECT_MAPPER_PAYLOAD = {
    "name": "subject (sub)",
    "protocol": "openid-connect",
    "protocolMapper": "oidc-sub-mapper",
    "config": {
        "access.token.claim": "true",
        "lightweight.claim": "true",
        "introspection.token.claim": "true",
    },
}


class KeycloakSyncError(RuntimeError):
    """Raised when the live realm cannot be checked or synchronized safely."""


@dataclass(frozen=True)
class RealmSnapshot:
    realm: dict
    clients: dict[str, dict]
    mcp_scope: dict
    mcp_mapper: dict
    basic_scope: dict | None
    subject_mapper: dict | None
    initial_admin: dict


def _one(items: list[dict], description: str) -> dict:
    if len(items) != 1:
        raise KeycloakSyncError(f"expected exactly one {description}")
    return items[0]


def _optional_one(items: list[dict], description: str) -> dict | None:
    if len(items) > 1:
        raise KeycloakSyncError(f"expected at most one {description}")
    return items[0] if items else None


class PublicUrlSynchronizer:
    def __init__(
        self,
        client: httpx.Client,
        token: str,
        initial_admin_username: str,
    ):
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}
        self.initial_admin_username = initial_admin_username

    def _get(self, path: str, *, params: dict | None = None):
        response = self.client.get(path, params=params, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def _put(self, path: str, payload: dict) -> None:
        response = self.client.put(path, json=payload, headers=self.headers)
        response.raise_for_status()

    def _put_without_body(self, path: str) -> None:
        response = self.client.put(path, headers=self.headers)
        response.raise_for_status()

    def _post(self, path: str, payload: dict | None = None) -> None:
        kwargs = {"headers": self.headers}
        if payload is not None:
            kwargs["json"] = payload
        response = self.client.post(path, **kwargs)
        response.raise_for_status()

    def snapshot(self) -> RealmSnapshot:
        realm = self._get(f"/admin/realms/{REALM}")
        clients = {}
        for client_id in CLIENT_IDS:
            clients[client_id] = _one(
                self._get(
                    f"/admin/realms/{REALM}/clients",
                    params={"clientId": client_id},
                ),
                f"client {client_id}",
            )
        scopes = self._get(f"/admin/realms/{REALM}/client-scopes")
        mcp_scope = _one(
            [scope for scope in scopes if scope.get("name") == MCP_SCOPE],
            f"client scope {MCP_SCOPE}",
        )
        mappers = self._get(
            f"/admin/realms/{REALM}/client-scopes/{mcp_scope['id']}"
            "/protocol-mappers/models"
        )
        mcp_mapper = _one(
            [mapper for mapper in mappers if mapper.get("name") == MCP_MAPPER],
            f"protocol mapper {MCP_MAPPER}",
        )
        basic_scope = _optional_one(
            [scope for scope in scopes if scope.get("name") == BASIC_SCOPE],
            f"client scope {BASIC_SCOPE}",
        )
        subject_mapper = None
        if basic_scope is not None:
            basic_mappers = self._get(
                f"/admin/realms/{REALM}/client-scopes/{basic_scope['id']}"
                "/protocol-mappers/models"
            )
            subject_mapper = _optional_one(
                [
                    mapper
                    for mapper in basic_mappers
                    if mapper.get("protocolMapper") == "oidc-sub-mapper"
                ],
                "subject protocol mapper",
            )
        initial_admin = _one(
            self._get(
                f"/admin/realms/{REALM}/users",
                params={
                    "username": self.initial_admin_username,
                    "exact": True,
                },
            ),
            f"initial admin user {self.initial_admin_username}",
        )
        return RealmSnapshot(
            realm,
            clients,
            mcp_scope,
            mcp_mapper,
            basic_scope,
            subject_mapper,
            initial_admin,
        )

    @staticmethod
    def drift(snapshot: RealmSnapshot) -> set[str]:
        drift = set()
        if snapshot.realm.get("attributes", {}).get("frontendUrl") != PUBLIC_BASE_URL:
            drift.add("realm.frontendUrl")
        web = snapshot.clients["tradingng-web"]
        if web.get("redirectUris") != [PUBLIC_WEB_REDIRECT]:
            drift.add("tradingng-web.redirectUris")
        if web.get("webOrigins") != [PUBLIC_BASE_URL]:
            drift.add("tradingng-web.webOrigins")
        mapper_config = snapshot.mcp_mapper.get("config", {})
        audience = mapper_config.get("included.custom.audience")
        if (
            audience != PUBLIC_MCP_RESOURCE
            or "included.client.audience" in mapper_config
        ):
            drift.add("tradingng-mcp-resource.audience")
        if snapshot.initial_admin.get("emailVerified") is not True:
            drift.add(f"{snapshot.initial_admin['username']}.emailVerified")
        if snapshot.basic_scope is None:
            drift.add("basic.client-scope")
        else:
            mapper = snapshot.subject_mapper
            required_config = SUBJECT_MAPPER_PAYLOAD["config"]
            if (
                mapper is None
                or mapper.get("protocol") != "openid-connect"
                or any(
                    mapper.get("config", {}).get(key) != value
                    for key, value in required_config.items()
                )
            ):
                drift.add("basic.subject-mapper")
        for client_id, client in snapshot.clients.items():
            if BASIC_SCOPE not in client.get("defaultClientScopes", []):
                drift.add(f"{client_id}.basic-scope")
        return drift

    def check(self) -> set[str]:
        return self.drift(self.snapshot())

    def apply(self) -> None:
        snapshot = self.snapshot()
        drift = self.drift(snapshot)
        if "realm.frontendUrl" in drift:
            realm = deepcopy(snapshot.realm)
            realm.setdefault("attributes", {})["frontendUrl"] = PUBLIC_BASE_URL
            self._put(f"/admin/realms/{REALM}", realm)
        if {
            "tradingng-web.redirectUris",
            "tradingng-web.webOrigins",
        } & drift:
            web = deepcopy(snapshot.clients["tradingng-web"])
            web["redirectUris"] = [PUBLIC_WEB_REDIRECT]
            web["webOrigins"] = [PUBLIC_BASE_URL]
            self._put(f"/admin/realms/{REALM}/clients/{web['id']}", web)
        if "tradingng-mcp-resource.audience" in drift:
            mapper = deepcopy(snapshot.mcp_mapper)
            mapper_config = mapper.setdefault("config", {})
            mapper_config.pop("included.client.audience", None)
            mapper_config["included.custom.audience"] = PUBLIC_MCP_RESOURCE
            self._put(
                f"/admin/realms/{REALM}/client-scopes/{snapshot.mcp_scope['id']}"
                f"/protocol-mappers/models/{mapper['id']}",
                mapper,
            )
        admin_drift = f"{snapshot.initial_admin['username']}.emailVerified"
        if admin_drift in drift:
            initial_admin = deepcopy(snapshot.initial_admin)
            initial_admin["emailVerified"] = True
            self._put(
                f"/admin/realms/{REALM}/users/{initial_admin['id']}",
                initial_admin,
            )
        if "basic.client-scope" in drift:
            self._post(
                f"/admin/realms/{REALM}/client-scopes",
                BASIC_SCOPE_PAYLOAD,
            )

        snapshot = self.snapshot()
        drift = self.drift(snapshot)
        if "basic.subject-mapper" in drift:
            if snapshot.basic_scope is None:
                raise KeycloakSyncError("basic client scope was not created")
            mapper_path = (
                f"/admin/realms/{REALM}/client-scopes/{snapshot.basic_scope['id']}"
                "/protocol-mappers/models"
            )
            if snapshot.subject_mapper is None:
                self._post(mapper_path, SUBJECT_MAPPER_PAYLOAD)
            else:
                mapper = deepcopy(snapshot.subject_mapper)
                mapper.update(
                    {
                        key: value
                        for key, value in SUBJECT_MAPPER_PAYLOAD.items()
                        if key != "config"
                    }
                )
                mapper.setdefault("config", {}).update(SUBJECT_MAPPER_PAYLOAD["config"])
                self._put(f"{mapper_path}/{mapper['id']}", mapper)

        snapshot = self.snapshot()
        drift = self.drift(snapshot)
        if snapshot.basic_scope is None:
            raise KeycloakSyncError("basic client scope is unavailable")
        for client_id in CLIENT_IDS:
            if f"{client_id}.basic-scope" in drift:
                self._put_without_body(
                    f"/admin/realms/{REALM}/clients/{snapshot.clients[client_id]['id']}"
                    f"/default-client-scopes/{snapshot.basic_scope['id']}"
                )
        remaining = self.check()
        if remaining:
            raise KeycloakSyncError(
                f"public URL synchronization still has {len(remaining)} drift items"
            )


def _credential(env_file: Path, name: str) -> str:
    values = dotenv_values(env_file)
    value = os.getenv(name) or values.get(name)
    if not value:
        raise KeycloakSyncError(f"required Keycloak credential is missing: {name}")
    return value


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
    token = response.json().get("access_token")
    if not token:
        raise KeycloakSyncError("Keycloak admin token response is incomplete")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synchronize TradingNG public Keycloak URLs"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env.platform",
    )
    arguments = parser.parse_args()
    username = _credential(arguments.env_file, "KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME")
    password = _credential(arguments.env_file, "KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD")
    initial_admin_username = _credential(
        arguments.env_file, "TRADINGNG_INITIAL_ADMIN_USERNAME"
    )

    with httpx.Client(base_url="http://127.0.0.1:18081", timeout=30) as client:
        token = _admin_token(client, username, password)
        synchronizer = PublicUrlSynchronizer(client, token, initial_admin_username)
        if arguments.check:
            drift = synchronizer.check()
            if drift:
                print(f"keycloak_public_url_drift={len(drift)}")
                raise SystemExit(1)
            print("keycloak_public_urls=ok")
        else:
            synchronizer.apply()
            print("keycloak_public_urls=synchronized")


if __name__ == "__main__":
    main()
