import re
from pathlib import Path
from typing import Annotated

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

_DB_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
_MYSQL_CHARSETS = frozenset({"utf8mb4"})
_MYSQL_COLLATIONS = frozenset({"utf8mb4_unicode_ci", "utf8mb4_0900_ai_ci"})


def _split_host_port(value: str) -> tuple[str, int]:
    host, separator, raw_port = value.rpartition(":")
    if separator and host and raw_port.isdigit():
        return host, int(raw_port)
    return value, 3306


class Settings(BaseSettings):
    """Immutable configuration loaded from ``TRADINGNG_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="TRADINGNG_",
        env_file=(".env", ".env.platform"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: str = Field(default="", repr=False)
    db_host: str | None = Field(default=None, validation_alias="DB_HOST", exclude=True)
    db_name: str | None = Field(default=None, validation_alias="DB_NAME", exclude=True)
    db_user: str | None = Field(default=None, validation_alias="DB_USER", exclude=True)
    db_password: SecretStr | None = Field(
        default=None,
        validation_alias="DB_PASSWORD",
        exclude=True,
    )
    db_charset: str = Field(
        default="utf8mb4",
        validation_alias="DB_CHARSET",
        exclude=True,
    )
    db_collate: str = Field(
        default="utf8mb4_unicode_ci",
        validation_alias="DB_COLLATE",
        exclude=True,
    )
    data_dir: Path = Path("/app/devs/TradingNG/var")
    api_host: str = "127.0.0.1"
    api_port: int = 8010
    gateway_url: AnyHttpUrl = "http://127.0.0.1:8000"
    oidc_issuer: AnyHttpUrl = "https://ushome.amycat.com/realms/tradingng"
    oidc_audience: str = "tradingng-api"
    oidc_jwks_ttl_seconds: int = 300
    mcp_allowed_origins: Annotated[tuple[str, ...], NoDecode] = ()
    mcp_resource_uri: AnyHttpUrl = "https://ushome.amycat.com/mcp"
    token_pepper: SecretStr = SecretStr("")
    webhook_encryption_key: SecretStr = SecretStr("")
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = ()
    webhook_private_host_allowlist: Annotated[tuple[str, ...], NoDecode] = ()
    max_running_validation: int = 2

    @model_validator(mode="after")
    def resolve_database_url(self):
        if self.database_url:
            return self
        required = (self.db_host, self.db_name, self.db_user, self.db_password)
        if any(value is None for value in required):
            raise ValueError("TRADINGNG_DATABASE_URL or every DB_* setting is required")
        if not _DB_IDENTIFIER.fullmatch(str(self.db_name)):
            raise ValueError("DB_NAME is not a safe MySQL identifier")
        if self.db_charset not in _MYSQL_CHARSETS or self.db_collate not in _MYSQL_COLLATIONS:
            raise ValueError("MySQL charset or collation is not allowed")
        host, port = _split_host_port(str(self.db_host))
        resolved = URL.create(
            "mysql+asyncmy",
            username=str(self.db_user),
            password=self.db_password.get_secret_value(),
            host=host,
            port=port,
            database=str(self.db_name),
            query={"charset": self.db_charset},
        ).render_as_string(hide_password=False)
        object.__setattr__(self, "database_url", resolved)
        return self

    @field_validator(
        "allowed_origins",
        "mcp_allowed_origins",
        "webhook_private_host_allowlist",
        mode="before",
    )
    @classmethod
    def parse_comma_separated_tuple(cls, value):
        if isinstance(value, str):
            return tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())
        return value

    @computed_field
    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @computed_field
    @property
    def job_dir(self) -> Path:
        return self.data_dir / "jobs"

    @computed_field
    @property
    def database_dialect(self) -> str:
        return make_url(self.database_url).get_backend_name()
