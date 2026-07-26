import pytest

from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.scheduler.policy import (
    AdmissionPolicy,
    CapacitySnapshot,
    SystemSnapshot,
)


@pytest.mark.parametrize(
    (
        "active_runs",
        "gateway_active",
        "cpu_sustained",
        "memory_gib",
        "disk_gib",
        "disk_percent",
        "allowed",
    ),
    [
        (1, 2, False, 20, 100, 50, True),
        (2, 0, False, 20, 100, 50, False),
        (0, 3, False, 20, 100, 50, False),
        (0, 0, True, 20, 100, 50, False),
        (0, 0, False, 7, 100, 50, False),
        (0, 0, False, 20, 9, 50, False),
        (0, 0, False, 20, 100, 9, False),
    ],
)
def test_default_admission_boundaries(
    active_runs,
    gateway_active,
    cpu_sustained,
    memory_gib,
    disk_gib,
    disk_percent,
    allowed,
):
    policy = AdmissionPolicy()
    snapshot = CapacitySnapshot(
        active_runs=active_runs,
        gateway=GatewaySnapshot(
            status="ok",
            active_completions=gateway_active,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            snapshot_id="a" * 64,
            latency_ms=10,
        ),
        system=SystemSnapshot(
            cpu_percent=86 if cpu_sustained else 40,
            available_memory_gib=memory_gib,
            available_disk_gib=disk_gib,
            available_disk_percent=disk_percent,
            cpu_above_limit_for_two_minutes=cpu_sustained,
        ),
        open_circuits=(),
    )

    assert policy.evaluate(snapshot).allowed is allowed


def test_policy_accepts_the_host_absolute_limit():
    policy = AdmissionPolicy(max_running_total=32, hard_max_running_total=32)

    assert policy.max_running_total == 32
    assert policy.hard_max_running_total == 32


def test_policy_rejects_values_above_host_absolute_limit():
    with pytest.raises(ValueError, match="hard maximum"):
        AdmissionPolicy(max_running_total=33, hard_max_running_total=33)


def test_policy_rejects_maximum_above_configured_hard_limit():
    with pytest.raises(ValueError, match="hard maximum"):
        AdmissionPolicy(max_running_total=5, hard_max_running_total=4)
