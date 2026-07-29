import json
from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 test environments
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[3]


def test_compose_ports_are_loopback_and_services_are_healthy():
    compose = yaml.safe_load((ROOT / "deploy/compose.prod.yml").read_text())
    for service in compose["services"].values():
        for published in service.get("ports", []):
            assert str(published).startswith("127.0.0.1:")
    assert "healthcheck" in compose["services"]["postgres"]
    assert "healthcheck" in compose["services"]["keycloak"]
    assert compose["services"]["keycloak"]["command"] == ["start", "--import-realm"]
    assert compose["services"]["keycloak"]["ports"] == ["127.0.0.1:18081:8080"]
    assert compose["services"]["keycloak"]["image"] == ("quay.io/keycloak/keycloak:26.3.5")
    rendered = json.dumps(compose)
    assert "change-me" not in rendered
    assert "password123" not in rendered.lower()


def test_keycloak_clients_scopes_and_mcp_audience_match_platform():
    realm = json.loads((ROOT / "deploy/keycloak/tradingng-realm.json").read_text())
    assert realm["realm"] == "tradingng"
    assert realm["attributes"]["frontendUrl"] == "https://ushome.amycat.com"
    assert {item["name"] for item in realm["roles"]["realm"]} == {
        "Admin",
        "User",
    }
    scope_definitions = {item["name"]: item for item in realm["clientScopes"]}
    scopes = set(scope_definitions)
    assert {
        "basic",
        "profile",
        "email",
        "roles",
        "assessments:read",
        "assessments:submit",
        "assessments:cancel",
        "assessments:review",
        "assessments:admin",
        "validations:read",
        "validations:write",
        "system:read",
        "artifacts:read",
        "users:manage",
    } <= scopes
    basic_mappers = scope_definitions["basic"]["protocolMappers"]
    assert basic_mappers == [
        {
            "name": "subject (sub)",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-sub-mapper",
            "config": {
                "access.token.claim": "true",
                "lightweight.claim": "true",
                "introspection.token.claim": "true",
            },
        }
    ]
    standard_scopes = {
        item["name"]: item
        for item in realm["clientScopes"]
        if item["name"] in {"profile", "email", "roles"}
    }
    mapper_types = {
        mapper["protocolMapper"]
        for item in standard_scopes.values()
        for mapper in item["protocolMappers"]
    }
    assert {
        "oidc-usermodel-attribute-mapper",
        "oidc-usermodel-property-mapper",
        "oidc-usermodel-realm-role-mapper",
    } <= mapper_types
    assert "https://ushome.amycat.com/mcp" in json.dumps(realm)
    assert '"included.custom.audience": "https://ushome.amycat.com/mcp"' in json.dumps(realm)
    assert '"included.client.audience": "tradingng-api"' in json.dumps(realm)
    assert {item["clientId"] for item in realm["clients"]} >= {
        "tradingng-web",
        "tradingng-api",
        "tradingng-mcp",
        "tradingng-user-admin",
    }
    for client in realm["clients"]:
        assert "basic" in client["defaultClientScopes"]
    web = next(item for item in realm["clients"] if item["clientId"] == "tradingng-web")
    assert {
        "roles",
        "assessments:read",
        "assessments:submit",
        "assessments:cancel",
        "assessments:review",
        "assessments:admin",
        "validations:read",
        "validations:write",
        "system:read",
        "artifacts:read",
        "users:manage",
        "tradingng-api-resource",
    } <= set(web["defaultClientScopes"])
    management = next(
        item for item in realm["clients"] if item["clientId"] == "tradingng-user-admin"
    )
    assert management["publicClient"] is False
    assert management["serviceAccountsEnabled"] is True
    assert management["standardFlowEnabled"] is False
    assert management["directAccessGrantsEnabled"] is False
    assert management["secret"] == "${TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET}"
    assert "users:manage" not in management["defaultClientScopes"]


