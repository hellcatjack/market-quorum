import asyncio
import contextlib
import os
import signal
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.contracts import (
    AssessmentItem,
    MemoryMode,
    SubmitAssessments,
)
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.models import (
    AssessmentRun,
    Decision,
    RunConfigSnapshot,
    RunEvent,
    Validation,
    Worker,
    WorkerLease,
)
from tradingng_platform.scheduler.policy import SystemSnapshot
from tradingng_platform.scheduler.repository import (
    ExecutionMetadata,
    SchedulerPolicyRepository,
    SchedulerRepository,
)
from tradingng_platform.scheduler.service import AdmissionService
from tradingng_platform.worker.process import ManagedProcess, ProcessIdentity
from tradingng_platform.worker.repository import WorkerRepository
from tradingng_platform.worker.service import WorkerService


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


class _SystemProbe:
    def sample(self):
        return SystemSnapshot(20, 32, 100, 50, False)


class _FixtureProcessController:
    def __init__(self, fixture: Path, delay_ms=300):
        self.fixture = fixture
        self.delay_ms = delay_ms
        self.processes = []

    async def launch(self, python_bin, config_path, stderr_path):
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_stream = stderr_path.open("ab", buffering=0)
        environment = {**os.environ, "FAKE_RUNNER_DELAY_MS": str(self.delay_ms)}
        process = await asyncio.create_subprocess_exec(
            python_bin,
            str(self.fixture),
            "--config",
            str(config_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_stream,
            env=environment,
            start_new_session=True,
        )
        identity = ProcessIdentity.read(process.pid)
        assert identity is not None
        managed = ManagedProcess(process, identity, stderr_path, stderr_stream)
        self.processes.append(managed)
        return managed


async def _admit_once(session_factory, metadata):
    async with session_factory() as session, session.begin():
        return await AdmissionService(
            SchedulerRepository(session),
            SchedulerPolicyRepository(session),
            _Gateway(),
            _SystemProbe(),
            metadata,
        ).admit_one()


async def _register_worker(session_factory, name):
    async with session_factory() as session, session.begin():
        worker = await WorkerRepository(session).register_worker(name, os.getpid())
        return worker.id


async def test_idle_worker_heartbeat_refreshes_liveness(session_factory):
    worker_id = await _register_worker(session_factory, f"worker-{uuid.uuid4()}")
    stale_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    async with session_factory() as session, session.begin():
        worker = await session.get(Worker, worker_id)
        worker.heartbeat_at = stale_at

    async with session_factory() as session, session.begin():
        await WorkerRepository(session).heartbeat_idle(worker_id)

    async with session_factory() as session:
        worker = await session.get(Worker, worker_id)
        assert worker.status == "idle"
        assert worker.heartbeat_at > stale_at


async def test_historical_mode_is_pinned_at_admission_without_eligible_history(
    session_factory,
    instrument_classifier,
):
    principal = Principal(
        "issuer",
        "historical-mode-test",
        "user",
        frozenset({"assessments:submit"}),
        roles=frozenset({"Analyst"}),
    )
    run = (
        await AssessmentService(session_factory, instrument_classifier).submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25))],
                memory_mode=MemoryMode.HISTORICAL,
                idempotency_key="historical-mode-snapshot",
            ),
            "request-historical-mode",
        )
    )[0]

    assert (
        await _admit_once(
            session_factory,
            ExecutionMetadata("root", "submodule", "v1", {}, {}),
        )
    ).allowed

    async with session_factory() as session:
        admitted = await session.get(AssessmentRun, run.id)
        snapshot = await session.get(RunConfigSnapshot, admitted.config_snapshot_id)
        event_payload = await session.scalar(
            select(RunEvent.payload_json).where(
                RunEvent.run_id == run.id,
                RunEvent.event_type == "assessment.admitted",
            )
        )

    assert snapshot.content_json["request"]["memory_mode"] == "historical"
    assert snapshot.content_json["memory"]["mode"] == "historical"
    assert snapshot.content_json["memory"]["entries"] == []
    assert event_payload["memory_entry_count"] == 0


