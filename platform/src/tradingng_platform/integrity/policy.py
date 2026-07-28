from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

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
