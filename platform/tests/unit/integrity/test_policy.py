from datetime import date, datetime, timezone

from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.policy import PointInTimeRecorder


def test_at_risk_dominates_unknown_and_safe():
    recorder = PointInTimeRecorder(
        date(2025, 7, 1),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    recorder.record("get_stock_data", IntegrityStatus.SAFE, "date_bounded")
    recorder.record("get_unknown_data", IntegrityStatus.UNKNOWN, "unregistered_tool")
    recorder.record("get_income_statement", IntegrityStatus.AT_RISK, "future_publication")

    document = recorder.finalize()

    assert document.policy_version == "point-in-time.v1"
    assert document.status is IntegrityStatus.AT_RISK
    assert [item.reason_code for item in document.findings] == [
        "date_bounded",
        "unregistered_tool",
        "future_publication",
    ]
    assert len(document.input_fingerprint) == 64


def test_historical_run_without_observed_tools_is_unknown():
    recorder = PointInTimeRecorder(
        date(2025, 7, 1),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    document = recorder.finalize()

    assert document.status is IntegrityStatus.UNKNOWN
    assert document.temporal_scope == "historical_reconstruction"
    assert document.reason_codes == ("no_observed_tools",)


def test_live_run_is_safe_and_explicitly_scoped():
    now = datetime(2026, 7, 27, 15, tzinfo=timezone.utc)

    document = PointInTimeRecorder(now.date(), now=now).finalize()

    assert document.status is IntegrityStatus.SAFE
    assert document.temporal_scope == "contemporaneous"
    assert document.reason_codes == ("live_current_snapshot",)


def test_input_fingerprint_is_stable_for_identical_findings():
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    first = PointInTimeRecorder(date(2025, 7, 1), now=now)
    second = PointInTimeRecorder(date(2025, 7, 1), now=now)
    for recorder in (first, second):
        recorder.record(
            "get_news",
            IntegrityStatus.SAFE,
            "date_bounded",
            {"end_date": "2025-07-01"},
        )

    assert first.finalize().input_fingerprint == second.finalize().input_fingerprint


def test_checked_at_is_not_part_of_the_input_fingerprint():
    first = PointInTimeRecorder(
        date(2025, 7, 1),
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    second = PointInTimeRecorder(
        date(2025, 7, 1),
        now=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    for recorder in (first, second):
        recorder.record("get_stock_data", IntegrityStatus.SAFE, "date_bounded")

    assert first.finalize().input_fingerprint == second.finalize().input_fingerprint