async def test_admission_pins_an_eligible_validated_prior_assessment(
    session_factory,
    instrument_classifier,
):
    principal = Principal(
        "issuer",
        "validated-history-test",
        "user",
        frozenset({"assessments:submit"}),
        roles=frozenset({"Analyst"}),
    )
    service = AssessmentService(session_factory, instrument_classifier)
    old_run = (
        await service.submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 6, 1))],
                idempotency_key="validated-history-source",
            ),
            "request-validated-source",
        )
    )[0]
    observed_at = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
    async with session_factory() as session, session.begin():
        source = await session.get(AssessmentRun, old_run.id)
        source.status = RunStatus.SUCCEEDED.value
        source.finished_at = observed_at
        session.add(
            Decision(
                run_id=old_run.id,
                rating="Buy",
                executive_summary="Earlier conclusion",
                investment_thesis="Earlier thesis",
                price_target=Decimal("200"),
                time_horizon="6 months",
                structured_json={},
            )
        )
        session.add(
            Validation(
                run_id=old_run.id,
                horizon=20,
                status="completed",
                scheduled_for=observed_at,
                observed_at=observed_at,
                raw_return=Decimal("0.05"),
                benchmark_return=Decimal("0.03"),
                alpha=Decimal("0.02"),
                max_adverse_excursion=Decimal("-0.03"),
                max_favorable_excursion=Decimal("0.07"),
                trigger_results_json={
                    "entry_session": "2026-06-02",
                    "exit_session": "2026-07-10",
                    "direction_correct": True,
                    "price_target_hit": False,
                },
                attempts=1,
            )
        )

    new_run = (
        await service.submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25))],
                memory_mode=MemoryMode.HISTORICAL,
                idempotency_key="validated-history-target",
            ),
            "request-validated-target",
        )
    )[0]
    assert (
        await _admit_once(
            session_factory,
            ExecutionMetadata("root", "submodule", "v1", {}, {}),
        )
    ).allowed

    async with session_factory() as session:
        admitted = await session.get(AssessmentRun, new_run.id)
        snapshot = await session.get(RunConfigSnapshot, admitted.config_snapshot_id)

    entry = snapshot.content_json["memory"]["entries"][0]
    assert entry["source_run_id"] == str(old_run.id)
    assert entry["horizon"] == 20
    assert entry["analysis_date"] == "2026-06-01"
    assert "Earlier conclusion" in entry["decision"]
    assert "Validated after 20 sessions" in entry["reflection"]


async def test_two_tickers_overlap_but_same_ticker_never_overlaps(
    session_factory,
    instrument_classifier,
    tmp_path,
):
    principal = Principal(
        "issuer",
        "scheduler-test",
        "user",
        frozenset({"assessments:submit"}),
        roles=frozenset({"Analyst"}),
    )
    command = SubmitAssessments(
        items=[
            AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25)),
            AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25)),
            AssessmentItem(ticker="TSLA", analysis_date=date(2026, 7, 25)),
        ],
        idempotency_key="scheduler-concurrency",
    )
    runs = await AssessmentService(session_factory, instrument_classifier).submit(
        principal, command, "request-1"
    )
    nvda_first, nvda_second, tsla = runs
    metadata = ExecutionMetadata("root", "submodule", "v1", {}, {})

    assert (await _admit_once(session_factory, metadata)).allowed
    assert (await _admit_once(session_factory, metadata)).allowed

    fixture = Path(__file__).parents[1] / "fixtures" / "fake_runner.py"
    controller = _FixtureProcessController(fixture)
    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    worker_ids = await asyncio.gather(
        _register_worker(session_factory, f"worker-{uuid.uuid4()}"),
        _register_worker(session_factory, f"worker-{uuid.uuid4()}"),
    )
    services = [
        WorkerService(
            session_factory,
            job_dir=tmp_path / "jobs",
            gateway_url="http://127.0.0.1:8000",
            artifact_store=artifact_store,
            python_bin=sys.executable,
            process_controller=controller,
        )
        for _ in worker_ids
    ]
    assert all(
        await asyncio.gather(*(s.run_once(w) for s, w in zip(services, worker_ids, strict=True)))
    )

    assert (await _admit_once(session_factory, metadata)).allowed
    assert await services[0].run_once(worker_ids[0])

    async with session_factory() as session:
        persisted_runs = {run.id: run for run in await session.scalars(select(AssessmentRun))}
        assert {run.status for run in persisted_runs.values()} == {RunStatus.SUCCEEDED.value}
        for run_id in persisted_runs:
            sequences = list(
                await session.scalars(
                    select(RunEvent.sequence)
                    .where(RunEvent.run_id == run_id)
                    .order_by(RunEvent.sequence)
                )
            )
            assert sequences == list(range(1, len(sequences) + 1))
            assert persisted_runs[run_id].config_snapshot_id is not None

        timing = {}
        for run_id in persisted_runs:
            payload = await session.scalar(
                select(RunEvent.payload_json).where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "runner.result.assessment.completed",
                )
            )
            timing[run_id] = payload
        assert timing[nvda_first.id]["started_monotonic"] < timing[tsla.id]["ended_monotonic"]
        assert timing[tsla.id]["started_monotonic"] < timing[nvda_first.id]["ended_monotonic"]
        assert (
            timing[nvda_second.id]["started_monotonic"] >= timing[nvda_first.id]["ended_monotonic"]
        )
        assert len(list(await session.scalars(select(Worker)))) == 2