def test_keycloak_bootstraps_one_environment_secured_platform_admin():
    realm = json.loads((ROOT / "deploy/keycloak/tradingng-realm.json").read_text())
    assert realm["users"] == [
        {
            "username": "${TRADINGNG_INITIAL_ADMIN_USERNAME}",
            "email": "platform-admin@amycat.com",
            "enabled": True,
            "emailVerified": True,
            "realmRoles": ["Admin"],
            "credentials": [
                {
                    "type": "password",
                    "value": "${TRADINGNG_INITIAL_ADMIN_PASSWORD}",
                    "temporary": True,
                }
            ],
        }
    ]
    compose = yaml.safe_load((ROOT / "deploy/compose.prod.yml").read_text())
    environment = compose["services"]["keycloak"]["environment"]
    assert environment["TRADINGNG_INITIAL_ADMIN_USERNAME"] == (
        "${TRADINGNG_INITIAL_ADMIN_USERNAME:?required}"
    )
    assert environment["TRADINGNG_INITIAL_ADMIN_PASSWORD"] == (
        "${TRADINGNG_INITIAL_ADMIN_PASSWORD:?required}"
    )


def test_oauth2_proxy_uses_pkce_secure_cookie_and_bearer_passthrough():
    config = (ROOT / "deploy/oauth2-proxy.cfg").read_text()
    for setting in (
        'http_address = "0.0.0.0:4180"',
        'code_challenge_method = "S256"',
        'cookie_name = "__Host-tradingng"',
        "cookie_secure = true",
        "cookie_httponly = true",
        'cookie_refresh = "4m"',
        "set_xauthrequest = true",
        "pass_access_token = true",
        "skip_jwt_bearer_tokens = true",
    ):
        assert setting in config
    assert 'cookie_refresh = "1h"' not in config
    assert "${OAUTH2_PROXY_CLIENT_SECRET}" in config
    assert "${OAUTH2_PROXY_COOKIE_SECRET}" in config
    assert 'http_address = "127.0.0.1:4180"' not in config
    example = (ROOT / ".env.platform.example").read_text()
    assert "openssl rand -hex 16" in example


def test_parallel_login_flows_keep_independent_bounded_csrf_cookies():
    oauth2_proxy = (ROOT / "deploy/oauth2-proxy.cfg").read_text()
    assert "cookie_csrf_per_request = true" in oauth2_proxy
    assert "cookie_csrf_per_request_limit = 16" in oauth2_proxy

    for relative_path in ("deploy/Caddyfile", "deploy/caddy/tradingng.caddy"):
        caddy = (ROOT / relative_path).read_text()
        favicon_handler = 'handle /favicon.ico {\n\t\trespond "" 204\n\t}'
        assert favicon_handler in caddy
        assert caddy.index(favicon_handler) < caddy.index("@noSession")


def test_oauth2_proxy_logs_out_the_keycloak_session_before_clearing_its_cookie():
    compose = yaml.safe_load((ROOT / "deploy/compose.prod.yml").read_text())
    assert compose["services"]["oauth2-proxy"]["image"] == (
        "quay.io/oauth2-proxy/oauth2-proxy:v7.15.1"
    )

    config = (ROOT / "deploy/oauth2-proxy.cfg").read_text()
    assert (
        'backend_logout_url = "https://ushome.amycat.com/realms/tradingng/'
        'protocol/openid-connect/logout?id_token_hint={id_token}"'
    ) in config


def test_browser_api_forward_auth_passes_the_access_token_to_platform():
    oauth2_proxy = (ROOT / "deploy/oauth2-proxy.cfg").read_text()
    assert "set_xauthrequest = true" in oauth2_proxy
    assert "pass_access_token = true" in oauth2_proxy
    assert "set_authorization_header = true" not in oauth2_proxy

    for relative_path in ("deploy/Caddyfile", "deploy/caddy/tradingng.caddy"):
        caddy = (ROOT / relative_path).read_text()
        assert caddy.count("route {") >= 2
        assert "handle_response @apiAuthSuccess" in caddy
        assert (
            "request_header X-Auth-Request-Access-Token {rp.header.X-Auth-Request-Access-Token}"
        ) in caddy
        assert caddy.count("copy_response_headers {") == 2
        assert caddy.count("include Set-Cookie") == 2
        assert "copy_response_headers Set-Cookie" not in caddy
        assert "handle_response @webAuthSuccess" in caddy
        assert (
            'request_header Authorization "Bearer '
            '{http.request.header.X-Auth-Request-Access-Token}"'
        ) in caddy
        assert "request_header -X-Auth-Request-Access-Token" in caddy
        assert "copy_headers Set-Cookie" not in caddy
        assert "request_header Set-Cookie" not in caddy
        assert "copy_headers Authorization" not in caddy


