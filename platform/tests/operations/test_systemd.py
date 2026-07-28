import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UNITS = ROOT / "systemd/user"


def test_gateway_unit_is_isolated_and_platform_units_are_strict():
    gateway = (UNITS / "tradingng-codex-gateway.service").read_text()
    assert "127.0.0.1:8000" not in gateway or "codex_gateway" in gateway
    assert "mcp_servers.playwright.enabled=true" not in gateway
    assert "audit" not in gateway.lower()

    platform_units = sorted(UNITS.glob("tradingng-platform-*.service"))
    assert len(platform_units) == 7
    combined = "\n".join(path.read_text() for path in platform_units)
    assert "EnvironmentFile=/app/devs/TradingNG/.env.platform" in combined
    assert "127.0.0.1:8010" not in combined
    assert "codex-gateway-audit" not in combined
    assert "mcp_servers.playwright.enabled=true" not in combined
    for path in platform_units:
        content = path.read_text()
        assert "WorkingDirectory=/app/devs/TradingNG" in content
        if "containers" not in path.name:
            assert "Restart=always" in content or "Restart=on-failure" in content


def test_worker_pool_target_provisions_exactly_32_stable_instances():
    target = (UNITS / "tradingng-platform-workers.target").read_text()
    instances = {
        int(instance)
        for instance in re.findall(r"tradingng-platform-worker@(\d+)\.service", target)
    }
    assert instances == set(range(1, 33))
    assert "tradingng-platform-worker@33.service" not in target

    worker = (UNITS / "tradingng-platform-worker@.service").read_text()
    assert "PartOf=tradingng-platform-workers.target" in worker
    assert "Environment=TRADINGNG_WORKER_INSTANCE=%i" in worker


def test_documented_enablement_uses_the_worker_pool_target():
    readme = (ROOT / "README.md").read_text()
    command = "systemctl --user enable --now tradingng-platform-workers.target"
    assert command in readme


def test_restore_manages_workers_as_one_pool():
    restore = (ROOT / "scripts/restore_platform.sh").read_text()
    assert restore.count("tradingng-platform-workers.target") == 2
    assert "tradingng-platform-worker@1.service" not in restore
    assert "tradingng-platform-worker@2.service" not in restore
    assert restore.count("tradingng-platform-alpha-broker.service") == 2


def test_api_uses_public_trust_and_business_services_preflight_mysql():
    api = (UNITS / "tradingng-platform-api.service").read_text()
    assert "SSL_CERT_FILE" not in api
    preflight = (
        "ExecStartPre=/app/devs/TradingNG/.venv/bin/python "
        "/app/devs/TradingNG/scripts/check_platform_database.py"
    )
    for unit_name in (
        "tradingng-platform-api.service",
        "tradingng-platform-scheduler.service",
        "tradingng-platform-validation.service",
        "tradingng-platform-worker@.service",
    ):
        assert preflight in (UNITS / unit_name).read_text()


def test_alpha_broker_is_loopback_only_and_required_by_alpha_consumers():
    broker = (UNITS / "tradingng-platform-alpha-broker.service").read_text()
    assert "tradingng_platform.vendors.alpha_vantage_broker_app" in broker
    assert "EnvironmentFile=/app/devs/TradingNG/.env.platform" in broker
    assert "NoNewPrivileges=yes" in broker
    assert "check_platform_database.py" not in broker

    for unit_name in (
        "tradingng-platform-api.service",
        "tradingng-platform-scheduler.service",
        "tradingng-platform-validation.service",
        "tradingng-platform-worker@.service",
    ):
        unit = (UNITS / unit_name).read_text()
        assert "After=tradingng-platform-alpha-broker.service" in unit
        assert "Requires=tradingng-platform-alpha-broker.service" in unit

    example = (ROOT / ".env.platform.example").read_text()
    assert "TRADINGNG_ALPHA_VANTAGE_BROKER_HOST=127.0.0.1" in example
    assert "TRADINGNG_ALPHA_VANTAGE_BROKER_PORT=8020" in example
    assert "TRADINGNG_ALPHA_VANTAGE_BROKER_UTILIZATION=0.8" in example
    assert "TRADINGNG_ALPHA_VANTAGE_BROKER_MAX_IN_FLIGHT=3" in example
    assert "TRADINGNG_ALPHA_VANTAGE_AUTO_RETRY_ATTEMPTS=2" in example


def test_verification_requires_the_alpha_broker_to_be_active():
    verify = (ROOT / "scripts/verify_platform.sh").read_text()
    assert "is-active --quiet tradingng-platform-alpha-broker.service" in verify


def test_postgres_is_described_as_keycloak_only_and_legacy_caddy_is_disabled():
    containers = (UNITS / "tradingng-platform-containers.service").read_text()
    assert "TradingNG Keycloak PostgreSQL and identity containers" in containers

    caddy = (UNITS / "tradingng-platform-caddy.service").read_text()
    assert "Legacy TradingNG internal TLS proxy (rollback only)" in caddy

    readme = (ROOT / "README.md").read_text()
    assert "enable --now tradingng-platform-caddy.service" not in readme
    assert "disable --now tradingng-platform-caddy.service" in readme


def test_container_unit_refreshes_docker_group_membership_for_lingering_manager():
    containers = (UNITS / "tradingng-platform-containers.service").read_text()
    assert containers.count("/usr/bin/sg docker -c") == 2
    assert "docker compose --env-file" in containers


def test_caddy_never_receives_or_dumps_platform_secrets():
    caddy = (UNITS / "tradingng-platform-caddy.service").read_text()
    assert "EnvironmentFile=" not in caddy
    assert "--environ" not in caddy
    assert "--address 127.0.0.1:2020" in caddy
