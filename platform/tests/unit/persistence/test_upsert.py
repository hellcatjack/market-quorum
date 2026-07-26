from pathlib import Path

import pytest
from sqlalchemy.dialects import mysql, postgresql

from tradingng_platform.models import Role, Worker
from tradingng_platform.persistence.upsert import insert_ignore, upsert


def test_insert_ignore_compiles_for_both_supported_dialects():
    postgresql_statement = insert_ignore(
        "postgresql",
        Role,
        {"name": "Admin"},
        [Role.name],
    )
    mysql_statement = insert_ignore(
        "mysql",
        Role,
        {"name": "Admin"},
        [Role.name],
    )

    assert "ON CONFLICT" in str(postgresql_statement.compile(dialect=postgresql.dialect()))
    mysql_sql = str(mysql_statement.compile(dialect=mysql.dialect()))
    assert "ON DUPLICATE KEY UPDATE" in mysql_sql
    assert "name = roles.name" in mysql_sql


def test_upsert_update_compiles_for_mysql():
    statement = upsert(
        "mysql",
        Worker,
        {"instance_name": "worker-1", "status": "idle", "pid": 7},
        [Worker.instance_name],
        {"status": "idle", "pid": 7},
    )

    sql = str(statement.compile(dialect=mysql.dialect()))
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "status = %s" in sql
    assert "pid = %s" in sql


def test_upsert_rejects_unsupported_dialect():
    with pytest.raises(RuntimeError, match="unsupported database dialect"):
        insert_ignore("sqlite", Role, {"name": "Admin"}, [Role.name])


def test_repositories_do_not_import_postgresql_insert_directly():
    package = Path(__file__).resolve().parents[3] / "src" / "tradingng_platform"
    offenders = []
    for source_path in sorted(package.rglob("*.py")):
        if source_path.name == "upsert.py":
            continue
        source = source_path.read_text(encoding="utf-8")
        if "sqlalchemy.dialects.postgresql import insert" in source:
            offenders.append(source_path.relative_to(package).as_posix())

    assert offenders == []