async def test_crashed_worker_is_recovered_once_and_reuses_checkpoint_directory(
    session_factory,
    instrument_classifier,
    tmp_path,
):
    principal = Principal(
        "issuer",
        "recovery-test",
        "user",
        frozenset({"assessments:submit"}),
        roles=frozenset({"Analyst"}),
    )
    runs = await AssessmentService(session_factory, instrument_classifier).submit(
        principal,
        SubmitAssessments(
            items=[AssessmentItem(ticker="SPCX", analysis_date=date(2026, 7, 25))],
            idempotency_key="worker-recovery",
        ),
        "request-recovery",
    )
    run = runs[0]
    assert (
        await _admit_once(
            session_factory,
            ExecutionMetadata("root", "submodule", "v1", {}, {}),
        )
    ).allowed

    fixture = Path(__file__).parents[1] / "fixtures" / "fake_runner.py"
    slow_controller = _FixtureProcessController(fixture, delay_ms=10_000)
    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    worker_id = await _register_worker(session_factory, f"worker-{uuid.uuid4()}")
    service = WorkerService(
        session_factory,
        job_dir=tmp_path / "jobs",
        gateway_url="http://127.0.0.1:8000",
        artifact_store=artifact_store,
        python_bin=sys.executable,
        process_controller=slow_controller,
    )
    worker_task = asyncio.create_task(service.run_once(worker_id))

    lease = None
    for _ in range(100):
        async with session_factory() as session:
            lease = await session.scalar(select(WorkerLease).where(WorkerLease.run_id == run.id))
        if lease is not None and lease.child_pid is not None:
            break
        await asyncio.sleep(0.02)
    assert lease is not None and lease.child_pid is not None
    assert lease.child_pgid is not None

    checkpoint_dir = tmp_path / "jobs" / str(run.id) / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    checkpoint_marker = checkpoint_dir / "resume.marker"
    checkpoint_marker.write_text("preserve", encoding="utf-8")

    worker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker_task
    os.killpg(lease.child_pgid, signal.SIGTERM)
    await slow_controller.processes[0].process.wait()

    async with session_factory() as session, session.begin():
        persisted_lease = await session.scalar(
            select(WorkerLease).where(WorkerLease.run_id == run.id).with_for_update()
        )
        persisted_lease.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    async with session_factory() as session, session.begin():
        assert await WorkerRepository(session).recover_stale_leases() == (1, 0)
    async with session_factory() as session, session.begin():
        assert await WorkerRepository(session).recover_stale_leases() == (0, 0)
    async with session_factory() as session:
        recovered = await session.get(AssessmentRun, run.id)
        assert recovered.status == RunStatus.ADMITTED.value
        assert recovered.attempt == 2

    replacement_worker = await _register_worker(session_factory, f"worker-{uuid.uuid4()}")
    replacement_service = WorkerService(
        session_factory,
        job_dir=tmp_path / "jobs",
        gateway_url="http://127.0.0.1:8000",
        artifact_store=artifact_store,
        python_bin=sys.executable,
        process_controller=_FixtureProcessController(fixture),
    )
    assert await replacement_service.run_once(replacement_worker)
    assert checkpoint_marker.read_text(encoding="utf-8") == "preserve"
    async with session_factory() as session:
        recovered = await session.get(AssessmentRun, run.id)
        assert recovered.status == RunStatus.SUCCEEDED.value
        assert recovered.attempt == 2
