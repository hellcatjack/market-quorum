from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingng_platform.integrity.contracts import CURRENT_POLICY_VERSION, IntegrityDocument
from tradingng_platform.models import RunIntegrityAssessment


class IntegrityRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def latest_supported_subquery():
        ranked = (
            select(
                RunIntegrityAssessment.id.label("integrity_id"),
                RunIntegrityAssessment.run_id,
                RunIntegrityAssessment.status,
                RunIntegrityAssessment.audit_mode,
                RunIntegrityAssessment.temporal_scope,
                RunIntegrityAssessment.checked_at,
                RunIntegrityAssessment.input_fingerprint,
                func.row_number()
                .over(
                    partition_by=RunIntegrityAssessment.run_id,
                    order_by=(
                        RunIntegrityAssessment.checked_at.desc(),
                        RunIntegrityAssessment.created_at.desc(),
                        RunIntegrityAssessment.id.desc(),
                    ),
                )
                .label("integrity_rank"),
            )
            .where(RunIntegrityAssessment.policy_version == CURRENT_POLICY_VERSION)
            .subquery("ranked_run_integrity")
        )
        return (
            select(
                ranked.c.integrity_id,
                ranked.c.run_id,
                ranked.c.status,
                ranked.c.audit_mode,
                ranked.c.temporal_scope,
                ranked.c.checked_at,
                ranked.c.input_fingerprint,
            )
            .where(ranked.c.integrity_rank == 1)
            .subquery("latest_run_integrity")
        )

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

    async def find_document(
        self,
        run_id: uuid.UUID,
        document: IntegrityDocument,
    ) -> RunIntegrityAssessment | None:
        return await self.session.scalar(
            select(RunIntegrityAssessment).where(
                RunIntegrityAssessment.run_id == run_id,
                RunIntegrityAssessment.policy_version == document.policy_version,
                RunIntegrityAssessment.input_fingerprint == document.input_fingerprint,
            )
        )

    async def latest_for_run(
        self,
        run_id: uuid.UUID,
    ) -> RunIntegrityAssessment | None:
        latest = self.latest_supported_subquery()
        return await self.session.scalar(
            select(RunIntegrityAssessment)
            .join(latest, latest.c.integrity_id == RunIntegrityAssessment.id)
            .where(latest.c.run_id == run_id)
        )
