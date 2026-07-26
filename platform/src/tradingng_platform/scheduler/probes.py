import os
import time
from pathlib import Path

import psutil

from tradingng_platform.scheduler.policy import SystemSnapshot

_GIB = 1024**3


class SystemProbe:
    def __init__(
        self,
        data_dir: Path,
        cpu_limit_percent: float = 85.0,
        sustained_seconds: float = 120.0,
        clock=time.monotonic,
    ):
        self.data_dir = data_dir
        self.cpu_limit_percent = cpu_limit_percent
        self.sustained_seconds = sustained_seconds
        self.clock = clock
        self._cpu_above_since: float | None = None

    def sample(self) -> SystemSnapshot:
        now = self.clock()
        cpu_percent = self._one_minute_cpu_percent()
        if cpu_percent > self.cpu_limit_percent:
            if self._cpu_above_since is None:
                self._cpu_above_since = now
        else:
            self._cpu_above_since = None
        sustained = (
            self._cpu_above_since is not None
            and now - self._cpu_above_since >= self.sustained_seconds
        )

        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(self._existing_disk_path())
        return SystemSnapshot(
            cpu_percent=cpu_percent,
            available_memory_gib=memory.available / _GIB,
            available_disk_gib=disk.free / _GIB,
            available_disk_percent=100.0 - disk.percent,
            cpu_above_limit_for_two_minutes=sustained,
        )

    def _one_minute_cpu_percent(self) -> float:
        cpu_count = psutil.cpu_count() or 1
        if hasattr(os, "getloadavg"):
            one_minute_load = os.getloadavg()[0]
            return min(100.0, max(0.0, one_minute_load / cpu_count * 100.0))
        return float(psutil.cpu_percent(interval=1.0))

    def _existing_disk_path(self) -> Path:
        candidate = self.data_dir.resolve()
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return candidate
