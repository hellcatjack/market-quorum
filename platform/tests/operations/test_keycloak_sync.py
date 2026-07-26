import json

import httpx
from sync_keycloak_public_urls import (
    BASIC_SCOPE_PAYLOAD,
    PUBLIC_BASE_URL,
    PUBLIC_MCP_RESOURCE,
    PUBLIC_WEB_REDIRECT,
    SUBJECT_MAPPER_PAYLOAD,
    PublicUrlSynchronizer,
)


def test_public_url_synchronizer_checks_and_applies_exact_admin_payloads():
    state = {
        "realm": {
            "realm": "tradingng",
            "enabled": True,
            "attributes": {"existing": "kept", "frontendUrl": "https://old.example"},
        },
        "clients": {
            "tradingng-web": {
                "id": "web-id",
                "clientId": "tradingng-web",
                "enabled": True,
                "redirectUris": ["https://old.example/oauth2/callback"],
                "webOrigins": ["https://old.example"],
            },
            "tradingng-api": {
                "id": "api-id",
                "clientId": "tradingng-api",
                "enabled": True,
            },
            "tradingng-mcp": {
                "id": "mcp-id",
                "clientId": "tradingng-mcp",
                "enabled": True,
            },
        },
        "scope": {"id": "scope-id", "name": "tradingng-mcp-resource"},
        "mapper": {
            "id": "mapper-id",
            "name": "tradingng-mcp-audience",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {
                "included.client.audience": "https://old.example/mcp",
                "access.token.claim": "true",
            },
        },
        "admin_user": {
            "id": "admin-user-id",
            "username": "platform-admin",
            "email": "hellcatjack@gmail.com",
            "enabled": True,
            "emailVerified": False,
            "requiredActions": [],
        },
    }
    puts = []
    posts = []
    scope_links = []
    gets = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET":
            gets.append((path, dict(request.url.params)))
            if path == "/admin/realms/tradingng":
                return httpx.Response(200, json=state["realm"])
            if path == "/admin/realms/tradingng/clients":
                return httpx.Response(200, json=[state["clients"][request.url.params["clientId"]]])
            if path == "/admin/realms/tradingng/client-scopes":
                scopes = [state["scope"]]
                if state.get("basic_scope"):
                    scopes.append(state["basic_scope"])
                return httpx.Response(200, json=scopes)
            if path.endswith("/protocol-mappers/models"):
                if "/basic-scope-id/" in path:
                    mapper = state.get("subject_mapper")
                    return httpx.Response(200, json=[mapper] if mapper else [])
                return httpx.Response(200, json=[state["mapper"]])
            if path == "/admin/realms/tradingng/users":
                return httpx.Response(200, json=[state["admin_user"]])
        if request.method == "PUT":
            if "/default-client-scopes/basic-scope-id" in path:
                scope_links.append(path)
                client_id = path.split("/")[5]
                client = next(item for item in state["clients"].values() if item["id"] == client_id)
                client.setdefault("defaultClientScopes", []).append("basic")
                return httpx.Response(204)
            payload = json.loads(request.content)
            puts.append((path, payload))
            if path == "/admin/realms/tradingng":
                state["realm"] = payload
            elif path == "/admin/realms/tradingng/clients/web-id":
                state["clients"]["tradingng-web"] = payload
            elif path.endswith("/protocol-mappers/models/mapper-id"):
                state["mapper"] = payload
            elif path == "/admin/realms/tradingng/users/admin-user-id":
                state["admin_user"] = payload
            return httpx.Response(204)
        if request.method == "POST":
            payload = json.loads(request.content) if request.content else None
            posts.append((path, payload))
            if path == "/admin/realms/tradingng/client-scopes":
                state["basic_scope"] = {"id": "basic-scope-id", **payload}
                return httpx.Response(201)
            if path.endswith("/basic-scope-id/protocol-mappers/models"):
                state["subject_mapper"] = {"id": "subject-mapper-id", **payload}
                return httpx.Response(201)
        return httpx.Response(404)

    with httpx.Client(
        base_url="http://127.0.0.1:18081",
        transport=httpx.MockTransport(handler),
    ) as client:
        synchronizer = PublicUrlSynchronizer(
            client,
            token="test-token",
            initial_admin_username="platform-admin",
        )
        assert synchronizer.check() == {
            "realm.frontendUrl",
            "tradingng-web.redirectUris",
            "tradingng-web.webOrigins",
            "tradingng-mcp-resource.audience",
            "platform-admin.emailVerified",
            "basic.client-scope",
            "tradingng-web.basic-scope",
            "tradingng-api.basic-scope",
            "tradingng-mcp.basic-scope",
        }
        synchronizer.apply()
        assert synchronizer.check() == set()

    queried_clients = {
        params.get("clientId") for path, params in gets if path == "/admin/realms/tradingng/clients"
    }
    assert queried_clients == {"tradingng-web", "tradingng-api", "tradingng-mcp"}
    queried_users = [params for path, params in gets if path == "/admin/realms/tradingng/users"]
    assert queried_users
    assert all(
        params == {"username": "platform-admin", "exact": "true"} for params in queried_users
    )
    assert puts == [
        (
            "/admin/realms/tradingng",
            {
                "realm": "tradingng",
                "enabled": True,
                "attributes": {"existing": "kept", "frontendUrl": PUBLIC_BASE_URL},
            },
        ),
        (
            "/admin/realms/tradingng/clients/web-id",
            {
                "id": "web-id",
                "clientId": "tradingng-web",
                "enabled": True,
                "redirectUris": [PUBLIC_WEB_REDIRECT],
                "webOrigins": [PUBLIC_BASE_URL],
                "defaultClientScopes": ["basic"],
            },
        ),
        (
            "/admin/realms/tradingng/client-scopes/scope-id/protocol-mappers/models/mapper-id",
            {
                "id": "mapper-id",
                "name": "tradingng-mcp-audience",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "config": {
                    "included.custom.audience": PUBLIC_MCP_RESOURCE,
                    "access.token.claim": "true",
                },
            },
        ),
        (
            "/admin/realms/tradingng/users/admin-user-id",
            {
                "id": "admin-user-id",
                "username": "platform-admin",
                "email": "hellcatjack@gmail.com",
                "enabled": True,
                "emailVerified": True,
                "requiredActions": [],
            },
        ),
    ]
    assert posts == [
        ("/admin/realms/tradingng/client-scopes", BASIC_SCOPE_PAYLOAD),
        (
            "/admin/realms/tradingng/client-scopes/basic-scope-id/protocol-mappers/models",
            SUBJECT_MAPPER_PAYLOAD,
        ),
    ]
    assert scope_links == [
        ("/admin/realms/tradingng/clients/web-id/default-client-scopes/basic-scope-id"),
        ("/admin/realms/tradingng/clients/api-id/default-client-scopes/basic-scope-id"),
        ("/admin/realms/tradingng/clients/mcp-id/default-client-scopes/basic-scope-id"),
    ]
