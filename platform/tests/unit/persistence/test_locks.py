from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.dialects import mysql, postgresql

from tradingng_platform.models import AssessmentRun, CoordinationLock
from tradingng_platform.persistence.locks import coordination_lock_statement
from tradingng_platform.webhooks.worker import webhook_claim_statement


def test_coordination_lock_has_a_bounded_primary_key():
    assert list(CoordinationLock.__table__.primary_key.columns.keys()) == ["lock_key"]
    assert CoordinationLock.lock_key.type.length == 191


def test_assessment_run_has_a_deterministic_claim_index():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in AssessmentRun.__table__.indexes
    }

    assert indexes["ix_assessment_runs_claim"] == ("status", "admitted_at", "id")


def test_nonblocking_coordination_lock_compiles_for_both_databases():
    statement = coordination_lock_statement("ticker:NVDA", wait=False)

    for dialect in (postgresql.dialect(), mysql.dialect()):
        sql = str(statement.compile(dialect=dialect))
        assert "FOR UPDATE" in sql
        assert "SKIP LOCKED" in sql


def test_webhook_claim_order_compiles_without_nulls_first_for_mysql():
    statement = webhook_claim_statement(datetime(2026, 7, 25, tzinfo=timezone.utc))

    sql = str(statement.compile(dialect=mysql.dialect()))
    assert "NULLS FIRST" not in sql
    assert "CASE WHEN" in sql


def test_platform_runtime_contains_no_postgresql_advisory_lock_sql():
    package = Path(__file__).resolve().parents[3] / "src" / "tradingng_platform"
    offenders = []
    for source_path in sorted(package.rglob("*.py")):
        source = source_path.read_text(encoding="utf-8")
        if "pg_advisory" in source or "pg_try_advisory" in source:
            offenders.append(source_path.relative_to(package).as_posix())

    assert offenders == []
