from __future__ import annotations

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
