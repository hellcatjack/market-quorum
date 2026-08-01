from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from tradingng_platform.config import Settings


def test_settings_are_loopback_and_project_local(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(
        "TRADINGNG_DATABASE_URL",
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng",
    )
    monkeypatch.setenv("TRADINGNG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADINGNG_ALLOWED_ORIGINS", "https://one.test, https://two.test/")
    webhook_key = Fernet.generate_key().decode()
    monkeypatch.setenv("TRADINGNG_WEBHOOK_ENCRYPTION_KEY", webhook_key)
    monkeypatch.setenv(
        "TRADINGNG_WEBHOOK_PRIVATE_HOST_ALLOWLIST",
        "hooks.internal, reports.internal ",
    )

    settings = Settings()

    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8010
    assert str(settings.alpha_vantage_broker_url) == "http://127.0.0.1:8020/"
    assert settings.alpha_vantage_broker_host == "127.0.0.1"
    assert settings.alpha_vantage_broker_port == 8020
    assert settings.alpha_vantage_broker_utilization == 1.0
    assert settings.alpha_vantage_broker_max_in_flight == 3
    assert settings.alpha_vantage_broker_admission_queue_limit == 6
    assert settings.alpha_vantage_auto_retry_attempts == 2
    assert str(settings.gateway_url) == "http://127.0.0.1:8000/"
    assert settings.artifact_dir == tmp_path / "artifacts"
    assert settings.job_dir == tmp_path / "jobs"
    assert settings.alpha_vantage_cache_dir == tmp_path / "vendor-cache" / "alpha-vantage"
    assert settings.sec_cache_dir == tmp_path / "vendor-cache" / "sec"
    assert settings.sec_user_agent == "MarketQuorum/0.1 (+https://ushome.amycat.com)"
    assert settings.sec_request_timeout_seconds == 10
    assert settings.allowed_origins == ("https://one.test", "https://two.test")
    assert settings.webhook_encryption_key.get_secret_value() == webhook_key
    assert settings.webhook_private_host_allowlist == (
        "hooks.internal",
        "reports.internal",
    )


def test_settings_build_mysql_url_from_db_environment(monkeypatch):
    monkeypatch.delenv("TRADINGNG_DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "db.internal:3307")
    monkeypatch.setenv("DB_NAME", "tradingNG")
    monkeypatch.setenv("DB_USER", "app-user")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/word")
    monkeypatch.setenv("DB_CHARSET", "utf8mb4")
    monkeypatch.setenv("DB_COLLATE", "utf8mb4_unicode_ci")

    settings = Settings(_env_file=None)

    url = make_url(settings.database_url)
    assert url.drivername == "mysql+asyncmy"
    assert (url.host, url.port, url.database) == ("db.internal", 3307, "tradingNG")
    assert (url.username, url.password) == ("app-user", "p@ss/word")
    assert settings.database_dialect == "mysql"


def test_explicit_database_url_wins_over_db_environment(monkeypatch):
    monkeypatch.setenv("TRADINGNG_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DB_HOST", "db.internal:3306")
    monkeypatch.setenv("DB_NAME", "tradingNG")
    monkeypatch.setenv("DB_USER", "app-user")
    monkeypatch.setenv("DB_PASSWORD", "secret")

    assert Settings(_env_file=None).database_url == "sqlite+aiosqlite:///:memory:"


def test_public_identity_and_mcp_defaults_use_the_canonical_origin():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
    )

    assert str(settings.oidc_issuer) == ("https://ushome.amycat.com/realms/tradingng")
    assert str(settings.mcp_resource_uri) == "https://ushome.amycat.com/mcp"
    assert settings.validation_price_providers == ("stocklean",)
    assert settings.alpha_vantage_api_key is None
    assert settings.research_data_vendor_chain == ("stocklean",)


def test_sec_user_agent_cannot_be_blank():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
            sec_user_agent="   ",
        )


