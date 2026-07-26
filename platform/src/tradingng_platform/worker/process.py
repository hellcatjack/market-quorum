import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    pgid: int
    start_time_ticks: int

    @classmethod
    def read(cls, pid: int) -> "ProcessIdentity | None":
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            return None
        closing_parenthesis = stat.rfind(")")
        if closing_parenthesis < 0:
            return None
        fields = stat[closing_parenthesis + 2 :].split()
        try:
            pgid = int(fields[2])
            start_time_ticks = int(fields[19])
        except (IndexError, ValueError):
            return None
        return cls(pid=pid, pgid=pgid, start_time_ticks=start_time_ticks)


@dataclass
class ManagedProcess:
    process: asyncio.subprocess.Process
    identity: ProcessIdentity
    stderr_path: Path
    stderr_stream: BinaryIO

    def close(self) -> None:
        self.stderr_stream.close()


class ProcessController:
    async def launch(
        self,
        python_bin: str,
        config_path: Path,
        stderr_path: Path,
    ) -> ManagedProcess:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_stream = stderr_path.open("ab", buffering=0)
        try:
            process = await asyncio.create_subprocess_exec(
                python_bin,
                "-m",
                "tradingng_platform.runner.cli",
                "--config",
                str(config_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr_stream,
                start_new_session=True,
            )
            identity = ProcessIdentity.read(process.pid)
            if identity is None or identity.pgid != process.pid:
                process.terminate()
                await process.wait()
                raise RuntimeError("runner process group identity could not be verified")
            return ManagedProcess(process, identity, stderr_path, stderr_stream)
        except BaseException:
            stderr_stream.close()
            raise


class CancellationController:
    def __init__(
        self,
        *,
        node_grace_seconds: float = 30.0,
        term_grace_seconds: float = 10.0,
        identity_matches=None,
        signal_group=os.killpg,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ):
        self.node_grace_seconds = node_grace_seconds
        self.term_grace_seconds = term_grace_seconds
        self.identity_matches = identity_matches or self._identity_matches
        self.signal_group = signal_group
        self.clock = clock
        self.sleep = sleep

    async def cancel(self, identity: ProcessIdentity, requested_at: float) -> None:
        remaining = self.node_grace_seconds - (self.clock() - requested_at)
        if remaining > 0:
            await self.sleep(remaining)
        if not self.identity_matches(identity):
            return
        self.signal_group(identity.pgid, signal.SIGTERM)
        await self.sleep(self.term_grace_seconds)
        if self.identity_matches(identity):
            self.signal_group(identity.pgid, signal.SIGKILL)

    @staticmethod
    def _identity_matches(identity: ProcessIdentity) -> bool:
        return ProcessIdentity.read(identity.pid) == identity
