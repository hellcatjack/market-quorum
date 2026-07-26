from datetime import datetime, timedelta, timezone

from tradingng_platform.retention.policy import is_due


def test_retention_ages_and_legal_hold():
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)

    assert is_due("raw_180d", now - timedelta(days=181), {}, now)
    assert is_due("diagnostic_90d", now - timedelta(days=91), {}, now)
    assert not is_due("permanent", now - timedelta(days=1000), {}, now)
    assert not is_due(
        "raw_180d",
        now - timedelta(days=1000),
        {"legal_hold": True},
        now,
    )
