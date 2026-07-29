import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from tradingng_platform.identity.errors import IdentityError
from tradingng_platform.identity.keycloak import KeycloakAdminClient


def _request_json(request: httpx.Request):
    return json.loads(request.content.decode()) if request.content else None


async def test_token_is_form_encoded_cached_and_shared_by_concurrent_requests():
    token_calls = 0
    user_calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, user_calls
        if request.url.path.endswith("/protocol/openid-connect/token"):
            token_calls += 1
            form = parse_qs(request.content.decode())
            assert form == {
                "grant_type": ["client_credentials"],
                "client_id": ["tradingng-user-admin"],
                "client_secret": ["client-secret"],
            }
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"access_token": "admin-token", "expires_in": 300})
        user_calls += 1
        assert request.headers["Authorization"] == "Bearer admin-token"
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="http://keycloak.test",
    ) as http:
        client = KeycloakAdminClient(
            "http://keycloak.test",
            "tradingng",
            "tradingng-user-admin",
            "client-secret",
            client=http,
        )
        await asyncio.gather(
            client.list_users(search=None, first=0, maximum=20),
            client.list_users(search="alice", first=0, maximum=20),
        )

    assert token_calls == 1
    assert user_calls == 4


async def test_get_user_maps_profile_and_exactly_one_formal_role():
    async def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        if path.endswith("/users/user-1"):
            return httpx.Response(
                200,
                json={
                    "id": "user-1",
                    "username": "alice",
                    "firstName": "Alice",
                    "lastName": "Ng",
                    "email": "alice@example.com",
                    "enabled": True,
                },
            )
        if path.endswith("/users/user-1/role-mappings/realm"):
            return httpx.Response(200, json=[{"id": "role-user", "name": "User"}])
        raise AssertionError(path)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://keycloak.test"
    ) as http:
        user = await KeycloakAdminClient(
            "http://keycloak.test", "tradingng", "client", "secret", client=http
        ).get_user("user-1")

    assert user.subject == "user-1"
    assert user.display_name == "Alice Ng"
    assert user.role == "User"


@pytest.mark.parametrize(
    "roles",
    [[], [{"id": "a", "name": "Admin"}, {"id": "u", "name": "User"}]],
)
async def test_get_user_rejects_missing_or_ambiguous_formal_role(roles):
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        if request.url.path.endswith("/role-mappings/realm"):
            return httpx.Response(200, json=roles)
        return httpx.Response(
            200,
            json={"id": "user-1", "username": "alice", "enabled": True},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://keycloak.test"
    ) as http:
        client = KeycloakAdminClient(
            "http://keycloak.test", "tradingng", "client", "secret", client=http
        )
        with pytest.raises(IdentityError) as captured:
            await client.get_user("user-1")

    assert captured.value.code == "identity_role_invalid"


async def test_create_update_role_password_logout_and_sessions_use_expected_protocol():
    seen = []

    async def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        seen.append((request.method, path, _request_json(request)))
        if request.method == "POST" and path.endswith("/users"):
            return httpx.Response(201, headers={"Location": f"{path}/user-1"})
        if path.endswith("/role-mappings/realm") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": "old-admin", "name": "Admin"},
                    {"id": "offline", "name": "offline_access"},
                ],
            )
        if path.endswith("/roles/User"):
            return httpx.Response(200, json={"id": "role-user", "name": "User"})
        if path.endswith("/sessions"):
            return httpx.Response(
                200,
                json=[{"id": "s1", "start": 1000, "lastAccess": 2000, "ipAddress": "private"}],
            )
        return httpx.Response(204)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://keycloak.test"
    ) as http:
        client = KeycloakAdminClient(
            "http://keycloak.test", "tradingng", "client", "secret", client=http
        )
        subject = await client.create_user(
            username="alice",
            display_name="Alice Ng",
            email="alice@example.com",
            enabled=False,
        )
        await client.update_user(
            subject,
            display_name="Alice Smith",
            email="alice@new.example",
            enabled=True,
        )
        await client.replace_role(subject, "User")
        await client.set_temporary_password(subject, "temporary-password")
        await client.logout(subject)
        sessions = await client.sessions(subject)

    assert subject == "user-1"
    assert sessions[0].session_id == "s1"
    assert not hasattr(sessions[0], "ip_address")
    assert (
        "PUT",
        "/admin/realms/tradingng/users/user-1/reset-password",
        {"type": "password", "value": "temporary-password", "temporary": True},
    ) in seen
    assert ("POST", "/admin/realms/tradingng/users/user-1/logout", None) in seen
    role_deletes = [item for item in seen if item[0] == "DELETE"]
    assert role_deletes[0][2] == [{"id": "old-admin", "name": "Admin"}]
    role_posts = [
        item for item in seen if item[0] == "POST" and item[1].endswith("role-mappings/realm")
    ]
    assert role_posts[0][2] == [{"id": "role-user", "name": "User"}]


@pytest.mark.parametrize(
    ("upstream", "operation", "expected_code", "expected_status"),
    [
        (409, "create_username", "username_conflict", 409),
        (409, "create_email", "email_conflict", 409),
        (404, "get_user", "user_not_found", 404),
        (403, "get_user", "identity_provider_forbidden", 503),
        (429, "get_user", "identity_provider_unavailable", 503),
        (500, "get_user", "identity_provider_unavailable", 503),
    ],
)
async def test_upstream_errors_map_to_stable_redacted_errors(
    upstream, operation, expected_code, expected_status
):
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        return httpx.Response(upstream, text="upstream-sensitive-body")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://keycloak.test"
    ) as http:
        client = KeycloakAdminClient(
            "http://keycloak.test", "tradingng", "client", "secret", client=http
        )
        with pytest.raises(IdentityError) as captured:
            await client._request("GET", "/probe", operation=operation)

    assert captured.value.code == expected_code
    assert captured.value.status_code == expected_status
    assert "upstream-sensitive-body" not in str(captured.value)


async def test_network_timeout_maps_to_unavailable():
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        raise httpx.ReadTimeout("private network details", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://keycloak.test"
    ) as http:
        client = KeycloakAdminClient(
            "http://keycloak.test", "tradingng", "client", "secret", client=http
        )
        with pytest.raises(IdentityError) as captured:
            await client._request("GET", "/probe", operation="get_user")

    assert captured.value.code == "identity_provider_unavailable"
    assert "private network details" not in str(captured.value)
