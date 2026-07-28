from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingng_platform.integrity.contracts import IntegrityDocument
from tradingng_platform.models import RunIntegrityAssessment


class IntegrityRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def persist_document(
        self,
        run_id: uuid.UUID,
        document: IntegrityDocument,
        *,
        artifact_id: uuid.UUID | None,
        audit_mode: str,
    ) -> RunIntegrityAssessment:
        existing = await self.session.scalar(
            select(RunIntegrityAssessment).where(
                RunIntegrityAssessment.run_id == run_id,
                RunIntegrityAssessment.policy_version == document.policy_version,
                RunIntegrityAssessment.input_fingerprint == document.input_fingerprint,
            )
        )
        if existing is not None:
            return existing
        row = RunIntegrityAssessment(
            run_id=run_id,
            artifact_id=artifact_id,
            policy_version=document.policy_version,
            status=document.status.value,
            audit_mode=audit_mode,
            temporal_scope=document.temporal_scope,
            analysis_date=document.analysis_date,
            checked_at=document.checked_at,
            reason_codes_json=list(document.reason_codes),
            tool_findings_json=[
                finding.model_dump(mode="json") for finding in document.findings
            ],
            input_fingerprint=document.input_fingerprint,
        )
        self.session.add(row)
        await self.session.flush()
        return row