def test_oauth2_proxy_can_reach_host_oidc_endpoint_without_trusting_private_ca():
    compose = yaml.safe_load((ROOT / "deploy/compose.prod.yml").read_text())
    oauth2_proxy = compose["services"]["oauth2-proxy"]
    assert "ushome.amycat.com:host-gateway" in oauth2_proxy["extra_hosts"]
    assert compose["services"]["keycloak"]["environment"]["KC_HOSTNAME"] == (
        "https://ushome.amycat.com"
    )

    config = (ROOT / "deploy/oauth2-proxy.cfg").read_text()
    assert 'oidc_issuer_url = "https://ushome.amycat.com/realms/tradingng"' in config
    assert 'redirect_url = "https://ushome.amycat.com/oauth2/callback"' in config
    assert "ssl_insecure_skip_verify" not in config
    assert "tradingng.internal" not in json.dumps(compose)
    assert "tradingng.internal" not in json.dumps(
        json.loads((ROOT / "deploy/keycloak/tradingng-realm.json").read_text())
    )
    assert "tradingng.internal" not in config


def test_caddy_routes_identity_traffic_to_the_private_keycloak_port():
    config = (ROOT / "deploy/Caddyfile").read_text()
    assert "https://ushome.amycat.com:8443 {" in config
    assert "\n:8443 {" not in config
    assert "admin 127.0.0.1:2020" in config
    assert "auto_https disable_redirects" in config
    assert config.count("reverse_proxy 127.0.0.1:18081") == 2
    assert "reverse_proxy 127.0.0.1:8080" not in config
    assert "@apiBearer" in config
    assert "header_regexp Authorization ^Bearer[[:space:]]+[^[:space:]]+$" in config
    assert "@localApiToken" not in config
    assert "@noSession" in config
    assert "not header Cookie *__Host-tradingng=*" in config
    assert "redir * /oauth2/start?rd={http.request.uri} 302" in config
    assert "handle_errors" not in config


def test_public_caddy_routes_only_to_loopback_platform_services():
    config = (ROOT / "deploy/caddy/tradingng.caddy").read_text()
    assert config.startswith("ushome.amycat.com {")
    assert "@apiBearer" in config
    assert "header_regexp Authorization ^Bearer[[:space:]]+[^[:space:]]+$" in config
    assert config.count("reverse_proxy 127.0.0.1:18081") == 2
    assert config.count("reverse_proxy 127.0.0.1:4180") == 3
    assert "forward_auth 127.0.0.1:4180" not in config
    assert "reverse_proxy 127.0.0.1:8010" in config
    assert "root * /app/devs/TradingNG/web/dist" in config
    assert "Strict-Transport-Security" in config
    assert "X-Content-Type-Options" in config
    assert "Referrer-Policy" in config
    assert "Permissions-Policy" in config
    assert "-Server" in config
    assert "127.0.0.1:8000" not in config
    assert ".env" not in config


def test_public_maintenance_caddy_has_no_application_upstream():
    config = (ROOT / "deploy/caddy/tradingng-maintenance.caddy").read_text()
    assert config.startswith("ushome.amycat.com {")
    assert 'respond "TradingNG maintenance" 503' in config
    assert "reverse_proxy" not in config
    assert "forward_auth" not in config


def test_public_caddy_installer_is_domain_and_mode_guarded():
    installer = (ROOT / "scripts/install_public_caddy.sh").read_text()
    assert "--mode maintenance|final" in installer
    assert "--confirm-domain ushome.amycat.com" in installer
    assert "/etc/caddy/backups" in installer
    assert "/etc/caddy/sites-enabled/tradingng.caddy" in installer
    assert "import /etc/caddy/sites-enabled/*.caddy" in installer
    assert "caddy validate --config /etc/caddy/Caddyfile" in installer
    assert "systemctl reload caddy" in installer
    assert "tradingng-codex-gateway" not in installer


