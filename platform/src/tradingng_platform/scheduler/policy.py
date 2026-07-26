from dataclasses import asdict, dataclass

from tradingng_platform.assessments.contracts import Depth
from tradingng_platform.gateway.client import GatewaySnapshot

DEPTH_ROUNDS = {
    Depth.SHALLOW: (1, 1),
    Depth.MEDIUM: (2, 2),
    Depth.DEEP: (3, 3),
}

ABSOLUTE_MAX_RUNNING_TOTAL = 32


@dataclass(frozen=True)
class SystemSnapshot:
    cpu_percent: float
    available_memory_gib: float
    available_disk_gib: float
    available_disk_percent: float
    cpu_above_limit_for_two_minutes: bool


@dataclass(frozen=True)
class CapacitySnapshot:
    active_runs: int
    gateway: GatewaySnapshot
    system: SystemSnapshot
    open_circuits: tuple[str, ...]

    @property
    def cpu_percent(self) -> float:
        return self.system.cpu_percent

    @property
    def available_memory_gib(self) -> float:
        return self.system.available_memory_gib

    @property
    def available_disk_gib(self) -> float:
        return self.system.available_disk_gib

    @property
    def available_disk_percent(self) -> float:
        return self.system.available_disk_percent


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AdmissionPolicy:
    max_running_total: int = 2
    hard_max_running_total: int = ABSOLUTE_MAX_RUNNING_TOTAL
    gateway_active_limit: int = 3
    cpu_limit_percent: float = 85.0
    minimum_memory_gib: float = 8.0
    minimum_disk_gib: float = 10.0
    minimum_disk_percent: float = 10.0

    def __post_init__(self) -> None:
        if not (
            1 <= self.max_running_total <= self.hard_max_running_total <= ABSOLUTE_MAX_RUNNING_TOTAL
        ):
            raise ValueError(
                f"scheduler capacity exceeds the hard maximum of {ABSOLUTE_MAX_RUNNING_TOTAL}"
            )
        if self.gateway_active_limit < 1:
            raise ValueError("gateway active limit must be positive")

    def evaluate(self, snapshot: CapacitySnapshot) -> AdmissionDecision:
        reasons = []
        if snapshot.active_runs >= self.max_running_total:
            reasons.append("run_capacity")
        if snapshot.gateway.active_completions >= self.gateway_active_limit:
            reasons.append("gateway_capacity")
        if snapshot.system.cpu_above_limit_for_two_minutes:
            reasons.append("cpu")
        if snapshot.available_memory_gib < self.minimum_memory_gib:
            reasons.append("memory")
        if snapshot.available_disk_gib < self.minimum_disk_gib:
            reasons.append("disk_gib")
        if snapshot.available_disk_percent < self.minimum_disk_percent:
            reasons.append("disk_percent")
        if snapshot.open_circuits:
            reasons.append("circuit_breaker")
        return AdmissionDecision(allowed=not reasons, reasons=tuple(reasons))

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "AdmissionPolicy":
        return cls(**value)
