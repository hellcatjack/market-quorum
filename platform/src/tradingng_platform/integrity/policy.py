from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone

from tradingng_platform.integrity.contracts import (
    CURRENT_POLICY_VERSION,
    IntegrityDocument,
    IntegrityFinding,
    IntegrityStatus,
)

_STATUS_PRIORITY = {
    IntegrityStatus.SAFE: 0,
    IntegrityStatus.UNKNOWN: 1,
    IntegrityStatus.AT_RISK: 2,
}
_DATE_BOUNDED_TOOLS = frozenset(
    {
        "get_stock_data",
        "get_verified_market_snapshot",
        "get_indicators",
        "get_news",
        "get_global_news",
    }
)
_FINANCIAL_STATEMENT_TOOLS = frozenset(
    {"get_balance_sheet", "get_cashflow", "get_income_statement"}
)
_CURRENT_SNAPSHOT_TOOLS = frozenset(
    {"get_fundamentals", "get_insider_transactions", "get_prediction_markets"}
)


class PointInTimeRecorder:
    def __init__(self, analysis_date: date, *, now: datetime | None = None):
        resolved_now = now or datetime.now(timezone.utc)
        if resolved_now.tzinfo is None:
            resolved_now = resolved_now.replace(tzinfo=timezone.utc)
        self.analysis_date = analysis_date
        self.checked_at = resolved_now
        self.temporal_scope = (
            "contemporaneous"
            if analysis_date >= resolved_now.date()
            else "historical_reconstruction"
        )
        self._findings: list[IntegrityFinding] = []

    @property
    def is_historical(self) -> bool:
        return self.temporal_scope == "historical_reconstruction"

    def record(
        self,
        tool_name: str,
        status: IntegrityStatus,
        reason_code: str,
        details: dict | None = None,
    ) -> None:
        self._findings.append(
            IntegrityFinding(
                tool_name=tool_name,
                status=status,
                reason_code=reason_code,
                details=dict(details or {}),
            )
        )

    def finalize(self) -> IntegrityDocument:
        findings = list(self._findings)
        if not findings:
            if self.temporal_scope == "contemporaneous":
                findings.append(
                    IntegrityFinding(
                        tool_name="run",
                        status=IntegrityStatus.SAFE,
                        reason_code="live_current_snapshot",
                    )
                )
            else:
                findings.append(
                    IntegrityFinding(
                        tool_name="run",
                        status=IntegrityStatus.UNKNOWN,
                        reason_code="no_observed_tools",
                    )
                )

        status = max(
            (finding.status for finding in findings),
            key=_STATUS_PRIORITY.__getitem__,
        )
        reason_codes = tuple(dict.fromkeys(finding.reason_code for finding in findings))
        fingerprint_payload = {
            "policy_version": CURRENT_POLICY_VERSION,
            "temporal_scope": self.temporal_scope,
            "analysis_date": self.analysis_date.isoformat(),
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }
        canonical = json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return IntegrityDocument(
            status=status,
            temporal_scope=self.temporal_scope,
            analysis_date=self.analysis_date,
            checked_at=self.checked_at,
            findings=tuple(findings),
            reason_codes=reason_codes,
            input_fingerprint=hashlib.sha256(canonical).hexdigest(),
        )


def record_observed_tool(
    recorder: PointInTimeRecorder | None,
    tool_name: str,
    output,
) -> None:
    if recorder is None or not recorder.is_historical:
        return
    rendered = str(output)
    if tool_name in _DATE_BOUNDED_TOOLS:
        recorder.record(tool_name, IntegrityStatus.SAFE, "date_bounded_route")
    elif tool_name in _FINANCIAL_STATEMENT_TOOLS:
        recorder.record(tool_name, IntegrityStatus.SAFE, "point_in_time_filtered")
    elif tool_name == "get_macro_indicators" and rendered.startswith("POINT_IN_TIME_VINTAGE:"):
        recorder.record(tool_name, IntegrityStatus.SAFE, "fred_vintage_applied")
    elif tool_name in _CURRENT_SNAPSHOT_TOOLS:
        if "POINT_IN_TIME_DATA_UNAVAILABLE:" in rendered:
            recorder.record(tool_name, IntegrityStatus.SAFE, "current_snapshot_blocked")
        else:
            recorder.record(tool_name, IntegrityStatus.AT_RISK, "current_snapshot_exposed")
    else:
        recorder.record(tool_name, IntegrityStatus.UNKNOWN, "unregistered_tool")


def evidence_temporal_metadata(
    tool_name: str,
    analysis_date: date | None,
    output,
) -> tuple[str | None, str | None]:
    if analysis_date is None:
        return None, None
    effective_at = datetime.combine(analysis_date, time.max, timezone.utc).isoformat()
    rendered = str(output)
    if tool_name in _DATE_BOUNDED_TOOLS:
        return effective_at, "point_in_time_bounded"
    if tool_name in _FINANCIAL_STATEMENT_TOOLS:
        return effective_at, "point_in_time_filtered"
    if tool_name == "get_macro_indicators" and rendered.startswith("POINT_IN_TIME_VINTAGE:"):
        return effective_at, "point_in_time_vintage"
    if "POINT_IN_TIME_DATA_UNAVAILABLE:" in rendered:
        return None, "point_in_time_unavailable"
    return None, "current_snapshot" if analysis_date >= datetime.now(timezone.utc).date() else None
