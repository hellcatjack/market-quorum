from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from tradingng_platform.domain.runs import RunStatus, assert_transition
from tradingng_platform.models import (
    AssessmentDataRequirement,
    AssessmentRequest,
    AssessmentRun,
    RunEvent,
)


@dataclass(frozen=True)
class DataRequirementClaim:
    requirement_id: uuid.UUID
    run_id: uuid.UUID
    provider_request_id: str
    version: int
    lease_owner: str


class DataReadinessRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def claim_due(self, worker_id: str, lease_seconds: int) -> DataRequirementClaim | None:
        now = datetime.now(timezone.utc)
        async with self.sessions() as session, session.begin():
            requirement = await session.scalar(
                select(AssessmentDataRequirement)
                .join(AssessmentRun, AssessmentRun.id == AssessmentDataRequirement.run_id)
                .where(
                    AssessmentDataRequirement.status == "waiting",
                    AssessmentRun.status == RunStatus.WAITING_FOR_DATA.value,
                    or_(
                        AssessmentDataRequirement.next_poll_at.is_(None),
                        AssessmentDataRequirement.next_poll_at <= now,
                    ),
                    or_(
                        AssessmentDataRequirement.lease_expires_at.is_(None),
                        AssessmentDataRequirement.lease_expires_at <= now,
                    ),
                )
                .order_by(
                    AssessmentDataRequirement.next_poll_at,
                    AssessmentDataRequirement.created_at,
                    AssessmentDataRequirement.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if requirement is None:
                return None
            requirement.lease_owner = worker_id
            requirement.lease_expires_at = now + timedelta(seconds=lease_seconds)
            requirement.version += 1
            await session.flush()
            return DataRequirementClaim(
                requirement_id=requirement.id,
                run_id=requirement.run_id,
                provider_request_id=requirement.provider_request_id,
                version=requirement.version,
                lease_owner=worker_id,
            )

    async def apply_waiting(self, claim, status, next_poll_at) -> bool:
        async with self.sessions() as session, session.begin():
            requirement = await self._locked(session, claim)
            if requirement is None:
                return False
            progress = status.job.model_dump(mode="json") if status.job else {}
            fingerprint = self._fingerprint(progress)
            changed = fingerprint != requirement.last_progress_fingerprint
            requirement.progress_json = progress
            requirement.last_progress_fingerprint = fingerprint
            self._release(requirement, next_poll_at)
            if changed:
                await self._append_event(
                    session,
                    claim.run_id,
                    "assessment.data_progress",
                    {"progress": progress},
                )
            return True

    async def apply_ready(self, claim, status, manifest) -> bool:
        async with self.sessions() as session, session.begin():
            requirement = await self._locked(session, claim)
            if requirement is None:
                return False
            run = await session.scalar(
                select(AssessmentRun).where(AssessmentRun.id == claim.run_id).with_for_update()
            )
            if run is None or RunStatus(run.status) is not RunStatus.WAITING_FOR_DATA:
                return False
            assert_transition(RunStatus.WAITING_FOR_DATA, RunStatus.QUEUED)
            request = await session.get(AssessmentRequest, run.request_id)
            if request is None:
                return False
            config = dict(request.requested_config_json or {})
            config["data_manifest"] = status.manifest.model_dump(mode="json")
            request.requested_config_json = config
            requirement.status = "ready"
            requirement.progress_json = {"stage": "ready"}
            requirement.manifest_snapshot_id = manifest.snapshot_id
            requirement.manifest_sha256 = manifest.manifest_sha256
            requirement.lease_owner = None
            requirement.lease_expires_at = None
            requirement.next_poll_at = None
            requirement.version += 1
            run.status = RunStatus.QUEUED.value
            run.version += 1
            await self._append_event(
                session,
                run.id,
                "assessment.data_ready",
                {
                    "snapshot_id": manifest.snapshot_id,
                    "manifest_sha256": manifest.manifest_sha256,
                },
            )
            await self._append_event(session, run.id, "assessment.queued", {})
            return True

    async def apply_rejected(self, claim, status) -> bool:
        error = status.error
        return await self._finish(
            claim,
            run_status=RunStatus.FAILED,
            requirement_status="failed",
            code=error.code if error else "data_preparation_failed",
            summary=error.message if error else "StockLean data preparation failed",
        )

    async def apply_attention(self, claim, code: str) -> bool:
        return await self._finish(
            claim,
            run_status=RunStatus.NEEDS_ATTENTION,
            requirement_status="needs_attention",
            code=code,
            summary="StockLean manifest integrity verification failed",
        )

    async def release_transient(self, claim, code: str, next_poll_at) -> bool:
        async with self.sessions() as session, session.begin():
            requirement = await self._locked(session, claim)
            if requirement is None:
                return False
            progress = dict(requirement.progress_json or {})
            progress["transient_error"] = code
            requirement.progress_json = progress
            self._release(requirement, next_poll_at)
            return True

    async def _finish(
        self,
        claim,
        *,
        run_status: RunStatus,
        requirement_status: str,
        code: str,
        summary: str,
    ) -> bool:
        async with self.sessions() as session, session.begin():
            requirement = await self._locked(session, claim)
            if requirement is None:
                return False
            run = await session.scalar(
                select(AssessmentRun).where(AssessmentRun.id == claim.run_id).with_for_update()
            )
            if run is None or RunStatus(run.status) is not RunStatus.WAITING_FOR_DATA:
                return False
            assert_transition(RunStatus.WAITING_FOR_DATA, run_status)
            requirement.status = requirement_status
            requirement.progress_json = {"stage": requirement_status, "error_code": code}
            requirement.lease_owner = None
            requirement.lease_expires_at = None
            requirement.next_poll_at = None
            requirement.version += 1
            run.status = run_status.value
            run.error_code = code
            run.error_summary = summary[:500]
            run.finished_at = datetime.now(timezone.utc)
            run.version += 1
            await self._append_event(
                session,
                run.id,
                f"assessment.{requirement_status}",
                {"error_code": code},
            )
            return True

    @staticmethod
    async def _locked(session, claim):
        return await session.scalar(
            select(AssessmentDataRequirement)
            .where(
                AssessmentDataRequirement.id == claim.requirement_id,
                AssessmentDataRequirement.version == claim.version,
                AssessmentDataRequirement.lease_owner == claim.lease_owner,
            )
            .with_for_update()
        )

    @staticmethod
    def _release(requirement, next_poll_at) -> None:
        requirement.next_poll_at = next_poll_at
        requirement.lease_owner = None
        requirement.lease_expires_at = None
        requirement.version += 1

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    async def _append_event(session, run_id, event_type, payload) -> None:
        sequence = await session.scalar(
            select(func.coalesce(func.max(RunEvent.sequence), 0) + 1).where(
                RunEvent.run_id == run_id
            )
        )
        session.add(
            RunEvent(
                run_id=run_id,
                sequence=int(sequence or 1),
                event_type=event_type,
                payload_json=payload,
            )
        )
        await session.flush()
