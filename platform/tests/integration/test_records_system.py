from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.auth.principal import Principal
from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.models import (
    Artifact,
    AssessmentRun,
    AuditEvent,
    Decision,
    EvidenceItem,
    RunConfigSnapshot,
    Worker,
)
from tradingng_platform.records.service import ArtifactIntegrityError, RecordService
from tradingng_platform.scheduler.policy import SystemSnapshot
from tradingng_platform.system.contracts import SchedulerPolicyCommand
from tradingng_platform.system.service import SystemService


class _Gateway:
    async def status(self):
        return GatewaySnapshot(
            status="ok",
            active_completions=0,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            snapshot_id="a" * 64,
            latency_ms=1,
        )


class _Probe:
    def sample(self):
        return SystemSnapshot(20, 32, 100, 50, False)


def _admin():
    return Principal(
        "issuer",
        "records-admin",
        "user",
        frozenset(
            {
                "assessments:submit",
                "assessments:read",
                "assessments:review",
                "artifacts:read",
                "system:read",
                "assessments:admin",
            }
        ),
        display_name="Records Admin",
        roles=frozenset({"Admin"}),
    )


async def test_records_are_hash_verified_and_collaboration_is_audited(
    session_factory,
    instrument_classifier,
    tmp_path,
):
    principal = _admin()
    run = (
        await AssessmentService(session_factory, instrument_classifier).submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25))],
                idempotency_key="records-20260725",
            ),
            "request-submit",
        )
    )[0]
    store = LocalArtifactStore(tmp_path / "artifacts")
    source = tmp_path / "report.md"
    source.write_text("verified report", encoding="utf-8")
    stored = store.put(run.id, "report_1_complete", "text/markdown", source)
    async with session_factory() as session, session.begin():
        snapshot = RunConfigSnapshot(
            content_json={
                "gateway": {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
            },
            sha256="c" * 64,
            gateway_snapshot_id="snapshot-records",
        )
        session.add(snapshot)
        await session.flush()
        persisted_run = await session.get(AssessmentRun, run.id)
        persisted_run.config_snapshot_id = snapshot.id
        artifact = Artifact(
            run_id=run.id,
            kind=stored.kind,
            media_type=stored.media_type,
            size=stored.size,
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            redacted=True,
        )
        session.add(artifact)
        session.add(
            Decision(
                run_id=run.id,
                rating="Hold",
                executive_summary="Wait",
                investment_thesis="Balanced",
                price_target=Decimal("100"),
                time_horizon="5 days",
                structured_json={},
            )
        )
        session.add(
            EvidenceItem(
                run_id=run.id,
                source="yfinance",
                tool_name="get_stock_data",
                arguments_json={"ticker": "NVDA"},
                collected_at=datetime.now(timezone.utc),
                effective_at=None,
                freshness="fresh",
                artifact_id=artifact.id,
                content_hash="b" * 64,
            )
        )
        await session.flush()
        artifact_id = artifact.id

    service = RecordService(session_factory, store)
    assert (await service.decision(principal, run.id)).rating == "Hold"
    assert (await service.evidence(principal, run.id))[0].source == "yfinance"
    public_artifact = (await service.list_artifacts(principal, run.id))[0]
    assert not hasattr(public_artifact, "storage_key")
    opened = await service.open_artifact(principal, artifact_id)
    assert opened.path.read_text(encoding="utf-8") == "verified report"
    review = await service.add_review(principal, run.id, "approved", "Reviewed", "request-review")
    comment = await service.add_comment(principal, run.id, "Watch valuation", "request-comment")
    assert review.reviewer == "Records Admin"
    assert comment.author == "Records Admin"
    assert len(await service.list_reviews(principal, run.id)) == 1
    assert len(await service.list_comments(principal, run.id)) == 1
    assert (await service.instrument_summary(principal, "nvda")).assessment_count == 1
    history = await service.instrument_history(principal, "NVDA")
    assert len(history) == 1
    assert history[0].executive_summary == "Wait"
    assert history[0].price_target == Decimal("100")
    assert history[0].gateway_model == "gpt-5.6-sol"
    assert history[0].gateway_reasoning_effort == "xhigh"
    assert history[0].config_snapshot_sha256 == "c" * 64
    assert history[0].validation_outcome is None

    opened.path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        await service.open_artifact(principal, artifact_id)


async def test_system_capacity_and_policy_update_are_bounded_and_audited(
    session_factory,
    instrument_classifier,
):
    principal = _admin()
    await AssessmentService(session_factory, instrument_classifier).submit(
        principal,
        SubmitAssessments(
            items=[AssessmentItem(ticker="TSLA", analysis_date=date(2026, 7, 25))],
            idempotency_key="system-20260725",
        ),
        "request-submit",
    )
    service = SystemService(session_factory, _Gateway(), _Probe())

    capacity = await service.capacity(principal)
    assert capacity.queued == 1
    assert capacity.max_running_total == 2
    updated = await service.update_scheduler_policy(
        principal,
        SchedulerPolicyCommand(
            max_running_total=32,
            hard_max_running_total=32,
            gateway_active_limit=32,
            cpu_limit_percent=85,
            minimum_memory_gib=8,
            minimum_disk_gib=10,
            minimum_disk_percent=10,
        ),
        "request-policy",
    )
    assert updated.max_running_total == 32
    assert updated.hard_max_running_total == 32
    assert updated.version == 2
    async with session_factory() as session:
        actions = tuple(await session.scalars(select(AuditEvent.action)))
    assert "scheduler.policy.update" in actions


async def test_system_status_only_lists_workers_with_recent_heartbeats(session_factory):
    now = datetime.now(timezone.utc)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                Worker(
                    instance_name="host:1",
                    status="idle",
                    capabilities_json={"runner": "tradingagents"},
                    pid=1001,
                    heartbeat_at=now,
                ),
                Worker(
                    instance_name="host:old-pid",
                    status="idle",
                    capabilities_json={"runner": "tradingagents"},
                    pid=999,
                    heartbeat_at=now - timedelta(seconds=31),
                ),
            ]
        )

    status = await SystemService(session_factory, _Gateway(), _Probe()).status(_admin())

    assert [worker["instance_name"] for worker in status["workers"]] == ["host:1"]
