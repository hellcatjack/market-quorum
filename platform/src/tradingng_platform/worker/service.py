import asyncio
import os
import re
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.memory import MemorySnapshot, empty_memory_snapshot
from tradingng_platform.runner.contracts import (
    DependencyHealthEvent,
    RunnerEvent,
    RunnerInput,
)
from tradingng_platform.scheduler.circuits import CircuitBreakerRepository
from tradingng_platform.worker.process import CancellationController, ProcessController
from tradingng_platform.worker.repository import ClaimedRun, WorkerRepository

_MAX_RUNNER_LINE_BYTES = 1024 * 1024
_MAX_HEALTH_FILE_BYTES = 1024 * 1024
_DIAGNOSTIC_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|password|secret|token)(\s*[:=]\s*)(\S+)"
)


class RunnerProtocol:
    def __init__(self):
        self.expected_sequence = 1
        self.saw_result = False
        self.last_error_code: str | None = None

    def consume(self, line: str) -> RunnerEvent:
        if self.saw_result:
            raise ValueError("runner emitted data after terminal result")
        event = RunnerEvent.model_validate_json(line)
        if event.sequence != self.expected_sequence:
            raise ValueError(
                f"expected runner sequence {self.expected_sequence}, received {event.sequence}"
            )
        self.expected_sequence += 1
        if event.type == "error":
            error_code = event.payload.get("error_code")
            if isinstance(error_code, str):
                self.last_error_code = error_code
        if event.type == "result":
            self.saw_result = True
        return event


async def persist_dependency_health(session, work_dir: Path) -> int:
    path = work_dir / "working" / "dependency_health.jsonl"
    if not path.is_file():
        return 0
    if path.stat().st_size > _MAX_HEALTH_FILE_BYTES:
        raise ValueError("dependency health file exceeds one MiB")

    repository = CircuitBreakerRepository(session)
    persisted = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line:
            continue
        if len(raw_line.encode("utf-8")) > _MAX_RUNNER_LINE_BYTES:
            raise ValueError("dependency health event exceeds one MiB")
        event = DependencyHealthEvent.model_validate_json(raw_line)
        if event.scope == "gateway":
            await repository.record_gateway_sample(
                healthy=event.healthy,
                latency_ms=event.latency_ms,
                detail={"source": "runner_callback"},
                now=event.observed_at,
                error_code=event.error_code,
            )
        else:
            await repository.record_vendor_sample(
                vendor=event.vendor or "default",
                category=event.category or "unknown",
                healthy=event.healthy,
                latency_ms=event.latency_ms,
                detail={"source": "runner_callback"},
                now=event.observed_at,
                error_code=event.error_code,
            )
        persisted += 1
    return persisted


def build_runner_input(
    claim: ClaimedRun,
    *,
    job_dir: Path,
    gateway_url: str,
) -> RunnerInput:
    request = claim.snapshot["request"]
    resolved = claim.snapshot["resolved"]
    gateway = claim.snapshot["gateway"]
    memory_payload = claim.snapshot.get("memory")
    memory = (
        MemorySnapshot.model_validate(memory_payload)
        if memory_payload is not None
        else empty_memory_snapshot()
    )
    alpha_policy = dict((claim.snapshot.get("vendor_policies") or {}).get("alpha_vantage") or {})
    return RunnerInput(
        run_id=claim.run_id,
        ticker=claim.ticker,
        asset_type=claim.asset_type,
        analysis_date=claim.analysis_date,
        analysts=tuple(request["analysts"]),
        debate_rounds=resolved["debate_rounds"],
        risk_rounds=resolved["risk_rounds"],
        language=request["language"],
        gateway_url=gateway_url,
        codex_model=gateway["model"],
        codex_reasoning_effort=gateway["reasoning_effort"],
        work_dir=job_dir / str(claim.run_id),
        data_vendors=claim.snapshot["data_vendors"],
        tool_vendors=claim.snapshot["tool_vendors"],
        alpha_vantage_coordination_dir=job_dir.parent / "vendor-limits",
        alpha_vantage_requests_per_minute=alpha_policy.get("requests_per_minute", 75),
        alpha_vantage_retry_attempts=alpha_policy.get("retry_attempts", 6),
        alpha_vantage_retry_base_seconds=alpha_policy.get("retry_base_seconds", 5),
        alpha_vantage_retry_max_seconds=alpha_policy.get("retry_max_seconds", 60),
        memory=memory,
    )


