from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

CURRENT_POLICY_VERSION = "point-in-time.v1"


class IntegrityStatus(str, Enum):
    SAFE = "safe"
    AT_RISK = "at_risk"
    UNKNOWN = "unknown"


class IntegrityFinding(BaseModel):
    tool_name: str = Field(min_length=1, max_length=128)
    status: IntegrityStatus
    reason_code: str = Field(min_length=1, max_length=64)
    details: dict = Field(default_factory=dict)


class IntegrityDocument(BaseModel):
    policy_version: Literal["point-in-time.v1"] = CURRENT_POLICY_VERSION
    status: IntegrityStatus
    temporal_scope: Literal["contemporaneous", "historical_reconstruction"]
    analysis_date: date
    checked_at: datetime
    findings: tuple[IntegrityFinding, ...]
    reason_codes: tuple[str, ...]
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class IntegrityFindingView(BaseModel):
    tool_name: str
    status: Literal["safe", "at_risk", "unknown"]
    reason_code: str
    details: dict = Field(default_factory=dict)


class IntegrityView(BaseModel):
    run_id: uuid.UUID
    policy_version: str = CURRENT_POLICY_VERSION
    status: Literal["safe", "at_risk", "unknown", "unassessed"]
    audit_mode: Literal["live", "retrospective"] | None = None
    temporal_scope: Literal["contemporaneous", "historical_reconstruction"] | None = None
    analysis_date: date
    checked_at: datetime | None = None
    reason_codes: tuple[str, ...] = ()
    findings: tuple[IntegrityFindingView, ...] = ()
    input_fingerprint: str | None = None
    clean_reassessment_of_run_id: uuid.UUID | None = None
    clean_reassessment_run_id: uuid.UUID | None = None


class IntegritySummaryView(BaseModel):
    policy_version: str = CURRENT_POLICY_VERSION
    total: int
    safe: int
    at_risk: int
    unknown: int
    unassessed: int
    eligible_count: int
    excluded_at_risk_count: int
    excluded_unknown_count: int
