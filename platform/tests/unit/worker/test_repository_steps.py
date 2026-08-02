import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.integrity.policy import PointInTimeRecorder
from tradingng_platform.models import (
    Artifact,
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    Base,
    EvidenceItem,
    Instrument,
    RunEvent,
    RunIntegrityAssessment,
    RunStep,
    User,
)
from tradingng_platform.runner.contracts import RunnerEvent
from tradingng_platform.worker import repository as repository_module
from tradingng_platform.worker.repository import WorkerRepository


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _stage(sequence: int, name: str) -> RunnerEvent:
    return RunnerEvent(
        sequence=sequence,
        type="stage",
        name=name,
        payload={"status": name, "progress_key": f"{name}.done"},
        emitted_at=datetime.now(timezone.utc),
    )


def _result(sequence: int) -> RunnerEvent:
    return RunnerEvent(
        sequence=sequence,
        type="result",
        name="assessment.completed",
        payload={"rating": "Hold"},
        emitted_at=datetime.now(timezone.utc),
    )


async def _run(session, status: RunStatus) -> AssessmentRun:
    run = AssessmentRun(
        request_id=uuid.uuid4(),
        status=status.value,
        attempt=1,
        version=1,
    )
    session.add(run)
    await session.flush()
    return run


def _sqlite_upsert(dialect, model, values, conflict_columns, update_values):
    assert dialect == "sqlite"
    return (
        sqlite_insert(model)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[column.key for column in conflict_columns],
            set_=dict(update_values),
        )
    )


async def test_stage_transition_and_result_complete_every_step(monkeypatch):
    monkeypatch.setattr(repository_module, "upsert", _sqlite_upsert)
    engine, sessions = await _database()
    try:
        async with sessions() as session, session.begin():
            run = await _run(session, RunStatus.STARTING)
            repository = WorkerRepository(session)
            stages = (
                "running_analysts",
                "research_debate",
                "trader_plan",
                "risk_debate",
                "portfolio_decision",
            )
            for sequence, name in enumerate(stages, start=1):
                await repository.persist_runner_event(run.id, _stage(sequence, name))
                steps = list(
                    await session.scalars(
                        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.created_at)
                    )
                )
                assert steps[-1].status == "running"
                assert all(step.status == "completed" for step in steps[:-1])

            await repository.persist_runner_event(run.id, _result(6))
            steps = list(await session.scalars(select(RunStep).where(RunStep.run_id == run.id)))

        assert len(steps) == 5
        assert all(step.status == "completed" for step in steps)
        assert all(step.finished_at is not None for step in steps)
    finally:
        await engine.dispose()


async def test_recovered_attempt_starts_a_new_durable_runner_sequence(monkeypatch):
    monkeypatch.setattr(repository_module, "upsert", _sqlite_upsert)
    engine, sessions = await _database()
    try:
        async with sessions() as session, session.begin():
            run = await _run(session, RunStatus.STARTING)
            repository = WorkerRepository(session)
            await repository.persist_runner_event(run.id, _stage(1, "running_analysts"))

            run.attempt = 2
            run.status = RunStatus.STARTING.value
            await repository.persist_runner_event(run.id, _stage(1, "running_analysts"))
            events = list(
                await session.scalars(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run.id,
                        RunEvent.event_type.like("runner.%"),
                    )
                    .order_by(RunEvent.sequence)
                )
            )

        assert [event.payload_json["runner_sequence"] for event in events] == [1, 1]
        assert [event.payload_json["runner_attempt"] for event in events] == [1, 2]
    finally:
        await engine.dispose()


