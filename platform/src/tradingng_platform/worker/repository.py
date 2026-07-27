import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.domain.runs import RunStatus, assert_transition
from tradingng_platform.models import (
    Artifact,
    AssessmentRequest,
    AssessmentRun,
    Decision,
    EvidenceItem,
    Instrument,
    RunConfigSnapshot,
    RunEvent,
    RunStep,
    Worker,
    WorkerLease,
)
from tradingng_platform.persistence.locks import acquire_transaction_lock
from tradingng_platform.persistence.upsert import session_dialect, upsert
from tradingng_platform.runner.contracts import RunnerEvent
from tradingng_platform.validation.repository import schedule_validations
from tradingng_platform.validation.service import SYSTEM_VALIDATION_PRINCIPAL
from tradingng_platform.worker.process import ProcessIdentity


@dataclass(frozen=True)
class ClaimedRun:
    run_id: uuid.UUID
    ticker: str
    asset_type: str
    analysis_date: date
    snapshot: dict


class WorkerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def register_worker(
        self,
        instance_name: str,
        pid: int,
        capabilities: dict | None = None,
    ) -> Worker:
        now = datetime.now(timezone.utc)
        values = {
            "instance_name": instance_name,
            "status": "idle",
            "capabilities_json": capabilities or {},
            "pid": pid,
            "heartbeat_at": now,
        }
        await self.session.execute(
            upsert(
                session_dialect(self.session),
                Worker,
                values,
                [Worker.instance_name],
                {
                    "status": "idle",
                    "capabilities_json": capabilities or {},
                    "pid": pid,
                    "heartbeat_at": now,
                },
            )
        )
        worker = await self.session.scalar(
            select(Worker).where(Worker.instance_name == instance_name)
        )
        if worker is None:
            raise RuntimeError("worker registration is not visible")
        return worker

    async def claim(self, worker_id: uuid.UUID) -> ClaimedRun | None:
        run_id = await self.session.scalar(
            select(AssessmentRun.id)
            .where(AssessmentRun.status == RunStatus.ADMITTED.value)
            .order_by(AssessmentRun.admitted_at, AssessmentRun.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if run_id is None:
            return None
        row = (
            await self.session.execute(
                select(
                    AssessmentRun,
                    AssessmentRequest,
                    Instrument,
                    RunConfigSnapshot,
                )
                .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .join(
                    RunConfigSnapshot,
                    AssessmentRun.config_snapshot_id == RunConfigSnapshot.id,
                )
                .where(AssessmentRun.id == run_id)
            )
        ).one()
        run, request, instrument, snapshot = row
        assert_transition(RunStatus(run.status), RunStatus.STARTING)
        now = datetime.now(timezone.utc)
        run.status = RunStatus.STARTING.value
        run.started_at = run.started_at or now
        run.version += 1
        self.session.add(
            WorkerLease(
                run_id=run.id,
                worker_id=worker_id,
                child_pid=None,
                child_pgid=None,
                lease_expires_at=now + timedelta(seconds=60),
                heartbeat_at=now,
            )
        )
        await self.session.execute(
            update(Worker).where(Worker.id == worker_id).values(status="busy", heartbeat_at=now)
        )
        await AssessmentRepository(self.session).append_event(run.id, "assessment.starting", {})
        return ClaimedRun(
            run_id=run.id,
            ticker=instrument.canonical_ticker,
            asset_type=instrument.asset_type,
            analysis_date=request.analysis_date,
            snapshot=dict(snapshot.content_json),
        )

    async def set_child_identity(
        self,
        run_id: uuid.UUID,
        identity: ProcessIdentity,
    ) -> None:
        lease = await self.session.scalar(
            select(WorkerLease).where(WorkerLease.run_id == run_id).with_for_update()
        )
        if lease is None:
            raise RuntimeError("worker lease disappeared before child launch")
        lease.child_pid = identity.pid
        lease.child_pgid = identity.pgid
        lease.heartbeat_at = datetime.now(timezone.utc)

    async def heartbeat(self, worker_id: uuid.UUID, run_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(Worker).where(Worker.id == worker_id).values(heartbeat_at=now)
        )
        result = await self.session.execute(
            update(WorkerLease)
            .where(WorkerLease.run_id == run_id, WorkerLease.worker_id == worker_id)
            .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=60))
        )
        if result.rowcount != 1:
            raise RuntimeError("worker lease heartbeat target is missing")

    async def heartbeat_idle(self, worker_id: uuid.UUID) -> None:
        result = await self.session.execute(
            update(Worker)
            .where(Worker.id == worker_id)
            .values(status="idle", heartbeat_at=datetime.now(timezone.utc))
        )
        if result.rowcount != 1:
            raise RuntimeError("idle worker heartbeat target is missing")

    async def is_cancel_requested(self, run_id: uuid.UUID) -> bool:
        status = await self.session.scalar(
            select(AssessmentRun.status).where(AssessmentRun.id == run_id)
        )
        return status in {RunStatus.CANCEL_REQUESTED.value, RunStatus.CANCELLING.value}

    async def persist_runner_event(self, run_id: uuid.UUID, event: RunnerEvent) -> RunStatus:
        run = await self.session.scalar(
            select(AssessmentRun).where(AssessmentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise RuntimeError("runner event references an unknown run")
        last_payload = await self.session.scalar(
            select(RunEvent.payload_json)
            .where(
                RunEvent.run_id == run_id,
                RunEvent.event_type.like("runner.%"),
            )
            .order_by(RunEvent.sequence.desc())
            .limit(1)
        )
        expected = int((last_payload or {}).get("runner_sequence", 0)) + 1
        if event.sequence != expected:
            raise ValueError(
                f"expected durable runner sequence {expected}, received {event.sequence}"
            )

        payload = {**event.payload, "runner_sequence": event.sequence}
        await AssessmentRepository(self.session).append_event(
            run_id,
            f"runner.{event.type}.{event.name}",
            payload,
        )
        current = RunStatus(run.status)
        if event.type == "stage":
            target = RunStatus(event.payload.get("status", event.name))
            if current not in {RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLING}:
                if target != current:
                    assert_transition(current, target)
                    await self._finish_running_steps(run, "completed")
                    run.status = target.value
                    run.version += 1
                    current = target
                await self._upsert_step(run, event, current)
        elif event.type == "result":
            if current not in {RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLING}:
                assert_transition(current, RunStatus.FINALIZING)
                await self._finish_running_steps(run, "completed")
                run.status = RunStatus.FINALIZING.value
                run.version += 1
                current = RunStatus.FINALIZING
        return current

    async def _finish_running_steps(
        self,
        run: AssessmentRun,
        status: str,
        *,
        error_code: str | None = None,
    ) -> None:
        await self.session.execute(
            update(RunStep)
            .where(
                RunStep.run_id == run.id,
                RunStep.attempt == run.attempt,
                RunStep.status == "running",
            )
            .values(
                status=status,
                finished_at=datetime.now(timezone.utc),
                error_code=error_code,
            )
        )

    async def _upsert_step(
        self,
        run: AssessmentRun,
        event: RunnerEvent,
        status: RunStatus,
    ) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            upsert(
                session_dialect(self.session),
                RunStep,
                {
                    "run_id": run.id,
                    "name": event.name,
                    "status": "running",
                    "attempt": run.attempt,
                    "started_at": now,
                    "finished_at": None,
                    "error_code": None,
                    "summary": event.payload.get("progress_key"),
                },
                [RunStep.run_id, RunStep.name, RunStep.attempt],
                {
                    "status": "running" if status.value == event.name else "completed",
                    "summary": event.payload.get("progress_key"),
                },
            )
        )

    async def finalize_success(
        self,
        run_id: uuid.UUID,
        work_dir: Path,
        store: LocalArtifactStore,
    ) -> None:
        if not await acquire_transaction_lock(self.session, "global:archive"):
            raise RuntimeError("archive coordination lock is unavailable")
        run = await self.session.scalar(
            select(AssessmentRun).where(AssessmentRun.id == run_id).with_for_update()
        )
        if run is None or RunStatus(run.status) != RunStatus.FINALIZING:
            raise RuntimeError("successful runner is not ready for finalization")

        await self._finish_running_steps(run, "completed")

        artifact_specs = self._artifact_specs(work_dir)
        manifest = []
        evidence_artifact_id = None
        for kind, media_type, path in artifact_specs:
            stored = store.put(run_id, kind, media_type, path)
            artifact = Artifact(
                run_id=run_id,
                kind=kind,
                media_type=media_type,
                size=stored.size,
                sha256=stored.sha256,
                storage_key=stored.storage_key,
                redacted=True,
            )
            self.session.add(artifact)
            await self.session.flush()
            if kind == "evidence":
                evidence_artifact_id = artifact.id
            manifest.append(
                {
                    "kind": kind,
                    "media_type": media_type,
                    "size": stored.size,
                    "sha256": stored.sha256,
                    "storage_key": stored.storage_key,
                }
            )

        manifest_path = work_dir / "manifest.json"
        _atomic_json(manifest_path, {"run_id": str(run_id), "artifacts": manifest})
        stored_manifest = store.put(run_id, "manifest", "application/json", manifest_path)
        self.session.add(
            Artifact(
                run_id=run_id,
                kind="manifest",
                media_type="application/json",
                size=stored_manifest.size,
                sha256=stored_manifest.sha256,
                storage_key=stored_manifest.storage_key,
                redacted=True,
            )
        )

        decision_data = json.loads((work_dir / "decision.json").read_text(encoding="utf-8"))
        self.session.add(
            Decision(
                run_id=run_id,
                rating=decision_data["rating"],
                executive_summary=decision_data["executive_summary"],
                investment_thesis=decision_data["investment_thesis"],
                price_target=(
                    Decimal(decision_data["price_target"])
                    if decision_data.get("price_target") is not None
                    else None
                ),
                time_horizon=decision_data.get("time_horizon"),
                structured_json=decision_data,
            )
        )
        await self._persist_evidence(run_id, work_dir, evidence_artifact_id)
        assert_transition(RunStatus.FINALIZING, RunStatus.SUCCEEDED)
        run.status = RunStatus.SUCCEEDED.value
        run.finished_at = datetime.now(timezone.utc)
        run.version += 1
        await AssessmentRepository(self.session).append_event(
            run_id,
            "assessment.succeeded",
            {"artifact_count": len(manifest) + 1},
        )
        await schedule_validations(
            self.session,
            run_id,
            (1, 5, 20),
            SYSTEM_VALIDATION_PRINCIPAL,
            f"validation-terminal-{run_id}",
        )
        await self._release_lease(run_id)

    async def _persist_evidence(
        self,
        run_id: uuid.UUID,
        work_dir: Path,
        artifact_id: uuid.UUID | None,
    ) -> None:
        evidence_path = work_dir / "working" / "evidence.jsonl"
        if not evidence_path.is_file():
            return
        for line in evidence_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            collected_at = datetime.fromisoformat(item["collected_at"].replace("Z", "+00:00"))
            self.session.add(
                EvidenceItem(
                    run_id=run_id,
                    source=item["source"],
                    tool_name=item["tool_name"],
                    arguments_json=item["arguments"],
                    collected_at=collected_at,
                    effective_at=None,
                    freshness=None,
                    artifact_id=artifact_id,
                    content_hash=item["output_sha256"],
                )
            )

    async def finalize_failure(
        self,
        run_id: uuid.UUID,
        error_code: str,
        error_summary: str,
    ) -> None:
        run = await self.session.scalar(
            select(AssessmentRun).where(AssessmentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise RuntimeError("failed runner references an unknown run")
        current = RunStatus(run.status)
        if current in {RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLING}:
            await self.finalize_cancelled(run_id)
            return
        assert_transition(current, RunStatus.FAILED)
        await self._finish_running_steps(run, "failed", error_code=error_code)
        run.status = RunStatus.FAILED.value
        run.error_code = error_code
        run.error_summary = error_summary[:2000]
        run.finished_at = datetime.now(timezone.utc)
        run.version += 1
        await AssessmentRepository(self.session).append_event(
            run_id,
            "assessment.failed",
            {"error_code": error_code},
        )
        await self._release_lease(run_id)

    async def finalize_cancelled(self, run_id: uuid.UUID) -> None:
        run = await self.session.scalar(
            select(AssessmentRun).where(AssessmentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise RuntimeError("cancelled runner references an unknown run")
        current = RunStatus(run.status)
        await self._finish_running_steps(run, "cancelled")
        if current == RunStatus.CANCEL_REQUESTED:
            assert_transition(current, RunStatus.CANCELLING)
            run.status = RunStatus.CANCELLING.value
            run.version += 1
            await AssessmentRepository(self.session).append_event(
                run_id, "assessment.cancelling", {}
            )
            current = RunStatus.CANCELLING
        assert_transition(current, RunStatus.CANCELLED)
        run.status = RunStatus.CANCELLED.value
        run.finished_at = datetime.now(timezone.utc)
        run.version += 1
        await AssessmentRepository(self.session).append_event(run_id, "assessment.cancelled", {})
        await self._release_lease(run_id)

    async def _release_lease(self, run_id: uuid.UUID) -> None:
        worker_id = await self.session.scalar(
            select(WorkerLease.worker_id).where(WorkerLease.run_id == run_id)
        )
        await self.session.execute(delete(WorkerLease).where(WorkerLease.run_id == run_id))
        if worker_id is not None:
            await self.session.execute(
                update(Worker).where(Worker.id == worker_id).values(status="idle")
            )

    @staticmethod
    def _artifact_specs(work_dir: Path) -> list[tuple[str, str, Path]]:
        specs = []
        fixed = (
            ("final_state", "application/json", work_dir / "final_state.json"),
            ("decision", "application/json", work_dir / "decision.json"),
            ("run_config", "application/json", work_dir / "run-config.json"),
            (
                "memory_context",
                "application/json",
                work_dir / "working" / "memory_context.json",
            ),
            ("evidence", "application/x-ndjson", work_dir / "working" / "evidence.jsonl"),
            (
                "llm_interactions",
                "application/x-ndjson",
                work_dir / "working" / "llm_interactions.jsonl",
            ),
            (
                "dependency_health",
                "application/x-ndjson",
                work_dir / "working" / "dependency_health.jsonl",
            ),
        )
        specs.extend(item for item in fixed if item[2].is_file())
        reports = work_dir / "reports"
        if reports.is_dir():
            for index, path in enumerate(sorted(reports.rglob("*")), start=1):
                if path.is_file():
                    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)[:40]
                    specs.append((f"report_{index}_{safe_stem}", "text/markdown", path))
        return specs

    async def recover_stale_leases(self) -> tuple[int, int]:
        now = datetime.now(timezone.utc)
        leases = list(
            await self.session.scalars(
                select(WorkerLease)
                .where(WorkerLease.lease_expires_at < now)
                .with_for_update(skip_locked=True)
            )
        )
        reclaimed = 0
        attention = 0
        for lease in leases:
            run = await self.session.get(AssessmentRun, lease.run_id)
            if run is None:
                await self.session.delete(lease)
                continue
            if lease.child_pid is not None and Path(f"/proc/{lease.child_pid}").exists():
                run.status = RunStatus.NEEDS_ATTENTION.value
                run.error_code = "stale_lease_process_alive"
                attention += 1
            else:
                run.status = RunStatus.ADMITTED.value
                run.attempt += 1
                await self.session.delete(lease)
                reclaimed += 1
            run.version += 1
            await AssessmentRepository(self.session).append_event(
                run.id,
                "assessment.recovery",
                {
                    "reclaimed": lease.child_pid is None
                    or not Path(f"/proc/{lease.child_pid}").exists()
                },
            )
        return reclaimed, attention


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
