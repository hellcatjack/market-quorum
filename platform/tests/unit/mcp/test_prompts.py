import pytest

from tradingng_platform.auth.principal import Principal
from tradingng_platform.mcp.context import reset_principal, set_principal
from tradingng_platform.mcp.server import create_mcp_server
from tradingng_platform.mcp.services import McpServices


class _NoCalls:
    def __getattr__(self, name):
        raise AssertionError(f"prompt retrieval called service method {name}")


async def _get(server, name, arguments):
    token = set_principal(
        Principal(
            issuer="issuer",
            subject="viewer",
            actor_type="user",
            scopes=frozenset({"assessments:read", "validations:read"}),
        )
    )
    try:
        return await server.get_prompt(name, arguments)
    finally:
        reset_principal(token)


@pytest.mark.asyncio
async def test_prompt_inventory_is_side_effect_free_and_resource_grounded():
    no_calls = _NoCalls()
    server = create_mcp_server(McpServices(no_calls, no_calls, no_calls))

    assert {prompt.name for prompt in server._prompt_manager.list_prompts()} == {
        "review_assessment",
        "compare_instrument_runs",
        "summarize_risk_changes",
        "validate_past_decision",
    }

    result = await _get(
        server,
        "review_assessment",
        {"run_id": "11111111-1111-1111-1111-111111111111", "focus": "evidence quality"},
    )
    rendered = result.messages[0].content.text
    assert "tradingng://assessments/11111111-1111-1111-1111-111111111111/summary" in rendered
    assert "observed facts" in rendered
    assert "submit_assessment" not in rendered
    assert "cancel_assessment" not in rendered


@pytest.mark.asyncio
async def test_prompt_inputs_are_bounded():
    no_calls = _NoCalls()
    server = create_mcp_server(McpServices(no_calls, no_calls, no_calls))

    with pytest.raises(ValueError):
        await _get(
            server,
            "compare_instrument_runs",
            {"ticker": "NVDA", "run_ids": ",".join(str(index) for index in range(11))},
        )
    with pytest.raises(ValueError):
        await _get(
            server,
            "review_assessment",
            {"run_id": "run-1", "focus": "x" * 201},
        )