async def test_failure_marks_running_step_failed_with_error_code():
    engine, sessions = await _database()
    try:
        async with sessions() as session, session.begin():
            run = await _run(session, RunStatus.RUNNING_ANALYSTS)
            session.add(
                RunStep(
                    run_id=run.id,
                    name="running_analysts",
                    status="running",
                    attempt=1,
                    started_at=datetime.now(timezone.utc),
                )
            )
            await WorkerRepository(session).finalize_failure(
                run.id,
                "vendor_rate_limit",
                "provider unavailable",
            )
            step = await session.scalar(select(RunStep).where(RunStep.run_id == run.id))

        assert step.status == "failed"
        assert step.error_code == "vendor_rate_limit"
        assert step.finished_at is not None
    finally:
        await engine.dispose()


async def test_cancellation_marks_running_step_cancelled():
    engine, sessions = await _database()
    try:
        async with sessions() as session, session.begin():
            run = await _run(session, RunStatus.CANCEL_REQUESTED)
            session.add(
                RunStep(
                    run_id=run.id,
                    name="risk_debate",
                    status="running",
                    attempt=1,
                    started_at=datetime.now(timezone.utc),
                )
            )
            await WorkerRepository(session).finalize_cancelled(run.id)
            step = await session.scalar(select(RunStep).where(RunStep.run_id == run.id))

        assert step.status == "cancelled"
        assert step.finished_at is not None
    finally:
        await engine.dispose()