class WorkerService:
    def __init__(
        self,
        sessions,
        *,
        job_dir: Path,
        gateway_url: str,
        artifact_store: LocalArtifactStore,
        python_bin: str = sys.executable,
        process_controller: ProcessController | None = None,
        cancellation_controller: CancellationController | None = None,
        clock=time.monotonic,
    ):
        self.sessions = sessions
        self.job_dir = job_dir
        self.gateway_url = gateway_url
        self.artifact_store = artifact_store
        self.python_bin = python_bin
        self.process_controller = process_controller or ProcessController()
        self.cancellation_controller = cancellation_controller or CancellationController()
        self.clock = clock

    async def run_once(self, worker_id) -> bool:
        async with self.sessions() as session, session.begin():
            claim = await WorkerRepository(session).claim(worker_id)
        if claim is None:
            return False

        runner_input = build_runner_input(
            claim,
            job_dir=self.job_dir,
            gateway_url=self.gateway_url,
        )
        work_dir = runner_input.work_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        config_path = work_dir / "run-config.json"
        _atomic_runner_config(config_path, runner_input)
        stderr_path = work_dir / "logs" / "runner.stderr.log"
        managed = await self.process_controller.launch(
            self.python_bin,
            config_path,
            stderr_path,
        )
        async with self.sessions() as session, session.begin():
            await WorkerRepository(session).set_child_identity(claim.run_id, managed.identity)

        protocol = RunnerProtocol()
        last_heartbeat = self.clock()
        cancellation_task = None
        failure_code = None
        failure_summary = None
        read_task = asyncio.create_task(managed.process.stdout.readline())
        try:
            while True:
                done, _ = await asyncio.wait({read_task}, timeout=1.0)
                if read_task in done:
                    line = read_task.result()
                    if not line:
                        break
                    if len(line) > _MAX_RUNNER_LINE_BYTES:
                        raise ValueError("runner protocol line exceeds one MiB")
                    event = protocol.consume(line.decode("utf-8"))
                    async with self.sessions() as session, session.begin():
                        await WorkerRepository(session).persist_runner_event(
                            claim.run_id,
                            event,
                        )
                    read_task = asyncio.create_task(managed.process.stdout.readline())

                now = self.clock()
                if now - last_heartbeat >= 10.0:
                    async with self.sessions() as session, session.begin():
                        await WorkerRepository(session).heartbeat(worker_id, claim.run_id)
                    last_heartbeat = now
                if cancellation_task is None:
                    async with self.sessions() as session:
                        cancel_requested = await WorkerRepository(session).is_cancel_requested(
                            claim.run_id
                        )
                    if cancel_requested:
                        cancellation_task = asyncio.create_task(
                            self.cancellation_controller.cancel(managed.identity, now)
                        )

            return_code = await managed.process.wait()
            if return_code != 0:
                failure_code = protocol.last_error_code or "runner_nonzero_exit"
                failure_summary = f"runner exited with status {return_code}"
            elif not protocol.saw_result:
                failure_code = "runner_missing_result"
                failure_summary = "runner exited without a terminal result event"
        except (UnicodeDecodeError, ValidationError, ValueError) as exc:
            failure_code = "runner_protocol_error"
            failure_summary = str(exc)
            await self.cancellation_controller.cancel(
                managed.identity,
                self.clock() - self.cancellation_controller.node_grace_seconds,
            )
            await managed.process.wait()
        finally:
            if not read_task.done():
                read_task.cancel()
            if cancellation_task is not None and not cancellation_task.done():
                cancellation_task.cancel()
            managed.close()

        async with self.sessions() as session:
            cancel_requested = await WorkerRepository(session).is_cancel_requested(claim.run_id)
        async with self.sessions() as session, session.begin():
            repository = WorkerRepository(session)
            await persist_dependency_health(session, work_dir)
            if cancel_requested:
                await repository.finalize_cancelled(claim.run_id)
            elif failure_code is None:
                await repository.finalize_success(
                    claim.run_id,
                    work_dir,
                    self.artifact_store,
                )
                await CircuitBreakerRepository(session).record_gateway_sample(
                    healthy=True,
                    latency_ms=0,
                    detail={"run_id": str(claim.run_id), "source": "assessment"},
                )
            else:
                await self._store_redacted_diagnostic(
                    session,
                    claim.run_id,
                    stderr_path,
                    work_dir,
                )
                await repository.finalize_failure(
                    claim.run_id,
                    failure_code,
                    failure_summary or failure_code,
                )
                if failure_code in {"gateway_overload", "gateway_unavailable"}:
                    await CircuitBreakerRepository(session).record_gateway_sample(
                        healthy=False,
                        latency_ms=0,
                        detail={"run_id": str(claim.run_id), "source": "assessment"},
                        error_code=failure_code,
                    )
        return True

    async def _store_redacted_diagnostic(
        self,
        session,
        run_id,
        stderr_path: Path,
        work_dir: Path,
    ) -> None:
        if not stderr_path.is_file():
            return
        content = stderr_path.read_text(encoding="utf-8", errors="replace")[-8192:]
        redacted = _DIAGNOSTIC_SECRET.sub(r"\1\2[REDACTED]", content)
        diagnostic = work_dir / "runner.diagnostic.redacted.log"
        diagnostic.write_text(redacted, encoding="utf-8")
        stored = self.artifact_store.put(
            run_id,
            "runner_diagnostic",
            "text/plain",
            diagnostic,
        )
        from tradingng_platform.models import Artifact

        session.add(
            Artifact(
                run_id=run_id,
                kind="runner_diagnostic",
                media_type="text/plain",
                size=stored.size,
                sha256=stored.sha256,
                storage_key=stored.storage_key,
                redacted=True,
            )
        )


def _atomic_runner_config(path: Path, runner_input: RunnerInput) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(runner_input.model_dump_json() + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
