import httpx
import pytest

from tradingng_platform.gateway.client import GatewayClient, GatewayStatusError


async def test_gateway_client_classifies_overload_without_exposing_response_body():
    async def handler(request):
        return httpx.Response(429, json={"detail": "secret provider diagnostic"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GatewayStatusError) as captured:
            await GatewayClient("http://gateway", client=client).status()

    assert captured.value.error_code == "gateway_overload"
    assert captured.value.status_code == 429
    assert "secret provider diagnostic" not in str(captured.value)


async def test_gateway_client_returns_validated_snapshot():
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "active_completions": 1,
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "snapshot_id": "a" * 64,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snapshot = await GatewayClient("http://gateway", client=client).status()

    assert snapshot.model == "gpt-5.6-sol"
    assert snapshot.reasoning_effort == "xhigh"
    assert snapshot.latency_ms >= 0
