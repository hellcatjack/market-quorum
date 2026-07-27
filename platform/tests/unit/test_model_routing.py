import pytest
from pydantic import ValidationError

from tradingng_platform.model_routing import (
    AVAILABLE_CODEX_MODELS,
    AVAILABLE_REASONING_EFFORTS,
    ModelRoute,
    ModelRoutingPolicy,
)


def test_model_routing_defaults_use_terra_fast_sol_slow_and_high_effort():
    policy = ModelRoutingPolicy()

    assert policy.fast == ModelRoute(model="gpt-5.6-terra", reasoning_effort="high")
    assert policy.slow == ModelRoute(model="gpt-5.6-sol", reasoning_effort="high")
    assert AVAILABLE_CODEX_MODELS == ("gpt-5.6-terra", "gpt-5.6-sol")
    assert "high" in AVAILABLE_REASONING_EFFORTS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "unknown-model"),
        ("reasoning_effort", "extreme"),
    ],
)
def test_model_route_rejects_unsupported_values(field, value):
    values = {"model": "gpt-5.6-terra", "reasoning_effort": "high", field: value}

    with pytest.raises(ValidationError):
        ModelRoute(**values)


def test_routing_snapshot_id_is_deterministic_and_changes_with_route():
    first = ModelRoutingPolicy()
    second = ModelRoutingPolicy.model_validate(
        {
            "slow": {"reasoning_effort": "high", "model": "gpt-5.6-sol"},
            "fast": {"reasoning_effort": "high", "model": "gpt-5.6-terra"},
        }
    )
    changed = ModelRoutingPolicy(
        fast=ModelRoute(model="gpt-5.6-terra", reasoning_effort="medium")
    )

    assert first.snapshot_id == second.snapshot_id
    assert len(first.snapshot_id) == 64
    assert first.snapshot_id != changed.snapshot_id