def test_validation_provider_order_and_alpha_key_are_secret(monkeypatch):
    monkeypatch.setenv("TRADINGNG_VALIDATION_PRICE_PROVIDERS", "alphavantage,yfinance")
    monkeypatch.setenv("TRADINGNG_ALPHA_VANTAGE_API_KEY", "premium-secret")

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
    )

    assert settings.validation_price_providers == ("alphavantage", "yfinance")
    assert settings.alpha_vantage_api_key.get_secret_value() == "premium-secret"
    assert "premium-secret" not in repr(settings)


def test_legacy_alpha_configuration_cannot_override_stocklean_routing(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "research-premium-secret")
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        alpha_vantage_api_key="validation-premium-secret",
        research_data_vendor_chain=("alpha_vantage", "yfinance"),
        validation_price_providers=("alphavantage", "yfinance"),
    )

    assert settings.effective_research_data_vendor_chain == ("stocklean",)
    assert settings.effective_validation_price_providers == ("stocklean",)
    assert settings.alpha_vantage_retry_attempts == 6
    assert settings.alpha_vantage_retry_base_seconds == 5
    assert settings.alpha_vantage_retry_max_seconds == 60
    assert "research-premium-secret" not in repr(settings)
    assert "validation-premium-secret" not in repr(settings)


def test_legacy_yahoo_configuration_cannot_override_stocklean_routing(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        alpha_vantage_api_key=None,
        research_data_vendor_chain=("yfinance",),
        validation_price_providers=("yfinance",),
    )

    assert settings.effective_research_data_vendor_chain == ("stocklean",)
    assert settings.effective_validation_price_providers == ("stocklean",)


def test_research_vendor_chain_preserves_explicit_order(monkeypatch):
    monkeypatch.setenv(
        "TRADINGNG_RESEARCH_DATA_VENDOR_CHAIN",
        "yfinance,alpha_vantage",
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
    )

    assert settings.research_data_vendor_chain == ("yfinance", "alpha_vantage")


@pytest.mark.parametrize(
    "value",
    ["alpha_vantage,alpha_vantage", "unknown,yfinance", ""],
)
def test_research_vendor_chain_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("TRADINGNG_RESEARCH_DATA_VENDOR_CHAIN", value)

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        )


@pytest.mark.parametrize("name", ["tradingNG;DROP", "../tradingNG", "trading-NG"])
def test_mysql_database_identifier_is_rejected(monkeypatch, name):
    monkeypatch.delenv("TRADINGNG_DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_NAME", name)
    monkeypatch.setenv("DB_USER", "app-user")
    monkeypatch.setenv("DB_PASSWORD", "secret")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_keycloak_admin_settings_keep_client_secret_private(monkeypatch):
    monkeypatch.setenv("TRADINGNG_KEYCLOAK_ADMIN_URL", "http://127.0.0.1:18081")
    monkeypatch.setenv("TRADINGNG_KEYCLOAK_ADMIN_REALM", "tradingng")
    monkeypatch.setenv("TRADINGNG_KEYCLOAK_ADMIN_CLIENT_ID", "tradingng-user-admin")
    monkeypatch.setenv("TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET", "management-secret")

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
    )

    assert str(settings.keycloak_admin_url) == "http://127.0.0.1:18081/"
    assert settings.keycloak_admin_realm == "tradingng"
    assert settings.keycloak_admin_client_id == "tradingng-user-admin"
    assert settings.keycloak_admin_client_secret.get_secret_value() == "management-secret"
    assert "management-secret" not in repr(settings)
    assert "management-secret" not in settings.model_dump_json()


def test_keycloak_admin_secret_can_be_unset():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
    )

    assert settings.keycloak_admin_client_secret is None


@pytest.mark.parametrize("field", ["../master", "", "client/child"])
def test_keycloak_admin_identifiers_are_path_safe(field):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
            keycloak_admin_realm=field,
        )
