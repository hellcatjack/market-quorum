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
    assert str(settings.gateway_url) == "http://127.0.0.1:8000/"
    assert settings.artifact_dir == tmp_path / "artifacts"
    assert settings.job_dir == tmp_path / "jobs"
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


@pytest.mark.parametrize("name", ["tradingNG;DROP", "../tradingNG", "trading-NG"])
def test_mysql_database_identifier_is_rejected(monkeypatch, name):
    monkeypatch.delenv("TRADINGNG_DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_NAME", name)
    monkeypatch.setenv("DB_USER", "app-user")
    monkeypatch.setenv("DB_PASSWORD", "secret")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
