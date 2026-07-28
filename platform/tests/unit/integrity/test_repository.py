from tradingng_platform.integrity.repository import IntegrityRepository


def test_latest_supported_subquery_ranks_one_current_policy_row_per_run():
    latest = IntegrityRepository.latest_supported_subquery()
    sql = str(latest.select())
    params = str(latest.select().compile().params.values())

    assert "row_number() OVER" in sql
    assert "run_integrity_assessments.run_id" in sql
    assert "point-in-time.v1" in params
    assert "integrity_rank =" in sql
