from datetime import date, datetime, timezone

from langchain_core.messages import ToolMessage

from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.policy import (
    PointInTimeRecorder,
    evidence_temporal_metadata,
    record_observed_tool,
)


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


def test_fred_vintage_tool_message_is_safe_and_time_bounded():
    analysis_date = date(2025, 7, 1)
    recorder = PointInTimeRecorder(
        analysis_date,
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    output = ToolMessage(
        content=(
            "POINT_IN_TIME_VINTAGE: FRED observations are limited to values "
            "available on 2025-07-01."
        ),
        tool_call_id="macro-1",
    )

    record_observed_tool(recorder, "get_macro_indicators", output)
    effective_at, freshness = evidence_temporal_metadata(
        "get_macro_indicators",
        analysis_date,
        output,
    )

    document = recorder.finalize()
    assert document.status is IntegrityStatus.SAFE
    assert document.reason_codes == ("fred_vintage_applied",)
    assert effective_at == "2025-07-01T23:59:59.999999+00:00"
    assert freshness == "point_in_time_vintage"


def test_unavailable_historical_macro_data_is_safe_without_an_effective_date():
    analysis_date = date(2025, 7, 1)
    recorder = PointInTimeRecorder(
        analysis_date,
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    output = ToolMessage(
        content="DATA_UNAVAILABLE: optional macro_data could not be retrieved.",
        tool_call_id="macro-unavailable",
    )

    record_observed_tool(recorder, "get_macro_indicators", output)
    effective_at, freshness = evidence_temporal_metadata(
        "get_macro_indicators",
        analysis_date,
        output,
    )

    document = recorder.finalize()
    assert document.status is IntegrityStatus.SAFE
    assert document.reason_codes == ("macro_data_unavailable",)
    assert effective_at is None
    assert freshness == "point_in_time_unavailable"