async def _failed_context(session, *, attempt=1, error_code="vendor_rate_limit"):
    user = User(
        issuer="issuer",
        subject=f"subject-{uuid.uuid4()}",
        display_name="Analyst",
        email=None,
    )
    instrument = Instrument(
        canonical_ticker=f"T{uuid.uuid4().hex[:6].upper()}",
        asset_type="stock",
        exchange="NASDAQ",
        name=None,
        metadata_json={},
    )
    session.add_all([user, instrument])
    await session.flush()
    batch = AssessmentBatch(
        submitted_by=user.id,
        idempotency_key=f"batch-{uuid.uuid4()}",
        defaults_json={"analysts": ["market"], "depth": "shallow", "language": "Chinese"},
    )
    session.add(batch)
    await session.flush()
    request = AssessmentRequest(
        batch_id=batch.id,
        instrument_id=instrument.id,
        analysis_date=datetime.now(timezone.utc).date(),
        requested_config_json={},
    )
    session.add(request)
    await session.flush()
    run = AssessmentRun(
        request_id=request.id,
        status=RunStatus.FAILED.value,
        attempt=attempt,
        version=2,
        error_code=error_code,
        error_summary="safe failure",
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    return run


async def test_vendor_failure_schedules_one_linked_automatic_retry():
    engine, sessions = await _database()
    try:
        async with sessions() as session, session.begin():
            failed = await _failed_context(session)
            repository = WorkerRepository(session)

            retry = await repository.schedule_automatic_retry(
                failed.id,
                "vendor_rate_limit",
                max_retries=2,
            )
            duplicate = await repository.schedule_automatic_retry(
                failed.id,
                "vendor_rate_limit",
                max_retries=2,
            )

            events = list(
                await session.scalars(
                    select(RunEvent).where(RunEvent.run_id == failed.id).order_by(RunEvent.sequence)
                )
            )

        assert retry is not None
        assert retry.status == RunStatus.QUEUED.value
        assert retry.attempt == 2
        assert retry.retry_of_run_id == failed.id
        assert duplicate is None
        assert [event.event_type for event in events] == ["assessment.auto_retry_scheduled"]
        assert events[0].payload_json["retry_run_id"] == str(retry.id)
    finally:
        await engine.dispose()


async def test_automatic_retry_is_bounded_and_rejects_non_vendor_errors():
    engine, sessions = await _database()
    try:
        async with sessions() as session, session.begin():
            exhausted = await _failed_context(session, attempt=3)
            invalid = await _failed_context(session, error_code="runner_protocol_error")
            repository = WorkerRepository(session)

            assert (
                await repository.schedule_automatic_retry(
                    exhausted.id,
                    "vendor_transient",
                    max_retries=2,
                )
                is None
            )
            assert (
                await repository.schedule_automatic_retry(
                    invalid.id,
                    "runner_protocol_error",
                    max_retries=2,
                )
                is None
            )
    finally:
        await engine.dispose()


async def _allow_archive_lock(*args, **kwargs):
    return True


async def _skip_validation_schedule(*args, **kwargs):
    return []


def _write_success_artifacts(work_dir: Path, analysis_date: date) -> None:
    (work_dir / "working").mkdir(parents=True)
    (work_dir / "decision.json").write_text(
        json.dumps(
            {
                "rating": "Hold",
                "executive_summary": "Wait.",
                "investment_thesis": "Evidence is balanced.",
                "price_target": None,
                "time_horizon": "20 trading days",
            }
        ),
        encoding="utf-8",
    )
    (work_dir / "final_state.json").write_text("{}", encoding="utf-8")
    evidence = {
        "tool_name": "get_stock_data",
        "source": "alpha_vantage",
        "arguments": {"ticker": "NVDA"},
        "output": {"close": 100},
        "output_sha256": "a" * 64,
        "collected_at": "2026-07-27T12:00:00+00:00",
        "effective_at": "2025-07-01T23:59:59.999999+00:00",
        "freshness": "point_in_time_bounded",
        "retention_class": "raw_180d",
    }
    (work_dir / "working" / "evidence.jsonl").write_text(
        json.dumps(evidence) + "\n",
        encoding="utf-8",
    )
    recorder = PointInTimeRecorder(
        analysis_date,
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    recorder.record("get_stock_data", "safe", "date_bounded_route")
    (work_dir / "working" / "point_in_time_integrity.json").write_text(
        recorder.finalize().model_dump_json(),
        encoding="utf-8",
    )


async def test_finalize_success_archives_and_persists_integrity(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(repository_module, "acquire_transaction_lock", _allow_archive_lock)
    monkeypatch.setattr(repository_module, "schedule_validations", _skip_validation_schedule)
    engine, sessions = await _database()
    try:
        async with sessions() as session, session.begin():
            user = User(
                issuer="issuer",
                subject="integrity-owner",
                display_name="Owner",
                email=None,
            )
            instrument = Instrument(
                canonical_ticker="NVDA",
                asset_type="stock",
                exchange="NASDAQ",
                name="NVIDIA",
                metadata_json={},
            )
            session.add_all([user, instrument])
            await session.flush()
            batch = AssessmentBatch(
                submitted_by=user.id,
                idempotency_key="integrity-finalize-test",
                defaults_json={"analysts": ["market"], "depth": "deep", "language": "Chinese"},
            )
            session.add(batch)
            await session.flush()
            analysis_date = date(2025, 7, 1)
            request = AssessmentRequest(
                batch_id=batch.id,
                instrument_id=instrument.id,
                analysis_date=analysis_date,
                requested_config_json={},
            )
            session.add(request)
            await session.flush()
            run = AssessmentRun(
                request_id=request.id,
                status=RunStatus.FINALIZING.value,
                attempt=1,
                version=1,
            )
            session.add(run)
            await session.flush()
            work_dir = tmp_path / "job"
            _write_success_artifacts(work_dir, analysis_date)

            await WorkerRepository(session).finalize_success(
                run.id,
                work_dir,
                LocalArtifactStore(tmp_path / "artifacts"),
            )

            integrity = await session.scalar(
                select(RunIntegrityAssessment).where(RunIntegrityAssessment.run_id == run.id)
            )
            evidence = await session.scalar(
                select(EvidenceItem).where(EvidenceItem.run_id == run.id)
            )
            artifact = await session.get(Artifact, integrity.artifact_id)

        assert integrity.status == "safe"
        assert integrity.audit_mode == "live"
        assert artifact.kind == "point_in_time_integrity"
        assert evidence.effective_at.isoformat() == "2025-07-01T23:59:59.999999+00:00"
        assert evidence.freshness == "point_in_time_bounded"
    finally:
        await engine.dispose()
