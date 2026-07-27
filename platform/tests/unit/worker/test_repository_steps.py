import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.models import AssessmentRun, Base, RunStep
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
