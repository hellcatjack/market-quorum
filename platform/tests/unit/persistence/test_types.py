from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from tradingng_platform.models import Base
from tradingng_platform.persistence.types import UtcDateTime


def _ddl(dialect) -> str:
    return "\n".join(
        str(CreateTable(table).compile(dialect=dialect)) for table in Base.metadata.sorted_tables
    )


def test_every_model_table_compiles_for_mysql_without_jsonb():
    ddl = _ddl(mysql.dialect())

    assert "JSONB" not in ddl
    assert " JSON" in ddl
    assert "DATETIME(6)" in ddl


def test_postgresql_keeps_jsonb():
    assert "JSONB" in _ddl(postgresql.dialect())


def test_utc_datetime_normalizes_mysql_bind_and_result_values():
    column_type = UtcDateTime()
    eastern = timezone(timedelta(hours=-4))
    value = datetime(2026, 7, 25, 12, 0, 0, 123456, tzinfo=eastern)

    bound = column_type.process_bind_param(value, mysql.dialect())
    assert bound == datetime(2026, 7, 25, 16, 0, 0, 123456)
    restored = column_type.process_result_value(bound, mysql.dialect())
    assert restored == datetime(2026, 7, 25, 16, 0, 0, 123456, tzinfo=timezone.utc)


def test_historical_migrations_use_portable_json_types():
    versions = Path(__file__).resolve().parents[3] / "migrations" / "versions"

    for migration in sorted(versions.glob("*.py")):
        source = migration.read_text(encoding="utf-8")
        if "_0005_" not in migration.name:
            assert "def json_type()" in source, migration.name
            assert source.count("postgresql.JSONB(") == 1, migration.name
            assert "def datetime_type()" in source, migration.name
            assert source.count("sa.DateTime(timezone=True)") == 1, migration.name


def test_every_migration_revision_can_be_imported():
    platform_root = Path(__file__).resolve().parents[3]
    scripts = ScriptDirectory.from_config(Config(str(platform_root / "alembic.ini")))

    assert [revision.revision for revision in scripts.walk_revisions()] == [
        "20260726_0006",
        "20260725_0005",
        "20260725_0004",
        "20260725_0003",
        "20260725_0002",
        "20260725_0001",
    ]
