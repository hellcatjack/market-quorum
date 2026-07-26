import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from migrate_platform_database import (
    MigrationSafetyError,
    artifact_verification_statement,
    assert_empty_target,
    assert_seeded_coordination_locks,
    canonical_row,
    foreign_key_orphan_statement,
    migrate,
)
from sqlalchemy import insert, text
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.models import (
    Artifact,
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    Base,
    CoordinationLock,
    Decision,
    EvidenceItem,
    Instrument,
    RunConfigSnapshot,
    User,
    Validation,
)


def test_canonical_row_is_stable_for_database_value_types():
    row = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "created_at": datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        "amount": Decimal("1.2300"),
        "payload": {"b": 2, "a": 1},
    }

    assert canonical_row(row) == canonical_row(dict(reversed(tuple(row.items()))))
    assert b'"amount":"1.23"' in canonical_row(row)
    assert b'"id":"00000000-0000-0000-0000-000000000001"' in canonical_row(row)


def test_canonical_row_tolerates_mysql_json_double_rounding_only():
    round_trips = (
        (48.499691009521484, 48.49969100952149),
        (49.091148376464844, 49.09114837646485),
    )

    for source, mysql_round_trip in round_trips:
        assert canonical_row({"metric": source}) == canonical_row({"metric": mysql_round_trip})
    assert canonical_row({"metric": round_trips[0][0]}) != canonical_row({"metric": 48.4997})


def test_target_preflight_rejects_business_rows():
    with pytest.raises(MigrationSafetyError, match="target database is not empty"):
        assert_empty_target({"users": 1, "coordination_locks": 3})


def test_target_preflight_allows_only_seeded_coordination_rows():
    assert_empty_target({"users": 0, "coordination_locks": 3})


def test_target_preflight_requires_the_exact_coordination_seeds():
    assert_seeded_coordination_locks({"global:admission", "global:archive", "global:retention"})
    with pytest.raises(MigrationSafetyError, match="coordination lock seeds"):
        assert_seeded_coordination_locks({"global:admission", "ticker:NVDA"})


def test_artifact_verification_selects_values_instead_of_orm_entities():
    statement = artifact_verification_statement()

    assert tuple(column.key for column in statement.selected_columns) == (
        Artifact.id.key,
        Artifact.storage_key.key,
        Artifact.sha256.key,
    )


def test_self_referential_foreign_key_verification_uses_a_parent_alias():
    retry_foreign_key = next(
        foreign_key
        for foreign_key in AssessmentRun.__table__.foreign_keys
        if foreign_key.parent.name == "retry_of_run_id"
    )

    sql = str(
        foreign_key_orphan_statement(AssessmentRun.__table__, retry_foreign_key).compile(
            dialect=mysql.dialect()
        )
    )

    assert "assessment_runs AS parent_assessment_runs_retry_of_run_id" in sql


def _dedicated_migration_urls() -> tuple[str, str]:
    source_url = os.getenv("TRADINGNG_MIGRATION_SOURCE_TEST_URL")
    target_url = os.getenv("TRADINGNG_MIGRATION_TARGET_TEST_URL")
    if not source_url or not target_url:
        pytest.skip("dedicated PostgreSQL and MySQL migration test URLs are not configured")
    source = make_url(source_url)
    target = make_url(target_url)
    if source.get_backend_name() != "postgresql" or target.get_backend_name() != "mysql":
        pytest.fail("migration test URLs use the wrong database dialects")
    for url in (source, target):
        if not str(url.database).startswith("tradingng_test"):
            pytest.fail("migration tests require dedicated tradingng_test databases")
    return source_url, target_url


async def _reset_postgres_source(source_url: str) -> None:
    engine = create_async_engine(source_url)
    try:
        async with engine.begin() as connection:
            tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
            await connection.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
            await connection.execute(
                insert(CoordinationLock),
                [
                    {"lock_key": "global:admission"},
                    {"lock_key": "global:archive"},
                    {"lock_key": "global:retention"},
                ],
            )
    finally:
        await engine.dispose()


