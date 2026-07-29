import asyncio
import time
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

import httpx

from tradingng_platform.auth.oidc import FORMAL_ROLES
from tradingng_platform.identity.contracts import (
    KeycloakPage,
    KeycloakSession,
    KeycloakUser,
)
from tradingng_platform.identity.errors import identity_error


class KeycloakAdminClient:
    def __init__(
        self,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self._client_secret = client_secret
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def _admin_root(self) -> str:
        return f"/admin/realms/{self.realm}"

    async def _access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token is not None and time.monotonic() < self._token_expires_at:
                return self._token
            try:
                response = await self._client.post(
                    f"/realms/{self.realm}/protocol/openid-connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self._client_secret,
                    },
                )
            except httpx.HTTPError:
                raise identity_error("identity_provider_unavailable") from None
            if response.status_code in {401, 403}:
                raise identity_error("identity_provider_forbidden")
            if response.status_code >= 400:
                raise identity_error("identity_provider_unavailable")
            document = response.json()
            token = document.get("access_token") if isinstance(document, dict) else None
            expires_in = document.get("expires_in", 60) if isinstance(document, dict) else 60
            if not isinstance(token, str) or not token:
                raise identity_error("identity_provider_unavailable")
            self._token = token
            self._token_expires_at = time.monotonic() + max(0.0, float(expires_in) - 30.0)
            return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        **kwargs,
    ) -> httpx.Response:
        token = await self._access_token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise identity_error("identity_provider_unavailable") from None
        if response.status_code < 400:
            return response
        if response.status_code == 404:
            raise identity_error("user_not_found")
        if response.status_code == 409:
            code = "email_conflict" if operation == "create_email" else "username_conflict"
            raise identity_error(code)
        if response.status_code in {401, 403}:
            raise identity_error("identity_provider_forbidden")
        raise identity_error("identity_provider_unavailable")

    async def list_users(
        self,
        *,
        search: str | None,
        first: int,
        maximum: int,
    ) -> KeycloakPage:
        params = {"first": first, "max": maximum}
        if search:
            params["search"] = search
        users_response, count_response = await asyncio.gather(
            self._request(
                "GET",
                f"{self._admin_root}/users",
                operation="list_users",
                params=params,
            ),
            self._request(
                "GET",
                f"{self._admin_root}/users/count",
                operation="list_users",
                params={"search": search} if search else None,
            ),
        )
        raw_users = users_response.json()
        items = tuple(
            await asyncio.gather(*(self._user_from_representation(item) for item in raw_users))
        )
        count_document = count_response.json()
        total = int(count_document if isinstance(count_document, int) else len(items))
        return KeycloakPage(items=items, total=total)

    async def get_user(self, subject: str) -> KeycloakUser:
        response = await self._request(
            "GET",
            f"{self._admin_root}/users/{subject}",
            operation="get_user",
        )
        return await self._user_from_representation(response.json())

    async def _user_from_representation(self, representation: dict) -> KeycloakUser:
        subject = str(representation.get("id", ""))
        roles_response = await self._request(
            "GET",
            f"{self._admin_root}/users/{subject}/role-mappings/realm",
            operation="get_user",
        )
        formal_roles = {
            str(role.get("name"))
            for role in roles_response.json()
            if isinstance(role, dict) and role.get("name") in FORMAL_ROLES
        }
        if len(formal_roles) != 1:
            raise identity_error("identity_role_invalid")
        first_name = str(representation.get("firstName", "")).strip()
        last_name = str(representation.get("lastName", "")).strip()
        display_name = " ".join(part for part in (first_name, last_name) if part)
        username = str(representation.get("username", ""))
        return KeycloakUser(
            subject=subject,
            username=username,
            display_name=display_name or username,
            email=representation.get("email"),
            enabled=bool(representation.get("enabled", False)),
            role=next(iter(formal_roles)),
        )

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        email: str,
        enabled: bool,
    ) -> str:
        response = await self._request(
            "POST",
            f"{self._admin_root}/users",
            operation="create_username",
            json={
                "username": username,
                "firstName": display_name,
                "lastName": "",
                "email": email,
                "emailVerified": True,
                "enabled": enabled,
            },
        )
        location = response.headers.get("Location", "")
        subject = urlparse(location).path.rstrip("/").rsplit("/", 1)[-1]
        if not subject or subject == "users":
            raise identity_error("identity_provider_unavailable")
        return subject

    async def update_user(
        self,
        subject: str,
        *,
        display_name: str,
        email: str,
        enabled: bool,
    ) -> None:
        await self._request(
            "PUT",
            f"{self._admin_root}/users/{subject}",
            operation="update_user",
            json={
                "firstName": display_name,
                "lastName": "",
                "email": email,
                "emailVerified": True,
                "enabled": enabled,
            },
        )

    async def replace_role(
        self,
        subject: str,
        role: Literal["Admin", "User"],
    ) -> None:
        mappings = await self._request(
            "GET",
            f"{self._admin_root}/users/{subject}/role-mappings/realm",
            operation="update_role",
        )
        old_roles = [
            item
            for item in mappings.json()
            if isinstance(item, dict) and item.get("name") in FORMAL_ROLES
        ]
        mapping_path = f"{self._admin_root}/users/{subject}/role-mappings/realm"
        if old_roles:
            await self._request(
                "DELETE",
                mapping_path,
                operation="update_role",
                json=old_roles,
            )
        role_response = await self._request(
            "GET",
            f"{self._admin_root}/roles/{role}",
            operation="update_role",
        )
        role_representation = role_response.json()
        await self._request(
            "POST",
            mapping_path,
            operation="update_role",
            json=[
                {
                    "id": role_representation["id"],
                    "name": role_representation["name"],
                }
            ],
        )

    async def set_temporary_password(self, subject: str, password: str) -> None:
        await self._request(
            "PUT",
            f"{self._admin_root}/users/{subject}/reset-password",
            operation="reset_password",
            json={"type": "password", "value": password, "temporary": True},
        )

    async def logout(self, subject: str) -> None:
        await self._request(
            "POST",
            f"{self._admin_root}/users/{subject}/logout",
            operation="logout",
        )

    async def sessions(self, subject: str) -> tuple[KeycloakSession, ...]:
        response = await self._request(
            "GET",
            f"{self._admin_root}/users/{subject}/sessions",
            operation="sessions",
        )
        return tuple(
            KeycloakSession(
                session_id=str(item["id"]),
                started_at=datetime.fromtimestamp(int(item["start"]) / 1000, timezone.utc),
                last_access_at=datetime.fromtimestamp(
                    int(item["lastAccess"]) / 1000,
                    timezone.utc,
                ),
            )
            for item in response.json()
        )

    async def close(self) -> None:
        self._token = None
        self._client_secret = ""
        if self._owned_client:
            await self._client.aclose()
