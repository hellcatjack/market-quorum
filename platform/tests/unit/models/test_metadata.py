from tradingng_platform.models import Base


def test_initial_schema_contains_authoritative_tables():
    required = {
        "api_credentials",
        "artifacts",
        "assessment_batches",
        "assessment_requests",
        "assessment_runs",
        "audit_events",
        "instruments",
        "roles",
        "run_config_snapshots",
        "run_events",
        "user_roles",
        "users",
    }

    assert required <= set(Base.metadata.tables)
