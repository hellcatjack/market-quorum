from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.auth.principal import Principal
from tradingng_platform.models import Artifact, AuditEvent
from tradingng_platform.retention.service import RetentionService


async def test_retention_dry_run_and_audited_tombstone(
    session_factory,
    instrument_classifier,
    tmp_path,
):
    principal = Principal(
        "issuer",
        "retention-owner",
        "user",
        frozenset({"assessments:submit"}),
    )
    run = (
        await AssessmentService(session_factory, instrument_classifier).submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 1, 1))],
                idempotency_key="retention-integration-20260725",  # gitleaks:allow
            ),
            "retention-submit",
        )
    )[0]
    store = LocalArtifactStore(tmp_path / "artifacts")
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    specifications = [
        ("raw", "raw_180d", 181, {}),
        ("diagnostic", "diagnostic_90d", 91, {}),
        ("decision", "permanent", 1000, {}),
        ("held", "raw_180d", 1000, {"legal_hold": True}),
    ]
    paths = {}
    async with session_factory() as session, session.begin():
        for kind, retention_class, age, metadata in specifications:
            source = tmp_path / f"{kind}.txt"
            source.write_text(kind, encoding="utf-8")
            stored = store.put(run.id, kind, "text/plain", source)
            paths[kind] = stored.path
            session.add(
                Artifact(
                    run_id=run.id,
                    kind=kind,
                    media_type="text/plain",
                    size=stored.size,
                    sha256=stored.sha256,
                    storage_key=stored.storage_key,
                    redacted=True,
                    retention_class=retention_class,
                    metadata_json=metadata,
                    created_at=now - timedelta(days=age),
                )
            )

    service = RetentionService(session_factory, store)
    due = await service.run(now=now)
    assert len(due) == 2
    assert all(path.is_file() for path in paths.values())

    deleted = await service.run(apply=True, now=now)
    assert deleted == due
    assert not paths["raw"].exists()
    assert not paths["diagnostic"].exists()
    assert paths["decision"].is_file()
    assert paths["held"].is_file()
    async with session_factory() as session:
        actions = list(
            await session.scalars(
                select(AuditEvent.action).where(AuditEvent.action == "artifact.retained_delete")
            )
        )
    assert len(actions) == 2