def test_gateway_service_supports_unbounded_turns_and_graceful_drain():
    service = (ROOT / "systemd/user/tradingng-codex-gateway.service").read_text()
    assert "Environment=CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS=0" in service
    assert "TimeoutStopSec=infinity" in service


def test_worker_pool_follows_api_and_scheduler_lifecycle():
    target = (ROOT / "systemd/user/tradingng-platform-workers.target").read_text()
    assert "PartOf=tradingng-platform-scheduler.service" in target
    scheduler = (ROOT / "systemd/user/tradingng-platform-scheduler.service").read_text()
    assert "PartOf=tradingng-platform-api.service" in scheduler
    assert "Wants=tradingng-platform-workers.target" in scheduler


def test_offline_compose_gate_supplies_every_required_bootstrap_variable():
    script = (ROOT / "scripts/verify_platform.sh").read_text()
    assert "TRADINGNG_POSTGRES_PASSWORD" in script
    assert ":${test_database_password}@127.0.0.1:5432/tradingng_test" in script
    assert "TRADINGNG_INITIAL_ADMIN_USERNAME=config-check" in script
    assert "TRADINGNG_INITIAL_ADMIN_PASSWORD=config-check" in script
    assert "TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET=config-check" in script


def test_verification_gate_covers_both_databases_public_config_and_artifacts():
    script = (ROOT / "scripts/verify_platform.sh").read_text()
    assert "platform/tests/integration" in script
    assert 'mysql_test_name="tradingng_test_$(openssl rand -hex 6)"' in script
    assert "deploy/caddy/tradingng.caddy" in script
    assert "deploy/caddy/tradingng-maintenance.caddy" in script
    assert "--adapter caddyfile" in script
    assert "docker compose" in script
    assert "sync_keycloak_public_urls.py --check" in script
    assert "sync_keycloak_user_management.py --check" in script
    assert "verify_artifacts.py" in script
    assert "--database-url-env TRADINGNG_VERIFY_DATABASE_URL" in script
    assert "tradingng\\.internal" in script


def test_public_environment_example_uses_one_canonical_origin():
    example = (ROOT / ".env.platform.example").read_text()
    assert "TRADINGNG_OIDC_ISSUER=https://ushome.amycat.com/realms/tradingng" in example
    assert "TRADINGNG_MCP_RESOURCE_URI=https://ushome.amycat.com/mcp" in example
    assert "TRADINGNG_MCP_ALLOWED_ORIGINS=https://ushome.amycat.com" in example
    assert "TRADINGNG_ALLOWED_ORIGINS=https://ushome.amycat.com" in example
    assert "tradingng.internal" not in example
    assert ":8443" not in example


def test_point_in_time_audit_operation_is_documented_without_private_identity():
    example = (ROOT / ".env.platform.example").read_text()
    sec_lines = [
        line for line in example.splitlines() if line.startswith("TRADINGNG_SEC_USER_AGENT=")
    ]
    assert sec_lines == [
        "TRADINGNG_SEC_USER_AGENT=MarketQuorum/0.1 (+https://ushome.amycat.com)"
    ]
    assert "@" not in sec_lines[0]

    pyproject = tomllib.loads((ROOT / "platform/pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["tradingng-platform-integrity-audit"] == (
        "tradingng_platform.integrity.main:main"
    )


def test_official_name_backfill_and_scheduler_use_sec_configuration():
    pyproject = tomllib.loads((ROOT / "platform/pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["tradingng-platform-name-backfill"] == (
        "tradingng_platform.instruments.backfill:main"
    )

    scheduler = (
        ROOT / "platform/src/tradingng_platform/scheduler/main.py"
    ).read_text()
    assert "user_agent=settings.sec_user_agent" in scheduler
    assert 'cache_dir=settings.sec_cache_dir / "instrument-names"' in scheduler