async def _seed_migration_fixture(source_url: str, artifact_root) -> None:
    observed_at = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    ids = {
        name: UUID(f"00000000-0000-0000-0000-{index:012d}")
        for index, name in enumerate(
            ("user", "instrument", "batch", "request", "snapshot", "run", "artifact"),
            start=1,
        )
    }
    source_file = artifact_root.parent / "migration-fixture.json"
    source_file.write_bytes(b'{"ticker":"TEST"}\n')
    stored = LocalArtifactStore(artifact_root).put(
        ids["run"], "final_report", "application/json", source_file
    )

    rows = (
        (
            User,
            {
                "id": ids["user"],
                "issuer": "https://issuer.test/realms/tradingng",
                "subject": "fixture-user",
                "display_name": "Fixture User",
                "email": "fixture@example.test",
                "status": "active",
                "created_at": observed_at,
            },
        ),
        (
            Instrument,
            {
                "id": ids["instrument"],
                "canonical_ticker": "TEST",
                "asset_type": "equity",
                "exchange": "TEST",
                "name": "Fixture Equity",
                "metadata_json": {"source": "migration-test"},
                "created_at": observed_at,
            },
        ),
        (
            AssessmentBatch,
            {
                "id": ids["batch"],
                "submitted_by": ids["user"],
                "idempotency_key": "migration-fixture",
                "defaults_json": {"depth": "deep"},
                "created_at": observed_at,
            },
        ),
        (
            AssessmentRequest,
            {
                "id": ids["request"],
                "batch_id": ids["batch"],
                "instrument_id": ids["instrument"],
                "analysis_date": date(2026, 7, 25),
                "requested_config_json": {"language": "zh-CN"},
                "created_at": observed_at,
            },
        ),
        (
            RunConfigSnapshot,
            {
                "id": ids["snapshot"],
                "content_json": {"model": "inherited", "reasoning": "inherited"},
                "sha256": "1" * 64,
                "gateway_snapshot_id": "fixture",
                "created_at": observed_at,
            },
        ),
        (
            AssessmentRun,
            {
                "id": ids["run"],
                "request_id": ids["request"],
                "attempt": 1,
                "status": "succeeded",
                "config_snapshot_id": ids["snapshot"],
                "retry_of_run_id": None,
                "version": 1,
                "admitted_at": observed_at,
                "started_at": observed_at,
                "finished_at": observed_at,
                "error_code": None,
                "error_summary": None,
                "created_at": observed_at,
            },
        ),
        (
            Artifact,
            {
                "id": ids["artifact"],
                "run_id": ids["run"],
                "kind": stored.kind,
                "media_type": stored.media_type,
                "size": stored.size,
                "sha256": stored.sha256,
                "storage_key": stored.storage_key,
                "redacted": True,
                "retention_class": "permanent",
                "deleted_at": None,
                "metadata_json": {"fixture": True},
                "created_at": observed_at,
            },
        ),
        (
            Decision,
            {
                "run_id": ids["run"],
                "rating": "hold",
                "executive_summary": "Fixture summary",
                "investment_thesis": "Fixture thesis",
                "price_target": Decimal("123.450000"),
                "time_horizon": "12 months",
                "structured_json": {"confidence": 0.75},
                "created_at": observed_at,
            },
        ),
        (
            EvidenceItem,
            {
                "run_id": ids["run"],
                "source": "fixture",
                "tool_name": "fixture_tool",
                "arguments_json": {"ticker": "TEST"},
                "collected_at": observed_at,
                "effective_at": observed_at,
                "freshness": "current",
                "artifact_id": ids["artifact"],
                "content_hash": "2" * 64,
                "created_at": observed_at,
            },
        ),
        (
            Validation,
            {
                "run_id": ids["run"],
                "horizon": 30,
                "status": "completed",
                "scheduled_for": observed_at,
                "observed_at": observed_at,
                "raw_return": Decimal("0.1200000000"),
                "benchmark_return": Decimal("0.1000000000"),
                "alpha": Decimal("0.0200000000"),
                "max_adverse_excursion": Decimal("-0.0300000000"),
                "max_favorable_excursion": Decimal("0.1500000000"),
                "trigger_results_json": {"verified": True},
                "data_artifact_id": ids["artifact"],
                "attempts": 1,
                "next_attempt_at": None,
                "error_code": None,
                "created_at": observed_at,
            },
        ),
    )
    engine = create_async_engine(source_url)
    try:
        async with engine.begin() as connection:
            for model, values in rows:
                await connection.execute(insert(model), values)
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_copier_preserves_fixture_rows_and_artifact_hashes(tmp_path):
    source_url, target_url = _dedicated_migration_urls()
    artifact_root = tmp_path / "artifacts"
    await _reset_postgres_source(source_url)
    await _seed_migration_fixture(source_url, artifact_root)

    manifest = await migrate(source_url, target_url, artifact_root)

    assert manifest["verified_artifacts"] == 1
    assert manifest["tables"]["users"]["rows"] == 1
    assert manifest["tables"]["instruments"]["rows"] == 1
    assert manifest["tables"]["assessment_runs"]["rows"] == 1
    assert manifest["tables"]["evidence_items"]["rows"] == 1
    assert manifest["tables"]["decisions"]["rows"] == 1
    assert manifest["tables"]["validations"]["rows"] == 1
