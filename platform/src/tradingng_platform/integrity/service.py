from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.assessments.contracts import RunView
from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.assessments.service import _required_products
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.integrity.contracts import (
    CURRENT_POLICY_VERSION,
    IntegrityFindingView,
    IntegritySummaryView,
    IntegrityView,
)
from tradingng_platform.integrity.repository import IntegrityRepository
from tradingng_platform.models import AssessmentRun


class IntegrityNotFound(Exception):
    def __init__(self, run_id: uuid.UUID):
        self.run_id = run_id
        super().__init__(f"assessment not found: {run_id}")


class CleanReassessmentNotAllowed(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class IntegrityService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], stocklean_client=None):
        self.sessions = sessions
        self.stocklean_client = stocklean_client

    async def get(self, principal: Principal, run_id: uuid.UUID) -> IntegrityView:
        principal.require("assessments:read")
        async with self.sessions() as session:
            assessments = AssessmentRepository(session)
            context = await assessments.get_run_context(run_id)
            if context is None:
                raise IntegrityNotFound(run_id)
            integrity = await IntegrityRepository(session).latest_for_run(run_id)
            clean = await assessments.find_clean_reassessment(run_id)
            if integrity is None:
                return IntegrityView(
                    run_id=run_id,
                    status="unassessed",
                    analysis_date=context.request.analysis_date,
                    reason_codes=("integrity_not_assessed",),
                    clean_reassessment_of_run_id=(context.run.clean_reassessment_of_run_id),
                    clean_reassessment_run_id=clean.id if clean is not None else None,
                )
            return IntegrityView(
                run_id=run_id,
                policy_version=integrity.policy_version,
                status=integrity.status,
                audit_mode=integrity.audit_mode,
                temporal_scope=integrity.temporal_scope,
                analysis_date=integrity.analysis_date,
                checked_at=integrity.checked_at,
                reason_codes=tuple(integrity.reason_codes_json or ()),
                findings=tuple(
                    IntegrityFindingView.model_validate(item)
                    for item in (integrity.tool_findings_json or ())
                ),
                input_fingerprint=integrity.input_fingerprint,
                clean_reassessment_of_run_id=context.run.clean_reassessment_of_run_id,
                clean_reassessment_run_id=clean.id if clean is not None else None,
            )

    async def summary(self, principal: Principal) -> IntegritySummaryView:
        principal.require("assessments:read")
        async with self.sessions() as session:
            latest = IntegrityRepository.latest_supported_subquery()
            rows = (
                await session.execute(
                    select(latest.c.status, func.count(AssessmentRun.id))
                    .select_from(AssessmentRun)
                    .outerjoin(latest, latest.c.run_id == AssessmentRun.id)
                    .where(AssessmentRun.status == "succeeded")
                    .group_by(latest.c.status)
                )
            ).all()
        counts = {status or "unassessed": int(count) for status, count in rows}
        safe = counts.get("safe", 0)
        at_risk = counts.get("at_risk", 0)
        unknown = counts.get("unknown", 0)
        unassessed = counts.get("unassessed", 0)
        return IntegritySummaryView(
            total=safe + at_risk + unknown + unassessed,
            safe=safe,
            at_risk=at_risk,
            unknown=unknown,
            unassessed=unassessed,
            eligible_count=safe,
            excluded_at_risk_count=at_risk,
            excluded_unknown_count=unknown,
        )

    async def clean_reassess(
        self,
        principal: Principal,
        run_id: uuid.UUID,
        request_id: str,
    ) -> RunView:
        principal.require("assessments:admin", "assessments:submit")
        if "Admin" not in principal.roles:
            raise PermissionError("clean reassessment requires the Admin role")
        async with self.sessions() as session, session.begin():
            assessments = AssessmentRepository(session)
            context = await assessments.get_run_context(run_id, for_update=True)
            if context is None:
                raise IntegrityNotFound(run_id)
            if context.run.status != "succeeded":
                raise CleanReassessmentNotAllowed("source_run_not_succeeded")
            existing = await assessments.find_clean_reassessment(run_id)
            if existing is not None:
                return existing
            integrity = await IntegrityRepository(session).latest_for_run(run_id)
            if integrity is None:
                raise CleanReassessmentNotAllowed("source_run_unassessed")
            if integrity.status not in {"at_risk", "unknown"}:
                raise CleanReassessmentNotAllowed("source_run_is_safe")
            defaults = dict(context.batch.defaults_json or {})
            request_config = dict(context.request.requested_config_json or {})
            request_config.pop("data_manifest", None)
            request_config["memory_mode"] = "independent"
            initial_status = RunStatus.QUEUED
            data_requirement = None
            if self.stocklean_client is not None:
                analysts = tuple(
                    defaults.get("analysts") or request_config.get("analysts") or ("market",)
                )
                products = _required_products(analysts)
                raw_key = f"clean:{context.run.id}:{CURRENT_POLICY_VERSION}"
                external_key = "tradingng:" + hashlib.sha256(raw_key.encode()).hexdigest()
                subject_hash = hashlib.sha256(
                    f"{principal.issuer}\0{principal.subject}".encode()
                ).hexdigest()
                response = await self.stocklean_client.resolve_candidates(
                    subject_ref=f"principal:{subject_hash}",
                    items=[
                        {
                            "external_request_key": external_key,
                            "symbol": context.instrument.canonical_ticker,
                            "analysis_date": context.request.analysis_date,
                            "analysts": list(analysts),
                            "required_products": list(products),
                        }
                    ],
                )
                if len(response.items) != 1:
                    raise CleanReassessmentNotAllowed("stocklean_incomplete_response")
                admission = response.items[0]
                if admission.readiness == "rejected":
                    code = admission.error.code if admission.error else "stocklean_rejected"
                    raise CleanReassessmentNotAllowed(code)
                if admission.readiness == "ready":
                    if admission.manifest is None:
                        raise CleanReassessmentNotAllowed("manifest_reference_missing")
                    request_config["data_manifest"] = admission.manifest.model_dump(mode="json")
                else:
                    if admission.candidate_request_id is None or admission.job is None:
                        raise CleanReassessmentNotAllowed("progress_reference_missing")
                    initial_status = RunStatus.WAITING_FOR_DATA
                    data_requirement = {
                        "provider_request_id": str(admission.candidate_request_id),
                        "external_request_key": admission.external_request_key,
                        "required_products": list(admission.required_products),
                        "progress": admission.job.model_dump(mode="json"),
                    }
            clean = await assessments.create_clean_reassessment(
                context,
                CURRENT_POLICY_VERSION,
                request_config=request_config,
                initial_status=initial_status,
                data_requirement=data_requirement,
            )
            await assessments.append_audit(
                principal,
                "assessment.clean_reassessment",
                "assessment_run",
                str(clean.id),
                request_id,
                {
                    "clean_reassessment_of_run_id": str(run_id),
                    "integrity_status": integrity.status,
                    "integrity_policy_version": integrity.policy_version,
                },
            )
            return clean
