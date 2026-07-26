from tradingng_platform.models import Base


def test_execution_schema_tables():
    required = {
        "circuit_breakers",
        "decisions",
        "evidence_items",
        "gateway_health_samples",
        "run_steps",
        "scheduler_policy",
        "vendor_health_samples",
        "worker_leases",
        "workers",
    }

    assert required <= set(Base.metadata.tables)
